"""
Lin et al. baseline — IR-first agentic framework with verification feedback.

Reference: Shan Lin et al., "An LLM-based Agentic Framework for Accessible
Network Control", ACM SIGMETRICS Performance Evaluation Review, 2025
(arXiv:2509.20600).

The framework's pipeline is: intent → intermediate representation (IR) →
retrieve network state from memory → action generation → verification with an
external-feedback self-correction loop. We reproduce that pipeline:

  1. IR: LLM converts the intent into a vendor-agnostic structured representation
     (required NFs, scale drivers, HA/slice flags) — distinct from the final
     Helm output.
  2. State grounding: inject the "network state" retrieved from memory. Experiment
     2 is pre-deployment with no live cluster, so memory = this testbed's known
     capability catalog (CHART_MAP VNF types) + the intent's structured features.
     (Stub documented in the appendix.)
  3. Action generation: LLM turns IR + state (+ any prior feedback) into the final
     topology + Helm config, using the shared B4 schema prompt.
  4. Verification + feedback: run the same static policy checks the harness scores
     with; if the decision is not GO, feed the violation reasons back as external
     feedback and regenerate (up to N_ATTEMPTS).

Deploys for real when ``deploy=True`` (intent-to-deploy metric). The validation
report produced by the final verification round is carried on the artifact and
reused by the deploy tail.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from agents.deployer import CHART_MAP
from core.llm_core import LLMCore
from experiments.baselines._deploy_common import build_validation_report, deploy_and_record
from experiments.baselines._sizing import resource_sizing_reference
from experiments.baselines.b4_single_llm import (
    _build_runtime_prompt,
    _split_topology_and_artifacts,
)
from experiments.common.runners import RunArtifact, register

logger = logging.getLogger(__name__)

# Max action-generation attempts (1 initial + up to N_ATTEMPTS-1 corrections).
N_ATTEMPTS = 2

# "Network state" retrieved from memory — the VNF types this testbed can deploy.
_NETWORK_STATE = (
    "Known deployable network functions (capability memory): "
    + ", ".join(sorted({k for k in CHART_MAP}))
)

IR_SYSTEM_PROMPT = """\
You are the INTENT-UNDERSTANDING stage of an LLM-based network-control agent.
Convert the user's natural-language intent into a vendor-agnostic INTERMEDIATE
REPRESENTATION (IR): a compact structured JSON capturing WHAT is required, not
HOW to configure it. Use this schema:
  {
    "required_nfs": [<core/RAN NF short names>],
    "scale_drivers": {"ue_count": int, "throughput_gbps": number, "sessions": int},
    "ha_required": bool,
    "slices": [<slice descriptors>],
    "sla": {<key>: <value>}
  }
Output ONLY the IR JSON.
"""


class LinEtAlRunner:
    """IR → state → action-generation → verify-and-correct agentic baseline."""

    name = "lin"

    def __init__(self) -> None:
        self._llm = LLMCore()

    def _build_ir(self, intent: dict[str, Any]) -> dict[str, Any]:
        feat = intent.get("structured_features") or {}
        user = (
            f"User intent: {intent['prompt']}\n\n"
            f"Structured features: {json.dumps(feat)}\n\n"
            f"Produce the IR JSON."
        )
        raw = self._llm.chat(IR_SYSTEM_PROMPT, user)
        ir = LLMCore._extract_json(raw)
        return ir if isinstance(ir, dict) else {}

    def _generate(
        self,
        intent: dict[str, Any],
        ir: dict[str, Any],
        feedback: str | None,
    ) -> dict[str, Any] | None:
        sys_msg, base_user = _build_runtime_prompt(intent)
        user = (
            base_user
            + f"\n\nIntermediate representation (IR) of the intent:\n{json.dumps(ir)[:4000]}"
            + f"\n\n{_NETWORK_STATE}"
            + f"\n\n{resource_sizing_reference(intent)}"
        )
        if feedback:
            user += (
                "\n\nEXTERNAL FEEDBACK — your previous attempt failed verification "
                "with these policy violations; fix them in this revision:\n"
                + feedback[:2000]
            )
        user += "\n\nOutput ONLY the single JSON topology+config object."
        raw = self._llm.chat(sys_msg, user)
        return LLMCore._extract_json(raw)

    def run(self, intent: dict[str, Any], *, deploy: bool = True) -> RunArtifact:
        run_id = str(uuid.uuid4())
        start = time.time()
        error: str | None = None
        topology: dict[str, Any] = {}
        artifacts: list[dict[str, Any]] = []
        stage_log: list[dict[str, Any]] = []
        validation_report: dict[str, Any] = {}

        try:
            ir = self._build_ir(intent)
            stage_log.append({"stage": "ir", "built": bool(ir)})

            feedback: str | None = None
            for attempt in range(N_ATTEMPTS):
                parsed = self._generate(intent, ir, feedback)
                if not isinstance(parsed, dict):
                    stage_log.append({"stage": "action", "attempt": attempt + 1,
                                      "parsed": False})
                    error = f"Action generation returned non-JSON (first 200): {str(parsed)[:200]}"
                    continue

                topology, artifacts = _split_topology_and_artifacts(parsed)
                error = None
                # Verification with external feedback.
                validation_report = build_validation_report(artifacts, topology)
                decision = validation_report.get("decision")
                stage_log.append({
                    "stage": "verify",
                    "attempt": attempt + 1,
                    "decision": decision,
                })
                if decision == "GO":
                    break
                feedback = "; ".join(validation_report.get("reasons") or []) or "policy NO_GO"
        except Exception as e:
            logger.exception("Lin et al. run failed for intent %s", intent.get("id"))
            error = str(e)

        artifact = RunArtifact({
            "system": self.name,
            "intent_id": intent.get("id"),
            "run_id": run_id,
            "topology": topology,
            "config_artifacts": artifacts,
            "validation_report": validation_report,  # from final verify round
            "deployment_results": [],
            "intervention_log": [],        # autonomous IR/verify rounds are not operator interventions
            "_stage_log": stage_log,       # internal traceability only (not scored)
            "wall_clock_s": time.time() - start,
            "deployment_wait_s": None,
            "release_names": list(CHART_MAP.values()),
            "error": error,
        })

        if deploy and not error:
            deploy_and_record(artifact)

        return artifact


register(LinEtAlRunner())
