"""
Anomaly Detector Agent — The Outlier Spotter.

Reads the labelled metric stream produced by the KPI Monitor (Phase 5.1)
and applies three layered statistical detectors: robust z-score (point
anomaly), sustained-deviation (collective anomaly), and pod-restart spike
(instability). Emits one or more ``AnomalyAlert`` objects per cycle for
the Planner / Reasoning agent to consume.

Pipeline position: 2nd in post-deployment loop
  KPI Monitor → [Anomaly Detector] → SLA Compliance → Planner → ...

Input:  OrchestratorState with current_metrics (from KPI Monitor)
Output: OrchestratorState with anomaly_alerts (append-only — return only
        newly detected alerts)
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import yaml

from config.settings import K8S_NAMESPACE, SLA_THRESHOLDS_PATH
from core.llm_core import LLMCore
from core.state import AnomalyAlert, MetricEvent, OrchestratorState
from infra.prometheus_client import PrometheusClient

logger = logging.getLogger(__name__)


_BASELINE_WINDOW = timedelta(minutes=15)
_SUSTAINED_WINDOW = timedelta(minutes=5)
_RESTART_WINDOW = timedelta(hours=1)
_MAX_LLM_CALLS_PER_CYCLE = 5

# Suppression cache: fingerprint -> last-fired UTC time. Same (type, vnf, metric)
# is not re-emitted within the window, preventing unbounded alert growth across
# cycles (Phase 5.2 documented limitation).
_DEDUP_WINDOW = timedelta(minutes=3)
_seen_alerts: dict[str, datetime] = {}


# ──────────────────── Statistical helpers ──────────────────── #


_MIN_BASELINE_SAMPLES = 30  # 15 min × 2 samples/min — enough for stable MAD
_Z_SPARSE_BASELINE_CAP = 50.0  # above this, downgrade severity (untrustworthy baseline)


def _robust_z(current: float, samples: list[float]) -> float | None:
    """
    Robust z-score using median + MAD.

    Returns ``None`` if fewer than ``_MIN_BASELINE_SAMPLES`` samples are
    available, or if the series is constant (MAD == 0). The 30-sample
    minimum follows the MAD-stability guidance in Iglewicz & Hoaglin and
    prevents extreme z-scores on sparse post-startup baselines.
    """
    if len(samples) < _MIN_BASELINE_SAMPLES:
        return None
    arr = np.asarray(samples, dtype=float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    if mad == 0:
        return None
    return abs(current - med) / (1.4826 * mad)


def _z_to_severity(z: float) -> tuple[str, float] | None:
    """
    Map a robust z-score to (severity, confidence). None below z=3.

    Above ``_Z_SPARSE_BASELINE_CAP`` the score is treated as evidence the
    baseline is untrustworthy (Prometheus history not yet stable) and the
    severity is downgraded one band — preventing astronomical z-scores
    from polluting the alert stream.
    """
    if z > _Z_SPARSE_BASELINE_CAP:
        return ("medium", 0.65)
    if z >= 6:
        return ("critical", 0.95)
    if z >= 4.5:
        return ("high", 0.85)
    if z >= 3:
        return ("medium", 0.70)
    return None


def _make_alert_id(vnf: str, metric: str, ts: str) -> str:
    digest = hashlib.sha1(f"{vnf}|{metric}|{ts}".encode()).hexdigest()[:8]
    return f"{ts}-{digest}"


def _alert_fingerprint(alert: AnomalyAlert) -> str:
    """Stable identity for dedup: alert type + first affected (vnf, metric)."""
    metrics = alert.get("affected_metrics") or []
    metric_name = metrics[0].get("name", "") if metrics else ""
    description = alert.get("description", "")
    vnf = description.split(" ", 1)[0] if description else ""
    return f"{alert.get('type', '')}|{vnf}|{metric_name}"


def _filter_suppressed(alerts: list[AnomalyAlert], now: datetime) -> tuple[list[AnomalyAlert], int]:
    """
    Drop alerts whose fingerprint fired within ``_DEDUP_WINDOW``. Update the
    cache with the surviving alerts. Returns (kept_alerts, suppressed_count).
    """
    kept: list[AnomalyAlert] = []
    suppressed = 0
    cutoff = now - _DEDUP_WINDOW
    for alert in alerts:
        fp = _alert_fingerprint(alert)
        last = _seen_alerts.get(fp)
        if last is not None and last >= cutoff:
            suppressed += 1
            continue
        _seen_alerts[fp] = now
        kept.append(alert)

    # Garbage-collect entries older than 2x the window so the cache cannot grow
    # without bound across long monitoring sessions.
    stale_cutoff = now - 2 * _DEDUP_WINDOW
    for fp in [k for k, v in _seen_alerts.items() if v < stale_cutoff]:
        del _seen_alerts[fp]
    return kept, suppressed


def _default_actions(metric_name: str, severity: str) -> list[str]:
    """Rule-based suggested actions; LLM may override."""
    table = {
        "cpu_utilization": ["scale_out_replicas", "increase_cpu_limit"],
        "memory_utilization": ["increase_memory_limit", "investigate_memory_leak"],
        "network_rx_mbps": ["check_traffic_source", "inspect_upf_throughput"],
        "pod_restarts": ["inspect_pod_events", "check_image_pull", "rollback_release"],
    }
    actions = list(table.get(metric_name, ["investigate", "check_logs"]))
    if severity == "critical":
        actions.append("page_oncall")
    return actions


def _default_cause(metric_name: str, alert_type: str) -> str:
    if alert_type == "instability":
        return "Pod restart spike — likely crash loop or recent bad release"
    if alert_type == "sustained_overload":
        return f"Sustained over-threshold values on {metric_name} suggest a real load increase or resource shortage"
    return f"Statistical outlier on {metric_name} relative to its 15-minute baseline"


# ──────────────────── Prometheus history fetch ──────────────────── #


def _history_query(metric_name: str, vnf_name: str, namespace: str, window: str) -> str | None:
    """
    Build a PromQL query that returns the same value the KPI Monitor would
    record, but as a range series. The vnf_name is used as a pod regex
    prefix (works because OAI pods are named ``<vnf>-<hash>-<hash>``).
    """
    pod_re = f"{vnf_name}.*"
    if metric_name == "cpu_utilization":
        return (
            f'sum(rate(container_cpu_usage_seconds_total'
            f'{{namespace="{namespace}", pod=~"{pod_re}", container!=""}}[{window}]))'
        )
    if metric_name == "memory_utilization":
        return (
            f'sum(container_memory_working_set_bytes'
            f'{{namespace="{namespace}", pod=~"{pod_re}", container!=""}})'
        )
    if metric_name == "network_rx_mbps":
        return (
            f'sum(rate(container_network_receive_bytes_total'
            f'{{namespace="{namespace}", pod=~"{pod_re}"}}[{window}])) '
            f'* 8 / 1000000'
        )
    if metric_name == "pod_restarts":
        return (
            f'sum(kube_pod_container_status_restarts_total'
            f'{{namespace="{namespace}", pod=~"{pod_re}"}})'
        )
    return None


def _fetch_history(
    prom: PrometheusClient,
    metric_name: str,
    vnf_name: str,
    namespace: str,
    window: timedelta,
) -> list[float]:
    """Run the matching range query and return a flat list of values."""
    promql = _history_query(metric_name, vnf_name, namespace, "5m")
    if promql is None:
        return []
    try:
        end = datetime.utcnow()
        start = end - window
        raw = prom.range_query(promql, start=start, end=end, step="30s")
    except Exception as exc:
        logger.warning(
            "Anomaly detector: range query failed for %s/%s: %s",
            vnf_name, metric_name, exc,
        )
        return []

    out: list[float] = []
    for series in raw or []:
        for _, val in series.get("values", []) or []:
            try:
                fv = float(val)
            except (ValueError, TypeError):
                continue
            if fv != fv:  # NaN
                continue
            out.append(fv)
    return out


# ──────────────────── Threshold loading (for detector 2) ──────────────────── #


def _load_critical_thresholds() -> dict[str, float]:
    try:
        with open(SLA_THRESHOLDS_PATH) as f:
            data = yaml.safe_load(f)
        infra = data.get("infra_thresholds") or {}
    except Exception as exc:
        logger.warning("Could not load SLA thresholds: %s", exc)
        infra = {}
    return {
        "cpu_utilization": float(infra.get("cpu_utilization", {}).get("critical", 85)),
        "memory_utilization": float(infra.get("memory_utilization", {}).get("critical", 90)),
        "pod_restarts": float(infra.get("pod_restart_count", {}).get("critical", 5)),
    }


# ──────────────────── Detectors ──────────────────── #


def _detect_point_outlier(
    event: MetricEvent,
    prom: PrometheusClient,
    namespace: str,
    timestamp: str,
) -> AnomalyAlert | None:
    metric = event.get("metric_name", "")
    if metric not in ("cpu_utilization", "memory_utilization", "network_rx_mbps"):
        return None
    vnf = event.get("vnf_name", "")
    if not vnf:
        return None

    history = _fetch_history(prom, metric, vnf, namespace, _BASELINE_WINDOW)
    # The KPI Monitor's value is a percentage (cpu/mem) or Mbps (net); the
    # raw Prometheus query returns cores / bytes / Mbps. Compare on the raw
    # scale by converting the historical samples to the same units the
    # KPI Monitor reports — but for outlier detection the *shape* matters,
    # not the absolute scale, so we operate directly on whichever units the
    # range query returned and compare against a current value drawn from
    # the same range query (the most recent sample) when available.
    if history:
        current_raw = history[-1]
        baseline_history = history[:-1]
    else:
        current_raw = 0.0
        baseline_history = history

    z = _robust_z(current_raw, baseline_history)
    if z is None:
        return None
    sev = _z_to_severity(z)
    if sev is None:
        return None

    severity, confidence = sev
    median_raw = float(np.median(baseline_history)) if baseline_history else 0.0

    return {
        "alert_id": _make_alert_id(vnf, metric, timestamp),
        "type": "point_outlier",
        "confidence": confidence,
        "description": (
            f"{vnf} {metric} = {event.get('value')} {event.get('unit', '')}"
            f" (z={z:.1f} vs 15m baseline), severity={severity}"
        ),
        "affected_metrics": [
            {
                "name": metric,
                "current": float(event.get("value", 0.0)),
                "baseline": round(median_raw, 4),
                "unit": event.get("unit", ""),
            }
        ],
        "suggested_cause": _default_cause(metric, "point_outlier"),
        "suggested_actions": _default_actions(metric, severity),
    }


def _detect_sustained(
    event: MetricEvent,
    prom: PrometheusClient,
    namespace: str,
    critical_thresholds: dict[str, float],
    timestamp: str,
) -> AnomalyAlert | None:
    if event.get("threshold_status") != "critical":
        return None
    metric = event.get("metric_name", "")
    threshold = critical_thresholds.get(metric)
    if threshold is None:
        return None
    vnf = event.get("vnf_name", "")
    if not vnf:
        return None

    history = _fetch_history(prom, metric, vnf, namespace, _SUSTAINED_WINDOW)
    if len(history) < 4:
        return None

    over = sum(1 for v in history if v >= threshold)
    fraction = over / len(history)
    if fraction < 0.80:
        return None

    return {
        "alert_id": _make_alert_id(vnf, f"sustained_{metric}", timestamp),
        "type": "sustained_overload",
        "confidence": 0.90,
        "description": (
            f"{vnf} {metric} above critical threshold for {round(fraction * 100)}% "
            f"of the last 5 minutes"
        ),
        "affected_metrics": [
            {
                "name": metric,
                "current": float(event.get("value", 0.0)),
                "baseline": threshold,
                "unit": event.get("unit", ""),
            }
        ],
        "suggested_cause": _default_cause(metric, "sustained_overload"),
        "suggested_actions": _default_actions(metric, "high"),
    }


def _detect_restart_spike(
    event: MetricEvent,
    prom: PrometheusClient,
    namespace: str,
    timestamp: str,
) -> AnomalyAlert | None:
    if event.get("metric_name") != "pod_restarts":
        return None
    current = float(event.get("value", 0.0))
    if current <= 0:
        return None
    vnf = event.get("vnf_name", "")
    if not vnf:
        return None

    history = _fetch_history(prom, "pod_restarts", vnf, namespace, _RESTART_WINDOW)
    if not history:
        return None
    past = float(history[0])
    delta = current - past
    if delta < 2:
        return None

    if delta >= 5:
        severity, confidence = "critical", 0.95
    else:
        severity, confidence = "high", 0.85

    return {
        "alert_id": _make_alert_id(vnf, "restart_spike", timestamp),
        "type": "instability",
        "confidence": confidence,
        "description": (
            f"{vnf} restarted {int(delta)} times in the last hour "
            f"(severity={severity})"
        ),
        "affected_metrics": [
            {
                "name": "pod_restarts",
                "current": current,
                "baseline": past,
                "unit": "count",
            }
        ],
        "suggested_cause": _default_cause("pod_restarts", "instability"),
        "suggested_actions": _default_actions("pod_restarts", severity),
    }


# ──────────────────── LLM enhancement ──────────────────── #


def _enhance_with_llm(
    alert: AnomalyAlert,
    metrics: list[MetricEvent],
    history_snapshot: dict[str, list[float]],
) -> AnomalyAlert:
    """
    Best-effort LLM augmentation. Merges narrative fields only — never
    overrides type / confidence / affected_metrics / alert_id.
    """
    try:
        llm = LLMCore()
        result = llm.invoke(
            "anomaly_detector",
            {
                "anomaly_data": alert,
                "historical_context": history_snapshot,
                "system_state": {"current_metrics": metrics},
            },
            expect_json=True,
        )
    except Exception as exc:
        logger.warning("LLM enhancement skipped for %s: %s", alert["alert_id"], exc)
        return alert

    if not isinstance(result, dict):
        return alert

    if (cause := result.get("suggested_cause")):
        alert["suggested_cause"] = str(cause)
    if (actions := result.get("suggested_actions")) and isinstance(actions, list):
        alert["suggested_actions"] = [str(a) for a in actions[:8]]
    if (desc := result.get("description")):
        alert["description"] = str(desc)
    return alert


# ──────────────────── Summary ──────────────────── #


def _build_summary(alerts: list[AnomalyAlert]) -> str:
    if not alerts:
        return "**Anomaly Detector** — no anomalies detected."

    counts = {"point_outlier": 0, "sustained_overload": 0, "instability": 0}
    for a in alerts:
        counts[a.get("type", "point_outlier")] = counts.get(a.get("type", "point_outlier"), 0) + 1

    lines = [
        "**Anomaly Detector** — alerts raised",
        "",
        f"- Total: {len(alerts)}  "
        f"|  point: {counts['point_outlier']}  "
        f"|  sustained: {counts['sustained_overload']}  "
        f"|  instability: {counts['instability']}",
    ]
    for a in alerts[:10]:
        lines.append(
            f"  - [{a.get('confidence', 0):.2f}] {a.get('type')}: "
            f"{a.get('description', '')}"
        )
    return "\n".join(lines)


# ──────────────────── Agent ──────────────────── #


def anomaly_detector_agent(state: OrchestratorState) -> dict[str, Any]:
    """
    LangGraph node: Anomaly Detector.

    Reads:  state["current_metrics"]
    Writes: state["anomaly_alerts"]  (append-only — return ONLY new alerts),
            state["messages"], state["current_agent"], state["phase"]
    """
    logger.info("Anomaly Detector Agent: starting")

    metrics: list[MetricEvent] = list(state.get("current_metrics") or [])
    if not metrics:
        return {
            "anomaly_alerts": [],
            "current_agent": "anomaly_detector",
            "phase": "post_deployment",
            "messages": [
                {"role": "agent", "content": "**Anomaly Detector** — no metrics to analyse."}
            ],
        }

    try:
        prom = PrometheusClient()
    except Exception as exc:
        logger.error("Could not instantiate PrometheusClient: %s", exc)
        return {
            "error": f"Prometheus client init failed: {exc}",
            "anomaly_alerts": [],
            "current_agent": "anomaly_detector",
            "messages": [
                {"role": "agent",
                 "content": f"Anomaly Detector: Prometheus client init failed: {exc}"}
            ],
        }

    if not prom.ping():
        logger.error("Prometheus unreachable at %s", prom.url)
        return {
            "error": f"Prometheus unreachable at {prom.url}",
            "anomaly_alerts": [],
            "current_agent": "anomaly_detector",
            "messages": [
                {"role": "agent",
                 "content": f"Anomaly Detector: Prometheus unreachable at {prom.url}"}
            ],
        }

    ns = K8S_NAMESPACE
    timestamp = datetime.utcnow().isoformat()
    critical_thresholds = _load_critical_thresholds()

    alerts: list[AnomalyAlert] = []
    history_snapshot: dict[str, list[float]] = {}

    for event in metrics:
        for detector in (
            lambda e: _detect_point_outlier(e, prom, ns, timestamp),
            lambda e: _detect_sustained(e, prom, ns, critical_thresholds, timestamp),
            lambda e: _detect_restart_spike(e, prom, ns, timestamp),
        ):
            try:
                alert = detector(event)
            except Exception as exc:
                logger.warning(
                    "Detector failed on %s/%s: %s",
                    event.get("vnf_name"), event.get("metric_name"), exc,
                )
                continue
            if alert is not None:
                alerts.append(alert)

    logger.info("Anomaly Detector: %d raw alerts before dedup", len(alerts))

    now = datetime.utcnow()
    alerts, suppressed = _filter_suppressed(alerts, now)
    if suppressed:
        logger.info(
            "Anomaly Detector: suppressed %d duplicate alert(s) within %s window",
            suppressed, _DEDUP_WINDOW,
        )

    logger.info("Anomaly Detector: %d alerts after dedup, before LLM enhancement", len(alerts))

    for alert in alerts[:_MAX_LLM_CALLS_PER_CYCLE]:
        _enhance_with_llm(alert, metrics, history_snapshot)

    summary = _build_summary(alerts)

    return {
        "anomaly_alerts": alerts,
        "current_agent": "anomaly_detector",
        "phase": "post_deployment",
        "messages": [{"role": "agent", "content": summary}],
    }
