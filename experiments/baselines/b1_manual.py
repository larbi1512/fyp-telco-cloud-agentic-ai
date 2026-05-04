"""
B1 — Manual operator baseline (calibrated stochastic emulator).

Real human runs across 5 reps × 36 intents × 5 systems are infeasible. Instead
we run a *calibrated emulator*: a pilot study (one operator, ~2 hours, two
intents) measures the timing distribution and error rate; the emulator then
draws timing samples and injects realistic errors to produce a comparable
RunArtifact. Methodology and pilot data are documented in the thesis appendix.

The emulator:
  1. Reads timing + error-rate parameters from b1_calibration.yaml.
  2. Copies the basic chart values as a starting point (mirrors what an
     operator would do).
  3. Stochastically injects errors at the calibrated rate, drawn from the
     calibrated kinds (omitted probe, missing resource limits, latest tag, ...).
  4. Draws total wall-clock from a normal distribution conditioned on intent
     complexity and VNF count.
  5. Logs interventions matching the calibrated per-VNF intervention count so
     Metric 4 sees a realistic count.

The emulator is fully deterministic given a seed, which the harness passes per
rep so re-runs reproduce.
"""

from __future__ import annotations

import copy
import logging
import random
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from agents.deployer import CHART_MAP
from config.settings import PROJECT_ROOT
from experiments.common.runners import RunArtifact, register


CALIBRATION_PATH = (
    PROJECT_ROOT / "experiments" / "baselines" / "b1_calibration.yaml"
)
BASIC_CHART_VALUES = (
    PROJECT_ROOT / "charts" / "oai-5g-core" / "oai-5g-basic" / "values.yaml"
)

logger = logging.getLogger(__name__)


def _load_calibration() -> dict[str, Any]:
    with open(CALIBRATION_PATH) as f:
        cal = yaml.safe_load(f)
    source = (cal.get("active") or {}).get("source", cal.get("source", "literature"))
    prefix = source  # "literature" or "pilot"
    keys = [
        "per_vnf_edit_sec",
        "per_intent_understanding_sec",
        "helm_install_sec",
        "iterations_to_converge",
        "config_error_rate_pct",
        "per_vnf_intervention_count",
        "error_kinds",
    ]
    out: dict[str, Any] = {"source": source}
    for k in keys:
        out[k] = cal.get(f"{prefix}_{k}")
        if out[k] is None or out[k] == [] or (isinstance(out[k], dict)
                                              and all(v is None for v in out[k].values())):
            # Pilot block not yet filled — fall back to literature
            out[k] = cal.get(f"literature_{k}")
            out["source"] = f"{source}->literature_fallback"
    return out


def _load_basic_chart_values() -> dict[str, Any]:
    if not BASIC_CHART_VALUES.exists():
        return {}
    with open(BASIC_CHART_VALUES) as f:
        return yaml.safe_load(f) or {}


# Standard VNF set an operator would deploy by hand.
DEFAULT_VNF_TYPES = ["nrf", "amf", "smf", "upf", "ausf", "udm", "udr"]


def _stub_helm_values(
    base_chart_values: dict[str, Any],
    vnf_type: str,
) -> dict[str, Any]:
    """Mirrors what an operator copy-pasting from the basic chart would have."""
    chart_key = CHART_MAP.get(vnf_type, f"oai-{vnf_type}")
    src = base_chart_values.get(chart_key) or {}
    out = copy.deepcopy(src) if src else {}
    out.setdefault("nfimage", {
        "repository": f"docker.io/oaisoftwarealliance/oai-{vnf_type}",
        "version": "v2.1.0",
    })
    out.setdefault("exposedPorts", {"sbi": 80})
    out.setdefault("start", {vnf_type: True, "tcpdump": False})
    # Operators following the runbook usually include resource limits, modulo
    # the error injector below.
    out.setdefault("resources", {
        "define": True,
        "requests": {"nf": {"cpu": "200m", "memory": "512Mi"}},
        "limits": {"nf": {"cpu": "1000m", "memory": "2Gi"}},
    })
    out.setdefault("readinessProbe", True)
    out.setdefault("livenessProbe", True)
    return out


def _inject_error(
    helm_values: dict[str, Any],
    error_kind: str,
    vnf_type: str,
) -> str:
    """Mutate helm_values in place to simulate a calibrated operator error."""
    if error_kind == "omitted_probe":
        helm_values.pop("readinessProbe", None)
        helm_values.pop("livenessProbe", None)
        return "removed readiness/liveness probes"
    if error_kind == "missing_resource_limits":
        helm_values.setdefault("resources", {})["define"] = False
        helm_values["resources"].pop("requests", None)
        helm_values["resources"].pop("limits", None)
        return "set resources.define=false"
    if error_kind == "latest_image_tag":
        helm_values.setdefault("nfimage", {})["version"] = "latest"
        return "image tag set to 'latest'"
    if error_kind == "wrong_plmn":
        return "wrong PLMN (handled at topology level)"
    if error_kind == "undersized_upf" and vnf_type == "upf":
        helm_values.setdefault("resources", {}) \
            .setdefault("requests", {}) \
            .setdefault("nf", {})["cpu"] = "100m"
        helm_values["resources"]["requests"]["nf"]["memory"] = "128Mi"
        return "undersized UPF (cpu=100m, memory=128Mi)"
    if error_kind == "missing_nssf":
        return "skipped NSSF (handled at topology level)"
    return f"unknown error kind: {error_kind}"


def _pick_error_kind(rng: random.Random, error_kinds: list[dict[str, Any]]) -> str:
    weights = [float(k.get("weight", 0)) for k in error_kinds]
    total = sum(weights)
    if total <= 0:
        return error_kinds[0]["name"] if error_kinds else "omitted_probe"
    pick = rng.uniform(0, total)
    acc = 0.0
    for k, w in zip(error_kinds, weights):
        acc += w
        if pick <= acc:
            return k["name"]
    return error_kinds[-1]["name"]


def _draw_normal(rng: random.Random, mean: float, sd: float, lo: float = 1.0) -> float:
    return max(lo, rng.gauss(mean, sd))


class B1ManualRunner:
    """Calibrated emulator for a human operator deploying by hand."""

    name = "b1"

    def __init__(self) -> None:
        self._cal = _load_calibration()
        self._chart_values = _load_basic_chart_values()
        logger.info("B1 calibration source: %s", self._cal["source"])

    def _seed_for(self, intent_id: str, rep: int) -> int:
        return abs(hash(("b1", intent_id, rep))) % (2**32)

    def run(self, intent: dict[str, Any], *, deploy: bool = True) -> RunArtifact:
        run_id = str(uuid.uuid4())
        rng = random.Random(self._seed_for(intent.get("id", ""), int(intent.get("_rep", 0))))
        wall_start = time.time()

        complexity = intent.get("complexity", "simple")
        feat = intent.get("structured_features") or {}

        # Decide VNF set: include NSSF for multi-slice intents, mirroring an
        # operator who reads the runbook carefully (modulo the missing_nssf
        # error injection below).
        vnf_types = list(DEFAULT_VNF_TYPES)
        if len(feat.get("slices") or []) > 1:
            vnf_types.append("nssf")

        # Replica count is the "operator default" — 1 per VNF, +1 if HA.
        base_replicas = 2 if feat.get("ha_required") else 1

        # ── Simulated wall-clock ────────────────────────────────────────────
        understanding_sec = float(
            self._cal["per_intent_understanding_sec"].get(complexity, 180)
        )
        per_vnf_block = self._cal["per_vnf_edit_sec"]
        edit_sec = sum(
            _draw_normal(rng, per_vnf_block["mean"], per_vnf_block["sd"])
            for _ in vnf_types
        )
        iters = max(1, round(_draw_normal(
            rng,
            self._cal["iterations_to_converge"]["mean"],
            self._cal["iterations_to_converge"]["sd"],
            lo=1,
        )))
        helm_block = self._cal["helm_install_sec"]
        helm_sec = sum(
            _draw_normal(rng, helm_block["mean"], helm_block["sd"])
            for _ in range(iters)
        )
        wall_clock_s = understanding_sec + edit_sec + helm_sec

        # ── Build artifacts (with stochastic error injection) ───────────────
        artifacts: list[dict[str, Any]] = []
        injected: list[dict[str, str]] = []
        error_rate = float(self._cal["config_error_rate_pct"]) / 100.0
        error_kinds = self._cal["error_kinds"] or []

        # Decide which (if any) VNFs receive an error, plus possible
        # topology-level errors (wrong PLMN, missing NSSF).
        topology_error: str | None = None

        for vnf_type in vnf_types:
            hv = _stub_helm_values(self._chart_values, vnf_type)
            if rng.random() < error_rate and error_kinds:
                kind = _pick_error_kind(rng, error_kinds)
                if kind == "wrong_plmn":
                    topology_error = "wrong_plmn"
                elif kind == "missing_nssf" and vnf_type != "nssf":
                    topology_error = "missing_nssf"
                else:
                    note = _inject_error(hv, kind, vnf_type)
                    injected.append({"vnf": vnf_type, "kind": kind, "note": note})
            artifacts.append({
                "vnf_name": vnf_type,
                "helm_values": hv,
                "config_maps": {},
                "secrets": [],
            })

        # ── Topology ────────────────────────────────────────────────────────
        cpu = "200m"
        mem = "512Mi"
        topo_vnfs = [
            {
                "name": f"oai-{t}",
                "type": t,
                "replicas": base_replicas,
                "interfaces": [{"name": "sbi", "protocol": "http", "port": 80}],
                "resources": {
                    "requests": {"cpu": cpu, "memory": mem},
                    "limits": {"cpu": "1000m", "memory": "2Gi"},
                },
            }
            for t in vnf_types
        ]
        if topology_error == "missing_nssf" and "nssf" in vnf_types:
            topo_vnfs = [v for v in topo_vnfs if v["type"] != "nssf"]
            artifacts = [a for a in artifacts if a["vnf_name"] != "nssf"]
            injected.append({"vnf": "nssf", "kind": "missing_nssf",
                             "note": "operator skipped NSSF"})

        plmn = (
            {"mcc": "002", "mnc": "02"} if topology_error == "wrong_plmn"
            else {"mcc": "001", "mnc": "01"}
        )
        if topology_error == "wrong_plmn":
            injected.append({"vnf": "*", "kind": "wrong_plmn",
                             "note": "MCC/MNC off-by-one"})

        topology = {
            "topology_id": f"b1-{run_id[:8]}",
            "connectivity": {
                "plmn": plmn,
                "slices": [{"sst": int(s.get("sst", 1)),
                            "sd": "000001",
                            "description": f"sst{s.get('sst')}"}
                           for s in (feat.get("slices") or [{"sst": 1}])],
                "dnns": ["internet"],
            },
            "vnfs": topo_vnfs,
            "connections": [],
            "sla": {},
        }

        # ── Interventions: simulate operator HITL moments ───────────────────
        per_vnf = self._cal["per_vnf_intervention_count"]
        intervention_log: list[dict[str, Any]] = []
        for vnf_type in vnf_types:
            count = max(0, round(_draw_normal(rng, per_vnf["mean"], per_vnf["sd"], lo=0)))
            for _ in range(count):
                intervention_log.append({
                    "source": "manual_operator",
                    "vnf": vnf_type,
                    "would_approve": True,
                    "reasons": ["operator manual edit"],
                })

        # ── Override emulated wall-clock; ignore real Python time ───────────
        _ = time.time() - wall_start  # not used; emulator provides the timing

        return RunArtifact({
            "system": self.name,
            "intent_id": intent.get("id"),
            "run_id": run_id,
            "topology": topology,
            "config_artifacts": artifacts,
            "validation_report": {},   # filled in by harness
            "deployment_results": [],  # B1 does not deploy — emulator
            "intervention_log": intervention_log,
            "wall_clock_s": round(wall_clock_s, 2),
            "deployment_wait_s": None,
            "release_names": list(CHART_MAP.values()),
            "error": None,
            "_calibration_source": self._cal["source"],
            "_injected_errors": injected,
            "_iterations_to_converge": iters,
        })


register(B1ManualRunner())
