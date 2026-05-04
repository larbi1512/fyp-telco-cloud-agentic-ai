"""
Unit tests for agents.fault_recovery.

All tests use mocks — no live cluster or Helm binary needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.fault_recovery import (
    _OWNED_ACTIONS,
    _execute_restart,
    _execute_rollback,
    _execute_config_change,
    _build_summary,
    fault_recovery_agent,
)


# ──────────────────── Fixtures ──────────────────── #


def _mock_k8s(
    deployments: list[dict] | None = None,
):
    """Return a MagicMock K8sClient with seeded data."""
    k8s = MagicMock()
    k8s.get_deployments.return_value = deployments if deployments is not None else [
        {"name": "oai-amf", "replicas": 1, "ready_replicas": 1, "available_replicas": 1},
        {"name": "oai-upf", "replicas": 2, "ready_replicas": 2, "available_replicas": 2},
    ]
    k8s.rollout_restart_deployment.return_value = {"status": "ok"}
    k8s.patch_configmap.return_value = {"status": "ok"}
    return k8s


def _mock_helm():
    """Return a MagicMock HelmManager."""
    helm = MagicMock()
    helm.rollback_release.return_value = {
        "status": "rolled_back",
        "release": "oai-amf",
        "namespace": "oai-5g",
        "revision": None,
        "output": "Rollback was a success",
    }
    return helm


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


# ──────────────────── Restart ──────────────────── #


class TestRestart:
    def test_basic_restart(self):
        k8s = _mock_k8s()
        action = {"type": "restart", "target": "oai-amf", "params": {}}
        result = _execute_restart(k8s, action, "oai-5g")

        assert result["status"] == "success"
        assert "oai-amf" in result["details"]
        k8s.rollout_restart_deployment.assert_called_once_with("oai-amf", namespace="oai-5g")

    def test_deployment_not_found(self):
        k8s = _mock_k8s()
        action = {"type": "restart", "target": "oai-ghost", "params": {}}
        result = _execute_restart(k8s, action, "oai-5g")

        assert result["status"] == "failed"
        assert "not found" in result["details"]

    def test_api_failure(self):
        k8s = _mock_k8s()
        k8s.rollout_restart_deployment.side_effect = RuntimeError("API timeout")
        action = {"type": "restart", "target": "oai-amf", "params": {}}
        result = _execute_restart(k8s, action, "oai-5g")

        assert result["status"] == "failed"
        assert "API timeout" in result["details"]


# ──────────────────── Rollback ──────────────────── #


class TestRollback:
    def test_basic_rollback(self):
        helm = _mock_helm()
        action = {"type": "rollback", "target": "oai-amf", "params": {}}
        result = _execute_rollback(helm, action, "oai-5g")

        assert result["status"] == "success"
        assert "previous revision" in result["details"]
        helm.rollback_release.assert_called_once_with(
            "oai-amf", revision=None, namespace="oai-5g"
        )

    def test_rollback_specific_revision(self):
        helm = _mock_helm()
        action = {"type": "rollback", "target": "oai-amf", "params": {"revision": 3}}
        result = _execute_rollback(helm, action, "oai-5g")

        assert result["status"] == "success"
        assert "revision 3" in result["details"]
        helm.rollback_release.assert_called_once_with(
            "oai-amf", revision=3, namespace="oai-5g"
        )

    def test_rollback_failure(self):
        helm = _mock_helm()
        helm.rollback_release.side_effect = RuntimeError("No revisions found")
        action = {"type": "rollback", "target": "oai-amf", "params": {}}
        result = _execute_rollback(helm, action, "oai-5g")

        assert result["status"] == "failed"
        assert "No revisions found" in result["details"]


# ──────────────────── Config Change ──────────────────── #


class TestConfigChange:
    def test_basic_config_change(self):
        k8s = _mock_k8s()
        action = {
            "type": "config_change",
            "target": "oai-amf",
            "params": {
                "config_map": "oai-amf-config",
                "updates": {"log_level": "debug"},
            },
        }
        result = _execute_config_change(k8s, action, "oai-5g")

        assert result["status"] == "success"
        assert "oai-amf-config" in result["details"]
        k8s.patch_configmap.assert_called_once_with(
            "oai-amf-config", {"log_level": "debug"}, namespace="oai-5g"
        )
        k8s.rollout_restart_deployment.assert_called_once_with("oai-amf", namespace="oai-5g")

    def test_missing_config_map(self):
        k8s = _mock_k8s()
        action = {
            "type": "config_change",
            "target": "oai-amf",
            "params": {"updates": {"log_level": "debug"}},
        }
        result = _execute_config_change(k8s, action, "oai-5g")

        assert result["status"] == "failed"
        assert "config_map" in result["details"]

    def test_missing_updates(self):
        k8s = _mock_k8s()
        action = {
            "type": "config_change",
            "target": "oai-amf",
            "params": {"config_map": "oai-amf-config"},
        }
        result = _execute_config_change(k8s, action, "oai-5g")

        assert result["status"] == "failed"
        assert "updates" in result["details"]

    def test_patch_failure(self):
        k8s = _mock_k8s()
        k8s.patch_configmap.side_effect = RuntimeError("ConfigMap not found")
        action = {
            "type": "config_change",
            "target": "oai-amf",
            "params": {
                "config_map": "oai-amf-config",
                "updates": {"log_level": "debug"},
            },
        }
        result = _execute_config_change(k8s, action, "oai-5g")

        assert result["status"] == "failed"
        assert "ConfigMap not found" in result["details"]

    def test_restart_failure_after_patch(self):
        """ConfigMap patched OK but restart fails → partial status."""
        k8s = _mock_k8s()
        k8s.rollout_restart_deployment.side_effect = RuntimeError("Restart failed")
        action = {
            "type": "config_change",
            "target": "oai-amf",
            "params": {
                "config_map": "oai-amf-config",
                "updates": {"log_level": "debug"},
            },
        }
        result = _execute_config_change(k8s, action, "oai-5g")

        assert result["status"] == "partial"
        assert "patched" in result["details"]
        assert "Restart failed" in result["details"]


# ──────────────────── Summary builder ──────────────────── #


class TestBuildSummary:
    def test_empty_results(self):
        summary = _build_summary([], {})
        assert "no recovery actions" in summary

    def test_with_results(self):
        results = [
            {"action_type": "restart", "target": "oai-amf",
             "status": "success", "details": "Restarted", "timestamp": "now"},
            {"action_type": "rollback", "target": "oai-upf",
             "status": "failed", "details": "No revision", "timestamp": "now"},
        ]
        verifications = {
            "oai-amf": {"replicas": 1, "ready_replicas": 1, "available_replicas": 1},
        }
        summary = _build_summary(results, verifications)

        assert "✓ 1" in summary
        assert "✗ 1" in summary
        assert "oai-amf" in summary
        assert "Post-check" in summary

    def test_partial_status_in_summary(self):
        results = [
            {"action_type": "config_change", "target": "oai-amf",
             "status": "partial", "details": "Patched but restart failed", "timestamp": "now"},
        ]
        summary = _build_summary(results, {})
        assert "◑ 1" in summary


# ──────────────────── Agent-level tests ──────────────────── #


class TestFaultRecoveryAgent:
    def test_no_plan_noop(self):
        update = fault_recovery_agent(_state())
        assert update["execution_results"] == []
        assert "skipped" in update["messages"][0]["content"].lower()

    def test_empty_plan_actions_noop(self):
        update = fault_recovery_agent(_state(remediation_plan=_plan([])))
        assert update["execution_results"] == []

    def test_non_owned_actions_skipped(self):
        plan = _plan([
            {"order": 1, "type": "horizontal_scale", "target": "oai-amf",
             "action": "scale_replicas", "params": {"delta": 1}},
            {"order": 2, "type": "vertical_scale", "target": "oai-amf",
             "action": "increase_memory", "params": {"factor": 1.5}},
        ])
        update = fault_recovery_agent(_state(remediation_plan=plan))
        assert update["execution_results"] == []
        assert "no recovery actions" in update["messages"][0]["content"].lower()

    @patch("agents.fault_recovery.time.sleep")
    @patch("agents.fault_recovery.K8sClient")
    def test_restart_execution(self, mock_k8s_cls, mock_sleep):
        mock_k8s_cls.return_value = _mock_k8s()

        plan = _plan([
            {"order": 1, "type": "restart", "target": "oai-amf",
             "action": "rollout_restart", "params": {}},
        ])
        update = fault_recovery_agent(_state(remediation_plan=plan))

        assert len(update["execution_results"]) == 1
        assert update["execution_results"][0]["status"] == "success"
        assert update["execution_results"][0]["action_type"] == "restart"
        assert update["current_agent"] == "fault_recovery"

    @patch("agents.fault_recovery.time.sleep")
    @patch("agents.fault_recovery.HelmManager")
    def test_rollback_execution(self, mock_helm_cls, mock_sleep):
        mock_helm_cls.return_value = _mock_helm()

        plan = _plan([
            {"order": 1, "type": "rollback", "target": "oai-amf",
             "action": "helm_rollback", "params": {}},
        ])
        update = fault_recovery_agent(_state(remediation_plan=plan))

        assert len(update["execution_results"]) == 1
        assert update["execution_results"][0]["status"] == "success"
        assert update["execution_results"][0]["action_type"] == "rollback"

    @patch("agents.fault_recovery.time.sleep")
    @patch("agents.fault_recovery.K8sClient")
    def test_config_change_execution(self, mock_k8s_cls, mock_sleep):
        mock_k8s_cls.return_value = _mock_k8s()

        plan = _plan([
            {"order": 1, "type": "config_change", "target": "oai-amf",
             "action": "edit_configmap",
             "params": {"config_map": "oai-amf-config", "updates": {"key": "val"}}},
        ])
        update = fault_recovery_agent(_state(remediation_plan=plan))

        assert len(update["execution_results"]) == 1
        assert update["execution_results"][0]["status"] == "success"

    @patch("agents.fault_recovery.time.sleep")
    @patch("agents.fault_recovery.HelmManager")
    @patch("agents.fault_recovery.K8sClient")
    def test_mixed_actions_filters_correctly(self, mock_k8s_cls, mock_helm_cls, mock_sleep):
        """Only restart/rollback/config_change are executed; scaling is skipped."""
        mock_k8s_cls.return_value = _mock_k8s()
        mock_helm_cls.return_value = _mock_helm()

        plan = _plan([
            {"order": 1, "type": "restart", "target": "oai-amf",
             "action": "rollout_restart", "params": {}},
            {"order": 2, "type": "horizontal_scale", "target": "oai-amf",
             "action": "scale_replicas", "params": {"delta": 1}},
            {"order": 3, "type": "rollback", "target": "oai-upf",
             "action": "helm_rollback", "params": {}},
        ])
        update = fault_recovery_agent(_state(remediation_plan=plan))

        # Only 2 recovery actions should be executed (horizontal_scale is skipped)
        assert len(update["execution_results"]) == 2
        types = [r["action_type"] for r in update["execution_results"]]
        assert "restart" in types
        assert "rollback" in types
        assert "horizontal_scale" not in types

    @patch("agents.fault_recovery.K8sClient")
    def test_k8s_init_failure(self, mock_k8s_cls):
        mock_k8s_cls.side_effect = RuntimeError("No kubeconfig")

        plan = _plan([
            {"order": 1, "type": "restart", "target": "oai-amf",
             "action": "rollout_restart", "params": {}},
        ])
        update = fault_recovery_agent(_state(remediation_plan=plan))

        assert update.get("error")
        assert all(r["status"] == "failed" for r in update["execution_results"])

    def test_owned_actions_constant(self):
        """Sanity: the agent only claims restart, rollback, config_change."""
        assert _OWNED_ACTIONS == {"restart", "rollback", "config_change"}
