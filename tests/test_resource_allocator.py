import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.resource_allocator import (
    _parse_cpu,
    _parse_memory,
    _format_cpu,
    _format_memory,
    _load_profiles,
    _extract_capacity,
    _validate_allocation,
    calculate_vnf_resources,
    resource_allocator_agent,
)


# ── Unit Tests (no LLM) ──────────────────────────────────────

def test_parse_cpu():
    """CPU string parsing to millicores."""
    assert _parse_cpu("100m") == 100
    assert _parse_cpu("1") == 1000
    assert _parse_cpu("2.5") == 2500
    assert _parse_cpu("500m") == 500
    print("  [OK] CPU parsing: 100m→100, 1→1000, 2.5→2500")


def test_parse_memory():
    """Memory string parsing to MiB."""
    assert _parse_memory("256Mi") == 256
    assert _parse_memory("1Gi") == 1024
    assert _parse_memory("512Mi") == 512
    assert _parse_memory("2Gi") == 2048
    print("  [OK] Memory parsing: 256Mi→256, 1Gi→1024, 2Gi→2048")


def test_format_functions():
    """Format CPU/memory back to K8s strings."""
    assert _format_cpu(1500) == "1500m"
    assert _format_memory(256) == "256Mi"
    assert _format_memory(1024) == "1Gi"
    assert _format_memory(2048) == "2Gi"
    print("  [OK] Format functions: 1500→1500m, 1024→1Gi")


def test_load_profiles():
    """VNF profiles load from YAML."""
    profiles = _load_profiles()
    assert "oai-amf" in profiles
    assert "oai-upf" in profiles
    assert "base" in profiles["oai-amf"]
    assert "scaling" in profiles["oai-amf"]
    print(f"  [OK] Loaded {len(profiles)} VNF profiles")


def test_calculate_amf_100ue():
    """AMF resources for 100 UEs (minimal load)."""
    profiles = _load_profiles()
    capacity = {"ue_count": 100, "throughput_gbps": 1.0, "sessions": 100}
    res = calculate_vnf_resources("oai-amf", "amf", profiles, capacity)

    req_cpu = _parse_cpu(res["requests"]["cpu"])
    req_mem = _parse_memory(res["requests"]["memory"])

    # base=100m + (100/1000)*200m = 100+20 = 120m
    assert req_cpu == 120, f"Expected 120m CPU, got {req_cpu}m"
    # base=256Mi + (100/1000)*100Mi = 256+10 = 266Mi
    assert req_mem == 266, f"Expected 266Mi, got {req_mem}Mi"

    # Limits should be 150% of requests
    lim_cpu = _parse_cpu(res["limits"]["cpu"])
    assert lim_cpu == 180, f"Expected 180m limit, got {lim_cpu}m"

    print(f"  [OK] AMF@100UE: req={res['requests']}, lim={res['limits']}")


def test_calculate_amf_1000ue():
    """AMF resources for 1000 UEs."""
    profiles = _load_profiles()
    capacity = {"ue_count": 1000, "throughput_gbps": 1.0, "sessions": 1000}
    res = calculate_vnf_resources("oai-amf", "amf", profiles, capacity)

    req_cpu = _parse_cpu(res["requests"]["cpu"])
    # base=100m + (1000/1000)*200m = 100+200 = 300m
    assert req_cpu == 300, f"Expected 300m CPU, got {req_cpu}m"
    print(f"  [OK] AMF@1000UE: req={res['requests']}")


def test_calculate_upf_throughput():
    """UPF resources scale with throughput."""
    profiles = _load_profiles()
    capacity = {"ue_count": 100, "throughput_gbps": 2.0, "sessions": 100}
    res = calculate_vnf_resources("oai-upf", "upf", profiles, capacity)

    req_cpu = _parse_cpu(res["requests"]["cpu"])
    # base=500m + 2.0*800m = 500+1600 = 2100m
    assert req_cpu == 2100, f"Expected 2100m CPU, got {req_cpu}m"
    print(f"  [OK] UPF@2Gbps: req={res['requests']}")


def test_calculate_max_cap():
    """Resources are capped at profile max."""
    profiles = _load_profiles()
    capacity = {"ue_count": 50000, "throughput_gbps": 100, "sessions": 50000}
    res = calculate_vnf_resources("oai-amf", "amf", profiles, capacity)

    req_cpu = _parse_cpu(res["requests"]["cpu"])
    # max for AMF is 2000m
    assert req_cpu == 2000, f"Expected 2000m (capped), got {req_cpu}m"
    print(f"  [OK] AMF@50kUE capped at max: {res['requests']['cpu']}")


def test_calculate_unknown_vnf():
    """Unknown VNF gets fallback defaults."""
    profiles = _load_profiles()
    capacity = {"ue_count": 100, "throughput_gbps": 1.0, "sessions": 100}
    res = calculate_vnf_resources("custom-vnf", "custom", profiles, capacity)

    assert res["requests"]["cpu"] == "100m"
    assert res["requests"]["memory"] == "128Mi"
    print(f"  [OK] Unknown VNF gets defaults: {res['requests']}")


def test_extract_capacity_from_intent():
    """Capacity extraction from user intent text."""
    state = {"user_intent": "Deploy 5G core with 500 UEs and 2 Gbps throughput"}
    cap = _extract_capacity(state)
    assert cap["ue_count"] == 500, f"Expected 500 UEs, got {cap['ue_count']}"
    assert cap["throughput_gbps"] == 2.0, f"Expected 2.0 Gbps, got {cap['throughput_gbps']}"
    print(f"  [OK] Capacity from intent: {cap}")


def test_extract_capacity_defaults():
    """Default capacity when intent has no numbers."""
    state = {"user_intent": "Deploy a minimal 5G core"}
    cap = _extract_capacity(state)
    assert cap["ue_count"] == 100
    assert cap["throughput_gbps"] == 1.0
    print(f"  [OK] Default capacity: {cap}")


def test_validate_allocation():
    """Allocation validation catches missing resources."""
    # Valid
    alloc = {"vnfs": [{"name": "amf", "resources": {
        "requests": {"cpu": "100m", "memory": "256Mi"},
        "limits": {"cpu": "200m", "memory": "512Mi"},
    }}]}
    assert _validate_allocation(alloc) == []

    # Missing resources
    alloc_bad = {"vnfs": [{"name": "amf"}]}
    issues = _validate_allocation(alloc_bad)
    assert len(issues) > 0
    print(f"  [OK] Validation: valid=pass, missing resources={len(issues)} issues")


def test_agent_no_topology():
    """Agent returns error when no topology is in state."""
    state = {"user_intent": "test"}
    result = resource_allocator_agent(state)
    assert "error" in result
    print("  [OK] Agent returns error without topology")


# ── Integration Test (Ollama) ─────────────────────────────────

def test_agent_full_invocation():
    """Full agent invocation with a realistic topology from Network Planner."""
    # Simulate the output that Network Planner would have produced
    topology = {
        "topology_id": "test-5gc-001",
        "vnfs": [
            {"name": "oai-nrf", "type": "nrf", "replicas": 1, "interfaces": [
                {"name": "sbi", "protocol": "http2", "port": 80}
            ]},
            {"name": "oai-amf", "type": "amf", "replicas": 1, "interfaces": [
                {"name": "n2", "protocol": "sctp", "port": 38412},
                {"name": "sbi", "protocol": "http2", "port": 80},
            ]},
            {"name": "oai-smf", "type": "smf", "replicas": 1, "interfaces": [
                {"name": "n4", "protocol": "pfcp", "port": 8805},
                {"name": "sbi", "protocol": "http2", "port": 80},
            ]},
            {"name": "oai-upf", "type": "upf", "replicas": 1, "interfaces": [
                {"name": "n3", "protocol": "gtp-u", "port": 2152},
                {"name": "n4", "protocol": "pfcp", "port": 8805},
            ]},
            {"name": "oai-ausf", "type": "ausf", "replicas": 1, "interfaces": []},
            {"name": "oai-udm", "type": "udm", "replicas": 1, "interfaces": []},
        ],
        "connections": [
            {"from": "oai-amf", "to": "oai-nrf", "interface": "sbi"},
            {"from": "oai-smf", "to": "oai-nrf", "interface": "sbi"},
            {"from": "oai-smf", "to": "oai-upf", "interface": "n4"},
        ],
        "sla": {"max_latency_ms": 20, "min_throughput_gbps": 1.0, "min_availability_pct": 99.9},
    }

    state = {
        "user_intent": "Deploy a 5G core network for 500 UEs with 2 Gbps throughput",
        "topology": topology,
        "messages": [],
        "anomaly_alerts": [],
        "execution_results": [],
        "phase": "pre_deployment",
    }

    print("\n  Calling Resource Allocator (includes Ollama for affinity)...")
    result = resource_allocator_agent(state)

    print(f"  Result keys: {list(result.keys())}")

    if "error" in result and result["error"]:
        print(f"  [WARN] Agent error: {result['error']}")

    assert "resource_allocation" in result, f"Missing 'resource_allocation', keys: {list(result.keys())}"
    alloc = result["resource_allocation"]

    print(f"  Topology ID: {alloc['topology_id']}")
    print(f"  VNFs allocated: {len(alloc['vnfs'])}")

    # Print resource table
    print("\n  Resource Allocation Table:")
    print("  " + "-" * 70)
    print(f"  {'VNF':<15} {'Replicas':>8} {'CPU req':>10} {'CPU lim':>10} {'Mem req':>10} {'Mem lim':>10}")
    print("  " + "-" * 70)
    for vnf in alloc["vnfs"]:
        r = vnf["resources"]
        print(
            f"  {vnf['name']:<15} {vnf['replicas']:>8} "
            f"{r['requests']['cpu']:>10} {r['limits']['cpu']:>10} "
            f"{r['requests']['memory']:>10} {r['limits']['memory']:>10}"
        )
    print("  " + "-" * 70)

    # Validate all VNFs got resources
    for vnf in alloc["vnfs"]:
        assert "resources" in vnf, f"VNF {vnf['name']} missing resources"
        assert "requests" in vnf["resources"]
        assert "limits" in vnf["resources"]

    # Check specific calculations
    amf = next(v for v in alloc["vnfs"] if v["name"] == "oai-amf")
    amf_cpu = _parse_cpu(amf["resources"]["requests"]["cpu"])
    # 100 + (500/1000)*200 = 200m
    assert amf_cpu == 200, f"AMF CPU should be 200m for 500 UEs, got {amf_cpu}m"

    upf = next(v for v in alloc["vnfs"] if v["name"] == "oai-upf")
    upf_cpu = _parse_cpu(upf["resources"]["requests"]["cpu"])
    # 500 + 2.0*800 = 2100m
    assert upf_cpu == 2100, f"UPF CPU should be 2100m for 2Gbps, got {upf_cpu}m"

    # Print summary message
    print(f"\n  Agent message:\n{result['messages'][0]['content']}")

    print("\n  [OK] Resource Allocator agent produced valid allocation")


if __name__ == "__main__":
    print("\n=== Phase 2.2 — Resource Allocator Agent Tests ===\n")

    print("--- Unit Tests (no LLM) ---")
    test_parse_cpu()
    test_parse_memory()
    test_format_functions()
    test_load_profiles()
    test_calculate_amf_100ue()
    test_calculate_amf_1000ue()
    test_calculate_upf_throughput()
    test_calculate_max_cap()
    test_calculate_unknown_vnf()
    test_extract_capacity_from_intent()
    test_extract_capacity_defaults()
    test_validate_allocation()
    test_agent_no_topology()

    print("\n--- Integration Test (Ollama) ---")
    test_agent_full_invocation()

    print("\n=== All Resource Allocator tests passed! ===")