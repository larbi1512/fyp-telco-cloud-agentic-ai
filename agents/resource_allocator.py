"""
Resource Allocator Agent — The Accountant.

Calculates and assigns compute resources (CPU, memory) to each VNF
based on the topology blueprint and VNF resource profiles.

Pipeline position: 2nd in pre-deployment chain
  User Intent → Network Planner → [Resource Allocator] → VNF Configurator → ...

Input:  OrchestratorState with topology (from Network Planner)
Output: OrchestratorState with resource_allocation (ResourceAllocation)

Design decision:
  Resource CALCULATION is deterministic (math from profiles).
  The LLM is used only for OPTIMIZATION advice (affinity, co-location).
  This ensures reliability — the agent never hallucinates resource numbers.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import yaml

from config.settings import VNF_PROFILES_PATH
from core.llm_core import LLMCore
from core.state import OrchestratorState, ResourceAllocation

logger = logging.getLogger(__name__)


# ── Resource math helpers ─────────────────────────────────────

def _parse_cpu(cpu_str: str) -> int:
    """
    Parse Kubernetes CPU string to millicores.
    Examples: "100m" → 100, "1" → 1000, "2.5" → 2500
    """
    cpu_str = str(cpu_str).strip()
    if cpu_str.endswith("m"):
        return int(cpu_str[:-1])
    return int(float(cpu_str) * 1000)


def _parse_memory(mem_str: str) -> int:
    """
    Parse Kubernetes memory string to MiB.
    Examples: "256Mi" → 256, "1Gi" → 1024, "512Mi" → 512
    """
    mem_str = str(mem_str).strip()
    match = re.match(r"^([\d.]+)\s*(Mi|Gi|Ki|M|G|K)?$", mem_str)
    if not match:
        raise ValueError(f"Cannot parse memory string: '{mem_str}'")
    value = float(match.group(1))
    unit = match.group(2) or "Mi"
    multipliers = {"Ki": 1 / 1024, "K": 1 / 1024, "Mi": 1, "M": 1, "Gi": 1024, "G": 1024}
    return int(value * multipliers.get(unit, 1))


def _format_cpu(millicores: int) -> str:
    """Format millicores back to K8s string. 1500 → '1500m'."""
    return f"{millicores}m"


def _format_memory(mib: int) -> str:
    """Format MiB back to K8s string. 1024 → '1Gi', 256 → '256Mi'."""
    if mib >= 1024 and mib % 1024 == 0:
        return f"{mib // 1024}Gi"
    return f"{mib}Mi"


# ── Profile loading ───────────────────────────────────────────

def _load_profiles() -> dict[str, Any]:
    """Load VNF resource profiles from YAML config."""
    with open(VNF_PROFILES_PATH) as f:
        data = yaml.safe_load(f)
    return data.get("profiles", {})


# ── Capacity extraction from user intent / SLA ───────────────

def _extract_capacity(state: OrchestratorState) -> dict[str, Any]:
    """
    Extract capacity requirements from the state.
    Looks at parsed_intent, user_intent text, and topology SLA.
    """
    capacity = {
        "ue_count": 100,          # default
        "throughput_gbps": 1.0,   # default
        "sessions": 100,          # default
    }

    # Try parsed_intent first
    parsed = state.get("parsed_intent", {})
    if isinstance(parsed, dict):
        capacity["ue_count"] = parsed.get("ue_count", capacity["ue_count"])
        capacity["throughput_gbps"] = parsed.get("throughput_gbps", capacity["throughput_gbps"])

    # Extract numbers from user_intent text as fallback
    intent = state.get("user_intent", "")
    ue_match = re.search(r"(\d+)\s*(?:UE|user|subscriber|device)", intent, re.IGNORECASE)
    if ue_match:
        capacity["ue_count"] = int(ue_match.group(1))
    throughput_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:Gbps|gbps)", intent)
    if throughput_match:
        capacity["throughput_gbps"] = float(throughput_match.group(1))

    # Sessions ~ UE count (rough approximation)
    capacity["sessions"] = capacity["ue_count"]

    return capacity


# ── Core resource calculation (deterministic) ─────────────────

def calculate_vnf_resources(
    vnf_name: str,
    vnf_type: str,
    profiles: dict[str, Any],
    capacity: dict[str, Any],
) -> dict[str, dict[str, str]]:
    """
    Calculate CPU and memory for a single VNF using deterministic formulas.

    Uses: base resources + scaling formula from profiles.
    Returns: {"requests": {"cpu": "X", "memory": "Y"}, "limits": {"cpu": "X", "memory": "Y"}}
    """
    # Try exact name match first, then type-based match
    profile = profiles.get(vnf_name)
    if not profile:
        # Try matching by oai-<type>
        profile = profiles.get(f"oai-{vnf_type.lower()}")
    if not profile:
        # Try matching by type pattern in profile names
        for pname, pdata in profiles.items():
            if vnf_type.lower() in pname.lower():
                profile = pdata
                break
    if not profile:
        # Fallback: conservative defaults
        logger.warning("No profile found for %s (type=%s), using defaults", vnf_name, vnf_type)
        return {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "200m", "memory": "256Mi"},
        }

    # Base resources
    base_cpu = _parse_cpu(profile["base"]["cpu"])
    base_mem = _parse_memory(profile["base"]["memory"])

    # Apply scaling formula
    scaling = profile.get("scaling", {})
    scaled_cpu = base_cpu
    scaled_mem = base_mem

    ue_count = capacity.get("ue_count", 100)
    throughput = capacity.get("throughput_gbps", 1.0)
    sessions = capacity.get("sessions", 100)

    for scale_key, scale_values in scaling.items():
        scale_cpu = _parse_cpu(scale_values.get("cpu", "0m"))
        scale_mem = _parse_memory(scale_values.get("memory", "0Mi"))

        if "per_1000_ue" in scale_key:
            factor = ue_count / 1000
            scaled_cpu += int(scale_cpu * factor)
            scaled_mem += int(scale_mem * factor)
        elif "per_500_sessions" in scale_key:
            factor = sessions / 500
            scaled_cpu += int(scale_cpu * factor)
            scaled_mem += int(scale_mem * factor)
        elif "per_1_gbps" in scale_key:
            factor = throughput
            scaled_cpu += int(scale_cpu * factor)
            scaled_mem += int(scale_mem * factor)
        elif "per_100_ue" in scale_key:
            factor = ue_count / 100
            scaled_cpu += int(scale_cpu * factor)
            scaled_mem += int(scale_mem * factor)
        elif "per_10_vnfs" in scale_key:
            factor = 1  # assume ~10 VNFs
            scaled_cpu += int(scale_cpu * factor)
            scaled_mem += int(scale_mem * factor)
        elif "per_5000_subscribers" in scale_key:
            factor = ue_count / 5000
            scaled_cpu += int(scale_cpu * factor)
            scaled_mem += int(scale_mem * factor)
        elif "per_5_slices" in scale_key:
            slice_count = capacity.get("slice_count", 1)
            factor = slice_count / 5
            scaled_cpu += int(scale_cpu * factor)
            scaled_mem += int(scale_mem * factor)

    # Cap at max
    max_cpu = _parse_cpu(profile.get("max", {}).get("cpu", "8000m"))
    max_mem = _parse_memory(profile.get("max", {}).get("memory", "8Gi"))
    scaled_cpu = min(scaled_cpu, max_cpu)
    scaled_mem = min(scaled_mem, max_mem)

    # Limits = 150% of requests (standard headroom)
    limit_cpu = min(int(scaled_cpu * 1.5), max_cpu)
    limit_mem = min(int(scaled_mem * 1.5), max_mem)

    return {
        "requests": {"cpu": _format_cpu(scaled_cpu), "memory": _format_memory(scaled_mem)},
        "limits": {"cpu": _format_cpu(limit_cpu), "memory": _format_memory(limit_mem)},
    }


# ── Validation ────────────────────────────────────────────────

def _validate_allocation(allocation: dict[str, Any]) -> list[str]:
    """Validate the resource allocation for completeness."""
    issues = []
    vnfs = allocation.get("vnfs", [])
    if not vnfs:
        issues.append("No VNFs in allocation")
        return issues

    for vnf in vnfs:
        name = vnf.get("name", "unknown")
        res = vnf.get("resources", {})
        if not res:
            issues.append(f"VNF '{name}' has no resources assigned")
            continue
        for section in ("requests", "limits"):
            if section not in res:
                issues.append(f"VNF '{name}' missing '{section}'")
            else:
                if "cpu" not in res[section]:
                    issues.append(f"VNF '{name}' missing {section}.cpu")
                if "memory" not in res[section]:
                    issues.append(f"VNF '{name}' missing {section}.memory")
    return issues


# ── Agent function (LangGraph node) 

def resource_allocator_agent(state: OrchestratorState) -> dict[str, Any]:
    """
    LangGraph node: Resource Allocator.

    Reads:  state["topology"]
    Writes: state["resource_allocation"], state["messages"], state["current_agent"]
    """
    logger.info("Resource Allocator Agent: starting")

    topology = state.get("topology")
    if not topology:
        return {
            "error": "No topology provided — run Network Planner first",
            "messages": [{"role": "agent", "content": " No topology available for resource allocation."}],
            "current_agent": "resource_allocator",
        }

    # Load profiles
    profiles = _load_profiles()
    logger.info("Loaded %d VNF resource profiles", len(profiles))

    # Extract capacity requirements
    capacity = _extract_capacity(state)
    # Enrich capacity with real slice count from topology connectivity
    slice_count = len(topology.get("connectivity", {}).get("slices", []))
    capacity["slice_count"] = max(slice_count, 1)
    logger.info("Capacity requirements: %s", capacity)

    # Calculate resources for each VNF (deterministic)
    enriched_vnfs = []
    total_cpu = 0
    total_mem = 0

    for vnf in topology.get("vnfs", []):
        vnf_name = vnf.get("name", "")
        vnf_type = vnf.get("type", "")
        replicas = vnf.get("replicas", 1)

        resources = calculate_vnf_resources(vnf_name, vnf_type, profiles, capacity)

        enriched_vnf = {
            "name": vnf_name,
            "type": vnf_type,
            "replicas": replicas,
            "resources": resources,
            "interfaces": vnf.get("interfaces", []),
            "supported_slices": vnf.get("supported_slices", []),
            "affinity": {},  # will be enriched by LLM below
        }
        enriched_vnfs.append(enriched_vnf)

        # Running totals (per replica)
        req_cpu = _parse_cpu(resources["requests"]["cpu"])
        req_mem = _parse_memory(resources["requests"]["memory"])
        total_cpu += req_cpu * replicas
        total_mem += req_mem * replicas

    # Use LLM for affinity/co-location recommendations (non-critical)
    try:
        llm = LLMCore()
        llm_result = llm.invoke(
            "resource_allocator",
            {
                "topology": topology,
                "vnf_profiles": {k: {
                    "base": v.get("base"),
                    "scaling_type": v.get("scaling_type"),
                    "category": v.get("category"),
                } for k, v in profiles.items() if "base" in v},
                "cluster_capacity": "Remote Kubernetes cluster (capacity will be validated at deployment)",
                "capacity_requirements": capacity,
            },
            expect_json=True,
        )
        # Extract affinity recommendations from LLM if available
        if isinstance(llm_result, dict):
            llm_vnfs = {v.get("name"): v for v in llm_result.get("vnfs", [])}
            for vnf in enriched_vnfs:
                llm_vnf = llm_vnfs.get(vnf["name"], {})
                if "affinity" in llm_vnf and llm_vnf["affinity"]:
                    vnf["affinity"] = llm_vnf["affinity"]
            logger.info("LLM provided affinity recommendations")
    except Exception as e:
        logger.warning("LLM optimization call failed (non-critical): %s", e)
        # Continue without LLM — deterministic allocation is sufficient

    # Build allocation
    allocation: ResourceAllocation = {
        "topology_id": topology.get("topology_id", "unknown"),
        "vnfs": enriched_vnfs,
        "node_assignments": {},  # populated in Phase 4 with real cluster data
    }

    # Validate
    issues = _validate_allocation(allocation)
    if issues:
        logger.warning("Allocation validation issues: %s", issues)

    # Summary message
    summary_lines = [
        f"**Resources Allocated** (Topology: {allocation['topology_id']})",
        f"   Total: {_format_cpu(total_cpu)} CPU, {_format_memory(total_mem)} Memory",
        f"   UE capacity: {capacity['ue_count']}, Throughput: {capacity['throughput_gbps']} Gbps",
        "",
        "   | VNF | Replicas | CPU (req/lim) | Memory (req/lim) |",
        "   |-----|----------|---------------|------------------|",
    ]
    for vnf in enriched_vnfs:
        res = vnf["resources"]
        summary_lines.append(
            f"   | {vnf['name']} | {vnf['replicas']} | "
            f"{res['requests']['cpu']}/{res['limits']['cpu']} | "
            f"{res['requests']['memory']}/{res['limits']['memory']} |"
        )
    if issues:
        summary_lines.append(f"\n Issues: {'; '.join(issues)}")

    summary = "\n".join(summary_lines)
    logger.info("Resource Allocator: allocated resources for %d VNFs", len(enriched_vnfs))

    return {
        "resource_allocation": allocation,
        "current_agent": "resource_allocator",
        "messages": [{"role": "agent", "content": summary}],
    }