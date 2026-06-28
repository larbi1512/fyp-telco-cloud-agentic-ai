"""
Shared deploy tail for the deploying baselines (confucius, ossgpt, lin).

These three baselines plan with an LLM and then deploy for real so the
intent-to-deploy metric is measured against a live cluster — exactly like
``b4r``. The deploy branch is identical across all three, so it lives here
instead of being copy-pasted. It mirrors B4WithRepairRunner.run()'s deploy
branch (experiments/baselines/b4_with_repair.py).
"""

from __future__ import annotations

import time
from typing import Any

from agents.deployer import deployer_agent
from agents.policy_validator import determine_decision, run_static_policies


def build_validation_report(
    cfgs: list[dict[str, Any]], topology: dict[str, Any] | None
) -> dict[str, Any]:
    """Run the same static policy checks the harness scores with and package
    them into the validation_report shape the deployer + metrics expect."""
    checks = run_static_policies(cfgs)
    decision, reasons = determine_decision(checks)
    return {
        "topology_id": (topology or {}).get("topology_id"),
        "policy_checks": checks,
        "dry_run": {"status": "SKIPPED", "errors": []},
        "conflicts": [],
        "decision": decision,
        "reasons": reasons,
        "violation_count": sum(int(c.get("violations", 0)) for c in checks.values()),
    }


def deploy_and_record(artifact: dict[str, Any]) -> None:
    """Mutate ``artifact`` in place: ensure a validation_report, run the real
    deployer, record deployment_results, and add the helm-install wall-clock.

    If the runner already populated validation_report (e.g. Lin et al.'s
    verification loop), that report is reused rather than recomputed.
    """
    cfgs = artifact.get("config_artifacts") or []
    if not artifact.get("validation_report"):
        artifact["validation_report"] = build_validation_report(
            cfgs, artifact.get("topology")
        )

    deploy_start = time.time()
    result = deployer_agent({
        "config_artifacts": cfgs,
        "validation_report": artifact["validation_report"],
        "clean_deploy": True,
    })
    artifact["wall_clock_s"] = artifact.get("wall_clock_s", 0.0) + (
        time.time() - deploy_start
    )
    artifact["deployment_results"] = result.get("deployment_results") or []
    if result.get("error") and not artifact.get("error"):
        artifact["error"] = result["error"]
