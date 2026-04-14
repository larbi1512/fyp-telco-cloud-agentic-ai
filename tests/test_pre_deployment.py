"""
Phase 2 End-to-End Test — Pre-Deployment Pipeline.

Tests the sequential flow of all 4 pre-deployment agents:
User Intent → [Network Planner] → [Resource Allocator] → [VNF Configurator] → [Policy Validator]

Run: cd ~/fyp && python tests/test_pre_deployment.py
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.state import OrchestratorState
from agents import (
    network_planner_agent,
    resource_allocator_agent,
    vnf_configurator_agent,
    policy_validator_agent,
)


def run_pipeline(intent: str):
    """Run the 4 agents sequentially, simulating LangGraph."""
    print(f"\n{'='*60}")
    print(f"STARTING PRE-DEPLOYMENT PIPELINE")
    print(f"User Intent: '{intent}'")
    print(f"{'='*60}")

    # Initial state
    state: OrchestratorState = {
        "user_intent": intent,
        "parsed_intent": {},
        "topology": None,
        "resource_allocation": None,
        "config_artifacts": [],
        "validation_report": None,
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

    # 1. Network Planner
    print("\n[1/4] Network Planner Agent...")
    state.update(network_planner_agent(state))
    if state.get("error"):
        print(f" Network Planner failed: {state['error']}")
        return state

    topology = state.get("topology")
    print(f" Topology generated: {topology.get('topology_id')}")
    print(f" VNFs: {[v.get('name') for v in topology.get('vnfs', [])]}")

    # 2. Resource Allocator
    print("\n[2/4] Resource Allocator Agent...")
    state.update(resource_allocator_agent(state))
    if state.get("error"):
        print(f"Resource Allocator failed: {state['error']}")
        return state

    res_alloc = state.get("resource_allocation")
    print(f"Resources allocated for {len(res_alloc.get('vnfs', []))} VNFs")
    # Print UPF resources as an example
    for vnf in res_alloc.get("vnfs", []):
        if vnf.get("type", "").lower() == "upf":
            print(f"UPF limits: {vnf.get('resources', {}).get('limits')}")

    # 3. VNF Configurator
    print("\n[3/4] VNF Configurator Agent...")
    state.update(vnf_configurator_agent(state))
    if state.get("error"):
        print(f"VNF Configurator failed: {state['error']}")
        return state

    configs = state.get("config_artifacts", [])
    print(f" Configuration artifacts generated: {len(configs)}")
    if configs:
        core_cm = configs[0].get("config_maps", {}).get("core_network")
        if core_cm:
            print(f" Core ConfigMap includes PLMN: {core_cm.get('amf', {}).get('served_guami_list', [{}])[0]}")

    # 4. Policy Validator
    print("\n[4/4] Policy Validator Agent...")
    state.update(policy_validator_agent(state))
    if state.get("error"):
        print(f"Policy Validator failed: {state['error']}")
        return state

    report = state.get("validation_report", {})
    decision = report.get("decision")
    print(f"  Policy Validation Decision: {decision}")
    
    # Print the aggregate messages accumulated
    print(f"\n{'='*60}")
    print("AGENT MESSAGES")
    print(f"{'='*60}")
    for msg in state.get("messages", []):
        print(f"\n--- {msg.get('role').upper()} ---")
        print(msg.get("content"))
        
    print(f"\n{'='*60}")
    if decision == "GO":
        print("PIPELINE SUCCESS — Artifacts ready for deployment.")
    else:
        print("PIPELINE FAILED — Validation blocked deployment.")
        print(f"Reasons: {report.get('reasons')}")
    print(f"{'='*60}\n")

    return state


if __name__ == "__main__":
    # Test 1: Standard core
    run_pipeline("Deploy a standard 5G core network for 1000 UEs with 5 Gbps throughput and eMBB slicing.")
