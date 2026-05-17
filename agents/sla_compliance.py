"""
SLA Compliance Agent — The Contract Auditor.

Maps live infrastructure and 5G control-plane metrics onto the contractual
SLA rules declared in ``config/sla_thresholds.yaml``, computes compliance
percentages over each rule's measurement window, predicts time-to-breach
via linear extrapolation, and (optionally) asks the LLM for a trade-off
summary.

Pipeline position: 3rd in post-deployment loop
  KPI Monitor → Anomaly Detector → [SLA Compliance] → Planner → ...

Input:  OrchestratorState with optional current_metrics, anomaly_alerts
Output: OrchestratorState with sla_status (list[SLAStatus]) and a markdown
        compliance summary on state["messages"].
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Literal

import yaml

from config.settings import K8S_NAMESPACE, SLA_THRESHOLDS_PATH
from core.llm_core import LLMCore
from core.state import OrchestratorState, SLAStatus
from infra.prometheus_client import PrometheusClient

logger = logging.getLogger(__name__)


# ──────────────────────── Window parsing ──────────────────────── #

_DURATION_RE = re.compile(r"^(\d+)\s*([smhd])$")

# Traffic floor (Mbps): below this, the upf_throughput SLA rule is reported
# as `unknown` rather than `breach`. Throughput compliance is only meaningful
# under load; control-plane chatter alone is well below 1 Mbps.
_TRAFFIC_FLOOR_MBPS = 1.0


def _parse_duration(s: str) -> timedelta:
    """Parse a Prometheus-style duration like '5m', '1h', '30s', '2d'."""
    m = _DURATION_RE.match((s or "").strip())
    if not m:
        return timedelta(minutes=5)
    n = int(m.group(1))
    unit = m.group(2)
    return {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
    }[unit]


# ──────────────────────── PromQL builders ──────────────────────── #

def _build_query(metric: str, namespace: str, window: str) -> str | None:
    """
    Map an SLA-rule ``metric`` name to a concrete PromQL expression.

    Returns ``None`` for metrics that are not yet wired (the rule will be
    reported with status ``unknown`` rather than raising).
    """
    ns = namespace
    w = window or "5m"

    if metric == "upf_throughput_mbps":
        return (
            f'sum(rate(container_network_transmit_bytes_total'
            f'{{namespace="{ns}", pod=~"oai-upf.*"}}[{w}])) '
            f'* 8 / 1000000'
        )

    if metric == "availability_pct":
        return f'avg(up{{namespace="{ns}"}}) * 100'

    if metric == "latency_p99_ms":
        return (
            f'histogram_quantile(0.99, sum by (le) ('
            f'rate(probe_duration_seconds_bucket{{namespace="{ns}"}}[{w}]))) '
            f'* 1000'
        )

    if metric == "amf_registration_success_pct":
        return (
            f'(sum(rate(amf_registration_success_total'
            f'{{namespace="{ns}"}}[{w}])) '
            f'/ sum(rate(amf_registration_total'
            f'{{namespace="{ns}"}}[{w}]))) * 100'
        )

    if metric == "pdu_session_setup_ms":
        return (
            f'histogram_quantile(0.95, sum by (le) ('
            f'rate(smf_pdu_session_setup_duration_seconds_bucket'
            f'{{namespace="{ns}"}}[{w}]))) * 1000'
        )

    if metric == "upf_packet_loss_pct":
        return (
            f'(sum(rate(upf_packets_dropped_total'
            f'{{namespace="{ns}"}}[{w}])) '
            f'/ sum(rate(upf_packets_total'
            f'{{namespace="{ns}"}}[{w}]))) * 100'
        )

    return None


def _build_fallback_query(metric: str, namespace: str, window: str) -> str | None:
    """
    Fallback PromQL when the primary OAI control-plane metric is not
    exported. Uses infrastructure-level proxies that approximate the SLA
    intent. Used only when the primary query returns no samples.

    - amf_registration_success_pct → AMF pod readiness % (`up`)
    - upf_packet_loss_pct          → container_network packet drops on UPF pods
    - pdu_session_setup_ms         → SMF restart-derived health proxy (1 if no recent restarts, else 0)
    - latency_p99_ms               → no usable infra proxy; remains unknown
    """
    ns = namespace
    w = window or "5m"

    if metric == "amf_registration_success_pct":
        return f'avg(up{{namespace="{ns}", pod=~"oai-amf.*"}}) * 100'

    if metric == "upf_packet_loss_pct":
        return (
            f'(sum(rate(container_network_transmit_packets_dropped_total'
            f'{{namespace="{ns}", pod=~"oai-upf.*"}}[{w}])) '
            f'/ clamp_min(sum(rate(container_network_transmit_packets_total'
            f'{{namespace="{ns}", pod=~"oai-upf.*"}}[{w}])), 1)) * 100'
        )

    if metric == "pdu_session_setup_ms":
        # No latency proxy at infra level; report 0ms when SMF healthy, large
        # number when SMF restarting. This is intentionally coarse and is
        # documented as a fallback in the thesis.
        return (
            f'(sum(increase(kube_pod_container_status_restarts_total'
            f'{{namespace="{ns}", pod=~"oai-smf.*"}}[{w}])) > bool 0) * 9999'
        )

    return None


# ──────────────────────── Compliance arithmetic ──────────────────────── #

ComplianceStatus = Literal["compliant", "warning", "breach", "unknown"]


def _satisfies(value: float, threshold: float, operator: str) -> bool:
    """Return True iff *value* satisfies the rule (op threshold)."""
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    if operator == ">":
        return value > threshold
    if operator == ">=":
        return value >= threshold
    if operator == "==":
        return value == threshold
    return False


def _classify(
    value: float, threshold: float, operator: str
) -> ComplianceStatus:
    """
    Three-band verdict.

    The 'warning' band is a 10% approach to the threshold from the
    compliant side. For ``<`` rules it lies between threshold and
    0.9 * threshold; for ``>=`` rules it lies between threshold and
    1.1 * threshold.
    """
    if not _satisfies(value, threshold, operator):
        return "breach"

    margin = abs(threshold) * 0.10
    if operator in ("<", "<="):
        warn_floor = threshold - margin
        return "warning" if value >= warn_floor else "compliant"
    if operator in (">", ">="):
        warn_ceil = threshold + margin
        return "warning" if value <= warn_ceil else "compliant"
    return "compliant"


def _compute_trend(
    samples: list[tuple[float, float]], operator: str
) -> Literal["improving", "stable", "degrading"]:
    """
    Trend over a list of (ts, value) tuples.

    'Improvement' direction depends on the rule operator:
      - ``<``  → smaller is better → improving means decreasing
      - ``>=`` → larger is better  → improving means increasing
    """
    if len(samples) < 4:
        return "stable"

    mid = len(samples) // 2
    first_avg = sum(v for _, v in samples[:mid]) / mid
    second_avg = sum(v for _, v in samples[mid:]) / (len(samples) - mid)

    if first_avg == 0:
        return "stable"
    delta_pct = (second_avg - first_avg) / abs(first_avg) * 100

    if abs(delta_pct) < 5:
        return "stable"

    going_up = delta_pct > 0
    larger_is_better = operator in (">", ">=")
    improving = going_up == larger_is_better
    return "improving" if improving else "degrading"


def _compliance_pct(
    samples: list[tuple[float, float]], threshold: float, operator: str
) -> float:
    if not samples:
        return 0.0
    ok = sum(1 for _, v in samples if _satisfies(v, threshold, operator))
    return round(ok / len(samples) * 100.0, 2)


def _estimate_time_to_breach(
    samples: list[tuple[float, float]],
    threshold: float,
    operator: str,
) -> float | None:
    """
    Linear-regression extrapolation of the value series toward the
    threshold. Returns minutes until projected crossing, or ``None`` if
    the trend is not heading toward a breach.
    """
    if len(samples) < 4:
        return None

    n = len(samples)
    mean_t = sum(t for t, _ in samples) / n
    mean_v = sum(v for _, v in samples) / n
    num = sum((t - mean_t) * (v - mean_v) for t, v in samples)
    den = sum((t - mean_t) ** 2 for t, _ in samples)
    if den == 0:
        return None
    slope = num / den
    intercept = mean_v - slope * mean_t

    if slope == 0:
        return None
    crossing_t = (threshold - intercept) / slope
    last_t = samples[-1][0]
    delta_seconds = crossing_t - last_t
    if delta_seconds <= 0:
        return None

    heading_to_breach = (
        (operator in ("<", "<=") and slope > 0)
        or (operator in (">", ">=") and slope < 0)
    )
    if not heading_to_breach:
        return None
    return round(delta_seconds / 60.0, 1)


# ──────────────────────── Rule loading ──────────────────────── #

def _load_sla_rules() -> list[dict[str, Any]]:
    try:
        with open(SLA_THRESHOLDS_PATH) as f:
            data = yaml.safe_load(f)
        return data.get("sla_rules") or []
    except Exception as exc:
        logger.warning("Could not load SLA rules: %s", exc)
        return []


# ──────────────────────── Per-rule evaluation ──────────────────────── #

def _evaluate_rule(
    prom: PrometheusClient,
    rule: dict[str, Any],
    namespace: str,
) -> SLAStatus:
    """
    Evaluate a single SLA rule end-to-end: instant value + range samples
    + compliance% + trend + time-to-breach.
    """
    name = rule.get("name", "<unnamed>")
    metric = rule.get("metric", "")
    threshold = float(rule.get("threshold", 0.0))
    operator = rule.get("operator", "<")
    window = rule.get("measurement_window", "5m")

    base: SLAStatus = {
        "rule": name,
        "current_value": 0.0,
        "threshold": threshold,
        "status": "unknown",
        "time_to_breach_min": None,
        "compliance_pct": 0.0,
        "data_source": "none",  # type: ignore[typeddict-unknown-key]
    }

    primary = _build_query(metric, namespace, window)
    fallback = _build_fallback_query(metric, namespace, window)
    if primary is None and fallback is None:
        logger.debug("SLA rule %s: no PromQL builder for metric %s", name, metric)
        return base

    promql, source = (primary, "primary") if primary else (fallback, "fallback")

    def _try_instant(q: str) -> float | None:
        try:
            inst = prom.instant_query(q)
        except Exception as exc:
            logger.warning("SLA rule %s: instant query failed: %s", name, exc)
            return None
        if not inst:
            return None
        try:
            v = float(inst[0].get("value", [None, "nan"])[1])
        except (ValueError, TypeError):
            return None
        return None if v != v else v

    current = _try_instant(promql) if promql else None
    if current is None and primary and fallback:
        # Primary metric absent (probes not exported); try infra-level fallback.
        promql, source = fallback, "fallback"
        current = _try_instant(promql)
        if current is not None:
            logger.info("SLA rule %s: using fallback metric for %s", name, metric)

    if current is None:
        base["data_source"] = "none"  # type: ignore[typeddict-unknown-key]
        return base

    base["current_value"] = round(current, 4)

    # Traffic-active gate: throughput compliance is only meaningful under load.
    # If the live throughput is below the evaluation floor, the workload is
    # idle (control-plane chatter only) and the rule is reported as `unknown`
    # rather than `breach`, preventing the Planner from chasing a metric the
    # workload cannot satisfy.
    if metric == "upf_throughput_mbps" and current < _TRAFFIC_FLOOR_MBPS:
        base["status"] = "unknown"
        base["data_source"] = "traffic_below_floor"  # type: ignore[typeddict-unknown-key]
        logger.debug(
            "SLA rule %s: throughput %.4f < %.1f Mbps → reporting as unknown",
            name, current, _TRAFFIC_FLOOR_MBPS,
        )
        return base

    base["status"] = _classify(current, threshold, operator)
    base["data_source"] = source  # type: ignore[typeddict-unknown-key]

    win_delta = _parse_duration(window)
    try:
        end = datetime.utcnow()
        start = end - win_delta
        raw = prom.range_query(promql, start=start, end=end, step="30s")
    except Exception as exc:
        logger.warning("SLA rule %s: range query failed: %s", name, exc)
        raw = []

    samples: list[tuple[float, float]] = []
    for series in raw or []:
        for ts, val in series.get("values", []) or []:
            try:
                fv = float(val)
            except (ValueError, TypeError):
                continue
            if fv != fv:
                continue
            samples.append((float(ts), fv))
    samples.sort(key=lambda x: x[0])

    if samples:
        base["compliance_pct"] = _compliance_pct(samples, threshold, operator)
        base["time_to_breach_min"] = _estimate_time_to_breach(
            samples, threshold, operator
        )
        base["trend"] = _compute_trend(samples, operator)  # type: ignore[typeddict-unknown-key]
    else:
        base["compliance_pct"] = 100.0 if base["status"] == "compliant" else 0.0
        base["trend"] = "stable"  # type: ignore[typeddict-unknown-key]

    return base


# ──────────────────────── Summary ──────────────────────── #

def _overall_status(statuses: list[SLAStatus]) -> str:
    known = [s for s in statuses if s.get("status") != "unknown"]
    if not known:
        return "unknown"
    if any(s["status"] == "breach" for s in known):
        return "breached"
    if any(s["status"] == "warning" for s in known):
        return "at_risk"
    return "compliant"


def _build_summary(
    statuses: list[SLAStatus], llm_result: dict[str, Any] | None
) -> str:
    overall = _overall_status(statuses)
    counts = {"compliant": 0, "warning": 0, "breach": 0, "unknown": 0}
    for s in statuses:
        key = s.get("status", "unknown")
        counts[key] = counts.get(key, 0) + 1

    lines = [
        "**SLA Compliance** — contractual audit",
        "",
        f"- Overall: **{overall}**",
        f"- Compliant: {counts['compliant']}  |  Warning: {counts['warning']}  "
        f"|  Breach: {counts['breach']}  |  Unknown: {counts['unknown']}",
    ]

    breaches = [s for s in statuses if s["status"] == "breach"]
    warnings = [s for s in statuses if s["status"] == "warning"]
    for s in breaches + warnings:
        ttb = s.get("time_to_breach_min")
        ttb_str = f", ETA {ttb}m" if ttb else ""
        lines.append(
            f"  - {s['rule']}: {s['current_value']} (threshold {s['threshold']}) "
            f"→ {s['status']} ({s.get('compliance_pct', 0)}% compliant{ttb_str})"
        )

    if llm_result:
        lines.append("")
        actions = llm_result.get("recommended_actions") or []
        if actions:
            lines.append("**Recommended actions:**")
            for a in actions[:5]:
                lines.append(f"  - {a}")
        if (trade := llm_result.get("trade_off_analysis")):
            lines.append("")
            lines.append(f"**Trade-offs:** {trade}")

    return "\n".join(lines)


# ──────────────────────── Agent ──────────────────────── #

def sla_compliance_agent(state: OrchestratorState) -> dict[str, Any]:
    """
    LangGraph node: SLA Compliance.

    Reads:  state["current_metrics"], state["anomaly_alerts"]
    Writes: state["sla_status"], state["messages"], state["current_agent"], state["phase"]
    """
    logger.info("SLA Compliance Agent: starting")

    rules = _load_sla_rules()
    if not rules:
        logger.warning("No SLA rules loaded — nothing to evaluate")
        return {
            "sla_status": [],
            "current_agent": "sla_compliance",
            "phase": "post_deployment",
            "messages": [
                {"role": "agent", "content": "SLA Compliance: no rules configured."}
            ],
        }

    try:
        prom = PrometheusClient()
    except Exception as exc:
        logger.error("Could not instantiate PrometheusClient: %s", exc)
        return {
            "error": f"Prometheus client init failed: {exc}",
            "sla_status": [],
            "current_agent": "sla_compliance",
            "messages": [
                {"role": "agent",
                 "content": f"SLA Compliance: Prometheus client init failed: {exc}"}
            ],
        }

    if not prom.ping():
        logger.error("Prometheus unreachable at %s", prom.url)
        return {
            "error": f"Prometheus unreachable at {prom.url}",
            "sla_status": [],
            "current_agent": "sla_compliance",
            "messages": [
                {"role": "agent",
                 "content": f"SLA Compliance: Prometheus unreachable at {prom.url}"}
            ],
        }

    ns = K8S_NAMESPACE
    statuses: list[SLAStatus] = [_evaluate_rule(prom, r, ns) for r in rules]
    logger.info(
        "SLA Compliance: evaluated %d rules (%d unknown)",
        len(statuses),
        sum(1 for s in statuses if s["status"] == "unknown"),
    )

    llm_result: dict[str, Any] | None = None
    known = [s for s in statuses if s["status"] != "unknown"]
    if known:
        try:
            llm = LLMCore()
            compact = [
                {
                    "rule": s["rule"],
                    "value": s["current_value"],
                    "threshold": s["threshold"],
                    "status": s["status"],
                    "compliance_pct": s.get("compliance_pct"),
                    "trend": s.get("trend"),  # type: ignore[typeddict-item]
                    "time_to_breach_min": s.get("time_to_breach_min"),
                }
                for s in statuses
            ]
            result = llm.invoke(
                "sla_compliance",
                {
                    "metrics": state.get("current_metrics") or [],
                    "sla_definitions": rules,
                    "compliance_history": compact,
                    "anomaly_alerts": state.get("anomaly_alerts") or [],
                },
                expect_json=True,
            )
            if isinstance(result, dict):
                llm_result = result
            else:
                logger.warning("SLA Compliance LLM returned non-JSON text")
        except Exception as exc:
            logger.warning("SLA Compliance LLM enhancement failed: %s", exc)

    summary = _build_summary(statuses, llm_result)

    return {
        "sla_status": statuses,
        "current_agent": "sla_compliance",
        "phase": "post_deployment",
        "messages": [{"role": "agent", "content": summary}],
    }
