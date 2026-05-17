#!/usr/bin/env python3
"""
Experiment 2 — Per-intent failure analysis (MAS only).

Converts the headline "X% of intents fail" into a named, classified table:
  • Which of the 36 intents fail, and how often (across 5 reps × scenarios)
  • Which failure mode dominates each failure (coverage / policy / critic / deploy)
  • Cross-tabulation by complexity × scenario × failure mode

Inputs:
  experiments/experiment_2/results/main/raw.jsonl   (per-run components)
  experiments/experiment_2/results/main/runs.csv    (scenario / ue_band / complexity backfill)

Outputs:
  experiments/experiment_2/results/main/per_intent_failures.csv
  experiments/experiment_2/results/main/failure_mode_crosstabs.md
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
RES_DIR = _ROOT / "experiments" / "experiment_2" / "results" / "full"
JSONL = RES_DIR / "raw.jsonl"
RUNS_CSV = RES_DIR / "runs.csv"


def load_mas_runs() -> pd.DataFrame:
    rows = []
    with open(JSONL) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("system") != "mas":
                continue
            comps = (r.get("components") or {}).get("intent_accuracy_components") or {}
            rows.append({
                "intent_id": r.get("intent_id"),
                "rep": r.get("rep"),
                "complexity": r.get("complexity"),
                "scenario": r.get("scenario"),
                "ue_band": r.get("ue_band"),
                "coverage_ok": bool(comps.get("coverage_ok")),
                "policy_go": bool(comps.get("policy_go")),
                "critic_approved": bool(comps.get("critic_approved")),
                "deployment_success": bool(comps.get("deployment_success")),
                "i2d": int(r.get("intent_to_deploy_accuracy") or 0),
                "error": r.get("error") or "",
            })
    df = pd.DataFrame(rows)
    df["cia"] = df["coverage_ok"] & df["policy_go"] & df["critic_approved"]
    return df


def classify(r: pd.Series) -> str:
    """Earliest-failing pipeline stage. Order matches MAS execution order."""
    if not r["coverage_ok"]:
        return "coverage_gap"
    if not r["policy_go"]:
        return "policy_violation"
    if not r["critic_approved"]:
        return "critic_reject"
    if not r["deployment_success"]:
        return "deploy_fail"
    return "ok"


def per_intent_table(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["mode"] = df.apply(classify, axis=1)
    g = df.groupby(["intent_id", "complexity"]).agg(
        n=("i2d", "size"),
        n_scenarios=("scenario", "nunique"),
        i2d_pass_rate=("i2d", "mean"),
        cia_pass_rate=("cia", "mean"),
        cov_fail=("coverage_ok", lambda s: int((~s).sum())),
        pol_fail=("policy_go", lambda s: int((~s).sum())),
        cri_fail=("critic_approved", lambda s: int((~s).sum())),
        dep_fail=("deployment_success", lambda s: int((~s).sum())),
    ).reset_index()
    # Dominant failure mode (only for intents with at least one failure)
    dominant = (
        df[df["mode"] != "ok"]
        .groupby("intent_id")["mode"]
        .agg(lambda s: s.value_counts().idxmax())
        .rename("dominant_mode")
    )
    g = g.merge(dominant, on="intent_id", how="left").fillna({"dominant_mode": "—"})
    g = g.sort_values(["i2d_pass_rate", "intent_id"]).reset_index(drop=True)
    return g, df  # df has 'mode' attached


def make_md_report(df: pd.DataFrame, per_intent: pd.DataFrame) -> str:
    n = len(df)
    n_fail = int((df["i2d"] == 0).sum())
    fail_rate = n_fail / n
    n_intents = per_intent["intent_id"].nunique()
    n_intents_with_failure = int((per_intent["i2d_pass_rate"] < 1).sum())
    n_intents_always_fail = int((per_intent["i2d_pass_rate"] == 0).sum())

    # Failure modes overall
    fails = df[df["mode"] != "ok"]
    mode_counts = fails["mode"].value_counts()
    n_fails = len(fails)

    # Crosstabs
    by_complexity = df.groupby("complexity").agg(
        n=("i2d", "size"),
        fail_rate=("i2d", lambda s: 1 - s.mean()),
    ).round(3)
    by_scenario = df.groupby("scenario").agg(
        n=("i2d", "size"),
        fail_rate=("i2d", lambda s: 1 - s.mean()),
    ).round(3)
    cx_sc = df.groupby(["complexity", "scenario"]).agg(
        n=("i2d", "size"),
        fail=("i2d", lambda s: int((s == 0).sum())),
        fail_rate=("i2d", lambda s: round(1 - s.mean(), 3)),
    ).reset_index()
    mode_by_cx = pd.crosstab(fails["complexity"], fails["mode"])
    mode_by_sc = pd.crosstab(fails["scenario"], fails["mode"])

    def df_to_md(d: pd.DataFrame, idx_label: str = "") -> str:
        d = d.reset_index() if d.index.name else d.copy()
        cols = list(d.columns)
        if idx_label and cols and cols[0] != idx_label:
            d = d.rename(columns={cols[0]: idx_label})
            cols = list(d.columns)
        header = "| " + " | ".join(str(c) for c in cols) + " |"
        sep = "| " + " | ".join(["---"] * len(cols)) + " |"
        body = "\n".join(
            "| " + " | ".join(str(v) for v in row) + " |"
            for _, row in d.iterrows()
        )
        return "\n".join([header, sep, body])

    lines = [
        "# Experiment 2 — Per-intent failure breakdown (MAS)",
        "",
        f"**Source:** `{RUNS_CSV.relative_to(_ROOT)}` ({n} MAS runs across "
        f"{n_intents} unique intents × ~5 reps).",
        "",
        f"**Headline:** {n_fail}/{n} runs fail (intent_to_deploy_accuracy = 0) → "
        f"**{fail_rate*100:.1f}% failure rate**. "
        f"{n_intents_with_failure}/{n_intents} intents fail ≥ 1× across reps; "
        f"{n_intents_always_fail}/{n_intents} intents fail in **every** rep.",
        "",
        "## 1. Failure modes (overall)",
        "",
        "Each failed run is classified by the earliest failing pipeline stage:",
        "",
        "| Mode | Count | % of failures | % of all runs |",
        "|---|---:|---:|---:|",
    ]
    for mode in ["coverage_gap", "policy_violation", "critic_reject", "deploy_fail"]:
        c = int(mode_counts.get(mode, 0))
        pct_fails = c / n_fails * 100 if n_fails else 0
        pct_all = c / n * 100
        lines.append(f"| {mode} | {c} | {pct_fails:.1f}% | {pct_all:.1f}% |")

    lines += [
        "",
        "## 2. Failure rate by complexity",
        "",
        df_to_md(by_complexity, idx_label="complexity"),
        "",
        "## 3. Failure rate by scenario",
        "",
        df_to_md(by_scenario, idx_label="scenario"),
        "",
        "## 4. Failure rate × complexity × scenario",
        "",
        df_to_md(cx_sc),
        "",
        "## 5. Failure mode × complexity",
        "",
        df_to_md(mode_by_cx, idx_label="complexity"),
        "",
        "## 6. Failure mode × scenario",
        "",
        df_to_md(mode_by_sc, idx_label="scenario"),
        "",
        "## 7. Per-intent failure table",
        "",
        "Sorted by ascending pass-rate. `dominant_mode` is the modal failure cause "
        "for that intent (— if the intent never fails).",
        "",
        df_to_md(per_intent),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    df = load_mas_runs()
    df = df.copy()
    df["mode"] = df.apply(classify, axis=1)
    per_intent, df = per_intent_table(df)

    # Persist
    per_intent.to_csv(RES_DIR / "per_intent_failures.csv", index=False)
    md = make_md_report(df, per_intent)
    (RES_DIR / "failure_mode_crosstabs.md").write_text(md)

    # Console summary
    print(f"  per-intent table → {RES_DIR / 'per_intent_failures.csv'}")
    print(f"  crosstabs/report → {RES_DIR / 'failure_mode_crosstabs.md'}")
    print()
    print(f"Headline: {(df['i2d']==0).sum()}/{len(df)} runs fail "
          f"({(1-df['i2d'].mean())*100:.1f}%); "
          f"{(per_intent['i2d_pass_rate']<1).sum()}/{len(per_intent)} intents fail ≥1×; "
          f"{(per_intent['i2d_pass_rate']==0).sum()} intents always fail.")


if __name__ == "__main__":
    main()
