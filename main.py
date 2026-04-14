#!/usr/bin/env python3
"""
5G Network Orchestrator — Main Entry Point.

Wires the LangGraph state machine with the Rich CLI to run the
pre-deployment pipeline interactively.

Usage:
    cd ~/fyp && source venv/bin/activate && python main.py
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from core.logger import setup_logging
from core.graph import build_pre_deployment_graph
from core.state import OrchestratorState
from interface.cli import (
    print_welcome,
    capture_intent,
    display_agent_start,
    display_agent_done,
    display_topology,
    display_resource_allocation,
    display_validation_report,
    display_deployment_results,
    display_agent_message,
    ask_approval,
    display_error,
    display_final_status,
    console,
)
import os
print(f"OPENAI_API_BASE: {os.environ.get('OPENAI_API_BASE')}")

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the interactive pre-deployment pipeline."""

    # ── Initialise ──
    log_file = setup_logging()
    print_welcome(log_file)

    graph, memory = build_pre_deployment_graph()

    # Unique thread ID for this run (LangGraph uses this for state persistence)
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    logger.info("Pipeline started — thread_id=%s", thread_id)

    # ── Capture user intent ──
    intent = capture_intent()
    if not intent:
        console.print("[dim]No intent provided. Exiting.[/dim]")
        return

    # ── Build initial state ──
    initial_state: OrchestratorState = {
        "user_intent": intent,
        "parsed_intent": {},
        "topology": None,
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
        "current_agent": "start",
        "requires_approval": False,
        "user_approved": None,
        "error": None,
        "phase": "pre_deployment",
    }

    # ── Run graph until first interrupt ──
    console.print("\n[bold]Starting pre-deployment pipeline...[/bold]\n")
    display_agent_start("Network Planner")

    logger.info("Invoking graph — first segment (Network Planner → Topology Review)")
    for event in graph.stream(initial_state, config, stream_mode="values"):
        current_agent = event.get("current_agent", "")
        logger.debug("Stream event: current_agent=%s", current_agent)

    # ── Get state after first interrupt (topology_review) ──
    state = graph.get_state(config)
    current_values = state.values
    logger.info("First interrupt reached. current_agent=%s", current_values.get("current_agent"))

    # Check for errors
    if current_values.get("error"):
        display_error(current_values["error"])
        return

    display_agent_done("Network Planner")

    # ── Display topology and ask for approval ──
    topology = current_values.get("topology")
    if topology:
        display_topology(topology)
        logger.info("Topology displayed: %s (%d VNFs)",
                     topology.get("topology_id"),
                     len(topology.get("vnfs", [])))

    approved = ask_approval("Do you approve this topology? (The pipeline will proceed to Resource Allocation)")

    # ── Resume graph with user decision ──
    logger.info("Resuming graph with user_approved=%s", approved)
    graph.update_state(config, {"user_approved": approved})

    if not approved:
        display_final_status("NO_GO")
        logger.info("Pipeline halted — topology rejected by user")
        return

    display_agent_start("Resource Allocator → VNF Configurator → Policy Validator")

    for event in graph.stream(None, config, stream_mode="values"):
        current_agent = event.get("current_agent", "")
        logger.debug("Stream event: current_agent=%s", current_agent)

    # ── Get state after second interrupt (deployment_review) ──
    state = graph.get_state(config)
    current_values = state.values

    if current_values.get("error"):
        display_error(current_values["error"])
        return

    display_agent_done("Resource Allocator → VNF Configurator → Policy Validator")

    # ── Display results ──
    resource_alloc = current_values.get("resource_allocation")
    if resource_alloc:
        display_resource_allocation(resource_alloc)

    validation = current_values.get("validation_report")
    if validation:
        display_validation_report(validation)

    # ── Show the latest agent message ──
    messages = current_values.get("messages", [])
    if messages:
        display_agent_message(messages[-1])

    # ── Ask for deployment approval ──
    approved = ask_approval("Do you approve deployment? (Artifacts will be marked ready)")

    logger.info("Resuming graph with deployment approval=%s", approved)
    graph.update_state(config, {"user_approved": approved})

    for event in graph.stream(None, config, stream_mode="values"):
        current_agent = event.get("current_agent", "")
        logger.debug("Stream event: current_agent=%s", current_agent)

    # ── Final state ──
    final_state = graph.get_state(config).values

    # Check for errors from deployer
    if final_state.get("error"):
        display_error(final_state["error"])
        return

    # Display deployment results
    dep_results = final_state.get("deployment_results", [])
    if dep_results:
        display_deployment_results(dep_results)

    # Show latest agent message (deployer summary)
    messages = final_state.get("messages", [])
    if messages:
        display_agent_message(messages[-1])

    decision = "GO" if approved else "NO_GO"
    display_final_status(decision)

    # ── Log final summary ──
    logger.info("Pipeline finished — decision=%s, thread_id=%s", decision, thread_id)
    logger.info("Final topology: %s", final_state.get("topology", {}).get("topology_id"))
    logger.info("VNFs configured: %d", len(final_state.get("config_artifacts", [])))
    logger.info("Deployments: %d", len(dep_results))
    logger.info(
        "Validation: %s",
        json.dumps(final_state.get("validation_report", {}).get("policy_checks", {}), indent=2),
    )


if __name__ == "__main__":
    main()
