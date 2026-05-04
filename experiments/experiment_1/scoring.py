"""
Scoring module for Experiment 1: Intent Translation Accuracy.

Two-tier scoring system:
  Tier 1 — Deterministic metrics (objective, formula-based)
  Tier 2 — LLM-as-Judge (qualitative, rubric-based)

References:
  - Jaccard index: Jaccard, P. (1912). The Distribution of the Flora in the Alpine Zone.
  - LLM-as-Judge: Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena" (2023).
  - 3GPP TS 23.501: System architecture for the 5G System (5GS).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.llm_core import LLMCore

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TIER 1 — Deterministic Metrics
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def vnf_coverage_score(
    expected_vnfs: list[str],
    generated_vnfs: list[dict[str, Any]],
) -> float:
    """
    Jaccard similarity between expected and generated VNF type sets.

    J(A, B) = |A ∩ B| / |A ∪ B|

    Returns a float in [0.0, 1.0].
    Returns -1.0 if expected_vnfs is empty (metric not applicable).
    """
    if not expected_vnfs:
        return -1.0  # sentinel: metric not applicable

    expected = {v.lower().replace("oai-", "") for v in expected_vnfs}
    generated = set()
    for vnf in generated_vnfs:
        vnf_type = vnf.get("type", vnf.get("name", "")).lower().replace("oai-", "")
        generated.add(vnf_type)

    if not expected and not generated:
        return 1.0

    intersection = expected & generated
    union = expected | generated
    return len(intersection) / len(union) if union else 0.0


def structural_completeness_score(topology: dict[str, Any]) -> float:
    """
    Check whether the topology contains all expected structural blocks.

    Checks for:
      - topology_id present                (weight: 0.1)
      - vnfs list with ≥1 VNF             (weight: 0.2)
      - connectivity block present         (weight: 0.2)
      - connectivity.plmn present          (weight: 0.1)
      - connectivity.slices present        (weight: 0.1)
      - connections list present           (weight: 0.1)
      - sla block present                  (weight: 0.1)
      - all VNFs have interfaces list      (weight: 0.1)

    Returns a float in [0.0, 1.0].
    """
    if not topology:
        return 0.0

    checks = {
        "topology_id": bool(topology.get("topology_id")),
        "vnfs_present": bool(topology.get("vnfs")) and len(topology.get("vnfs", [])) > 0,
        "connectivity": bool(topology.get("connectivity")),
        "plmn": bool(topology.get("connectivity", {}).get("plmn")),
        "slices": bool(topology.get("connectivity", {}).get("slices")),
        "connections": isinstance(topology.get("connections"), list),
        "sla": bool(topology.get("sla")),
        "vnf_interfaces": all(
            isinstance(v.get("interfaces"), list)
            for v in topology.get("vnfs", [])
        ) if topology.get("vnfs") else False,
    }

    weights = {
        "topology_id": 0.1,
        "vnfs_present": 0.2,
        "connectivity": 0.2,
        "plmn": 0.1,
        "slices": 0.1,
        "connections": 0.1,
        "sla": 0.1,
        "vnf_interfaces": 0.1,
    }

    return sum(weights[k] * (1.0 if v else 0.0) for k, v in checks.items())


def resource_validity_score(vnfs: list[dict[str, Any]]) -> float:
    """
    Check resource specifications for all VNFs.

    For each VNF, checks:
      - resources.requests.cpu exists
      - resources.requests.memory exists
      - resources.limits.cpu exists
      - resources.limits.memory exists

    Returns the fraction of VNFs that pass all checks.
    """
    if not vnfs:
        return 0.0

    valid_count = 0
    for vnf in vnfs:
        res = vnf.get("resources", {})
        req = res.get("requests", {})
        lim = res.get("limits", {})

        has_all = all([
            req.get("cpu"),
            req.get("memory"),
            lim.get("cpu"),
            lim.get("memory"),
        ])
        if has_all:
            valid_count += 1

    return valid_count / len(vnfs)


def policy_pass_rate(validation_report: dict[str, Any]) -> float:
    """
    Compute the ratio of PASS policy checks.

    Returns a float in [0.0, 1.0].
    """
    checks = validation_report.get("policy_checks", {})
    if not checks:
        return 0.0

    total = len(checks)
    passed = sum(1 for c in checks.values() if c.get("status") == "PASS")
    return passed / total


def deployment_success_rate(deployment_results: list[dict[str, Any]]) -> float:
    """
    Ratio of successful deployments over *attempted* deployments.

    `skipped` results — VNFs whose chart is not in the deployer's CHART_MAP
    (e.g. ueransim-gnb / ueransim-ue when only OAI core charts are wired in)
    — are excluded from the denominator. They represent a deployer-vocabulary
    gap, not a deployment failure, and a skipped result was never a real
    install attempt.

    Returns 0.0 if nothing was attempted.
    """
    if not deployment_results:
        return 0.0

    attempted = [r for r in deployment_results if r.get("status") != "skipped"]
    if not attempted:
        return 0.0

    success = sum(
        1 for r in attempted
        if r.get("status") in ("installed", "upgraded")
    )
    return success / len(attempted)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  TIER 2 — LLM-as-Judge
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


LLM_JUDGE_PROMPT = """You are an expert 5G network architect acting as an impartial judge.

You are given:
1. A natural language intent describing desired 5G network requirements.
2. A generated 5G network topology (JSON) produced by an AI system in response to that intent.

Your task is to evaluate how well the generated topology satisfies the original intent.

Score each dimension on a scale of 1–5:
  1 = Completely wrong / missing
  2 = Major issues
  3 = Partially correct, notable gaps
  4 = Mostly correct, minor issues
  5 = Fully correct and appropriate

Dimensions:
  - completeness: Does the topology include all VNFs implied by the intent?
  - correctness: Are VNF types and dependencies valid per 3GPP TS 23.501?
  - resource_appropriateness: Are the allocated resources proportional to the scale described in the intent?
  - slice_alignment: Do the selected S-NSSAI slices (SST/SD) match the use cases in the intent?
  - sla_fitness: Are the SLA targets (latency, throughput, availability) reasonable for the described scenario?

Return ONLY a valid JSON object with the following structure (no markdown, no explanation outside the JSON):
{{
  "completeness": <int 1-5>,
  "correctness": <int 1-5>,
  "resource_appropriateness": <int 1-5>,
  "slice_alignment": <int 1-5>,
  "sla_fitness": <int 1-5>,
  "justification": "<brief textual justification covering all dimensions>"
}}

--- INTENT ---
{intent}

--- GENERATED TOPOLOGY ---
{topology_json}
"""


def llm_as_judge(
    intent: str,
    topology: dict[str, Any],
    resource_allocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Use an LLM to evaluate the generated topology against the original intent.

    Returns a dict with per-dimension scores (1–5), a justification string,
    and a normalised composite score (0.0–1.0).
    """
    # Merge resource info into topology for richer evaluation
    eval_topology = dict(topology)
    if resource_allocation and resource_allocation.get("vnfs"):
        eval_topology["vnfs"] = resource_allocation["vnfs"]

    topology_json = json.dumps(eval_topology, indent=2, default=str)

    prompt_text = LLM_JUDGE_PROMPT.format(
        intent=intent,
        topology_json=topology_json,
    )

    try:
        llm = LLMCore()

        system_prompt = (
            "You are an expert 5G network architect acting as an impartial judge. "
            "You evaluate AI-generated 5G topologies against the original human intent. "
            "Always respond with ONLY valid JSON, no markdown fences or extra text."
        )

        user_prompt = LLM_JUDGE_PROMPT.format(
            intent=intent,
            topology_json=topology_json,
        )

        raw_response = llm.chat(system_prompt, user_prompt)
        result = LLMCore._extract_json(raw_response)

        if result is None:
            logger.warning("LLM-as-Judge returned unparseable response: %s", raw_response[:300])
            return {
                "scores": {d: 0 for d in ["completeness", "correctness",
                                           "resource_appropriateness",
                                           "slice_alignment", "sla_fitness"]},
                "composite": 0.0,
                "justification": f"Could not parse LLM response: {raw_response[:200]}",
                "error": "JSON parse failed",
            }

        # Validate and extract scores
        dimensions = [
            "completeness", "correctness", "resource_appropriateness",
            "slice_alignment", "sla_fitness",
        ]
        scores = {}
        for dim in dimensions:
            val = result.get(dim, 3)
            scores[dim] = max(1, min(5, int(val)))

        # Compute normalised composite (0.0–1.0)
        raw_sum = sum(scores.values())
        composite = (raw_sum - 5) / 20.0  # maps [5, 25] → [0.0, 1.0]

        return {
            "scores": scores,
            "composite": round(composite, 4),
            "justification": result.get("justification", "No justification provided."),
        }

    except Exception as e:
        logger.error("LLM-as-Judge failed: %s", e)
        return {
            "scores": {d: 0 for d in ["completeness", "correctness",
                                       "resource_appropriateness",
                                       "slice_alignment", "sla_fitness"]},
            "composite": 0.0,
            "justification": f"LLM-as-Judge evaluation failed: {e}",
            "error": str(e),
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Composite Score
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def compute_composite_score(
    scores: dict[str, float],
    has_expected_vnfs: bool = True,
) -> float:
    """
    Compute weighted composite score from individual metrics.

    Weights (when expected_vnfs IS available):
      vnf_coverage:           0.15
      structural_completeness: 0.10
      resource_validity:       0.10
      policy_pass_rate:        0.10
      deployment_success_rate: 0.10
      llm_judge:               0.45

    When expected_vnfs is NOT available, vnf_coverage weight
    is redistributed to llm_judge (→ 0.60).
    """
    if has_expected_vnfs:
        weights = {
            "vnf_coverage": 0.15,
            "structural_completeness": 0.10,
            "resource_validity": 0.10,
            "policy_pass_rate": 0.10,
            "deployment_success_rate": 0.10,
            "llm_judge": 0.45,
        }
    else:
        weights = {
            "structural_completeness": 0.10,
            "resource_validity": 0.10,
            "policy_pass_rate": 0.10,
            "deployment_success_rate": 0.10,
            "llm_judge": 0.60,
        }

    total = 0.0
    for metric, weight in weights.items():
        val = scores.get(metric, 0.0)
        if val < 0:  # sentinel for N/A
            continue
        total += weight * val

    return round(total, 4)
