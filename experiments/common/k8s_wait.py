"""
Pod readiness polling — drives the "Deployment time" metric.

Polls the cluster until every pod in the target namespace whose name matches
one of the provided release names reports Ready, or until the timeout
elapses. Returns the wall-clock seconds to "all ready" (or the timeout if
some pods never became Ready).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from infra.k8s_client import K8sClient

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 2.0
DEFAULT_TIMEOUT_S = 600.0


def _matches_any_release(pod_name: str, release_names: list[str]) -> bool:
    """Helm-installed pods are typically named `<release>-<suffix>`."""
    pod_lc = pod_name.lower()
    return any(pod_lc.startswith(rn.lower()) for rn in release_names)


def _is_pod_ready(pod: dict[str, Any]) -> bool:
    """A pod is Ready when every container reports ready=True."""
    if pod.get("phase") != "Running":
        return False
    containers = pod.get("containers") or []
    if not containers:
        return False
    return all(c.get("ready") for c in containers)


def wait_for_pods_ready(
    release_names: list[str],
    namespace: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    *,
    expected_pod_count: int | None = None,
    k8s: K8sClient | None = None,
) -> dict[str, Any]:
    """
    Block until every pod whose name starts with one of `release_names` reports
    Ready, or until `timeout_s` elapses.

    Returns:
        {
          "elapsed_s": float,        # wall-clock waited
          "all_ready": bool,         # True iff every matching pod is Ready
          "matched_pods": int,       # pods matching release_names at end
          "ready_pods": int,         # of those, how many are Ready
          "expected_pod_count": int | None,  # echoed input
          "timed_out": bool,
          "details": list[dict],     # final per-pod snapshot (name/phase/ready)
        }

    If `expected_pod_count` is given, also waits until that many pods exist
    *before* declaring success — guards against the case where helm install
    has returned but the controller has not yet created all replicas.
    """
    k8s = k8s or K8sClient()
    start = time.time()
    last_snapshot: list[dict[str, Any]] = []

    while True:
        pods = k8s.get_pods(namespace=namespace)
        matched = [p for p in pods if _matches_any_release(p["name"], release_names)]
        ready = [p for p in matched if _is_pod_ready(p)]
        last_snapshot = matched

        count_ok = (
            expected_pod_count is None or len(matched) >= expected_pod_count
        )
        if count_ok and matched and len(ready) == len(matched):
            elapsed = time.time() - start
            return {
                "elapsed_s": round(elapsed, 2),
                "all_ready": True,
                "matched_pods": len(matched),
                "ready_pods": len(ready),
                "expected_pod_count": expected_pod_count,
                "timed_out": False,
                "details": [
                    {"name": p["name"], "phase": p["phase"],
                     "ready": _is_pod_ready(p)}
                    for p in matched
                ],
            }

        if (time.time() - start) >= timeout_s:
            elapsed = time.time() - start
            return {
                "elapsed_s": round(elapsed, 2),
                "all_ready": False,
                "matched_pods": len(matched),
                "ready_pods": len(ready),
                "expected_pod_count": expected_pod_count,
                "timed_out": True,
                "details": [
                    {"name": p["name"], "phase": p["phase"],
                     "ready": _is_pod_ready(p)}
                    for p in last_snapshot
                ],
            }

        time.sleep(POLL_INTERVAL_S)
