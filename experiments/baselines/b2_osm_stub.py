"""
B2 — OSM/MANO baseline (literature-anchored stub).

Per the experiment plan, B2 is *not* a real OSM deployment. Standing up OSM
and onboarding NSDs/VNFDs would consume ~2 weeks of infra work whose noise
would dominate any measurement, and the user-confirmed plan accepts a
literature-only stub for fairness.

The stub emits a RunArtifact whose timing, intervention count, error rate,
and per-VNF resources are drawn from published OSM/MANO benchmarks (see
[b2_calibration.yaml](b2_calibration.yaml) and
[B1_PILOT_GUIDE.md](B1_PILOT_GUIDE.md) §1 for the same three references).

What B2 represents in the comparison:
  - No intent translation: the NSD/VNFD library is fixed, so resource
    sizing does NOT scale with UE count, throughput, slices, or HA flag.
    This is the most important methodological point — OSM literally does
    this.
  - Schema-validated artifacts: config errors are stochastically injected at
    a lower rate than B1 (6% vs 12%) because OSM's own validators catch many
    typos. But the errors that DO slip through are higher-impact.
  - High intervention count: NSD authoring is intervention-heavy
    (schema lookups, descriptor library browsing). Per-VNF intervention
    count ~2x B1's.
  - Slow deployment: 8-12 min per NS, vs <2 min for direct helm.

Honest framing for the thesis: "literature-anchored stub, not measured."
"""

from __future__ import annotations

import copy
import logging
import random
import uuid
from pathlib import Path
from typing import Any

import yaml

from agents.deployer import CHART_MAP
from config.settings import PROJECT_ROOT
from experiments.common.runners import RunArtifact, register


CALIBRATION_PATH = (
    PROJECT_ROOT / "experiments" / "baselines" / "b2_calibration.yaml"
)

logger = logging.getLogger(__name__)


# Minimum VNFs an OSM NSD for a 5G core would carry, regardless of intent.
# OSM does not deduce these from intent — they come from the descriptor.
DEFAULT_VNF_TYPES = ["nrf", "amf", "smf", "upf", "ausf", "udm", "udr"]


def _load_calibration() -> dict[str, Any]:
    with open(CALIBRATION_PATH) as f:
        return yaml.safe_load(f)


def _draw_normal(rng: random.Random, mean: float, sd: float, lo: float = 1.0) -> float:
    return max(lo, rng.gauss(mean, sd))


def _pick_error_kind(rng: random.Random, error_kinds: list[dict[str, Any]]) -> str:
    weights = [float(k.get("weight", 0)) for k in error_kinds]
    total = sum(weights)
    if total <= 0:
        return error_kinds[0]["name"] if error_kinds else "missing_resource_limits"
    pick = rng.uniform(0, total)
    acc = 0.0
    for k, w in zip(error_kinds, weights):
        acc += w
        if pick <= acc:
            return k["name"]
    return error_kinds[-1]["name"]


def _stub_helm_values(vnf_type: str, sizing: dict[str, Any]) -> dict[str, Any]:
    """
    OSM produces NSDs/VNFDs, not Helm values. To make B2 comparable to the
    other systems' artifacts, we synthesise the equivalent Helm values that
    OSM's OAI plugin would generate from its default descriptor library.
    Crucially: sizing is FIXED (from the calibration's default_vnf_resources),
    not derived from the intent.
    """
    cpu = f"{sizing['cpu_m']}m"
    mem = f"{sizing['memory_mi']}Mi"
    return {
        "nfimage": {
            "repository": f"docker.io/oaisoftwarealliance/oai-{vnf_type}",
            "version": "v2.1.0",
        },
        "exposedPorts": {"sbi": 80},
        "start": {vnf_type: True, "tcpdump": False},
        "resources": {
            "define": True,
            "requests": {"nf": {"cpu": cpu, "memory": mem}},
            "limits": {"nf": {"cpu": f"{sizing['cpu_m'] * 2}m",
                              "memory": f"{sizing['memory_mi'] * 2}Mi"}},
        },
        "readinessProbe": True,
        "livenessProbe": True,
        # B2-specific marker: this came from OSM's NSD library, not from the
        # intent. Useful for downstream auditing.
        "_descriptor_source": "osm_library_v1.0",
    }


def _inject_error(
    helm_values: dict[str, Any],
    error_kind: str,
    vnf_type: str,
) -> str:
    """Mutate helm_values to simulate a calibrated MANO operator error."""
    if error_kind == "missing_resource_limits":
        helm_values.setdefault("resources", {})["define"] = False
        helm_values["resources"].pop("requests", None)
        helm_values["resources"].pop("limits", None)
        return "VDU descriptor omitted resources block"
    if error_kind == "stale_vnfd_version":
        helm_values.setdefault("nfimage", {})["version"] = "v1.5.0"
        return "stale VNFD version (v1.5.0)"
    if error_kind == "wrong_vim_target":
        return "wrong VIM target (handled at topology level)"
    if error_kind == "wrong_plmn":
        return "wrong PLMN (handled at topology level)"
    if error_kind == "undersized_upf" and vnf_type == "upf":
        helm_values.setdefault("resources", {}) \
            .setdefault("requests", {}) \
            .setdefault("nf", {})["cpu"] = "100m"
        helm_values["resources"]["requests"]["nf"]["memory"] = "128Mi"
        return "undersized UPF (cpu=100m, memory=128Mi)"
    return f"unknown error kind: {error_kind}"


class B2OSMStubRunner:
    """Literature-anchored OSM/MANO stub — does not deploy or call any LLM."""

    name = "b2"

    def __init__(self) -> None:
        self._cal = _load_calibration()
        logger.info("B2 stub loaded — source: %s", self._cal.get("source"))

    def _seed_for(self, intent_id: str, rep: int) -> int:
        return abs(hash(("b2", intent_id, rep))) % (2**32)

    def run(self, intent: dict[str, Any], *, deploy: bool = True) -> RunArtifact:
        run_id = str(uuid.uuid4())
        rng = random.Random(self._seed_for(intent.get("id", ""), int(intent.get("_rep", 0))))

        feat = intent.get("structured_features") or {}

        # B2's VNF set is fixed by the descriptor library — no slice-aware
        # planning, no NSSF inference, no HA replica bumps. This is the
        # essence of "rule-based MANO without intent translation".
        vnf_types = list(DEFAULT_VNF_TYPES)

        # Per-VNF resources come from the descriptor library, NOT the intent.
        defaults = self._cal["default_vnf_resources"]

        # ── Wall-clock from literature distributions ────────────────────────
        author_block = self._cal["per_vnf_descriptor_authoring_sec"]
        author_sec = sum(
            _draw_normal(rng, author_block["mean"], author_block["sd"])
            for _ in vnf_types
        )
        ns_block = self._cal["ns_instantiation_sec"]
        ns_sec = _draw_normal(rng, ns_block["mean"], ns_block["sd"])
        wall_clock_s = author_sec + ns_sec

        # ── Build artifacts (with stochastic error injection) ───────────────
        artifacts: list[dict[str, Any]] = []
        injected: list[dict[str, str]] = []
        error_rate = float(self._cal["config_error_rate_pct"]["value"]) / 100.0
        error_kinds = self._cal["error_kinds"] or []
        topology_error: str | None = None

        for vnf_type in vnf_types:
            sizing = defaults.get(vnf_type, {"cpu_m": 250, "memory_mi": 256, "replicas": 1})
            hv = _stub_helm_values(vnf_type, sizing)
            if rng.random() < error_rate and error_kinds:
                kind = _pick_error_kind(rng, error_kinds)
                if kind in ("wrong_plmn", "wrong_vim_target"):
                    topology_error = kind
                else:
                    note = _inject_error(hv, kind, vnf_type)
                    injected.append({"vnf": vnf_type, "kind": kind, "note": note})
            artifacts.append({
                "vnf_name": vnf_type,
                "helm_values": hv,
                "config_maps": {},
                "secrets": [],
            })

        # ── Topology mirror of artifacts ────────────────────────────────────
        topo_vnfs = []
        for vnf_type in vnf_types:
            sizing = defaults.get(vnf_type, {"cpu_m": 250, "memory_mi": 256, "replicas": 1})
            cpu = f"{sizing['cpu_m']}m"
            mem = f"{sizing['memory_mi']}Mi"
            topo_vnfs.append({
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

        plmn = (
            {"mcc": "002", "mnc": "02"} if topology_error == "wrong_plmn"
            else {"mcc": "001", "mnc": "01"}
        )
        if topology_error in ("wrong_plmn", "wrong_vim_target"):
            injected.append({"vnf": "*", "kind": topology_error,
                             "note": f"topology-level error: {topology_error}"})

        topology = {
            "topology_id": f"b2-{run_id[:8]}",
            "connectivity": {
                "plmn": plmn,
                # B2 carries whatever slices the NSD descriptor defines —
                # default is single eMBB. No slice-aware planning.
                "slices": [{"sst": 1, "sd": "000001",
                            "description": "default eMBB (NSD library)"}],
                "dnns": ["internet"],
            },
            "vnfs": topo_vnfs,
            "connections": [],
            "sla": {},
        }

        # ── Interventions: literature-derived per-VNF count (~2x B1) ────────
        per_vnf = self._cal["per_vnf_intervention_count"]
        intervention_log: list[dict[str, Any]] = []
        for vnf_type in vnf_types:
            count = max(0, round(_draw_normal(rng, per_vnf["mean"], per_vnf["sd"], lo=0)))
            for _ in range(count):
                intervention_log.append({
                    "source": "osm_descriptor_authoring",
                    "vnf": vnf_type,
                    "would_approve": True,
                    "reasons": ["operator NSD/VNFD edit"],
                })

        return RunArtifact({
            "system": self.name,
            "intent_id": intent.get("id"),
            "run_id": run_id,
            "topology": topology,
            "config_artifacts": artifacts,
            "validation_report": {},   # filled in by harness
            "deployment_results": [],  # B2 does not deploy — stub
            "intervention_log": intervention_log,
            "wall_clock_s": round(wall_clock_s, 2),
            "deployment_wait_s": None,
            "release_names": list(CHART_MAP.values()),
            "error": None,
            "_calibration_source": self._cal.get("source"),
            "_injected_errors": injected,
        })


register(B2OSMStubRunner())
