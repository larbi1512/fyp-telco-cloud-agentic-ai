#!/usr/bin/env python3
"""
Experiment 3 — Report generator.

Reads runs.csv produced by run_llm_comparison.py and generates:
  - Summary table (mean ± std per LLM × metric)
  - 5 plots saved as PNG under --out/figures/
  - results/report.md embedding all tables + figure references

Usage:
  python experiments/experiment_3/report.py \\
      --results experiments/experiment_3/results/runs.csv \\
      --out     experiments/experiment_3/results/figures
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns
from rich.console import Console
from rich.table import Table

from experiments.experiment_3.configs import LLM_CONFIGS, LLM_ORDER

console = Console()

# ── Metric metadata ─────────────────────────────────────────────────────── #

METRICS = {
    "config_intent_accuracy": {
        "label": "Config Intent Acc",
        "higher_is_better": True,
        "ylim": (0, 1.05),
        "fmt": ".2f",
    },
    "resource_accuracy": {
        "label": "Resource Accuracy",
        "higher_is_better": True,
        "ylim": (0, 1.05),
        "fmt": ".2f",
    },
    "config_error_rate": {
        "label": "Config Error Rate",
        "higher_is_better": False,
        "ylim": (0, None),
        "fmt": ".3f",
    },
    "policy_violation_rate": {
        "label": "Policy Violation Rate",
        "higher_is_better": False,
        "ylim": (0, None),
        "fmt": ".3f",
    },
    "interventions": {
        "label": "Interventions",
        "higher_is_better": False,
        "ylim": (0, None),
        "fmt": ".2f",
    },
    "deployment_time_s": {
        "label": "Plan Time (s)",
        "higher_is_better": False,
        "ylim": (0, None),
        "fmt": ".1f",
    },
}

PALETTE = sns.color_palette("tab10", n_colors=10)


_SHORT_LABELS = {
    "llama32_3b":    "Llama\n3.2 3B",
    "gemma3_4b":     "Gemma\n3 4B",
    "deepseek_r1_7b":"DeepSeek\nR1 7B",
    "qwen25_7b":     "Qwen2.5\n7B",
    "qwen25_14b":    "Qwen2.5\n14B",
    "mistral22b":    "Mistral\n22B",
    "qwen_coder30b": "Qwen3\n30B",
    "gpt120b":       "GPT-OSS\n120B",
}


def _llm_label(llm_id: str) -> str:
    return _SHORT_LABELS.get(llm_id, llm_id)


def _ordered_ids(df: pd.DataFrame) -> list[str]:
    present = set(df["llm_id"].unique()) if "llm_id" in df.columns else set(df["system"].unique())
    return [lid for lid in LLM_ORDER if lid in present]


# ── Data loading ─────────────────────────────────────────────────────────── #

def _load_config_intent_accuracy(jsonl_path: Path) -> pd.DataFrame:
    """
    Read raw.jsonl and compute config_intent_accuracy for each (llm_id, intent_id, rep).

    config_intent_accuracy = coverage_ok AND policy_go AND critic_approved
    (deployment_success excluded — this is a config-only experiment).
    """
    import json
    rows = []
    if not jsonl_path.exists():
        return pd.DataFrame(columns=["llm_id", "intent_id", "rep", "config_intent_accuracy"])
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            comps = (r.get("components") or {}).get("intent_accuracy_components") or {}
            cia = 1 if (comps.get("coverage_ok") and comps.get("policy_go") and comps.get("critic_approved")) else 0
            rows.append({
                "llm_id": r.get("llm_id") or r.get("system"),
                "intent_id": r.get("intent_id"),
                "rep": r.get("rep"),
                "config_intent_accuracy": cia,
            })
    return pd.DataFrame(rows)


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    # normalise: use llm_id if present, else fall back to system
    if "llm_id" not in df.columns:
        df["llm_id"] = df["system"]

    # Compute config_intent_accuracy from raw.jsonl (works for config-only mode;
    # intent_to_deploy_accuracy always=0 here because no deployment happens)
    jsonl_path = path.parent / "raw.jsonl"
    cia_df = _load_config_intent_accuracy(jsonl_path)
    if not cia_df.empty:
        merge_keys = [c for c in ("llm_id", "intent_id", "rep") if c in df.columns and c in cia_df.columns]
        df = df.merge(cia_df, on=merge_keys, how="left")

    # drop rows with all-null metrics (catastrophic failures)
    metric_cols = list(METRICS.keys())
    available = [c for c in metric_cols if c in df.columns]
    df = df.dropna(subset=available, how="all")
    return df


# ── Plot 1: Grouped bar chart (2×3 grid) ─────────────────────────────────── #

def plot_grouped_bars(df: pd.DataFrame, out_dir: Path) -> Path:
    llm_ids = _ordered_ids(df)
    labels = [_llm_label(lid) for lid in llm_ids]
    x = np.arange(len(llm_ids))
    colors = PALETTE[:len(llm_ids)]

    fig, axes = plt.subplots(2, 3, figsize=(20, 10))
    axes = axes.flatten()

    for idx, (metric, meta) in enumerate(METRICS.items()):
        ax = axes[idx]
        if metric not in df.columns:
            ax.set_visible(False)
            continue

        means, stds = [], []
        for lid in llm_ids:
            vals = df[df["llm_id"] == lid][metric].dropna()
            means.append(vals.mean() if len(vals) else 0.0)
            stds.append(vals.std() if len(vals) > 1 else 0.0)

        bars = ax.bar(x, means, yerr=stds, capsize=3,
                      color=colors, alpha=0.85, width=0.55,
                      error_kw={"elinewidth": 1.2, "alpha": 0.7})
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9, ha="center", linespacing=1.3)
        ax.set_title(meta["label"], fontsize=12, fontweight="bold", pad=8)
        ax.set_ylim(meta["ylim"])
        direction = "↑ better" if meta["higher_is_better"] else "↓ better"
        ax.set_ylabel(direction, fontsize=9, color="grey")
        ax.yaxis.grid(True, linestyle="--", alpha=0.4, zorder=0)
        ax.set_axisbelow(True)

        max_std = max(stds) if stds else 0
        for bar, mean_val in zip(bars, means):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max_std * 0.05 + 0.008,
                f"{mean_val:{meta['fmt']}}",
                ha="center", va="bottom", fontsize=8, fontweight="bold",
            )

    fig.suptitle("Experiment 3 — MAS Pre-Deployment Performance by LLM",
                 fontsize=15, fontweight="bold", y=1.01)
    plt.tight_layout(h_pad=3.5, w_pad=2.5)
    out = out_dir / "1_grouped_bars.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.print(f"  [green]✓[/green] {out.name}")
    return out


# ── Plot 2: Radar / spider chart ─────────────────────────────────────────── #

def _normalize(df: pd.DataFrame, metric: str, higher_is_better: bool) -> pd.Series:
    col = df[metric].dropna()
    mn, mx = col.min(), col.max()
    if mx == mn:
        return pd.Series(0.5, index=df.index)
    normed = (df[metric] - mn) / (mx - mn)
    return normed if higher_is_better else 1 - normed


def plot_radar(df: pd.DataFrame, out_dir: Path) -> Path:
    llm_ids = _ordered_ids(df)
    metric_keys = [m for m in METRICS if m in df.columns]
    N = len(metric_keys)
    if N < 3:
        console.print("  [yellow]Skipping radar — fewer than 3 metrics available[/yellow]")
        return out_dir / "2_radar.png"

    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles += angles[:1]  # close loop

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw={"polar": True})

    for i, lid in enumerate(llm_ids):
        sub = df[df["llm_id"] == lid]
        values = []
        for metric in metric_keys:
            hi = METRICS[metric]["higher_is_better"]
            normed = _normalize(df, metric, hi)
            values.append(normed[sub.index].mean() if len(sub) else 0.0)
        values += values[:1]

        ax.plot(angles, values, color=PALETTE[i], linewidth=2,
                label=LLM_CONFIGS.get(lid, {}).get("name", lid))
        ax.fill(angles, values, color=PALETTE[i], alpha=0.15)

    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(
        [METRICS[m]["label"] for m in metric_keys], fontsize=9
    )
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["0.25", "0.5", "0.75", "1.0"], fontsize=7, color="grey")
    ax.set_title("Normalised Multi-metric Comparison", fontsize=13, fontweight="bold", pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.35, 1.15), fontsize=9)

    out = out_dir / "2_radar.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.print(f"  [green]✓[/green] {out.name}")
    return out


# ── Plot 3: Heatmap ───────────────────────────────────────────────────────── #

def plot_heatmap(df: pd.DataFrame, out_dir: Path) -> Path:
    llm_ids = _ordered_ids(df)
    metric_keys = [m for m in METRICS if m in df.columns]

    # Build normalized score matrix (row=LLM, col=metric)
    matrix = []
    for lid in llm_ids:
        sub = df[df["llm_id"] == lid]
        row = []
        for metric in metric_keys:
            hi = METRICS[metric]["higher_is_better"]
            normed = _normalize(df, metric, hi)
            row.append(normed[sub.index].mean() if len(sub) else np.nan)
        matrix.append(row)

    heat_df = pd.DataFrame(
        matrix,
        index=[LLM_CONFIGS.get(lid, {}).get("name", lid) for lid in llm_ids],
        columns=[METRICS[m]["label"] for m in metric_keys],
    )

    fig, ax = plt.subplots(figsize=(10, 4))
    sns.heatmap(
        heat_df, ax=ax, annot=True, fmt=".2f", cmap="RdYlGn",
        vmin=0, vmax=1, linewidths=0.5, cbar_kws={"label": "Normalised score (1=best)"},
    )
    ax.set_title("Experiment 3 — Performance Heatmap (normalised per metric)", fontsize=12, fontweight="bold")
    ax.set_xlabel("")
    ax.set_ylabel("")
    plt.xticks(rotation=20, ha="right", fontsize=9)
    plt.yticks(rotation=0, fontsize=9)

    out = out_dir / "3_heatmap.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.print(f"  [green]✓[/green] {out.name}")
    return out


# ── Plot 4: Box plots ─────────────────────────────────────────────────────── #

def plot_boxplots(df: pd.DataFrame, out_dir: Path) -> Path:
    llm_ids = _ordered_ids(df)
    key_metrics = [
        m for m in ("config_intent_accuracy", "resource_accuracy")
        if m in df.columns
    ]
    if not key_metrics:
        return out_dir / "4_boxplots.png"

    fig, axes = plt.subplots(1, len(key_metrics), figsize=(7 * len(key_metrics), 6))
    if len(key_metrics) == 1:
        axes = [axes]

    for ax, metric in zip(axes, key_metrics):
        data = [
            df[df["llm_id"] == lid][metric].dropna().tolist()
            for lid in llm_ids
        ]
        bp = ax.boxplot(data, patch_artist=True, notch=False,
                        medianprops={"color": "black", "linewidth": 2})
        for patch, color in zip(bp["boxes"], PALETTE):
            patch.set_facecolor(color)
            patch.set_alpha(0.75)

        ax.set_xticks(range(1, len(llm_ids) + 1))
        ax.set_xticklabels(
            [_llm_label(lid) for lid in llm_ids], fontsize=9
        )
        meta = METRICS.get(metric, {"label": metric, "ylim": (0, 1.05), "higher_is_better": True})
        ax.set_title(meta["label"], fontsize=11, fontweight="bold")
        ax.set_ylim(meta["ylim"])
        direction = "↑ better" if meta["higher_is_better"] else "↓ better"
        ax.set_ylabel(direction, fontsize=8, color="grey")
        ax.yaxis.grid(True, linestyle="--", alpha=0.5)

    fig.suptitle("Experiment 3 — Score Distributions per LLM", fontsize=13, fontweight="bold")
    plt.tight_layout()
    out = out_dir / "4_boxplots.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.print(f"  [green]✓[/green] {out.name}")
    return out


# ── Plot 5: Complexity breakdown bars ────────────────────────────────────── #

def plot_complexity_breakdown(df: pd.DataFrame, out_dir: Path) -> Path:
    metric = "config_intent_accuracy" if "config_intent_accuracy" in df.columns else "intent_to_deploy_accuracy"
    if "complexity" not in df.columns or metric not in df.columns:
        return out_dir / "5_complexity.png"

    llm_ids = _ordered_ids(df)
    complexities = ["simple", "medium", "complex"]
    cmap = {"simple": "#4CAF50", "medium": "#FF9800", "complex": "#F44336"}

    x = np.arange(len(llm_ids))
    width = 0.25
    offsets = [-width, 0, width]

    fig, ax = plt.subplots(figsize=(12, 6))
    for i, complexity in enumerate(complexities):
        sub = df[df["complexity"] == complexity]
        means = []
        for lid in llm_ids:
            vals = sub[sub["llm_id"] == lid][metric].dropna()
            means.append(vals.mean() if len(vals) else 0.0)
        ax.bar(
            x + offsets[i], means, width, label=complexity.capitalize(),
            color=cmap[complexity], alpha=0.85,
        )

    ax.set_xticks(x)
    ax.set_xticklabels([_llm_label(lid) for lid in llm_ids], fontsize=9)
    ax.set_ylabel("Config Intent Accuracy", fontsize=10)
    ax.set_ylim(0, 1.15)
    ax.set_title("Experiment 3 — Intent Accuracy by Complexity × LLM", fontsize=12, fontweight="bold")
    ax.legend(title="Complexity", fontsize=9)
    ax.yaxis.grid(True, linestyle="--", alpha=0.5)

    out = out_dir / "5_complexity_breakdown.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.print(f"  [green]✓[/green] {out.name}")
    return out


# ── Summary tables ────────────────────────────────────────────────────────── #

def build_summary_df(df: pd.DataFrame) -> pd.DataFrame:
    llm_ids = _ordered_ids(df)
    rows = []
    for lid in llm_ids:
        sub = df[df["llm_id"] == lid]
        row = {
            "LLM": LLM_CONFIGS.get(lid, {}).get("name", lid),
            "Size (B)": LLM_CONFIGS.get(lid, {}).get("size_b", "?"),
            "N runs": len(sub),
        }
        for metric, meta in METRICS.items():
            if metric not in df.columns:
                continue
            vals = sub[metric].dropna()
            mean = vals.mean() if len(vals) else float("nan")
            std  = vals.std()  if len(vals) > 1 else 0.0
            row[meta["label"]] = f"{mean:{meta['fmt']}} ± {std:{meta['fmt']}}"
        rows.append(row)
    return pd.DataFrame(rows)


def print_rich_table(summary: pd.DataFrame) -> None:
    table = Table(title="Experiment 3 — Summary (mean ± std)", show_lines=True)
    for col in summary.columns:
        table.add_column(col, justify="right" if col not in ("LLM",) else "left")
    for _, row in summary.iterrows():
        table.add_row(*[str(v) for v in row])
    console.print(table)


def build_complexity_df(df: pd.DataFrame) -> pd.DataFrame:
    metric = "config_intent_accuracy" if "config_intent_accuracy" in df.columns else "intent_to_deploy_accuracy"
    if "complexity" not in df.columns or metric not in df.columns:
        return pd.DataFrame()
    llm_ids = _ordered_ids(df)
    rows = []
    for lid in llm_ids:
        for complexity in ("simple", "medium", "complex"):
            sub = df[(df["llm_id"] == lid) & (df["complexity"] == complexity)]
            vals = sub[metric].dropna()
            rows.append({
                "LLM": LLM_CONFIGS.get(lid, {}).get("name", lid),
                "Complexity": complexity.capitalize(),
                "N": len(sub),
                "Config Intent Accuracy": f"{vals.mean():.2f} ± {vals.std():.2f}" if len(vals) > 1
                                          else (f"{vals.mean():.2f}" if len(vals) else "-"),
            })
    return pd.DataFrame(rows)


# ── Markdown report ───────────────────────────────────────────────────────── #

def write_markdown(
    summary: pd.DataFrame,
    complexity: pd.DataFrame,
    figures_dir: Path,
    out_path: Path,
) -> None:
    def _df_to_md(df: pd.DataFrame) -> str:
        if df.empty:
            return "_No data._"
        header = "| " + " | ".join(df.columns) + " |"
        sep    = "| " + " | ".join(["---"] * len(df.columns)) + " |"
        rows   = "\n".join(
            "| " + " | ".join(str(v) for v in row) + " |"
            for _, row in df.iterrows()
        )
        return f"{header}\n{sep}\n{rows}"

    fig_names = [
        ("1_grouped_bars.png",      "Figure 1 — Grouped bar chart (mean ± std per metric)"),
        ("2_radar.png",             "Figure 2 — Radar chart (normalised multi-metric comparison)"),
        ("3_heatmap.png",           "Figure 3 — Heatmap (normalised scores)"),
        ("4_boxplots.png",          "Figure 4 — Box plots (score distributions)"),
        ("5_complexity_breakdown.png", "Figure 5 — Complexity breakdown"),
    ]

    lines = [
        "# Experiment 3 — LLM Comparison Report",
        "",
        "Pre-deployment MAS pipeline evaluated with 5 different LLMs across 36 intents × 5 reps. "
        "No cluster deployment (config-only mode). Metrics are identical to Experiment 2.",
        "",
        "## Summary Table",
        "",
        _df_to_md(summary),
        "",
        "## Intent Accuracy by Complexity",
        "",
        _df_to_md(complexity),
        "",
        "## Figures",
        "",
    ]
    for fname, caption in fig_names:
        fpath = figures_dir / fname
        rel = fpath.relative_to(out_path.parent) if fpath.exists() else Path(fname)
        lines += [f"### {caption}", "", f"![{caption}]({rel})", ""]

    out_path.write_text("\n".join(lines))
    console.print(f"\n  [green]✓[/green] report written → {out_path}")


# ── Entry point ───────────────────────────────────────────────────────────── #

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Experiment 3 report")
    parser.add_argument(
        "--results",
        type=Path,
        default=_ROOT / "experiments" / "experiment_3" / "results" / "runs.csv",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_ROOT / "experiments" / "experiment_3" / "results" / "figures",
    )
    args = parser.parse_args()

    if not args.results.exists():
        console.print(f"[red]Results file not found: {args.results}[/red]")
        console.print("Run run_llm_comparison.py first.")
        sys.exit(1)

    args.out.mkdir(parents=True, exist_ok=True)

    console.rule("[bold]Experiment 3 — Report Generator")
    df = load(args.results)
    console.print(f"Loaded {len(df)} rows  |  LLMs: {sorted(df['llm_id'].unique())}")

    console.print("\n[bold]Generating plots...[/bold]")
    plot_grouped_bars(df, args.out)
    plot_radar(df, args.out)
    plot_heatmap(df, args.out)
    plot_boxplots(df, args.out)
    plot_complexity_breakdown(df, args.out)

    console.print("\n[bold]Summary table[/bold]")
    summary = build_summary_df(df)
    print_rich_table(summary)

    complexity_df = build_complexity_df(df)

    report_path = args.out.parent / "report.md"
    write_markdown(summary, complexity_df, args.out, report_path)

    console.print(f"\nAll outputs in {args.out.parent}/")


if __name__ == "__main__":
    main()
