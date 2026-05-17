"""
Experiment 4 helper — seed a topology snapshot into Redis from the live
cluster. Used when no full pre-deployment run has populated Redis but the
OAI 5G core is already deployed (the common case for runtime experiments).

Discovers VNFs from kubectl pods, writes a minimal topology + resource
allocation under the standard ``topology:latest`` and
``resource_allocation:latest`` keys.

Usage:
    python experiments/experiment_4/seed_topology.py [--namespace oai-5g]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from infra.redis_client import RedisClient  # noqa: E402

# Map pod-name prefix → 3GPP VNF type
VNF_PREFIXES = {
    "oai-amf": "AMF",
    "oai-smf": "SMF",
    "oai-upf": "UPF",
    "oai-nrf": "NRF",
    "oai-nssf": "NSSF",
    "oai-ausf": "AUSF",
    "oai-udm": "UDM",
    "oai-udr": "UDR",
    "oai-gnb": "gNB",
    "oai-nr-ue": "UE",
}


def _kubectl_pods(namespace: str) -> list[dict]:
    proc = subprocess.run(
        ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"kubectl get pods failed: {proc.stderr}")
    return json.loads(proc.stdout).get("items", [])


def _vnf_from_pod_name(pod_name: str) -> tuple[str, str] | None:
    """Return (canonical_vnf_name, type) or None if not a known VNF."""
    for prefix, vnf_type in VNF_PREFIXES.items():
        if pod_name.startswith(prefix):
            return prefix, vnf_type
    return None


def build_topology(namespace: str) -> tuple[dict, dict]:
    pods = _kubectl_pods(namespace)
    seen: dict[str, dict] = {}
    for p in pods:
        name = p.get("metadata", {}).get("name", "")
        if p.get("status", {}).get("phase") != "Running":
            continue
        vnf = _vnf_from_pod_name(name)
        if not vnf:
            continue
        canonical, vnf_type = vnf
        if canonical in seen:
            continue
        # Pull resource requests/limits off the first container, if available.
        containers = p.get("spec", {}).get("containers", [])
        resources = containers[0].get("resources", {}) if containers else {}
        seen[canonical] = {
            "name": canonical,
            "type": vnf_type,
            "replicas": 1,
            "resources": resources or {
                "requests": {"cpu": "200m", "memory": "256Mi"},
                "limits": {"cpu": "1", "memory": "1Gi"},
            },
            "interfaces": [],
            "supported_slices": [],
            "config": {},
        }

    vnfs = list(seen.values())
    topology = {
        "topology_id": f"runtime-{namespace}",
        "connectivity": {
            "plmn": {"mcc": "001", "mnc": "01"},
            "slices": [{"sst": 1, "sd": "ebmbase"}],
            "dnns": ["oai", "internet"],
        },
        "vnfs": vnfs,
        "connections": [],
        "sla": {},
    }
    allocation = {
        "topology_id": topology["topology_id"],
        "vnfs": vnfs,
        "node_assignments": {},
    }
    return topology, allocation


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--namespace", default="oai-5g")
    p.add_argument("--dry-run", action="store_true", help="Print but don't write to Redis.")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    topology, allocation = build_topology(args.namespace)
    logger.info(
        "Discovered %d VNFs in %s: %s",
        len(topology["vnfs"]), args.namespace,
        [v["name"] for v in topology["vnfs"]],
    )
    if args.dry_run:
        print(json.dumps(topology, indent=2))
        return 0

    rc = RedisClient()
    if not rc.ping():
        logger.error("Redis unreachable at %s", rc.url)
        return 2

    rc.set_topology(topology)
    rc.set_resource_allocation(allocation)
    logger.info("Topology + resource_allocation written to Redis")
    return 0


if __name__ == "__main__":
    sys.exit(main())
