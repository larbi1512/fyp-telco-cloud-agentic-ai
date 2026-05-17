#!/usr/bin/env python3
"""
Experiment 3 — Mann-Whitney + Cohen's d analysis (unpaired, run-level).

Complements the paired Wilcoxon analysis in report.py. Treats each individual
MAS run as an independent sample (n=180 per LLM, n=900 across 5 LLMs).
Targets the three claims that anchor the Exp 3 narrative:

  1. 7B vs 14B (Qwen2.5)        — "14B threshold" (expected: significant, large d)
  2. 14B vs 22B (Mistral-Small)  — "plateau"        (expected: ns, small |d|)
  3. 14B vs 120B (GPT-OSS)       — "diminishing returns" (expected: ns or reverse)

For each comparison and stratum (overall + per-complexity):
  • Mann-Whitney U (two-sided)
  • Cohen's d (pooled SD, independent samples)
  • Cliff's delta (rank-based effect size; robust to binary outcomes)
  • Bootstrap 95% CI on the mean difference (B=10000)

Outputs:
  results/mann_whitney.csv     — full table
  results/mann_whitney_summary.md — formatted markdown table

Run:
  python experiments/experiment_3/mann_whitney_analysis.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = _ROOT / "experiments" / "experiment_3" / "results"

# --- Comparisons requested (run-level, unpaired) ------------------------- #
COMPARISONS: list[tuple[str, str, str]] = [
    ("qwen25_7b",  "qwen25_14b", "7B vs 14B (Qwen2.5) — threshold"),
    ("qwen25_14b", "mistral22b", "14B vs 22B (Mistral) — plateau"),
    ("qwen25_14b", "gpt120b",    "14B vs 120B (GPT-OSS) — diminishing returns"),
]
ALPHA = 0.05
N_BONF = len(COMPARISONS)             # bonferroni across the 3 headline tests
ALPHA_BONF = ALPHA / N_BONF           # 0.0167
BOOT_B = 10_000
RNG_SEED = 42


def load_run_level(jsonl_path: Path) -> pd.DataFrame:
    """Return run-level df with config_intent_accuracy ∈ {0,1}."""
    rows = []
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
            cia = 1 if (
                comps.get("coverage_ok")
                and comps.get("policy_go")
                and comps.get("critic_approved")
            ) else 0
            rows.append({
                "llm_id": r.get("llm_id") or r.get("system"),
                "intent_id": r.get("intent_id"),
                "rep": r.get("rep"),
                "complexity": r.get("complexity"),
                "config_intent_accuracy": cia,
            })
    df = pd.DataFrame(rows)
    # complexity may be missing in older raw.jsonl; backfill from runs.csv
    if df["complexity"].isna().any():
        runs = pd.read_csv(RESULTS_DIR / "runs.csv")
        runs = runs.rename(columns={"system": "llm_id"})
        df = df.drop(columns=["complexity"]).merge(
            runs[["llm_id", "intent_id", "rep", "complexity"]],
            on=["llm_id", "intent_id", "rep"],
            how="left",
        )
    return df


def cohens_d_independent(a: np.ndarray, b: np.ndarray) -> float:
    """Cohen's d with pooled SD for two independent samples."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("nan")
    va = a.var(ddof=1)
    vb = b.var(ddof=1)
    pooled = np.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    if pooled < 1e-12:
        md = a.mean() - b.mean()
        if md == 0:
            return 0.0
        return float(np.sign(md) * 10.0)
    return float((a.mean() - b.mean()) / pooled)


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    """Cliff's δ in [-1, 1]. Computed via Mann-Whitney U identity."""
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return float("nan")
    # U statistic counting (a>b) - (a<b) pairs; ties contribute 0
    u, _ = scipy_stats.mannwhitneyu(a, b, alternative="two-sided", method="asymptotic")
    # scipy returns U1 = #(a>b) + 0.5 * #ties.  delta = (#a>b - #a<b) / (na*nb)
    # Easier: use rank-sums directly.
    combined = np.concatenate([a, b])
    ranks = scipy_stats.rankdata(combined, method="average")
    ra = ranks[:na].sum()
    # Mean rank difference identity: delta = 2*(mean_rank_a - mean_rank_b) / (na+nb)
    rank_a = ra / na
    rank_b = (ranks.sum() - ra) / nb
    return float(2.0 * (rank_a - rank_b) / (na + nb))


def cliffs_delta_label(d: float) -> str:
    ad = abs(d)
    if ad < 0.147: return "negligible"
    if ad < 0.33:  return "small"
    if ad < 0.474: return "medium"
    return "large"


def bootstrap_ci_diff(a: np.ndarray, b: np.ndarray,
                      B: int = BOOT_B, seed: int = RNG_SEED) -> tuple[float, float]:
    """95% percentile bootstrap CI on (mean_a - mean_b)."""
    rng = np.random.default_rng(seed)
    if len(a) == 0 or len(b) == 0:
        return (float("nan"), float("nan"))
    # vectorized resampling
    idx_a = rng.integers(0, len(a), size=(B, len(a)))
    idx_b = rng.integers(0, len(b), size=(B, len(b)))
    diffs = a[idx_a].mean(axis=1) - b[idx_b].mean(axis=1)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return float(lo), float(hi)


def run_test(a_vals: np.ndarray, b_vals: np.ndarray) -> dict:
    """One Mann-Whitney comparison with effect sizes + bootstrap CI."""
    if len(a_vals) == 0 or len(b_vals) == 0:
        return {
            "n_a": len(a_vals), "n_b": len(b_vals),
            "mean_a": float("nan"), "mean_b": float("nan"),
            "mean_diff": float("nan"),
            "U": float("nan"), "p_mw": float("nan"),
            "Cohen_d": float("nan"), "Cliffs_delta": float("nan"),
            "delta_label": "—",
            "ci_lo": float("nan"), "ci_hi": float("nan"),
        }
    u_stat, p_mw = scipy_stats.mannwhitneyu(
        a_vals, b_vals, alternative="two-sided", method="asymptotic"
    )
    d = cohens_d_independent(a_vals, b_vals)
    delta = cliffs_delta(a_vals, b_vals)
    ci_lo, ci_hi = bootstrap_ci_diff(a_vals, b_vals)
    return {
        "n_a": int(len(a_vals)), "n_b": int(len(b_vals)),
        "mean_a": float(a_vals.mean()),
        "mean_b": float(b_vals.mean()),
        "mean_diff": float(a_vals.mean() - b_vals.mean()),
        "U": float(u_stat), "p_mw": float(p_mw),
        "Cohen_d": d, "Cliffs_delta": delta,
        "delta_label": cliffs_delta_label(delta),
        "ci_lo": ci_lo, "ci_hi": ci_hi,
    }


def main() -> None:
    jsonl_path = RESULTS_DIR / "raw.jsonl"
    if not jsonl_path.exists():
        print(f"Missing {jsonl_path}", file=sys.stderr)
        sys.exit(1)

    df = load_run_level(jsonl_path)
    metric = "config_intent_accuracy"
    print(f"Loaded {len(df)} run rows across {df['llm_id'].nunique()} LLMs")

    rows = []
    for a_id, b_id, label in COMPARISONS:
        # Overall
        a_all = df.loc[df["llm_id"] == a_id, metric].to_numpy()
        b_all = df.loc[df["llm_id"] == b_id, metric].to_numpy()
        res = run_test(a_all, b_all)
        res.update({"comparison": label, "A": a_id, "B": b_id, "stratum": "overall"})
        rows.append(res)

        # By complexity
        for cx in ("simple", "medium", "complex"):
            a_cx = df.loc[(df["llm_id"] == a_id) & (df["complexity"] == cx), metric].to_numpy()
            b_cx = df.loc[(df["llm_id"] == b_id) & (df["complexity"] == cx), metric].to_numpy()
            res = run_test(a_cx, b_cx)
            res.update({"comparison": label, "A": a_id, "B": b_id, "stratum": cx})
            rows.append(res)

    out_df = pd.DataFrame(rows)

    # Bonferroni only over the 3 overall headline tests (per-complexity is exploratory)
    overall_mask = out_df["stratum"] == "overall"
    out_df["p_bonf"] = np.where(
        overall_mask,
        np.minimum(out_df["p_mw"] * N_BONF, 1.0),
        np.nan,
    )
    out_df["sig"] = "—"
    out_df.loc[overall_mask, "sig"] = np.where(
        out_df.loc[overall_mask, "p_bonf"] < ALPHA, "✓", "✗"
    )

    # Reorder columns for readability
    cols = [
        "comparison", "A", "B", "stratum", "n_a", "n_b",
        "mean_a", "mean_b", "mean_diff", "ci_lo", "ci_hi",
        "U", "p_mw", "p_bonf", "Cohen_d", "Cliffs_delta", "delta_label", "sig",
    ]
    out_df = out_df[cols]

    csv_path = RESULTS_DIR / "mann_whitney.csv"
    out_df.to_csv(csv_path, index=False)
    print(f"Wrote {csv_path}")

    # Build a clean markdown summary too (overall only)
    lines = [
        "# Experiment 3 — Mann-Whitney U + Cohen's d (unpaired, run-level)",
        "",
        f"Each LLM contributes n=180 runs (36 intents × 5 reps). "
        f"Bonferroni α' = {ALPHA_BONF:.4f} across {N_BONF} headline comparisons. "
        "Cliff's δ thresholds: |δ|<0.147 negligible, <0.33 small, <0.474 medium, ≥0.474 large.",
        "",
        "## Headline (overall, run-level)",
        "",
        "| Comparison | n(A) | n(B) | mean(A) | mean(B) | Δ mean | 95% CI | U | p | p (Bonf.) | Cohen's d | Cliff's δ | Magnitude | Sig. |",
        "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---|:-:|",
    ]
    for _, r in out_df[overall_mask].iterrows():
        lines.append(
            "| {comp} | {na} | {nb} | {ma:.3f} | {mb:.3f} | {md:+.3f} | "
            "[{lo:+.3f}, {hi:+.3f}] | {u:.0f} | {pm:.3g} | {pb:.3g} | {d:+.3f} | "
            "{cd:+.3f} | {lab} | {s} |".format(
                comp=r["comparison"], na=r["n_a"], nb=r["n_b"],
                ma=r["mean_a"], mb=r["mean_b"], md=r["mean_diff"],
                lo=r["ci_lo"], hi=r["ci_hi"],
                u=r["U"], pm=r["p_mw"], pb=r["p_bonf"],
                d=r["Cohen_d"], cd=r["Cliffs_delta"],
                lab=r["delta_label"], s=r["sig"],
            )
        )

    lines += ["", "## Stratified by complexity (exploratory, no correction)", ""]
    for a_id, b_id, label in COMPARISONS:
        lines += [
            f"### {label}",
            "",
            "| Stratum | n(A) | n(B) | mean(A) | mean(B) | Δ mean | 95% CI | p (MW) | Cohen's d | Cliff's δ | Magnitude |",
            "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|",
        ]
        sub = out_df[(out_df["A"] == a_id) & (out_df["B"] == b_id) & (out_df["stratum"] != "overall")]
        for _, r in sub.iterrows():
            lines.append(
                "| {st} | {na} | {nb} | {ma:.3f} | {mb:.3f} | {md:+.3f} | "
                "[{lo:+.3f}, {hi:+.3f}] | {pm:.3g} | {d:+.3f} | {cd:+.3f} | {lab} |".format(
                    st=r["stratum"].capitalize(),
                    na=r["n_a"], nb=r["n_b"],
                    ma=r["mean_a"], mb=r["mean_b"], md=r["mean_diff"],
                    lo=r["ci_lo"], hi=r["ci_hi"],
                    pm=r["p_mw"], d=r["Cohen_d"],
                    cd=r["Cliffs_delta"], lab=r["delta_label"],
                )
            )
        lines.append("")

    md_path = RESULTS_DIR / "mann_whitney_summary.md"
    md_path.write_text("\n".join(lines))
    print(f"Wrote {md_path}")

    # Console summary
    print("\nHeadline (overall):")
    for _, r in out_df[overall_mask].iterrows():
        print(f"  {r['comparison']}: Δ={r['mean_diff']:+.3f}  d={r['Cohen_d']:+.2f}  "
              f"δ={r['Cliffs_delta']:+.2f} ({r['delta_label']})  "
              f"p={r['p_mw']:.3g}  p_bonf={r['p_bonf']:.3g}  {r['sig']}")


if __name__ == "__main__":
    main()
