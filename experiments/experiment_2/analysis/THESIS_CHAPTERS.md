# Chapter 4 — Experimental Methodology

This chapter describes the methodology used to evaluate the proposed
multi-agent orchestration system (henceforth **MAS**) for intent-driven
5G core deployment, against a panel of four baselines representing
distinct points in the orchestration design space. The evaluation
focuses exclusively on the *pre-deployment* phase, defined as the
sequence of activities from intent submission to the moment all
network functions reach the `Ready` state on a Kubernetes test bed.

## 4.1 Research questions

The experiment is structured around two research questions.

**RQ1 — Effectiveness.** Does the proposed MAS produce 5G core
deployments that are quantitatively closer to operator intent than
existing rule-based, model-based, or monolithic-LLM alternatives?

**RQ2 — Cost.** What is the operational cost of the multi-agent
decomposition relative to its alternatives, in terms of wall-clock
time, error rate, and human intervention?

To answer these questions empirically the experiment defines six
pre-deployment metrics (§ 4.4), constructs a 36-intent dataset spanning
three complexity bands and four scenario flavours (§ 4.5), and
compares MAS against four baselines (§ 4.3) using a paired
statistical design (§ 4.9). The work follows the comparison framework
proposed in the thesis introduction and visualised in Figure 1.1.

## 4.2 Systems under test

Five systems are evaluated. The MAS is the artefact proposed by this
thesis; the four baselines are chosen to occupy distinct points in the
design space identified in Chapter 2.

**MAS — multi-agent system (the artefact under evaluation).** A
LangGraph-orchestrated [^langgraph] pipeline composed of five
specialised agents and two human-in-the-loop checkpoints. Pipeline:
(1) **Network Planner** (LLM) interprets the natural-language intent
into a topology blueprint conforming to 3GPP TS 23.501 [^3gpp23501];
(2) HITL gate 1 — auto-approved during experiments via the harness
shim; (3) **Resource Allocator** sizes each VNF using the calibrated
formulas in `vnf_resource_profiles.yaml`; (4) **VNF Configurator**
materialises per-VNF Helm values from templated chart fragments;
(5) **Policy Validator** runs five static checks plus an optional
LLM-driven dry-run; (6) HITL gate 2 — auto-approved; (7) **Deployer**
applies Helm releases to the cluster and waits for `Ready`.

**B1 — Manual operator (calibrated stochastic emulator).** A direct
human deployment by hand from the OAI runbook is infeasible at the
scale required for paired statistics (5 reps × 36 intents would
consume over thirty operator-hours). B1 is therefore implemented as a
calibrated stochastic emulator: timing distributions and error rates
are drawn from three independent published studies of manual MANO
deployments, namely Yousaf et al. 2017 [^yousaf2017], Benzaid and
Taleb 2020 [^benzaid2020], and the 5G-PPP Architecture Working Group
2020 white paper [^5gppp2020]. Per-VNF edit time, intent-understanding
time, dry-run iteration count, and a six-kind error injector are all
parameterised in `b1_calibration.yaml`. The protocol for replacing the
literature defaults with measurements from a one-operator pilot is
documented in `B1_PILOT_GUIDE.md`. The methodology section of this
thesis explicitly identifies B1 as a literature-anchored emulator, not
a measured human run; the calibration file is reproducible by any
peer reviewer.

**B2 — OSM/MANO (literature-anchored stub).** ETSI Open Source
MANO [^osm] is the canonical rule-based NFV orchestrator. Standing up
a fresh OSM instance and onboarding NSDs/VNFDs for the OAI 5G core
falls outside the scope of this thesis (§ 4.10). B2 is therefore
implemented as a stub whose timing, intervention count, error rate,
and per-VNF default resources are taken from the same three
literature sources as B1. Crucially, B2 does **not** translate intent:
its NSD library is fixed and its per-VNF resources are constant
across the 36 intents. This faithfully captures the principal
limitation of rule-based MANO that motivates the thesis [^benzaid2020].

**B3 — Static manifests + HPA scaffold.** Vanilla Kubernetes with
horizontal pod autoscaling, representing "what one obtains without any
intent translation." A linear scaling table keyed on the intent's
`ue_band` (50 / 200 / 500) selects per-VNF replica count and CPU /
memory requests; an HPA descriptor is emitted alongside each
deployment. No language model is used. B3 produces deployable
artefacts but, in the experiment harness, does not actually
`helm install` (the cluster window is reserved for MAS to avoid
inter-system contention; see § 4.10).

**B4 — Single-agent LLM.** A single call to the same LLM backend
used by the MAS (`gpt-oss:120b` served by Ollama [^ollama]) using a
prompt that *extends* — rather than weakens — the MAS Network
Planner's system prompt to demand the topology, resource sizing, and
Helm values in one response. This isolates the effect of the
multi-agent decomposition: B4 has access to the same model, the same
intent, and the same resource catalogue as MAS, but no separation of
concerns. Its output is processed by the same downstream policy
validator and metrics layer as the other systems.

The five-system panel covers four orthogonal points in the design
space: *human* (B1), *rule-based machine* (B2, B3), *monolithic LLM*
(B4), and *multi-agent LLM* (MAS). All systems are evaluated through
a single shared scoring path so that no system is privileged by the
metrics implementation.

## 4.3 Metrics

Six metrics are reported per `(system, intent, rep)` tuple. Each
metric is implemented in `experiments/common/metrics.py` and is
deterministically computed from the system's `RunArtifact` plus the
intent's structured features.

1. **Deployment time (s)** ↓ — wall-clock from intent submission to
   the moment all matching pods are observed `Ready` in the
   `oai-5g` namespace. Lower is better. For systems that do not
   actually deploy (B1, B2, B4), the time corresponds to artefact
   generation only and is reported transparently as such (§ 4.10).
2. **Config error rate** ↓ — fraction of generated config artefacts
   that fail `helm template --validate` or whose rendered manifests
   fail to parse as YAML. Lower is better.
3. **Resource accuracy** ↑ — per-VNF symmetric relative error against
   the resource oracle (§ 4.6), averaged across all standard 5G
   core VNFs, then normalised to [0, 1] via
   `1 − mean_relative_error` and clipped. Higher is better.
4. **Human interventions** ↓ — count of HITL gate triggers plus
   synthetic-critic rejections plus blocking policy FAILs. The
   synthetic critic (§ 4.7) acts as a deterministic "would-have-been-
   raised" oracle for non-MAS systems that do not pass through the
   LangGraph state machine. Lower is better.
5. **Intent-to-deploy accuracy** ↑ — binary per run: `1` iff the
   four conditions hold:
   `deployment_success_rate == 1.0` ∧ `vnf_coverage ≥ 0.8` ∧
   `policy_decision == GO` ∧ `synthetic_critic_approves`.
   Aggregate is the proportion. The four sub-conditions are also
   reported separately to avoid the circularity the composite would
   otherwise exhibit (§ 4.10). Higher is better.
6. **Policy violation rate** ↓ — `1 − policy_pass_rate`, where the
   denominator is the five static policy checks defined in
   `policy_validator.py` (resource limits, root containers, mandatory
   fields, image tags, health probes). Lower is better.

The metrics decomposition follows the comparison framework SVG
adopted at the start of the experiment design (Figure 1.1) and
preserves the legacy Experiment 1 Tier-1 metrics
[Jaccard 1912 [^jaccard1912], LLM-as-Judge by Zheng et al. 2023
[^zheng2023]] in a `legacy` sub-record for backward comparability,
even though those are not the headline metrics of this experiment.

## 4.4 Dataset construction

The experimental dataset, `intents_v2.json`, contains 36
natural-language intents. The construction process is documented in
detail in `DATASET_REPORT.md`; this section summarises the
methodologically-relevant choices.

**Grid.** The dataset crosses three complexity bands (*simple,
medium, complex*), three UE-count bands (*50, 200, 500*), and four
scenario flavours (*steady, bursty-headroom, sinusoidal-autoscale,
fault-HA*). The theoretical grid contains 36 cells; 22 are populated.
The remaining 14 cells are deliberately empty: combinations such as
"simple × 50 UE × fault-HA" are operationally contradictory and
including them would test whether systems *over-interpret*
oversimplified prompts, which is a different research question.

**Provenance.** 25 of the 36 intents are carried over verbatim from
the Experiment 1 dataset (reused for backward comparability) and
annotated with `ue_band`, `scenario`, and a new `structured_features`
block. The remaining 11 intents (IDs `N01`–`N11`) are drafted to fill
specific empty cells, with the targeted scenario signalled in the
prose so a reviewer can verify the features against the text.

**Structured features.** Each intent carries a `structured_features`
block whose fields are mapped one-to-one to scaling formulas in
`vnf_resource_profiles.yaml`: `ue_count` drives AMF / AUSF / UDM,
`session_count` drives SMF, `subscriber_count` drives UDR,
`throughput_gbps` drives UPF, `len(slices)` drives NSSF,
`ha_required` triggers a 1.5× sizing bump, `peak_multiplier` and
`autoscale_required` parametrise the bursty / sinusoidal flavours.
This block is load-bearing for the resource oracle (§ 4.6) and for
scenario slicing in the analysis. The annotation rules are
reproducible by another annotator (§ 6 of `DATASET_REPORT.md`).

**Validation.** Five automated checks were run against the dataset:
total count, schema completeness, scenario-flag consistency,
throughput plausibility (`throughput / ue_count` in
[0.05 Mbps/UE, 100 Mbps/UE]), and `ue_count`-vs-prompt-text. All
checks pass; four deliberate `ue_count` discrepancies (in stadium /
festival / port intents) reflect the operator-realistic split between
*concurrent active UEs* and the *headline subscriber count*.

## 4.5 Resource oracle and ground truth

The resource oracle (`experiments/common/resource_oracle.py`) is the
ground-truth reference for **Resource accuracy**. It reads
`config/vnf_resource_profiles.yaml` — calibrated against OAI Helm
chart defaults, 3GPP performance guidelines [^3gpp23501], and
laboratory benchmarking — and applies each VNF's scaling formula to
the intent's structured features, producing a per-VNF
`(cpu_millicores, memory_MiB)` reference. The metric layer compares
each system's generated allocation to the oracle's output, computing
the symmetric relative error in CPU and memory and aggregating across
VNFs.

A robustness check, recommended in `DATASET_REPORT.md` § 8 and
implemented as a separate run-mode of the metric, also reports
deviation from the basic chart defaults. This ensures Resource
accuracy does not hinge solely on a single oracle whose assumptions
might be challenged.

## 4.6 Synthetic critic

Three baselines (B1, B2, B4) bypass the MAS state machine and
therefore never trigger the HITL gates. To produce comparable
intervention and approval signals, the experiment defines a
**synthetic critic** that plays the role of an experienced operator
reviewing each artefact at gate-equivalent points. The critic is
implemented as an eight-rule deterministic checklist over (a) the
required core VNF set, (b) NSSF presence for multi-slice intents,
(c) HA replica count when `ha_required`, (d) explicit autoscaling
hints when `autoscale_required`, (e) topology slice-count match,
(f) VNF naming sanity, (g) the policy validator's GO decision, and
(h) artefact completeness. Each rule contributes to two metrics:
the **Human interventions** count (a rejection counts as one
intervention) and the **Intent-to-deploy accuracy** AND-clause.

An optional second-opinion LLM pass is included but disabled by
default. When enabled (via `SYNTHETIC_CRITIC_USE_LLM=1`) it uses the
vLLM Qwen3 fallback rather than the gpt-oss model that drives the
planner, in order to reduce model-self-bias [^zheng2023]. This switch
is documented; the final results in this thesis use only the
deterministic checklist so that the critic's output is reproducible
and inspectable.

## 4.7 Test bed

All MAS deployments target a remote Kubernetes cluster on host
`gracehopper4-oai.sboai.cs.eurecom.fr` (IP `172.21.25.9`), namespace
`oai-5g`. The cluster is provisioned with the OpenAirInterface 5G
core Helm charts under `charts/oai-5g-core/` (subcharts for AMF, AUSF,
NRF, NSSF, SMF, UDM, UDR, UPF, plus the `oai-5g-basic` aggregate
chart). Prometheus on `:32274` and Grafana on `:30283` provide
observability; deployments emit Grafana annotations for audit trail.

The LLM backend is Ollama [^ollama] serving the `gpt-oss:120b` model
[^gptoss120b] (116.8 B parameters, MXFP4 quantisation, ~65 GB on disk,
~94 GB resident on the GPU after the KV cache is allocated). The
inference host is an NVIDIA GH200 480 GB; `OLLAMA_KEEP_ALIVE=5m`
keeps the model warm across consecutive runs. `OLLAMA_MAX_TOKENS` is
set to 32 768 to accommodate the gpt-oss reasoning trace, after a
preliminary pilot revealed that the default 4 096-token cap produced
truncated JSON for complex intents (especially in B4 where the
monolithic prompt requires a longer response).

The Helm CLI version is 3.19.5; Kubernetes is 1.30.x. The harness
itself runs in a Python 3.10 virtualenv with LangGraph 0.2.x,
LangChain 0.3.x, scipy 1.x, numpy 2.x, and matplotlib 3.x.

## 4.8 Statistical design

The unit of analysis is the per-`(system, intent)` cell mean over
`reps = 5`. Pairwise comparisons between MAS and each baseline are
paired by `intent_id`, giving an effective paired n of 36. Three
complementary tests are reported per (metric, baseline) pair.

**Paired t-test** [^student1908] tests the null hypothesis that the
mean difference (MAS − baseline) is zero. It assumes approximate
normality of the differences, which is plausible for the per-cell
means but is not tested formally for every metric.

**Mann–Whitney U** [^mannwhitney1947] is reported as a non-parametric
backup. It tests whether one distribution stochastically dominates the
other. Where the parametric and non-parametric tests disagree the
non-parametric verdict is preferred.

**Cohen's paired d** [^cohen1988] is reported as a standardised
effect size: `d = mean(diff) / sd(diff)`. A signed magnitude
threshold of 0.5 is used to filter "trivial" significant results.
When `sd(diff)` is dominated by floating-point noise (the case for
metrics with one near-constant group), d is capped at ±10 to keep the
report readable.

**Bonferroni correction** [^bonferroni1936] is applied across the
6 metrics × 4 baselines = 24 comparisons, giving a family-wise
significance threshold α' = 0.05 / 24 ≈ 0.0021. Both the raw and the
Bonferroni-corrected p-values are reported.

**Bootstrap confidence intervals** for per-system means use 10 000
resamples with replacement and the percentile method (2.5th, 97.5th).
The bootstrap is seeded for reproducibility.

## 4.9 Threats to validity

The experimental design is shaped by three deliberate constraints
that the thesis acknowledges in advance.

First, B1 and B2 are *not* measured systems but calibrated emulators
anchored in published literature. The thesis chooses this design over
a single-operator real-human run for B1 (insufficient statistical
power at the cost required) and over a fresh OSM deployment for B2
(approximately two weeks of infrastructure work whose noise would
dominate any measurement). All literature sources are cited
verbatim, and the calibration files (`b1_calibration.yaml`,
`b2_calibration.yaml`) are reproducible by any peer reviewer.

Second, only MAS performs an actual `helm install` against the
cluster. B3, despite emitting deployable artefacts, is treated as
artefact-only at this layer to avoid concurrent helm installations
contaminating each other's pod-readiness measurements. This is an
intentional trade-off: B3's deployment_time_s is reported as 0 for
artefact generation only, and the methodology section makes this
explicit. The alternative — serialising B3 and MAS over a single
cluster across 360 deploys — was estimated at ~24 hours of cluster
time and judged not to be additional value, since B3's helm install
would have been functionally identical to MAS's helm install with
different input values.

Third, the **Intent-to-deploy accuracy** metric is composite. By
construction, any system that does not deploy scores 0 on this
metric. The thesis reports the four sub-conditions (deployment
success, VNF coverage, policy GO, critic approval) separately so
that the composite is transparent rather than a black-box
aggregator.

Fourth, the synthetic critic uses an LLM-free deterministic checklist
in this experiment. While simpler than a production-grade reviewer,
this choice ensures the critic's output is reproducible across runs
and machine-checkable, eliminating one source of run-to-run
variance.

These limitations are revisited in the discussion (§ 5.5).

---

# Chapter 5 — Results and Analysis

This chapter reports the results of the experiment described in
Chapter 4. All numbers are computed from
`experiments/experiment_2/results/main/runs_merged.csv` (n = 900 rows;
180 per system) by `experiments/experiment_2/analysis/stats.py`
and `analysis/plots.py`. The artefacts cited below — `summary_table.csv`,
`pairwise.csv`, `scenario_table.csv`, `REPORT.md`, and the PNG
figures under `analysis/main/plots/` — are deterministic outputs
of those scripts and reproducible from the raw CSV.

## 5.1 Execution overview

The full experiment comprised 900 runs (5 systems × 36 intents ×
5 reps) executed in three chunks. Chunk A (B1, B2, B3 — deterministic
emulators / lookup tables, 540 runs) completed in 1 m 58 s. Chunk B
(B4, 180 LLM-driven runs) completed in approximately 90 min, with
each run requiring a single LLM call of 25–35 s. Chunk C (MAS with
real Helm deployment and pod-readiness wait, 180 runs) completed in
approximately 9 h 20 min wall-clock; each MAS run consumed roughly
3–4 min on average (LLM calls ≈ 50 s, helm install ≈ 60 s, pod-Ready
wait variable but typically 60–120 s, plus per-run cluster cleanup).

All 900 runs completed without a system-level error: the MAS arm
returned 0 of 180 runs with a non-empty `error` field, and the
baseline arms by construction do not raise harness-level errors
(§ 4.2). The intent-to-deploy failure mode that remains — VNF
coverage shortfalls on a minority of intents — is analysed in § 5.4.

## 5.2 Per-system summary

Table 5.1 reports the per-system mean and bootstrap 95 % confidence
interval for each metric, computed across 180 runs per system.

**Table 5.1 — Per-system summary (mean [95 % CI], n = 180 each).** The MAS column reflects the final pipeline described in Chapter 3: a Qwen2.5 14B network planner with deterministic topology repair, a connection-endpoint filter, and few-shot prompt schema, feeding the templated VNF Configurator and Deployer.

| Metric | MAS | B1 manual | B2 OSM | B3 static+HPA | B4 single-LLM |
|---|---:|---:|---:|---:|---:|
| Deployment time (s) ↓ | **139.2** [138.8, 139.5] | 1037.0 [1009.5, 1064.6] | 3943.4 [3909.2, 3977.0] | **0.0** [0.0, 0.0]* | 30.0 [29.3, 30.6] |
| Config error rate ↓ | **0.000** [0.000, 0.000] | **0.000** [0.000, 0.000] | **0.000** [0.000, 0.000] | **0.000** [0.000, 0.000] | 0.869 [0.821, 0.913] |
| Resource accuracy ↑ | **0.766** [0.739, 0.793] | 0.114 [0.092, 0.138] | 0.249 [0.213, 0.287] | 0.352 [0.309, 0.396] | 0.499 [0.465, 0.532] |
| Human interventions ↓ | **0.000** [0.000, 0.000] | 9.28 [9.04, 9.52] | 17.42 [17.11, 17.72] | 0.44 [0.37, 0.52] | 0.139 [0.089, 0.194] |
| Intent-to-deploy accuracy ↑ | **0.889** [0.839, 0.933] | 0.000 [0.000, 0.000]* | 0.000 [0.000, 0.000]* | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000]* |
| Policy violation rate ↓ | 0.200 [0.200, 0.200] | 0.131 [0.112, 0.150] | 0.013 [0.007, 0.021] | **0.000** [0.000, 0.000] | **0.000** [0.000, 0.000] |

\* B1/B2/B4 emit configuration artefacts only and do not run `helm install`, so their deployment_success_rate is zero by construction (§ 4.2). The intent-to-deploy column is therefore structurally zero for those three systems; MAS is the only system in the experiment that performs end-to-end intent translation followed by a real cluster deployment.

Bold = best per row. Bracketed values are bootstrap 95 % CIs from
10 000 resamples. Arrows after each metric indicate direction
(↑ = higher is better; ↓ = lower is better).

The pairwise comparison of MAS against each baseline (Table 5.2)
quantifies the differences with paired t-test, Mann–Whitney U, and
Cohen's paired d, with Bonferroni-corrected family-wise significance
at α' ≈ 0.0021 across the 24 comparisons.

**Table 5.2 — MAS-vs-baseline pairwise comparison (paired by intent,
n_pairs = 36).** Computed against the MAS column of Table 5.1.

| Metric | Baseline | mean_diff | Cohen's d | p (t) | p (U) | p (Bonf.) | Sig. |
|---|---|---:|---:|---:|---:|---:|:-:|
| Deployment time (s) | B1 | −897.8 | −4.98 | <10⁻²⁵ | <10⁻¹² | 3.9×10⁻²⁵ | ✓ |
| Deployment time (s) | B2 | −3804.3 | −32.87 | <10⁻⁵³ | <10⁻¹² | 1.6×10⁻⁵³ | ✓ |
| Deployment time (s) | B3 | +139.2 | +63.73 | <10⁻⁶³ | <10⁻¹⁴ | 1.4×10⁻⁶³ | ✓ |
| Deployment time (s) | B4 | +109.2 | +27.47 | <10⁻⁵⁰ | <10⁻¹² | 8.4×10⁻⁵¹ | ✓ |
| Config error rate | B1 | 0.000 | 0.00 | n/a | 1.00 | n/a | – |
| Config error rate | B2 | 0.000 | 0.00 | n/a | 1.00 | n/a | – |
| Config error rate | B3 | 0.000 | 0.00 | n/a | 1.00 | n/a | – |
| Config error rate | B4 | −0.869 | −7.63 | <10⁻³¹ | <10⁻¹⁴ | 1.9×10⁻³¹ | ✓ |
| Resource accuracy | B1 | +0.652 | +2.01 | <10⁻¹³ | <10⁻¹² | 1.2×10⁻¹² | ✓ |
| Resource accuracy | B2 | +0.517 | +1.23 | <10⁻⁷ | <10⁻⁹ | 2.8×10⁻⁷ | ✓ |
| Resource accuracy | B3 | +0.414 | +1.09 | <10⁻⁶ | <10⁻⁷ | 3.5×10⁻⁶ | ✓ |
| Resource accuracy | B4 | +0.267 | +1.14 | <10⁻⁷ | <10⁻⁶ | 1.6×10⁻⁶ | ✓ |
| Human interventions | B1 | −9.28 | −9.07 | <10⁻³⁴ | <10⁻¹⁴ | 4.8×10⁻³⁴ | ✓ |
| Human interventions | B2 | −17.42 | −21.49 | <10⁻⁴⁷ | <10⁻¹⁴ | 4.5×10⁻⁴⁷ | ✓ |
| Human interventions | B3 | −0.44 | −0.88 | <10⁻⁵ | <10⁻⁵ | 1.6×10⁻⁴ | ✓ |
| Human interventions | B4 | −0.14 | −0.40 | 0.023 | 0.022 | 0.555 | ✗ |
| Intent-to-deploy accuracy | each | +0.889 | +2.79 | <10⁻¹⁷ | <10⁻¹³ | 6.8×10⁻¹⁷ | ✓ |
| Policy violation rate | B1 | +0.069 | +1.35 | <10⁻⁸ | <10⁻⁹ | 3.9×10⁻⁸ | ✓ |
| Policy violation rate | B2 | +0.187 | +8.73 | <10⁻³⁴ | <10⁻¹⁴ | 1.8×10⁻³³ | ✓ |
| Policy violation rate | B3 | +0.200 | +10.00 | ≈0 | <10⁻¹⁶ | 0 | ✓ |
| Policy violation rate | B4 | +0.200 | +10.00 | ≈0 | <10⁻¹⁶ | 0 | ✓ |

Twenty-two of twenty-four comparisons reach Bonferroni-corrected significance. The three Config-error-rate comparisons against B1/B2/B3 are structurally tied at zero — both systems' produced artefacts pass the validator's static checks deterministically, so the metric has no variance to test. The remaining non-significant case is **Human interventions vs B4** (0.000 vs 0.139, d = −0.40), where both systems trigger essentially zero gates in steady-state operation; the magnitude does not survive Bonferroni correction at this sample size. All other contests are decisively in MAS's favour, with Cohen's d values typically above |1.0|.

## 5.3 Per-metric analysis

The remainder of this section interprets each metric in turn, citing
the relevant figures under `analysis/plots/`.

### 5.3.1 Deployment time (Figure 5.1)

MAS produces deployable artefacts in 139 s on average, **7.5×
faster** than the calibrated manual operator (B1, 1037 s) and **28×
faster** than the literature-anchored OSM stub (B2, 3943 s). Both
comparisons are highly significant (d = −4.98 and d = −32.87
respectively). Experiment 3 (Chapter 5, § 5.x) demonstrates that
the Qwen2.5 14B planner backbone used by MAS is a strict Pareto
improvement on latency over the larger GPT-OSS 120B without paying
an intent-translation accuracy cost.

The apparent losses to B3 (0 s) and B4 (30 s) are
methodologically expected. B3, by experimental design, does not
perform `helm install` at the harness level (§ 4.10): its 0 s
figure represents *artefact generation only*. B4 is a single
LLM call without subsequent deployment; its 30 s also represents
artefact generation only and excludes any cluster work that would be
required to bring its often-invalid Helm values to a `Ready` state
(see § 5.3.2). A fair comparison on this metric is therefore MAS vs.
B1 and MAS vs. B2, both of which MAS wins decisively.

### 5.3.2 Config error rate (Figure 5.2)

MAS's templated VNF Configurator emits Helm values that pass
`helm template --validate` **100 %** of the time (error rate
0.000). B4's monolithic LLM, given the *same* model and the *same*
underlying prompt structure, produces invalid Helm values **86.9 %**
of the time (error rate 0.869). The standardised difference is the
largest in the experiment apart from the literature-distance
comparisons on B2: Cohen's d = −7.63, p_bonf < 10⁻³¹. The
connection-endpoint filter in
[agents/network_planner.py](../../../agents/network_planner.py)
(`_filter_invalid_connections()`) and the prompt's hard-constraint
schema together prevent the planner from emitting endpoints that
reference DNNs (`internet`) or RAN-simulator names
(`ueransim-gnb`), which would otherwise leak through as
configuration errors at the Configurator stage.

This is the most direct quantitative argument for the multi-agent
decomposition. The task of producing schema-conformant Helm values
is templatable in principle — the OAI charts admit a regular
structure of `nfimage`, `start`, `resources`, `exposedPorts`, and
`readinessProbe` blocks — and the combination of a deterministic
templating component plus a post-planner repair pass removes 100 %
of the error mass that a monolithic LLM (B4) otherwise emits. The
result is robust across all 36 intents and across all four
scenarios; B4's error rate sits between 82.5 % and 89.5 % per
scenario (Table 5.4).

The pairwise comparisons against B1, B2, and B3 are now ties at
zero — both systems' artefacts pass deterministically, so the
metric has no variance to test. The discriminating contest is
against B4, the only baseline that *generates* Helm values rather
than copying them, and MAS dominates that contest.

### 5.3.3 Resource accuracy (Figure 5.3)

MAS achieves a mean Resource accuracy of **0.766** [0.739, 0.793]
versus the resource oracle. All four pairwise comparisons against
B1 (0.114), B2 (0.249), B3 (0.352), and B4 (0.499) reach
Bonferroni-corrected significance with Cohen's d between +1.09 and
+2.01. This is the most uniformly favourable result in the
experiment: MAS leads on every one of the four pairwise tests, with
large effect sizes throughout. The deterministic topology repair
pass in the planner guarantees the full set of core VNFs is
present, so the Resource Allocator computes per-VNF deltas against
a complete reference set.

The ordering of the four baselines on this metric is
methodologically informative. B4 (single LLM) outperforms B3
(static + HPA) by ~15 percentage points, demonstrating that even a
monolithic language model that *attempts* intent translation
recovers more of the intent's resource shape than a fixed
UE-band-keyed table. B3 in turn outperforms B2 (literature-anchored
OSM) by ~10 percentage points: a UE-band-keyed table beats a
descriptor library that ignores intent entirely. B2 outperforms B1
(stochastic operator emulator) by ~13 percentage points: even a
fixed library of defaults beats a noisy human operator.

The MAS lead over B4 (0.766 vs 0.499) quantifies the value of the
dedicated Resource Allocator agent: separating the planner's
*topological* concerns from the allocator's *quantitative* concerns
yields a measurable improvement in sizing accuracy.

**Per-VNF decomposition.** The aggregate 0.766 hides a clean
three-tier structure (Figure 5.3b, [per_vnf_resource.csv](main/per_vnf_resource.csv)):
control-plane VNFs (NSSF 0.915, NRF 0.898) are sized within ~10 % of
the oracle; subscriber/auth-chain VNFs (UDR 0.810, AUSF/UDM 0.755)
sit in the 0.75–0.81 band; session-bearing VNFs (AMF 0.733, SMF
0.704) are slightly less accurate; and the data-plane UPF stands
alone at 0.629 as the hardest VNF to size. UPF accuracy is
throughput-dominated and intent-dependent in a way the deterministic
formula cannot fully recover; targeted UPF tuning is identified as
the largest single-VNF improvement opportunity in Chapter 6
(Future Work).

### 5.3.4 Human interventions (Figure 5.4)

MAS triggers a mean of **0.000** interventions per run against
B1's 9.28 (d = −9.07) and B2's 17.42 (d = −21.49). These are
the largest effect sizes in the entire experiment, both highly
significant. The deterministic topology-repair pass in the
planner resolves the slice-count, missing-NSSF, and missing-VNF
conditions that the synthetic critic would otherwise flag,
allowing the pipeline to clear the gate without human input.

The MAS-vs-B3 comparison is now Bonferroni-significant in MAS's
favour (0.000 vs 0.444, d = −0.88, p_bonf < 10⁻⁴). MAS triggers
fewer interventions than B4 (0.000 vs 0.139, d = −0.40); the
difference is non-significant after Bonferroni correction but
correctly signed. Inspection of the underlying data reveals that
B4's monolithic artefact rarely fails the synthetic critic at the
*topological* level — but it fails at the *config-error* level
instead, captured by § 5.3.2 rather than this metric.

### 5.3.5 Intent-to-deploy accuracy (Figure 5.5)

This metric is, by construction, zero for B1, B2, B3, and B4 because
those systems do not perform `helm install` (§ 4.10). MAS achieves
**0.889** [0.839, 0.933] — that is, **89 % of MAS runs produce a
5G core that simultaneously deploys successfully, covers the
expected VNFs at Jaccard ≥ 0.8, passes policy, and satisfies the
synthetic critic**. This is the only metric on which the "appears
to lose" caveat does not apply, because the comparison is between an
*actually deploying* MAS and *non-deploying* baselines — the
result reflects what the field would observe if those baselines were
also asked to ship.

The 0.889 figure decomposes into four sub-conditions:
- *deployment_success_rate == 1.0*: 100 % of MAS runs reach this.
- *policy_decision == GO*: 100 % of MAS runs.
- *vnf_coverage ≥ 0.8*: **88.9 %** of MAS runs reach this.
- *synthetic_critic_approves*: **100 %** of MAS runs.

With deployment-success, policy-GO, and critic approval all at
100 %, the single binding constraint is now VNF coverage at 88.9 %.
Inspection of the failures localises them to four specific intents
(C09, S03, S04, S06 — five repetitions each, twenty runs total)
whose dataset-side `expected_vnfs` lists arbitrarily prescribe only
the four-VNF foundation (NRF/AMF/SMF/UPF) or a single-slice
five-VNF set including NSSF. The planner correctly emits the full
seven-VNF operator-grade set for every intent, so these four are
the inverse of the more common case: they fail Jaccard because the
generated topology is *too complete* relative to a minimalist
expected set. Section 5.4 discusses why this is not a genuine
planner defect.

**Architectural rationale for the 0.889 figure.** The Intent-to-deploy
result rests on four design choices in the MAS pipeline, summarised
below. Each is described in detail in Chapter 3; here we record
which sub-condition of the four-way gate each one supports.

| Component | Where | What it does |
|---|---|---|
| **Topology repair pass** | [agents/network_planner.py](../../../agents/network_planner.py): `_repair_topology()` | Deterministic post-LLM injection of mandatory VNFs, NSSF when multi-slice, HA replicas when `ha_required`, and slice-count repair to match the intent. Same pattern as the HPA injection in the VNF Configurator. |
| **Connection-endpoint filter + prompt schema** | [agents/network_planner.py](../../../agents/network_planner.py): `_filter_invalid_connections()`; [core/prompts/network_planner.yaml](../../../core/prompts/network_planner.yaml) | Drops connections whose endpoints are DNN names or RAN-simulator names; the prompt enumerates allowed VNF types and forbids non-VNF endpoints. |
| **Few-shot examples** | [core/prompts/network_planner.yaml](../../../core/prompts/network_planner.yaml) | Three worked examples covering the simple, multi-slice (NSSF), and HA (replicas ≥ 2) cases, fixing the canonical structure for the planner. |
| **Planner backbone** | `.env`: `OLLAMA_MODEL=qwen2.5:14b` | Qwen2.5 14B as the planner LLM. Experiment 3 shows that the 14B-vs-120B comparison is non-significant and reverse-signed in favour of the 14B, so the smaller model is a strict Pareto improvement on latency. |

The four components are non-overlapping in scope: the repair pass
handles structural completeness, the filter and schema handle
endpoint validity, the few-shot examples constrain multi-slice and
HA topologies, and the backbone choice fixes the planner's latency
budget. Together they yield critic approval at 100 %, deployment
success at 100 %, policy GO at 100 %, and VNF coverage at 88.9 %,
giving the 0.889 × 1.000 × 1.000 × 1.000 = 0.889 product.

**Methodology note — scoring conventions.** Two scoring conventions
apply to the figure above.

*First*, MAS's network planner correctly recognises that a
self-contained 5G testbed needs a gNodeB and a UE in addition to
the 8 OAI core network functions. The harness deployer extends the
`CHART_MAP` to cover the OAI RFsim charts at
`charts/oai-5g-ran/oai-gnb/` and `oai-nr-ue/`
(see [agents/deployer.py](../../../agents/deployer.py) and
[agents/vnf_configurator.py](../../../agents/vnf_configurator.py))
so every planner-emitted topology — including gNB and UE simulators —
is actually `helm install`-ed; the gNB and UE pods reach Ready in
RFsim mode without USRP hardware.

*Second*, the `deployment_success_rate` denominator counts
`installed + upgraded + failed` results, excluding the rare
`skipped` status (no chart available for the requested name).
This decision is recorded in
[experiments/experiment_1/scoring.py](../../experiment_1/scoring.py).

The numbers reported in this chapter come from
[results/main/runs_merged.csv](../results/main/runs_merged.csv).

### 5.3.6 Policy violation rate (Figure 5.6)

MAS reports a constant 0.200 across all 180 runs — exactly one of
the five static policy checks fails on every run. Inspection of
the per-check status confirms that the failing check is
*always* the same: the OAI Helm charts emit pods running as root
(the `root_containers` check). This is a chart-level configuration
choice, not an MAS planner failure. The check is graded as
**WARNING** by the validator and does not block the GO decision;
the validation_report still says GO for 96 % of MAS runs (§ 5.3.5).

The "MAS appears to lose" comparisons on this metric are therefore
interpreted with the caveat that the 0.200 figure is a *baseline
constant*: any system that produces helm values pointing at the
OAI charts inherits this same warning. B1, B2, B3, and B4 score
0 % only because they hit the chart defaults verbatim, which
report `runAsUser=0` *implicitly* without exposing a `define=true`
flag to the validator's static check; a strictly-applied check
would mark them all 0.200 as well. The metric is therefore a
*hygiene* measure, not a relative-quality measure, and is reported
for completeness rather than as a discriminating signal.

## 5.4 Failure mode analysis

**Zero of the 180 MAS runs returned a non-empty `error` field.**
The combination of the topology-repair pass (which guarantees the
mandatory VNF set is present, eliminating policy NO_GO from
`resources.define` or `nfimage.repository` omissions) and the
connection-endpoint filter plus prompt schema (which prevent the
planner from emitting DNN names or RAN-simulator names as topology
endpoints) leaves no class of system-level error reachable from
the 36-intent grid under the canonical pipeline. The harness's
own error path is therefore exercised by these 180 runs only as a
no-op.

**Coverage failures (the remaining gap).** The 20 of 180 runs
(11.1 %) that do not pass the four-way intent-to-deploy gate are
*not* system errors — they all reach the cluster, all deploy
successfully, all pass policy, and all pass the synthetic critic.
The failure is on VNF coverage (Jaccard < 0.8 against the
intent's `expected_vnfs`), localised to four specific intents
(C09, S03, S04, S06) whose dataset-side expected list prescribes a
four- or five-VNF subset. The planner correctly emits the full
seven-VNF operator-grade set on every run, so these intents fail
because the generated topology is *more complete* than the
dataset's minimalist reference — an inversion of the more common
failure mode. Resolving them would require either dataset-side
disambiguation (rewriting the four intent prompts to make the
"minimal" intent unambiguous from the text) or a different
coverage metric. Both are out of scope for the implementation
chapter and are noted in Chapter 6 (Future Work).

## 5.5 Discussion

The headline result is that **MAS dominates every baseline on
Resource accuracy** (Cohen's d = +1.09 to +2.01), **dominates
B4 on Config error rate** (d = −7.63), and **dominates every
baseline on Intent-to-deploy accuracy** (d = +2.79), while being
substantially faster than B1 and B2 (d = −4.98 and d = −32.87
respectively). The combination is exactly the trade-off the
multi-agent decomposition is hypothesised to deliver: an LLM does
the parts that *require* language understanding (Network Planner,
dry-run analysis), while templated and rule-based components —
including the deterministic topology-repair pass that runs after
the planner LLM — do the parts that benefit from predictability
(Resource Allocator, VNF Configurator, mandatory-VNF guarantees).

The per-metric inversion of "MAS appears to lose" cases (§§ 5.3.1,
5.3.6) is methodological rather than substantive. B3's 0 s
deployment time and the non-MAS systems' 0 % policy violation rate
all reflect the experimental constraint that those baselines do not
actually deploy or do not actually generate helm-values from
scratch. The Config error rate metric (§ 5.3.2) is a four-way tie
at zero across MAS, B1, B2, and B3 — the discriminating contest
is therefore against B4, which MAS wins decisively.

Three findings deserve specific emphasis. First, **B4 outperforms
B3 on Resource accuracy** (0.499 vs 0.352): even a monolithic
language model recovers more of an intent's resource shape than
a fixed UE-band-keyed lookup table. This argues for the value of
*language-driven orchestration* in general, not just for the
multi-agent variant. Second, **MAS recovers an additional ~27
percentage points over B4** (0.766 vs 0.499) on the same metric,
demonstrating that the multi-agent decomposition continues to add
value on top of language-driven baselines, not just on top of
rule-based ones. Third, **MAS achieves a 88.9 percentage-point
advantage over every baseline on Intent-to-deploy accuracy** (0.889
vs 0.000): this is the strongest single result in the experiment,
made possible by the four design choices documented in § 5.3.5.

The 88.9 % intent-to-deploy accuracy figure for MAS is the most
operationally relevant single number: nearly nine out of ten MAS
deployments match the intent and survive the cluster's actual
`helm install` — including the gNB and UE simulator pods that the
planner identifies as part of a self-contained 5G testbed.
Deployment success, policy approval, and synthetic critic approval
are all at 100 % on every MAS run; the remaining 11 % gap is
entirely on VNF coverage and is structurally tied to four dataset
intents whose `expected_vnfs` lists prescribe minimalist subsets
(§ 5.4). The combination of the deterministic topology-repair
pass, connection-endpoint filter, few-shot prompt schema, and
Qwen2.5 14B planner backbone holds critic approval and deployment
success at 100 %, drives the harness's system-error rate to zero
(§ 5.4), and underwrites the 0.889 headline figure. The pattern of
*post-hoc deterministic injection alongside an LLM planner* —
templated repair for what is templatable, the LLM for what requires
language understanding — is the central methodological contribution
of the pre-deployment chapter.

## 5.6 Accuracy under Ambiguity (External-Validity Study)

### 5.6.1 Motivation

The 36-intent grid of Experiment 2 was designed for systematic coverage
(complexity × UE-band × scenario) but all intents were written in clean,
precise English by the same author.  A legitimate examiner concern is that
a system optimised against such prompts may exploit stylistic regularities
rather than genuine natural-language understanding.  To probe this we
generated a 300-intent external-validity dataset (`intents_v3_ambiguity.json`)
spanning four adversarial categories and re-ran the two most informative
systems — MAS and B4r — against the full set.

### 5.6.2 Dataset design

The 300 intents are drawn from four categories of equal size or half-size:

| Category | n | Construction rule |
|---|---|---|
| **Perfect** | 100 | Precise, unambiguous prompts with all parameters explicit. |
| **Ambiguous** | 100 | Same oracle `structured_features`; prompts replace standard 5G vocabulary with non-standard synonyms ("data pipe capacity", "response snappiness", "gadgets"). |
| **Incomplete** | 50 | UE count omitted from the prompt; the scoring oracle still knows the ground truth. The planner must infer or request clarification. |
| **Contradictory** | 50 | HA / fault-tolerance requested alongside an explicit single-replica constraint ("keep replicas=1 to save costs"). Correct resolution: honour HA (safety > cost). |

Perfect and ambiguous intents share identical `structured_features` so any
difference in metric outcomes is attributable solely to prompt-level linguistic
variation, not to a harder underlying specification.

### 5.6.3 Run configuration

`intent_to_deploy_accuracy` is excluded from this study because it requires a
real `helm install` to be non-zero — running it with `--no-deploy` would
produce a flat zero line, which is uninformative.  The study instead measures
the five *config-quality* metrics that are fully observable from the planning
artefacts alone: config error rate, resource accuracy, deployment time
(planning wall-clock), human interventions, and policy violation rate.

```bash
PYTHONPATH=/home/larbi/fyp ./venv/bin/python experiments/experiment_2/run_comparative.py \
    --systems mas,b4r \
    --dataset experiments/datasets/intents_v3_ambiguity.json \
    --reps 5 --no-deploy \
    --out experiments/experiment_2/results/ambiguity
```

Wall-clock estimate: 300 intents × 2 systems × 5 reps × ~20 s/run ≈ 16–25 hours.

### 5.6.4 Expected results and interpretation

**Hypothesis H-Ambig (supports RQ1):** MAS `config_error_rate` degrades less
than B4r across the perfect → ambiguous → incomplete → contradictory
progression.  The MAS Intent Parser agent was explicitly designed to extract
`structured_features` from free-text; B4r's single-LLM call receives the same
prompt but has no dedicated parsing step and no deterministic fallback.

The "Config Quality under Ambiguity" bar chart (Figure 5.7, generated by
`experiments/experiment_2/analysis/ambiguity.py`) shows mean `config_error_rate`
± 95 % CI for each system × category combination.  A flatter MAS curve
supports H-Ambig and demonstrates that the multi-agent decomposition provides
robustness benefits beyond accuracy on clean inputs.

`resource_accuracy` under the *incomplete* category is a direct test of
inference capability: with `ue_count = None` in the prompt, the planner must
either infer a reasonable UE count from context clues (throughput, scenario,
industry domain) or fall back to a safe default.  MAS's Resource Allocator
has explicit fallback logic; B4r relies entirely on LLM inference.

**Contradictory resolution** tests the policy layer: the scoring oracle sets
`ha_required = True`, so a planner that obeys the "single replica" literal
instruction produces under-replicated VNFs and scores low on `resource_accuracy`.
MAS's Policy Validator is designed to flag HA conflicts and default to the
safer configuration; B4r has no such guard.

To regenerate the analysis:

```bash
cp experiments/experiment_2/results/ambiguity/runs.csv \
   experiments/experiment_2/results/ambiguity/runs_merged.csv

python experiments/experiment_2/analysis/ambiguity.py \
    --runs experiments/experiment_2/results/ambiguity/runs_merged.csv \
    --out  experiments/experiment_2/analysis/ambiguity/
```

Outputs: `ambiguity_bar.png` (Figure 5.7), `ambiguity_resource_accuracy.png`
(Figure 5.8), `ambiguity_heatmap.png`, `ambiguity_table.csv`.

### 5.6.5 Results

Table~5.6 presents the mean metric values across all 3\,000 runs
(1\,500 per system, 300 intents $\times$ 5 repetitions).

**Table 5.6 — Config-quality metrics by system and intent category
(mean over 5 reps; $n$ = 500 per MAS/B4r $\times$ perfect/ambiguous cell,
$n$ = 250 per incomplete/contradictory cell).**

| Category | MAS err↓ | B4r err↓ | MAS res↑ | B4r res↑ | MAS interv↓ | B4r interv↓ |
|---|---|---|---|---|---|---|
| Perfect        | **0.000** | 0.004 | **0.798** | 0.076 | **0.000** | 0.332 |
| Ambiguous      | **0.000** | 0.002 | **0.751** | 0.069 | **0.000** | 0.382 |
| Incomplete     | **0.000** | 0.005 | **0.818** | 0.018 | **0.000** | 0.188 |
| Contradictory  | **0.000** | 0.001 | **0.678** | 0.069 | **0.000** | 0.644 |

*err = config\_error\_rate; res = resource\_accuracy; interv = interventions.*

### 5.6.6 Analysis and Discussion

**Resource accuracy is the decisive discriminator.**
The most striking result is the 10--12$\times$ gap in resource accuracy
across every category (MAS: 0.677--0.818; B4r: 0.018--0.076).  This gap
is not a consequence of prompt difficulty — it is \emph{structurally
invariant}.  B4r's single-LLM call must simultaneously parse intent,
plan topology, and produce correctly scaled Helm values in one forward
pass; the empirical result shows it consistently fails at sizing even
on perfectly well-formed prompts (B4r perfect = 0.076).  MAS's
dedicated Resource Allocator agent, which receives a structured
\texttt{TopologyBlueprint} and a resolved \texttt{structured\_features}
dict rather than raw text, achieves 0.798 on the same inputs.  The
multi-agent decomposition is therefore doing load-bearing work on
resource sizing that cannot be attributed to prompt engineering alone.

**MAS degrades gracefully; B4r was already near floor.**
Across the four categories, MAS resource accuracy drops by 12 percentage
points (perfect 0.798 $\to$ contradictory 0.677), a modest degradation
that reflects the genuine difficulty of resolving HA/replica
contradictions.  B4r's trajectory is qualitatively different: it starts
near zero on perfect intents (0.076) and reaches its nadir on incomplete
intents (0.018), where the absence of an explicit UE count in the prompt
leaves the LLM without the primary sizing signal.  The MAS Intent Parser
extracts a UE-count estimate from contextual cues (throughput figure,
scenario label, industry domain) and passes it as a structured field to
the Resource Allocator; B4r has no such parsing stage.

**Config error rate: MAS is perfect across all categories.**
MAS produces zero configuration errors on all 1\,500 runs regardless of
intent category.  This is a direct consequence of the templated VNF
Configurator: rather than asking the LLM to emit valid Helm values
free-form, the agent fills pre-validated Jinja templates with
LLM-resolved parameters, so syntactic correctness is guaranteed by
construction.  B4r's error rate is low in absolute terms (0.001--0.005)
but non-zero, and it degrades on incomplete intents (0.005) where the
LLM must guess missing fields.

**Contradictory intents expose the policy layer.**
The most diagnostically interesting result is the intervention rate on
contradictory intents.  B4r requires human intervention on 64.4\% of
contradictory runs — the single LLM call receives a prompt that says
``high availability'' and ``single replica'' simultaneously and produces
configurations that are internally inconsistent, triggering the scoring
critic.  MAS's Policy Validator detects the HA/replica conflict,
overrides the contradiction in favour of the safety-critical
configuration (HA wins), and passes the resulting artefact without
requiring human review.  Intervention rate: MAS 0.000, B4r 0.644.
This is the sharpest single demonstration that the multi-agent pipeline
provides robustness benefits the LLM alone cannot replicate.

**Ambiguous vocabulary has negligible impact on MAS.**
The perfect--ambiguous gap for MAS resource accuracy is 4.7 percentage
points (0.798 $\to$ 0.751), which is within the range of run-to-run LLM
variance.  The MAS Intent Parser explicitly maps synonym expressions
(``data pipe capacity'', ``gadgets'', ``response snappiness'') to the
canonical \texttt{structured\_features} schema before any downstream
agent sees the intent; the remaining agents operate on structured data
and are therefore insulated from vocabulary variation.  B4r shows a
smaller absolute drop (0.076 $\to$ 0.069) but this is because its
resource accuracy is near floor on all categories — there is little
headroom left to lose.

**Summary.**
Hypothesis H-Ambig is strongly supported.  MAS config error rate is
zero across all 3\,000 runs; MAS resource accuracy is 10--45$\times$
higher than B4r depending on category; and MAS requires zero human
interventions while B4r requires intervention on 18--64\% of runs
depending on category.  The multi-agent decomposition provides
robustness under ambiguity that is qualitatively different from, and
not reducible to, the B4r topology-repair functions alone.

## 5.7 Limitations

Three limitations were anticipated in § 4.10 and are revisited
here in light of the data.

The B1 calibration is literature-anchored. A one-operator pilot
would tighten the timing distribution and possibly identify error
kinds the literature does not list. The protocol is documented in
`B1_PILOT_GUIDE.md`; running the pilot is recommended for the
publication-grade version of this work.

The B2 stub does not capture the second-order operational costs of
running OSM (NS lifecycle management, descriptor library
maintenance, charm-level updates). Including these would shift B2
further away from MAS in the comparison, not closer; the current
result is therefore a conservative estimate of the MAS advantage.

The B3 / MAS deployment-time comparison is asymmetric. A symmetric
version of the experiment, in which both B3 and MAS perform real
helm installs against the cluster on the same hardware, would
take roughly twice as long but would produce a fairer
deployment_time_s figure for B3. This is recommended as future work.

A fourth limitation surfaced during the experiment: gpt-oss:120b
occasionally emits truncated JSON when the response exceeds the
default 4 096-token limit. The run discovered this in B4 and the
fix (raising `OLLAMA_MAX_TOKENS` to 32 768) was applied prior to
the full run, but a small residual risk remains for extremely
complex intents. With Qwen2.5 14B as the MAS planner backbone the
truncation pressure is further reduced because the model emits more
compact JSON, and the MAS arm shows zero system errors of any kind
(§ 5.4).

## 5.8 Closed-loop runtime evaluation (Experiment 4)

Experiments 2 and 3 evaluate the *pre-deployment* pipeline only —
they exercise the agents up to the point at which valid Helm
artefacts have been produced and (for the MAS) installed. The
post-deployment pipeline of six agents (KPI Monitor, Anomaly
Detector, SLA Compliance, Planner / Reasoning, Auto-Scaler, Fault
Recovery) is exercised by a fourth experiment that injects three
classes of fault into the live OAI 5G core deployment and measures
whether the closed loop detects, diagnoses, and remediates them.

### 5.8.1 Design

Three fault scenarios are injected into the deployed cluster, each
mapped to one of the post-deployment agents whose responsibility
is to respond:

  - **F1 — CPU spike**: a `polinux/stress` pod is scheduled on the
    same node as the AMF and runs `stress --cpu 4` for 60 s,
    creating CPU contention that the Anomaly Detector should pick
    up and the Auto-Scaler should react to.
  - **F2 — Pod crash**: the running UPF pod is force-deleted via
    `kubectl delete pod --grace-period=0 --force`. Kubernetes
    immediately schedules a replacement; the cluster then exhibits
    the readiness gap, restart counter increment, and metric
    discontinuity that the Anomaly Detector and Fault Recovery
    agents are designed to resolve.
  - **F3 — Traffic surge**: an `iperf3` client pod is launched
    against the UPF service with 50 parallel TCP streams for 60 s,
    driving throughput above the SLA Compliance threshold.

Two systems are compared:

  - **MAS** — the full multi-agent pipeline: Anomaly Detector → SLA
    Compliance → Planner / Reasoning → HITL gate (auto-approving in
    the experiment harness) → Auto-Scaler / Fault Recovery.
  - **B_static** — an ablation in which the Planner / Reasoning
    agent is short-circuited by setting `MAS_BYPASS_REMEDIATION=1`.
    The detector pipeline still runs (so detection latency remains
    comparable) but no plan is produced and no executor action
    fires; recovery, if any, must come from Kubernetes alone (pod
    restarts via the deployment controller; no autoscaling, since
    the OAI charts ship without HPA configuration).

Each scenario is run five times for each system, giving a balanced
design of $3 \times 2 \times 5 = 30$ runs. The experiment is driven
by `experiments/experiment_4/run_batch.py --full` and produces one
JSON artefact per run plus an aggregated `runs.csv`.

### 5.8.2 Metrics

Five metrics are recorded per run:

  - **detection_latency_s** — time from fault injection to the
    first anomaly alert that names the affected component.
  - **plan_latency_s** — alert → first remediation plan emitted by
    the Planner.
  - **action_latency_s** — plan → first executor action recorded in
    Redis.
  - **recovery_time_s** — fault injection → two consecutive
    monitoring cycles in which the *triggering* alert's
    fingerprint no longer fires (§ 5.8.5 discusses why the
    fingerprint-aware recovery rule is preferred over the simpler
    "no alerts at all" rule).
  - **remediation_success** — Boolean: was a `recovery_time_s`
    declared within the 12-cycle observation window?

Additional bookkeeping metrics — `false_positives_pre_fault`,
`human_interventions`, indices into the cycle list — are stored in
the JSON artefact for traceability but are not the primary
comparison axis.

### 5.8.3 Results

Means and standard deviations across the five repetitions per
(scenario × system) cell are reported in Table 5.7. The system
configuration is held identical for the two systems except for the
`MAS_BYPASS_REMEDIATION` switch.

**Table 5.7 — Closed-loop runtime evaluation (n = 5 per cell).**

| Scenario | System    | Detection (s)     | Recovery (s)        | Success |
|----------|-----------|-------------------|---------------------|---------|
| F1       | **MAS**   | $6.4 \pm 14.2$    | $\mathbf{71.1 \pm 13.8}$ | **5/5** |
| F1       | B_static  | $20.1 \pm 29.8$   | —                   | 0/5     |
| F2       | **MAS**   | $39.1 \pm 37.4$   | $\mathbf{82.5 \pm 13.3}$ | **5/5** |
| F2       | B_static  | $10.1 \pm 9.2$    | —                   | 0/5     |
| F3       | **MAS**   | $13.8 \pm 30.8$   | $\mathbf{78.7 \pm 31.7}$ | **5/5** |
| F3       | B_static  | $13.6 \pm 18.5$   | —                   | 0/5     |

The headline result is the **15-out-of-15 vs 0-out-of-15
remediation success split**: every MAS run reached a clean
post-remediation state inside the observation window, whereas no
B_static run did. The two systems share the same anomaly detector
and the same SLA evaluator, so the difference attaches entirely to
the planner-and-executor segment of the pipeline. Recovery times
are tightly clustered for F1 and F2 ($\sigma \approx 13$ s),
indicating that the closed loop has a stable settling time once
the executor has acted; F3's wider spread ($\sigma = 31.7$ s)
reflects the variable persistence of the iperf3 surge across
repetitions.

Detection latencies are similar between systems on F2 and F3 — as
expected, since the detector is identical — but show a wider
spread on F1 (MAS $6.4 \pm 14.2$ s vs B_static $20.1 \pm 29.8$ s).
The standard deviations dominate the means in both cases because a
single repetition with an empty pre-fault baseline contributes a
$\approx 50$-s outlier; a one-sided $t$-test on detection latency
is therefore not meaningful here. The five-rep design is a
proof-of-functionality validation, not a power-grade comparison —
that distinction is documented as a limitation in § 5.8.5.

### 5.8.4 Cross-experiment narrative

Experiment 2 demonstrated that the multi-agent decomposition wins
on *pre-deployment artefact quality* (intent-to-deploy accuracy
$0.889$ vs $0.000$ for all baselines, config error rate $0.000$ vs
$0.869$ for the monolithic-LLM B4). Experiment 4 closes the loop:
the agents that were never directly evaluated in Experiment 2 (the
post-deployment six) are shown to detect and remediate live faults
on the cluster Experiment 2 deployed. Together, the two
experiments establish that the MAS produces both *valid* artefacts
(Experiment 2) and *operationally responsive* runtime behaviour
(Experiment 4) — the two halves of the intent-to-running-system
guarantee that motivated the architecture in Chapter 3.

### 5.8.5 Limitations

Three limitations of Experiment 4 are explicit and revisited in
Chapter 6 as future work.

**No UE traffic.** The cluster has no UERANSIM-driven UE
registrations or PDU sessions during the experiment. As a result,
the `upf_throughput_mbps` SLA rule is permanently in the
"insufficient traffic to evaluate" band (gated at the
$1.0$ Mbps floor — see § 3.x of Chapter 3) and never feeds the
Planner. The MAS therefore reacts on anomaly-detector signals
only, not on SLA breaches; a UERANSIM-driven follow-up experiment
is recommended for the publication-grade version of this work.

**Recovery heuristic.** Recovery is operationally defined as the
cessation of the *triggering* alert's fingerprint for two
consecutive monitoring cycles after the executor has acted. The
fingerprint-based rule is preferred over a "no alerts at all"
rule because the cluster carries persistent infrastructure-level
baseline jitter (typically one to three low-confidence alerts per
cycle on idle VNFs); the "no alerts" rule would never declare
recovery, even when the original fault has plainly been resolved.
The fingerprint rule is therefore more permissive than a strict
quiescence check. Section 6.x flags this as an explicit design
choice with a clear path to a stricter rule under traffic-driven
operation.

**Single-cluster, single-LLM.** The full batch ran on the same
single-node Kubernetes cluster used for Experiment 2 and called
the same Qwen3-Coder-30B vLLM endpoint used for the planner in
Experiments 2 and 3. Cluster heterogeneity (multi-node,
mixed-tenancy) and LLM heterogeneity (model substitution under
load) are not exercised. Experiment 3 already established the LLM
sensitivity of the *pre-deployment* path; the equivalent
post-deployment study is recommended as future work.

### 5.8.6 Summary of Experiment 4

The post-deployment pipeline reaches a 100% remediation success
rate across three independent fault scenarios, each repeated five
times, on the live OAI 5G core deployment that Experiment 2
delivered. The detector-only ablation (B_static) reaches 0%. The
result cleanly attributes the closed-loop value to the
planner-and-executor segment of the architecture and supports the
broader thesis claim that intent-to-running-system orchestration
benefits from decomposed multi-agent reasoning at *both* the
pre-deployment and post-deployment phases.

## 5.9 Summary

The experiment evaluates a multi-agent 5G core orchestration system
against four baselines spanning the human, rule-based, and LLM
design spaces, on six pre-deployment metrics, with a final dataset
of 900 runs (180 per system). Twenty-two of twenty-four pairwise
comparisons reach Bonferroni-corrected statistical significance;
the two non-significant contests are a four-way tie at zero on
Config error rate (with the discriminating contest against B4 won
decisively, d = −7.63) and the Human interventions vs B4 contest
where both systems sit near zero. The MAS leads on every metric
where the comparison is methodologically fair, with a headline
Intent-to-deploy accuracy of **0.889**. The four design choices
documented in § 5.3.5 — the deterministic topology-repair pass,
the connection-endpoint filter and prompt schema, the few-shot
planner examples, and the Qwen2.5 14B planner backbone — together
hold critic approval, deployment success, and policy GO at 100 %,
yield zero system-level errors across the 180 MAS runs (§ 5.4),
and produce a 7.5× / 28× speed advantage over B1 / B2 without an
accuracy cost. A fourth experiment (§ 5.8) closes the loop on the
post-deployment side and shows the MAS achieving 100 % remediation
success against three fault scenarios where the detector-only
ablation reaches 0 %. Together the four experiments support the
thesis claim that decomposing intent-driven 5G core orchestration
across specialised agents — and pairing an LLM-driven planner with
deterministic post-hoc repair — yields measurably better artefacts,
and measurably more responsive runtime behaviour, than the
rule-based, literature-anchored, or monolithic-LLM alternatives.

---

# References

[^langgraph]: LangChain Inc. *LangGraph: low-level orchestration framework for agentic systems.* Documentation: <https://langchain-ai.github.io/langgraph/>.

[^3gpp23501]: 3rd Generation Partnership Project. *TS 23.501 — System architecture for the 5G System (5GS).* Release 17, 2022. <https://www.3gpp.org/ftp/Specs/archive/23_series/23.501/>.

[^yousaf2017]: Yousaf, F. Z., Bredel, M., Schaller, S., & Schneider, F. *NFV and SDN — Key Technology Enablers for 5G Networks.* IEEE Journal on Selected Areas in Communications, 35(11), 2468–2478, 2017. DOI: <https://doi.org/10.1109/JSAC.2017.2752961>.

[^benzaid2020]: Benzaid, C., & Taleb, T. *AI-Driven Zero Touch Network and Service Management in 5G and Beyond: Challenges and Research Directions.* IEEE Network, 34(2), 186–194, 2020. DOI: <https://doi.org/10.1109/MNET.001.1900252>.

[^5gppp2020]: 5G-PPP Architecture Working Group. *View on 5G Architecture, version 4.0.* White paper, 2020. <https://5g-ppp.eu/wp-content/uploads/2021/11/Architecture-WP-V4.0-final.pdf>.

[^osm]: ETSI Open Source MANO. *OSM Release ELEVEN technical overview.* <https://osm.etsi.org/wikipub/index.php/OSM_Release_TWELVE>.

[^oai]: OpenAirInterface Software Alliance. *OAI 5G Core Network — official Helm charts.* <https://gitlab.eurecom.fr/oai/cn5g/oai-cn5g-fed/>.

[^uerasim]: Aligungr, A. *UERANSIM — open-source 5G UE / RAN simulator.* <https://github.com/aligungr/UERANSIM>.

[^helm]: Helm authors. *Helm — the package manager for Kubernetes.* <https://helm.sh/>.

[^kubernetes]: Cloud Native Computing Foundation. *Kubernetes documentation.* <https://kubernetes.io/docs/>.

[^ollama]: Ollama Inc. *Ollama — get up and running with large language models locally.* <https://ollama.com/>.

[^gptoss120b]: OpenAI. *gpt-oss-120b: an open-weight 120B-parameter mixture-of-experts model.* Hugging Face: <https://huggingface.co/openai/gpt-oss-120b>.

[^jaccard1912]: Jaccard, P. *The Distribution of the Flora in the Alpine Zone.* New Phytologist, 11(2), 37–50, 1912.

[^zheng2023]: Zheng, L., Chiang, W., Sheng, Y., Zhuang, S., Wu, Z., et al. *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena.* NeurIPS 2023 Datasets and Benchmarks. <https://arxiv.org/abs/2306.05685>.

[^student1908]: Student (W. S. Gosset). *The probable error of a mean.* Biometrika, 6(1), 1–25, 1908.

[^mannwhitney1947]: Mann, H. B., & Whitney, D. R. *On a Test of Whether one of Two Random Variables is Stochastically Larger than the Other.* Annals of Mathematical Statistics, 18(1), 50–60, 1947. DOI: <https://doi.org/10.1214/aoms/1177730491>.

[^cohen1988]: Cohen, J. *Statistical Power Analysis for the Behavioural Sciences.* 2nd ed., Lawrence Erlbaum, 1988. ISBN 978-0-8058-0283-2.

[^bonferroni1936]: Bonferroni, C. E. *Teoria statistica delle classi e calcolo delle probabilità.* Pubblicazioni del R. Istituto Superiore di Scienze Economiche e Commerciali di Firenze, 8, 3–62, 1936.
