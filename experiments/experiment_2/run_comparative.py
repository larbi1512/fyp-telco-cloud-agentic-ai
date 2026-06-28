#!/usr/bin/env python3
"""
Experiment 2 — Pre-deployment comparative runner.

Iterates the cartesian product (system × intent × rep), runs each system
in isolation, scores its output through the unified metrics layer, and
appends one row per run to a JSONL + CSV. Crash-safe: re-running picks
up where it left off via experiments.common.io.ResultStore.

Usage examples:
  # 5-system × 4-intent × 1-rep smoke test, no cluster contact:
  python experiments/experiment_2/run_comparative.py \\
      --systems mas,b1,b2,b3,b4 \\
      --intent-ids S01,M01,M03,C03 \\
      --reps 1 --no-deploy \\
      --out experiments/experiment_2/results/main

  # Full run (5 × 36 × 5) with real deployment for MAS and B3:
  python experiments/experiment_2/run_comparative.py \\
      --systems mas,b1,b2,b3,b4 --reps 5 --deploy \\
      --out experiments/experiment_2/results/main
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from agents.deployer import CHART_MAP, teardown_releases
from agents.policy_validator import determine_decision, run_static_policies
from config.settings import K8S_NAMESPACE
from core.logger import setup_logging
from experiments.common.io import ResultStore
from experiments.common.k8s_wait import wait_for_pods_ready
from experiments.common.metrics import score
from experiments.common.runners import RUNNERS, get_runner

# Force registration of all baselines.
import experiments.baselines  # noqa: F401

logger = logging.getLogger(__name__)
console = Console()


# Systems that perform real cluster work when deploy=True. B1/B2/B4 do not
# deploy — their wall-clock and artifacts come from the emulators / LLM only.
DEPLOYING_SYSTEMS = {"mas", "b3", "b4r", "confucius", "ossgpt", "lin"}

DEFAULT_DATASET = (
    Path(__file__).resolve().parents[1] / "datasets" / "intents_v2.json"
)


def load_dataset(path: Path) -> list[dict[str, Any]]:
    with open(path) as f:
        return json.load(f).get("prompts", [])


def filter_intents(
    prompts: list[dict[str, Any]], ids: list[str] | None
) -> list[dict[str, Any]]:
    if not ids:
        return prompts
    keep = set(ids)
    out = [p for p in prompts if p["id"] in keep]
    missing = keep - {p["id"] for p in out}
    if missing:
        raise SystemExit(f"unknown intent ids in --intent-ids: {sorted(missing)}")
    return out


def run_policy_validator_on_artifact(artifact: dict[str, Any]) -> None:
    """Mutates artifact in place so it carries a validation_report.

    MAS already populates this internally; B1/B2/B3/B4 do not. The harness
    runs the same policy_validator code path so all systems are scored
    against the same checks.
    """
    if artifact.get("validation_report"):
        return  # MAS already populated it
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


def deploy_artifact_and_wait(artifact: dict[str, Any]) -> None:
    """For systems that emit real Helm artifacts (currently only MAS in the
    full-deploy mode). MAS's deployer node already does install + tracks
    deployment_results, but we additionally wait for pod readiness and stamp
    deployment_wait_s for Metric 1.

    For B3, the artifacts are valid Helm values but we do NOT auto-install
    here — the plan reserves real installs for MAS. B3 is treated as
    artifact-only at this layer so the cluster only sees one occupant. If you
    want B3 to actually deploy, change DEPLOYING_SYSTEMS to include it AND
    serialise runs (no concurrent helm installs).
    """
    if not artifact.get("deployment_results"):
        return
    rels = artifact.get("release_names") or list(CHART_MAP.values())
    wait_start = time.time()
    try:
        report = wait_for_pods_ready(
            release_names=rels,
            namespace=K8S_NAMESPACE,
            timeout_s=600.0,
        )
        artifact["deployment_wait_s"] = report["elapsed_s"]
        artifact["_pod_wait_report"] = report
    except Exception as e:
        logger.warning("wait_for_pods_ready failed: %s", e)
        artifact["deployment_wait_s"] = round(time.time() - wait_start, 2)


def cluster_cleanup_between_runs(system: str) -> None:
    """Tear down OAI releases so the next deploying-system run starts clean."""
    if system not in DEPLOYING_SYSTEMS:
        return
    try:
        teardown_releases(namespace=K8S_NAMESPACE)
    except Exception as e:
        logger.warning("teardown between runs failed: %s", e)


def run_one(
    system: str,
    intent: dict[str, Any],
    rep: int,
    deploy_flag: bool,
) -> dict[str, Any]:
    runner = get_runner(system)
    intent_with_rep = dict(intent)
    intent_with_rep["_rep"] = rep  # B1/B2 use this for deterministic seeds

    deploy_now = deploy_flag and (system in DEPLOYING_SYSTEMS)
    if deploy_now:
        cluster_cleanup_between_runs(system)

    artifact = runner.run(intent_with_rep, deploy=deploy_now)
    artifact["rep"] = rep

    if deploy_now:
        deploy_artifact_and_wait(artifact)

    run_policy_validator_on_artifact(artifact)
    record = score(artifact, intent)
    record["_artifact_run_id"] = artifact.get("run_id")
    if artifact.get("error"):
        record["error"] = artifact["error"]
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-deployment comparative runner")
    parser.add_argument("--systems", default="mas,b1,b2,b3,b4",
                        help="comma-separated list; subset of "
                             + ",".join(sorted(RUNNERS)))
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--intent-ids", default=None,
                        help="optional comma-separated intent IDs to filter "
                             "(e.g. S01,M03,C03)")
    parser.add_argument("--reps", type=int, default=5)
    deploy_grp = parser.add_mutually_exclusive_group()
    deploy_grp.add_argument("--deploy", dest="deploy", action="store_true",
                            help="actually run helm install for deploying "
                                 "systems (mas, b3) and wait for pods Ready")
    deploy_grp.add_argument("--no-deploy", dest="deploy", action="store_false",
                            help="skip cluster contact entirely (smoke mode)")
    parser.set_defaults(deploy=True)
    parser.add_argument("--out", type=Path, default=Path("experiments/experiment_2/results/run"))
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the dataset to the first N intents")
    parser.add_argument("--retry-failed", action="store_true",
                        help="drop rows with non-empty error from the store "
                             "and re-attempt them this session")
    args = parser.parse_args()

    setup_logging()

    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    for s in systems:
        if s not in RUNNERS:
            raise SystemExit(f"unknown system: {s!r}; available: {sorted(RUNNERS)}")

    intent_ids = [s.strip() for s in args.intent_ids.split(",")] if args.intent_ids else None
    prompts = filter_intents(load_dataset(args.dataset), intent_ids)
    if args.limit:
        prompts = prompts[: args.limit]

    store = ResultStore(args.out, retry_failed=args.retry_failed)
    progress = store.progress(systems, [p["id"] for p in prompts], args.reps)

    console.rule(f"[bold]Experiment 2 — Comparative")
    console.print(
        f"systems={systems}  intents={len(prompts)}  reps={args.reps}  "
        f"deploy={args.deploy}  out={args.out}"
    )
    console.print(
        f"resume: {progress['done']}/{progress['total']} runs already done — "
        f"{progress['remaining']} to go"
    )

    fail_count = 0
    completed = 0

    try:
        for system in systems:
            for intent in prompts:
                for rep in range(args.reps):
                    if store.is_done(system, intent["id"], rep):
                        continue
                    label = f"[{system}] {intent['id']} rep={rep}"
                    console.print(f"\n[cyan]{label}[/cyan]  starting...")
                    t0 = time.time()
                    try:
                        record = run_one(system, intent, rep, args.deploy)
                    except Exception as e:
                        logger.exception("run_one failed: %s", label)
                        fail_count += 1
                        record = {
                            "system": system,
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
                            "error": str(e)[:300],
                        }
                    elapsed = time.time() - t0
                    store.append(record)
                    completed += 1
                    err = record.get("error")
                    if err:
                        fail_count += 1
                    flag = f"[red]ERR: {err[:60]}[/red]" if err else "[green]ok[/green]"
                    console.print(
                        f"  {flag}  "
                        f"deploy_t={record.get('deployment_time_s')}  "
                        f"err_rate={record.get('config_error_rate')}  "
                        f"res_acc={record.get('resource_accuracy')}  "
                        f"interv={record.get('interventions')}  "
                        f"intent_acc={record.get('intent_to_deploy_accuracy')}  "
                        f"pol_viol={record.get('policy_violation_rate')}  "
                        f"({elapsed:.1f}s)"
                    )
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted — partial results saved[/yellow]")

    console.rule("[bold]Summary")
    summary_table = Table(title="Per-system mean (this session)")
    summary_table.add_column("system")
    summary_table.add_column("n")
    summary_table.add_column("deploy_t (s)", justify="right")
    summary_table.add_column("err_rate", justify="right")
    summary_table.add_column("res_acc", justify="right")
    summary_table.add_column("interv", justify="right")
    summary_table.add_column("intent_acc", justify="right")
    summary_table.add_column("pol_viol", justify="right")

    # Re-read JSONL to compute per-system means
    by_system: dict[str, list[dict[str, Any]]] = {s: [] for s in systems}
    if store.jsonl_path.exists():
        with open(store.jsonl_path) as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("system") in by_system:
                    by_system[rec["system"]].append(rec)

    def _mean_or_dash(rows: list[dict[str, Any]], key: str) -> str:
        vals = [r.get(key) for r in rows if isinstance(r.get(key), (int, float))]
        if not vals:
            return "-"
        return f"{sum(vals) / len(vals):.3f}"

    for s in systems:
        rows = by_system[s]
        summary_table.add_row(
            s, str(len(rows)),
            _mean_or_dash(rows, "deployment_time_s"),
            _mean_or_dash(rows, "config_error_rate"),
            _mean_or_dash(rows, "resource_accuracy"),
            _mean_or_dash(rows, "interventions"),
            _mean_or_dash(rows, "intent_to_deploy_accuracy"),
            _mean_or_dash(rows, "policy_violation_rate"),
        )
    console.print(summary_table)
    console.print(
        f"\nthis session: completed={completed} failed={fail_count}; "
        f"total in store: {len(sum(by_system.values(), []))}"
    )
    console.print(f"raw:  {store.jsonl_path}")
    console.print(f"csv:  {store.csv_path}")

    if fail_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
