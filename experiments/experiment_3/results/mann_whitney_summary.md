# Experiment 3 — Mann-Whitney U + Cohen's d (unpaired, run-level)

Each LLM contributes n=180 runs (36 intents × 5 reps). Bonferroni α' = 0.0167 across 3 headline comparisons. Cliff's δ thresholds: |δ|<0.147 negligible, <0.33 small, <0.474 medium, ≥0.474 large.

## Headline (overall, run-level)

| Comparison | n(A) | n(B) | mean(A) | mean(B) | Δ mean | 95% CI | U | p | p (Bonf.) | Cohen's d | Cliff's δ | Magnitude | Sig. |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---|:-:|
| 7B vs 14B (Qwen2.5) — threshold | 180 | 180 | 0.383 | 0.694 | -0.311 | [-0.406, -0.211] | 11160 | 3.38e-09 | 1.01e-08 | -0.655 | -0.311 | small | ✓ |
| 14B vs 22B (Mistral) — plateau | 180 | 180 | 0.694 | 0.700 | -0.006 | [-0.100, +0.089] | 16110 | 0.909 | 1 | -0.012 | -0.006 | negligible | ✗ |
| 14B vs 120B (GPT-OSS) — diminishing returns | 180 | 180 | 0.694 | 0.622 | +0.072 | [-0.028, +0.172] | 17370 | 0.149 | 0.448 | +0.152 | +0.072 | negligible | ✗ |

## Stratified by complexity (exploratory, no correction)

### 7B vs 14B (Qwen2.5) — threshold

| Stratum | n(A) | n(B) | mean(A) | mean(B) | Δ mean | 95% CI | p (MW) | Cohen's d | Cliff's δ | Magnitude |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| Simple | 35 | 35 | 0.229 | 0.543 | -0.314 | [-0.543, -0.086] | 0.00748 | -0.672 | -0.314 | small |
| Medium | 85 | 85 | 0.706 | 0.824 | -0.118 | [-0.247, +0.012] | 0.0718 | -0.278 | -0.118 | negligible |
| Complex | 60 | 60 | 0.017 | 0.600 | -0.583 | [-0.717, -0.450] | 5.72e-12 | -1.616 | -0.583 | large |

### 14B vs 22B (Mistral) — plateau

| Stratum | n(A) | n(B) | mean(A) | mean(B) | Δ mean | 95% CI | p (MW) | Cohen's d | Cliff's δ | Magnitude |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| Simple | 35 | 35 | 0.543 | 0.286 | +0.257 | [+0.029, +0.486] | 0.0307 | +0.533 | +0.257 | small |
| Medium | 85 | 85 | 0.824 | 0.753 | +0.071 | [-0.047, +0.200] | 0.262 | +0.172 | +0.071 | negligible |
| Complex | 60 | 60 | 0.600 | 0.867 | -0.267 | [-0.417, -0.117] | 0.00102 | -0.627 | -0.267 | small |

### 14B vs 120B (GPT-OSS) — diminishing returns

| Stratum | n(A) | n(B) | mean(A) | mean(B) | Δ mean | 95% CI | p (MW) | Cohen's d | Cliff's δ | Magnitude |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| Simple | 35 | 35 | 0.543 | 0.286 | +0.257 | [+0.029, +0.486] | 0.0307 | +0.533 | +0.257 | small |
| Medium | 85 | 85 | 0.824 | 0.800 | +0.024 | [-0.094, +0.141] | 0.697 | +0.060 | +0.024 | negligible |
| Complex | 60 | 60 | 0.600 | 0.567 | +0.033 | [-0.150, +0.217] | 0.715 | +0.067 | +0.033 | negligible |
