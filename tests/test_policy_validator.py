"""
Phase 2.4 — Policy Validator Agent Tests.

Tests:
  1. Resource limits check (valid)
  2. Resource limits check (invalid — define=False)
  3. Root container check (flags root)
  4. Mandatory fields check (valid)
  5. Mandatory fields check (missing nfimage)
  6. Image tags check (warns on 'latest')
  7. Health probes check
  8. Full static policy aggregation
  9. GO/NO_GO decision — all pass
  10. GO/NO_GO decision — with FAIL
  11. Agent error when no config_artifacts
  12. Full agent invocation with Ollama (integration)

Run:  cd ~/fyp && python tests/test_policy_validator.py
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.policy_validator import (
    _check_resource_limits,
    _check_root_containers,
    _check_mandatory_fields,
    _check_image_tags,
    _check_probes,
    run_static_policies,
    determine_decision,
    policy_validator_agent,
)


# ── Sample data ───────────────────────────────────────────────

VALID_CONFIGS = [
    {
        "vnf_name": "oai-nrf",
        "helm_values": {
            "nfimage": {"repository": "docker.io/oaisoftwarealliance/oai-nrf", "version": "v2.1.0"},
            "exposedPorts": {"sbi": 80},
            "start": {"nrf": True, "tcpdump": False},
            "podSecurityContext": {"runAsUser": 0, "runAsGroup": 0},
            "resources": {
                "define": True,
                "requests": {"nf": {"cpu": "150m", "memory": "160Mi"}},
                "limits": {"nf": {"cpu": "225m", "memory": "240Mi"}},
            },
            "readinessProbe": True,
            "livenessProbe": False,
        },
        "config_maps": {},
        "secrets": [],
    },
    {
        "vnf_name": "oai-amf",
        "helm_values": {
            "nfimage": {"repository": "docker.io/oaisoftwarealliance/oai-amf", "version": "v2.1.0"},
            "exposedPorts": {"sctp": 38412, "sbi": 80},
            "start": {"amf": True, "tcpdump": False},
            "podSecurityContext": {"runAsUser": 0, "runAsGroup": 0},
            "resources": {
                "define": True,
                "requests": {"nf": {"cpu": "200m", "memory": "306Mi"}},
                "limits": {"nf": {"cpu": "300m", "memory": "459Mi"}},
            },
            "readinessProbe": True,
            "livenessProbe": False,
        },
        "config_maps": {"core_network": {"snssais": [{"sst": 1}], "nfs": {"amf": {}}}},
        "secrets": [],
    },
    {
        "vnf_name": "oai-upf",
        "helm_values": {
            "nfimage": {"repository": "docker.io/oaisoftwarealliance/oai-upf", "version": "v2.1.0"},
            "exposedPorts": {"sbi": 80, "n4": 8805, "n3": 2152},
            "start": {"spgwu": True, "tcpdump": False},
            "podSecurityContext": {"runAsUser": 0, "runAsGroup": 0},
            "securityContext": {"privileged": True},
            "resources": {
                "define": True,
                "requests": {"nf": {"cpu": "2100m", "memory": "1Gi"}},
                "limits": {"nf": {"cpu": "3150m", "memory": "1536Mi"}},
            },
            "readinessProbe": True,
            "livenessProbe": False,
        },
        "config_maps": {},
        "secrets": [],
    },
]


# ── Unit Tests (no LLM) ──────────────────────────────────────

def test_resource_limits_pass():
    """Valid configs pass resource limits check."""
    result = _check_resource_limits(VALID_CONFIGS)
    assert result["status"] == "PASS"
    assert result["violations"] == 0
    print("  [OK] Resource limits: valid configs pass")


def test_resource_limits_fail():
    """Configs with resources.define=False fail."""
    bad = [{
        "vnf_name": "bad-vnf",
        "helm_values": {"resources": {"define": False}},
    }]
    result = _check_resource_limits(bad)
    assert result["status"] == "FAIL"
    assert result["violations"] > 0
    assert "define is False" in result["details"]
    print(f"  [OK] Resource limits: define=False flagged ({result['violations']} violations)")


def test_root_containers():
    """Root containers are flagged as WARNING."""
    result = _check_root_containers(VALID_CONFIGS)
    assert result["status"] == "WARNING"
    assert result["violations"] > 0
    assert "root" in result["details"].lower() or "privileged" in result["details"].lower()
    print(f"  [OK] Root containers: {result['violations']} warnings flagged")


def test_mandatory_fields_pass():
    """Valid configs pass mandatory fields check."""
    result = _check_mandatory_fields(VALID_CONFIGS)
    assert result["status"] == "PASS"
    print("  [OK] Mandatory fields: valid configs pass")


def test_mandatory_fields_fail():
    """Configs missing nfimage fail."""
    bad = [{"vnf_name": "no-image", "helm_values": {"start": {"x": True}}}]
    result = _check_mandatory_fields(bad)
    assert result["status"] == "FAIL"
    assert "nfimage" in result["details"]
    print(f"  [OK] Mandatory fields: missing nfimage flagged")


def test_image_tags_pass():
    """Pinned image tags pass."""
    result = _check_image_tags(VALID_CONFIGS)
    assert result["status"] == "PASS"
    print("  [OK] Image tags: pinned versions pass")


def test_image_tags_warn():
    """'latest' tag triggers warning."""
    bad = [{"vnf_name": "dev-vnf", "helm_values": {"nfimage": {"version": "latest"}}}]
    result = _check_image_tags(bad)
    assert result["status"] == "WARNING"
    print(f"  [OK] Image tags: 'latest' flagged as warning")


def test_probes_pass():
    """VNFs with readinessProbe pass."""
    result = _check_probes(VALID_CONFIGS)
    assert result["status"] == "PASS"
    print("  [OK] Health probes: valid configs pass")


def test_static_policies_aggregation():
    """All static checks run and return a dict."""
    checks = run_static_policies(VALID_CONFIGS)
    assert "resource_limits" in checks
    assert "root_containers" in checks
    assert "mandatory_fields" in checks
    assert "image_tags" in checks
    assert "health_probes" in checks
    print(f"  [OK] Static policies: {len(checks)} checks executed")


def test_decision_go():
    """All PASS/WARNING → GO."""
    checks = {
        "resource_limits": {"status": "PASS"},
        "root_containers": {"status": "WARNING", "details": "runs as root"},
        "mandatory_fields": {"status": "PASS"},
    }
    decision, reasons = determine_decision(checks)
    assert decision == "GO"
    print(f"  [OK] Decision: PASS+WARNING → GO")


def test_decision_no_go():
    """Any FAIL → NO_GO."""
    checks = {
        "resource_limits": {"status": "FAIL", "details": "missing limits"},
        "root_containers": {"status": "PASS"},
    }
    decision, reasons = determine_decision(checks)
    assert decision == "NO_GO"
    assert any("FAIL" in r for r in reasons)
    print(f"  [OK] Decision: FAIL → NO_GO (reasons: {len(reasons)})")


def test_agent_no_configs():
    """Agent returns error when no config_artifacts."""
    state = {"user_intent": "test"}
    result = policy_validator_agent(state)
    assert "error" in result
    assert "No configuration artifacts" in result["error"]
    print("  [OK] Agent returns error for missing config_artifacts")


# ── Integration Test (Ollama) ─────────────────────────────────

def test_agent_full_invocation():
    """Integration: invoke Policy Validator agent with valid configs."""
    state = {
        "user_intent": "Deploy a 5G core network for 500 UEs with 2 Gbps throughput",
        "topology": {
            "topology_id": "test-5gc-001",
            "connectivity": {
                "plmn": {"mcc": "001", "mnc": "01"},
                "slices": [{"sst": 1, "sd": "000001"}],
                "dnns": ["oai"],
            },
        },
        "resource_allocation": {"topology_id": "test-5gc-001"},
        "config_artifacts": VALID_CONFIGS,
        "messages": [],
        "anomaly_alerts": [],
        "execution_results": [],
        "phase": "pre_deployment",
    }

    print("\n  Calling Policy Validator (includes Ollama for compliance analysis)...")
    result = policy_validator_agent(state)

    print(f"  Result keys: {list(result.keys())}")

    if "error" in result and result.get("error"):
        print(f"  [WARN] Agent error: {result['error']}")

    assert "validation_report" in result, f"Missing 'validation_report', keys: {list(result.keys())}"
    report = result["validation_report"]

    print(f"\n  Topology ID: {report['topology_id']}")
    print(f"  Decision: {report['decision']}")

    # Print policy checks table
    print("\n  Policy Checks:")
    print("  " + "-" * 70)
    print(f"  {'Check':<20} {'Status':<10} {'Violations':<12} {'Details':<30}")
    print("  " + "-" * 70)
    for check_name, result_data in report["policy_checks"].items():
        print(f"  {check_name:<20} {result_data['status']:<10} {result_data.get('violations', 0):<12} "
              f"{str(result_data.get('details', ''))[:30]}")
    print("  " + "-" * 70)

    print(f"\n  Dry-run: {report['dry_run']}")
    print(f"  Conflicts: {report['conflicts']}")
    print(f"  Reasons ({len(report['reasons'])}):")
    for r in report["reasons"]:
        print(f"    - {r}")

    # Decision should be GO (valid configs, only WARNINGs)
    assert report["decision"] == "GO", f"Expected GO, got {report['decision']}"

    # Print agent message
    print(f"\n  Agent message:\n{result['messages'][0]['content']}")

    print("\n  [OK] Policy Validator agent produced valid report with GO decision")


if __name__ == "__main__":
    print("\n=== Phase 2.4 — Policy Validator Agent Tests ===\n")

    print("--- Unit Tests (no LLM) ---")
    test_resource_limits_pass()
    test_resource_limits_fail()
    test_root_containers()
    test_mandatory_fields_pass()
    test_mandatory_fields_fail()
    test_image_tags_pass()
    test_image_tags_warn()
    test_probes_pass()
    test_static_policies_aggregation()
    test_decision_go()
    test_decision_no_go()
    test_agent_no_configs()

    print("\n--- Integration Test (Ollama) ---")
    test_agent_full_invocation()

    print("\n=== All Policy Validator tests passed! ===")
