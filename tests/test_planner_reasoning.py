"""
Unit tests for agents.planner_reasoning.

All tests use mocks — no live cluster, no real LLM.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.planner_reasoning import (
    ACTION_TYPES,
    _heuristic_action,
    _select_trigger,
    _validate_plan,
    planner_reasoning_agent,
)


# ──────────────────── Helper unit tests ──────────────────── #


class TestSelectTrigger:
    def test_none_when_empty(self):
        rank, payload, kind = _select_trigger([], [])
        assert rank == 0
        assert payload is None
        assert kind == ""

    def test_sla_breach_dominates_high_anomaly(self):
        alerts = [{"alert_id": "a1", "type": "point_outlier", "confidence": 0.95}]
        slas = [{"rule": "service_availability", "status": "breach"}]
        rank, payload, kind = _select_trigger(alerts, slas)
        assert kind == "sla"
        assert payload["rule"] == "service_availability"

    def test_high_anomaly_beats_sla_warning(self):
        alerts = [{"alert_id": "a1", "type": "point_outlier", "confidence": 0.85}]
        slas = [{"rule": "e2e_latency", "status": "warning"}]
        rank, payload, kind = _select_trigger(alerts, slas)
        assert kind == "anomaly"
        assert payload["alert_id"] == "a1"

    def test_compliant_sla_ignored(self):
        slas = [{"rule": "x", "status": "compliant"}]
        rank, _, _ = _select_trigger([], slas)
        assert rank == 0


class TestValidatePlan:
    def test_rejects_non_dict(self):
        plan, issues = _validate_plan("not a plan", ["oai-amf"])  # type: ignore[arg-type]
        assert plan is None
        assert issues

    def test_rejects_empty_actions(self):
        plan, issues = _validate_plan({"recommended_actions": []}, ["oai-amf"])
        assert plan is None

    def test_rejects_unknown_action_type(self):
        bad = {
            "recommended_actions": [
                {"type": "summon_demon", "target": "oai-amf"},
            ]
        }
        plan, issues = _validate_plan(bad, ["oai-amf"])
        assert plan is None
        assert any("not in whitelist" in i for i in issues)

    def test_filters_invalid_target(self):
        bad = {
            "recommended_actions": [
                {"type": "horizontal_scale", "target": "oai-ghost"},
                {"type": "restart", "target": "oai-amf"},
            ]
        }
        plan, _ = _validate_plan(bad, ["oai-amf"])
        assert plan is not None
        assert len(plan["recommended_actions"]) == 1
        assert plan["recommended_actions"][0]["target"] == "oai-amf"

    def test_clamps_confidence(self):
        good = {
            "confidence": 5.0,
            "recommended_actions": [
                {"type": "restart", "target": "oai-amf", "action": "rollout_restart"},
            ],
        }
        plan, _ = _validate_plan(good, ["oai-amf"])
        assert plan["confidence"] == 1.0

    def test_accepts_minimal_valid_plan(self):
        good = {
            "diagnosis": "AMF overloaded",
            "recommended_actions": [
                {"type": "horizontal_scale", "target": "oai-amf",
                 "action": "scale_replicas", "params": {"delta": 1}},
            ],
        }
        plan, issues = _validate_plan(good, ["oai-amf"])
        assert plan is not None
        assert plan["diagnosis"] == "AMF overloaded"
        assert plan["recommended_actions"][0]["type"] == "horizontal_scale"


class TestHeuristicAction:
    def test_instability_maps_to_rollback(self):
        payload = {
            "type": "instability",
            "affected_metrics": [{"name": "pod_restarts"}],
            "vnf_name": "oai-amf",
        }
        action = _heuristic_action("anomaly", payload, ["oai-amf"])
        assert action["type"] == "rollback"

    def test_memory_overload_maps_to_vertical(self):
        payload = {
            "type": "sustained_overload",
            "affected_metrics": [{"name": "memory_utilization"}],
            "vnf_name": "oai-smf",
        }
        action = _heuristic_action("anomaly", payload, ["oai-smf"])
        assert action["type"] == "vertical_scale"

    def test_throughput_sla_maps_to_upf_scale(self):
        payload = {"rule": "upf_throughput", "status": "breach"}
        action = _heuristic_action("sla", payload, ["oai-amf", "oai-upf"])
        assert action["type"] == "horizontal_scale"
        assert action["target"] == "oai-upf"

    def test_availability_maps_to_restart(self):
        payload = {"rule": "service_availability", "status": "breach"}
        action = _heuristic_action("sla", payload, ["oai-amf", "oai-smf"])
        assert action["type"] == "restart"


# ──────────────────── Agent-level tests ──────────────────── #


def _state(**overrides):
    s = {
        "current_metrics": [],
        "anomaly_alerts": [],
        "sla_status": [],
        "topology": {"vnfs": [{"name": "oai-amf"}, {"name": "oai-upf"}]},
        "resource_allocation": None,
        "execution_results": [],
        "messages": [],
        "current_agent": "",
        "phase": "post_deployment",
    }
    s.update(overrides)
    return s


class TestPlannerAgent:
    def test_no_trigger_no_plan(self):
        update = planner_reasoning_agent(_state())
        assert update["remediation_plan"] is None
        assert "no remediation needed" in update["messages"][0]["content"].lower()

    @patch("agents.planner_reasoning.LLMCore")
    def test_llm_plan_accepted(self, mock_llm_cls):
        mock_llm_cls.return_value.invoke.return_value = {
            "diagnosis": "AMF CPU saturation",
            "confidence": 0.8,
            "recommended_actions": [
                {"type": "horizontal_scale", "target": "oai-amf",
                 "action": "scale_replicas", "params": {"delta": 2}},
            ],
            "expected_outcome": "AMF CPU returns to baseline",
            "rollback_plan": "Scale back to original replicas",
            "validation_metric": "cpu_utilization",
        }

        alerts = [{
            "alert_id": "alert-1",
            "type": "sustained_overload",
            "confidence": 0.9,
            "vnf_name": "oai-amf",
            "affected_metrics": [{"name": "cpu_utilization", "current": 92, "baseline": 85, "unit": "%"}],
        }]
        update = planner_reasoning_agent(_state(anomaly_alerts=alerts))

        plan = update["remediation_plan"]
        assert plan is not None
        assert plan["diagnosis"] == "AMF CPU saturation"
        assert plan["confidence"] == 0.8
        assert plan["recommended_actions"][0]["type"] == "horizontal_scale"
        assert plan["recommended_actions"][0]["target"] == "oai-amf"
        assert plan["plan_id"].startswith("plan-")
        assert plan["triggered_by"] == "alert-1"
        assert "(llm)" in update["messages"][0]["content"]

    @patch("agents.planner_reasoning.LLMCore")
    def test_llm_failure_falls_back_to_heuristic(self, mock_llm_cls):
        mock_llm_cls.return_value.invoke.side_effect = RuntimeError("LLM down")

        alerts = [{
            "alert_id": "alert-2",
            "type": "instability",
            "confidence": 0.85,
            "vnf_name": "oai-amf",
            "affected_metrics": [{"name": "pod_restarts"}],
        }]
        update = planner_reasoning_agent(_state(anomaly_alerts=alerts))

        plan = update["remediation_plan"]
        assert plan is not None
        # Heuristic for instability is rollback
        assert plan["recommended_actions"][0]["type"] == "rollback"
        assert plan["recommended_actions"][0]["target"] == "oai-amf"
        assert "(heuristic)" in update["messages"][0]["content"]
        assert plan["plan_id"].startswith("plan-")

    @patch("agents.planner_reasoning.LLMCore")
    def test_invalid_llm_plan_falls_back(self, mock_llm_cls):
        # Invalid: action type not in whitelist + no valid actions
        mock_llm_cls.return_value.invoke.return_value = {
            "recommended_actions": [
                {"type": "summon_demon", "target": "oai-amf"},
            ]
        }
        alerts = [{
            "alert_id": "alert-3",
            "type": "point_outlier",
            "confidence": 0.95,
            "vnf_name": "oai-upf",
            "affected_metrics": [{"name": "cpu_utilization"}],
        }]
        update = planner_reasoning_agent(_state(anomaly_alerts=alerts))
        plan = update["remediation_plan"]
        assert plan is not None
        # Heuristic for cpu point outlier is horizontal_scale
        assert plan["recommended_actions"][0]["type"] == "horizontal_scale"
        assert "(heuristic)" in update["messages"][0]["content"]

    @patch("agents.planner_reasoning.LLMCore")
    def test_sla_breach_takes_precedence(self, mock_llm_cls):
        mock_llm_cls.return_value.invoke.return_value = {
            "diagnosis": "UPF throughput breach",
            "confidence": 0.7,
            "recommended_actions": [
                {"type": "horizontal_scale", "target": "oai-upf",
                 "action": "scale_replicas", "params": {"delta": 1}},
            ],
        }
        # Both an anomaly (high) and an SLA breach — breach must win
        alerts = [{
            "alert_id": "alert-4",
            "type": "point_outlier",
            "confidence": 0.95,
            "vnf_name": "oai-amf",
        }]
        slas = [{"rule": "upf_throughput", "status": "breach",
                 "current_value": 800, "threshold": 1000}]
        update = planner_reasoning_agent(_state(anomaly_alerts=alerts, sla_status=slas))
        plan = update["remediation_plan"]
        assert plan["triggered_by"] == "sla:upf_throughput"

    def test_action_whitelist_constants(self):
        # Sanity: agree with the YAML / executor expectations.
        assert "horizontal_scale" in ACTION_TYPES
        assert "rollback" in ACTION_TYPES
        assert "config_change" in ACTION_TYPES
