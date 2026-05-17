#!/usr/bin/env python3
"""
Config-Quality-under-Ambiguity analysis for Experiment 2 external-validity dataset.

Run with --no-deploy, so intent_to_deploy_accuracy is excluded (always 0).
The five remaining metrics capture config quality, resource sizing accuracy,
and policy compliance under four categories of prompt difficulty.

Reads results from a run against intents_v3_ambiguity.json (300 intents,
categories: perfect / ambiguous / incomplete / contradictory) and produces:

  ambiguity_bar.png     — mean config_error_rate per (system × category)
  ambiguity_heatmap.png — mean of 5 metrics per (system × category)
  ambiguity_table.csv   — aggregated numbers for the thesis table

Usage:
  python experiments/experiment_2/analysis/ambiguity.py \\
      --runs experiments/experiment_2/results/ambiguity/runs_merged.csv \\
      --out  experiments/experiment_2/analysis/ambiguity/
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

CATEGORIES = ["perfect", "ambiguous", "incomplete", "contradictory"]
CAT_LABELS = {
    "perfect":       "Perfect\n(unambiguous)",
    "ambiguous":     "Ambiguous\n(synonym vocab)",
    "incomplete":    "Incomplete\n(UE count missing)",
    "contradictory": "Contradictory\n(HA + 1 replica)",
}
CAT_COLORS = {
    "perfect":       "#2ca02c",
    "ambiguous":     "#ff7f0e",
    "incomplete":    "#1f77b4",
    "contradictory": "#d62728",
}

SYSTEMS = ["mas", "b4r"]
SYSTEM_LABELS = {
    "mas": "MAS (LangGraph)",
    "b4r": "B4r (Single-LLM + repair)",
}
SYSTEM_COLORS = {
    "mas": "#2c3e9f",
    "b4r": "#c97a2d",
}

# intent_to_deploy_accuracy excluded: always 0 with --no-deploy (requires
# deployment_success which is only set during a real helm install).
METRICS: list[tuple[str, str, str]] = [
    ("deployment_time_s",  "Deployment time (s)",  "lower"),
    ("config_error_rate",  "Config error rate",    "lower"),
    ("resource_accuracy",  "Resource accuracy",    "higher"),
    ("interventions",      "Human interventions",  "lower"),
    ("policy_violation_rate", "Policy violation rate", "lower"),
]

# The primary headline metric for the ambiguity bar chart.
HEADLINE_METRIC = "config_error_rate"
HEADLINE_LABEL  = "Config error rate ↓ lower is better"


def load_runs(path: Path) -> list[dict[str, Any]]:
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            for key, _, _ in METRICS:
                v = row.get(key, "")
                row[key] = float(v) if v not in ("", None, "None") else math.nan
            rows.append(row)
    return rows


def _mean_ci(vals: list[float]) -> tuple[float, float, float]:
    """Return (mean, ci_lo, ci_hi) using 95% normal approximation."""
    n = len(vals)
    if n == 0:
        return math.nan, math.nan, math.nan
    mu = float(np.mean(vals))
    if n < 2:
        return mu, mu, mu
    se = float(np.std(vals, ddof=1)) / math.sqrt(n)
    z = 1.96
    return mu, mu - z * se, mu + z * se


def quality_bar_plot(rows: list[dict[str, Any]], out_path: Path) -> None:
    """Grouped bar: config_error_rate by system × category (headline metric)."""
    fig, ax = plt.subplots(figsize=(10, 5))
    n_sys = len(SYSTEMS)
    width = 0.35
    offsets = np.linspace(-(n_sys - 1) * width / 2, (n_sys - 1) * width / 2, n_sys)
    xs = np.arange(len(CATEGORIES))

    for si, sys in enumerate(SYSTEMS):
        means, lo_errs, hi_errs = [], [], []
        for cat in CATEGORIES:
            vals = [r[HEADLINE_METRIC] for r in rows
                    if r.get("system") == sys
                    and r.get("category") == cat
                    and not math.isnan(r[HEADLINE_METRIC])]
            mu, ci_lo, ci_hi = _mean_ci(vals)
            means.append(mu if not math.isnan(mu) else 0.0)
            lo_errs.append(max(0.0, mu - ci_lo) if not math.isnan(mu) else 0.0)
            hi_errs.append(max(0.0, ci_hi - mu) if not math.isnan(mu) else 0.0)
        bars = ax.bar(
            xs + offsets[si], means, width,
            yerr=[lo_errs, hi_errs], capsize=4,
            label=SYSTEM_LABELS[sys],
            color=SYSTEM_COLORS[sys],
            edgecolor="black", linewidth=0.5, alpha=0.85,
        )
        for bar, m in zip(bars, means):
            if not math.isnan(m):
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.01,
                        f"{m:.3f}", ha="center", va="bottom", fontsize=7.5)

    ax.set_xticks(xs)
    ax.set_xticklabels([CAT_LABELS[c] for c in CATEGORIES], fontsize=9)
    ax.set_ylabel("Config error rate (mean ± 95% CI)")
    ax.set_title("Config Quality under Ambiguity — MAS vs B4r across intent categories",
                 fontsize=11)
    ax.set_ylim(0, max(0.1, ax.get_ylim()[1]) * 1.2)
    ax.legend(fontsize=9)
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.text(0.5, 0.01,
             "intent_to_deploy_accuracy excluded (--no-deploy run; deploy metrics require helm install).",
             ha="center", fontsize=7.5, style="italic", color="#666")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def resource_accuracy_bar_plot(rows: list[dict[str, Any]], out_path: Path) -> None:
    """Grouped bar: resource_accuracy by system × category."""
    fig, ax = plt.subplots(figsize=(10, 5))
    n_sys = len(SYSTEMS)
    width = 0.35
    offsets = np.linspace(-(n_sys - 1) * width / 2, (n_sys - 1) * width / 2, n_sys)
    xs = np.arange(len(CATEGORIES))

    for si, sys in enumerate(SYSTEMS):
        means, lo_errs, hi_errs = [], [], []
        for cat in CATEGORIES:
            vals = [r["resource_accuracy"] for r in rows
                    if r.get("system") == sys
                    and r.get("category") == cat
                    and not math.isnan(r["resource_accuracy"])]
            mu, ci_lo, ci_hi = _mean_ci(vals)
            means.append(mu if not math.isnan(mu) else 0.0)
            lo_errs.append(max(0.0, mu - ci_lo) if not math.isnan(mu) else 0.0)
            hi_errs.append(max(0.0, ci_hi - mu) if not math.isnan(mu) else 0.0)
        bars = ax.bar(
            xs + offsets[si], means, width,
            yerr=[lo_errs, hi_errs], capsize=4,
            label=SYSTEM_LABELS[sys],
            color=SYSTEM_COLORS[sys],
            edgecolor="black", linewidth=0.5, alpha=0.85,
        )
        for bar, m in zip(bars, means):
            if not math.isnan(m):
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.01,
                        f"{m:.3f}", ha="center", va="bottom", fontsize=7.5)

    ax.set_xticks(xs)
    ax.set_xticklabels([CAT_LABELS[c] for c in CATEGORIES], fontsize=9)
    ax.set_ylabel("Resource accuracy (mean ± 95% CI)  ↑ higher is better")
    ax.set_title("Resource Sizing Accuracy under Ambiguity — MAS vs B4r",
                 fontsize=11)
    ax.set_ylim(0, 1.15)
    ax.legend(fontsize=9)
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def metric_heatmap(rows: list[dict[str, Any]], out_path: Path) -> None:
    """Per-metric heatmap: systems (rows) × categories (cols), one panel per metric."""
    fig, axes = plt.subplots(1, len(METRICS), figsize=(18, 4))
    if len(METRICS) == 1:
        axes = [axes]

    for idx, (key, label, direction) in enumerate(METRICS):
        ax = axes[idx]
        mat = np.zeros((len(SYSTEMS), len(CATEGORIES)))
        for si, sys in enumerate(SYSTEMS):
            for ci, cat in enumerate(CATEGORIES):
                vals = [r[key] for r in rows
                        if r.get("system") == sys
                        and r.get("category") == cat
                        and not math.isnan(r[key])]
                mat[si, ci] = float(np.mean(vals)) if vals else math.nan

        flat = mat[~np.isnan(mat)]
        vmin = float(flat.min()) if flat.size else 0.0
        vmax = float(flat.max()) if flat.size else 1.0
        if vmin == vmax:
            vmax = vmin + 1.0
        cmap = "RdYlGn" if direction == "higher" else "RdYlGn_r"
        im = ax.imshow(mat, aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)

        for si in range(len(SYSTEMS)):
            for ci in range(len(CATEGORIES)):
                v = mat[si, ci]
                txt = "—" if math.isnan(v) else (
                    f"{v:.0f}" if key == "deployment_time_s" else
                    f"{v:.1f}" if key == "interventions" else
                    f"{v:.2f}"
                )
                ax.text(ci, si, txt, ha="center", va="center", fontsize=8)

        ax.set_xticks(range(len(CATEGORIES)))
        ax.set_xticklabels([c.capitalize() for c in CATEGORIES], fontsize=8, rotation=15)
        ax.set_yticks(range(len(SYSTEMS)))
        ax.set_yticklabels([SYSTEM_LABELS[s] for s in SYSTEMS], fontsize=8)
        arrow = " ↓" if direction == "lower" else " ↑"
        ax.set_title(f"{label}{arrow}", fontsize=9)
        plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)

    fig.suptitle("5 config-quality metrics × ambiguity category (mean, --no-deploy run)", fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def write_table(rows: list[dict[str, Any]], out_path: Path) -> None:
    """CSV table: system × category × metric means."""
    fieldnames = ["system", "category", "n"] + [m[0] for m in METRICS]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for sys in SYSTEMS:
            for cat in CATEGORIES:
                subset = [r for r in rows
                          if r.get("system") == sys and r.get("category") == cat]
                row = {"system": sys, "category": cat, "n": len(subset)}
                for key, _, _ in METRICS:
                    vals = [r[key] for r in subset if not math.isnan(r[key])]
                    row[key] = round(float(np.mean(vals)), 4) if vals else ""
                w.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path,
                        default=Path("experiments/experiment_2/results/ambiguity/runs_merged.csv"))
    parser.add_argument("--out", type=Path,
                        default=Path("experiments/experiment_2/analysis/ambiguity"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows = load_runs(args.runs)
    if not rows:
        print("No rows found — run the experiment first.")
        return

    present_cats = {r.get("category") for r in rows}
    print(f"Loaded {len(rows)} rows. Categories present: {sorted(present_cats)}")

    quality_bar_plot(rows, args.out / "ambiguity_bar.png")
    resource_accuracy_bar_plot(rows, args.out / "ambiguity_resource_accuracy.png")
    metric_heatmap(rows, args.out / "ambiguity_heatmap.png")
    write_table(rows, args.out / "ambiguity_table.csv")

    print(f"Outputs written to {args.out}/")
    for p in sorted(args.out.glob("*")):
        print(f"  {p.name}")


if __name__ == "__main__":
    main()
