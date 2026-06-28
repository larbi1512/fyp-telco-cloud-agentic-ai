"""
OSS-GPT baseline — multi-agent LLM with hierarchical intent planning.

Reference: Mekrache, Ksentini, Verikoukis, "OSS-GPT: An LLM-Powered
Intent-Driven Operations Support System for 6G Networks", IEEE NetSoft 2025.

OSS-GPT decomposes a natural-language intent into an ordered sequence of OSS
**API calls** drawn from an API catalog, then executes them in a coordinated
manner (Planner → Executor, role-specific agents). We reproduce that two-stage
hierarchy here:

  1. Planner: given the intent + structured features + an API catalog derived
     from this testbed's real capabilities, emit an ordered JSON list of API
     calls that fulfils the intent.
  2. Executor: realize that ordered plan into the final topology + Helm config
     (same schema as B4/MAS, via the shared B4 prompt so the schema and base
     instructions match the other systems).

The API catalog is a pre-deployment stub grounded in CHART_MAP (the VNF types
this testbed can actually deploy) — document this adaptation in the appendix.
Deploys for real when ``deploy=True`` (intent-to-deploy metric).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from agents.deployer import CHART_MAP
from core.llm_core import LLMCore
from experiments.baselines._deploy_common import deploy_and_record
from experiments.baselines._sizing import resource_sizing_reference
from experiments.baselines.b4_single_llm import (
    _build_runtime_prompt,
    _split_topology_and_artifacts,
)
from experiments.common.runners import RunArtifact, register

logger = logging.getLogger(__name__)

# OSS API catalog — the operations the Planner may sequence. Grounded in the
# VNF types this testbed can actually deploy (CHART_MAP keys).
_VNF_TYPES = sorted({k for k in CHART_MAP})

API_CATALOG = f"""\
AVAILABLE OSS API OPERATIONS (you may only use these):
  - create_vnf(type)            : instantiate a network function. Valid types:
                                  {", ".join(_VNF_TYPES)}
  - set_resources(vnf, cpu, memory)
                                : set Kubernetes requests/limits (e.g. "200m", "512Mi").
  - set_helm_values(vnf, values): set image, exposedPorts, probes, resource block.
  - connect(vnf_a, vnf_b)       : declare a topology connection between two VNFs.
  - set_sla(key, value)         : record an SLA target (latency/throughput/availability).
"""

PLANNER_SYSTEM_PROMPT = f"""\
You are the OSS-GPT PLANNER agent for an OAI 5G core/RAN deployment. Your sole
job is to decompose the user's high-level intent into an ORDERED sequence of
low-level OSS API calls that, when executed, fulfil the intent. Include every
mandatory core NF (nrf, amf, smf, upf, udm, udr, ausf) and add nssf when more
than one slice is requested. Size resources to the stated UE/throughput load.

{API_CATALOG}

Output ONLY a single JSON object of the form:
  {{"api_calls": [{{"op": "create_vnf", "args": {{"type": "amf"}}}}, ...]}}
No prose outside the JSON.
"""


class OSSGPTRunner:
    """Planner → Executor hierarchical baseline grounded by an API catalog."""

    name = "ossgpt"

    def __init__(self) -> None:
        self._llm = LLMCore()

    def _plan(self, intent: dict[str, Any]) -> list[dict[str, Any]]:
        feat = intent.get("structured_features") or {}
        user = (
            f"User intent: {intent['prompt']}\n\n"
            f"Structured features: {json.dumps(feat)}\n\n"
            f"Produce the ordered api_calls JSON."
        )
        raw = self._llm.chat(PLANNER_SYSTEM_PROMPT, user)
        parsed = LLMCore._extract_json(raw)
        if isinstance(parsed, dict):
            calls = parsed.get("api_calls")
            if isinstance(calls, list):
                return calls
        return []

    def run(self, intent: dict[str, Any], *, deploy: bool = True) -> RunArtifact:
        run_id = str(uuid.uuid4())
        start = time.time()
        error: str | None = None
        topology: dict[str, Any] = {}
        artifacts: list[dict[str, Any]] = []
        stage_log: list[dict[str, Any]] = []

        try:
            # Stage 1 — Planner decomposes intent into ordered API calls.
            api_calls = self._plan(intent)
            stage_log.append({"stage": "planner", "n_api_calls": len(api_calls)})

            # Stage 2 — Executor realizes the plan into the final topology+config.
            sys_msg, base_user = _build_runtime_prompt(intent)
            exec_user = (
                base_user
                + "\n\nThe OSS-GPT Planner produced this ordered plan of API calls; "
                "realize EXACTLY this plan (same VNF set and ordering) in your output:\n"
                + json.dumps({"api_calls": api_calls})[:6000]
                + "\n\n" + resource_sizing_reference(intent)
                + "\n\nOutput ONLY the single JSON topology+config object."
            )
            raw = self._llm.chat(sys_msg, exec_user)
            parsed = LLMCore._extract_json(raw)
            stage_log.append({"stage": "executor", "parsed": isinstance(parsed, dict)})

            if not isinstance(parsed, dict):
                error = f"Executor returned non-JSON output (first 200 chars): {str(raw)[:200]}"
            else:
                topology, artifacts = _split_topology_and_artifacts(parsed)
        except Exception as e:
            logger.exception("OSS-GPT run failed for intent %s", intent.get("id"))
            error = str(e)

        artifact = RunArtifact({
            "system": self.name,
            "intent_id": intent.get("id"),
            "run_id": run_id,
            "topology": topology,
            "config_artifacts": artifacts,
            "validation_report": {},
            "deployment_results": [],
            "intervention_log": [],        # autonomous planner/executor stages are not operator interventions
            "_stage_log": stage_log,       # internal traceability only (not scored)
            "wall_clock_s": time.time() - start,
            "deployment_wait_s": None,
            "release_names": list(CHART_MAP.values()),
            "error": error,
        })

        if deploy and not error:
            deploy_and_record(artifact)

        return artifact


register(OSSGPTRunner())
