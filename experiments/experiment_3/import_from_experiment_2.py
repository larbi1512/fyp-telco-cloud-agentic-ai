#!/usr/bin/env python3
"""
Import MAS records from Experiment 2 → Experiment 3 as the `gpt120b` LLM.

Experiment 2 ran the MAS with gpt-oss:120b across all 36 intents × 5 reps
(180 rows). Experiment 3 cannot run gpt-oss:120b on this machine because the
shared vLLM container holds 65 GB of GPU memory, leaving no room for the
120B model. Rather than running it again, we lift those 180 records and
relabel them as `llm_id=gpt120b` so the report includes a 120B data point.

Usage:
    python experiments/experiment_3/import_from_experiment_2.py
    python experiments/experiment_3/import_from_experiment_2.py \\
        --source experiments/experiment_2/results/full_bmin \\
        --dest   experiments/experiment_3/results
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from rich.console import Console

from experiments.common.io import ResultStore
from experiments.experiment_3.configs import LLM_CONFIGS

console = Console()

LLM_ID = "gpt120b"
SOURCE_SYSTEM = "mas"  # The system label in Experiment 2 records


def relabel(record: dict) -> dict:
    """
    Convert an Experiment-2 MAS record into an Experiment-3 gpt120b record.
    Mutates a copy and returns it.
    """
    out = dict(record)
    cfg = LLM_CONFIGS[LLM_ID]
    out["system"] = LLM_ID
    out["llm_id"] = LLM_ID
    out["llm_name"] = cfg["name"]
    out["llm_size_b"] = cfg["size_b"]
    out["_imported_from"] = "experiment_2"
    out["_original_system"] = SOURCE_SYSTEM
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Import gpt120b records from Experiment 2")
    parser.add_argument(
        "--source",
        type=Path,
        default=_ROOT / "experiments" / "experiment_2" / "results" / "full_bmin",
        help="directory containing raw.jsonl from Experiment 2",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=_ROOT / "experiments" / "experiment_3" / "results",
        help="Experiment 3 results directory",
    )
    args = parser.parse_args()

    src_jsonl = args.source / "raw.jsonl"
    if not src_jsonl.exists():
        console.print(f"[red]Source not found: {src_jsonl}[/red]")
        return 1

    args.dest.mkdir(parents=True, exist_ok=True)
    store = ResultStore(args.dest)

    console.rule("[bold]Importing gpt120b records from Experiment 2")
    console.print(f"source: {src_jsonl}")
    console.print(f"dest  : {args.dest}")

    imported, skipped, kept_existing = 0, 0, 0

    with open(src_jsonl) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            if rec.get("system") != SOURCE_SYSTEM:
                continue

            relabeled = relabel(rec)
            iid = relabeled.get("intent_id")
            rep = relabeled.get("rep")

            if iid is None or rep is None:
                skipped += 1
                continue

            if store.is_done(LLM_ID, iid, rep):
                kept_existing += 1
                continue

            store.append(relabeled)
            imported += 1

    console.print(
        f"\n[green]✓[/green] imported={imported}  "
        f"already-present={kept_existing}  malformed={skipped}"
    )
    console.print(f"\nresults written to {args.dest}/runs.csv  +  raw.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
