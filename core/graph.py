"""
LangGraph State Machine — Pre-Deployment + Deployment + Post-Deployment.

Pre-deployment graph:
  Network Planner → (HITL) → Resource Allocator → VNF Configurator → Policy Validator → (HITL) → Deployer → END

Post-deployment graph (monitoring / remediation loop):
  KPI Monitor → Anomaly Detector → SLA Compliance → Planner Reasoning → Auto-Scaler → END

Human-in-the-Loop checkpoints pause execution so the CLI can prompt
the user for approval before continuing.
"""

from __future__ import annotations

import logging
from typing import Literal

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
        → Auto-Scaler → Fault Recovery → END

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
    builder.add_node("auto_scaler", auto_scaler_agent)
    builder.add_node("fault_recovery", fault_recovery_agent)

    # ── Edges — linear pipeline ──
    builder.add_edge(START, "kpi_monitor")
    builder.add_edge("kpi_monitor", "anomaly_detector")
    builder.add_edge("anomaly_detector", "sla_compliance")
    builder.add_edge("sla_compliance", "planner_reasoning")
    builder.add_edge("planner_reasoning", "auto_scaler")
    builder.add_edge("auto_scaler", "fault_recovery")
    builder.add_edge("fault_recovery", END)

    # ── Compile with memory ──
    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)

    logger.info("Post-deployment graph compiled (nodes=%d)", len(builder.nodes))
    return graph, memory
