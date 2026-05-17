"""
Experiment 4 — Batch Runner.

Drives ``run_scenario.py`` repeatedly to populate a full results matrix.

Two modes:
  --smoke    : F1×2, F2×2, F3×1 for MAS only. Quick validation (~45 min).
  --full     : 3 scenarios × 5 reps × 2 systems = 30 runs. Used for the
               thesis Experiment 4 dataset.
  --mas-only : MAS system only, 3 scenarios × 5 reps = 15 runs. Use when
               re-running to pick up schema changes (e.g. plan source field)
               without touching the existing B_static dataset.

Each completed scenario is written by the runner under
``results/runs/<run_id>.json`` and ``analyze_results.py`` aggregates them.
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
SCENARIO_RUNNER = HERE / "run_scenario.py"

SMOKE_PLAN = [
    # (scenario, system, rep, target)
    ("F1", "MAS", 1, "oai-amf"),
    ("F1", "MAS", 2, "oai-amf"),
    ("F2", "MAS", 1, "oai-upf"),
    ("F2", "MAS", 2, "oai-upf"),
    ("F3", "MAS", 1, "oai-upf"),
]


def _full_plan() -> list[tuple[str, str, int, str]]:
    plan: list[tuple[str, str, int, str]] = []
    targets = {"F1": "oai-amf", "F2": "oai-upf", "F3": "oai-upf"}
    for scenario in ("F1", "F2", "F3"):
        for rep in range(1, 6):
            plan.append((scenario, "MAS", rep, targets[scenario]))
    # Baseline: 5 reps each — 15 runs
    for scenario in ("F1", "F2", "F3"):
        for rep in range(1, 6):
            plan.append((scenario, "B_static", rep, targets[scenario]))
    return plan


def run_one(scenario: str, system: str, rep: int, target: str,
            baseline_cycles: int, recovery_cycles: int,
            cycle_interval: int, namespace: str) -> int:
    cmd = [
        sys.executable, str(SCENARIO_RUNNER),
        "--scenario", scenario,
        "--system", system,
        "--rep", str(rep),
        "--target", target,
        "--namespace", namespace,
        "--baseline-cycles", str(baseline_cycles),
        "--recovery-cycles", str(recovery_cycles),
        "--cycle-interval", str(cycle_interval),
    ]
    logger.info("=== %s/%s/rep%d ===", scenario, system, rep)
    logger.info("$ %s", " ".join(cmd))
    proc = subprocess.run(cmd)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--smoke", action="store_true",
                       help="Run the F1×2, F2×2, F3×1 smoke set (MAS only).")
    group.add_argument("--full", action="store_true",
                       help="Run the full 3×5×2 thesis matrix.")
    group.add_argument("--mas-only", action="store_true",
                       help="Run 3×5 MAS runs only (skip B_static).")
    p.add_argument("--baseline-cycles", type=int, default=3)
    p.add_argument("--recovery-cycles", type=int, default=12)
    p.add_argument("--cycle-interval", type=int, default=15)
    p.add_argument("--namespace", default="oai-5g")
    p.add_argument("--cooldown", type=int, default=30,
                   help="Seconds to wait between runs (let cluster settle).")
    p.add_argument("--start-from", type=int, default=0,
                   help="Skip the first N runs in the plan (resume support).")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.smoke:
        plan = SMOKE_PLAN
    elif args.mas_only:
        targets = {"F1": "oai-amf", "F2": "oai-upf", "F3": "oai-upf"}
        plan = [(s, "MAS", r, targets[s]) for s in ("F1", "F2", "F3") for r in range(1, 6)]
    else:
        plan = _full_plan()
    plan = plan[args.start_from:]

    failures = 0
    for i, (scenario, system, rep, target) in enumerate(plan, start=args.start_from):
        logger.info("[%d/%d] starting", i + 1, args.start_from + len(plan))
        rc = run_one(
            scenario, system, rep, target,
            args.baseline_cycles, args.recovery_cycles,
            args.cycle_interval, args.namespace,
        )
        if rc != 0:
            logger.warning("Run %s/%s/rep%d exited with rc=%d", scenario, system, rep, rc)
            failures += 1
        if i + 1 < args.start_from + len(plan):
            logger.info("Cooldown: sleeping %ds", args.cooldown)
            time.sleep(args.cooldown)

    logger.info("Batch complete: %d/%d runs failed", failures, len(plan))
    return 1 if failures > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
