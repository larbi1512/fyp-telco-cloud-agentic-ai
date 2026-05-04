"""
KPI Monitor Agent — The Sensor.

Queries remote Prometheus for live infrastructure metrics from OAI 5G Core VNFs,
attaches a threshold verdict to each sample, and emits a structured metric list
for the downstream anomaly detector and SLA compliance agents.

Pipeline position: 1st in post-deployment loop
  Deployer → [KPI Monitor] → Anomaly Detector / SLA Compliance → ...

Input:  OrchestratorState with optional topology + resource_allocation
Output: OrchestratorState with current_metrics (list[MetricEvent])
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Literal

import yaml

from config.settings import (
    K8S_NAMESPACE,
    SLA_THRESHOLDS_PATH,
    VNF_PROFILES_PATH,
)
from core.llm_core import LLMCore
from core.state import MetricEvent, OrchestratorState
from infra.prometheus_client import PrometheusClient

logger = logging.getLogger(__name__)


# ───────────────────────── Helpers ───────────────────────── #

_MEM_BINARY = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}
_MEM_DECIMAL = {"K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12}


def _parse_k8s_resource(value: str | int | float) -> float:
    """
    Parse a Kubernetes resource string into a float.

    CPU:    "100m" -> 0.1 cores, "1.5" -> 1.5 cores.
    Memory: "256Mi" -> 256*1024**2 bytes, "1G" -> 10**9 bytes.
    """
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0

    if s.endswith("m") and s[:-1].replace(".", "", 1).isdigit():
        return float(s[:-1]) / 1000.0

    for suffix, mult in _MEM_BINARY.items():
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * mult

    for suffix, mult in _MEM_DECIMAL.items():
        if s.endswith(suffix):
            return float(s[:-1]) * mult

    try:
        return float(s)
    except ValueError:
        logger.warning("Could not parse k8s resource value: %r", value)
        return 0.0


def _pod_to_vnf(pod_name: str, known_vnfs: list[str]) -> str | None:
    """Longest-prefix match from pod name to a known VNF name."""
    matches = [v for v in known_vnfs if pod_name.startswith(v)]
    if not matches:
        return None
    return max(matches, key=len)


def _fallback_vnf_from_pod(pod_name: str) -> str:
    """
    Derive a best-guess VNF label from a pod name when topology is absent.
    Strips ReplicaSet/pod hash suffixes (e.g. "oai-amf-55b9d4d44f-qxxs8" -> "oai-amf").
    """
    parts = pod_name.split("-")
    while parts and re.fullmatch(r"[a-z0-9]{5,10}", parts[-1]):
        parts.pop()
    return "-".join(parts) if parts else pod_name


def _threshold_status(
    value: float, warn: float, crit: float
) -> Literal["normal", "warning", "critical"]:
    if value >= crit:
        return "critical"
    if value >= warn:
        return "warning"
    return "normal"


def _load_vnf_limits_from_state(
    resource_allocation: dict[str, Any] | None,
) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    if not resource_allocation:
        return out
    for vnf in resource_allocation.get("vnfs", []) or []:
        name = vnf.get("name")
        limits = (vnf.get("resources") or {}).get("limits") or {}
        if not name or not limits:
            continue
        out[name] = {
            "cpu_cores": _parse_k8s_resource(limits.get("cpu", "1")),
            "memory_bytes": _parse_k8s_resource(limits.get("memory", "1Gi")),
        }
    return out


def _load_vnf_limits_fallback() -> dict[str, dict[str, float]]:
    """Load fallback VNF limits from vnf_resource_profiles.yaml 'max' values."""
    try:
        with open(VNF_PROFILES_PATH) as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        logger.warning("Could not load VNF profiles: %s", exc)
        return {}

    out: dict[str, dict[str, float]] = {}
    for name, profile in (data.get("profiles") or {}).items():
        m = profile.get("max", {})
        out[name] = {
            "cpu_cores": _parse_k8s_resource(m.get("cpu", "1000m")),
            "memory_bytes": _parse_k8s_resource(m.get("memory", "1Gi")),
        }
    return out


def _load_infra_thresholds() -> dict[str, dict[str, float]]:
    try:
        with open(SLA_THRESHOLDS_PATH) as f:
            data = yaml.safe_load(f)
        return data.get("infra_thresholds") or {}
    except Exception as exc:
        logger.warning("Could not load SLA thresholds: %s", exc)
        return {
            "cpu_utilization": {"warning": 70, "critical": 85},
            "memory_utilization": {"warning": 75, "critical": 90},
            "pod_restart_count": {"warning": 3, "critical": 5},
        }


# ──────────────────── Metric-event construction ──────────────────── #


def _build_events(
    cpu_samples: list[dict[str, Any]],
    mem_samples: list[dict[str, Any]],
    net_samples: list[dict[str, Any]],
    restart_samples: list[dict[str, Any]],
    vnf_limits: dict[str, dict[str, float]],
    thresholds: dict[str, dict[str, float]],
) -> list[MetricEvent]:
    now = datetime.utcnow().isoformat()
    known_vnfs = list(vnf_limits.keys())
    cpu_th = thresholds.get("cpu_utilization", {"warning": 70, "critical": 85})
    mem_th = thresholds.get("memory_utilization", {"warning": 75, "critical": 90})
    restart_th = thresholds.get("pod_restart_count", {"warning": 3, "critical": 5})

    def _resolve_vnf(pod: str) -> str:
        return _pod_to_vnf(pod, known_vnfs) or _fallback_vnf_from_pod(pod)

    events: list[MetricEvent] = []

    for s in cpu_samples:
        pod = s.get("pod", "")
        if not pod:
            continue
        vnf = _resolve_vnf(pod)
        cpu_cores = float(s.get("cpu_cores", 0.0))
        limit = (vnf_limits.get(vnf) or {}).get("cpu_cores") or 0.0
        pct = (cpu_cores / limit * 100.0) if limit > 0 else 0.0
        events.append(
            {
                "timestamp": now,
                "vnf_name": vnf,
                "metric_name": "cpu_utilization",
                "value": round(pct, 2),
                "unit": "%",
                "threshold_status": _threshold_status(
                    pct, cpu_th["warning"], cpu_th["critical"]
                ),
            }
        )

    for s in mem_samples:
        pod = s.get("pod", "")
        if not pod:
            continue
        vnf = _resolve_vnf(pod)
        mem_bytes = float(s.get("memory_bytes", 0.0))
        limit = (vnf_limits.get(vnf) or {}).get("memory_bytes") or 0.0
        pct = (mem_bytes / limit * 100.0) if limit > 0 else 0.0
        events.append(
            {
                "timestamp": now,
                "vnf_name": vnf,
                "metric_name": "memory_utilization",
                "value": round(pct, 2),
                "unit": "%",
                "threshold_status": _threshold_status(
                    pct, mem_th["warning"], mem_th["critical"]
                ),
            }
        )

    for s in net_samples:
        pod = s.get("pod", "")
        if not pod:
            continue
        vnf = _resolve_vnf(pod)
        mbps = float(s.get("rx_bytes_per_sec", 0.0)) * 8.0 / 1_000_000.0
        events.append(
            {
                "timestamp": now,
                "vnf_name": vnf,
                "metric_name": "network_rx_mbps",
                "value": round(mbps, 3),
                "unit": "Mbps",
                "threshold_status": "normal",
            }
        )

    for s in restart_samples:
        pod = s.get("pod", "")
        if not pod:
            continue
        vnf = _resolve_vnf(pod)
        restarts = int(s.get("restarts", 0))
        events.append(
            {
                "timestamp": now,
                "vnf_name": vnf,
                "metric_name": "pod_restarts",
                "value": float(restarts),
                "unit": "count",
                "threshold_status": _threshold_status(
                    restarts, restart_th["warning"], restart_th["critical"]
                ),
            }
        )

    return events


# ───────────────────────── Summary ───────────────────────── #


def _build_summary(
    events: list[MetricEvent], llm_result: dict[str, Any] | None
) -> str:
    counts = {"normal": 0, "warning": 0, "critical": 0}
    for e in events:
        counts[e.get("threshold_status", "normal")] += 1

    lines = [
        "**KPI Monitor** — metric snapshot",
        "",
        f"- Total samples: {len(events)}",
        f"- Normal: {counts['normal']}  |  Warning: {counts['warning']}  |  Critical: {counts['critical']}",
    ]

    critical = [e for e in events if e.get("threshold_status") == "critical"]
    if critical:
        lines.append("")
        lines.append("**Critical samples:**")
        for e in critical[:10]:
            lines.append(
                f"  - {e['vnf_name']} / {e['metric_name']} = {e['value']}{e['unit']}"
            )

    if llm_result:
        status = llm_result.get("health_status", "unknown")
        summary_text = (llm_result.get("summary") or "").strip()
        concerns = llm_result.get("concerns") or []
        lines.append("")
        lines.append(f"**Health status:** {status}")
        if summary_text:
            lines.append(summary_text)
        if concerns:
            lines.append("**Concerns:**")
            for c in concerns[:5]:
                lines.append(f"  - {c}")

    return "\n".join(lines)


# ───────────────────────── Agent ───────────────────────── #


def kpi_monitor_agent(state: OrchestratorState) -> dict[str, Any]:
    """
    LangGraph node: KPI Monitor.

    Reads:  state["topology"], state["resource_allocation"]
    Writes: state["current_metrics"], state["messages"], state["current_agent"], state["phase"]
    """
    logger.info("KPI Monitor Agent: starting")

    try:
        prom = PrometheusClient()
    except Exception as exc:
        logger.error("Could not instantiate PrometheusClient: %s", exc)
        return {
            "error": f"Prometheus client init failed: {exc}",
            "current_metrics": [],
            "current_agent": "kpi_monitor",
            "messages": [
                {"role": "agent", "content": f"KPI Monitor: Prometheus client init failed: {exc}"}
            ],
        }

    if not prom.ping():
        logger.error("Prometheus unreachable at %s", prom.url)
        return {
            "error": f"Prometheus unreachable at {prom.url}",
            "current_metrics": [],
            "current_agent": "kpi_monitor",
            "messages": [
                {"role": "agent", "content": f"KPI Monitor: Prometheus unreachable at {prom.url}"}
            ],
        }

    vnf_limits = _load_vnf_limits_from_state(state.get("resource_allocation"))
    if not vnf_limits:
        vnf_limits = _load_vnf_limits_fallback()
        logger.info("KPI Monitor: using fallback VNF limits from profiles YAML")

    thresholds = _load_infra_thresholds()

    ns = K8S_NAMESPACE
    try:
        cpu_samples = prom.get_cpu_usage(namespace=ns)
        mem_samples = prom.get_memory_usage(namespace=ns)
        net_samples = prom.get_network_receive_bytes(namespace=ns)
        restart_samples = prom.get_pod_restart_count(namespace=ns)
    except Exception as exc:
        logger.error("Prometheus query failed: %s", exc)
        return {
            "error": f"Prometheus query failed: {exc}",
            "current_metrics": [],
            "current_agent": "kpi_monitor",
            "messages": [
                {"role": "agent", "content": f"KPI Monitor: Prometheus query failed: {exc}"}
            ],
        }

    events = _build_events(
        cpu_samples, mem_samples, net_samples, restart_samples,
        vnf_limits, thresholds,
    )
    logger.info("KPI Monitor: collected %d metric events", len(events))

    llm_result: dict[str, Any] | None = None
    if events:
        try:
            llm = LLMCore()
            compact = [
                {
                    "vnf": e["vnf_name"],
                    "metric": e["metric_name"],
                    "value": e["value"],
                    "unit": e["unit"],
                    "status": e["threshold_status"],
                }
                for e in events
            ]
            result = llm.invoke(
                "kpi_monitor",
                {
                    "metrics": compact,
                    "baselines": {},
                    "sla_thresholds": thresholds,
                },
                expect_json=True,
            )
            if isinstance(result, dict):
                llm_result = result
            else:
                logger.warning("KPI Monitor LLM returned non-JSON text")
        except Exception as exc:
            logger.warning("KPI Monitor LLM enhancement failed: %s", exc)

    summary = _build_summary(events, llm_result)

    return {
        "current_metrics": events,
        "current_agent": "kpi_monitor",
        "phase": "post_deployment",
        "messages": [{"role": "agent", "content": summary}],
    }
