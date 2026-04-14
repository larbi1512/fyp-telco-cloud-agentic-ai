"""
Phase 3 — Graph Integration Test.

Tests the LangGraph state machine by programmatically stepping through
the compiled graph, simulating user approvals at HITL breakpoints.

Run:  cd ~/fyp && source venv/bin/activate && python tests/test_graph.py
"""

import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.logger import setup_logging
from core.graph import build_pre_deployment_graph
from core.state import OrchestratorState


def test_graph_full_pipeline():
    """
    End-to-end: build graph, stream through all 4 agents with
    HITL approvals, verify final state.
    """
    setup_logging()
    graph, memory = build_pre_deployment_graph()

    thread_id = "test-graph-001"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: OrchestratorState = {
        "user_intent": "Deploy a standard 5G core for 500 UEs with 2 Gbps throughput",
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

    # ── Segment 1: START → Network Planner → topology_review_gate → interrupt ──
    print("\n[1] Running Network Planner (this may take 30-60s with Ollama)...")
    for event in graph.stream(initial_state, config, stream_mode="values"):
        pass

    state = graph.get_state(config)
    values = state.values

    assert values.get("topology") is not None, "Topology should be set after Network Planner"
    assert values.get("requires_approval") == True, "Should require approval at topology gate"
    print(f"  ✅ Network Planner produced topology: {values['topology'].get('topology_id')}")
    print(f"  ✅ VNFs: {[v.get('name') for v in values['topology'].get('vnfs', [])]}")

    # ── Approve topology ──
    print("\n[2] Simulating user approval of topology...")
    graph.update_state(config, {"user_approved": True})

    # ── Segment 2: Resource Allocator → VNF Configurator → Policy Validator → deployment_review_gate → interrupt ──
    print("  Running Resource Allocator → VNF Configurator → Policy Validator...")
    for event in graph.stream(None, config, stream_mode="values"):
        pass

    state = graph.get_state(config)
    values = state.values

    assert values.get("resource_allocation") is not None, "Resource allocation should be set"
    assert len(values.get("config_artifacts", [])) > 0, "Config artifacts should be generated"
    assert values.get("validation_report") is not None, "Validation report should be set"
    assert values.get("requires_approval") == True, "Should require approval at deployment gate"

    report = values["validation_report"]
    print(f"  ✅ Resources allocated for {len(values['resource_allocation'].get('vnfs', []))} VNFs")
    print(f"  ✅ Config artifacts: {len(values['config_artifacts'])}")
    print(f"  ✅ Validation decision: {report.get('decision')}")

    # Print policy checks
    for check_name, data in report.get("policy_checks", {}).items():
        print(f"     {check_name}: {data.get('status')}")

    # ── Approve deployment ──
    print("\n[3] Simulating user approval of deployment...")
    graph.update_state(config, {"user_approved": True})

    for event in graph.stream(None, config, stream_mode="values"):
        pass

    final_state = graph.get_state(config).values
    assert final_state.get("phase") == "deploying", f"Phase should be 'deploying', got '{final_state.get('phase')}'"
    print(f"  ✅ Final phase: {final_state.get('phase')}")
    print(f"  ✅ Pipeline complete — artifacts approved for deployment!")


def test_graph_topology_rejection():
    """Test that rejecting the topology ends the pipeline early."""
    graph, memory = build_pre_deployment_graph()

    thread_id = "test-graph-reject"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: OrchestratorState = {
        "user_intent": "Deploy a basic 5G core",
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

    # ── Segment 1: Run to first interrupt ──
    print("\n[4] Running Network Planner for rejection test...")
    for event in graph.stream(initial_state, config, stream_mode="values"):
        pass

    state = graph.get_state(config)
    assert state.values.get("topology") is not None
    print(f"  ✅ Topology generated: {state.values['topology'].get('topology_id')}")

    # ── Reject topology ──
    print("  Simulating user REJECTION of topology...")
    graph.update_state(config, {"user_approved": False})

    for event in graph.stream(None, config, stream_mode="values"):
        pass

    final_state = graph.get_state(config).values
    # Pipeline should have ended — no resource allocation
    assert final_state.get("resource_allocation") is None, "No resource allocation after rejection"
    print(f"  ✅ Pipeline terminated early — no resource allocation generated")


if __name__ == "__main__":
    print("\n=== Phase 3 — LangGraph Integration Tests ===\n")

    test_graph_full_pipeline()

    print("\n" + "=" * 60)

    test_graph_topology_rejection()

    print("\n=== All graph tests passed! ===\n")
