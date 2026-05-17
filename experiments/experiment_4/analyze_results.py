"""
Experiment 4 — Results Aggregation & Plotting.

Walks ``results/runs/`` for per-scenario JSON artifacts produced by
``run_scenario.py``, flattens them into a single CSV at ``results/runs.csv``,
prints a per-(scenario, system) summary table, and writes four core plots
under ``figures/``:

  - bar_detection_latency.png  — mean detection latency, scenario × system
  - bar_recovery_time.png      — mean time-to-recovery, scenario × system
  - heatmap_success.png        — remediation success rate (%)
  - box_false_positives.png    — false-positive count in pre-fault baseline

Re-run after every batch of scenarios:
    python analyze_results.py
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

logger = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
RUNS_DIR = HERE / "results" / "runs"
SUMMARY_CSV = HERE / "results" / "runs.csv"
FIGURES_DIR = HERE / "figures"

CSV_FIELDS = [
    "run_id", "scenario", "system", "rep",
    "detection_latency_s", "detection_latency_fingerprint_s",
    "plan_latency_s", "action_latency_s",
    "recovery_time_s", "remediation_success",
    "false_positives_pre_fault", "human_interventions",
    "n_plans_llm", "n_plans_heuristic",
    "first_alert_idx", "first_plan_idx", "first_action_idx", "recovered_idx",
    "trigger_fingerprint", "fault_started_at", "completed_at",
]


# ───── Helpers (mirror run_scenario.py to keep the analyzer self-contained) ─────

def _alert_fingerprint(alert: dict) -> str:
    """type|vnf|metric — must match run_scenario._alert_fingerprint exactly."""
    affected = alert.get("affected_metrics") or []
    metric_name = affected[0].get("name", "") if affected else ""
    description = alert.get("description", "") or ""
    vnf = description.split(" ", 1)[0] if description else ""
    return f"{alert.get('type', '')}|{vnf}|{metric_name}"


def _infer_plan_source(plan: dict) -> str:
    """Prefer explicit `source` field; fall back to diagnosis-text heuristic
    so this works on both old and new run JSONs."""
    src = plan.get("source")
    if src in ("llm", "heuristic"):
        return src
    diag = str(plan.get("diagnosis", ""))
    if diag.startswith("Auto-generated plan for"):
        return "heuristic"
    return "llm"


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _per_fingerprint_latency(record: dict) -> float | None:
    """Time from fault_started_at to the first alert whose fingerprint matches
    the run's trigger_fingerprint. None if no match found."""
    trigger_fp = record.get("trigger_fingerprint")
    if not trigger_fp:
        return None
    t0 = _parse_iso((record.get("timing") or {}).get("fault_started_at"))
    if t0 is None:
        return None
    for cyc in record.get("recovery_cycles_detail", []) or []:
        for alert in cyc.get("anomaly_alerts", []) or []:
            if _alert_fingerprint(alert) == trigger_fp:
                t1 = _parse_iso(cyc.get("started_at"))
                return (t1 - t0).total_seconds() if t1 else None
    return None


def _count_plan_sources(record: dict) -> tuple[int, int]:
    """(n_llm, n_heuristic) across all cycles in a run."""
    llm = heu = 0
    for cyc in record.get("recovery_cycles_detail", []) or []:
        plan = cyc.get("remediation_plan")
        if not plan:
            continue
        src = _infer_plan_source(plan)
        if src == "heuristic":
            heu += 1
        else:
            llm += 1
    return llm, heu


def _flatten(record: dict) -> dict:
    m = record.get("metrics", {})
    idx = record.get("indices", {})
    timing = record.get("timing", {})
    n_llm, n_heu = _count_plan_sources(record)
    return {
        "run_id": record.get("run_id", ""),
        "scenario": record.get("scenario", ""),
        "system": record.get("system", ""),
        "rep": record.get("rep", ""),
        "detection_latency_s": m.get("detection_latency_s"),
        "detection_latency_fingerprint_s": _per_fingerprint_latency(record),
        "plan_latency_s": m.get("plan_latency_s"),
        "action_latency_s": m.get("action_latency_s"),
        "recovery_time_s": m.get("recovery_time_s"),
        "remediation_success": int(bool(m.get("remediation_success"))),
        "false_positives_pre_fault": m.get("false_positives_pre_fault"),
        "human_interventions": m.get("human_interventions"),
        "n_plans_llm": n_llm,
        "n_plans_heuristic": n_heu,
        "first_alert_idx": idx.get("first_alert"),
        "first_plan_idx": idx.get("first_plan"),
        "first_action_idx": idx.get("first_action"),
        "recovered_idx": idx.get("recovered"),
        "trigger_fingerprint": record.get("trigger_fingerprint"),
        "fault_started_at": timing.get("fault_started_at"),
        "completed_at": record.get("completed_at"),
    }


def collect(runs_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for fp in sorted(runs_dir.glob("*.json")):
        try:
            record = json.loads(fp.read_text())
        except Exception as exc:
            logger.warning("Skipping %s — could not read: %s", fp.name, exc)
            continue
        rows.append(_flatten(record))
    return rows


def collect_full(runs_dir: Path) -> list[dict]:
    """Return the full per-run JSONs (not flattened) so that per-cycle
    walks can be done by Tasks A/B/C analyses."""
    out: list[dict] = []
    for fp in sorted(runs_dir.glob("*.json")):
        try:
            out.append(json.loads(fp.read_text()))
        except Exception as exc:
            logger.warning("Skipping %s — could not read: %s", fp.name, exc)
            continue
    return out


def write_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in CSV_FIELDS})


def _by_pair(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        out.setdefault((r["scenario"], r["system"]), []).append(r)
    return out


def _avg(values: list[float | None]) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return mean(nums) if nums else None


def _stdev(values: list[float | None]) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return stdev(nums) if len(nums) >= 2 else None


def print_summary(rows: list[dict]) -> None:
    grouped = _by_pair(rows)
    print(f"{'Scenario':<8} {'System':<10} {'N':>3} "
          f"{'detect_s':>10} {'recover_s':>10} {'success%':>9} {'FP_pre':>7}")
    print("-" * 65)
    for (scen, sys_), group in sorted(grouped.items()):
        det = _avg([r["detection_latency_s"] for r in group])
        rec = _avg([r["recovery_time_s"] for r in group])
        succ = _avg([r["remediation_success"] for r in group]) or 0
        fps = _avg([r["false_positives_pre_fault"] for r in group]) or 0
        det_s = f"{det:.1f}" if det is not None else "—"
        rec_s = f"{rec:.1f}" if rec is not None else "—"
        print(f"{scen:<8} {sys_:<10} {len(group):>3d} "
              f"{det_s:>10} {rec_s:>10} {succ * 100:>8.1f}% {fps:>7.1f}")


def plot_bar_metric(rows: list[dict], metric: str, ylabel: str,
                    title: str, out_path: Path) -> None:
    grouped = _by_pair(rows)
    scenarios = sorted({s for s, _ in grouped})
    systems = sorted({s for _, s in grouped})

    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(scenarios))
    width = 0.8 / max(len(systems), 1)
    for i, sys_ in enumerate(systems):
        vals: list[float] = []
        errs: list[float] = []
        for scen in scenarios:
            group = grouped.get((scen, sys_), [])
            v = _avg([r[metric] for r in group])
            e = _stdev([r[metric] for r in group])
            vals.append(v if v is not None else 0.0)
            errs.append(e if e is not None else 0.0)
        ax.bar(x + i * width, vals, width, label=sys_, yerr=errs, capsize=3)

    ax.set_xticks(x + width * (len(systems) - 1) / 2)
    ax.set_xticklabels(scenarios)
    ax.set_xlabel("Scenario")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_success_heatmap(rows: list[dict], out_path: Path) -> None:
    grouped = _by_pair(rows)
    scenarios = sorted({s for s, _ in grouped})
    systems = sorted({s for _, s in grouped})

    matrix = np.zeros((len(systems), len(scenarios)))
    for i, sys_ in enumerate(systems):
        for j, scen in enumerate(scenarios):
            group = grouped.get((scen, sys_), [])
            succ = _avg([r["remediation_success"] for r in group])
            matrix[i, j] = (succ or 0) * 100

    fig, ax = plt.subplots(figsize=(6, 3))
    im = ax.imshow(matrix, cmap="Greens", vmin=0, vmax=100, aspect="auto")
    ax.set_xticks(range(len(scenarios)))
    ax.set_xticklabels(scenarios)
    ax.set_yticks(range(len(systems)))
    ax.set_yticklabels(systems)
    for i in range(len(systems)):
        for j in range(len(scenarios)):
            ax.text(j, i, f"{matrix[i, j]:.0f}%", ha="center", va="center",
                    color="white" if matrix[i, j] > 50 else "black", fontsize=11)
    ax.set_title("Remediation Success Rate (%)")
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_false_positives_box(rows: list[dict], out_path: Path) -> None:
    grouped: dict[str, list[float]] = {}
    for r in rows:
        if r["false_positives_pre_fault"] is None:
            continue
        grouped.setdefault(r["system"], []).append(float(r["false_positives_pre_fault"]))

    if not grouped:
        return
    systems = sorted(grouped.keys())
    data = [grouped[s] for s in systems]
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.boxplot(data, labels=systems, showmeans=True)
    ax.set_ylabel("Alerts in pre-fault baseline window")
    ax.set_title("Pre-fault False Positives by System")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


# ───── Task A: Plan-source histogram (LLM vs heuristic) ─────

def plot_plan_source_histogram(records: list[dict], out_path: Path) -> None:
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for rec in records:
        sys_ = rec.get("system", "")
        scen = rec.get("scenario", "")
        for cyc in rec.get("recovery_cycles_detail", []) or []:
            plan = cyc.get("remediation_plan")
            if not plan:
                continue
            counts.setdefault((scen, sys_), {"llm": 0, "heuristic": 0})[_infer_plan_source(plan)] += 1

    plotting_keys = [k for k, c in counts.items() if c["llm"] + c["heuristic"] > 0]
    if not plotting_keys:
        logger.info("plot_plan_source_histogram: no plans found in any record")
        return

    scenarios = sorted({s for s, _ in plotting_keys})
    systems = sorted({sy for _, sy in plotting_keys})

    fig, ax = plt.subplots(figsize=(7, 4.5))
    x = np.arange(len(scenarios))
    width = 0.8 / max(len(systems), 1)
    llm_palette = ["#1f77b4", "#9bbcde", "#3a6e9b"]
    heu_palette = ["#ff7f0e", "#ffba8c", "#cc6309"]
    for i, sys_ in enumerate(systems):
        llm_vals = [counts.get((scen, sys_), {}).get("llm", 0) for scen in scenarios]
        heu_vals = [counts.get((scen, sys_), {}).get("heuristic", 0) for scen in scenarios]
        offset = x + i * width
        ax.bar(offset, llm_vals, width, label=f"{sys_} — LLM",
               color=llm_palette[i % len(llm_palette)])
        ax.bar(offset, heu_vals, width, bottom=llm_vals,
               label=f"{sys_} — heuristic",
               color=heu_palette[i % len(heu_palette)])
        for k, scen in enumerate(scenarios):
            total = llm_vals[k] + heu_vals[k]
            if total > 0:
                share = llm_vals[k] / total
                ax.text(offset[k], total + 0.3, f"{share:.0%} LLM",
                        ha="center", fontsize=8)

    ax.set_xticks(x + width * (len(systems) - 1) / 2)
    ax.set_xticklabels(scenarios)
    ax.set_xlabel("Scenario")
    ax.set_ylabel("Plans produced (count across all reps)")
    ax.set_title("Plan source: LLM-driven vs heuristic-fallback")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plan_source_table(records: list[dict]) -> str:
    """Markdown table: LLM/heuristic plan counts and LLM share per (scenario, system)."""
    counts: dict[tuple[str, str], dict[str, int]] = {}
    for rec in records:
        sys_ = rec.get("system", "")
        scen = rec.get("scenario", "")
        for cyc in rec.get("recovery_cycles_detail", []) or []:
            plan = cyc.get("remediation_plan")
            if not plan:
                continue
            counts.setdefault((scen, sys_), {"llm": 0, "heuristic": 0})[_infer_plan_source(plan)] += 1
    if not counts:
        return ""
    lines = ["| Scenario | System | LLM | Heuristic | Total | LLM share |",
             "|---|---|---:|---:|---:|---:|"]
    tot_llm = tot_heu = 0
    for (scen, sys_), c in sorted(counts.items()):
        total = c["llm"] + c["heuristic"]
        share = c["llm"] / total if total else 0
        tot_llm += c["llm"]
        tot_heu += c["heuristic"]
        lines.append(f"| {scen} | {sys_} | {c['llm']} | {c['heuristic']} | {total} | {share:.1%} |")
    grand = tot_llm + tot_heu
    grand_share = tot_llm / grand if grand else 0
    lines.append(f"| **All** | **All** | **{tot_llm}** | **{tot_heu}** | **{grand}** | **{grand_share:.1%}** |")
    return "\n".join(lines)


# ───── Task B: Per-fingerprint detection latency ─────

def plot_bar_fingerprint_latency(records: list[dict], out_path: Path) -> None:
    by_pair: dict[tuple[str, str], list[float]] = {}
    for rec in records:
        lat = _per_fingerprint_latency(rec)
        if lat is None:
            continue
        by_pair.setdefault((rec.get("scenario", ""), rec.get("system", "")), []).append(lat)
    if not by_pair:
        logger.info("plot_bar_fingerprint_latency: no fingerprint matches found")
        return

    scenarios = sorted({s for s, _ in by_pair})
    systems = sorted({s for _, s in by_pair})
    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(scenarios))
    width = 0.8 / max(len(systems), 1)
    for i, sys_ in enumerate(systems):
        vals: list[float] = []
        errs: list[float] = []
        for scen in scenarios:
            lats = by_pair.get((scen, sys_), [])
            vals.append(mean(lats) if lats else 0.0)
            errs.append(stdev(lats) if len(lats) >= 2 else 0.0)
        ax.bar(x + i * width, vals, width, label=sys_, yerr=errs, capsize=3)
    ax.set_xticks(x + width * (len(systems) - 1) / 2)
    ax.set_xticklabels(scenarios)
    ax.set_xlabel("Scenario")
    ax.set_ylabel("Latency to trigger fingerprint (s)")
    ax.set_title("Detection Latency to Trigger Fingerprint (resolves 'first alert' conflation)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def fingerprint_latency_table(records: list[dict]) -> str:
    """Markdown table: per-fingerprint detection latency (mean ± std) per (scenario, system).
    Includes the original first-alert latency for direct comparison."""
    rows: dict[tuple[str, str], dict[str, list[float]]] = {}
    for rec in records:
        scen = rec.get("scenario", "")
        sys_ = rec.get("system", "")
        first_alert_lat = (rec.get("metrics") or {}).get("detection_latency_s")
        fp_lat = _per_fingerprint_latency(rec)
        bucket = rows.setdefault((scen, sys_), {"first": [], "fp": []})
        if isinstance(first_alert_lat, (int, float)):
            bucket["first"].append(float(first_alert_lat))
        if isinstance(fp_lat, (int, float)):
            bucket["fp"].append(float(fp_lat))
    if not rows:
        return ""
    lines = ["| Scenario | System | First-alert latency (s) | Fingerprint latency (s) | Δ |",
             "|---|---|---|---|---:|"]
    for (scen, sys_), b in sorted(rows.items()):
        f = b["first"]; p = b["fp"]
        f_mean = mean(f) if f else float("nan")
        p_mean = mean(p) if p else float("nan")
        f_str = f"{f_mean:.1f} ± {stdev(f):.1f}" if len(f) >= 2 else (f"{f_mean:.1f}" if f else "—")
        p_str = f"{p_mean:.1f} ± {stdev(p):.1f}" if len(p) >= 2 else (f"{p_mean:.1f}" if p else "—")
        if f and p:
            d_str = f"{p_mean - f_mean:+.1f}"
        else:
            d_str = "—"
        lines.append(f"| {scen} | {sys_} | {f_str} | {p_str} | {d_str} |")
    return "\n".join(lines)


# ───── Task C: Cycle-by-cycle alert-count timeline ─────

def plot_alert_timeline(records: list[dict], out_path: Path) -> None:
    by_pair: dict[tuple[str, str], list[dict]] = {}
    for rec in records:
        by_pair.setdefault((rec.get("scenario", ""), rec.get("system", "")), []).append(rec)

    scenarios = sorted({s for s, _ in by_pair})
    systems = sorted({s for _, s in by_pair})
    if not scenarios or not systems:
        return

    fig, axes = plt.subplots(
        len(systems), len(scenarios),
        figsize=(4.0 * len(scenarios), 2.8 * len(systems)),
        sharey=True, sharex=True, squeeze=False,
    )
    for i, sys_ in enumerate(systems):
        for j, scen in enumerate(scenarios):
            ax = axes[i, j]
            recs = by_pair.get((scen, sys_), [])

            n_baseline_max = 0
            all_curves: list[list[int]] = []
            first_alert_marks: list[int] = []
            first_action_marks: list[int] = []
            recovered_marks: list[int] = []

            for rec in recs:
                bcyc = rec.get("baseline_cycles", []) or []
                rcyc = rec.get("recovery_cycles_detail", []) or []
                n_baseline_max = max(n_baseline_max, len(bcyc))
                cycles = bcyc + rcyc
                counts = [len(c.get("anomaly_alerts", []) or []) for c in cycles]
                if not counts:
                    continue
                ax.plot(range(len(counts)), counts, alpha=0.25, color="#1f77b4", linewidth=1)
                all_curves.append(counts)
                idx_obj = rec.get("indices") or {}
                if isinstance(idx_obj.get("first_alert"), int):
                    first_alert_marks.append(len(bcyc) + idx_obj["first_alert"])
                if isinstance(idx_obj.get("first_action"), int):
                    first_action_marks.append(len(bcyc) + idx_obj["first_action"])
                if isinstance(idx_obj.get("recovered"), int):
                    recovered_marks.append(len(bcyc) + idx_obj["recovered"])

            if all_curves:
                max_len = max(len(c) for c in all_curves)
                mean_curve = []
                for k in range(max_len):
                    vals = [c[k] for c in all_curves if k < len(c)]
                    mean_curve.append(mean(vals) if vals else 0.0)
                ax.plot(range(len(mean_curve)), mean_curve,
                        color="black", linewidth=2.0, label="mean")

            if n_baseline_max > 0:
                ax.axvline(x=n_baseline_max - 0.5, color="red",
                           linestyle="--", alpha=0.7, label="fault inject")
            if first_action_marks:
                ax.axvline(x=mean(first_action_marks), color="green",
                           linestyle=":", alpha=0.7, label="mean first action")
            if recovered_marks:
                ax.axvline(x=mean(recovered_marks), color="purple",
                           linestyle=":", alpha=0.7, label="mean recovered")

            ax.set_title(f"{scen} — {sys_}", fontsize=10)
            ax.grid(alpha=0.3)
            if i == len(systems) - 1:
                ax.set_xlabel("Cycle index")
            if j == 0:
                ax.set_ylabel("Alerts / cycle")

    handles, labels = axes[0, 0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="upper center", ncol=4, fontsize=9,
                   bbox_to_anchor=(0.5, 1.02))
    fig.suptitle("Cycle-by-cycle Alert Counts (one line per repetition; black = mean)",
                 y=1.06)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs-dir", default=str(RUNS_DIR))
    p.add_argument("--csv", default=str(SUMMARY_CSV))
    p.add_argument("--figures-dir", default=str(FIGURES_DIR))
    p.add_argument("--no-plots", action="store_true",
                   help="Only write the CSV; skip plotting (faster, no matplotlib).")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    runs_dir = Path(args.runs_dir)
    if not runs_dir.is_dir():
        logger.error("Runs directory not found: %s", runs_dir)
        return 2

    rows = collect(runs_dir)
    if not rows:
        logger.warning("No run JSON artifacts found in %s", runs_dir)
        return 0

    csv_path = Path(args.csv)
    write_csv(rows, csv_path)
    logger.info("Wrote %d rows to %s", len(rows), csv_path)

    print()
    print_summary(rows)
    print()

    if args.no_plots:
        return 0

    figs_dir = Path(args.figures_dir)
    figs_dir.mkdir(parents=True, exist_ok=True)
    plot_bar_metric(
        rows, "detection_latency_s", "Detection latency (s)",
        "Detection Latency by Scenario × System",
        figs_dir / "bar_detection_latency.png",
    )
    plot_bar_metric(
        rows, "recovery_time_s", "Recovery time (s)",
        "Time-to-Recovery by Scenario × System",
        figs_dir / "bar_recovery_time.png",
    )
    plot_success_heatmap(rows, figs_dir / "heatmap_success.png")
    plot_false_positives_box(rows, figs_dir / "box_false_positives.png")

    full_records = collect_full(runs_dir)
    plot_plan_source_histogram(full_records, figs_dir / "bar_plan_source.png")
    plot_bar_fingerprint_latency(full_records, figs_dir / "bar_detection_latency_fingerprint.png")
    plot_alert_timeline(full_records, figs_dir / "alert_timeline_faceted.png")

    tables_path = HERE / "results" / "tables.md"
    tables_path.parent.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    plan_tbl = plan_source_table(full_records)
    if plan_tbl:
        sections.append("## Plan source (LLM vs heuristic)\n\n" + plan_tbl)
    fp_tbl = fingerprint_latency_table(full_records)
    if fp_tbl:
        sections.append("## Detection latency: first-alert vs trigger-fingerprint\n\n" + fp_tbl)
    if sections:
        tables_path.write_text("\n\n".join(sections) + "\n")
        logger.info("Wrote analysis tables to %s", tables_path)

    logger.info("Wrote plots to %s", figs_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
