"""
Phase 2.3 — VNF Configurator Agent Tests.

Tests:
  1. Network params extraction (with connectivity)
  2. Network params extraction (defaults, no connectivity)
  3. NRF host discovery
  4. Core ConfigMap generation
  5. AMF Helm values generation
  6. UPF Helm values generation
  7. Generic fallback for unknown VNF types
  8. Config validation (valid and invalid)
  9. Agent error when no resource_allocation
  10. Full agent invocation with Ollama (integration)

Run:  cd ~/fyp && python tests/test_vnf_configurator.py
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.vnf_configurator import (
    _build_network_params,
    _get_nrf_host,
    _build_core_config,
    _build_nrf_values,
    _build_amf_values,
    _build_smf_values,
    _build_upf_values,
    _build_generic_values,
    _validate_configs,
    generate_vnf_configs,
    vnf_configurator_agent,
)


#  Helpers
SAMPLE_TOPOLOGY = {
    "topology_id": "test-5gc-001",
    "connectivity": {
        "plmn": {"mcc": "001", "mnc": "01"},
        "slices": [{"sst": 1, "sd": "000001", "description": "eMBB"}],
        "dnns": ["oai"],
    },
    "vnfs": [
        {"name": "oai-nrf", "type": "nrf", "replicas": 1},
        {"name": "oai-amf", "type": "amf", "replicas": 1},
        {"name": "oai-smf", "type": "smf", "replicas": 1},
        {"name": "oai-upf", "type": "upf", "replicas": 1},
    ],
}

SAMPLE_RESOURCED_VNFS = [
    {
        "name": "oai-nrf", "type": "nrf", "replicas": 1,
        "resources": {"requests": {"cpu": "150m", "memory": "160Mi"}, "limits": {"cpu": "225m", "memory": "240Mi"}},
        "interfaces": [], "supported_slices": [],
    },
    {
        "name": "oai-amf", "type": "amf", "replicas": 1,
        "resources": {"requests": {"cpu": "200m", "memory": "306Mi"}, "limits": {"cpu": "300m", "memory": "459Mi"}},
        "interfaces": [{"name": "n2", "protocol": "sctp", "port": 38412}],
        "supported_slices": [{"sst": 1, "sd": "000001"}],
    },
    {
        "name": "oai-smf", "type": "smf", "replicas": 1,
        "resources": {"requests": {"cpu": "250m", "memory": "336Mi"}, "limits": {"cpu": "375m", "memory": "504Mi"}},
        "interfaces": [{"name": "n4", "protocol": "pfcp", "port": 8805}],
        "supported_slices": [{"sst": 1, "sd": "000001"}],
    },
    {
        "name": "oai-upf", "type": "upf", "replicas": 1,
        "resources": {"requests": {"cpu": "2100m", "memory": "1Gi"}, "limits": {"cpu": "3150m", "memory": "1536Mi"}},
        "interfaces": [{"name": "n3", "protocol": "gtp-u", "port": 2152}],
        "supported_slices": [{"sst": 1, "sd": "000001"}],
    },
]


# ── Unit Tests (no LLM) ──────────────────────────────────────

def test_network_params_with_connectivity():
    """Extract network params from topology with connectivity block."""
    params = _build_network_params(SAMPLE_TOPOLOGY)
    assert params["mcc"] == "001"
    assert params["mnc"] == "01"
    assert params["plmn_id"] == "00101"
    assert len(params["snssais"]) == 1
    assert params["snssais"][0]["sst"] == 1
    assert params["dnns"] == ["oai"]
    assert len(params["dnn_configs"]) == 1
    assert params["dnn_configs"][0]["dnn"] == "oai"
    print("  [OK] Network params extracted with connectivity")


def test_network_params_defaults():
    """Network params default when no connectivity block."""
    params = _build_network_params({"vnfs": []})
    assert params["mcc"] == "001"
    assert params["mnc"] == "01"
    assert len(params["snssais"]) >= 1
    assert len(params["dnns"]) >= 1
    print("  [OK] Network params default correctly")


def test_nrf_host_discovery():
    """NRF host is discovered from VNF list."""
    vnfs = [
        {"name": "oai-nrf", "type": "nrf"},
        {"name": "oai-amf", "type": "amf"},
    ]
    assert _get_nrf_host(vnfs) == "oai-nrf"

    # Fallback when no NRF present
    assert _get_nrf_host([{"name": "oai-amf", "type": "amf"}]) == "oai-nrf"
    print("  [OK] NRF host discovery works")


def test_core_config_generation():
    """Shared core ConfigMap is generated correctly."""
    params = _build_network_params(SAMPLE_TOPOLOGY)
    core = _build_core_config(SAMPLE_RESOURCED_VNFS, params)

    assert "nfs" in core
    assert "amf" in core["nfs"]
    assert "smf" in core["nfs"]
    assert core["nfs"]["amf"]["sbi"]["port"] == 80
    assert core["nfs"]["amf"]["n2"]["port"] == 38412

    assert "amf" in core
    assert core["amf"]["served_guami_list"][0]["mcc"] == "001"
    assert len(core["amf"]["plmn_support_list"]) > 0

    assert "smf" in core
    assert "snssais" in core
    assert "dnns" in core
    print("  [OK] Core ConfigMap generated correctly")


def test_amf_helm_values():
    """AMF Helm values match OAI chart structure."""
    vnf = SAMPLE_RESOURCED_VNFS[1]  # AMF
    values = _build_amf_values(vnf)

    assert values["nfimage"]["repository"] == "docker.io/oaisoftwarealliance/oai-amf"
    assert values["nfimage"]["version"] == "v2.1.0"
    assert values["exposedPorts"]["sctp"] == 38412
    assert values["exposedPorts"]["sbi"] == 80
    assert values["resources"]["define"] is True
    assert values["resources"]["requests"]["nf"]["cpu"] == "200m"
    assert values["resources"]["limits"]["nf"]["memory"] == "459Mi"
    assert values["start"]["amf"] is True
    print("  [OK] AMF Helm values match OAI chart structure")


def test_upf_helm_values():
    """UPF Helm values match OAI chart structure."""
    vnf = SAMPLE_RESOURCED_VNFS[3]  # UPF
    values = _build_upf_values(vnf)

    assert values["exposedPorts"]["n3"] == 2152
    assert values["exposedPorts"]["n4"] == 8805
    assert values["securityContext"]["privileged"] is True
    assert values["start"]["spgwu"] is True  # OAI uses 'spgwu'
    assert values["resources"]["define"] is True
    print("  [OK] UPF Helm values match OAI chart structure")


def test_generic_fallback():
    """Unknown VNF types get generic Helm values."""
    vnf = {"name": "custom-vnf", "type": "custom", "replicas": 1, "resources": {}}
    values = _build_generic_values(vnf)

    assert "nfimage" in values
    assert "exposedPorts" in values
    assert values["resources"]["define"] is False
    print("  [OK] Generic fallback for unknown VNF types")


def test_full_config_generation():
    """Full config generation produces artifacts and core config."""
    configs, core = generate_vnf_configs(SAMPLE_TOPOLOGY, SAMPLE_RESOURCED_VNFS)

    assert len(configs) == 4
    names = [c["vnf_name"] for c in configs]
    assert "oai-nrf" in names
    assert "oai-amf" in names
    assert "oai-smf" in names
    assert "oai-upf" in names

    for cfg in configs:
        assert "helm_values" in cfg
        assert "config_maps" in cfg
    print(f"  [OK] Generated {len(configs)} config artifacts")


def test_validate_configs_valid():
    """Valid configs pass validation."""
    configs, core = generate_vnf_configs(SAMPLE_TOPOLOGY, SAMPLE_RESOURCED_VNFS)
    issues = _validate_configs(configs, core)
    assert issues == [], f"Expected no issues, got: {issues}"
    print("  [OK] Valid configs pass validation")


def test_validate_configs_invalid():
    """Invalid configs are flagged."""
    issues = _validate_configs([], {})
    assert any("No VNF" in i for i in issues)

    bad_configs = [{"vnf_name": "bad", "helm_values": {}}]
    issues2 = _validate_configs(bad_configs, {"nfs": {}, "snssais": [1], "dnns": ["x"], "amf": {}})
    assert len(issues2) > 0
    print(f"  [OK] Invalid configs flagged: {len(issues2)} issues")


def test_agent_no_allocation():
    """Agent returns error when no resource_allocation in state."""
    state = {"user_intent": "test"}
    result = vnf_configurator_agent(state)
    assert "error" in result
    assert "No resource allocation" in result["error"]
    print("  [OK] Agent correctly returns error for missing allocation")


# ── Integration Test (Ollama) ─────────────────────────────────

def test_agent_full_invocation():
    """Integration: invoke the VNF Configurator agent with realistic data."""
    state = {
        "user_intent": "Deploy a 5G core network for 500 UEs with 2 Gbps throughput",
        "topology": SAMPLE_TOPOLOGY,
        "resource_allocation": {
            "topology_id": "test-5gc-001",
            "vnfs": SAMPLE_RESOURCED_VNFS,
            "node_assignments": {},
        },
        "messages": [],
        "anomaly_alerts": [],
        "execution_results": [],
        "phase": "pre_deployment",
    }

    print("\n  Calling VNF Configurator (includes Ollama for validation)...")
    result = vnf_configurator_agent(state)

    print(f"  Result keys: {list(result.keys())}")

    if "error" in result and result.get("error"):
        print(f"  [WARN] Agent error: {result['error']}")

    assert "config_artifacts" in result, f"Missing 'config_artifacts', keys: {list(result.keys())}"
    configs = result["config_artifacts"]

    print(f"  VNFs configured: {len(configs)}")
    print(f"  VNF names: {[c['vnf_name'] for c in configs]}")

    # Check all expected VNFs got configs
    assert len(configs) == 4

    # Print config summary table
    print("\n  Configuration Summary:")
    print("  " + "-" * 80)
    print(f"  {'VNF':<15} {'Image':<10} {'CPU req':<10} {'Mem req':<10} {'Ports':<30}")
    print("  " + "-" * 80)
    for cfg in configs:
        hv = cfg["helm_values"]
        img = hv.get("nfimage", {}).get("version", "?")
        res = hv.get("resources", {})
        if res.get("define"):
            cpu = res["requests"]["nf"]["cpu"]
            mem = res["requests"]["nf"]["memory"]
        else:
            cpu = mem = "default"
        ports = ", ".join(f"{k}:{v}" for k, v in hv.get("exposedPorts", {}).items())
        print(f"  {cfg['vnf_name']:<15} {img:<10} {cpu:<10} {mem:<10} {ports:<30}")
    print("  " + "-" * 80)

    # Check core config is attached
    first_cfg = configs[0]
    assert first_cfg.get("config_maps", {}).get("core_network"), "Core ConfigMap should be attached"
    core = first_cfg["config_maps"]["core_network"]
    print(f"\n  Core ConfigMap keys: {list(core.keys())}")
    print(f"  PLMN: {core.get('amf', {}).get('served_guami_list', [{}])[0].get('mcc', '?')}"
          f"/{core.get('amf', {}).get('served_guami_list', [{}])[0].get('mnc', '?')}")
    print(f"  Slices: {core.get('snssais', [])}")
    print(f"  DNNs: {[d.get('dnn', '?') for d in core.get('dnns', [])]}")

    # Print agent message
    print(f"\n  Agent message:\n{result['messages'][0]['content']}")

    # Print sample Helm values (AMF)
    amf_cfg = next(c for c in configs if c["vnf_name"] == "oai-amf")
    print(f"\n  Sample AMF Helm values (first 1500 chars):")
    print(f"  {json.dumps(amf_cfg['helm_values'], indent=2)[:1500]}")

    print("\n  [OK] VNF Configurator agent produced valid configurations")


if __name__ == "__main__":
    print("\n=== Phase 2.3 — VNF Configurator Agent Tests ===\n")

    print("--- Unit Tests (no LLM) ---")
    test_network_params_with_connectivity()
    test_network_params_defaults()
    test_nrf_host_discovery()
    test_core_config_generation()
    test_amf_helm_values()
    test_upf_helm_values()
    test_generic_fallback()
    test_full_config_generation()
    test_validate_configs_valid()
    test_validate_configs_invalid()
    test_agent_no_allocation()

    print("\n--- Integration Test (Ollama) ---")
    test_agent_full_invocation()

    print("\n=== All VNF Configurator tests passed! ===")
