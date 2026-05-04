"""
B4 — Single-agent LLM baseline.

Collapses planner + resource allocator + VNF configurator into one LLM call.
There is no separate policy validator step *during planning* — the policy
validator is run on the output afterwards (via the harness) so policy and
resource errors are visible in the metrics. That asymmetry is the whole point
of B4: it shows what is lost when the multi-agent pipeline is reduced to a
monolithic prompt.

Fairness: the system prompt is the MAS Network Planner prompt, *extended* (not
weakened) to demand Helm values + resource specs in the same response. The
extension is the minimum necessary to obtain a comparable artifact. Document
this prompt as appendix material.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

from agents.deployer import CHART_MAP
from config.settings import PROMPTS_DIR
from core.llm_core import LLMCore
from experiments.common.runners import RunArtifact, register

logger = logging.getLogger(__name__)


def _load_planner_system_prompt() -> str:
    path = Path(PROMPTS_DIR) / "network_planner.yaml"
    with open(path) as f:
        return yaml.safe_load(f)["system_prompt"]


B4_EXTENSION = """

ADDITIONAL B4-SPECIFIC INSTRUCTIONS

You are the SOLE agent for this deployment — no separate Resource Allocator
or VNF Configurator will run after you. In a SINGLE JSON response, produce
both the topology AND the per-VNF resource allocation AND the per-VNF Helm
values. The output schema is the standard topology schema EXTENDED with:

  - For every VNF, populate `resources.requests.cpu`, `resources.requests.memory`,
    `resources.limits.cpu`, `resources.limits.memory` (Kubernetes string
    formats: e.g., "200m", "512Mi").

  - For every VNF, populate `helm_values` — a dict with at least:
        nfimage: {repository, version}    (use docker.io/oaisoftwarealliance/oai-<type>:v2.1.0)
        exposedPorts: {sbi: 80}           (or per-VNF appropriate ports)
        start: {<vnf>: true, tcpdump: false}
        resources:
          define: true
          requests: {nf: {cpu, memory}}
          limits:   {nf: {cpu, memory}}
        readinessProbe: true
        livenessProbe: true
        nodeSelector: {}

Output ONLY a single valid JSON object with the standard topology schema,
PLUS the additional `resources` and `helm_values` fields per VNF.
"""


def _build_runtime_prompt(intent: dict[str, Any]) -> tuple[str, str]:
    sys_msg = _load_planner_system_prompt() + B4_EXTENSION

    feat = intent.get("structured_features") or {}
    user_msg = (
        f"User request: {intent['prompt']}\n\n"
        f"Structured intent features (for resource sizing):\n"
        f"  ue_count: {feat.get('ue_count')}\n"
        f"  throughput_gbps: {feat.get('throughput_gbps')}\n"
        f"  session_count: {feat.get('session_count')}\n"
        f"  subscriber_count: {feat.get('subscriber_count')}\n"
        f"  slices: {feat.get('slices')}\n"
        f"  ha_required: {feat.get('ha_required')}\n"
        f"  autoscale_required: {feat.get('autoscale_required')}\n"
        f"  peak_multiplier: {feat.get('peak_multiplier')}\n\n"
        f"Output ONLY a single JSON object as specified."
    )
    return sys_msg, user_msg


def _split_topology_and_artifacts(
    raw: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Pull the helm_values blocks out of the LLM's flat object into the standard
    config_artifacts shape that the policy validator and metrics layer expect.
    The topology keeps the resource specs (so resource_oracle can score them).
    """
    topology = {
        "topology_id": raw.get("topology_id") or str(uuid.uuid4())[:8],
        "connectivity": raw.get("connectivity") or {},
        "vnfs": [],
        "connections": raw.get("connections") or [],
        "sla": raw.get("sla") or {},
    }
    artifacts: list[dict[str, Any]] = []

    for vnf in raw.get("vnfs") or []:
        vnf_copy = dict(vnf)
        helm_values = vnf_copy.pop("helm_values", None) or {}
        topology["vnfs"].append(vnf_copy)
        artifacts.append({
            "vnf_name": vnf_copy.get("type") or vnf_copy.get("name") or "unknown",
            "helm_values": helm_values,
            "config_maps": {},
            "secrets": [],
        })
    return topology, artifacts


class B4SingleLLMRunner:
    """B4 — collapses planner + allocator + configurator into one LLM call."""

    name = "b4"

    def __init__(self) -> None:
        self._llm = LLMCore()
        self._planner_extended_system_prompt = (
            _load_planner_system_prompt() + B4_EXTENSION
        )

    def run(self, intent: dict[str, Any], *, deploy: bool = True) -> RunArtifact:
        run_id = str(uuid.uuid4())
        start = time.time()
        error: str | None = None
        topology: dict[str, Any] = {}
        artifacts: list[dict[str, Any]] = []

        try:
            sys_msg, user_msg = _build_runtime_prompt(intent)
            raw = self._llm.chat(sys_msg, user_msg)
            parsed = LLMCore._extract_json(raw)
            if not isinstance(parsed, dict):
                error = f"LLM returned non-JSON output (first 200 chars): {raw[:200]}"
            else:
                topology, artifacts = _split_topology_and_artifacts(parsed)
        except Exception as e:
            logger.exception("B4 LLM call failed for intent %s", intent.get("id"))
            error = str(e)

        wall_clock_s = time.time() - start

        # B4 has no internal validation; the harness will run the standard
        # policy validator on `artifacts` and merge the report into the
        # artifact dict before scoring.
        return RunArtifact({
            "system": self.name,
            "intent_id": intent.get("id"),
            "run_id": run_id,
            "topology": topology,
            "config_artifacts": artifacts,
            "validation_report": {},      # filled in by harness
            "deployment_results": [],     # B4 does not auto-deploy
            "intervention_log": [],
            "wall_clock_s": wall_clock_s,
            "deployment_wait_s": None,
            "release_names": list(CHART_MAP.values()),
            "error": error,
        })


register(B4SingleLLMRunner())
