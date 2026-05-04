"""
Planner / Reasoning Agent — The Decision Maker.

The fourth and central node of the post-deployment loop. Reads the
structured outputs of the KPI Monitor, Anomaly Detector, and SLA
Compliance auditor, decides *whether* the cluster needs remediation and,
if so, *what* the remediation should be. Produces a single
``RemediationPlan`` for the executor agents (Auto-Scaler, Fault
Recovery) to carry out.

Pipeline position: 4th in post-deployment loop
  KPI Monitor → Anomaly Detector → SLA Compliance → [Planner] → Executors

Design — LLM-driven with deterministic guard rails:
  1. Deterministic gate: skip the cycle entirely if no alert / SLA
     breach / SLA warning is present.
  2. Trigger selection: deterministically rank the available signals and
     pick the highest-severity one as the focus of the plan.
  3. LLM reasoning: hand the trigger + full context + available action
     whitelist to the LLM and ask for a JSON plan.
  4. Schema validation: enforce the action whitelist, target validity,
     confidence clamping, non-empty action list. Reject invalid plans.
  5. Deterministic fallback: if the LLM call fails or its output is
     rejected, synthesise a minimum heuristic plan keyed on the trigger.

Input:  OrchestratorState with current_metrics, anomaly_alerts, sla_status,
        topology, resource_allocation, execution_results
Output: OrchestratorState with remediation_plan (single object, overwritten
        each cycle), messages, current_agent, phase
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

from core.llm_core import LLMCore
from core.state import (
    AnomalyAlert,
    MetricEvent,
    OrchestratorState,
    RemediationPlan,
    SLAStatus,
)

try:
    from infra.redis_client import RedisClient as _RedisClient
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)


# ──────────────────── Action whitelist ──────────────────── #

ACTION_TYPES: tuple[str, ...] = (
    "horizontal_scale",
    "vertical_scale",
    "restart",
    "rollback",
    "config_change",
)

_AVAILABLE_ACTIONS = [
    {"type": "horizontal_scale",
     "description": "Increase or decrease the replica count of a VNF Deployment"},
    {"type": "vertical_scale",
     "description": "Adjust CPU / memory limits of a VNF and trigger a rolling update"},
    {"type": "restart",
     "description": "Roll the VNF Deployment to clear stale state"},
    {"type": "rollback",
     "description": "Revert the VNF's Helm release to the previous revision"},
    {"type": "config_change",
     "description": "Edit a ConfigMap key and roll the affected VNF"},
]


# ──────────────────── Trigger selection ──────────────────── #


_TRIGGER_NONE = 0
_TRIGGER_ANOMALY_MEDIUM = 1
_TRIGGER_SLA_WARNING = 2
_TRIGGER_ANOMALY_HIGH = 3
_TRIGGER_SLA_BREACH = 4


def _rank_anomaly(alert: AnomalyAlert) -> int:
    conf = float(alert.get("confidence", 0.0))
    if conf >= 0.85:
        return _TRIGGER_ANOMALY_HIGH
    if conf >= 0.70:
        return _TRIGGER_ANOMALY_MEDIUM
    return _TRIGGER_NONE


def _rank_sla(status: SLAStatus) -> int:
    s = status.get("status")
    if s == "breach":
        return _TRIGGER_SLA_BREACH
    if s == "warning":
        return _TRIGGER_SLA_WARNING
    return _TRIGGER_NONE


def _select_trigger(
    alerts: list[AnomalyAlert],
    sla_statuses: list[SLAStatus],
) -> tuple[int, dict[str, Any] | None, str]:
    """
    Return (rank, payload, kind) for the highest-severity trigger.
    *kind* is "anomaly" | "sla" | "" so the caller can branch on it.
    """
    best_rank = _TRIGGER_NONE
    best_payload: dict[str, Any] | None = None
    best_kind = ""

    for a in alerts or []:
        r = _rank_anomaly(a)
        if r > best_rank:
            best_rank, best_payload, best_kind = r, dict(a), "anomaly"

    for s in sla_statuses or []:
        r = _rank_sla(s)
        if r > best_rank:
            best_rank, best_payload, best_kind = r, dict(s), "sla"

    return best_rank, best_payload, best_kind


# ──────────────────── Heuristic fallback plan ──────────────────── #


def _heuristic_action(
    trigger_kind: str,
    payload: dict[str, Any],
    topology_vnfs: list[str],
) -> dict[str, Any]:
    """
    Map the trigger to a single sensible default action. Used by the
    fallback plan when the LLM is unavailable or returns garbage.
    """
    if trigger_kind == "anomaly":
        affected = (payload.get("affected_metrics") or [{}])[0]
        metric = affected.get("name", "")
        target = _first_affected_vnf(payload, topology_vnfs)
        atype = payload.get("type", "")

        if atype == "instability":
            return {
                "order": 1,
                "type": "rollback",
                "target": target,
                "action": "helm_rollback",
                "params": {},
                "reason": "Pod-restart spike — most recent release is the prime suspect",
            }
        if metric == "memory_utilization":
            return {
                "order": 1,
                "type": "vertical_scale",
                "target": target,
                "action": "increase_memory_limit",
                "params": {"factor": 1.5},
                "reason": "Memory pressure on a stateful path — vertical scaling avoids fragmentation",
            }
        return {
            "order": 1,
            "type": "horizontal_scale",
            "target": target,
            "action": "scale_replicas",
            "params": {"delta": 1},
            "reason": "Compute saturation on a horizontally-scalable VNF",
        }

    if trigger_kind == "sla":
        rule = payload.get("rule", "")
        if "throughput" in rule:
            return {
                "order": 1,
                "type": "horizontal_scale",
                "target": _vnf_for_rule(rule, topology_vnfs),
                "action": "scale_replicas",
                "params": {"delta": 1},
                "reason": "Throughput SLA breach typically responds to user-plane scale-out",
            }
        if "availability" in rule:
            return {
                "order": 1,
                "type": "restart",
                "target": _vnf_for_rule(rule, topology_vnfs),
                "action": "rollout_restart",
                "params": {},
                "reason": "Availability SLA breach — clear stale state with a rolling restart",
            }
        if "latency" in rule:
            return {
                "order": 1,
                "type": "horizontal_scale",
                "target": _vnf_for_rule(rule, topology_vnfs),
                "action": "scale_replicas",
                "params": {"delta": 1},
                "reason": "Latency SLA breach — additional replicas reduce per-instance load",
            }

    return {
        "order": 1,
        "type": "restart",
        "target": topology_vnfs[0] if topology_vnfs else "unknown",
        "action": "rollout_restart",
        "params": {},
        "reason": "Generic recovery: roll the deployment to clear transient faults",
    }


def _first_affected_vnf(
    payload: dict[str, Any], topology_vnfs: list[str]
) -> str:
    """Extract the VNF name from an alert payload, falling back sensibly."""
    if (vnf := payload.get("vnf_name")):
        return str(vnf)
    desc = str(payload.get("description", ""))
    for v in topology_vnfs:
        if v in desc:
            return v
    return topology_vnfs[0] if topology_vnfs else "unknown"


def _vnf_for_rule(rule: str, topology_vnfs: list[str]) -> str:
    """Pick a plausible target VNF for an SLA rule when the LLM is absent."""
    if "upf" in rule.lower() or "throughput" in rule or "packet_loss" in rule:
        for v in topology_vnfs:
            if "upf" in v:
                return v
    if "registration" in rule:
        for v in topology_vnfs:
            if "amf" in v:
                return v
    if "pdu_session" in rule:
        for v in topology_vnfs:
            if "smf" in v:
                return v
    return topology_vnfs[0] if topology_vnfs else "unknown"


# ──────────────────── Plan validation ──────────────────── #


def _validate_plan(
    plan: dict[str, Any], topology_vnfs: list[str]
) -> tuple[RemediationPlan | None, list[str]]:
    """
    Check that an LLM-produced plan respects the schema and the action
    whitelist. Returns (clean_plan, issues) — clean_plan is None if any
    *fatal* issue was found.
    """
    issues: list[str] = []

    if not isinstance(plan, dict):
        return None, ["plan is not an object"]

    actions = plan.get("recommended_actions")
    if not isinstance(actions, list) or not actions:
        return None, ["recommended_actions is missing or empty"]

    cleaned_actions: list[dict[str, Any]] = []
    for i, raw in enumerate(actions):
        if not isinstance(raw, dict):
            issues.append(f"action[{i}] is not an object")
            continue
        atype = raw.get("type")
        if atype not in ACTION_TYPES:
            issues.append(f"action[{i}].type={atype!r} not in whitelist")
            continue
        target = raw.get("target")
        if not isinstance(target, str) or not target:
            issues.append(f"action[{i}].target missing")
            continue
        if topology_vnfs and target not in topology_vnfs:
            issues.append(
                f"action[{i}].target={target!r} not in topology VNFs"
            )
            # Soft-correct: snap to the closest known VNF if any prefix matches.
            snapped = next(
                (v for v in topology_vnfs if target.startswith(v) or v.startswith(target)),
                None,
            )
            if snapped is None:
                continue
            target = snapped
        cleaned_actions.append({
            "order": int(raw.get("order", i + 1)),
            "type": atype,
            "target": target,
            "action": str(raw.get("action", atype)),
            "params": raw.get("params") or {},
            "reason": str(raw.get("reason", "")),
        })

    if not cleaned_actions:
        return None, issues + ["no valid actions after filtering"]

    confidence = float(plan.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))

    cleaned: RemediationPlan = {
        "plan_id": str(plan.get("plan_id") or ""),
        "triggered_by": str(plan.get("triggered_by") or ""),
        "diagnosis": str(plan.get("diagnosis") or "(no diagnosis provided)"),
        "confidence": confidence,
        "recommended_actions": cleaned_actions,
        "expected_outcome": str(plan.get("expected_outcome") or ""),
        "rollback_plan": str(plan.get("rollback_plan") or ""),
        "validation_metric": str(plan.get("validation_metric") or ""),
    }
    return cleaned, issues


# ──────────────────── LLM call ──────────────────── #


def _ask_llm(
    trigger_payload: dict[str, Any],
    metrics: list[MetricEvent],
    alerts: list[AnomalyAlert],
    sla_statuses: list[SLAStatus],
    recent_actions: list[dict[str, Any]],
    past_incidents: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    try:
        llm = LLMCore()
        result = llm.invoke(
            "planner_reasoning",
            {
                "alert_details": trigger_payload,
                "system_context": {
                    "current_metrics": metrics,
                    "anomaly_alerts": alerts,
                    "sla_status": sla_statuses,
                },
                "recent_actions": recent_actions,
                "past_incidents": past_incidents or [],
                "available_actions": _AVAILABLE_ACTIONS,
            },
            expect_json=True,
        )
    except Exception as exc:
        logger.warning("Planner LLM invocation failed: %s", exc)
        return None
    return result if isinstance(result, dict) else None


# ──────────────────── Plan ID + summary ──────────────────── #


def _make_plan_id(triggered_by: str, ts: str) -> str:
    digest = hashlib.sha1(f"{triggered_by}|{ts}".encode()).hexdigest()[:8]
    return f"plan-{ts}-{digest}"


def _build_summary(plan: RemediationPlan, source: str) -> str:
    lines = [
        f"**Planner / Reasoning** — remediation plan ({source})",
        "",
        f"- Plan: `{plan['plan_id']}`",
        f"- Triggered by: {plan['triggered_by']}",
        f"- Confidence: {plan['confidence']:.2f}",
        f"- Diagnosis: {plan['diagnosis']}",
        "",
        "**Actions:**",
    ]
    for a in plan["recommended_actions"][:10]:
        lines.append(
            f"  {a['order']}. {a['type']} → {a['target']}  "
            f"({a.get('action', '')}; {a.get('reason', '')})"
        )
    if plan.get("expected_outcome"):
        lines.append(f"\n**Expected outcome:** {plan['expected_outcome']}")
    if plan.get("rollback_plan"):
        lines.append(f"**Rollback:** {plan['rollback_plan']}")
    if plan.get("validation_metric"):
        lines.append(f"**Validation metric:** {plan['validation_metric']}")
    return "\n".join(lines)


def _build_no_plan_message() -> str:
    return (
        "**Planner / Reasoning** — no remediation needed.\n"
        "No alerts and no SLA breaches or warnings on this cycle."
    )


# ──────────────────── Topology helpers ──────────────────── #


def _topology_vnf_names(state: OrchestratorState) -> list[str]:
    out: list[str] = []
    for source in (state.get("topology"), state.get("resource_allocation")):
        if not source:
            continue
        for vnf in source.get("vnfs", []) or []:
            name = vnf.get("name")
            if name and name not in out:
                out.append(name)
    return out


# ──────────────────── Agent ──────────────────── #


def planner_reasoning_agent(state: OrchestratorState) -> dict[str, Any]:
    """
    LangGraph node: Planner / Reasoning.

    Reads:  current_metrics, anomaly_alerts, sla_status, topology,
            resource_allocation, execution_results
    Writes: remediation_plan, messages, current_agent, phase
    """
    logger.info("Planner / Reasoning Agent: starting")

    metrics: list[MetricEvent] = list(state.get("current_metrics") or [])
    alerts: list[AnomalyAlert] = list(state.get("anomaly_alerts") or [])
    sla_statuses: list[SLAStatus] = list(state.get("sla_status") or [])
    _session_actions = list(state.get("execution_results") or [])
    recent_actions = _session_actions[-5:]
    past_incidents: list[dict[str, Any]] = []

    # ── Enrich context from Redis (best-effort) ──
    if _REDIS_AVAILABLE:
        try:
            _redis = _RedisClient()
            if _redis.ping():
                # Supplement recent_actions with cross-session history
                if len(_session_actions) < 5:
                    cross = _redis.get_actions(limit=5 - len(_session_actions))
                    recent_actions = _session_actions + cross
                past_incidents = _redis.get_incidents(limit=10)
                logger.debug(
                    "Planner: loaded %d past incidents + %d cross-session actions from Redis",
                    len(past_incidents),
                    len(recent_actions) - len(_session_actions),
                )
        except Exception as exc:
            logger.warning("Planner: Redis read failed (non-critical): %s", exc)

    topology_vnfs = _topology_vnf_names(state)

    rank, trigger_payload, trigger_kind = _select_trigger(alerts, sla_statuses)
    if rank == _TRIGGER_NONE or trigger_payload is None:
        return {
            "remediation_plan": None,
            "current_agent": "planner_reasoning",
            "phase": "post_deployment",
            "messages": [{"role": "agent", "content": _build_no_plan_message()}],
        }

    timestamp = datetime.utcnow().isoformat()
    if trigger_kind == "anomaly":
        triggered_by = trigger_payload.get("alert_id", "unknown_alert")
    else:
        triggered_by = f"sla:{trigger_payload.get('rule', 'unknown_rule')}"

    raw = _ask_llm(trigger_payload, metrics, alerts, sla_statuses, recent_actions, past_incidents)

    plan: RemediationPlan | None = None
    source = "llm"
    if raw is not None:
        cleaned, issues = _validate_plan(raw, topology_vnfs)
        if issues:
            logger.warning("Planner: validation issues — %s", issues)
        plan = cleaned

    if plan is None:
        logger.info("Planner: falling back to heuristic plan")
        action = _heuristic_action(trigger_kind, trigger_payload, topology_vnfs)
        plan = {
            "plan_id": "",
            "triggered_by": triggered_by,
            "diagnosis": (
                f"Auto-generated plan for {trigger_kind} trigger "
                f"({trigger_payload.get('type') or trigger_payload.get('rule', 'n/a')})"
            ),
            "confidence": 0.55,
            "recommended_actions": [action],
            "expected_outcome": (
                "Trigger condition resolves; KPI Monitor returns the affected "
                "metric to the normal band within 2 cycles."
            ),
            "rollback_plan": (
                "Revert the action (scale back / roll-forward) if no improvement is observed."
            ),
            "validation_metric": (
                (trigger_payload.get("affected_metrics") or [{}])[0].get("name")
                or trigger_payload.get("metric")
                or trigger_payload.get("rule")
                or "cpu_utilization"
            ),
        }
        source = "heuristic"

    if not plan.get("plan_id"):
        plan["plan_id"] = _make_plan_id(triggered_by, timestamp)
    if not plan.get("triggered_by"):
        plan["triggered_by"] = triggered_by

    summary = _build_summary(plan, source)

    return {
        "remediation_plan": plan,
        "current_agent": "planner_reasoning",
        "phase": "post_deployment",
        "messages": [{"role": "agent", "content": summary}],
    }
