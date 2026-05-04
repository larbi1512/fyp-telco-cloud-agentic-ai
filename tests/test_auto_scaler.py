"""
Unit tests for agents.auto_scaler.

All tests use mocks — no live cluster needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.auto_scaler import (
    _OWNED_ACTIONS,
    _MIN_REPLICAS,
    _MAX_REPLICAS,
    _execute_horizontal_scale,
    _execute_vertical_scale,
    _infer_container_name,
    _build_summary,
    auto_scaler_agent,
)


# ──────────────────── Fixtures ──────────────────── #


def _mock_k8s(
    deployments: list[dict] | None = None,
    pods: list[dict] | None = None,
):
    """Return a MagicMock K8sClient with seeded data."""
    k8s = MagicMock()
    k8s.get_deployments.return_value = deployments if deployments is not None else [
        {"name": "oai-amf", "replicas": 1, "ready_replicas": 1, "available_replicas": 1},
        {"name": "oai-upf", "replicas": 2, "ready_replicas": 2, "available_replicas": 2},
    ]
    k8s.get_pods.return_value = pods if pods is not None else [
        {
            "name": "oai-amf-55b9d4d44f-qxxs8",
            "namespace": "oai-5g",
            "phase": "Running",
            "node": "node-1",
            "containers": [
                {"name": "amf", "ready": True, "restart_count": 0, "image": "oai-amf:v2.1"},
            ],
            "creation_ts": "2026-04-29T10:00:00",
        },
        {
            "name": "oai-upf-77c8e6e55a-abc12",
            "namespace": "oai-5g",
            "phase": "Running",
            "node": "node-1",
            "containers": [
                {"name": "upf", "ready": True, "restart_count": 0, "image": "oai-upf:v2.1"},
            ],
            "creation_ts": "2026-04-29T10:00:00",
        },
    ]
    k8s.scale_deployment.return_value = {"status": "ok"}
    k8s.patch_deployment_resources.return_value = {"status": "ok"}
    return k8s


def _state(**overrides):
    """Minimal OrchestratorState dict for testing."""
    s = {
        "user_intent": "test",
        "topology": {"vnfs": [{"name": "oai-amf"}, {"name": "oai-upf"}]},
        "resource_allocation": None,
        "config_artifacts": [],
        "validation_report": None,
        "deployment_results": [],
        "current_metrics": [],
        "anomaly_alerts": [],
        "sla_status": [],
        "remediation_plan": None,
        "execution_results": [],
        "messages": [],
        "current_agent": "",
        "requires_approval": False,
        "user_approved": None,
        "error": None,
        "phase": "post_deployment",
    }
    s.update(overrides)
    return s


def _plan(actions):
    """Build a minimal RemediationPlan with the given action list."""
    return {
        "plan_id": "plan-test-001",
        "triggered_by": "test-trigger",
        "diagnosis": "Test diagnosis",
        "confidence": 0.8,
        "recommended_actions": actions,
        "expected_outcome": "Things improve",
        "rollback_plan": "Undo it",
        "validation_metric": "cpu_utilization",
    }


# ──────────────────── Container inference ──────────────────── #


class TestInferContainerName:
    def test_finds_main_container(self):
        k8s = _mock_k8s()
        result = _infer_container_name(k8s, "oai-amf", "oai-5g")
        assert result == "amf"

    def test_skips_sidecar(self):
        k8s = _mock_k8s(pods=[
            {
                "name": "oai-amf-55b9d4d44f-qxxs8",
                "namespace": "oai-5g",
                "phase": "Running",
                "node": "node-1",
                "containers": [
                    {"name": "istio-proxy", "ready": True, "restart_count": 0, "image": "istio:v1"},
                    {"name": "amf", "ready": True, "restart_count": 0, "image": "oai-amf:v2.1"},
                ],
                "creation_ts": "2026-04-29T10:00:00",
            },
        ])
        result = _infer_container_name(k8s, "oai-amf", "oai-5g")
        assert result == "amf"

    def test_fallback_to_deployment_name(self):
        k8s = _mock_k8s(pods=[])
        result = _infer_container_name(k8s, "oai-amf", "oai-5g")
        assert result == "oai-amf"


# ──────────────────── Horizontal scaling ──────────────────── #


class TestHorizontalScale:
    def test_basic_scale_up(self):
        k8s = _mock_k8s()
        action = {"type": "horizontal_scale", "target": "oai-amf", "params": {"delta": 2}}
        result = _execute_horizontal_scale(k8s, action, "oai-5g")

        assert result["status"] == "success"
        assert "1 → 3" in result["details"]
        k8s.scale_deployment.assert_called_once_with("oai-amf", 3, namespace="oai-5g")

    def test_scale_down(self):
        k8s = _mock_k8s()
        action = {"type": "horizontal_scale", "target": "oai-upf", "params": {"delta": -1}}
        result = _execute_horizontal_scale(k8s, action, "oai-5g")

        assert result["status"] == "success"
        assert "2 → 1" in result["details"]
        k8s.scale_deployment.assert_called_once_with("oai-upf", 1, namespace="oai-5g")

    def test_clamp_min(self):
        """Can't scale below _MIN_REPLICAS."""
        k8s = _mock_k8s()
        action = {"type": "horizontal_scale", "target": "oai-amf", "params": {"delta": -5}}
        result = _execute_horizontal_scale(k8s, action, "oai-5g")

        assert result["status"] == "success"
        # 1 + (-5) = -4, clamped to _MIN_REPLICAS (1) — no change
        assert "No change needed" in result["details"]

    def test_clamp_max(self):
        """Can't scale above _MAX_REPLICAS."""
        k8s = _mock_k8s(deployments=[
            {"name": "oai-upf", "replicas": 9, "ready_replicas": 9, "available_replicas": 9},
        ])
        action = {"type": "horizontal_scale", "target": "oai-upf", "params": {"delta": 5}}
        result = _execute_horizontal_scale(k8s, action, "oai-5g")

        assert result["status"] == "success"
        # 9 + 5 = 14, clamped to _MAX_REPLICAS (10)
        assert "→ 10" in result["details"]

    def test_deployment_not_found(self):
        k8s = _mock_k8s()
        action = {"type": "horizontal_scale", "target": "oai-ghost", "params": {"delta": 1}}
        result = _execute_horizontal_scale(k8s, action, "oai-5g")

        assert result["status"] == "failed"
        assert "not found" in result["details"]

    def test_k8s_api_failure(self):
        k8s = _mock_k8s()
        k8s.scale_deployment.side_effect = RuntimeError("API timeout")
        action = {"type": "horizontal_scale", "target": "oai-amf", "params": {"delta": 1}}
        result = _execute_horizontal_scale(k8s, action, "oai-5g")

        assert result["status"] == "failed"
        assert "API timeout" in result["details"]


# ──────────────────── Vertical scaling ──────────────────── #


class TestVerticalScale:
    @patch("agents.auto_scaler._infer_container_name", return_value="amf")
    def test_basic_vertical_scale(self, _mock_infer):
        k8s = _mock_k8s()
        action = {"type": "vertical_scale", "target": "oai-amf", "params": {"factor": 2.0}}
        result = _execute_vertical_scale(k8s, action, "oai-5g")

        assert result["status"] == "success"
        assert "factor 2.00" in result["details"]
        k8s.patch_deployment_resources.assert_called_once_with(
            name="oai-amf",
            container_name="amf",
            factor=2.0,
            namespace="oai-5g",
        )

    @patch("agents.auto_scaler._infer_container_name", return_value="amf")
    def test_factor_clamped_to_max(self, _mock_infer):
        k8s = _mock_k8s()
        action = {"type": "vertical_scale", "target": "oai-amf", "params": {"factor": 10.0}}
        result = _execute_vertical_scale(k8s, action, "oai-5g")

        assert result["status"] == "success"
        # factor clamped to 4.0
        k8s.patch_deployment_resources.assert_called_once()
        call_kwargs = k8s.patch_deployment_resources.call_args[1]
        assert call_kwargs["factor"] == 4.0

    @patch("agents.auto_scaler._infer_container_name", return_value="amf")
    def test_default_factor(self, _mock_infer):
        k8s = _mock_k8s()
        action = {"type": "vertical_scale", "target": "oai-amf", "params": {}}
        result = _execute_vertical_scale(k8s, action, "oai-5g")

        assert result["status"] == "success"
        call_kwargs = k8s.patch_deployment_resources.call_args[1]
        assert call_kwargs["factor"] == 1.5

    @patch("agents.auto_scaler._infer_container_name", return_value="amf")
    def test_patch_failure(self, _mock_infer):
        k8s = _mock_k8s()
        k8s.patch_deployment_resources.side_effect = RuntimeError("Quota exceeded")
        action = {"type": "vertical_scale", "target": "oai-amf", "params": {"factor": 2.0}}
        result = _execute_vertical_scale(k8s, action, "oai-5g")

        assert result["status"] == "failed"
        assert "Quota exceeded" in result["details"]


# ──────────────────── Summary builder ──────────────────── #


class TestBuildSummary:
    def test_empty_results(self):
        summary = _build_summary([], {})
        assert "no scaling actions" in summary

    def test_with_results(self):
        results = [
            {"action_type": "horizontal_scale", "target": "oai-amf",
             "status": "success", "details": "Scaled 1→2", "timestamp": "now"},
        ]
        verifications = {"oai-amf": {"replicas": 2, "ready_replicas": 2, "available_replicas": 2}}
        summary = _build_summary(results, verifications)

        assert "✓ 1" in summary
        assert "oai-amf" in summary
        assert "Post-check" in summary


# ──────────────────── Agent-level tests ──────────────────── #


class TestAutoScalerAgent:
    def test_no_plan_noop(self):
        update = auto_scaler_agent(_state())
        assert update["execution_results"] == []
        assert "skipped" in update["messages"][0]["content"].lower()

    def test_empty_plan_actions_noop(self):
        update = auto_scaler_agent(_state(remediation_plan=_plan([])))
        assert update["execution_results"] == []

    def test_non_owned_actions_skipped(self):
        plan = _plan([
            {"order": 1, "type": "restart", "target": "oai-amf", "action": "rollout_restart", "params": {}},
            {"order": 2, "type": "rollback", "target": "oai-amf", "action": "helm_rollback", "params": {}},
        ])
        update = auto_scaler_agent(_state(remediation_plan=plan))
        assert update["execution_results"] == []
        assert "no scaling actions" in update["messages"][0]["content"].lower()

    @patch("agents.auto_scaler.time.sleep")
    @patch("agents.auto_scaler.K8sClient")
    def test_horizontal_scale_execution(self, mock_k8s_cls, mock_sleep):
        mock_k8s_cls.return_value = _mock_k8s()

        plan = _plan([
            {"order": 1, "type": "horizontal_scale", "target": "oai-amf",
             "action": "scale_replicas", "params": {"delta": 1}},
        ])
        update = auto_scaler_agent(_state(remediation_plan=plan))

        assert len(update["execution_results"]) == 1
        assert update["execution_results"][0]["status"] == "success"
        assert update["execution_results"][0]["action_type"] == "horizontal_scale"
        assert update["current_agent"] == "auto_scaler"

    @patch("agents.auto_scaler.time.sleep")
    @patch("agents.auto_scaler.K8sClient")
    def test_vertical_scale_execution(self, mock_k8s_cls, mock_sleep):
        k8s = _mock_k8s()
        mock_k8s_cls.return_value = k8s

        plan = _plan([
            {"order": 1, "type": "vertical_scale", "target": "oai-amf",
             "action": "increase_memory_limit", "params": {"factor": 1.5}},
        ])
        update = auto_scaler_agent(_state(remediation_plan=plan))

        assert len(update["execution_results"]) == 1
        assert update["execution_results"][0]["status"] == "success"
        assert update["execution_results"][0]["action_type"] == "vertical_scale"

    @patch("agents.auto_scaler.time.sleep")
    @patch("agents.auto_scaler.K8sClient")
    def test_mixed_actions_filters_correctly(self, mock_k8s_cls, mock_sleep):
        """Only horizontal_scale and vertical_scale are executed; restart is skipped."""
        mock_k8s_cls.return_value = _mock_k8s()

        plan = _plan([
            {"order": 1, "type": "horizontal_scale", "target": "oai-amf",
             "action": "scale_replicas", "params": {"delta": 1}},
            {"order": 2, "type": "restart", "target": "oai-amf",
             "action": "rollout_restart", "params": {}},
            {"order": 3, "type": "vertical_scale", "target": "oai-upf",
             "action": "increase_memory_limit", "params": {"factor": 2.0}},
        ])
        update = auto_scaler_agent(_state(remediation_plan=plan))

        # Only 2 scaling actions should be executed (restart is skipped)
        assert len(update["execution_results"]) == 2
        types = [r["action_type"] for r in update["execution_results"]]
        assert "horizontal_scale" in types
        assert "vertical_scale" in types
        assert "restart" not in types

    @patch("agents.auto_scaler.K8sClient")
    def test_k8s_init_failure(self, mock_k8s_cls):
        mock_k8s_cls.side_effect = RuntimeError("No kubeconfig")

        plan = _plan([
            {"order": 1, "type": "horizontal_scale", "target": "oai-amf",
             "action": "scale_replicas", "params": {"delta": 1}},
        ])
        update = auto_scaler_agent(_state(remediation_plan=plan))

        assert update.get("error")
        assert all(r["status"] == "failed" for r in update["execution_results"])

    def test_owned_actions_constant(self):
        """Sanity: the agent only claims horizontal_scale and vertical_scale."""
        assert _OWNED_ACTIONS == {"horizontal_scale", "vertical_scale"}
