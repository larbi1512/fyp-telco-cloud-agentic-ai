"""
Phase 2.4 — Policy Validator Agent Tests.

Covers the deterministic Python checks (used as a fallback), the OPA Rego
bundle, the real Kubernetes server-side dry-run, the GO/NO_GO decision
logic, and the LangGraph agent function.

Run:  cd ~/fyp && python tests/test_policy_validator.py
"""

import os
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
    _python_fallback,
    _opa_violations_to_check_dict,
    run_static_policies,
    determine_decision,
    policy_validator_agent,
    OPA_RULES,
)
from infra.opa_client import OPAClient, OPABinaryNotFound
from infra.k8s_dry_run import K8sDryRunner


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
    """run_static_policies returns the full OPA_RULES check dict."""
    checks = run_static_policies(VALID_CONFIGS)
    for rule in OPA_RULES:
        assert rule in checks, f"Missing rule: {rule}"
    print(f"  [OK] Static policies: {len(checks)} checks executed")


# ── OPA tests (skip if binary missing) ────────────────────────

def test_opa_client_evaluate():
    """OPA returns FAIL violations for a deliberately broken config."""
    try:
        client = OPAClient()
    except OPABinaryNotFound:
        print("  [SKIP] OPA binary not installed")
        return

    bad = [{
        "vnf_name": "bad",
        "helm_values": {
            "nfimage": {"version": "latest"},
            "resources": {"define": False},
            "readinessProbe": False,
            "livenessProbe": False,
        },
    }]
    violations = client.violations({"config_artifacts": bad})
    rules = {v["rule"] for v in violations}
    assert "resource_limits" in rules, f"Expected resource_limits FAIL, got {rules}"
    assert "image_tags" in rules, f"Expected image_tags WARNING, got {rules}"
    print(f"  [OK] OPA evaluated bad config → {len(violations)} violations across {len(rules)} rule(s)")


def test_opa_violations_to_check_dict():
    """OPA violations bucket correctly into the legacy policy_checks shape."""
    violations = [
        {"rule": "resource_limits", "severity": "FAIL", "vnf": "bad",
         "message": "bad: resources.define is False"},
        {"rule": "image_tags", "severity": "WARNING", "vnf": "bad",
         "message": "bad: using non-pinned image tag 'latest'"},
    ]
    checks = _opa_violations_to_check_dict(violations, checked=1)
    assert checks["resource_limits"]["status"] == "FAIL"
    assert checks["resource_limits"]["violations"] == 1
    assert checks["image_tags"]["status"] == "WARNING"
    assert checks["mandatory_fields"]["status"] == "PASS"
    print("  [OK] OPA violations bucketed into policy_checks dict")


def test_opa_fallback_when_binary_missing(monkeypatch_env=True):
    """Pointing OPA_BINARY to a nonexistent path falls back to Python checks."""
    old_env = os.environ.get("OPA_BINARY")
    old_repo_bin = Path(__file__).resolve().parent.parent / "bin" / "opa"
    moved = False
    os.environ["OPA_BINARY"] = "/nonexistent/opa"
    if old_repo_bin.exists():
        old_repo_bin.rename(old_repo_bin.with_suffix(".bak"))
        moved = True
    try:
        checks = run_static_policies(VALID_CONFIGS)
        # Python fallback only knows these five rules
        for rule in ("resource_limits", "root_containers", "mandatory_fields",
                     "image_tags", "health_probes"):
            assert rule in checks, f"Missing fallback rule: {rule}"
        print("  [OK] OPA missing → Python fallback produced policy_checks")
    finally:
        if old_env is None:
            os.environ.pop("OPA_BINARY", None)
        else:
            os.environ["OPA_BINARY"] = old_env
        if moved:
            old_repo_bin.with_suffix(".bak").rename(old_repo_bin)


# ── K8s dry-run tests ─────────────────────────────────────────

def test_k8s_dry_runner_skips_when_unreachable():
    """With an empty KUBECONFIG path, the runner returns SKIPPED."""
    old_kc = os.environ.get("KUBECONFIG")
    old_kcp = os.environ.get("KUBECONFIG_PATH")
    os.environ["KUBECONFIG"] = "/nonexistent/kubeconfig"
    os.environ["KUBECONFIG_PATH"] = "/nonexistent/kubeconfig"
    try:
        runner = K8sDryRunner(VALID_CONFIGS)
        result = runner.run()
        assert result["status"] == "SKIPPED", f"Expected SKIPPED, got {result['status']}"
        assert result["reason"], "Expected a non-empty reason"
        print(f"  [OK] K8s dry-run skipped gracefully: {result['reason']}")
    finally:
        if old_kc is None:
            os.environ.pop("KUBECONFIG", None)
        else:
            os.environ["KUBECONFIG"] = old_kc
        if old_kcp is None:
            os.environ.pop("KUBECONFIG_PATH", None)
        else:
            os.environ["KUBECONFIG_PATH"] = old_kcp


def test_determine_decision_with_dry_run_failed():
    """Dry-run FAILED causes NO_GO."""
    checks = {"resource_limits": {"status": "PASS"}}
    dry_run = {
        "status": "FAILED",
        "reason": "1 manifest error(s)",
        "errors": [{"message": "Service/foo: Invalid value"}],
    }
    decision, reasons = determine_decision(checks, None, dry_run)
    assert decision == "NO_GO"
    assert any("k8s_dry_run" in r for r in reasons)
    print("  [OK] Decision: dry-run FAILED → NO_GO")


def test_determine_decision_with_dry_run_skipped():
    """Dry-run SKIPPED noted but doesn't block."""
    checks = {"resource_limits": {"status": "PASS"}}
    dry_run = {"status": "SKIPPED", "reason": "cluster unreachable", "errors": []}
    decision, reasons = determine_decision(checks, None, dry_run)
    assert decision == "GO"
    assert any("SKIPPED" in r for r in reasons)
    print("  [OK] Decision: dry-run SKIPPED → GO (with NOTE)")


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

    dry_run = report["dry_run"]
    print(f"\n  Dry-run: status={dry_run.get('status')} "
          f"validated={dry_run.get('validated', 0)} "
          f"errors={len(dry_run.get('errors', []))}")
    print(f"  Conflicts: {report['conflicts']}")
    print(f"  Reasons ({len(report['reasons'])}):")
    for r in report["reasons"]:
        print(f"    - {r}")

    # Sanity-check structure rather than exact GO/NO_GO outcome: real dry-run
    # results depend on whether the cluster already has these VNFs deployed.
    assert dry_run.get("status") in ("SUCCESS", "FAILED", "SKIPPED"), \
        f"Unexpected dry_run status: {dry_run.get('status')}"
    assert report["decision"] in ("GO", "NO_GO"), \
        f"Unexpected decision: {report['decision']}"

    # Print agent message
    print(f"\n  Agent message:\n{result['messages'][0]['content']}")

    print(f"\n  [OK] Policy Validator agent produced valid report (decision={report['decision']})")


if __name__ == "__main__":
    print("\n=== Phase 2.4 — Policy Validator Agent Tests ===\n")

    print("--- Python fallback unit tests ---")
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

    print("\n--- OPA tests ---")
    test_opa_client_evaluate()
    test_opa_violations_to_check_dict()
    test_opa_fallback_when_binary_missing()

    print("\n--- K8s dry-run tests ---")
    test_k8s_dry_runner_skips_when_unreachable()
    test_determine_decision_with_dry_run_failed()
    test_determine_decision_with_dry_run_skipped()

    print("\n--- Integration Test (real cluster + Ollama) ---")
    test_agent_full_invocation()

    print("\n=== All Policy Validator tests passed! ===")
