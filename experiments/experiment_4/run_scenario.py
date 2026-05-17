"""
Experiment 4 — Scenario Runner.

Runs one repetition of a closed-loop runtime scenario end-to-end and writes
a JSON artifact under ``results/runs/`` that ``analyze_results.py`` later
aggregates into a CSV.

Lifecycle of one run:
  1. Bootstrap: build the post-deployment graph and load the topology
     snapshot from Redis (so the agents know which VNFs exist).
  2. Baseline: drive *baseline_cycles* monitoring cycles to characterise
     the pre-fault state (counts any false-positive alerts).
  3. Inject:   call inject_fault.py to apply the F1 / F2 / F3 fault.
  4. Observe:  drive *recovery_cycles* additional monitoring cycles. For
     each cycle, capture timestamps, anomaly alerts, the planner's plan
     (if any), and any execution results.
  5. Recover:  detect the first cycle in which no anomaly alert fires
     after the plan has been executed; record that as the recovery time.
  6. Persist:  write a structured JSON artifact for offline analysis.

The two systems-under-test are:
  - MAS:     full pipeline (default).
  - B_static: bypass the Planner & executors by setting the
              HITL_REMEDIATION_AUTO_APPROVE=1 *and* MAS_BYPASS_REMEDIATION=1
              env vars; the cycle still runs the detectors but never acts.

Usage:
    python run_scenario.py --scenario F1 --rep 1 --system MAS
    python run_scenario.py --scenario F2 --rep 2 --system B_static --baseline-cycles 4
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make the project root importable regardless of where the script is launched.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from core.graph import build_post_deployment_graph  # noqa: E402
from core.state import OrchestratorState  # noqa: E402

logger = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
INJECT_SCRIPT = HERE / "inject_fault.py"
RESULTS_DIR = HERE / "results" / "runs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bootstrap_state() -> OrchestratorState:
    """Pre-populate state with topology + resource_allocation from Redis."""
    state: OrchestratorState = {
        "user_intent": "monitor",
        "parsed_intent": {},
        "topology": None,
        "resource_allocation": None,
        "config_artifacts": [],
        "validation_report": None,
        "current_metrics": [],
        "anomaly_alerts": [],
        "sla_status": [],
        "remediation_plan": None,
        "execution_results": [],
        "deployment_results": [],
        "messages": [],
        "intervention_log": [],
        "current_agent": "start",
        "requires_approval": False,
        "user_approved": True,  # auto-approve by default; gate may still pause
        "error": None,
        "phase": "post_deployment",
    }
    try:
        from infra.redis_client import RedisClient
        rc = RedisClient()
        if rc.ping():
            state["topology"] = rc.get_topology()
            state["resource_allocation"] = rc.get_resource_allocation()
    except Exception as exc:
        logger.warning("Redis bootstrap failed (non-critical): %s", exc)
    return state


def _run_cycle(graph: Any, state: OrchestratorState, config: dict,
               prev_counts: dict[str, int] | None = None) -> dict[str, Any]:
    """
    Drive the post-deployment graph through one full monitoring cycle.
    Returns a dict with the cycle's timing and key outputs.

    ``anomaly_alerts`` and ``execution_results`` are append-only in the
    LangGraph state (reducer = operator.add), so the cumulative list grows
    every cycle. We compute the *delta* against ``prev_counts`` so callers
    see only what was produced this cycle.
    """
    prev = prev_counts or {"anomaly_alerts": 0, "execution_results": 0}
    started_at = _now()
    try:
        result = graph.invoke(state, config)
    except Exception as exc:
        return {
            "started_at": started_at,
            "finished_at": _now(),
            "error": str(exc),
            "anomaly_alerts": [],
            "remediation_plan": None,
            "execution_results": [],
            "_cumulative_counts": prev,
        }

    # The post-deployment graph uses interrupt_before=["user_decision_remediation"]
    # to support HITL. After the gate runs the graph PAUSES regardless of whether
    # approval is required (the gate may have already auto-approved). So we
    # always resume here. If the gate set requires_approval=True, force-approve
    # for the experiment harness (operator-in-the-loop is out of scope here;
    # B_static / MAS-auto are the two systems-under-test).
    if result.get("current_agent") == "remediation_review":
        graph.update_state(
            config,
            {"user_approved": True, "requires_approval": False},
        )
        try:
            result = graph.invoke(None, config)
        except Exception as exc:
            return {
                "started_at": started_at,
                "finished_at": _now(),
                "error": f"resume failed: {exc}",
                "anomaly_alerts": [],
                "remediation_plan": None,
                "execution_results": [],
                "_cumulative_counts": prev,
            }

    cumulative_alerts = result.get("anomaly_alerts") or []
    cumulative_execs = result.get("execution_results") or []
    new_alerts = cumulative_alerts[prev["anomaly_alerts"]:]
    new_execs = cumulative_execs[prev["execution_results"]:]

    return {
        "started_at": started_at,
        "finished_at": _now(),
        "error": result.get("error"),
        "current_agent": result.get("current_agent"),
        "anomaly_alerts": new_alerts,
        "remediation_plan": result.get("remediation_plan"),
        "execution_results": new_execs,
        "intervention_log": result.get("intervention_log") or [],
        "_cumulative_counts": {
            "anomaly_alerts": len(cumulative_alerts),
            "execution_results": len(cumulative_execs),
        },
    }


def _has_alert(cycle: dict[str, Any]) -> bool:
    return bool(cycle.get("anomaly_alerts"))


def _has_action_executed(cycle: dict[str, Any]) -> bool:
    return any(
        r.get("status") in ("success", "failed")
        for r in cycle.get("execution_results", [])
    )


def _alert_fingerprint(alert: dict[str, Any]) -> str:
    """
    Stable identity for a single alert: type + first affected (vnf, metric).
    Mirrors the same approach used inside agents/anomaly_detector.py so the
    recovery heuristic can match the agent's deduplication.
    """
    affected = alert.get("affected_metrics") or []
    metric_name = affected[0].get("name", "") if affected else ""
    description = alert.get("description", "") or ""
    vnf = description.split(" ", 1)[0] if description else ""
    return f"{alert.get('type', '')}|{vnf}|{metric_name}"


def _trigger_fingerprint(cycles: list[dict[str, Any]],
                         alert_idx: int | None) -> str | None:
    """Fingerprint of the alert that triggered the first plan."""
    if alert_idx is None or alert_idx >= len(cycles):
        return None
    alerts = cycles[alert_idx].get("anomaly_alerts") or []
    if not alerts:
        return None
    return _alert_fingerprint(alerts[0])


def _cycle_has_fingerprint(cycle: dict[str, Any], fp: str) -> bool:
    return any(
        _alert_fingerprint(a) == fp for a in (cycle.get("anomaly_alerts") or [])
    )


def _detect_recovery(cycles: list[dict[str, Any]], action_idx: int | None,
                     consecutive_clean: int = 2,
                     trigger_fp: str | None = None) -> int | None:
    """
    Recovery is declared when N consecutive cycles after the first executed
    action no longer carry the **triggering** alert's fingerprint. When no
    fingerprint is available we fall back to the original "no anomaly
    alerts at all" rule.

    Persistent unrelated alerts (e.g. infrastructure-baseline jitter on
    other VNFs) do not block recovery: only the originally-triggering
    signal counts.

    Returns the cycle index where recovery is first observed, or None.
    """
    if action_idx is None:
        return None
    streak = 0
    for i in range(action_idx + 1, len(cycles)):
        cycle = cycles[i]
        if trigger_fp is not None:
            still_present = _cycle_has_fingerprint(cycle, trigger_fp)
        else:
            still_present = _has_alert(cycle)
        if still_present:
            streak = 0
            continue
        streak += 1
        if streak >= consecutive_clean:
            return i
    return None


def inject_fault(scenario: str, target: str, duration: int, namespace: str,
                 streams: int, loop: int) -> dict[str, Any]:
    """Invoke inject_fault.py as a subprocess and return its JSON record."""
    cmd = [
        sys.executable, str(INJECT_SCRIPT),
        "--scenario", scenario,
        "--namespace", namespace,
        "--no-cleanup",  # the runner does cleanup itself after the observation window
    ]
    if scenario in ("F1", "F2"):
        cmd += ["--target", target]
    if scenario in ("F1", "F3"):
        cmd += ["--duration", str(duration)]
    if scenario == "F3":
        cmd += ["--streams", str(streams)]
    if scenario == "F2":
        cmd += ["--loop", str(loop)]

    logger.info("Injecting fault: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"fault injection failed (rc={proc.returncode}):\n{proc.stderr}")

    # The script prints its JSON record to stdout as the last block.
    last_brace = proc.stdout.rfind("{")
    return json.loads(proc.stdout[last_brace:])


def cleanup_fault(record: dict[str, Any], namespace: str) -> None:
    """Remove injection sidecar pods (stress / iperf)."""
    pods = [p for p in (record.get("stress_pod"), record.get("iperf_pod")) if p]
    for pod in pods:
        try:
            subprocess.run(
                ["kubectl", "delete", "pod", pod, "-n", namespace,
                 "--ignore-not-found", "--grace-period=0", "--force"],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as exc:
            logger.warning("cleanup of %s failed: %s", pod, exc)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.system == "B_static":
        # Baseline: detector pipeline still runs but the planner-driven
        # remediation is suppressed. We do this by always auto-approving the
        # gate AND by clearing the plan before the executors see it. The
        # easiest way is to set HITL_REMEDIATION_AUTO_APPROVE=1 and patch
        # the planner agent to no-op via the env var.
        os.environ["HITL_REMEDIATION_AUTO_APPROVE"] = "1"
        os.environ["MAS_BYPASS_REMEDIATION"] = "1"
    else:
        os.environ.pop("MAS_BYPASS_REMEDIATION", None)
        # MAS may still auto-approve to avoid manual intervention during
        # the experiment (this is the "MAS-auto" variant; for "MAS-HITL"
        # the experimenter would unset this).
        os.environ.setdefault("HITL_REMEDIATION_AUTO_APPROVE", "1")

    run_id = f"{args.scenario}-{args.system}-rep{args.rep}-{uuid.uuid4().hex[:6]}"
    logger.info("=== Run %s ===", run_id)

    graph, _ = build_post_deployment_graph()
    config = {"configurable": {"thread_id": run_id}}
    state = _bootstrap_state()

    # 1. Baseline cycles
    baseline: list[dict[str, Any]] = []
    counts = {"anomaly_alerts": 0, "execution_results": 0}
    for i in range(args.baseline_cycles):
        logger.info("Baseline cycle %d/%d", i + 1, args.baseline_cycles)
        c = _run_cycle(graph, state, config, prev_counts=counts)
        c["phase"] = "baseline"
        baseline.append(c)
        counts = c.get("_cumulative_counts", counts)
        time.sleep(args.cycle_interval)

    # 2. Fault injection
    fault: dict[str, Any]
    try:
        fault = inject_fault(
            args.scenario, args.target, args.duration,
            args.namespace, args.streams, args.loop,
        )
    except Exception as exc:
        logger.error("fault injection failed: %s", exc)
        fault = {"error": str(exc), "started_at": _now()}

    fault_started_at = fault.get("started_at", _now())

    # 3. Recovery cycles
    cycles: list[dict[str, Any]] = []
    first_alert_idx: int | None = None
    first_plan_idx: int | None = None
    first_action_idx: int | None = None
    for i in range(args.recovery_cycles):
        logger.info("Recovery cycle %d/%d", i + 1, args.recovery_cycles)
        c = _run_cycle(graph, state, config, prev_counts=counts)
        c["phase"] = "recovery"
        cycles.append(c)
        counts = c.get("_cumulative_counts", counts)
        if first_alert_idx is None and _has_alert(c):
            first_alert_idx = i
        if first_plan_idx is None and c.get("remediation_plan"):
            first_plan_idx = i
        if first_action_idx is None and _has_action_executed(c):
            first_action_idx = i
        # Early exit: 3 consecutive clean cycles after an action is good evidence of recovery
        if (
            first_action_idx is not None
            and i - first_action_idx >= 3
            and all(not _has_alert(cc) for cc in cycles[-3:])
        ):
            logger.info("Early-exit: recovered after %d cycles", i + 1)
            break
        time.sleep(args.cycle_interval)

    trigger_fp = _trigger_fingerprint(cycles, first_alert_idx)
    recovered_idx = _detect_recovery(cycles, first_action_idx, trigger_fp=trigger_fp)
    logger.info(
        "Recovery analysis: trigger_fp=%r action_idx=%s recovered_idx=%s",
        trigger_fp, first_action_idx, recovered_idx,
    )

    # 4. Cleanup
    if isinstance(fault, dict):
        cleanup_fault(fault, args.namespace)

    # 5. Build derived metrics
    def _ts(idx: int | None) -> str | None:
        if idx is None or idx >= len(cycles):
            return None
        return cycles[idx]["started_at"]

    def _delta(a: str | None, b: str | None) -> float | None:
        if not a or not b:
            return None
        ta = datetime.fromisoformat(a.replace("Z", "+00:00"))
        tb = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return (tb - ta).total_seconds()

    record = {
        "run_id": run_id,
        "scenario": args.scenario,
        "system": args.system,
        "rep": args.rep,
        "fault": fault,
        "baseline_cycles": baseline,
        "recovery_cycles_detail": cycles,
        "indices": {
            "first_alert": first_alert_idx,
            "first_plan": first_plan_idx,
            "first_action": first_action_idx,
            "recovered": recovered_idx,
        },
        "trigger_fingerprint": trigger_fp,
        "timing": {
            "fault_started_at": fault_started_at,
            "first_alert_at": _ts(first_alert_idx),
            "first_plan_at": _ts(first_plan_idx),
            "first_action_at": _ts(first_action_idx),
            "recovered_at": _ts(recovered_idx),
        },
        "metrics": {
            "detection_latency_s": _delta(fault_started_at, _ts(first_alert_idx)),
            "plan_latency_s": _delta(_ts(first_alert_idx), _ts(first_plan_idx)),
            "action_latency_s": _delta(_ts(first_plan_idx), _ts(first_action_idx)),
            "recovery_time_s": _delta(fault_started_at, _ts(recovered_idx)),
            "remediation_success": recovered_idx is not None,
            "false_positives_pre_fault": sum(1 for c in baseline if _has_alert(c)),
            "human_interventions": sum(
                len(c.get("intervention_log", [])) for c in (baseline + cycles)
            ),
        },
        "config": {
            "baseline_cycles": args.baseline_cycles,
            "recovery_cycles": args.recovery_cycles,
            "cycle_interval_s": args.cycle_interval,
            "fault_duration_s": args.duration,
            "namespace": args.namespace,
            "target": args.target,
            "streams": args.streams,
            "loop": args.loop,
        },
        "completed_at": _now(),
    }

    return record


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", choices=["F1", "F2", "F3"], required=True)
    p.add_argument("--system", choices=["MAS", "B_static"], default="MAS")
    p.add_argument("--rep", type=int, default=1)
    p.add_argument("--target", default="oai-amf",
                   help="Target VNF prefix for F1 / F2.")
    p.add_argument("--namespace", default="oai-5g")
    p.add_argument("--baseline-cycles", type=int, default=3)
    p.add_argument("--recovery-cycles", type=int, default=12)
    p.add_argument("--cycle-interval", type=int, default=15,
                   help="Seconds between monitoring cycles.")
    p.add_argument("--duration", type=int, default=60,
                   help="Fault duration (F1 / F3) in seconds.")
    p.add_argument("--streams", type=int, default=50)
    p.add_argument("--loop", type=int, default=1)
    p.add_argument("--output", help="Override output JSON path.")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    record = run(args)

    out_dir = Path(args.output).parent if args.output else RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output) if args.output else out_dir / f"{record['run_id']}.json"
    out_path.write_text(json.dumps(record, indent=2, default=str))
    logger.info("Run record written to %s", out_path)

    m = record["metrics"]
    logger.info(
        "Result: detection=%.1fs  plan=%.1fs  action=%.1fs  recovery=%s  success=%s",
        m["detection_latency_s"] or -1,
        m["plan_latency_s"] or -1,
        m["action_latency_s"] or -1,
        m["recovery_time_s"],
        m["remediation_success"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
