"""
Unit tests for agents.sla_compliance.

All tests use mocks — no live cluster or Prometheus required.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from agents.sla_compliance import (
    _build_query,
    _classify,
    _compliance_pct,
    _compute_trend,
    _estimate_time_to_breach,
    _overall_status,
    _parse_duration,
    _satisfies,
    sla_compliance_agent,
)


# ──────────────────── Helper unit tests ──────────────────── #


class TestParseDuration:
    def test_units(self):
        assert _parse_duration("30s") == timedelta(seconds=30)
        assert _parse_duration("5m") == timedelta(minutes=5)
        assert _parse_duration("1h") == timedelta(hours=1)
        assert _parse_duration("2d") == timedelta(days=2)

    def test_default_on_garbage(self):
        assert _parse_duration("") == timedelta(minutes=5)
        assert _parse_duration("abc") == timedelta(minutes=5)


class TestSatisfies:
    def test_lt(self):
        assert _satisfies(10, 20, "<") is True
        assert _satisfies(20, 20, "<") is False

    def test_gte(self):
        assert _satisfies(99.95, 99.9, ">=") is True
        assert _satisfies(99.0, 99.9, ">=") is False


class TestClassify:
    def test_lt_compliant_warning_breach(self):
        # threshold 20, 10% margin = warn band [18, 20)
        assert _classify(15, 20, "<") == "compliant"
        assert _classify(19, 20, "<") == "warning"
        assert _classify(20, 20, "<") == "breach"
        assert _classify(25, 20, "<") == "breach"

    def test_gte_compliant_warning_breach(self):
        # threshold 1000, 10% margin = warn band [1000, 1100]
        assert _classify(1500, 1000, ">=") == "compliant"
        assert _classify(1050, 1000, ">=") == "warning"
        assert _classify(999, 1000, ">=") == "breach"


class TestCompliancePct:
    def test_all_compliant(self):
        samples = [(0.0, 5.0), (1.0, 6.0), (2.0, 7.0)]
        assert _compliance_pct(samples, 20.0, "<") == 100.0

    def test_partial(self):
        samples = [(0.0, 5.0), (1.0, 25.0), (2.0, 5.0), (3.0, 30.0)]
        assert _compliance_pct(samples, 20.0, "<") == 50.0

    def test_empty(self):
        assert _compliance_pct([], 20.0, "<") == 0.0


class TestComputeTrend:
    def test_degrading_for_lt_rule(self):
        # values increasing → degrading for "smaller is better"
        samples = [(0.0, 5.0), (1.0, 6.0), (2.0, 9.0), (3.0, 12.0)]
        assert _compute_trend(samples, "<") == "degrading"

    def test_improving_for_lt_rule(self):
        samples = [(0.0, 12.0), (1.0, 9.0), (2.0, 6.0), (3.0, 5.0)]
        assert _compute_trend(samples, "<") == "improving"

    def test_improving_for_gte_rule(self):
        samples = [(0.0, 100.0), (1.0, 110.0), (2.0, 130.0), (3.0, 150.0)]
        assert _compute_trend(samples, ">=") == "improving"

    def test_stable_within_5pct(self):
        samples = [(0.0, 100.0), (1.0, 101.0), (2.0, 99.0), (3.0, 100.5)]
        assert _compute_trend(samples, "<") == "stable"

    def test_too_few_samples(self):
        assert _compute_trend([(0.0, 1.0), (1.0, 2.0)], "<") == "stable"


class TestEstimateTimeToBreach:
    def test_lt_rule_heading_to_breach(self):
        # threshold 20, values rising linearly toward 20
        samples = [(0.0, 5.0), (60.0, 10.0), (120.0, 15.0), (180.0, 17.0)]
        ttb = _estimate_time_to_breach(samples, 20.0, "<")
        assert ttb is not None
        assert 0 < ttb < 10  # minutes

    def test_lt_rule_not_heading_to_breach(self):
        # values decreasing — getting safer
        samples = [(0.0, 18.0), (60.0, 15.0), (120.0, 12.0), (180.0, 10.0)]
        assert _estimate_time_to_breach(samples, 20.0, "<") is None

    def test_too_few_samples(self):
        assert _estimate_time_to_breach([(0.0, 1.0)], 20.0, "<") is None


class TestBuildQuery:
    def test_known_metrics(self):
        for m in (
            "upf_throughput_mbps",
            "availability_pct",
            "latency_p99_ms",
            "amf_registration_success_pct",
            "pdu_session_setup_ms",
            "upf_packet_loss_pct",
        ):
            q = _build_query(m, "oai-5g", "5m")
            assert q is not None and "oai-5g" in q

    def test_unknown_metric(self):
        assert _build_query("ufo_signal_strength", "oai-5g", "5m") is None


class TestOverallStatus:
    def test_breached_dominates(self):
        statuses = [
            {"status": "compliant"},
            {"status": "warning"},
            {"status": "breach"},
        ]
        assert _overall_status(statuses) == "breached"

    def test_at_risk(self):
        statuses = [{"status": "compliant"}, {"status": "warning"}]
        assert _overall_status(statuses) == "at_risk"

    def test_compliant(self):
        statuses = [{"status": "compliant"}, {"status": "compliant"}]
        assert _overall_status(statuses) == "compliant"

    def test_all_unknown(self):
        statuses = [{"status": "unknown"}, {"status": "unknown"}]
        assert _overall_status(statuses) == "unknown"


# ──────────────────── Agent-level tests ──────────────────── #


def _base_state(**overrides):
    state = {
        "current_metrics": [],
        "anomaly_alerts": [],
        "messages": [],
        "current_agent": "",
        "phase": "post_deployment",
    }
    state.update(overrides)
    return state


class TestSlaComplianceAgent:
    @patch("agents.sla_compliance.LLMCore")
    @patch("agents.sla_compliance.PrometheusClient")
    def test_breach_detection(self, mock_prom_cls, mock_llm_cls):
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"

        # availability rule (>= 99.9) returns 95 → breach
        # upf_throughput rule (>= 1000) returns 1500 → compliant
        # all other rules return [] → unknown
        def instant(query: str):
            if "avg(up" in query:
                return [{"value": [0.0, "95.0"]}]
            if "container_network_transmit_bytes_total" in query:
                return [{"value": [0.0, "1500.0"]}]
            return []

        mock_prom.instant_query.side_effect = instant
        mock_prom.range_query.return_value = []
        mock_prom_cls.return_value = mock_prom

        mock_llm_cls.return_value.invoke.return_value = {
            "compliance_summary": {"overall_status": "breached"},
            "recommended_actions": ["scale UDR"],
            "trade_off_analysis": "n/a",
        }

        update = sla_compliance_agent(_base_state())

        assert update["current_agent"] == "sla_compliance"
        assert update["phase"] == "post_deployment"
        assert "error" not in update

        statuses = update["sla_status"]
        avail = next(s for s in statuses if s["rule"] == "service_availability")
        assert avail["status"] == "breach"
        assert avail["current_value"] == 95.0

        upf = next(s for s in statuses if s["rule"] == "upf_throughput")
        assert upf["status"] == "compliant"
        assert upf["current_value"] == 1500.0

        latency = next(s for s in statuses if s["rule"] == "e2e_latency")
        assert latency["status"] == "unknown"

        assert "**SLA Compliance**" in update["messages"][0]["content"]
        assert "scale UDR" in update["messages"][0]["content"]

    @patch("agents.sla_compliance.PrometheusClient")
    def test_prometheus_unreachable(self, mock_prom_cls):
        mock_prom = MagicMock()
        mock_prom.ping.return_value = False
        mock_prom.url = "http://mock:9090"
        mock_prom_cls.return_value = mock_prom

        update = sla_compliance_agent(_base_state())

        assert update["current_agent"] == "sla_compliance"
        assert update["sla_status"] == []
        assert "error" in update
        assert "unreachable" in update["error"]

    @patch("agents.sla_compliance.LLMCore")
    @patch("agents.sla_compliance.PrometheusClient")
    def test_llm_failure_is_nonblocking(self, mock_prom_cls, mock_llm_cls):
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        mock_prom.instant_query.side_effect = lambda q: (
            [{"value": [0.0, "99.99"]}] if "avg(up" in q else []
        )
        mock_prom.range_query.return_value = []
        mock_prom_cls.return_value = mock_prom

        mock_llm_cls.return_value.invoke.side_effect = RuntimeError("LLM down")

        update = sla_compliance_agent(_base_state())

        assert "error" not in update
        avail = next(s for s in update["sla_status"]
                     if s["rule"] == "service_availability")
        assert avail["status"] in ("compliant", "warning")
        assert "**SLA Compliance**" in update["messages"][0]["content"]

    @patch("agents.sla_compliance.LLMCore")
    @patch("agents.sla_compliance.PrometheusClient")
    def test_compliance_pct_from_range(self, mock_prom_cls, mock_llm_cls):
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        mock_prom.instant_query.side_effect = lambda q: (
            [{"value": [0.0, "1500.0"]}]
            if "container_network_transmit_bytes_total" in q else []
        )
        # 4 samples, 3 above threshold (1000) → 75% compliant
        mock_prom.range_query.side_effect = lambda q, **kw: (
            [{"values": [
                [0.0, "1500"], [30.0, "1200"], [60.0, "800"], [90.0, "1100"],
            ]}]
            if "container_network_transmit_bytes_total" in q else []
        )
        mock_prom_cls.return_value = mock_prom
        mock_llm_cls.return_value.invoke.side_effect = Exception("skip")

        update = sla_compliance_agent(_base_state())

        upf = next(s for s in update["sla_status"] if s["rule"] == "upf_throughput")
        assert upf["compliance_pct"] == 75.0
