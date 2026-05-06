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
import time
import uuid
from datetime import datetime, timezone

from core.logger import setup_logging
from core.graph import build_pre_deployment_graph, build_post_deployment_graph
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
    display_metrics_summary,
    display_anomaly_alerts,
    display_remediation_plan,
    console,
)

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


def run_monitor(interval: int = 30) -> None:
    """Run the post-deployment monitoring loop."""
    log_file = setup_logging()
    print_welcome(log_file)

    # ── Bootstrap topology from Redis ──
    topology = None
    resource_allocation = None
    try:
        from infra.redis_client import RedisClient
        _redis = RedisClient()
        if _redis.ping():
            topology = _redis.get_topology()
            resource_allocation = _redis.get_resource_allocation()
            if topology:
                console.print(
                    f"  [bold green]Loaded topology from Redis:[/bold green] "
                    f"{topology.get('topology_id')} "
                    f"({len(topology.get('vnfs', []))} VNFs)"
                )
            else:
                console.print(
                    "  [bold yellow]Warning:[/bold yellow] No topology in Redis. "
                    "Run pre-deployment first (python main.py --mode deploy)."
                )
        else:
            console.print(
                "  [bold yellow]Warning:[/bold yellow] Redis unavailable — "
                "starting with empty state."
            )
    except ImportError:
        console.print("  [dim]Redis not installed — starting with empty state.[/dim]")

    # ── Build graph ──
    graph, _ = build_post_deployment_graph()
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # ── Initial state ──
    state: OrchestratorState = {
        "user_intent": "monitor",
        "parsed_intent": {},
        "intent_features": {},
        "topology": topology,
        "resource_allocation": resource_allocation,
        "config_artifacts": [],
        "validation_report": None,
        "deployment_results": [],
        "current_metrics": [],
        "anomaly_alerts": [],
        "sla_status": [],
        "remediation_plan": None,
        "execution_results": [],
        "messages": [],
        "intervention_log": [],
        "current_agent": "start",
        "requires_approval": False,
        "user_approved": None,
        "error": None,
        "phase": "post_deployment",
    }

    console.print(
        f"\n  [bold]Monitoring loop started[/bold] — "
        f"interval: [cyan]{interval}s[/cyan]  |  Ctrl+C to stop\n"
    )
    cycle = 0

    try:
        while True:
            cycle += 1
            console.rule(
                f"[bold blue]Cycle {cycle}[/bold blue]  "
                f"{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
            )

            try:
                result = graph.invoke(state, config)
            except Exception as exc:
                display_error(f"Monitoring cycle {cycle} failed: {exc}")
                logger.exception("Monitoring cycle %d failed", cycle)
                time.sleep(interval)
                continue

            # ── Display cycle outputs ──
            display_metrics_summary(result.get("current_metrics") or [])
            display_anomaly_alerts(result.get("anomaly_alerts") or [])
            display_remediation_plan(result.get("remediation_plan"))

            exec_results = result.get("execution_results") or []
            if exec_results:
                console.print("\n  [bold]Execution Results:[/bold]")
                display_deployment_results(exec_results)

            if result.get("error"):
                display_error(result["error"])

            # ── Roll state forward ──
            # Reset per-cycle fields; carry forward topology context
            state = {
                **state,
                "current_metrics": [],
                "anomaly_alerts": [],
                "sla_status": [],
                "remediation_plan": None,
                "execution_results": [],
                "messages": [],
                "error": None,
                "topology": result.get("topology") or state.get("topology"),
                "resource_allocation": (
                    result.get("resource_allocation") or state.get("resource_allocation")
                ),
            }

            time.sleep(interval)

    except KeyboardInterrupt:
        console.print("\n  [bold yellow]Monitoring stopped.[/bold yellow]\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="5G Network Orchestrator")
    parser.add_argument(
        "--mode",
        choices=["deploy", "monitor"],
        default="deploy",
        help="deploy: interactive pre-deployment pipeline (default). "
             "monitor: continuous post-deployment monitoring loop.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Monitoring cycle interval in seconds (monitor mode only, default: 30)",
    )
    args = parser.parse_args()

    if args.mode == "monitor":
        run_monitor(interval=args.interval)
    else:
        main()
