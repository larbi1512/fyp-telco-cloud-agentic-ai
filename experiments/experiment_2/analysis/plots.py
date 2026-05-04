#!/usr/bin/env python3
"""
Plot generation for Experiment 2 — per-metric bars with 95% CI error bars,
per-metric box plots across systems, and per-(metric × scenario) heatmap.

Inputs:
  - experiments/experiment_2/results/full/runs.csv
  - experiments/experiment_2/analysis/summary_table.csv  (for CIs)

Outputs (under experiments/experiment_2/analysis/plots/):
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

    fig, ax = plt.subplots(figsize=(7, 4))
    xs = np.arange(len(SYSTEMS))
    bars = ax.bar(
        xs, means, yerr=[lo, hi], capsize=4,
        color=[SYSTEM_COLORS[s] for s in SYSTEMS],
        edgecolor="black", linewidth=0.5,
    )
    ax.set_xticks(xs)
    ax.set_xticklabels([SYSTEM_LABELS[s] for s in SYSTEMS], fontsize=9)
    ax.set_ylabel(metric_label)
    arrow = " ↓ lower is better" if direction == "lower" else " ↑ higher is better"
    ax.set_title(f"{metric_label}{arrow}", fontsize=11)
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    for bar, m in zip(bars, means):
        ax.text(
            bar.get_x() + bar.get_width() / 2, bar.get_height(),
            f"{m:.3f}", ha="center", va="bottom", fontsize=8,
        )
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
    arrow = " ↓ lower is better" if direction == "lower" else " ↑ higher is better"
    ax.set_title(f"{metric_label} — distribution across 180 runs/system{arrow}",
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path,
                        default=Path("experiments/experiment_2/results/full/runs.csv"))
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
    print(f"plots written to {args.out}")
    for p in sorted(args.out.glob("*.png")):
        print(f"  {p.name}")


if __name__ == "__main__":
    main()
