"""
Targeted diagnostic reproduction for the dsr=0.8 / NO_GO patterns.

Picks 4 intents that exhibited each failure mode in the full run, runs MAS
on them with deploy=True, and dumps the FULL artifact (topology types,
deployment_results, validation_report.policy_checks) so we can see exactly
why dsr drops and why policy NO_GOs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.common.runners import get_runner

OUT_DIR = Path(__file__).parent / "results" / "diag"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Picked from raw.jsonl analysis:
# - S02: dsr=0.8 in 4 of 5 reps  (simple, frequent)
# - N02: dsr=0.8 in 5 of 5 reps  (worst dsr=0.8 offender)
# - M04: dsr=0.0 NO_GO            (catastrophic)
# - C06: dsr=0.0 NO_GO            (catastrophic, complex)
TARGET_IDS = ["S02", "N02", "M04", "C06"]

DATASET = Path(__file__).resolve().parents[1] / "datasets" / "intents_v2.json"


def summarise(artifact: dict) -> dict:
    topo = artifact.get("topology") or {}
    vnfs = topo.get("vnfs") or []
    cfgs = artifact.get("config_artifacts") or []
    drs = artifact.get("deployment_results") or []
    vr = artifact.get("validation_report") or {}
    return {
        "topology_vnfs": [{"name": v.get("name"), "type": v.get("type")} for v in vnfs],
        "config_artifact_names": [c.get("vnf_name") for c in cfgs],
        "deployment_results": [
            {"vnf_name": d.get("vnf_name"),
             "release": d.get("release_name"),
             "status": d.get("status"),
             "msg": (d.get("message") or "")[:120]}
            for d in drs
        ],
        "policy_checks": {
            k: {"status": v.get("status"), "violations": v.get("violations"),
                "details": (v.get("details") or "")[:120]}
            for k, v in (vr.get("policy_checks") or {}).items()
        },
        "policy_decision": vr.get("decision"),
        "error": artifact.get("error"),
    }


def main() -> None:
    intents = {p["id"]: p for p in json.load(open(DATASET))["prompts"]}
    targets = [intents[i] for i in TARGET_IDS if i in intents]
    print(f"Running MAS on {len(targets)} intents: {[t['id'] for t in targets]}")

    runner = get_runner("mas")
    for intent in targets:
        print(f"\n=== {intent['id']} ===")
        intent_with_rep = dict(intent); intent_with_rep["_rep"] = 0
        artifact = runner.run(intent_with_rep, deploy=True)

        out_file = OUT_DIR / f"{intent['id']}_artifact.json"
        with open(out_file, "w") as f:
            json.dump(artifact, f, indent=2, default=str)

        diag = summarise(artifact)
        print(json.dumps(diag, indent=2, default=str))
        print(f"  -> {out_file}")


if __name__ == "__main__":
    main()
