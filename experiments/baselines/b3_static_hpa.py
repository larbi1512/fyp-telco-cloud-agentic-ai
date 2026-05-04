"""
B3 — Static manifests + HPA scaffold (vanilla k8s autoscaling baseline).

This baseline represents "what you get with vanilla Kubernetes if you do not
do intent translation at all". The pipeline:

  1. Read the standard OAI Helm values (`oai-5g-basic/values.yaml`).
  2. Pick replicas + per-VNF CPU/memory from a *fixed linear rule* keyed on
     `intent.ue_band` (50 / 200 / 500). No LLM, no slice planning, no
     scenario reasoning.
  3. Emit a vanilla HPA spec block per scalable VNF.
  4. Build a topology and config_artifacts in the same shape as MAS / B4.

Per the plan's framing notes, B3 is included to represent vanilla autoscaling.
Several pre-deployment metrics (notably resource_accuracy) are computed
against the *static* manifest only, not against HPA's runtime adjustments.
The methodology section makes this explicit.
"""

from __future__ import annotations

import copy
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from agents.deployer import CHART_MAP
from config.settings import PROJECT_ROOT
from experiments.common.runners import RunArtifact, register


BASIC_CHART_VALUES = (
    PROJECT_ROOT / "charts" / "oai-5g-core" / "oai-5g-basic" / "values.yaml"
)


# Static scaling table — deliberately simple. Each row gives the per-VNF
# CPU (millicores) and memory (Mi) request, and the replica count. Numbers
# come from rounding the OAI chart defaults up linearly across UE bands.
STATIC_TABLE: dict[int, dict[str, dict[str, Any]]] = {
    50: {
        "amf":  {"cpu_m": 200, "memory_mi": 256, "replicas": 1, "scalable": True},
        "smf":  {"cpu_m": 200, "memory_mi": 256, "replicas": 1, "scalable": True},
        "upf":  {"cpu_m": 500, "memory_mi": 512, "replicas": 1, "scalable": True},
        "nrf":  {"cpu_m": 100, "memory_mi": 128, "replicas": 1, "scalable": False},
        "ausf": {"cpu_m": 100, "memory_mi": 128, "replicas": 1, "scalable": True},
        "udm":  {"cpu_m": 100, "memory_mi": 128, "replicas": 1, "scalable": True},
        "udr":  {"cpu_m": 100, "memory_mi": 128, "replicas": 1, "scalable": False},
    },
    200: {
        "amf":  {"cpu_m": 400, "memory_mi": 512, "replicas": 2, "scalable": True},
        "smf":  {"cpu_m": 400, "memory_mi": 512, "replicas": 2, "scalable": True},
        "upf":  {"cpu_m": 1000, "memory_mi": 1024, "replicas": 2, "scalable": True},
        "nrf":  {"cpu_m": 200, "memory_mi": 256, "replicas": 1, "scalable": False},
        "ausf": {"cpu_m": 200, "memory_mi": 256, "replicas": 2, "scalable": True},
        "udm":  {"cpu_m": 200, "memory_mi": 256, "replicas": 2, "scalable": True},
        "udr":  {"cpu_m": 200, "memory_mi": 256, "replicas": 1, "scalable": False},
    },
    500: {
        "amf":  {"cpu_m": 800, "memory_mi": 1024, "replicas": 3, "scalable": True},
        "smf":  {"cpu_m": 800, "memory_mi": 1024, "replicas": 3, "scalable": True},
        "upf":  {"cpu_m": 2000, "memory_mi": 2048, "replicas": 3, "scalable": True},
        "nrf":  {"cpu_m": 300, "memory_mi": 384, "replicas": 1, "scalable": False},
        "ausf": {"cpu_m": 400, "memory_mi": 512, "replicas": 2, "scalable": True},
        "udm":  {"cpu_m": 400, "memory_mi": 512, "replicas": 2, "scalable": True},
        "udr":  {"cpu_m": 300, "memory_mi": 384, "replicas": 1, "scalable": False},
    },
}


def _nearest_band(ue_band: int) -> int:
    """Round to the nearest defined band (50 / 200 / 500)."""
    bands = sorted(STATIC_TABLE.keys())
    return min(bands, key=lambda b: abs(b - (ue_band or bands[0])))


def _hpa_block(vnf_type: str, base_replicas: int) -> dict[str, Any]:
    """A vanilla HPA spec to embed in helm_values for scalable VNFs."""
    return {
        "enabled": True,
        "minReplicas": base_replicas,
        "maxReplicas": max(base_replicas * 3, base_replicas + 2),
        "targetCPUUtilizationPercentage": 70,
    }


def _build_helm_values(
    base_chart_values: dict[str, Any],
    vnf_type: str,
    sizing: dict[str, Any],
) -> dict[str, Any]:
    """
    Take the basic-chart values for a single VNF, fill in deterministic
    resources + HPA, and return a stand-alone Helm values dict suitable for
    the policy validator.
    """
    chart_key = CHART_MAP.get(vnf_type, f"oai-{vnf_type}")
    src = base_chart_values.get(chart_key) or {}
    out = copy.deepcopy(src) if src else {}

    cpu = f"{sizing['cpu_m']}m"
    mem = f"{sizing['memory_mi']}Mi"

    out["resources"] = {
        "define": True,
        "requests": {"nf": {"cpu": cpu, "memory": mem}},
        "limits": {"nf": {"cpu": f"{sizing['cpu_m'] * 2}m",
                          "memory": f"{sizing['memory_mi'] * 2}Mi"}},
    }
    out.setdefault("nfimage", {
        "repository": f"docker.io/oaisoftwarealliance/oai-{vnf_type}",
        "version": "v2.1.0",
    })
    out.setdefault("exposedPorts", {"sbi": 80})
    out.setdefault("start", {vnf_type: True, "tcpdump": False})
    out.setdefault("readinessProbe", True)
    out.setdefault("livenessProbe", True)
    out["replicas"] = sizing["replicas"]
    if sizing.get("scalable"):
        out["autoscaling"] = _hpa_block(vnf_type, sizing["replicas"])
    return out


def _load_basic_chart_values() -> dict[str, Any]:
    if not BASIC_CHART_VALUES.exists():
        return {}
    with open(BASIC_CHART_VALUES) as f:
        return yaml.safe_load(f) or {}


class B3StaticHPARunner:
    """B3 — vanilla static manifests + HPA scaffold, no intent translation."""

    name = "b3"

    def __init__(self) -> None:
        self._chart_values = _load_basic_chart_values()

    def run(self, intent: dict[str, Any], *, deploy: bool = True) -> RunArtifact:
        run_id = str(uuid.uuid4())
        start = time.time()

        ue_band = _nearest_band(int(intent.get("ue_band") or 50))
        sizing_table = STATIC_TABLE[ue_band]

        # Topology — fixed structure, no slice reasoning.
        vnfs: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        for vnf_type, sizing in sizing_table.items():
            cpu = f"{sizing['cpu_m']}m"
            mem = f"{sizing['memory_mi']}Mi"
            vnfs.append({
                "name": f"oai-{vnf_type}",
                "type": vnf_type,
                "replicas": sizing["replicas"],
                "interfaces": [{"name": "sbi", "protocol": "http", "port": 80}],
                "resources": {
                    "requests": {"cpu": cpu, "memory": mem},
                    "limits": {"cpu": f"{sizing['cpu_m'] * 2}m",
                               "memory": f"{sizing['memory_mi'] * 2}Mi"},
                },
            })
            artifacts.append({
                "vnf_name": vnf_type,
                "helm_values": _build_helm_values(
                    self._chart_values, vnf_type, sizing
                ),
                "config_maps": {},
                "secrets": [],
            })

        topology = {
            "topology_id": f"b3-{run_id[:8]}",
            "connectivity": {
                # Fixed PLMN, single eMBB slice — no scenario reasoning.
                "plmn": {"mcc": "001", "mnc": "01"},
                "slices": [{"sst": 1, "sd": "000001", "description": "default eMBB"}],
                "dnns": ["internet"],
            },
            "vnfs": vnfs,
            "connections": [],
            "sla": {},
        }

        wall_clock_s = time.time() - start

        return RunArtifact({
            "system": self.name,
            "intent_id": intent.get("id"),
            "run_id": run_id,
            "topology": topology,
            "config_artifacts": artifacts,
            "validation_report": {},      # filled in by harness
            "deployment_results": [],     # populated by harness if deploy=True
            "intervention_log": [],
            "wall_clock_s": wall_clock_s,
            "deployment_wait_s": None,
            "release_names": list(CHART_MAP.values()),
            "error": None,
        })


register(B3StaticHPARunner())
