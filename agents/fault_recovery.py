"""
Fault Recovery Agent — The Executor (Stability Actions).

Reads the ``RemediationPlan`` produced by the Planner / Reasoning agent and
executes **restart**, **rollback**, and **config_change** actions on the
remote Kubernetes cluster.  All other action types (``horizontal_scale``,
``vertical_scale``) are left for the Auto-Scaler agent and are silently
skipped here.

Pipeline position: 6th (final) in post-deployment loop
  KPI Monitor → Anomaly Detector → SLA Compliance → Planner → Auto-Scaler → [Fault Recovery] → END

Design — deterministic executor with safety rails:
  1. Read the ``remediation_plan`` from state; if absent or empty, no-op.
  2. Filter actions to those this agent owns (restart / rollback / config_change).
  3. For **restart**: issue a rollout restart via K8sClient
     (annotate pod template to trigger a rolling update).
  4. For **rollback**: map the VNF target to a Helm release name and
     invoke ``HelmManager.rollback_release()``.
  5. For **config_change**: patch the named ConfigMap with the supplied
     key/value updates, then rollout-restart the owning Deployment so
     pods pick up the new config.
  6. Wait briefly and verify the new state.
  7. Emit one ``ExecutionResult`` per action attempted.

Input:  OrchestratorState with remediation_plan, topology, resource_allocation
Output: OrchestratorState with execution_results (append-only), messages,
        current_agent, phase
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from config.settings import K8S_NAMESPACE
from core.state import ExecutionResult, OrchestratorState, RemediationPlan
from infra.k8s_client import K8sClient
from infra.helm_manager import HelmManager

try:
    from infra.redis_client import RedisClient as _RedisClient
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)


# ──────────────────── Constants ──────────────────── #

# Action types this agent handles; everything else is skipped.
_OWNED_ACTIONS = {"restart", "rollback", "config_change"}

# Seconds to wait after a recovery action before verifying.
_VERIFY_WAIT_SECS = 15


# ──────────────────── Restart ──────────────────── #


def _execute_restart(
    k8s: K8sClient,
    action: dict[str, Any],
    namespace: str,
) -> ExecutionResult:
    """Trigger a rollout restart of a Deployment."""
    target = action["target"]

    # Verify deployment exists before restarting.
    try:
        deployments = k8s.get_deployments(namespace=namespace)
    except Exception as exc:
        return _fail(action, f"Failed to list deployments: {exc}")

    found = any(d["name"] == target for d in deployments)
    if not found:
        return _fail(action, f"Deployment '{target}' not found in namespace '{namespace}'")

    try:
        k8s.rollout_restart_deployment(target, namespace=namespace)
    except Exception as exc:
        return _fail(action, f"rollout_restart_deployment failed: {exc}")

    return _result(
        action,
        "success",
        f"Rollout restart triggered for {target}",
    )


# ──────────────────── Rollback ──────────────────── #


def _execute_rollback(
    helm: HelmManager,
    action: dict[str, Any],
    namespace: str,
) -> ExecutionResult:
    """Roll back a Helm release to the previous revision."""
    target = action["target"]
    params = action.get("params") or {}
    revision = params.get("revision")  # None → Helm rolls back by 1

    # The release name typically matches the VNF deployment name.
    release_name = target

    try:
        helm.rollback_release(
            release_name,
            revision=int(revision) if revision is not None else None,
            namespace=namespace,
        )
    except Exception as exc:
        return _fail(action, f"helm rollback failed for '{release_name}': {exc}")

    rev_msg = f" to revision {revision}" if revision else " to previous revision"
    return _result(
        action,
        "success",
        f"Rolled back Helm release '{release_name}'{rev_msg}",
    )


# ──────────────────── Config Change ──────────────────── #


def _execute_config_change(
    k8s: K8sClient,
    action: dict[str, Any],
    namespace: str,
) -> ExecutionResult:
    """
    Patch a ConfigMap and rollout-restart the owning Deployment.

    Expected params:
      - config_map: str — name of the ConfigMap to patch.
      - updates: dict[str, str] — key/value pairs to merge into the ConfigMap data.
    """
    target = action["target"]
    params = action.get("params") or {}
    config_map_name = params.get("config_map")
    updates = params.get("updates")

    if not config_map_name:
        return _fail(action, "Missing 'config_map' in action params")
    if not updates or not isinstance(updates, dict):
        return _fail(action, "Missing or invalid 'updates' dict in action params")

    # Step 1: Patch the ConfigMap.
    try:
        k8s.patch_configmap(config_map_name, updates, namespace=namespace)
    except Exception as exc:
        return _fail(
            action,
            f"patch_configmap failed for '{config_map_name}': {exc}",
        )

    # Step 2: Rollout restart the owning Deployment so pods pick up changes.
    try:
        k8s.rollout_restart_deployment(target, namespace=namespace)
    except Exception as exc:
        # ConfigMap was patched but the restart failed — partial success.
        return _result(
            action,
            "partial",
            f"ConfigMap '{config_map_name}' patched, but rollout restart "
            f"of '{target}' failed: {exc}",
        )

    return _result(
        action,
        "success",
        f"ConfigMap '{config_map_name}' patched and '{target}' restarted "
        f"(keys updated: {list(updates.keys())})",
    )


# ──────────────────── Post-action verification ──────────────────── #


def _verify_deployment(
    k8s: K8sClient,
    target: str,
    namespace: str,
) -> dict[str, Any]:
    """Quick health snapshot of a deployment after recovery."""
    try:
        for dep in k8s.get_deployments(namespace=namespace):
            if dep["name"] == target:
                return {
                    "replicas": dep.get("replicas"),
                    "ready_replicas": dep.get("ready_replicas"),
                    "available_replicas": dep.get("available_replicas"),
                }
    except Exception as exc:
        logger.warning("Post-recovery verification failed for %s: %s", target, exc)
    return {}


# ──────────────────── Result helpers ──────────────────── #


def _result(
    action: dict[str, Any],
    status: str,
    details: str,
) -> ExecutionResult:
    return {
        "action_type": action.get("type", "unknown"),
        "target": action.get("target", "unknown"),
        "status": status,
        "details": details,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _fail(action: dict[str, Any], details: str) -> ExecutionResult:
    logger.error("Fault Recovery action failed: %s — %s", action.get("target"), details)
    return _result(action, "failed", details)


# ──────────────────── Summary builder ──────────────────── #


def _build_summary(
    results: list[ExecutionResult],
    verifications: dict[str, dict[str, Any]],
) -> str:
    if not results:
        return (
            "**Fault Recovery** — no recovery actions to execute.\n"
            "The remediation plan contained no restart, rollback, or config_change actions."
        )

    lines = [
        "**Fault Recovery** — execution report",
        "",
    ]
    succeeded = sum(1 for r in results if r["status"] == "success")
    partial = sum(1 for r in results if r["status"] == "partial")
    failed = sum(1 for r in results if r["status"] == "failed")
    lines.append(
        f"- Actions executed: {len(results)}  "
        f"(✓ {succeeded}  ◑ {partial}  ✗ {failed})"
    )
    lines.append("")

    for r in results:
        if r["status"] == "success":
            icon = "✓"
        elif r["status"] == "partial":
            icon = "◑"
        else:
            icon = "✗"
        lines.append(f"  {icon} **{r['action_type']}** → `{r['target']}`: {r['details']}")
        v = verifications.get(r["target"])
        if v:
            lines.append(
                f"    Post-check: replicas={v.get('replicas')}, "
                f"ready={v.get('ready_replicas')}, "
                f"available={v.get('available_replicas')}"
            )

    return "\n".join(lines)


def _build_no_plan_message() -> str:
    return (
        "**Fault Recovery** — skipped.\n"
        "No remediation plan present on this cycle."
    )


# ──────────────────── Agent ──────────────────── #


def fault_recovery_agent(state: OrchestratorState) -> dict[str, Any]:
    """
    LangGraph node: Fault Recovery.

    Reads:  remediation_plan, topology, resource_allocation
    Writes: execution_results (append), messages, current_agent, phase
    """
    logger.info("Fault Recovery Agent: starting")

    plan: RemediationPlan | None = state.get("remediation_plan")
    namespace = K8S_NAMESPACE

    # ── No plan → no-op ──
    if not plan or not plan.get("recommended_actions"):
        return {
            "execution_results": [],
            "current_agent": "fault_recovery",
            "phase": "post_deployment",
            "messages": [{"role": "agent", "content": _build_no_plan_message()}],
        }

    # ── Filter to owned action types ──
    owned_actions = [
        a for a in plan["recommended_actions"]
        if a.get("type") in _OWNED_ACTIONS
    ]

    if not owned_actions:
        return {
            "execution_results": [],
            "current_agent": "fault_recovery",
            "phase": "post_deployment",
            "messages": [{"role": "agent", "content": _build_summary([], {})}],
        }

    # ── Initialise infrastructure clients ──
    k8s: K8sClient | None = None
    helm: HelmManager | None = None

    # Only create the clients we actually need.
    needs_k8s = any(a["type"] in {"restart", "config_change"} for a in owned_actions)
    needs_helm = any(a["type"] == "rollback" for a in owned_actions)

    try:
        if needs_k8s:
            k8s = K8sClient()
        if needs_helm:
            helm = HelmManager()
    except Exception as exc:
        logger.error("Fault Recovery: client init failed: %s", exc)
        error_results: list[ExecutionResult] = [
            _fail(a, f"Client init failed: {exc}") for a in owned_actions
        ]
        return {
            "execution_results": error_results,
            "current_agent": "fault_recovery",
            "phase": "post_deployment",
            "error": f"Client init failed: {exc}",
            "messages": [{"role": "agent", "content": _build_summary(error_results, {})}],
        }

    # ── Execute actions in order ──
    results: list[ExecutionResult] = []
    targets_touched: set[str] = set()

    for action in sorted(owned_actions, key=lambda a: int(a.get("order", 99))):
        atype = action["type"]
        if atype == "restart":
            result = _execute_restart(k8s, action, namespace)
        elif atype == "rollback":
            result = _execute_rollback(helm, action, namespace)
        elif atype == "config_change":
            result = _execute_config_change(k8s, action, namespace)
        else:
            continue  # should not happen after filtering

        results.append(result)
        targets_touched.add(action["target"])

    # ── Wait and verify ──
    if targets_touched and k8s is not None:
        logger.info(
            "Fault Recovery: waiting %ds for actions to stabilise...", _VERIFY_WAIT_SECS
        )
        time.sleep(_VERIFY_WAIT_SECS)

    verifications: dict[str, dict[str, Any]] = {}
    if k8s is not None:
        for tgt in targets_touched:
            verifications[tgt] = _verify_deployment(k8s, tgt, namespace)

    summary = _build_summary(results, verifications)

    # ── Persist to Redis (best-effort) ──
    if _REDIS_AVAILABLE and results:
        try:
            _redis = _RedisClient()
            if _redis.ping():
                for r in results:
                    _redis.push_action(dict(r))
                plan = state.get("remediation_plan") or {}
                n_ok = sum(1 for r in results if r["status"] == "success")
                n_fail = len(results) - n_ok
                outcome = f"{n_ok} success, {n_fail} failed"
                first = results[0]
                _redis.push_incident(
                    alert={
                        "triggered_by": plan.get("triggered_by", ""),
                        "diagnosis": plan.get("diagnosis", ""),
                    },
                    triggered_action=f"{first['action_type']}:{first['target']}",
                    outcome=outcome,
                )
                logger.info(
                    "Fault Recovery: persisted %d actions + 1 incident to Redis", len(results)
                )
        except Exception as exc:
            logger.warning("Fault Recovery: Redis persistence failed (non-critical): %s", exc)

    return {
        "execution_results": results,
        "current_agent": "fault_recovery",
        "phase": "post_deployment",
        "messages": [{"role": "agent", "content": summary}],
    }
