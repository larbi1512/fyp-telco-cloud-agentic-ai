"""
Confucius baseline — iterative tool learning via introspection feedback.

Reference: Gao et al., "Confucius: Iterative Tool Learning from Introspection
Feedback by Easy-to-Difficult Curriculum", AAAI 2024.

This repo is inference-only (Ollama/vLLM), so we cannot reproduce Confucius's
easy-to-difficult curriculum *fine-tuning*. We instead reproduce its runtime
contribution — the Introspection-based Self-Instruct Feedback (ISIF) loop:
the model produces an artifact, critiques its own output, and revises it over a
few rounds before committing. The first draft reuses the exact B4 single-LLM
combined topology+config prompt (fairness: identical base prompt to B4/MAS), and
each refinement round runs against the same system prompt so the schema is held
constant; only the introspection user-prompt is new (appendix material).

Like b4r, this baseline deploys for real when ``deploy=True`` so the
intent-to-deploy metric is measured against a live cluster.
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

# Number of introspection→revise rounds after the initial draft.
N_REFINE = 2

INTROSPECTION_PROMPT = """\
INTROSPECTION FEEDBACK ROUND

Below is your current draft 5G deployment for the user's intent. Critically
review it as if you were an expert OAI 5G operator. Identify CONCRETE errors or
weaknesses in:
  - the VNF set (missing mandatory NFs such as nrf/amf/smf/upf/udm/udr/ausf,
    or NSSF when multiple slices are requested),
  - per-VNF resource sizing (requests/limits vs. the stated UE/throughput load),
  - Helm values correctness (images, ports, probes, resource blocks),
  - SLA fields and connections.

User intent: {intent}

Current draft (JSON):
{draft}

{sizing}

First reason briefly about the specific problems, then OUTPUT ONLY a single
corrected JSON object in the SAME schema as the draft (topology with per-VNF
`resources` and `helm_values`). Do not include commentary in the JSON.
"""


class ConfuciusRunner:
    """Single-LLM draft + introspective self-refinement loop."""

    name = "confucius"

    def __init__(self) -> None:
        self._llm = LLMCore()

    def run(self, intent: dict[str, Any], *, deploy: bool = True) -> RunArtifact:
        run_id = str(uuid.uuid4())
        start = time.time()
        error: str | None = None
        topology: dict[str, Any] = {}
        artifacts: list[dict[str, Any]] = []
        stage_log: list[dict[str, Any]] = []

        try:
            sizing = resource_sizing_reference(intent)
            sys_msg, user_msg = _build_runtime_prompt(intent)
            raw = self._llm.chat(sys_msg, user_msg + "\n\n" + sizing)
            draft = LLMCore._extract_json(raw)

            for rnd in range(N_REFINE):
                if not isinstance(draft, dict):
                    break
                crit_user = INTROSPECTION_PROMPT.format(
                    intent=intent["prompt"],
                    draft=json.dumps(draft)[:6000],
                    sizing=sizing,
                )
                revised_raw = self._llm.chat(sys_msg, crit_user)
                revised = LLMCore._extract_json(revised_raw)
                stage_log.append({
                    "stage": "introspection",
                    "round": rnd + 1,
                    "revised": isinstance(revised, dict),
                })
                if isinstance(revised, dict):
                    draft = revised

            if not isinstance(draft, dict):
                error = f"LLM returned non-JSON output (first 200 chars): {str(raw)[:200]}"
            else:
                topology, artifacts = _split_topology_and_artifacts(draft)
        except Exception as e:
            logger.exception("Confucius run failed for intent %s", intent.get("id"))
            error = str(e)

        artifact = RunArtifact({
            "system": self.name,
            "intent_id": intent.get("id"),
            "run_id": run_id,
            "topology": topology,
            "config_artifacts": artifacts,
            "validation_report": {},      # filled by deploy tail / harness
            "deployment_results": [],
            "intervention_log": [],        # autonomous refine rounds are NOT operator interventions
            "_stage_log": stage_log,       # internal traceability only (not scored)
            "wall_clock_s": time.time() - start,
            "deployment_wait_s": None,
            "release_names": list(CHART_MAP.values()),
            "error": error,
        })

        if deploy and not error:
            deploy_and_record(artifact)

        return artifact


register(ConfuciusRunner())
