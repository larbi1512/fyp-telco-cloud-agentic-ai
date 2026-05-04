"""
Auto-Scaler Agent — The Executor (Scaling Actions).

Reads the ``RemediationPlan`` produced by the Planner / Reasoning agent and
executes **horizontal_scale** and **vertical_scale** actions on the remote
Kubernetes cluster.  All other action types (``restart``, ``rollback``,
``config_change``) are left for the Fault Recovery agent and are silently
skipped here.

Pipeline position: 5th in post-deployment loop
  KPI Monitor → Anomaly Detector → SLA Compliance → Planner → [Auto-Scaler] → Fault Recovery

Design — deterministic executor with safety rails:
  1. Read the ``remediation_plan`` from state; if absent or empty, no-op.
  2. Filter actions to those this agent owns (horizontal / vertical scale).
  3. For **horizontal_scale**: query current replica count via K8sClient,
     apply the ``delta`` parameter (clamped to a safe range), and patch the
     Deployment scale.
  4. For **vertical_scale**: query the target Deployment's first matching
     container, compute new resource limits using the ``factor`` parameter,
     and patch the container spec.
  5. Wait briefly and verify the new state (replicas ready / rollout status).
  6. Emit one ``ExecutionResult`` per action attempted.

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

try:
    from infra.redis_client import RedisClient as _RedisClient
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)


# ──────────────────── Constants ──────────────────── #

# Action types this agent handles; everything else is skipped.
_OWNED_ACTIONS = {"horizontal_scale", "vertical_scale"}

# Safety guardrails for horizontal scaling.
_MIN_REPLICAS = 1
_MAX_REPLICAS = 10
_MAX_DELTA = 5

# Default multiplier for vertical scaling when params are missing.
_DEFAULT_VERTICAL_FACTOR = 1.5

# Seconds to wait after a scaling action before verifying.
_VERIFY_WAIT_SECS = 15


# Container resolution


def _infer_container_name(
    k8s: K8sClient,
    deployment_name: str,
    namespace: str,
) -> str:
    """
    Infer the primary application container name within a Deployment.

    Strategy:
      1. List pods whose name starts with the deployment name.
      2. Pick the first container whose name is NOT a known sidecar.
      3. Fall back to the deployment name itself.
    """
    _SIDECAR_NAMES = {"istio-proxy", "tcpdump", "envoy", "fluentd", "linkerd-proxy"}
    try:
        pods = k8s.get_pods(namespace=namespace)
        for pod in pods:
            if not pod["name"].startswith(deployment_name):
                continue
            containers = pod.get("containers") or []
            for c in containers:
                if c["name"] not in _SIDECAR_NAMES:
                    return c["name"]
    except Exception as exc:
        logger.warning("Container inference failed for %s: %s", deployment_name, exc)

    # Sensible default: OAI charts typically name the container after the VNF.
    return deployment_name


# Horizontal scaling 


def _execute_horizontal_scale(
    k8s: K8sClient,
    action: dict[str, Any],
    namespace: str,
) -> ExecutionResult:
    """Scale a Deployment's replica count by a delta."""
    target = action["target"]
    params = action.get("params") or {}
    delta = int(params.get("delta", 1))
    delta = max(-_MAX_DELTA, min(_MAX_DELTA, delta))  # clamp

    # Discover current replica count.
    try:
        deployments = k8s.get_deployments(namespace=namespace)
    except Exception as exc:
        return _fail(action, f"Failed to list deployments: {exc}")

    current_replicas: int | None = None
    for dep in deployments:
        if dep["name"] == target:
            current_replicas = dep.get("replicas", 1)
            break

    if current_replicas is None:
        return _fail(action, f"Deployment '{target}' not found in namespace '{namespace}'")

    desired = max(_MIN_REPLICAS, min(_MAX_REPLICAS, current_replicas + delta))

    if desired == current_replicas:
        return _result(
            action,
            "success",
            f"No change needed — already at {current_replicas} replicas "
            f"(requested delta={delta}, clamped to bounds [{_MIN_REPLICAS}, {_MAX_REPLICAS}])",
        )

    try:
        k8s.scale_deployment(target, desired, namespace=namespace)
    except Exception as exc:
        return _fail(action, f"scale_deployment failed: {exc}")

    return _result(
        action,
        "success",
        f"Scaled {target} from {current_replicas} → {desired} replicas (delta={delta})",
    )


# Vertical scaling 


def _execute_vertical_scale(
    k8s: K8sClient,
    action: dict[str, Any],
    namespace: str,
) -> ExecutionResult:
    """Adjust CPU / memory limits of the primary container in a Deployment."""
    target = action["target"]
    params = action.get("params") or {}
    factor = float(params.get("factor", _DEFAULT_VERTICAL_FACTOR))
    factor = max(1.0, min(4.0, factor))  # clamp to a sane range

    container_name = _infer_container_name(k8s, target, namespace)

    try:
        k8s.patch_deployment_resources(
            name=target,
            container_name=container_name,
            factor=factor,
            namespace=namespace,
        )
    except Exception as exc:
        return _fail(action, f"patch_deployment_resources failed: {exc}")

    return _result(
        action,
        "success",
        f"Vertically scaled {target}/{container_name} by factor {factor:.2f}",
    )


# Post-action verification 


def _verify_deployment(
    k8s: K8sClient,
    target: str,
    namespace: str,
) -> dict[str, Any]:
    """Quick health snapshot of a deployment after scaling."""
    try:
        for dep in k8s.get_deployments(namespace=namespace):
            if dep["name"] == target:
                return {
                    "replicas": dep.get("replicas"),
                    "ready_replicas": dep.get("ready_replicas"),
                    "available_replicas": dep.get("available_replicas"),
                }
    except Exception as exc:
        logger.warning("Post-scale verification failed for %s: %s", target, exc)
    return {}


# Result helpers


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
    logger.error("Auto-Scaler action failed: %s — %s", action.get("target"), details)
    return _result(action, "failed", details)


# Summary builder 


def _build_summary(
    results: list[ExecutionResult],
    verifications: dict[str, dict[str, Any]],
) -> str:
    if not results:
        return (
            "**Auto-Scaler** — no scaling actions to execute.\n"
            "The remediation plan contained no horizontal_scale or vertical_scale actions."
        )

    lines = [
        "**Auto-Scaler** — execution report",
        "",
    ]
    succeeded = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "failed")
    lines.append(f"- Actions executed: {len(results)}  (✓ {succeeded}  ✗ {failed})")
    lines.append("")

    for r in results:
        icon = "✓" if r["status"] == "success" else "✗"
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
        "**Auto-Scaler** — skipped.\n"
        "No remediation plan present on this cycle."
    )


# Agent


def auto_scaler_agent(state: OrchestratorState) -> dict[str, Any]:
    """
    LangGraph node: Auto-Scaler.

    Reads:  remediation_plan, topology, resource_allocation
    Writes: execution_results (append), messages, current_agent, phase
    """
    logger.info("Auto-Scaler Agent: starting")

    plan: RemediationPlan | None = state.get("remediation_plan")
    namespace = K8S_NAMESPACE

    # ── No plan → no-op ──
    if not plan or not plan.get("recommended_actions"):
        return {
            "execution_results": [],
            "current_agent": "auto_scaler",
            "phase": "post_deployment",
            "messages": [{"role": "agent", "content": _build_no_plan_message()}],
        }

    # Filter to owned action types 
    owned_actions = [
        a for a in plan["recommended_actions"]
        if a.get("type") in _OWNED_ACTIONS
    ]

    if not owned_actions:
        return {
            "execution_results": [],
            "current_agent": "auto_scaler",
            "phase": "post_deployment",
            "messages": [{"role": "agent", "content": _build_summary([], {})}],
        }

    # Initialise K8s client
    try:
        k8s = K8sClient()
    except Exception as exc:
        logger.error("Auto-Scaler: K8s client init failed: %s", exc)
        error_results: list[ExecutionResult] = [
            _fail(a, f"K8s client init failed: {exc}") for a in owned_actions
        ]
        return {
            "execution_results": error_results,
            "current_agent": "auto_scaler",
            "phase": "post_deployment",
            "error": f"K8s client init failed: {exc}",
            "messages": [{"role": "agent", "content": _build_summary(error_results, {})}],
        }

    # Execute actions in order 
    results: list[ExecutionResult] = []
    targets_touched: set[str] = set()

    for action in sorted(owned_actions, key=lambda a: int(a.get("order", 99))):
        atype = action["type"]
        if atype == "horizontal_scale":
            result = _execute_horizontal_scale(k8s, action, namespace)
        elif atype == "vertical_scale":
            result = _execute_vertical_scale(k8s, action, namespace)
        else:
            continue  # should not happen after filtering

        results.append(result)
        targets_touched.add(action["target"])

    # ── Wait and verify ──
    if targets_touched:
        logger.info(
            "Auto-Scaler: waiting %ds for scaling to stabilise...", _VERIFY_WAIT_SECS
        )
        time.sleep(_VERIFY_WAIT_SECS)

    verifications: dict[str, dict[str, Any]] = {}
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
                logger.info(
                    "Auto-Scaler: persisted %d action results to Redis", len(results)
                )
        except Exception as exc:
            logger.warning("Auto-Scaler: Redis persistence failed (non-critical): %s", exc)

    return {
        "execution_results": results,
        "current_agent": "auto_scaler",
        "phase": "post_deployment",
        "messages": [{"role": "agent", "content": summary}],
    }
