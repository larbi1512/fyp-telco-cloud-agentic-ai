"""
Six-metric scorer — the single function that turns a system's RunArtifact
into a metrics record.

Inputs (all from a single (system, intent) run):
  - artifact: a dict with keys topology, config_artifacts, validation_report,
    deployment_results, intervention_log, wall_clock_s, deployment_wait_s
    (and optionally synthetic_critic_pre — see below).
  - intent: a dict from intents_v2.json including structured_features.

Outputs the six pre-deployment metrics plus the legacy Experiment 1 metrics
(reused via experiments.experiment_1.scoring) for backward comparability.

The six metrics, per plan:
  1. deployment_time_s        — wall-clock from intent submission to last pod Ready.
  2. config_error_rate        — fraction of config_artifacts that fail helm template.
  3. resource_accuracy        — 1 - mean(|generated - optimal| / optimal) clipped to [0,1].
  4. interventions            — gates_triggered + would_have_rejected_by_critic.
  5. intent_to_deploy_acc     — 1 iff deploy_success AND coverage>=0.8 AND policy_go AND critic_ok.
  6. policy_violation_rate    — 1 - policy_pass_rate (ratio in [0,1]).
"""

from __future__ import annotations

import logging
from typing import Any

from experiments.common.config_validation import validate_artifacts
from experiments.common.resource_oracle import (
    VNF_TYPE_TO_PROFILE,
    compute_optimal_resources,
    parse_vnf_resources,
)
from experiments.common.synthetic_critic import critic_verdict
from experiments.experiment_1.scoring import (
    deployment_success_rate,
    policy_pass_rate,
    resource_validity_score,
    structural_completeness_score,
    vnf_coverage_score,
)

logger = logging.getLogger(__name__)


COVERAGE_THRESHOLD_FOR_INTENT_ACCURACY = 0.8


def _resource_accuracy(
    topology: dict[str, Any],
    intent_features: dict[str, Any],
    resource_allocation: dict[str, Any] | None = None,
    config_artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Compute Metric 3: per-VNF symmetric relative error against the oracle,
    averaged. Returns score in [0, 1] plus per-VNF deltas for diagnostics.

    Resources can live in any of three places depending on the system:
      - topology.vnfs[i].resources                    (B1, B2, B3, sometimes B4)
      - resource_allocation.vnfs[i].resources         (MAS — Resource Allocator
                                                       enriches a copy of the
                                                       VNF list, leaving the
                                                       planner's bare topology
                                                       untouched)
      - config_artifacts[i].helm_values.resources     (MAS, B4 — final shape)
    The function tries each in order until it finds CPU+memory.
    """
    optimal = compute_optimal_resources(intent_features or {})
    deltas: list[dict[str, Any]] = []

    # Build a name→resources lookup from each fallback source.
    _known_alloc = set(VNF_TYPE_TO_PROFILE.keys())
    alloc_by_type: dict[str, dict[str, Any]] = {}
    for v in (resource_allocation or {}).get("vnfs") or []:
        raw_t = (v.get("type") or "").lower().replace("oai-", "")
        t = raw_t if raw_t in _known_alloc else (v.get("name") or "").lower().replace("oai-", "")
        if t:
            alloc_by_type[t] = v

    artifact_by_name: dict[str, dict[str, Any]] = {}
    for cfg in config_artifacts or []:
        name = (cfg.get("vnf_name") or "").lower().replace("oai-", "")
        if name:
            artifact_by_name[name] = cfg

    _known_types = set(VNF_TYPE_TO_PROFILE.keys())
    for vnf in topology.get("vnfs") or []:
        raw_type = (vnf.get("type") or "").lower().replace("oai-", "")
        # Fall back to name when type is not a recognized short form
        vnf_type = raw_type if raw_type in _known_types else (vnf.get("name") or "").lower().replace("oai-", "")
        if vnf_type not in VNF_TYPE_TO_PROFILE:
            continue
        gen = parse_vnf_resources(vnf)
        if gen is None and vnf_type in alloc_by_type:
            gen = parse_vnf_resources(alloc_by_type[vnf_type])
        if gen is None and vnf_type in artifact_by_name:
            # parse_vnf_resources already handles the helm_values.resources.requests.nf
            # shape via its "nf" branch, but we need to feed it the helm_values dict
            # wrapped to look like a vnf entry.
            hv = artifact_by_name[vnf_type].get("helm_values") or {}
            gen = parse_vnf_resources({"resources": hv.get("resources") or {}})
        if gen is None:
            deltas.append({"vnf": vnf_type, "missing": True})
            continue

        opt = optimal[vnf_type]
        cpu_err = abs(gen["cpu_m"] - opt["cpu_m"]) / max(opt["cpu_m"], 1)
        mem_err = abs(gen["memory_mi"] - opt["memory_mi"]) / max(opt["memory_mi"], 1)
        deltas.append({
            "vnf": vnf_type,
            "gen_cpu_m": gen["cpu_m"], "opt_cpu_m": opt["cpu_m"],
            "gen_mem_mi": gen["memory_mi"], "opt_mem_mi": opt["memory_mi"],
            "cpu_rel_err": round(cpu_err, 3),
            "mem_rel_err": round(mem_err, 3),
        })

    measured = [d for d in deltas if "cpu_rel_err" in d]
    if not measured:
        return {"score": 0.0, "deltas": deltas, "n_measured": 0}

    mean_err = sum(
        (d["cpu_rel_err"] + d["mem_rel_err"]) / 2 for d in measured
    ) / len(measured)
    score = max(0.0, min(1.0, 1.0 - mean_err))

    return {
        "score": round(score, 4),
        "deltas": deltas,
        "n_measured": len(measured),
        "mean_relative_error": round(mean_err, 4),
    }


def _interventions(
    intervention_log: list[dict[str, Any]] | None,
    critic: dict[str, Any],
    validation_report: dict[str, Any],
) -> dict[str, Any]:
    """
    Metric 4: gates_triggered + would_have_rejected_by_critic.

    `gates_triggered` is the number of HITL gate visits (auto-approved or not)
    that the harness logged into intervention_log. The critic's verdict adds 1
    if it would have rejected. For systems without a graph (B1, B2, B4) the
    `intervention_log` may be empty — the policy validator's FAIL count plus
    the critic's verdict are the proxies, captured by the runners.
    """
    gates_triggered = len(intervention_log or [])
    critic_rejected = 1 if not critic["would_approve"] else 0

    # Forced interventions: when the policy validator has FAIL items in its
    # report, those are operator-blocking by definition. Each FAIL counts.
    policy_fails = 0
    for check in (validation_report or {}).get("policy_checks", {}).values():
        if check.get("status") == "FAIL":
            policy_fails += 1

    return {
        "interventions": gates_triggered + critic_rejected + policy_fails,
        "gates_triggered": gates_triggered,
        "critic_rejected": critic_rejected,
        "policy_fails": policy_fails,
    }


def score(artifact: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    """
    Compute the metrics record for a single (system, intent) run.

    `artifact` shape (any missing keys default to empty/None):
        topology:           dict | None
        config_artifacts:   list[dict]
        validation_report:  dict | None
        deployment_results: list[dict]
        intervention_log:   list[dict]
        wall_clock_s:       float       — time from intent submit to artifact ready
        deployment_wait_s:  float | None — additional wait for pods Ready (if deployed)
        system:             str         — e.g. "mas", "b1", "b2", "b3", "b4"
        run_id:             str
        rep:                int
        error:              str | None

    `intent` is one row from intents_v2.json["prompts"].
    """
    topology = artifact.get("topology") or {}
    config_artifacts = artifact.get("config_artifacts") or []
    validation = artifact.get("validation_report") or {}
    deployment_results = artifact.get("deployment_results") or []
    intervention_log = artifact.get("intervention_log") or []

    intent_features = intent.get("structured_features") or {}
    expected_vnfs = intent.get("expected_vnfs") or []

    # Metric 1 — deployment time
    wall_clock_s = float(artifact.get("wall_clock_s") or 0.0)
    deploy_wait_s = artifact.get("deployment_wait_s")
    deployment_time_s = wall_clock_s + (float(deploy_wait_s) if deploy_wait_s else 0.0)

    # Metric 2 — config error rate
    config_validation = validate_artifacts(config_artifacts)
    config_error_rate = config_validation["error_rate"]

    # Metric 3 — resource accuracy
    resource_accuracy_block = _resource_accuracy(
        topology,
        intent_features,
        resource_allocation=artifact.get("resource_allocation"),
        config_artifacts=config_artifacts,
    )

    # Metric 6 — policy violation rate (also feeds Metric 5's policy_go term)
    policy_pass = policy_pass_rate(validation)
    policy_violation_rate = max(0.0, 1.0 - policy_pass)
    policy_go = (validation.get("decision") == "GO")

    # Synthetic critic (consumed by Metric 4 and Metric 5)
    critic = critic_verdict(
        intent_text=intent.get("prompt", ""),
        topology=topology,
        config_artifacts=config_artifacts,
        validation_report=validation,
        intent_features=intent_features,
    )

    # Metric 4 — interventions
    interventions_block = _interventions(intervention_log, critic, validation)

    # Components for Metric 5
    deploy_success_rate = deployment_success_rate(deployment_results)
    coverage = vnf_coverage_score(expected_vnfs, topology.get("vnfs") or [])
    coverage_ok = (coverage < 0) or (coverage >= COVERAGE_THRESHOLD_FOR_INTENT_ACCURACY)
    intent_accuracy_components = {
        "deployment_success": deploy_success_rate >= 1.0,
        "coverage_ok": coverage_ok,
        "policy_go": policy_go,
        "critic_approved": critic["would_approve"],
    }
    intent_to_deploy_accuracy = 1 if all(intent_accuracy_components.values()) else 0

    # Legacy Experiment 1 metrics for back-compat
    legacy = {
        "structural_completeness": structural_completeness_score(topology),
        "resource_validity": resource_validity_score(topology.get("vnfs") or []),
        "vnf_coverage": coverage,
        "policy_pass_rate": policy_pass,
        "deployment_success_rate": deploy_success_rate,
    }

    return {
        # Identity
        "system": artifact.get("system"),
        "intent_id": intent.get("id"),
        "rep": artifact.get("rep"),
        "run_id": artifact.get("run_id"),
        "complexity": intent.get("complexity"),
        "ue_band": intent.get("ue_band"),
        "scenario": intent.get("scenario"),
        "category": intent.get("category"),
        "error": artifact.get("error"),

        # Six pre-deployment metrics (the headline)
        "deployment_time_s": round(deployment_time_s, 3),
        "config_error_rate": round(config_error_rate, 4),
        "resource_accuracy": resource_accuracy_block["score"],
        "interventions": interventions_block["interventions"],
        "intent_to_deploy_accuracy": intent_to_deploy_accuracy,
        "policy_violation_rate": round(policy_violation_rate, 4),

        # Diagnostic detail (saved to JSONL but not the flat CSV)
        "components": {
            "config_validation": config_validation,
            "resource_accuracy_detail": resource_accuracy_block,
            "interventions_detail": interventions_block,
            "intent_accuracy_components": intent_accuracy_components,
            "critic": {
                "would_approve": critic["would_approve"],
                "reasons": critic["reasons"],
            },
        },

        # Legacy back-compat
        "legacy": legacy,
    }
