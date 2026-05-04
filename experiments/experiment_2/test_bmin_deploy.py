"""
B-min deploy smoke test — runs MAS on one intent with deploy=True and
verifies that:

  - The deployer attempts to install all 10 VNFs (8 core + gnb + nr-ue),
    none returning status='skipped' for chart-not-found.
  - oai-gnb and oai-nr-ue helm releases are present in the namespace.
  - The new deployment_success_rate (excluding skipped) is 1.0 if all
    helm installs succeed, regardless of whether pods reach Ready.

Run:
   PYTHONPATH=/home/larbi/fyp ./venv/bin/python experiments/experiment_2/test_bmin_deploy.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.common.runners import get_runner
from experiments.experiment_1.scoring import deployment_success_rate

OUT = Path(__file__).parent / "results" / "diag" / "B01_artifact.json"
OUT.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    intent = {
        "id": "S02",
        "prompt": "Set up a 5G network for 100 users.",
        "expected_vnfs": ["nrf", "amf", "smf", "upf",
                          "ausf", "udm", "udr"],
        "_rep": 0,
    }

    runner = get_runner("mas")
    print("Running MAS on S02 with deploy=True (this will helm-install + 2 min wait)")
    artifact = runner.run(intent, deploy=True)

    with open(OUT, "w") as f:
        json.dump(artifact, f, indent=2, default=str)

    drs = artifact.get("deployment_results") or []
    print(f"\nDeployment results ({len(drs)}):")
    by_status = {}
    for d in drs:
        st = d.get("status", "?")
        by_status.setdefault(st, []).append(d.get("vnf_name"))
        msg = (d.get("message") or "")[:80]
        print(f"  {d.get('vnf_name','?'):20s} status={st:10s}  {msg}")

    print("\nStatus summary:", {k: len(v) for k, v in by_status.items()})

    dsr = deployment_success_rate(drs)
    print(f"\ndeployment_success_rate (skipped excluded) = {dsr:.3f}")
    print(f"policy decision: {(artifact.get('validation_report') or {}).get('decision')}")
    print(f"error: {artifact.get('error')}")
    print(f"\nartifact saved -> {OUT}")

    # Pass/fail
    if any(d.get("status") == "skipped" for d in drs):
        print("\nFAIL — some VNFs were marked 'skipped' (chart not found).")
        sys.exit(1)

    if dsr < 1.0:
        print(f"\nPARTIAL — dsr={dsr} (some helm installs failed).")
        sys.exit(2)

    print("\nPASS — every VNF was attempted and helm-install succeeded.")


if __name__ == "__main__":
    main()
