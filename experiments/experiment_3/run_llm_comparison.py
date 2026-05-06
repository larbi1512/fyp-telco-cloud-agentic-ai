#!/usr/bin/env python3
"""
Experiment 3 — LLM comparison for the pre-deployment MAS.

Runs the same MAS pipeline (config-only, no helm install) with five
different LLMs and records the six standard metrics for each run.

The LLM is hot-swapped between batches by patching the module-level
variables that LLMCore.__init__ reads from core.llm_core. This requires
no changes to agents or LLMCore.

Usage:
  # Smoke: 2 LLMs × 4 intents × 1 rep = 8 records
  python experiments/experiment_3/run_llm_comparison.py \\
      --llms gpt120b qwen25_7b --reps 1 --intents smoke

  # Full: 5 LLMs × 36 intents × 5 reps = 900 records
  python experiments/experiment_3/run_llm_comparison.py --reps 5 --intents full
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path when run directly.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from rich.console import Console
from rich.table import Table

from agents.policy_validator import determine_decision, run_static_policies
from core.logger import setup_logging
from experiments.common.io import ResultStore
from experiments.common.metrics import score
from experiments.common.runners import MASRunner
from experiments.experiment_3.configs import (
    LLM_CONFIGS,
    LLM_ORDER,
    SMOKE_INTENT_IDS,
)

logger = logging.getLogger(__name__)
console = Console()

DEFAULT_DATASET = _ROOT / "experiments" / "datasets" / "intents_v2.json"
DEFAULT_OUT = _ROOT / "experiments" / "experiment_3" / "results"


# ── LLM hot-swap ──────────────────────────────────────────────────────────── #

def _apply_llm_config(cfg: dict) -> None:
    """
    Patch the module-level names in core.llm_core so the next LLMCore()
    instantiation uses the given model. Works because agents do
    `from config.settings import LLM_BACKEND, ...` which creates local
    bindings in the llm_core module namespace — directly patchable.
    """
    import core.llm_core as _llm_mod

    _llm_mod.LLM_BACKEND = cfg["backend"]
    if cfg["backend"] == "ollama":
        _llm_mod.OLLAMA_MODEL    = cfg["model"]
        _llm_mod.OLLAMA_BASE_URL = cfg.get("base_url", "http://localhost:11434")
    else:  # vllm
        _llm_mod.VLLM_MODEL    = cfg["model"]
        _llm_mod.VLLM_BASE_URL = cfg["base_url"]
        _llm_mod.VLLM_API_KEY  = cfg.get("api_key", "EMPTY")

    logger.info(
        "LLM config applied: backend=%s model=%s",
        cfg["backend"], cfg["model"],
    )


# ── Dataset helpers ────────────────────────────────────────────────────────── #

def load_dataset(path: Path) -> list[dict[str, Any]]:
    with open(path) as f:
        return json.load(f).get("prompts", [])


def filter_intents(
    prompts: list[dict[str, Any]],
    mode: str,
    ids: list[str] | None,
) -> list[dict[str, Any]]:
    if ids:
        keep = set(ids)
        out = [p for p in prompts if p["id"] in keep]
        missing = keep - {p["id"] for p in out}
        if missing:
            raise SystemExit(f"unknown intent ids: {sorted(missing)}")
        return out
    if mode == "smoke":
        keep = set(SMOKE_INTENT_IDS)
        return [p for p in prompts if p["id"] in keep]
    return prompts  # full


# ── Policy validation shim (same as experiment_2) ─────────────────────────── #

def _run_policy_validator(artifact: dict[str, Any]) -> None:
    if artifact.get("validation_report"):
        return
    cfgs = artifact.get("config_artifacts") or []
    checks = run_static_policies(cfgs)
    decision, reasons = determine_decision(checks)
    artifact["validation_report"] = {
        "topology_id": (artifact.get("topology") or {}).get("topology_id"),
        "policy_checks": checks,
        "dry_run": {"status": "SKIPPED", "errors": []},
        "conflicts": [],
        "decision": decision,
        "reasons": reasons,
        "violation_count": sum(int(c.get("violations", 0)) for c in checks.values()),
    }


# ── Single run ────────────────────────────────────────────────────────────── #

def run_one(
    runner: MASRunner,
    llm_id: str,
    intent: dict[str, Any],
    rep: int,
) -> dict[str, Any]:
    intent_with_rep = dict(intent)
    intent_with_rep["_rep"] = rep

    artifact = runner.run(intent_with_rep, deploy=False)
    artifact["rep"] = rep

    _run_policy_validator(artifact)

    # score() expects artifact["system"] to exist; we override after scoring
    artifact["system"] = llm_id
    record = score(artifact, intent)

    # Stamp llm_id alongside the standard 'system' field
    record["system"] = llm_id
    record["llm_id"] = llm_id
    record["llm_name"] = LLM_CONFIGS[llm_id]["name"]
    record["llm_size_b"] = LLM_CONFIGS[llm_id]["size_b"]
    record["_artifact_run_id"] = artifact.get("run_id")

    if artifact.get("error"):
        record["error"] = artifact["error"]
    return record


# ── Summary table ─────────────────────────────────────────────────────────── #

def _print_summary(store: ResultStore, llm_ids: list[str]) -> None:
    by_llm: dict[str, list[dict]] = {lid: [] for lid in llm_ids}
    if store.jsonl_path.exists():
        with open(store.jsonl_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                lid = rec.get("llm_id") or rec.get("system")
                if lid in by_llm:
                    by_llm[lid].append(rec)

    def _mean(rows: list[dict], key: str) -> str:
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return f"{sum(vals)/len(vals):.3f}" if vals else "-"

    table = Table(title="Experiment 3 — LLM comparison (means)")
    table.add_column("LLM")
    table.add_column("size (B)", justify="right")
    table.add_column("n", justify="right")
    table.add_column("intent_acc", justify="right")
    table.add_column("res_acc", justify="right")
    table.add_column("err_rate", justify="right")
    table.add_column("pol_viol", justify="right")
    table.add_column("interv", justify="right")
    table.add_column("deploy_t (s)", justify="right")

    for lid in LLM_ORDER:
        if lid not in by_llm:
            continue
        rows = by_llm[lid]
        cfg = LLM_CONFIGS[lid]
        table.add_row(
            cfg["name"],
            str(cfg["size_b"]),
            str(len(rows)),
            _mean(rows, "intent_to_deploy_accuracy"),
            _mean(rows, "resource_accuracy"),
            _mean(rows, "config_error_rate"),
            _mean(rows, "policy_violation_rate"),
            _mean(rows, "interventions"),
            _mean(rows, "deployment_time_s"),
        )
    console.print(table)


# ── Main ──────────────────────────────────────────────────────────────────── #

def main() -> None:
    parser = argparse.ArgumentParser(description="Experiment 3: LLM comparison")
    parser.add_argument(
        "--llms",
        nargs="+",
        default=list(LLM_CONFIGS.keys()),
        choices=list(LLM_CONFIGS.keys()),
        metavar="LLM_ID",
        help=f"LLM ids to test (default: all). Choices: {', '.join(LLM_CONFIGS)}",
    )
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument(
        "--intents",
        choices=["smoke", "full"],
        default="full",
        help="smoke=4 intents, full=36 intents",
    )
    parser.add_argument(
        "--intent-ids",
        default=None,
        help="explicit comma-separated intent IDs (overrides --intents)",
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="drop rows with errors from the store and retry them",
    )
    args = parser.parse_args()

    setup_logging()

    explicit_ids = (
        [s.strip() for s in args.intent_ids.split(",") if s.strip()]
        if args.intent_ids else None
    )
    prompts = filter_intents(
        load_dataset(args.dataset), args.intents, explicit_ids
    )

    llm_ids = args.llms
    store = ResultStore(args.out, retry_failed=args.retry_failed)
    progress = store.progress(llm_ids, [p["id"] for p in prompts], args.reps)

    console.rule("[bold]Experiment 3 — LLM Comparison")
    console.print(
        f"llms={llm_ids}  intents={len(prompts)}  reps={args.reps}  "
        f"mode=config-only  out={args.out}"
    )
    console.print(
        f"resume: {progress['done']}/{progress['total']} runs done — "
        f"{progress['remaining']} to go"
    )

    # Build the MAS runner once — graph structure is LLM-independent.
    # The LLM is hot-swapped via _apply_llm_config() before each LLM batch.
    runner = MASRunner()

    fail_count = 0

    try:
        for llm_id in llm_ids:
            cfg = LLM_CONFIGS[llm_id]
            console.rule(f"[bold cyan]{cfg['name']}[/bold cyan]  ({cfg['size_b']}B  {cfg['backend']})")

            _apply_llm_config(cfg)

            for intent in prompts:
                for rep in range(args.reps):
                    if store.is_done(llm_id, intent["id"], rep):
                        continue

                    label = f"[{llm_id}] {intent['id']} rep={rep}"
                    console.print(f"\n[cyan]{label}[/cyan]  starting...")
                    t0 = time.time()

                    try:
                        record = run_one(runner, llm_id, intent, rep)
                    except Exception as exc:
                        logger.exception("run_one failed: %s", label)
                        fail_count += 1
                        record = {
                            "system": llm_id,
                            "llm_id": llm_id,
                            "llm_name": cfg["name"],
                            "llm_size_b": cfg["size_b"],
                            "intent_id": intent["id"],
                            "rep": rep,
                            "complexity": intent.get("complexity"),
                            "ue_band": intent.get("ue_band"),
                            "scenario": intent.get("scenario"),
                            "deployment_time_s": None,
                            "config_error_rate": None,
                            "resource_accuracy": None,
                            "interventions": None,
                            "intent_to_deploy_accuracy": None,
                            "policy_violation_rate": None,
                            "error": str(exc)[:300],
                        }

                    elapsed = time.time() - t0
                    store.append(record)
                    err = record.get("error")
                    if err:
                        fail_count += 1
                    flag = f"[red]ERR: {str(err)[:60]}[/red]" if err else "[green]ok[/green]"
                    console.print(
                        f"  {flag}  "
                        f"intent_acc={record.get('intent_to_deploy_accuracy')}  "
                        f"res_acc={record.get('resource_accuracy')}  "
                        f"err_rate={record.get('config_error_rate')}  "
                        f"pol_viol={record.get('policy_violation_rate')}  "
                        f"({elapsed:.1f}s)"
                    )

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted — partial results saved.[/yellow]")

    console.rule("[bold]Summary")
    _print_summary(store, llm_ids)

    if fail_count:
        console.print(f"\n[red]{fail_count} failed runs — check raw.jsonl for details.[/red]")
    console.print(f"\nResults → {args.out}/runs.csv")
    console.print(
        f"Report  → python experiments/experiment_3/report.py "
        f"--results {args.out}/runs.csv --out {args.out}/figures"
    )


if __name__ == "__main__":
    main()
