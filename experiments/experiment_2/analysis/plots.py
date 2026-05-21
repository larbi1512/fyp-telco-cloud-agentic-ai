#!/usr/bin/env python3
"""
Plot generation for Experiment 2 — per-metric bars with 95% CI error bars,
per-metric box plots across systems, and per-(metric × scenario) heatmap.

Inputs:
  - experiments/experiment_2/results/main/runs_merged.csv
  - experiments/experiment_2/analysis/main/summary_table.csv  (for CIs)

Outputs (under experiments/experiment_2/analysis/main/plots/):
  - bars_<metric>.png        (one per metric)
  - box_<metric>.png         (one per metric)
  - heatmap_scenario.png     (single image, all 6 metrics × 4 scenarios × 5 systems)
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


METRICS: list[tuple[str, str, str]] = [
    ("deployment_time_s",        "Deployment time (s)",         "lower"),
    ("config_error_rate",        "Config error rate",           "lower"),
    ("resource_accuracy",        "Resource accuracy",           "higher"),
    ("interventions",            "Human interventions",         "lower"),
    ("intent_to_deploy_accuracy","Intent-to-deploy accuracy",   "higher"),
    ("policy_violation_rate",    "Policy violation rate",       "lower"),
]

SYSTEMS = ["mas", "b1", "b2", "b3", "b4"]
SYSTEM_LABELS = {
    "mas": "MAS\n(LangGraph)",
    "b1": "B1\nManual",
    "b2": "B2\nOSM stub",
    "b3": "B3\nStatic+HPA",
    "b4": "B4\nSingle-LLM",
}
SYSTEM_COLORS = {
    "mas": "#2c3e9f",
    "b1": "#444",
    "b2": "#6b0f1a",
    "b3": "#1e6e3e",
    "b4": "#9b4f12",
}


def load_runs(path: Path) -> list[dict[str, Any]]:
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            for k, _, _ in METRICS:
                v = row.get(k, "")
                row[k] = float(v) if v not in ("", None) else math.nan
            rows.append(row)
    return rows


def load_summary(path: Path) -> dict[tuple[str, str], dict[str, float]]:
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out[(row["system"], row["metric"])] = {
                "mean": float(row["mean"]),
                "ci_lo": float(row["ci_lo"]),
                "ci_hi": float(row["ci_hi"]),
                "n": int(row["n"]),
            }
    return out


# Systems that do NOT actually deploy in the experiment harness.
# For deploy-gated metrics (intent_to_deploy_accuracy, deployment_time_s),
# these bars should be hatched and asterisked to make the structural caveat
# explicit in the figure rather than only in the report's caveats paragraph.
NON_DEPLOYING_SYSTEMS = {"b1", "b2", "b4"}
DEPLOY_GATED_METRICS = {"intent_to_deploy_accuracy", "deployment_time_s"}


def bar_plot(
    summary: dict[tuple[str, str], dict[str, float]],
    metric_key: str, metric_label: str, direction: str,
    out_path: Path,
) -> None:
    means, lo, hi = [], [], []
    for s in SYSTEMS:
        v = summary.get((s, metric_key))
        if v is None:
            means.append(0.0); lo.append(0.0); hi.append(0.0)
            continue
        means.append(v["mean"])
        lo.append(v["mean"] - v["ci_lo"])
        hi.append(v["ci_hi"] - v["mean"])

    deploy_gated = metric_key in DEPLOY_GATED_METRICS

    fig, ax = plt.subplots(figsize=(7, 4))
    xs = np.arange(len(SYSTEMS))
    bars = ax.bar(
        xs, means, yerr=[lo, hi], capsize=4,
        color=[SYSTEM_COLORS[s] for s in SYSTEMS],
        edgecolor="black", linewidth=0.5,
    )
    # Mark non-deploying baselines with hatching on deploy-gated metrics.
    if deploy_gated:
        for bar, s in zip(bars, SYSTEMS):
            if s in NON_DEPLOYING_SYSTEMS:
                bar.set_hatch("//")
                bar.set_alpha(0.55)
    ax.set_xticks(xs)
    # Asterisk non-deploying systems on deploy-gated plots
    labels = []
    for s in SYSTEMS:
        lbl = SYSTEM_LABELS[s]
        if deploy_gated and s in NON_DEPLOYING_SYSTEMS:
            lbl = lbl + "*"
        labels.append(lbl)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel(metric_label)
    ax.set_title(metric_label, fontsize=11)
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    # Headroom so value labels (placed above the upper CI whisker) don't clip.
    top = max((m + h) for m, h in zip(means, hi)) if means else 1.0
    ax.set_ylim(top=top * 1.15 if top > 0 else 1.0)
    for bar, m, h in zip(bars, means, hi):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + h,
            f"{m:.3f}", ha="center", va="bottom", fontsize=9,
        )
    # Footnote for deploy-gated metrics
    if deploy_gated:
        fig.text(
            0.5, 0.005,
            "* B1/B2/B4 emit artifacts only; they do not run `helm install`, "
            "so deployment_success = 0 by design.",
            ha="center", va="bottom", fontsize=7.5, style="italic", color="#444",
        )
        fig.tight_layout(rect=(0, 0.04, 1, 1))
    else:
        fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def box_plot(
    rows: list[dict[str, Any]],
    metric_key: str, metric_label: str, direction: str,
    out_path: Path,
) -> None:
    data = []
    for s in SYSTEMS:
        vals = [r[metric_key] for r in rows
                if r["system"] == s and not math.isnan(r[metric_key])]
        data.append(vals)

    fig, ax = plt.subplots(figsize=(7, 4))
    bp = ax.boxplot(
        data,
        tick_labels=[SYSTEM_LABELS[s] for s in SYSTEMS],
        showmeans=True, meanline=True, widths=0.6, patch_artist=True,
    )
    for patch, s in zip(bp["boxes"], SYSTEMS):
        patch.set_facecolor(SYSTEM_COLORS[s])
        patch.set_alpha(0.5)
    ax.set_ylabel(metric_label)
    ax.set_title(f"{metric_label} — distribution across 180 runs/system",
                 fontsize=11)
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def scenario_heatmap(rows: list[dict[str, Any]], out_path: Path) -> None:
    scenarios = sorted({r["scenario"] for r in rows if r.get("scenario")})
    fig, axes = plt.subplots(2, 3, figsize=(14, 8))
    axes = axes.ravel()

    for idx, (key, label, direction) in enumerate(METRICS):
        ax = axes[idx]
        # Build a 5×N matrix (systems × scenarios) of per-(system, scenario) means
        mat = np.zeros((len(SYSTEMS), len(scenarios)))
        for si, s in enumerate(SYSTEMS):
            for sci, sc in enumerate(scenarios):
                vals = [r[key] for r in rows
                        if r["system"] == s and r["scenario"] == sc
                        and not math.isnan(r[key])]
                mat[si, sci] = float(np.mean(vals)) if vals else math.nan

        # Normalise per-metric for colour scaling — min-max across the matrix
        flat = mat[~np.isnan(mat)]
        vmin, vmax = (float(flat.min()), float(flat.max())) if flat.size else (0.0, 1.0)
        if vmin == vmax:
            vmax = vmin + 1.0
        cmap = "RdYlGn" if direction == "higher" else "RdYlGn_r"
        im = ax.imshow(mat, aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)

        for si in range(len(SYSTEMS)):
            for sci in range(len(scenarios)):
                v = mat[si, sci]
                if math.isnan(v):
                    txt = "—"
                elif key == "deployment_time_s":
                    txt = f"{v:.0f}"
                elif key == "interventions":
                    txt = f"{v:.1f}"
                else:
                    txt = f"{v:.2f}"
                ax.text(sci, si, txt, ha="center", va="center", fontsize=8,
                        color="black")
        ax.set_xticks(range(len(scenarios)))
        ax.set_xticklabels(scenarios, fontsize=8, rotation=20, ha="right")
        ax.set_yticks(range(len(SYSTEMS)))
        ax.set_yticklabels([s.upper() for s in SYSTEMS], fontsize=9)
        arrow = " ↓" if direction == "lower" else " ↑"
        ax.set_title(f"{label}{arrow}", fontsize=10)
        plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)

    fig.suptitle("Per-scenario × per-system mean across the 6 metrics",
                 fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_per_vnf_resource(csv_path: Path, out_path: Path) -> None:
    """Horizontal grouped bars: per-VNF CPU and memory relative error
    with bootstrap-95%-CI whiskers. Sorted by total error (best on top)."""
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return

    rows.sort(key=lambda r: float(r["cpu_rel_err_mean"]) + float(r["mem_rel_err_mean"]))
    vnfs = [r["vnf"].upper() for r in rows]
    cpu_mean = [float(r["cpu_rel_err_mean"]) for r in rows]
    cpu_lo = [float(r["cpu_rel_err_ci_lo"]) for r in rows]
    cpu_hi = [float(r["cpu_rel_err_ci_hi"]) for r in rows]
    mem_mean = [float(r["mem_rel_err_mean"]) for r in rows]
    mem_lo = [float(r["mem_rel_err_ci_lo"]) for r in rows]
    mem_hi = [float(r["mem_rel_err_ci_hi"]) for r in rows]

    cpu_err = [[m - lo for m, lo in zip(cpu_mean, cpu_lo)],
               [hi - m for m, hi in zip(cpu_mean, cpu_hi)]]
    mem_err = [[m - lo for m, lo in zip(mem_mean, mem_lo)],
               [hi - m for m, hi in zip(mem_mean, mem_hi)]]

    y = np.arange(len(vnfs))
    h = 0.4

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh(y - h / 2, cpu_mean, h, xerr=cpu_err, capsize=3,
            label="CPU rel. error", color="#4C78A8")
    ax.barh(y + h / 2, mem_mean, h, xerr=mem_err, capsize=3,
            label="Memory rel. error", color="#F58518")

    ax.set_yticks(y)
    ax.set_yticklabels(vnfs)
    ax.invert_yaxis()
    ax.set_xlabel("Relative error vs resource oracle (mean ± 95% bootstrap CI)")
    n_vals = [int(r["n_measurements"]) for r in rows]
    if min(n_vals) == max(n_vals):
        n_label = f"n={n_vals[0]}"
    else:
        n_label = f"n={min(n_vals)}–{max(n_vals)}"
    ax.set_title(f"Per-VNF resource accuracy (MAS, {n_label} measurements per VNF)")
    ax.axvline(0, color="black", linewidth=0.5)
    ax.legend(loc="lower right")
    ax.grid(axis="x", linestyle="--", alpha=0.4)

    for i, r in enumerate(rows):
        acc = float(r["vnf_accuracy"])
        ax.text(max(cpu_hi[i], mem_hi[i]) + 0.01, i,
                f"acc={acc:.3f}", va="center", fontsize=8, color="#444")

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path,
                        default=Path("experiments/experiment_2/results/main/runs_merged.csv"))
    parser.add_argument("--summary", type=Path,
                        default=Path("experiments/experiment_2/analysis/summary_table.csv"))
    parser.add_argument("--out", type=Path,
                        default=Path("experiments/experiment_2/analysis/plots"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows = load_runs(args.runs)
    summary = load_summary(args.summary)

    for key, label, direction in METRICS:
        bar_plot(summary, key, label, direction,
                 args.out / f"bars_{key}.png")
        box_plot(rows, key, label, direction,
                 args.out / f"box_{key}.png")
    scenario_heatmap(rows, args.out / "heatmap_scenario.png")

    # Look for per_vnf_resource.csv alongside the summary table (same analysis
    # output dir) first, then fall back to the legacy default location.
    for candidate in (args.summary.parent / "per_vnf_resource.csv",
                      Path("experiments/experiment_2/analysis/per_vnf_resource.csv")):
        if candidate.exists():
            plot_per_vnf_resource(candidate, args.out / "per_vnf_resource.png")
            break

    print(f"plots written to {args.out}")
    for p in sorted(args.out.glob("*.png")):
        print(f"  {p.name}")


if __name__ == "__main__":
    main()
