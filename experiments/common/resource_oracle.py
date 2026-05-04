"""
Resource Oracle — ground truth for the "Resource accuracy vs. optimal" metric.

Reads the calibrated profiles in config/vnf_resource_profiles.yaml and applies
the per-VNF scaling formulas to an intent's structured_features block. The
result is a per-VNF dict of optimal CPU (millicores) and memory (Mi).

Each system under test (MAS, B1, B3, B4) emits requested resources for its
generated VNFs; the metrics layer compares those to the oracle's output.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from config.settings import PROJECT_ROOT


PROFILES_PATH = PROJECT_ROOT / "config" / "vnf_resource_profiles.yaml"

# Maps a profile's scaling-unit key to the structured_features field that
# supplies the unit count.
SCALING_FEATURE_MAP: dict[str, str] = {
    "per_1000_ue": "ue_count",
    "per_500_sessions": "session_count",
    "per_5000_subscribers": "subscriber_count",
    "per_1_gbps": "throughput_gbps",
    "per_5_slices": "_slice_count",
    "per_10_vnfs": "_vnf_count",
    "per_100_ue": "ue_count",
}


@lru_cache(maxsize=1)
def _load_profiles() -> dict[str, Any]:
    with open(PROFILES_PATH) as f:
        return yaml.safe_load(f)["profiles"]


def _parse_cpu_millicores(value: str | int | float) -> int:
    """Parse Kubernetes CPU strings ('100m', '2', '0.5') into millicores."""
    s = str(value).strip()
    if s.endswith("m"):
        return int(s[:-1])
    return int(float(s) * 1000)


def _parse_memory_mi(value: str | int | float) -> int:
    """Parse Kubernetes memory strings ('256Mi', '2Gi', '512') into MiB."""
    s = str(value).strip()
    if s.endswith("Mi"):
        return int(s[:-2])
    if s.endswith("Gi"):
        return int(float(s[:-2]) * 1024)
    if s.endswith("Ki"):
        return max(1, int(float(s[:-2]) / 1024))
    return int(float(s))


def _scaling_unit_count(scale_key: str, features: dict[str, Any]) -> float:
    """Compute the scaling-unit count for a given profile key."""
    feat_name = SCALING_FEATURE_MAP.get(scale_key)
    if feat_name == "_slice_count":
        return float(len(features.get("slices", []) or []))
    if feat_name == "_vnf_count":
        # NRF scales with total registered VNFs; rough estimate from typical
        # 5G core size, refined later if a topology dict is available.
        return 8.0
    if feat_name is None:
        return 0.0
    return float(features.get(feat_name, 0) or 0)


def compute_optimal_for_profile(
    profile_name: str,
    features: dict[str, Any],
    *,
    ha_replica_factor: float = 1.5,
) -> dict[str, int]:
    """
    Apply the scaling formula for a single VNF profile and return its optimal
    CPU (millicores) and memory (Mi).

    Output is capped at the profile's `max` values. If `features.ha_required`
    is True, base+scaled CPU/mem are bumped by `ha_replica_factor` (per-replica
    sizing stays the same; HA implies more replicas, but Metric 3 evaluates
    per-VNF allocation, so we scale up the per-replica request slightly to
    represent a more conservative sizing).
    """
    profiles = _load_profiles()
    profile = profiles.get(profile_name)
    if profile is None:
        raise KeyError(f"Unknown VNF profile: {profile_name}")

    base_cpu = _parse_cpu_millicores(profile["base"]["cpu"])
    base_mem = _parse_memory_mi(profile["base"]["memory"])
    max_cpu = _parse_cpu_millicores(profile["max"]["cpu"])
    max_mem = _parse_memory_mi(profile["max"]["memory"])

    scaling_section = profile.get("scaling", {})
    if not scaling_section:
        cpu, mem = base_cpu, base_mem
    else:
        scale_key = next(iter(scaling_section))
        scale_cpu = _parse_cpu_millicores(scaling_section[scale_key]["cpu"])
        scale_mem = _parse_memory_mi(scaling_section[scale_key]["memory"])

        m = re.match(r"per_(\d+(?:\.\d+)?)_", scale_key)
        divisor = float(m.group(1)) if m else 1.0
        units = _scaling_unit_count(scale_key, features)

        cpu = base_cpu + (units / divisor) * scale_cpu
        mem = base_mem + (units / divisor) * scale_mem

    if features.get("ha_required"):
        cpu *= ha_replica_factor
        mem *= ha_replica_factor

    return {
        "cpu_m": int(round(min(cpu, max_cpu))),
        "memory_mi": int(round(min(mem, max_mem))),
    }


# Maps a VNF type (as used in topology["vnfs"][i]["type"]) to a profile name.
# Mirrors agents/deployer.py CHART_MAP for consistency.
VNF_TYPE_TO_PROFILE: dict[str, str] = {
    "amf": "oai-amf",
    "smf": "oai-smf",
    "nrf": "oai-nrf",
    "ausf": "oai-ausf",
    "udm": "oai-udm",
    "udr": "oai-udr",
    "nssf": "oai-nssf",
    "upf": "oai-upf",
}


def compute_optimal_resources(features: dict[str, Any]) -> dict[str, dict[str, int]]:
    """
    Return optimal {cpu_m, memory_mi} for every standard VNF type in the
    5G core, given an intent's structured_features block.

    The metrics layer uses this as the per-VNF reference when scoring
    "Resource accuracy (% vs optimal)". Systems that don't deploy a given
    VNF simply have nothing to compare against for that profile.
    """
    return {
        vnf_type: compute_optimal_for_profile(profile_name, features)
        for vnf_type, profile_name in VNF_TYPE_TO_PROFILE.items()
    }


def parse_vnf_resources(vnf: dict[str, Any]) -> dict[str, int] | None:
    """
    Extract a single VNF's requested resources from a topology entry into the
    same {cpu_m, memory_mi} shape. Returns None if requests are missing.

    Handles both the topology-level resources schema (state.VNFSpec) and the
    Helm-values-level resources.requests.nf schema used by config_artifacts.
    """
    res = vnf.get("resources") or {}
    req = res.get("requests") or {}
    nf = req.get("nf") if "nf" in req else req

    cpu = nf.get("cpu") if isinstance(nf, dict) else None
    mem = nf.get("memory") if isinstance(nf, dict) else None

    if cpu is None or mem is None:
        return None
    return {
        "cpu_m": _parse_cpu_millicores(cpu),
        "memory_mi": _parse_memory_mi(mem),
    }
