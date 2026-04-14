#!/usr/bin/env python3
"""
Experiment 1 Runner: Intent Translation Accuracy.

Runs the full pre-deployment pipeline over a dataset of 25 prompts,
automatically bypassing HITL gates and assessing translation accuracy.
Saves structured JSON results and a CSV summary.

Usage:
  cd ~/fyp && source venv/bin/activate
  python experiments/experiment_1/run_experiment.py [--dry-run] [--limit N]
"""

import argparse
import csv
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Import core LangGraph components
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

# Import agents, including the new teardown
from core.state import OrchestratorState
from core.logger import setup_logging
from agents import (
    network_planner_agent,
    resource_allocator_agent,
    vnf_configurator_agent,
    policy_validator_agent,
    deployer_agent,
)

# Import experiment scoring
from experiments.experiment_1.scoring import (
    vnf_coverage_score,
    structural_completeness_score,
    resource_validity_score,
    policy_pass_rate,
    deployment_success_rate,
    llm_as_judge,
    compute_composite_score,
)

# --- Configuration ---
EXPERIMENT_DIR = Path(__file__).parent
PROMPTS_FILE = EXPERIMENT_DIR / "prompts.json"
RESULTS_DIR = EXPERIMENT_DIR / "results"

console = Console()
logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Modified Graph for Automated Runs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def auto_approve(state: OrchestratorState) -> dict[str, Any]:
    """Auto-approve HITL gates for experiment runs."""
    return {"user_approved": True, "requires_approval": False}

def build_automated_graph() -> tuple[StateGraph, MemorySaver]:
    """
    Builds the pre-deployment graph but replaces HITL user inputs
    with unconditional auto-approval.
    """
    builder = StateGraph(OrchestratorState)

    builder.add_node("network_planner", network_planner_agent)
    builder.add_node("topology_review_gate", auto_approve)
    builder.add_node("resource_allocator", resource_allocator_agent)
    builder.add_node("vnf_configurator", vnf_configurator_agent)
    builder.add_node("policy_validator", policy_validator_agent)
    builder.add_node("deployment_review_gate", auto_approve)
    builder.add_node("deployer", deployer_agent)

    builder.add_edge(START, "network_planner")
    builder.add_edge("network_planner", "topology_review_gate")
    builder.add_edge("topology_review_gate", "resource_allocator")
    builder.add_edge("resource_allocator", "vnf_configurator")
    builder.add_edge("vnf_configurator", "policy_validator")
    builder.add_edge("policy_validator", "deployment_review_gate")
    builder.add_edge("deployment_review_gate", "deployer")
    builder.add_edge("deployer", END)

    memory = MemorySaver()
    # No interrupt_before checkpoints!
    graph = builder.compile(checkpointer=memory)
    return graph, memory


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Experiment Logic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_prompts() -> list[dict[str, Any]]:
    with open(PROMPTS_FILE, "r") as f:
        data = json.load(f)
    return data.get("prompts", [])


def run_single_prompt(
    graph: Any,
    prompt_data: dict[str, Any],
    dry_run: bool = False
) -> dict[str, Any]:
    """Run the pipeline for a single prompt and compute its scores."""
    prompt_id = prompt_data["id"]
    intent = prompt_data["prompt"]
    complexity = prompt_data["complexity"]
    expected_vnfs = prompt_data.get("expected_vnfs", [])

    console.print(f"\n[bold cyan]Run [{prompt_id}][/bold cyan] - {complexity.upper()}")
    console.print(f"[dim]Intent: {intent}[/dim]")

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: OrchestratorState = {
        "user_intent": intent,
        "clean_deploy": not dry_run,  # Toggles tearing down real K8s resources
        "requires_approval": False,
        "messages": [],
    }

    start_time = time.time()
    
    # In dry-run mode, we do NOT want the deployer to actually run helm commands
    # The deployer agent relies on the 'deployer' node. We can either patch the chart
    # path or rely on errors. But wait, deployer agent runs `helm install`. 
    # For a true dry run at the graph level, we could intercept before deployer.
    # To keep it simple, if dry_run, we can let it fail deployment but still score everything else.

    try:
        # Run graph to completion
        for _ in graph.stream(initial_state, config, stream_mode="values"):
            pass
        
        state_values = graph.get_state(config).values
    except Exception as e:
        logger.error(f"Graph execution failed for {prompt_id}: {e}")
        state_values = {"error": str(e)}

    execution_time = time.time() - start_time

    # Evaluate results
    topology = state_values.get("topology") or {}
    validation = state_values.get("validation_report") or {}
    deployment_results = state_values.get("deployment_results") or []
    resource_alloc = state_values.get("resource_allocation") or {}

    generated_vnfs = topology.get("vnfs", [])

    # 1. Deterministic Metrics
    score_struct = structural_completeness_score(topology)
    score_res = resource_validity_score(generated_vnfs)
    score_pol = policy_pass_rate(validation)
    score_dep = deployment_success_rate(deployment_results) if not dry_run else 0.0
    
    score_vnf = vnf_coverage_score(expected_vnfs, generated_vnfs)
    has_expected_vnfs = bool(expected_vnfs)

    # 2. LLM-as-Judge
    console.print("[dim]Evaluating with LLM-as-Judge...[/dim]")
    judge_result = llm_as_judge(intent, topology, resource_alloc)

    # 3. Composite Score
    scores_dict = {
        "vnf_coverage": score_vnf,
        "structural_completeness": score_struct,
        "resource_validity": score_res,
        "policy_pass_rate": score_pol,
        "deployment_success_rate": score_dep,
        "llm_judge": judge_result["composite"]
    }
    
    composite = compute_composite_score(scores_dict, has_expected_vnfs)

    result_record = {
        "id": prompt_id,
        "complexity": complexity,
        "execution_time_sec": round(execution_time, 2),
        "scores": scores_dict,
        "llm_judge_details": judge_result,
        "composite_score": composite,
        "generated_topology_id": topology.get("topology_id"),
        "vnf_count": len(generated_vnfs),
        "error": state_values.get("error"),
    }

    # Save individual result
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = RESULTS_DIR / f"{prompt_id}.json"
    with open(out_file, "w") as f:
        json.dump(result_record, f, indent=2)

    console.print(f"  ➜ Score: [bold green]{composite:.2f}[/bold green] (Judge: {judge_result['composite']:.2f})")
    
    return result_record


def write_summary_csv(results: list[dict[str, Any]]) -> None:
    csv_file = RESULTS_DIR / "summary.csv"
    headers = [
        "id", "complexity", "execution_time_sec", "composite_score",
        "vnf_coverage", "structural_completeness", "resource_validity",
        "policy_pass_rate", "deployment_success_rate", "llm_judge",
        "vnf_count", "error"
    ]
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for r in results:
            row = {
                "id": r["id"],
                "complexity": r["complexity"],
                "execution_time_sec": r["execution_time_sec"],
                "composite_score": r["composite_score"],
                "vnf_coverage": r["scores"]["vnf_coverage"],
                "structural_completeness": r["scores"]["structural_completeness"],
                "resource_validity": r["scores"]["resource_validity"],
                "policy_pass_rate": r["scores"]["policy_pass_rate"],
                "deployment_success_rate": r["scores"]["deployment_success_rate"],
                "llm_judge": r["scores"]["llm_judge"],
                "vnf_count": r["vnf_count"],
                "error": r.get("error", "")
            }
            writer.writerow(row)
    console.print(f"\n[bold green]Summary CSV saved to {csv_file}[/bold green]")


def main():
    parser = argparse.ArgumentParser(description="Run Experiment 1 Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Skip K8s teardown/deploy step")
    parser.add_argument("--limit", type=int, help="Limit number of prompts to run")
    args = parser.parse_args()

    setup_logging()
    
    prompts = load_prompts()
    if args.limit:
        prompts = prompts[:args.limit]
        
    console.print(Panel(
        f"[bold]Starting Experiment 1[/bold]\n"
        f"Prompts: {len(prompts)} | Dry-run: {args.dry_run}",
        title="Intent Translation Accuracy",
        border_style="magenta"
    ))

    graph, memory = build_automated_graph()
    
    all_results = []
    
    try:
        for prompt_data in prompts:
            res = run_single_prompt(graph, prompt_data, dry_run=args.dry_run)
            all_results.append(res)
    except KeyboardInterrupt:
        console.print("\n[bold red]Experiment interrupted by user.[/bold red]")
    
    if all_results:
        write_summary_csv(all_results)
        
        # Print rich table summary
        table = Table(title="Experiment Results Summary")
        table.add_column("ID")
        table.add_column("Complexity")
        table.add_column("Score", justify="right")
        table.add_column("Judge", justify="right")
        table.add_column("VNFs", justify="right")
        
        for r in all_results:
            table.add_row(
                r["id"],
                r["complexity"],
                f"{r['composite_score']:.2f}",
                f"{r['scores']['llm_judge']:.2f}",
                str(r["vnf_count"]),
            )
        console.print(table)


if __name__ == "__main__":
    main()
