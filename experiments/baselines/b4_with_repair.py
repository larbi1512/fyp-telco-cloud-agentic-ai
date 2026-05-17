"""
B4 + deterministic topology repair (strict variant).

Reuses B4's single-LLM output and pipes it through the same two functions the
MAS Network Planner uses post-LLM:

  - agents.network_planner._repair_topology()       (mandatory VNF set, NSSF
                                                     on multi-slice, HA replicas,
                                                     slice-count repair)
  - agents.network_planner._filter_invalid_connections() (drops DNN / RAN-sim
                                                          endpoints)

Nothing else from the MAS pipeline runs: no templated VNF Configurator, no
HPA injection, no resource-allocator reasoning. This isolates the contribution
of the two repair functions from the rest of the multi-agent decomposition.

When ``deploy=True`` the runner reuses agents.deployer.deployer_agent so that
``deployment_results`` is populated and the harness's wait_for_pods_ready
step is exercised — same code path as MAS.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agents.deployer import deployer_agent
from agents.network_planner import _filter_invalid_connections, _repair_topology
from agents.policy_validator import determine_decision, run_static_policies
from experiments.baselines.b4_single_llm import B4SingleLLMRunner
from experiments.common.runners import RunArtifact, register

logger = logging.getLogger(__name__)


class B4WithRepairRunner:
    """B4 single-LLM output piped through the MAS topology-repair functions."""

    name = "b4r"

    def __init__(self) -> None:
        self._b4 = B4SingleLLMRunner()

    def run(self, intent: dict[str, Any], *, deploy: bool = True) -> RunArtifact:
        # 1. Run B4 as artefact-only — we drive the deployer ourselves.
        artifact = self._b4.run(intent, deploy=False)
        artifact["system"] = self.name

        # 2. Apply the two MAS repair functions in place on the topology dict.
        feat = intent.get("structured_features")
        topology = artifact.get("topology") or {}
        repair_actions = _repair_topology(topology, feat)
        dropped = _filter_invalid_connections(topology)
        artifact["topology"] = topology
        artifact["_repair_actions"] = repair_actions
        artifact["_dropped_connections"] = dropped

        if repair_actions:
            logger.info("b4r repair applied: %s", ", ".join(repair_actions))
        if dropped:
            logger.info("b4r dropped %d invalid connection(s)", dropped)

        # 3. If asked to deploy, populate validation_report and call the
        #    standard deployer_agent. Track wall-clock added by the deploy step
        #    so the metric reflects the real `helm install` time.
        if deploy:
            cfgs = artifact.get("config_artifacts") or []
            checks = run_static_policies(cfgs)
            decision, reasons = determine_decision(checks)
            artifact["validation_report"] = {
                "topology_id": topology.get("topology_id"),
                "policy_checks": checks,
                "dry_run": {"status": "SKIPPED", "errors": []},
                "conflicts": [],
                "decision": decision,
                "reasons": reasons,
                "violation_count": sum(
                    int(c.get("violations", 0)) for c in checks.values()
                ),
            }

            deploy_start = time.time()
            result = deployer_agent({
                "config_artifacts": cfgs,
                "validation_report": artifact["validation_report"],
                "clean_deploy": True,
            })
            artifact["wall_clock_s"] = (
                artifact.get("wall_clock_s", 0.0) + (time.time() - deploy_start)
            )
            artifact["deployment_results"] = result.get("deployment_results") or []
            if result.get("error") and not artifact.get("error"):
                artifact["error"] = result["error"]

        return artifact


register(B4WithRepairRunner())
