"""
LangGraph State Machine — Pre-Deployment + Deployment + Post-Deployment.

Pre-deployment graph:
  Network Planner → (HITL) → Resource Allocator → VNF Configurator → Policy Validator → (HITL) → Deployer → END

Post-deployment graph (monitoring / remediation loop):
  KPI Monitor → Anomaly Detector → SLA Compliance → Planner Reasoning
    → (HITL on risky actions) → Auto-Scaler → Fault Recovery → END

Human-in-the-Loop checkpoints pause execution so the CLI can prompt
the user for approval before continuing.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from core.state import OrchestratorState
from agents import (
    network_planner_agent,
    resource_allocator_agent,
    vnf_configurator_agent,
    policy_validator_agent,
    deployer_agent,
    kpi_monitor_agent,
    anomaly_detector_agent,
    sla_compliance_agent,
    planner_reasoning_agent,
    auto_scaler_agent,
    fault_recovery_agent,
)

logger = logging.getLogger(__name__)


# Risk classification — actions that always need explicit operator approval.
_HITL_REQUIRED_ACTION_TYPES: frozenset[str] = frozenset({"rollback", "config_change"})
# Confidence below this triggers approval even for nominally-safe action types.
_HITL_LOW_CONFIDENCE = 0.60


def _should_require_remediation_approval(plan: dict[str, Any] | None) -> tuple[bool, str]:
    """
    Decide whether a remediation plan needs operator confirmation.

    Returns (requires_approval, reason). Approval is required when the plan
    contains a destructive action type or when planner confidence is low.
    Set the env var ``HITL_REMEDIATION_AUTO_APPROVE=1`` to bypass the gate
    (used by experiment baselines that need fully-autonomous execution).
    """
    if plan is None:
        return False, "no plan to review"

    if os.environ.get("HITL_REMEDIATION_AUTO_APPROVE") == "1":
        return False, "auto-approve override active"

    actions = plan.get("recommended_actions") or []
    risky = [a for a in actions if a.get("type") in _HITL_REQUIRED_ACTION_TYPES]
    if risky:
        types = ",".join(sorted({a.get("type", "") for a in risky}))
        return True, f"plan contains risky action(s): {types}"

    confidence = float(plan.get("confidence", 1.0))
    if confidence < _HITL_LOW_CONFIDENCE:
        return True, f"planner confidence {confidence:.2f} < {_HITL_LOW_CONFIDENCE}"

    return False, f"plan auto-approved (confidence={confidence:.2f}, low-risk actions)"


# HITL gate nodes 

def topology_review_gate(state: OrchestratorState) -> dict:
    """
    Checkpoint after Network Planner.
    Sets requires_approval=True so the CLI loop can pause.
    """
    logger.info("Topology review gate reached — awaiting user approval")
    return {
        "requires_approval": True,
        "current_agent": "topology_review",
    }


def deployment_review_gate(state: OrchestratorState) -> dict:
    """
    Checkpoint after Policy Validator.
    Sets requires_approval=True so the CLI loop can pause.
    """
    logger.info("Deployment review gate reached — awaiting user approval")
    return {
        "requires_approval": True,
        "current_agent": "deployment_review",
    }


def user_decision_topology(state: OrchestratorState) -> dict:
    """
    Node invoked after user responds to topology gate.
    Resets the approval flag.
    """
    logger.info("User decision on topology: approved=%s", state.get("user_approved"))
    return {"requires_approval": False}


def user_decision_deploy(state: OrchestratorState) -> dict:
    """
    Node invoked after user responds to deployment gate.
    Resets the approval flag.
    """
    logger.info("User decision on deployment: approved=%s", state.get("user_approved"))
    return {"requires_approval": False}


def remediation_review_gate(state: OrchestratorState) -> dict:
    """
    Checkpoint after Planner / Reasoning. Inspects the produced plan and
    sets ``requires_approval=True`` only when the plan contains risky
    actions (rollback, config_change) or low-confidence reasoning.
    Auto-approves otherwise so routine scaling / restart actions execute
    without operator friction.
    """
    plan = state.get("remediation_plan")
    needs_approval, reason = _should_require_remediation_approval(plan)
    if needs_approval:
        logger.info("Remediation review gate: pausing — %s", reason)
        return {
            "requires_approval": True,
            "current_agent": "remediation_review",
            "intervention_log": [
                {
                    "source": "remediation_review_gate",
                    "would_approve": False,
                    "reasons": [reason],
                    "ts": _now_iso(),
                }
            ],
        }
    logger.info("Remediation review gate: %s — auto-approving", reason)
    return {
        "requires_approval": False,
        "user_approved": True,
        "current_agent": "remediation_review",
    }


def user_decision_remediation(state: OrchestratorState) -> dict:
    """Reset the approval flag after the user has answered the remediation gate."""
    logger.info(
        "User decision on remediation: approved=%s",
        state.get("user_approved"),
    )
    return {"requires_approval": False}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# Routing functions

def route_after_topology_decision(
    state: OrchestratorState,
) -> Literal["resource_allocator", "__end__"]:
    """Route based on user's topology approval."""
    if state.get("user_approved"):
        return "resource_allocator"
    return "__end__"


def route_after_deploy_decision(
    state: OrchestratorState,
) -> Literal["deployer", "__end__"]:
    """Route based on user's deploy approval."""
    if state.get("user_approved"):
        return "deployer"
    return "__end__"


def route_after_remediation_decision(
    state: OrchestratorState,
) -> Literal["auto_scaler", "fault_recovery", "__end__"]:
    """
    Route after the remediation review gate.

    - If the user (or auto-approve) approved, dispatch by action type:
      rollback / restart / config_change → fault_recovery
      horizontal_scale / vertical_scale  → auto_scaler
    - If the user rejected, skip executors and end the cycle.
    """
    if not state.get("user_approved"):
        return "__end__"

    plan = state.get("remediation_plan") or {}
    actions = plan.get("recommended_actions") or []
    if not actions:
        return "__end__"

    first_type = actions[0].get("type", "")
    if first_type in ("rollback", "restart", "config_change"):
        return "fault_recovery"
    return "auto_scaler"


def route_after_planner(
    state: OrchestratorState,
) -> Literal["remediation_review_gate", "fault_recovery"]:
    """
    Decide whether to engage the HITL gate at all.

    On a quiet cycle (no plan produced) the gate has nothing to review and
    forcing the graph through it would trigger an unnecessary interrupt.
    Route around the gate straight to the fault_recovery agent — which
    no-ops cleanly when there is nothing to do — preserving the original
    linear behaviour for steady-state monitoring.
    """
    if state.get("remediation_plan") is None:
        return "fault_recovery"
    return "remediation_review_gate"


#  Graph builder — pre-deployment 

def build_pre_deployment_graph() -> tuple:
    """
    Build and compile the pre-deployment LangGraph.

    Returns:
        (compiled_graph, memory_saver) tuple.
    """
    builder = StateGraph(OrchestratorState)

    # ── Add nodes ──
    builder.add_node("network_planner", network_planner_agent)
    builder.add_node("topology_review_gate", topology_review_gate)
    builder.add_node("user_decision_topology", user_decision_topology)
    builder.add_node("resource_allocator", resource_allocator_agent)
    builder.add_node("vnf_configurator", vnf_configurator_agent)
    builder.add_node("policy_validator", policy_validator_agent)
    builder.add_node("deployment_review_gate", deployment_review_gate)
    builder.add_node("user_decision_deploy", user_decision_deploy)
    builder.add_node("deployer", deployer_agent)

    # ── Edges ──
    builder.add_edge(START, "network_planner")
    builder.add_edge("network_planner", "topology_review_gate")

    # HITL 1: topology review — graph pauses here
    # The CLI resumes by calling graph.invoke() with user_approved set
    builder.add_edge("topology_review_gate", "user_decision_topology")
    builder.add_conditional_edges(
        "user_decision_topology",
        route_after_topology_decision,
        {"resource_allocator": "resource_allocator", "__end__": END},
    )

    builder.add_edge("resource_allocator", "vnf_configurator")
    builder.add_edge("vnf_configurator", "policy_validator")
    builder.add_edge("policy_validator", "deployment_review_gate")

    # HITL 2: deployment review — graph pauses here
    builder.add_edge("deployment_review_gate", "user_decision_deploy")
    builder.add_conditional_edges(
        "user_decision_deploy",
        route_after_deploy_decision,
        {"deployer": "deployer", "__end__": END},
    )

    builder.add_edge("deployer", END)

    # ── Compile with memory ──
    memory = MemorySaver()
    graph = builder.compile(
        checkpointer=memory,
        interrupt_before=["user_decision_topology", "user_decision_deploy"],
    )

    logger.info("Pre-deployment graph compiled (nodes=%d)", len(builder.nodes))
    return graph, memory


#  Graph builder — post-deployment (monitoring / remediation) 

def build_post_deployment_graph() -> tuple:
    """
    Build and compile the post-deployment monitoring LangGraph.

    The graph runs a single monitoring cycle:
      KPI Monitor → Anomaly Detector → SLA Compliance → Planner Reasoning
        → Remediation Review Gate (HITL on risky actions)
        → Auto-Scaler  /  Fault Recovery → END

    Callers are expected to invoke this graph repeatedly (e.g. on a timer
    or in a while-loop) to achieve continuous monitoring.

    Returns:
        (compiled_graph, memory_saver) tuple.
    """
    builder = StateGraph(OrchestratorState)

    # ── Add nodes ──
    builder.add_node("kpi_monitor", kpi_monitor_agent)
    builder.add_node("anomaly_detector", anomaly_detector_agent)
    builder.add_node("sla_compliance", sla_compliance_agent)
    builder.add_node("planner_reasoning", planner_reasoning_agent)
    builder.add_node("remediation_review_gate", remediation_review_gate)
    builder.add_node("user_decision_remediation", user_decision_remediation)
    builder.add_node("auto_scaler", auto_scaler_agent)
    builder.add_node("fault_recovery", fault_recovery_agent)

    # ── Edges — linear pipeline up to the planner ──
    builder.add_edge(START, "kpi_monitor")
    builder.add_edge("kpi_monitor", "anomaly_detector")
    builder.add_edge("anomaly_detector", "sla_compliance")
    builder.add_edge("sla_compliance", "planner_reasoning")

    # ── HITL on remediation: only route through the gate when a plan exists.
    #    Quiet cycles (no plan) skip the gate to preserve the original linear
    #    flow and avoid unnecessary interrupt pauses.
    builder.add_conditional_edges(
        "planner_reasoning",
        route_after_planner,
        {
            "remediation_review_gate": "remediation_review_gate",
            "fault_recovery": "fault_recovery",
        },
    )
    builder.add_edge("remediation_review_gate", "user_decision_remediation")
    builder.add_conditional_edges(
        "user_decision_remediation",
        route_after_remediation_decision,
        {
            "auto_scaler": "auto_scaler",
            "fault_recovery": "fault_recovery",
            "__end__": END,
        },
    )

    builder.add_edge("auto_scaler", "fault_recovery")
    builder.add_edge("fault_recovery", END)

    # ── Compile with memory; pause before user_decision_remediation so the
    #    CLI / experiment harness can supply user_approved before resuming.
    memory = MemorySaver()
    graph = builder.compile(
        checkpointer=memory,
        interrupt_before=["user_decision_remediation"],
    )

    logger.info("Post-deployment graph compiled (nodes=%d)", len(builder.nodes))
    return graph, memory
