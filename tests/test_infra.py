"""
Unit tests for infra/ clients.

All tests use mocks — no live cluster or Prometheus required.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ────────────────────────────────────────────────────────────────────── #
#  K8sClient tests                                                       #
# ────────────────────────────────────────────────────────────────────── #


class TestK8sClient:
    """Tests for infra.k8s_client.K8sClient."""

    @patch("infra.k8s_client.config")
    @patch("infra.k8s_client.client")
    def _make_client(self, mock_client, mock_config):
        from infra.k8s_client import K8sClient

        mock_config.load_kube_config = MagicMock()
        mock_client.CoreV1Api = MagicMock
        mock_client.AppsV1Api = MagicMock
        return K8sClient(kubeconfig_path="/tmp/fake.yaml", default_namespace="test-ns")

    def test_init(self):
        k = self._make_client()
        assert k.default_ns == "test-ns"
        assert k.kubeconfig == "/tmp/fake.yaml"

    @patch("infra.k8s_client.config")
    @patch("infra.k8s_client.client")
    def test_get_pods_returns_list(self, mock_client_mod, mock_config):
        from infra.k8s_client import K8sClient

        mock_config.load_kube_config = MagicMock()

        # Build a fake pod response
        fake_container_status = SimpleNamespace(
            name="amf",
            ready=True,
            restart_count=0,
            image="oai-amf:v2.1.0",
        )
        fake_pod = SimpleNamespace(
            metadata=SimpleNamespace(
                name="oai-amf-0",
                namespace="free5gc",
                creation_timestamp=None,
            ),
            status=SimpleNamespace(
                phase="Running",
                container_statuses=[fake_container_status],
            ),
            spec=SimpleNamespace(node_name="worker-1"),
        )
        fake_resp = SimpleNamespace(items=[fake_pod])

        mock_core = MagicMock()
        mock_core.list_namespaced_pod.return_value = fake_resp
        mock_client_mod.CoreV1Api.return_value = mock_core
        mock_client_mod.AppsV1Api.return_value = MagicMock()

        k = K8sClient(kubeconfig_path="/tmp/fake.yaml", default_namespace="free5gc")
        pods = k.get_pods()

        assert len(pods) == 1
        assert pods[0]["name"] == "oai-amf-0"
        assert pods[0]["phase"] == "Running"
        assert pods[0]["containers"][0]["ready"] is True

    @patch("infra.k8s_client.config")
    @patch("infra.k8s_client.client")
    def test_get_deployments(self, mock_client_mod, mock_config):
        from infra.k8s_client import K8sClient

        mock_config.load_kube_config = MagicMock()

        fake_dep = SimpleNamespace(
            metadata=SimpleNamespace(name="oai-amf"),
            spec=SimpleNamespace(replicas=1),
            status=SimpleNamespace(ready_replicas=1, available_replicas=1),
        )
        mock_apps = MagicMock()
        mock_apps.list_namespaced_deployment.return_value = SimpleNamespace(
            items=[fake_dep]
        )
        mock_client_mod.CoreV1Api.return_value = MagicMock()
        mock_client_mod.AppsV1Api.return_value = mock_apps

        k = K8sClient(kubeconfig_path="/tmp/fake.yaml")
        deps = k.get_deployments()
        assert len(deps) == 1
        assert deps[0]["name"] == "oai-amf"
        assert deps[0]["replicas"] == 1

    @patch("infra.k8s_client.config")
    @patch("infra.k8s_client.client")
    def test_ping_success(self, mock_client_mod, mock_config):
        from infra.k8s_client import K8sClient

        mock_config.load_kube_config = MagicMock()
        mock_core = MagicMock()
        mock_core.list_namespace.return_value = SimpleNamespace(items=[])
        mock_client_mod.CoreV1Api.return_value = mock_core
        mock_client_mod.AppsV1Api.return_value = MagicMock()

        k = K8sClient(kubeconfig_path="/tmp/fake.yaml")
        assert k.ping() is True

    @patch("infra.k8s_client.config")
    @patch("infra.k8s_client.client")
    def test_ping_failure(self, mock_client_mod, mock_config):
        from infra.k8s_client import K8sClient

        mock_config.load_kube_config = MagicMock()
        mock_core = MagicMock()
        mock_core.list_namespace.side_effect = Exception("unreachable")
        mock_client_mod.CoreV1Api.return_value = mock_core
        mock_client_mod.AppsV1Api.return_value = MagicMock()

        k = K8sClient(kubeconfig_path="/tmp/fake.yaml")
        assert k.ping() is False


# ────────────────────────────────────────────────────────────────────── #
#  HelmManager tests                                                      #
# ────────────────────────────────────────────────────────────────────── #


class TestHelmManager:
    """Tests for infra.helm_manager.HelmManager."""

    def _make_manager(self):
        from infra.helm_manager import HelmManager

        return HelmManager(kubeconfig_path="/tmp/fake.yaml", default_namespace="test-ns")

    @patch("infra.helm_manager.subprocess.run")
    def test_install_chart(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="NAME: oai-amf\n", stderr=""
        )
        mgr = self._make_manager()
        result = mgr.install_chart("oai-amf", "./charts/oai-amf")
        assert result["status"] == "installed"
        assert result["release"] == "oai-amf"

    @patch("infra.helm_manager.subprocess.run")
    def test_list_releases(self, mock_run):
        releases_json = json.dumps([{"name": "oai-amf", "status": "deployed"}])
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=releases_json, stderr=""
        )
        mgr = self._make_manager()
        releases = mgr.list_releases()
        assert len(releases) == 1
        assert releases[0]["name"] == "oai-amf"

    @patch("infra.helm_manager.subprocess.run")
    def test_rollback_release(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Rollback was a success!", stderr=""
        )
        mgr = self._make_manager()
        result = mgr.rollback_release("oai-amf", revision=1)
        assert result["status"] == "rolled_back"
        assert result["revision"] == 1

    @patch("infra.helm_manager.subprocess.run")
    def test_install_failure_raises(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error: chart not found"
        )
        mgr = self._make_manager()
        with pytest.raises(subprocess.CalledProcessError):
            mgr.install_chart("bad-release", "./nonexistent")


# ────────────────────────────────────────────────────────────────────── #
#  PrometheusClient tests                                                  #
# ────────────────────────────────────────────────────────────────────── #


class TestPrometheusClient:
    """Tests for infra.prometheus_client.PrometheusClient."""

    @patch("infra.prometheus_client.PrometheusConnect")
    def _make_client(self, mock_prom_cls):
        from infra.prometheus_client import PrometheusClient

        mock_instance = MagicMock()
        mock_prom_cls.return_value = mock_instance
        client = PrometheusClient(prometheus_url="http://fake:9090")
        return client, mock_instance

    def test_ping_success(self):
        client, mock_prom = self._make_client()
        mock_prom.check_prometheus_connection.return_value = True
        assert client.ping() is True

    def test_ping_failure(self):
        client, mock_prom = self._make_client()
        mock_prom.check_prometheus_connection.side_effect = Exception("down")
        assert client.ping() is False

    def test_get_cpu_usage(self):
        client, mock_prom = self._make_client()
        mock_prom.custom_query.return_value = [
            {
                "metric": {"pod": "oai-amf-0", "container": "amf"},
                "value": [1700000000, "0.05"],
            }
        ]
        results = client.get_cpu_usage(namespace="free5gc")
        assert len(results) == 1
        assert results[0]["pod"] == "oai-amf-0"
        assert results[0]["cpu_cores"] == pytest.approx(0.05)

    def test_get_memory_usage(self):
        client, mock_prom = self._make_client()
        mock_prom.custom_query.return_value = [
            {
                "metric": {"pod": "oai-upf-0", "container": "upf"},
                "value": [1700000000, "134217728"],
            }
        ]
        results = client.get_memory_usage(namespace="free5gc")
        assert len(results) == 1
        assert results[0]["memory_bytes"] == pytest.approx(134217728)


# ────────────────────────────────────────────────────────────────────── #
#  GrafanaClient tests                                                     #
# ────────────────────────────────────────────────────────────────────── #


class TestGrafanaClient:
    """Tests for infra.grafana_client.GrafanaClient."""

    @patch("infra.grafana_client.requests.Session")
    def _make_client(self, mock_session_cls):
        from infra.grafana_client import GrafanaClient

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        client = GrafanaClient(grafana_url="http://fake:3000", api_key="test-key")
        return client, mock_session

    def test_ping_success(self):
        client, mock_session = self._make_client()
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_session.get.return_value = mock_resp
        assert client.ping() is True

    def test_ping_failure(self):
        client, mock_session = self._make_client()
        mock_session.get.side_effect = Exception("unreachable")
        assert client.ping() is False

    def test_list_dashboards(self):
        client, mock_session = self._make_client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"uid": "abc123", "title": "5G Core", "url": "/d/abc123"},
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp
        dashboards = client.list_dashboards()
        assert len(dashboards) == 1
        assert dashboards[0]["title"] == "5G Core"

    def test_add_annotation(self):
        client, mock_session = self._make_client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 42, "message": "Annotation added"}
        mock_resp.raise_for_status = MagicMock()
        mock_session.post.return_value = mock_resp
        result = client.add_annotation("Scaled UPF to 3 replicas", tags=["scaling"])
        assert result["id"] == 42
