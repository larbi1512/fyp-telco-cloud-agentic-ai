"""
Network Planner Agent — The Architect.

Interprets user intent and designs a 5G network topology blueprint.

Pipeline position: 1st in pre-deployment chain
  User Intent → [Network Planner] → Resource Allocator → ...

Input:  OrchestratorState with user_intent
Output: OrchestratorState with topology (TopologyBlueprint)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import yaml

from config.settings import VNF_PROFILES_PATH
from core.llm_core import LLMCore
from core.state import OrchestratorState, TopologyBlueprint

logger = logging.getLogger(__name__)

# VNF catalog built from resource profiles 

def _load_vnf_catalog() -> list[dict[str, str]]:
    """Load available VNF types from the resource profiles config."""
    with open(VNF_PROFILES_PATH) as f:
        data = yaml.safe_load(f)
    catalog = []
    for vnf_name, profile in data.get("profiles", {}).items():
        catalog.append({
            "name": vnf_name,
            "description": profile.get("description", ""),
            "category": profile.get("category", "unknown"),
        })
    return catalog


# Topology validation 

def _validate_topology(topology: dict[str, Any]) -> list[str]:
    """
    Validate the LLM-generated topology for structural correctness.
    Returns a list of issues (empty = valid).
    """
    issues = []

    if "topology_id" not in topology:
        topology["topology_id"] = str(uuid.uuid4())[:8]

    if "vnfs" not in topology or not isinstance(topology.get("vnfs"), list):
        issues.append("Missing or invalid 'vnfs' list")
        return issues

    if len(topology["vnfs"]) == 0:
        issues.append("Topology has no VNFs")

    # 2. Connectivity Block — apply defaults when missing
    if "connectivity" not in topology:
        topology["connectivity"] = {
            "plmn": {"mcc": "001", "mnc": "01"},
            "slices": [{"sst": 1, "sd": "000001", "description": "default eMBB"}],
            "dnns": ["internet"],
        }
    conn = topology["connectivity"]

    # Validate / default PLMN
    plmn = conn.get("plmn", {})
    if not plmn.get("mcc") or not plmn.get("mnc"):
        conn["plmn"] = {"mcc": "001", "mnc": "01"}

    # Validate / default S-NSSAI Slices
    slices = conn.get("slices", [])
    if not slices:
        conn["slices"] = [{"sst": 1, "sd": "000001", "description": "default eMBB"}]
    else:
        for i, s in enumerate(slices):
            if "sst" not in s:
                issues.append(f"Slice at index {i} missing 'sst'")

    # Validate / default DNNs
    if not conn.get("dnns") or not isinstance(conn.get("dnns"), list):
        conn["dnns"] = ["internet"]

    vnf_names = set()
    for i, vnf in enumerate(topology["vnfs"]):
        vnf_type = vnf.get("type", "").lower()
        name = vnf.get("name", f"index_{i}")
        if "name" not in vnf:
            issues.append(f"VNF at index {i} missing 'name'")
        else:
            if vnf["name"] in vnf_names:
                issues.append(f"Duplicate VNF name: {vnf['name']}")
            vnf_names.add(vnf["name"])

        if "type" not in vnf:
            issues.append(f"VNF '{vnf.get('name', i)}' missing 'type'")

        # Default supported_slices for slice-aware VNFs
        if vnf_type in ("oai-amf", "oai-smf", "oai-upf", "amf", "smf", "upf"):
            if not vnf.get("supported_slices"):
                vnf["supported_slices"] = topology["connectivity"]["slices"]

        # Default replicas if not set
        if "replicas" not in vnf:
            vnf["replicas"] = 1

        # Ensure interfaces is a list
        if "interfaces" not in vnf:
            vnf["interfaces"] = []

    # Validate connections reference existing VNFs
    for conn in topology.get("connections", []):
        if conn.get("from") not in vnf_names:
            issues.append(f"Connection references unknown VNF: {conn.get('from')}")
        if conn.get("to") not in vnf_names:
            issues.append(f"Connection references unknown VNF: {conn.get('to')}")

    # Ensure SLA block exists with defaults
    if "sla" not in topology:
        topology["sla"] = {
            "max_latency_ms": 20,
            "min_throughput_gbps": 1.0,
            "min_availability_pct": 99.9,
        }

    return issues


# Core 5G dependency knowledge 

CORE_5G_DEPENDENCIES = {
    "amf": ["nrf"],
    "smf": ["nrf", "amf"],
    "upf": ["smf", "nrf"],
    "ausf": ["nrf", "udm"],
    "udm": ["nrf", "udr"],
    "udr": ["nrf"],
    "nssf": ["nrf"],
    "nrf": [],
}


def _ensure_dependencies(topology: dict[str, Any]) -> list[str]:
    """
    Check that all required VNF dependencies are present in the topology.
    Returns list of missing dependencies as warnings.
    """
    warnings = []
    vnf_types = {vnf.get("type", "").lower().replace("oai-", "") for vnf in topology.get("vnfs", [])}

    for vnf in topology.get("vnfs", []):
        vnf_type = vnf.get("type", "").lower().replace("oai-", "")
        required = CORE_5G_DEPENDENCIES.get(vnf_type, [])
        for dep in required:
            if dep not in vnf_types:
                warnings.append(
                    f"VNF '{vnf['name']}' (type={vnf_type}) requires '{dep}' "
                    f"but it is not in the topology"
                )
    return warnings


#  Agent function (LangGraph node) 

def network_planner_agent(state: OrchestratorState) -> dict[str, Any]:
    """
    LangGraph node: Network Planner.

    Reads:  state["user_intent"]
    Writes: state["topology"], state["messages"], state["current_agent"]
    """
    logger.info("Network Planner Agent: starting")

    user_intent = state.get("user_intent", "")
    if not user_intent:
        return {
            "error": "No user intent provided",
            "messages": [{"role": "agent", "content": " No user intent provided to the Network Planner."}],
            "current_agent": "network_planner",
        }

    # Load VNF catalog
    vnf_catalog = _load_vnf_catalog()
    logger.info("Loaded VNF catalog: %d VNFs available", len(vnf_catalog))

    # Build infrastructure constraints (placeholder — will be replaced
    # by real cluster info from K8s client in Phase 4)
    infra_constraints = {
        "cluster_type": "remote_kubernetes",
        "notes": "Resources will be validated by the Resource Allocator agent against the live cluster.",
    }

    # Invoke LLM
    llm = LLMCore()
    result = llm.invoke(
        "network_planner",
        {
            "intent": user_intent,
            "vnf_catalog": vnf_catalog,
            "infra_constraints": infra_constraints,
        },
        expect_json=True,
    )

    # Handle LLM failure
    if isinstance(result, str):
        logger.error("Network Planner: LLM returned raw text instead of JSON")
        return {
            "error": f"LLM failed to produce valid JSON: {result[:200]}",
            "messages": [{"role": "agent", "content": f" Network Planner failed to parse LLM output."}],
            "current_agent": "network_planner",
        }

    # Validate topology structure
    issues = _validate_topology(result)
    if issues:
        logger.warning("Topology validation issues: %s", issues)
        return {
            "topology": result,
            "error": f"Topology has issues: {'; '.join(issues)}",
            "messages": [
                {"role": "agent", "content": f"Topology generated with issues: {'; '.join(issues)}"}
            ],
            "current_agent": "network_planner",
        }

    # Check dependencies
    dep_warnings = _ensure_dependencies(result)
    if dep_warnings:
        logger.warning("Dependency warnings: %s", dep_warnings)

    # Build topology object
    topology: TopologyBlueprint = {
        "topology_id": result["topology_id"],
        "connectivity": result.get("connectivity", {}),
        "vnfs": result["vnfs"],
        "connections": result.get("connections", []),
        "sla": result.get("sla", {}),
    }

    # Summary message
    vnf_names = [v["name"] for v in topology["vnfs"]]
    summary = (
        f" **Network Topology Designed** (ID: {topology['topology_id']})\n"
        f"   VNFs: {', '.join(vnf_names)}\n"
        f"   Connections: {len(topology['connections'])}\n"
        f"   SLA: latency<{topology['sla'].get('max_latency_ms', '?')}ms, "
        f"throughput≥{topology['sla'].get('min_throughput_gbps', '?')}Gbps"
    )
    if dep_warnings:
        summary += f"\n   Warnings: {'; '.join(dep_warnings)}"

    logger.info("Network Planner: topology designed with %d VNFs", len(vnf_names))

    return {
        "topology": topology,
        "current_agent": "network_planner",
        "messages": [{"role": "agent", "content": summary}],
    }
