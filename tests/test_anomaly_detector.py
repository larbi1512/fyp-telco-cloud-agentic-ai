"""
Unit tests for agents.anomaly_detector.

All tests use mocks — no live cluster or Prometheus required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.anomaly_detector import (
    _default_actions,
    _make_alert_id,
    _robust_z,
    _z_to_severity,
    anomaly_detector_agent,
)


# ──────────────────── Helper unit tests ──────────────────── #


class TestRobustZ:
    def test_constant_series_returns_none(self):
        assert _robust_z(50.0, [10.0] * 20) is None

    def test_too_few_samples(self):
        assert _robust_z(50.0, [1.0, 2.0, 3.0]) is None

    def test_obvious_outlier(self):
        # 30 samples around 30, current=95 → big z
        baseline = [30.0 + (i % 5) for i in range(30)]
        z = _robust_z(95.0, baseline)
        assert z is not None
        assert z > 4

    def test_in_band_value(self):
        # 30 samples around 30, current=31 → small z
        baseline = [30.0 + (i % 5) for i in range(30)]
        z = _robust_z(31.0, baseline)
        assert z is not None
        assert z < 1


class TestZToSeverity:
    def test_below_threshold(self):
        assert _z_to_severity(2.5) is None
        assert _z_to_severity(0.0) is None

    def test_medium(self):
        sev, conf = _z_to_severity(3.0)
        assert sev == "medium"
        assert conf == pytest.approx(0.7)

    def test_high(self):
        sev, _ = _z_to_severity(4.5)
        assert sev == "high"

    def test_critical(self):
        sev, conf = _z_to_severity(7.0)
        assert sev == "critical"
        assert conf == pytest.approx(0.95)


class TestMakeAlertId:
    def test_uniqueness(self):
        ids = {
            _make_alert_id("oai-amf", "cpu", "2026-04-25T12:00:00"),
            _make_alert_id("oai-smf", "cpu", "2026-04-25T12:00:00"),
            _make_alert_id("oai-amf", "memory", "2026-04-25T12:00:00"),
            _make_alert_id("oai-amf", "cpu", "2026-04-25T12:00:01"),
        }
        assert len(ids) == 4


class TestDefaultActions:
    def test_known_metric(self):
        actions = _default_actions("cpu_utilization", "high")
        assert "scale_out_replicas" in actions

    def test_unknown_metric(self):
        actions = _default_actions("ufo_signal", "high")
        assert actions  # non-empty
        assert "investigate" in actions

    def test_critical_adds_page_oncall(self):
        assert "page_oncall" in _default_actions("cpu_utilization", "critical")
        assert "page_oncall" not in _default_actions("cpu_utilization", "high")


# ──────────────────── Agent-level tests ──────────────────── #


def _base_state(metrics=None, **overrides):
    state = {
        "current_metrics": metrics or [],
        "anomaly_alerts": [],
        "messages": [],
        "current_agent": "",
        "phase": "post_deployment",
    }
    state.update(overrides)
    return state


def _range_series(values: list[float]) -> list[dict]:
    """Helper: wrap a flat value list in the prometheus-api-client shape."""
    return [{"values": [[float(i), str(v)] for i, v in enumerate(values)]}]


class TestAnomalyDetectorAgent:
    @patch("agents.anomaly_detector.LLMCore")
    @patch("agents.anomaly_detector.PrometheusClient")
    def test_point_outlier_detected(self, mock_prom_cls, mock_llm_cls):
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        # 30 nominal samples around 30, last sample (current_raw) is 95 → big z
        history = [30.0 + (i % 5) for i in range(35)] + [95.0]
        mock_prom.range_query.return_value = _range_series(history)
        mock_prom_cls.return_value = mock_prom

        # LLM returns an enhancement
        mock_llm_cls.return_value.invoke.return_value = {
            "suggested_cause": "AMF traffic spike",
            "suggested_actions": ["scale_out_replicas"],
            "description": "AMF CPU spike anomaly",
        }

        metrics = [{
            "vnf_name": "oai-amf",
            "metric_name": "cpu_utilization",
            "value": 95.0,
            "unit": "%",
            "threshold_status": "critical",
            "timestamp": "2026-04-25T12:00:00",
        }]
        update = anomaly_detector_agent(_base_state(metrics))

        assert update["current_agent"] == "anomaly_detector"
        assert "error" not in update
        alerts = update["anomaly_alerts"]
        # Should detect at least the point outlier (sustained may also fire)
        types = {a["type"] for a in alerts}
        assert "point_outlier" in types
        outlier = next(a for a in alerts if a["type"] == "point_outlier")
        assert outlier["affected_metrics"][0]["name"] == "cpu_utilization"
        # LLM-enhanced description used
        assert outlier["description"] == "AMF CPU spike anomaly"

    @patch("agents.anomaly_detector.LLMCore")
    @patch("agents.anomaly_detector.PrometheusClient")
    def test_sustained_overload(self, mock_prom_cls, mock_llm_cls):
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        # All samples above the critical threshold (85 for CPU)
        # No outlier (constant) → point_outlier should NOT fire (MAD=0)
        # Sustained should fire (10/10 above threshold)
        mock_prom.range_query.return_value = _range_series([90.0] * 10)
        mock_prom_cls.return_value = mock_prom
        mock_llm_cls.return_value.invoke.side_effect = Exception("skip llm")

        metrics = [{
            "vnf_name": "oai-amf",
            "metric_name": "cpu_utilization",
            "value": 90.0,
            "unit": "%",
            "threshold_status": "critical",
            "timestamp": "2026-04-25T12:00:00",
        }]
        update = anomaly_detector_agent(_base_state(metrics))

        types = {a["type"] for a in update["anomaly_alerts"]}
        assert "sustained_overload" in types
        sustained = next(
            a for a in update["anomaly_alerts"] if a["type"] == "sustained_overload"
        )
        assert sustained["affected_metrics"][0]["baseline"] == pytest.approx(85.0)

    @patch("agents.anomaly_detector.LLMCore")
    @patch("agents.anomaly_detector.PrometheusClient")
    def test_restart_spike(self, mock_prom_cls, mock_llm_cls):
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        # Past restart count = 1; with one restart per sample, current = 5
        mock_prom.range_query.return_value = _range_series([1.0, 2.0, 3.0, 4.0, 5.0])
        mock_prom_cls.return_value = mock_prom
        mock_llm_cls.return_value.invoke.side_effect = Exception("skip llm")

        metrics = [{
            "vnf_name": "oai-upf",
            "metric_name": "pod_restarts",
            "value": 5.0,
            "unit": "count",
            "threshold_status": "critical",
            "timestamp": "2026-04-25T12:00:00",
        }]
        update = anomaly_detector_agent(_base_state(metrics))

        types = {a["type"] for a in update["anomaly_alerts"]}
        assert "instability" in types
        instab = next(a for a in update["anomaly_alerts"] if a["type"] == "instability")
        # delta = 5 - 1 = 4 → high (delta < 5)
        assert instab["confidence"] == pytest.approx(0.85)

    @patch("agents.anomaly_detector.LLMCore")
    @patch("agents.anomaly_detector.PrometheusClient")
    def test_no_anomalies(self, mock_prom_cls, mock_llm_cls):
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        # Stable history, current value in band
        history = [30.0 + (i % 5) for i in range(35)] + [31.0]
        mock_prom.range_query.return_value = _range_series(history)
        mock_prom_cls.return_value = mock_prom
        mock_llm_cls.return_value.invoke.return_value = None

        metrics = [{
            "vnf_name": "oai-amf",
            "metric_name": "cpu_utilization",
            "value": 31.0,
            "unit": "%",
            "threshold_status": "normal",
            "timestamp": "2026-04-25T12:00:00",
        }]
        update = anomaly_detector_agent(_base_state(metrics))

        assert update["anomaly_alerts"] == []
        assert "no anomalies" in update["messages"][0]["content"].lower()

    @patch("agents.anomaly_detector.PrometheusClient")
    def test_prometheus_unreachable(self, mock_prom_cls):
        mock_prom = MagicMock()
        mock_prom.ping.return_value = False
        mock_prom.url = "http://mock:9090"
        mock_prom_cls.return_value = mock_prom

        metrics = [{
            "vnf_name": "oai-amf",
            "metric_name": "cpu_utilization",
            "value": 95.0,
            "unit": "%",
            "threshold_status": "critical",
            "timestamp": "2026-04-25T12:00:00",
        }]
        update = anomaly_detector_agent(_base_state(metrics))

        assert update["anomaly_alerts"] == []
        assert "error" in update
        assert "unreachable" in update["error"]

    def test_no_metrics_no_prometheus_call(self):
        # Should short-circuit — no Prometheus call needed
        update = anomaly_detector_agent(_base_state([]))
        assert update["anomaly_alerts"] == []
        assert update["current_agent"] == "anomaly_detector"
        assert "error" not in update

    @patch("agents.anomaly_detector.LLMCore")
    @patch("agents.anomaly_detector.PrometheusClient")
    def test_llm_failure_is_nonblocking(self, mock_prom_cls, mock_llm_cls):
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        history = [30.0 + (i % 5) for i in range(35)] + [95.0]
        mock_prom.range_query.return_value = _range_series(history)
        mock_prom_cls.return_value = mock_prom
        mock_llm_cls.return_value.invoke.side_effect = RuntimeError("LLM down")

        metrics = [{
            "vnf_name": "oai-amf",
            "metric_name": "cpu_utilization",
            "value": 95.0,
            "unit": "%",
            "threshold_status": "normal",
            "timestamp": "2026-04-25T12:00:00",
        }]
        update = anomaly_detector_agent(_base_state(metrics))

        assert "error" not in update
        assert any(a["type"] == "point_outlier" for a in update["anomaly_alerts"])
        # Default cause survives because LLM failed
        outlier = next(
            a for a in update["anomaly_alerts"] if a["type"] == "point_outlier"
        )
        assert "Statistical outlier" in outlier["suggested_cause"]

    @patch("agents.anomaly_detector.LLMCore")
    @patch("agents.anomaly_detector.PrometheusClient")
    def test_append_only_contract(self, mock_prom_cls, mock_llm_cls):
        """Agent must return only NEW alerts, not the cumulative list."""
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        history = [30.0 + (i % 5) for i in range(35)] + [95.0]
        mock_prom.range_query.return_value = _range_series(history)
        mock_prom_cls.return_value = mock_prom
        mock_llm_cls.return_value.invoke.return_value = None

        metrics = [{
            "vnf_name": "oai-amf",
            "metric_name": "cpu_utilization",
            "value": 95.0,
            "unit": "%",
            "threshold_status": "normal",
            "timestamp": "2026-04-25T12:00:00",
        }]
        prior = [{"alert_id": "old-1", "type": "instability"}]
        update = anomaly_detector_agent(_base_state(metrics, anomaly_alerts=prior))

        # The agent returns only newly minted alerts
        ids = [a["alert_id"] for a in update["anomaly_alerts"]]
        assert "old-1" not in ids

    @patch("agents.anomaly_detector.LLMCore")
    @patch("agents.anomaly_detector.PrometheusClient")
    def test_dedup_suppresses_repeat_alert(self, mock_prom_cls, mock_llm_cls):
        """Same (type, vnf, metric) within window must not be re-emitted."""
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        history = [30.0 + (i % 5) for i in range(35)] + [95.0]
        mock_prom.range_query.return_value = _range_series(history)
        mock_prom_cls.return_value = mock_prom
        mock_llm_cls.return_value.invoke.return_value = None

        metrics = [{
            "vnf_name": "oai-amf",
            "metric_name": "cpu_utilization",
            "value": 95.0,
            "unit": "%",
            "threshold_status": "normal",
            "timestamp": "2026-04-25T12:00:00",
        }]

        first = anomaly_detector_agent(_base_state(metrics))
        assert any(a["type"] == "point_outlier" for a in first["anomaly_alerts"])

        # Second cycle, same fault — must be suppressed
        second = anomaly_detector_agent(_base_state(metrics))
        assert second["anomaly_alerts"] == []
