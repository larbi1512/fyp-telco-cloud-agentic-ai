"""
Phase 2.1 — Network Planner Agent Tests.

Tests:
  1. VNF catalog loading from config
  2. Topology validation logic (unit, no LLM)
  3. Dependency checking logic (unit, no LLM)
  4. Full agent invocation with Ollama (integration)

Run:  cd ~/fyp && python tests/test_network_planner.py
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.network_planner import (
    _load_vnf_catalog,
    _validate_topology,
    _ensure_dependencies,
    network_planner_agent,
)


def test_vnf_catalog_loading():
    """VNF catalog loads from vnf_resource_profiles.yaml."""
    catalog = _load_vnf_catalog()
    assert len(catalog) >= 4, f"Expected ≥4 VNFs, got {len(catalog)}"
    names = [v["name"] for v in catalog]
    assert "oai-amf" in names
    assert "oai-smf" in names
    assert "oai-upf" in names
    assert "oai-nrf" in names
    print(f"  [OK] VNF catalog loaded: {len(catalog)} VNFs — {names}")


def test_topology_validation_valid():
    """Valid topology passes validation."""
    topo = {
        "topology_id": "test-001",
        "vnfs": [
            {"name": "oai-nrf", "type": "nrf", "replicas": 1, "interfaces": []},
            {"name": "oai-amf", "type": "amf", "replicas": 1, "interfaces": []},
            {"name": "oai-smf", "type": "smf", "replicas": 1, "interfaces": []},
            {"name": "oai-upf", "type": "upf", "replicas": 1, "interfaces": []},
        ],
        "connections": [
            {"from": "oai-amf", "to": "oai-nrf", "interface": "sbi"},
            {"from": "oai-smf", "to": "oai-nrf", "interface": "sbi"},
        ],
        "sla": {"max_latency_ms": 20, "min_throughput_gbps": 1.0},
    }
    issues = _validate_topology(topo)
    assert issues == [], f"Expected no issues, got: {issues}"
    print("  [OK] Valid topology passes validation")


def test_topology_validation_missing_vnfs():
    """Topology with no VNFs fails validation."""
    topo = {"topology_id": "bad", "vnfs": []}
    issues = _validate_topology(topo)
    assert any("no VNFs" in i for i in issues), f"Expected 'no VNFs' issue, got: {issues}"
    print("  [OK] Empty topology correctly flagged")


def test_topology_validation_duplicate_name():
    """Duplicate VNF names are caught."""
    topo = {
        "vnfs": [
            {"name": "oai-amf", "type": "amf"},
            {"name": "oai-amf", "type": "amf"},
        ]
    }
    issues = _validate_topology(topo)
    assert any("Duplicate" in i for i in issues), f"Expected duplicate issue, got: {issues}"
    print("  [OK] Duplicate VNF name correctly flagged")


def test_topology_validation_bad_connection():
    """Connection referencing nonexistent VNF is caught."""
    topo = {
        "vnfs": [{"name": "oai-amf", "type": "amf"}],
        "connections": [{"from": "oai-amf", "to": "oai-ghost", "interface": "sbi"}],
    }
    issues = _validate_topology(topo)
    assert any("unknown VNF" in i for i in issues)
    print("  [OK] Bad connection reference correctly flagged")


def test_topology_defaults():
    """Missing replicas and SLA get defaults."""
    topo = {
        "vnfs": [{"name": "oai-nrf", "type": "nrf"}],
    }
    _validate_topology(topo)
    assert topo["vnfs"][0]["replicas"] == 1, "Default replicas should be 1"
    assert "sla" in topo, "Default SLA should be added"
    assert topo["sla"]["max_latency_ms"] == 20
    print("  [OK] Defaults correctly applied (replicas=1, SLA added)")


def test_dependency_check_complete():
    """Complete topology has no dependency warnings."""
    topo = {
        "vnfs": [
            {"name": "oai-nrf", "type": "nrf"},
            {"name": "oai-udr", "type": "udr"},
            {"name": "oai-udm", "type": "udm"},
            {"name": "oai-ausf", "type": "ausf"},
            {"name": "oai-amf", "type": "amf"},
            {"name": "oai-smf", "type": "smf"},
            {"name": "oai-upf", "type": "upf"},
        ]
    }
    warnings = _ensure_dependencies(topo)
    assert warnings == [], f"Expected no warnings for complete topology, got: {warnings}"
    print("  [OK] Complete topology has no dependency warnings")


def test_dependency_check_missing():
    """Topology missing NRF triggers dependency warnings."""
    topo = {
        "vnfs": [
            {"name": "oai-amf", "type": "amf"},
            {"name": "oai-smf", "type": "smf"},
        ]
    }
    warnings = _ensure_dependencies(topo)
    assert len(warnings) > 0, "Should warn about missing NRF"
    assert any("nrf" in w for w in warnings)
    print(f"  [OK] Missing dependencies correctly flagged: {len(warnings)} warnings")


def test_agent_no_intent():
    """Agent returns error when no intent is provided."""
    state = {"user_intent": ""}
    result = network_planner_agent(state)
    assert "error" in result
    assert "No user intent" in result["error"]
    print("  [OK] Agent correctly returns error for empty intent")


def test_agent_full_invocation():
    """Integration: invoke the Network Planner agent with Ollama."""
    state = {
        "user_intent": "Deploy a minimal 5G core network with AMF, SMF, UPF, and NRF for testing with 100 UEs",
        "messages": [],
        "anomaly_alerts": [],
        "execution_results": [],
        "phase": "pre_deployment",
    }

    print("\n  Calling Ollama (this may take 30-60s)...")
    result = network_planner_agent(state)

    print(f"  Result keys: {list(result.keys())}")

    # Check for errors
    if "error" in result and result["error"]:
        print(f"  [WARN] Agent returned error: {result['error']}")
    
    # Check topology was produced
    assert "topology" in result, f"Expected 'topology' in result, got keys: {list(result.keys())}"
    topo = result["topology"]
    
    print(f"  Topology ID: {topo.get('topology_id', 'N/A')}")
    print(f"  VNFs: {[v['name'] for v in topo.get('vnfs', [])]}")
    print(f"  Connections: {len(topo.get('connections', []))}")
    print(f"  SLA: {topo.get('sla', {})}")

    # Validate structure
    assert len(topo["vnfs"]) >= 3, f"Expected ≥3 VNFs, got {len(topo['vnfs'])}"

    # Check we got expected VNF types
    vnf_types = {v.get("type", "").lower() for v in topo["vnfs"]}
    assert "amf" in vnf_types or any("amf" in v.get("name", "").lower() for v in topo["vnfs"]), \
        "Expected AMF in topology"

    # Check messages
    assert len(result["messages"]) > 0
    print(f"\n  Agent message:\n  {result['messages'][0]['content']}")

    # Pretty print full topology
    print(f"\n  Full topology JSON:")
    print(f"  {json.dumps(topo, indent=2)}")

    print("\n  [OK] Network Planner agent produced valid topology via Ollama")


if __name__ == "__main__":
    print("\n=== Phase 2.1 — Network Planner Agent Tests ===\n")

    # Unit tests (no LLM needed)
    print("--- Unit Tests ---")
    test_vnf_catalog_loading()
    test_topology_validation_valid()
    test_topology_validation_missing_vnfs()
    test_topology_validation_duplicate_name()
    test_topology_validation_bad_connection()
    test_topology_defaults()
    test_dependency_check_complete()
    test_dependency_check_missing()
    test_agent_no_intent()

    # Integration test (calls Ollama)
    print("\n--- Integration Test (Ollama) ---")
    test_agent_full_invocation()

    print("\n=== All Network Planner tests passed! ===")
