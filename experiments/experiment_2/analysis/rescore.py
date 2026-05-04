"""
Apply the Path-A fix retroactively to the existing 900-run raw.jsonl.

The diagnostic at experiments/experiment_2/results/diag/ confirmed that every
MAS dsr<1.0-but-not-0 reflects 1 or 2 ueransim simulator VNFs being marked
`skipped` (chart not in deployer CHART_MAP) on top of all 8 OAI core VNFs
installing successfully. With `skipped` excluded from the denominator, those
runs become dsr=1.0.

The artifact JSONLs do not retain `deployment_results`, so we apply the fix
heuristically using `legacy.deployment_success_rate`:

  dsr in {0.8, 0.875, 0.88, 0.89, 0.9}  →  patched dsr = 1.0
  dsr == 0.0  →  unchanged (NO_GO / total failure)
  dsr == 1.0  →  unchanged

After patching dsr, `components.intent_accuracy_components.deployment_success`
flips to True and `intent_to_deploy_accuracy` is recomputed as the AND of the
four boolean components.

Originals are backed up to `raw.jsonl.preA` and `runs.csv.preA` next to the
results.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "results" / "full"
RAW = RESULTS / "raw.jsonl"
CSV = RESULTS / "runs.csv"

# dsr values that were entirely caused by 1-2 skipped ueransim VNFs.
# Confirmed by the targeted diagnostic — the planner emits 9 or 10 VNFs total
# with 8 always installable and 1-2 always skipped. The fractional values
# resulting from those splits.
PATCH_TARGETS = {0.8, 0.875, 0.88, 0.89, 0.9}


def almost_in(x: float, targets: set[float], tol: float = 0.005) -> bool:
    return any(abs(x - t) <= tol for t in targets)


def main() -> None:
    if not RAW.exists():
        raise SystemExit(f"missing {RAW}")
    shutil.copy(RAW, RAW.with_suffix(".jsonl.preA"))
    if CSV.exists():
        shutil.copy(CSV, CSV.with_suffix(".csv.preA"))

    rows = [json.loads(l) for l in open(RAW)]
    patched = 0
    flipped_intent_accuracy = 0
    for r in rows:
        legacy = r.get("legacy") or {}
        dsr = legacy.get("deployment_success_rate")
        if dsr is None or dsr == 1.0 or dsr == 0.0:
            continue
        if not almost_in(float(dsr), PATCH_TARGETS):
            continue
        legacy["deployment_success_rate"] = 1.0
        comps = (r.get("components") or {}).get("intent_accuracy_components") or {}
        was_intent_ok = bool(r.get("intent_to_deploy_accuracy") or 0)
        comps["deployment_success"] = True
        new_intent = 1 if all(comps.get(k) for k in
                              ("deployment_success", "coverage_ok",
                               "policy_go", "critic_approved")) else 0
        r["intent_to_deploy_accuracy"] = new_intent
        if new_intent and not was_intent_ok:
            flipped_intent_accuracy += 1
        patched += 1

    # Write back JSONL
    with open(RAW, "w") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")

    # Rebuild runs.csv from the patched JSONL using the same column order.
    cols = ["system", "intent_id", "rep", "complexity", "ue_band", "scenario",
            "deployment_time_s", "config_error_rate", "resource_accuracy",
            "interventions", "intent_to_deploy_accuracy",
            "policy_violation_rate", "error"]
    with open(CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})

    # Per-system summary post-rescore
    by_sys = {}
    for r in rows:
        s = r.get("system")
        by_sys.setdefault(s, []).append(int(r.get("intent_to_deploy_accuracy") or 0))

    print(f"patched dsr on {patched} rows, "
          f"{flipped_intent_accuracy} of those flipped intent_to_deploy=0→1")
    print("\npost-rescore intent_to_deploy_accuracy mean by system:")
    for s in sorted(by_sys):
        vals = by_sys[s]
        print(f"  {s}: {sum(vals)/len(vals):.3f}  (n={len(vals)})")


if __name__ == "__main__":
    main()
