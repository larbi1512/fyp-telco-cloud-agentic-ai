"""
Unit tests for agents.kpi_monitor.

All tests use mocks — no live cluster or Prometheus required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.kpi_monitor import (
    _fallback_vnf_from_pod,
    _parse_k8s_resource,
    _pod_to_vnf,
    _threshold_status,
    kpi_monitor_agent,
)


# ──────────────────── Helper unit tests ──────────────────── #


class TestParseK8sResource:
    def test_millicores(self):
        assert _parse_k8s_resource("100m") == pytest.approx(0.1)
        assert _parse_k8s_resource("500m") == pytest.approx(0.5)

    def test_bare_number(self):
        assert _parse_k8s_resource("1.5") == pytest.approx(1.5)
        assert _parse_k8s_resource("2") == pytest.approx(2.0)

    def test_binary_memory(self):
        assert _parse_k8s_resource("256Mi") == pytest.approx(256 * 1024**2)
        assert _parse_k8s_resource("2Gi") == pytest.approx(2 * 1024**3)
        assert _parse_k8s_resource("1Ki") == pytest.approx(1024)

    def test_decimal_memory(self):
        assert _parse_k8s_resource("1G") == pytest.approx(10**9)
        assert _parse_k8s_resource("500M") == pytest.approx(500 * 10**6)

    def test_numeric_passthrough(self):
        assert _parse_k8s_resource(0.5) == 0.5
        assert _parse_k8s_resource(1024) == 1024.0

    def test_empty(self):
        assert _parse_k8s_resource("") == 0.0


class TestPodToVnf:
    def test_longest_prefix_match(self):
        vnfs = ["oai-amf", "oai-smf", "oai-upf"]
        assert _pod_to_vnf("oai-amf-55b9d4d44f-qxxs8", vnfs) == "oai-amf"
        assert _pod_to_vnf("oai-upf-deadbeef-xyz12", vnfs) == "oai-upf"

    def test_no_match(self):
        assert _pod_to_vnf("kube-proxy-abc", ["oai-amf", "oai-smf"]) is None

    def test_prefers_longer_match(self):
        vnfs = ["oai", "oai-amf"]
        assert _pod_to_vnf("oai-amf-xyz", vnfs) == "oai-amf"


class TestFallbackVnfFromPod:
    def test_strips_replica_suffixes(self):
        assert _fallback_vnf_from_pod("oai-amf-55b9d4d44f-qxxs8") == "oai-amf"

    def test_preserves_base_name(self):
        assert _fallback_vnf_from_pod("oai-nrf") == "oai-nrf"


class TestThresholdStatus:
    def test_levels(self):
        assert _threshold_status(50, 70, 85) == "normal"
        assert _threshold_status(75, 70, 85) == "warning"
        assert _threshold_status(90, 70, 85) == "critical"
        assert _threshold_status(70, 70, 85) == "warning"   # boundary
        assert _threshold_status(85, 70, 85) == "critical"  # boundary


# ──────────────────── Agent-level tests ──────────────────── #


def _base_state(**overrides):
    state = {
        "topology": None,
        "resource_allocation": None,
        "current_metrics": [],
        "messages": [],
        "current_agent": "",
        "phase": "post_deployment",
    }
    state.update(overrides)
    return state


class TestKpiMonitorAgent:
    @patch("agents.kpi_monitor.LLMCore")
    @patch("agents.kpi_monitor.PrometheusClient")
    def test_happy_path_with_topology(self, mock_prom_cls, mock_llm_cls):
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        mock_prom.get_cpu_usage.return_value = [
            {"pod": "oai-amf-xxx", "container": "amf", "cpu_cores": 0.9},
        ]
        mock_prom.get_memory_usage.return_value = [
            {"pod": "oai-amf-xxx", "container": "amf", "memory_bytes": 500 * 1024**2},
        ]
        mock_prom.get_network_receive_bytes.return_value = [
            {"pod": "oai-amf-xxx", "rx_bytes_per_sec": 1_000_000},
        ]
        mock_prom.get_pod_restart_count.return_value = [
            {"pod": "oai-amf-xxx", "restarts": 0},
        ]
        mock_prom.get_amf_registration_rate.return_value = []
        mock_prom.get_upf_session_count.return_value = []
        mock_prom_cls.return_value = mock_prom

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = {
            "summary": "AMF hot.",
            "health_status": "degraded",
            "metric_correlations": [],
            "concerns": ["CPU at 90%"],
            "recommended_checks": [],
        }
        mock_llm_cls.return_value = mock_llm

        state = _base_state(
            resource_allocation={
                "topology_id": "t1",
                "vnfs": [
                    {
                        "name": "oai-amf",
                        "resources": {"limits": {"cpu": "1", "memory": "1Gi"}},
                    }
                ],
            }
        )
        update = kpi_monitor_agent(state)

        assert update["current_agent"] == "kpi_monitor"
        assert update["phase"] == "post_deployment"
        assert "error" not in update

        metrics = update["current_metrics"]
        cpu = next(m for m in metrics if m["metric_name"] == "cpu_utilization")
        assert cpu["vnf_name"] == "oai-amf"
        assert cpu["value"] == pytest.approx(90.0)
        assert cpu["threshold_status"] == "critical"

        mem = next(m for m in metrics if m["metric_name"] == "memory_utilization")
        assert mem["threshold_status"] == "normal"  # 500Mi / 1Gi ≈ 48.8%

        restarts = next(m for m in metrics if m["metric_name"] == "pod_restarts")
        assert restarts["value"] == 0.0
        assert restarts["threshold_status"] == "normal"

        assert "**Health status:** degraded" in update["messages"][0]["content"]

    @patch("agents.kpi_monitor.LLMCore")
    @patch("agents.kpi_monitor.PrometheusClient")
    def test_namespace_fallback(self, mock_prom_cls, mock_llm_cls):
        """No topology → falls back to VNF profiles YAML for limits."""
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        # oai-amf profile max is cpu=2000m (2 cores), memory=2Gi
        mock_prom.get_cpu_usage.return_value = [
            {"pod": "oai-amf-xxx", "container": "amf", "cpu_cores": 0.1},
        ]
        mock_prom.get_memory_usage.return_value = [
            {"pod": "oai-amf-xxx", "container": "amf", "memory_bytes": 100 * 1024**2},
        ]
        mock_prom.get_network_receive_bytes.return_value = []
        mock_prom.get_pod_restart_count.return_value = []
        mock_prom.get_amf_registration_rate.return_value = []
        mock_prom.get_upf_session_count.return_value = []
        mock_prom_cls.return_value = mock_prom

        mock_llm_cls.return_value.invoke.side_effect = Exception("LLM skipped")

        update = kpi_monitor_agent(_base_state())

        assert "error" not in update
        cpu = next(m for m in update["current_metrics"] if m["metric_name"] == "cpu_utilization")
        assert cpu["vnf_name"] == "oai-amf"
        # 0.1 / 2.0 = 5%
        assert cpu["value"] == pytest.approx(5.0)
        assert cpu["threshold_status"] == "normal"

    @patch("agents.kpi_monitor.PrometheusClient")
    def test_prometheus_unreachable(self, mock_prom_cls):
        mock_prom = MagicMock()
        mock_prom.ping.return_value = False
        mock_prom.url = "http://mock:9090"
        mock_prom_cls.return_value = mock_prom

        update = kpi_monitor_agent(_base_state())

        assert update["current_agent"] == "kpi_monitor"
        assert update["current_metrics"] == []
        assert "error" in update
        assert "unreachable" in update["error"]

    @patch("agents.kpi_monitor.LLMCore")
    @patch("agents.kpi_monitor.PrometheusClient")
    def test_llm_failure_is_nonblocking(self, mock_prom_cls, mock_llm_cls):
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        mock_prom.get_cpu_usage.return_value = [
            {"pod": "oai-amf-xxx", "container": "amf", "cpu_cores": 0.1},
        ]
        mock_prom.get_memory_usage.return_value = []
        mock_prom.get_network_receive_bytes.return_value = []
        mock_prom.get_pod_restart_count.return_value = []
        mock_prom.get_amf_registration_rate.return_value = []
        mock_prom.get_upf_session_count.return_value = []
        mock_prom_cls.return_value = mock_prom

        mock_llm_cls.return_value.invoke.side_effect = RuntimeError("LLM down")

        update = kpi_monitor_agent(_base_state())

        assert "error" not in update
        assert len(update["current_metrics"]) == 1
        assert update["current_metrics"][0]["metric_name"] == "cpu_utilization"
        # Deterministic summary still produced
        assert "KPI Monitor" in update["messages"][0]["content"]

    @patch("agents.kpi_monitor.LLMCore")
    @patch("agents.kpi_monitor.PrometheusClient")
    def test_application_layer_metrics_emitted(self, mock_prom_cls, mock_llm_cls):
        """AMF registration rate and UPF session count are surfaced as MetricEvents."""
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        mock_prom.get_cpu_usage.return_value = []
        mock_prom.get_memory_usage.return_value = []
        mock_prom.get_network_receive_bytes.return_value = []
        mock_prom.get_pod_restart_count.return_value = []
        mock_prom.get_amf_registration_rate.return_value = [
            {"vnf": "oai-amf", "registrations_per_sec": 12.5},
        ]
        mock_prom.get_upf_session_count.return_value = [
            {"vnf": "oai-upf", "sessions": 47},
        ]
        mock_prom_cls.return_value = mock_prom

        mock_llm_cls.return_value.invoke.side_effect = Exception("LLM skipped")

        update = kpi_monitor_agent(_base_state())

        assert "error" not in update
        metrics = update["current_metrics"]

        amf = next(m for m in metrics if m["metric_name"] == "amf_registration_rate")
        assert amf["vnf_name"] == "oai-amf"
        assert amf["value"] == pytest.approx(12.5)
        assert amf["unit"] == "reg/sec"
        assert amf["threshold_status"] == "normal"

        upf = next(m for m in metrics if m["metric_name"] == "upf_session_count")
        assert upf["vnf_name"] == "oai-upf"
        assert upf["value"] == 47.0
        assert upf["unit"] == "count"

    @patch("agents.kpi_monitor.LLMCore")
    @patch("agents.kpi_monitor.PrometheusClient")
    def test_application_layer_absent_is_graceful(self, mock_prom_cls, mock_llm_cls):
        """No OAI exporters → no application-layer events, no errors, no missing infra events."""
        mock_prom = MagicMock()
        mock_prom.ping.return_value = True
        mock_prom.url = "http://mock:9090"
        mock_prom.get_cpu_usage.return_value = [
            {"pod": "oai-amf-xxx", "container": "amf", "cpu_cores": 0.1},
        ]
        mock_prom.get_memory_usage.return_value = []
        mock_prom.get_network_receive_bytes.return_value = []
        mock_prom.get_pod_restart_count.return_value = []
        # Exporters absent — methods raise, agent must continue.
        mock_prom.get_amf_registration_rate.side_effect = RuntimeError("metric not found")
        mock_prom.get_upf_session_count.side_effect = RuntimeError("metric not found")
        mock_prom_cls.return_value = mock_prom

        mock_llm_cls.return_value.invoke.side_effect = Exception("LLM skipped")

        update = kpi_monitor_agent(_base_state())

        assert "error" not in update
        # Infra metric still emitted; application-layer events absent.
        names = {m["metric_name"] for m in update["current_metrics"]}
        assert "cpu_utilization" in names
        assert "amf_registration_rate" not in names
        assert "upf_session_count" not in names
