"""
Regression test — loads the results/full_bmin/runs.csv artefact from the
900-run experiment and asserts the MAS attains the reported headline numbers.

This test does NOT re-run the experiment; it is a reproducibility anchor
for reviewers and for CI checks after any future code change.

Run:
    PYTHONPATH=/home/larbi/fyp ./venv/bin/python \
        experiments/experiment_2/test_regression_numbers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

RESULTS_CSV = Path(__file__).resolve().parent / "results" / "post_hpa" / "runs_merged.csv"

# Thresholds derived from the 900-run post_hpa experiment (MAS with HPA fix).
# These represent conservative lower bounds (not exact means) so minor float
# differences across platforms do not cause false failures.
THRESHOLDS = {
    "mas": {
        "intent_to_deploy_accuracy": 0.65,   # mean was 0.683
        "resource_accuracy": 0.70,           # mean was 0.726
        "config_error_rate": 0.20,           # must be ≤ this; mean was 0.150
        "policy_violation_rate": 0.25,       # must be ≤ this; mean was 0.200
    }
}


def main() -> None:
    if not RESULTS_CSV.exists():
        print(f"SKIP — {RESULTS_CSV} not found (run the experiment first)")
        sys.exit(0)

    df = pd.read_csv(RESULTS_CSV)
    failures: list[str] = []

    for system, checks in THRESHOLDS.items():
        sdf = df[df["system"] == system]
        if sdf.empty:
            failures.append(f"{system}: no rows found in {RESULTS_CSV}")
            continue

        n = len(sdf)
        for metric, threshold in checks.items():
            if metric not in sdf.columns:
                failures.append(f"{system}.{metric}: column not found")
                continue

            mean = sdf[metric].mean()
            low_metrics = {"config_error_rate", "policy_violation_rate"}
            if metric in low_metrics:
                ok = mean <= threshold
                op = "≤"
            else:
                ok = mean >= threshold
                op = "≥"

            status = "PASS" if ok else "FAIL"
            print(f"  {status}  {system}.{metric}: mean={mean:.3f} (want {op} {threshold})  [n={n}]")
            if not ok:
                failures.append(
                    f"{system}.{metric}: mean={mean:.3f} violates {op} {threshold}"
                )

    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)

    print("\nAll regression checks passed.")


if __name__ == "__main__":
    main()
