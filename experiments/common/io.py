"""
Crash-safe IO for run_comparative.py: appends one record per (system, intent,
rep) to JSONL + a flat CSV, and supports resumption by skipping rows whose
(system, intent_id, rep) triple is already present.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

# Columns written to runs.csv. Anything else stays only in raw.jsonl.
CSV_COLUMNS: list[str] = [
    "system", "intent_id", "rep", "complexity", "ue_band", "scenario",
    "deployment_time_s", "config_error_rate", "resource_accuracy",
    "interventions", "intent_to_deploy_accuracy", "policy_violation_rate",
    "error",
]


def _csv_row(record: dict[str, Any]) -> dict[str, Any]:
    return {col: record.get(col, "") for col in CSV_COLUMNS}


class ResultStore:
    def __init__(
        self,
        out_dir: str | Path,
        *,
        retry_failed: bool = False,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.out_dir / "raw.jsonl"
        self.csv_path = self.out_dir / "runs.csv"
        self._retry_failed = retry_failed
        self._completed: set[tuple[str, str, int]] = set()
        self._load_completed()

    def _load_completed(self) -> None:
        if not self.jsonl_path.exists():
            return
        # If retry_failed is set, we drop rows whose `error` is non-empty from
        # the completed-set AND rewrite the JSONL/CSV without them so the next
        # run produces a clean append history.
        keep_lines: list[str] = []
        with open(self.jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (rec.get("system"), rec.get("intent_id"), rec.get("rep"))
                if not all(k is not None for k in key):
                    continue
                if self._retry_failed and rec.get("error"):
                    continue  # drop — will be retried
                self._completed.add(key)  # type: ignore[arg-type]
                keep_lines.append(line)

        if self._retry_failed:
            # Rewrite jsonl with only the kept rows
            with open(self.jsonl_path, "w") as f:
                for line in keep_lines:
                    f.write(line + "\n")
            # Rewrite csv from scratch using kept rows
            if self.csv_path.exists():
                self.csv_path.unlink()
            for line in keep_lines:
                rec = json.loads(line)
                write_header = not self.csv_path.exists()
                with open(self.csv_path, "a", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                    if write_header:
                        writer.writeheader()
                    writer.writerow(_csv_row(rec))

    def is_done(self, system: str, intent_id: str, rep: int) -> bool:
        return (system, intent_id, rep) in self._completed

    def append(self, record: dict[str, Any]) -> None:
        # JSONL: full record
        with open(self.jsonl_path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

        # CSV: write header on first row, then append
        write_header = not self.csv_path.exists()
        with open(self.csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerow(_csv_row(record))

        self._completed.add(
            (record.get("system"), record.get("intent_id"), record.get("rep"))
        )

    def expected_keys(
        self, systems: Iterable[str], intent_ids: Iterable[str], reps: int
    ) -> list[tuple[str, str, int]]:
        out: list[tuple[str, str, int]] = []
        for s in systems:
            for iid in intent_ids:
                for r in range(reps):
                    out.append((s, iid, r))
        return out

    def progress(
        self, systems: Iterable[str], intent_ids: Iterable[str], reps: int
    ) -> dict[str, int]:
        keys = self.expected_keys(systems, intent_ids, reps)
        done = sum(1 for k in keys if k in self._completed)
        return {"done": done, "total": len(keys), "remaining": len(keys) - done}
