#!/usr/bin/env python3
"""
Generate Experiment-2 figures for the CNSM paper from the two sweep stores:
  - sweep_gptoss_nd   (gpt-oss:120b, frontier, no-deploy)
  - main_v2_qwen14b   (qwen2.5:14b, production, deploy)

Outputs PDF + PNG (300 dpi) into experiments/experiment_2/figures/.
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
FIGDIR = ROOT / "figures"
FIGDIR.mkdir(exist_ok=True)

SYSTEMS = ["mas", "confucius", "ossgpt", "lin"]
LABELS = {"mas": "MAS (ours)", "confucius": "Confucius", "ossgpt": "OSS-GPT", "lin": "Lin et al."}
STORES = {
    "gpt-oss:120b": "sweep_gptoss_nd",
    "qwen2.5:14b": "main_v2_qwen14b",
}
# MAS highlighted; baselines muted.
COLORS = {"mas": "#0B5394", "confucius": "#E69F00", "ossgpt": "#009E73", "lin": "#CC79A7"}

plt.rcParams.update({
    "font.size": 11, "axes.titlesize": 12, "axes.labelsize": 11,
    "legend.fontsize": 9.5, "figure.dpi": 120, "savefig.bbox": "tight",
})


def load(store: str) -> list[dict]:
    return [json.loads(l) for l in open(RESULTS / store / "raw.jsonl")]


def agg(store: str, system: str, key: str):
    rows = [r[key] for r in load(store)
            if r["system"] == system and isinstance(r.get(key), (int, float))]
    if not rows:
        return None, None, 0
    m = sum(rows) / len(rows)
    sd = st.pstdev(rows) if len(rows) > 1 else 0.0
    return m, sd, len(rows)


def save(fig, name):
    for ext in ("pdf", "png"):
        fig.savefig(FIGDIR / f"{name}.{ext}", dpi=300)
    plt.close(fig)
    print("wrote", FIGDIR / f"{name}.[pdf|png]")


# ── Figure 1: resource-accuracy robustness (grouped bars, frontier vs production)
def fig_resacc_robustness():
    models = list(STORES)
    x = np.arange(len(SYSTEMS))
    w = 0.38
    fig, ax = plt.subplots(figsize=(7, 4))
    for i, model in enumerate(models):
        stats = [agg(STORES[model], s, "resource_accuracy") for s in SYSTEMS]
        means = [(m or 0) for m, sd, n in stats]
        # 95% CI of the mean = 1.96 * SEM
        errs = [(1.96 * (sd or 0) / (n ** 0.5)) if n else 0 for m, sd, n in stats]
        bars = ax.bar(x + (i - 0.5) * w, means, w, yerr=errs, capsize=3,
                      label=model, edgecolor="black", linewidth=0.6,
                      color=("#4C4C4C" if i == 0 else "#BBBBBB"))
        for b, m in zip(bars, means):
            ax.text(b.get_x() + b.get_width() / 2, m + 0.02, f"{m:.2f}",
                    ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels([LABELS[s] for s in SYSTEMS])
    ax.set_ylabel("Resource accuracy")
    ax.set_ylim(0, 1.08)
    ax.set_title("Resource accuracy: frontier vs. deployable model")
    ax.legend(title="LLM backend", loc="upper left", bbox_to_anchor=(1.01, 1.0))
    ax.grid(axis="y", alpha=0.3)
    save(fig, "fig1_resacc_robustness")


# ── Figure 2: collapse slope chart (the dramatic one)
def fig_collapse_slope():
    fig, ax = plt.subplots(figsize=(6, 4.2))
    xs = [0, 1]
    for s in SYSTEMS:
        y0 = agg(STORES["gpt-oss:120b"], s, "resource_accuracy")[0] or 0
        y1 = agg(STORES["qwen2.5:14b"], s, "resource_accuracy")[0] or 0
        lw = 3.0 if s == "mas" else 1.8
        ms = 9 if s == "mas" else 7
        ax.plot(xs, [y0, y1], marker="o", color=COLORS[s], linewidth=lw,
                markersize=ms, label=LABELS[s],
                zorder=3 if s == "mas" else 2)
        ax.text(1.02, y1, f"{y1:.2f}", color=COLORS[s], va="center", fontsize=9)
        ax.text(-0.02, y0, f"{y0:.2f}", color=COLORS[s], va="center", ha="right", fontsize=9)
    ax.set_xticks(xs)
    ax.set_xticklabels(["gpt-oss:120b\n(frontier)", "qwen2.5:14b\n(deployable)"])
    ax.set_xlim(-0.35, 1.35)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Resource accuracy")
    ax.set_title("Baselines collapse on the deployable model; MAS holds")
    ax.legend(loc="center left", bbox_to_anchor=(0.32, 0.55))
    ax.grid(axis="y", alpha=0.3)
    save(fig, "fig2_collapse_slope")


# ── Figure 3: production model (qwen14b) multi-metric comparison
def fig_production_metrics():
    store = STORES["qwen2.5:14b"]
    keys = [("resource_accuracy", "Resource\naccuracy"),
            ("intent_to_deploy_accuracy", "Intent-to-deploy\naccuracy")]
    x = np.arange(len(SYSTEMS)); w = 0.38
    fig, ax = plt.subplots(figsize=(7, 4))
    for i, (k, lab) in enumerate(keys):
        means = [agg(store, s, k)[0] or 0 for s in SYSTEMS]
        bars = ax.bar(x + (i - 0.5) * w, means, w, label=lab,
                      edgecolor="black", linewidth=0.6,
                      color=("#0B5394" if i == 0 else "#9FC5E8"))
        for b, m in zip(bars, means):
            ax.text(b.get_x() + b.get_width() / 2, m + 0.02, f"{m:.2f}",
                    ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x); ax.set_xticklabels([LABELS[s] for s in SYSTEMS])
    ax.set_ylabel("Score"); ax.set_ylim(0, 1.08)
    ax.set_title("Production model (qwen2.5:14b): MAS leads on every axis")
    ax.legend(loc="upper right")
    ax.grid(axis="y", alpha=0.3)
    save(fig, "fig3_production_metrics")


def agg_by_complexity(store: str, system: str, key: str, comp: str):
    rows = [r[key] for r in load(store)
            if r["system"] == system and r.get("complexity") == comp
            and isinstance(r.get(key), (int, float))]
    return (sum(rows) / len(rows)) if rows else np.nan


# ── Figure 4: resource accuracy by intent complexity (grouped bars, panel per model)
def fig_resacc_by_complexity():
    comps = ["simple", "medium", "complex"]
    x = np.arange(len(comps))
    w = 0.2
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), sharey=True)
    for ax, (model, store) in zip(axes, STORES.items()):
        for j, s in enumerate(SYSTEMS):
            ys = [agg_by_complexity(store, s, "resource_accuracy", c) for c in comps]
            bars = ax.bar(x + (j - 1.5) * w, ys, w, label=LABELS[s],
                          color=COLORS[s], edgecolor="black", linewidth=0.5)
            for b, m in zip(bars, ys):
                if not np.isnan(m):
                    ax.text(b.get_x() + b.get_width() / 2, m + 0.015, f"{m:.2f}",
                            ha="center", va="bottom", fontsize=6.5)
        ax.set_title(model)
        ax.set_ylim(0, 1.12)
        ax.set_xticks(x)
        ax.set_xticklabels(comps)
        ax.set_xlabel("Intent complexity")
        ax.grid(axis="y", alpha=0.3)
    axes[0].set_ylabel("Resource accuracy")
    axes[0].legend(loc="upper right", fontsize=8)
    fig.suptitle("Resource accuracy by intent complexity", y=1.0)
    save(fig, "fig4_resacc_by_complexity")


if __name__ == "__main__":
    fig_resacc_robustness()
    fig_collapse_slope()
    fig_production_metrics()
    fig_resacc_by_complexity()
    print("done ->", FIGDIR)
