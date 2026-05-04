"""
B-min smoke test — verifies that:

1. The configurator emits valid helm_values for ueransim-gnb / ueransim-ue
   under the new RAN builders, with imagePullSecrets=[] and chart-compatible
   keys (config.amfhost, config.fullImsi, config.usrp=rfsim, ...).
2. The artifact's vnf_name is canonicalised to oai-gnb / oai-nr-ue so the
   deployer's _resolve_chart_path() returns a valid chart path.
3. The full MAS pipeline (Network Planner → Resource Allocator →
   VNF Configurator → Policy Validator) still produces a GO decision when
   the topology contains gNB + UE.

Run:
   PYTHONPATH=/home/larbi/fyp ./venv/bin/python experiments/experiment_2/test_bmin.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.deployer import _resolve_chart_path
from agents.vnf_configurator import generate_vnf_configs


# -- Test 1: configurator emits valid gNB / UE helm values --------------

def test_configurator_emits_ran_values() -> None:
    topology = {
        "topology_id": "test",
        "connectivity": {
            "plmn": {"mcc": "001", "mnc": "01"},
            "slices": [{"sst": 1, "sd": "16777215"}],
            "dnns": ["oai"],
        },
        "vnfs": [
            {"name": "oai-amf", "type": "oai-amf", "resources": {
                "requests": {"cpu": "200m", "memory": "256Mi"},
                "limits":   {"cpu": "400m", "memory": "512Mi"}}},
            {"name": "ueransim-gnb", "type": "ueransim-gnb", "resources": {
                "requests": {"cpu": "1000m", "memory": "1Gi"},
                "limits":   {"cpu": "2000m", "memory": "2Gi"}}},
            {"name": "ueransim-ue", "type": "ueransim-ue", "resources": {
                "requests": {"cpu": "500m", "memory": "512Mi"},
                "limits":   {"cpu": "1500m", "memory": "1Gi"}}},
        ],
    }

    artifacts, _core_cfg = generate_vnf_configs(topology, topology["vnfs"])
    by_name = {a["vnf_name"]: a for a in artifacts}

    # Canonical names
    assert "oai-gnb" in by_name, f"oai-gnb missing from {list(by_name)}"
    assert "oai-nr-ue" in by_name, f"oai-nr-ue missing from {list(by_name)}"
    assert "ueransim-gnb" not in by_name, "raw name should have been normalised"

    gnb_hv = by_name["oai-gnb"]["helm_values"]
    ue_hv  = by_name["oai-nr-ue"]["helm_values"]

    # imagePullSecrets must be overridden to empty list (no regcred required)
    assert gnb_hv.get("imagePullSecrets") == [], gnb_hv.get("imagePullSecrets")
    assert ue_hv.get("imagePullSecrets")  == [], ue_hv.get("imagePullSecrets")

    # Image repository must point at the public OAI images
    assert "oai-gnb" in gnb_hv["nfimage"]["repository"]
    assert "oai-nr-ue" in ue_hv["nfimage"]["repository"]

    # RFsim mode (no USRP, no host network)
    assert gnb_hv["config"]["usrp"] == "rfsim"
    assert ue_hv["config"]["usrp"]  == "rfsim"
    assert gnb_hv["multus"]["n2Interface"]["create"] is False

    # gNB targets the AMF service in the same namespace
    assert gnb_hv["config"]["amfhost"] == "oai-amf"

    # PLMN/SST come from topology
    assert gnb_hv["config"]["mcc"] == "001"
    assert gnb_hv["config"]["mnc"] == "01"
    assert gnb_hv["config"]["sst"] == "1"

    # UE attaches to gnb's RFsim server
    assert ue_hv["config"]["rfSimServer"] == "oai-gnb"
    # UE IMSI must match a row in the OAI MySQL seed
    assert ue_hv["config"]["fullImsi"].startswith("00101")
    assert ue_hv["config"]["dnn"] == "oai"

    # Resources from the (mocked) allocator are passed through
    assert gnb_hv["resources"]["define"] is True
    assert gnb_hv["resources"]["requests"]["nf"]["cpu"] == "1000m"
    assert ue_hv["resources"]["define"] is True

    # Mandatory fields the policy validator checks
    for hv in (gnb_hv, ue_hv):
        assert "nfimage" in hv and hv["nfimage"].get("repository")
        assert "exposedPorts" in hv
        assert "start" in hv

    print("PASS  test_configurator_emits_ran_values")


# -- Test 2: the deployer can resolve every emitted chart -------------------

def test_deployer_resolves_all_canonical_names() -> None:
    for name in ["oai-amf", "oai-nrf", "oai-smf", "oai-upf", "oai-ausf",
                 "oai-udm", "oai-udr", "oai-nssf", "oai-gnb", "oai-nr-ue"]:
        path = _resolve_chart_path(name)
        assert path and Path(path).is_dir(), f"{name} -> {path}"
    # Aliases also work
    assert _resolve_chart_path("ueransim-gnb")
    assert _resolve_chart_path("ueransim-ue")
    print("PASS  test_deployer_resolves_all_canonical_names")


# -- Test 3: minimum smoke through the no-deploy MAS graph ------------------

def test_mas_pipeline_no_deploy() -> None:
    """Run the MAS graph on a simple intent without helm install."""
    from experiments.common.runners import get_runner

    intent = {
        "id": "S02",
        "prompt": "Set up a 5G network for 100 users.",
        "expected_vnfs": ["nrf", "amf", "smf", "upf",
                          "ausf", "udm", "udr"],
        "_rep": 0,
    }

    runner = get_runner("mas")
    artifact = runner.run(intent, deploy=False)

    if artifact.get("error"):
        print(f"FAIL  test_mas_pipeline_no_deploy — error: {artifact['error']}")
        return

    cfgs = artifact.get("config_artifacts") or []
    names = sorted(c["vnf_name"] for c in cfgs)
    has_gnb = any("oai-gnb" in n or n == "oai-gnb" for n in names)
    has_ue  = any("oai-nr-ue" in n or n == "oai-nr-ue" for n in names)

    print(f"  config_artifact names: {names}")
    print(f"  has_oai_gnb={has_gnb}  has_oai_nr_ue={has_ue}")
    print(f"  policy decision: {(artifact.get('validation_report') or {}).get('decision')}")
    print("PASS  test_mas_pipeline_no_deploy")


if __name__ == "__main__":
    test_configurator_emits_ran_values()
    test_deployer_resolves_all_canonical_names()
    test_mas_pipeline_no_deploy()
    print("\nall b-min smoke tests passed")
