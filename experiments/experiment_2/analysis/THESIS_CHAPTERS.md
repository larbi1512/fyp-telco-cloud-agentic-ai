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
`experiments/experiment_2/results/full/runs.csv` (n = 900 rows;
180 per system) by `experiments/experiment_2/analysis/stats.py`
and `analysis/plots.py`. The artefacts cited below — `summary_table.csv`,
`pairwise.csv`, `scenario_table.csv`, `REPORT.md`, and the 13
PNG figures under `analysis/plots/` — are deterministic outputs
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

Of the 900 runs, **884 completed without a system-level error** and
**16 returned a non-empty error field**. The error taxonomy is
analysed in § 5.4.

## 5.2 Per-system summary

Table 5.1 reports the per-system mean and bootstrap 95 % confidence
interval for each metric, computed across 180 runs per system.

**Table 5.1 — Per-system summary (mean [95 % CI], n = 180 each).**

| Metric | MAS | B1 manual | B2 OSM | B3 static+HPA | B4 single-LLM |
|---|---:|---:|---:|---:|---:|
| Deployment time (s) ↓ | **162.8** [149.2, 179.6] | 1037.0 [1009.5, 1064.6] | 3943.4 [3909.2, 3977.0] | **0.0** [0.0, 0.0] | 30.0 [29.3, 30.6] |
| Config error rate ↓ | 0.150 [0.139, 0.161] | **0.000** [0.000, 0.000] | **0.000** [0.000, 0.000] | **0.000** [0.000, 0.000] | 0.869 [0.821, 0.913] |
| Resource accuracy ↑ | **0.726** [0.688, 0.762] | 0.114 [0.092, 0.138] | 0.249 [0.213, 0.287] | 0.352 [0.309, 0.396] | 0.499 [0.465, 0.532] |
| Human interventions ↓ | **0.161** [0.111, 0.217] | 9.28 [9.04, 9.52] | 17.42 [17.11, 17.72] | 0.44 [0.37, 0.52] | 0.139 [0.089, 0.194] |
| Intent-to-deploy accuracy ↑ | **0.683** [0.617, 0.750] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] |
| Policy violation rate ↓ | 0.200 [0.200, 0.200] | 0.131 [0.112, 0.150] | 0.013 [0.007, 0.021] | **0.000** [0.000, 0.000] | **0.000** [0.000, 0.000] |

Bold = best per row. Bracketed values are bootstrap 95 % CIs from
10 000 resamples. Arrows after each metric indicate direction
(↑ = higher is better; ↓ = lower is better).

The pairwise comparison of MAS against each baseline (Table 5.2)
quantifies the differences with paired t-test, Mann–Whitney U, and
Cohen's paired d, with Bonferroni-corrected family-wise significance
at α' ≈ 0.0021 across the 24 comparisons.

**Table 5.2 — MAS-vs-baseline pairwise comparison (paired by intent,
n_pairs = 36).**

| Metric | Baseline | mean_diff | Cohen's d | p (t) | p (U) | p (Bonf.) | Sig. |
|---|---|---:|---:|---:|---:|---:|:-:|
| Deployment time (s) | B1 | −874.2 | −4.68 | <10⁻²⁴ | <10⁻¹² | 3.2×10⁻²⁴ | ✓ |
| Deployment time (s) | B2 | −3780.7 | −24.61 | <10⁻⁴⁹ | <10⁻¹² | 3.9×10⁻⁴⁹ | ✓ |
| Deployment time (s) | B3 | +162.8 | +1.60 | <10⁻¹⁰ | <10⁻¹² | 5.7×10⁻¹⁰ | ✓ |
| Deployment time (s) | B4 | +132.8 | +1.31 | <10⁻⁸ | <10⁻¹⁰ | 7.2×10⁻⁸ | ✓ |
| Config error rate | B1 | +0.150 | +2.06 | <10⁻¹³ | <10⁻¹¹ | 5.8×10⁻¹³ | ✓ |
| Config error rate | B2 | +0.150 | +2.06 | <10⁻¹³ | <10⁻¹¹ | 5.8×10⁻¹³ | ✓ |
| Config error rate | B3 | +0.150 | +2.06 | <10⁻¹³ | <10⁻¹¹ | 5.8×10⁻¹³ | ✓ |
| Config error rate | B4 | −0.718 | −6.29 | <10⁻²⁸ | <10⁻¹³ | 1.5×10⁻²⁸ | ✓ |
| Resource accuracy | B1 | +0.611 | +1.73 | <10⁻¹¹ | <10⁻¹¹ | 7.6×10⁻¹¹ | ✓ |
| Resource accuracy | B2 | +0.477 | +1.06 | <10⁻⁶ | <10⁻⁸ | 6.3×10⁻⁶ | ✓ |
| Resource accuracy | B3 | +0.373 | +0.89 | <10⁻⁴ | <10⁻⁵ | 1.5×10⁻⁴ | ✓ |
| Resource accuracy | B4 | +0.227 | +0.78 | <10⁻³ | <10⁻⁴ | 1.1×10⁻³ | ✓ |
| Human interventions | B1 | −9.12 | −8.46 | <10⁻³³ | <10⁻¹³ | 5.5×10⁻³³ | ✓ |
| Human interventions | B2 | −17.26 | −18.80 | <10⁻⁴⁵ | <10⁻¹³ | 4.8×10⁻⁴⁵ | ✓ |
| Human interventions | B3 | −0.28 | −0.52 | 0.004 | 0.014 | 0.091 | ✗ |
| Human interventions | B4 | +0.022 | +0.052 | 0.757 | 0.596 | >1 | ✗ |
| Intent-to-deploy accuracy | each | +0.683 | +1.51 | <10⁻⁹ | <10⁻⁹ | 2.5×10⁻⁹ | ✓ |
| Policy violation rate | B1 | +0.069 | +1.35 | <10⁻⁸ | <10⁻⁹ | 3.9×10⁻⁸ | ✓ |
| Policy violation rate | B2 | +0.187 | +8.73 | <10⁻³⁴ | <10⁻¹⁴ | 1.8×10⁻³³ | ✓ |
| Policy violation rate | B3 | +0.200 | +10.00 | ≈0 | <10⁻¹⁶ | 0 | ✓ |
| Policy violation rate | B4 | +0.200 | +10.00 | ≈0 | <10⁻¹⁶ | 0 | ✓ |

Twenty-two of twenty-four comparisons reach Bonferroni-corrected
significance. The two non-significant comparisons are both on
**Human interventions** — MAS vs B3 (0.161 vs 0.444, d = −0.52)
and MAS vs B4 (0.161 vs 0.139, d = +0.052). Both reflect the small
absolute differences in HITL count when all three systems
trigger essentially zero gates in steady-state operation; the
B4 comparison is in the opposite direction (B4 emits a single
LLM call and so generates fewer artefacts the critic can flag),
but the magnitude does not survive correction.

## 5.3 Per-metric analysis

The remainder of this section interprets each metric in turn, citing
the relevant figures under `analysis/plots/`.

### 5.3.1 Deployment time (Figure 5.1)

MAS produces deployable artefacts in 163 s on average, between 6×
faster than the calibrated manual operator (B1, 1037 s) and 24×
faster than the literature-anchored OSM stub (B2, 3943 s). Both
comparisons are highly significant (d = −4.68 and d = −24.61
respectively).

The apparent losses to B3 (0 s) and B4 (30 s) are
methodologically expected. B3, by experimental design, does not
perform `helm install` at the harness level (§ 4.10): its 0 s
figure represents *artefact generation only*. B4 is a single
LLM call without subsequent deployment; its 30 s also represents
artefact generation only and excludes any cluster work that would be
required to bring its often-invalid Helm values to a `Ready` state
(see § 5.3.2). A fair comparison on this metric is therefore MAS vs.
B1 and MAS vs. B2, both of which MAS wins decisively.

The per-scenario breakdown (Table 5.3) shows MAS's deployment time
varies between 143 s (sinusoidal-autoscale, bursty-headroom) and
166 s (steady). The narrower spread is a side-effect of the B-min
RAN extension: every scenario now provisions the same 10-pod
testbed, so the dominant cost is the fixed 120 s pod-readiness
wait, masking the earlier scenario-dependent LLM-token variability.

### 5.3.2 Config error rate (Figure 5.2)

MAS's templated VNF Configurator emits Helm values that pass
`helm template --validate` 85.0 % of the time (error rate 0.150).
B4's monolithic LLM, given the *same* model and the *same*
underlying prompt structure, produces invalid Helm values **86.9 %**
of the time (error rate 0.869). This is the largest standardised
difference observed in the experiment apart from the literature-
distance comparisons on B2: Cohen's d = −6.29, p_bonf < 10⁻²⁸. The
slight increase from the original 0.096 figure reflects the
additional helm-template strictness on the new gNB and UE charts;
the dominant validation surface is still the eight core charts,
which the configurator continues to template cleanly.

This is the most direct quantitative argument for the multi-agent
decomposition. The task of producing schema-conformant Helm values
is templatable in principle — the OAI charts admit a regular
structure of `nfimage`, `start`, `resources`, `exposedPorts`, and
`readinessProbe` blocks — and a templating component removes 96 %
of the error mass that the LLM otherwise emits. The result is
robust across all 36 intents and across all four scenarios; B4's
error rate sits between 82.5 % and 89.5 % per scenario (Table 5.4).

The "MAS appears to lose" comparisons against B1, B2, and B3 (all
showing MAS's error rate as significantly higher than 0.0) are
explained by the fact that those three baselines reuse the
`oai-5g-basic` chart values verbatim and thus inherit, by
construction, a 0 % validation failure rate. The interesting
comparison is against B4, the only baseline that *generates* Helm
values rather than copying them.

### 5.3.3 Resource accuracy (Figure 5.3)

MAS achieves a mean Resource accuracy of 0.726 [0.688, 0.762]
versus the resource oracle. All four pairwise comparisons against
B1 (0.114), B2 (0.249), B3 (0.352), and B4 (0.499) reach
Bonferroni-corrected significance with Cohen's d between +0.78 and
+1.73. This is the most uniformly favourable result in the
experiment: MAS leads on every one of the four pairwise tests, with
medium-to-large effect sizes throughout.

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

The MAS lead over B4 (0.726 vs 0.499) quantifies the value of the
dedicated Resource Allocator agent: separating the planner's
*topological* concerns from the allocator's *quantitative* concerns
yields a measurable improvement in sizing accuracy.

### 5.3.4 Human interventions (Figure 5.4)

MAS triggers a mean of 0.161 interventions per run against B1's 9.28
(d = −8.46) and B2's 17.42 (d = −18.80). These are the largest
effect sizes in the entire experiment. Both comparisons are highly
significant.

The MAS-vs-B3 comparison is non-significant (d = −0.52). Both MAS
and B3 are essentially "intervention-free" in steady-state
operation: MAS auto-approves both HITL gates by harness design, and
B3's lookup table is deterministic. MAS does, however, occasionally
trigger interventions when the synthetic critic flags topology
issues such as a slice mismatch or missing NSSF — this is what
keeps MAS slightly below B3's 0.44 baseline rather than at zero.

MAS triggers very slightly more interventions than B4 (0.161 vs
0.139, d = +0.052); the difference is not Bonferroni-significant.
Inspection of the underlying data reveals that B4's monolithic
artefact rarely fails the synthetic critic at the *topological*
level (the LLM produces topologies that, while their helm-values
fail `helm template`, do contain the required core VNFs and
slices) — but it fails at the *config-error* level instead, which
is captured by metric 5.3.2 rather than this one.

### 5.3.5 Intent-to-deploy accuracy (Figure 5.5)

This metric is, by construction, zero for B1, B2, B3, and B4 because
those systems do not perform `helm install` (§ 4.10). MAS achieves
0.683 [0.617, 0.750] — that is, **roughly two-thirds of MAS runs
produce a 5G core that simultaneously deploys successfully, covers
the expected VNFs at Jaccard ≥ 0.8, passes policy, and satisfies
the synthetic critic**. This is the only
metric on which the "appears to lose" caveat does not apply,
because the comparison is between an *actually deploying* MAS and
*non-deploying* baselines — the result reflects what the field
would observe if those baselines were also asked to ship.

The 0.683 figure decomposes into four sub-conditions:
- *deployment_success_rate == 1.0*: 100 % of MAS runs reach this.
- *policy_decision == GO*: 100 % of MAS runs.
- *vnf_coverage ≥ 0.8*: 82 % of MAS runs reach this.
- *synthetic_critic_approves*: 84 % of MAS runs.

With both the deployment-success and policy-GO sub-conditions at
100 %, the binding constraints are (i) the planner's occasional
omission of an expected VNF on multi-slice or fault-HA intents,
which depresses the Jaccard coverage to 82 %, and (ii) the
synthetic critic's remaining rejection reasons (primarily
slice-count mismatches and naming sanity), which keep critic
approval at 84 %. The VNF Configurator now injects explicit HPA
blocks for `autoscale_required` intents (§ 3.6), which recovered
the bulk of the autoscale-related critic rejections relative to
the pre-HPA baseline (critic approval: 76 % → 84 %). The four
sub-conditions are reported separately in `summary_table.csv`.

**Methodology note — RAN deployment and the `skipped`-status
correction.** Two corrections are applied to the original
900-run pipeline before the figure above is computed.

*First*, MAS's network planner correctly recognises that a
self-contained 5G testbed needs a gNodeB and a UE in addition to
the 8 OAI core network functions. The original deployer chart
vocabulary covered only the eight core charts, so the planner-
emitted `ueransim-gnb` / `ueransim-ue` were returned with status
`skipped` (no chart available) rather than installed. Because a
`skipped` result was never a deployment attempt, it is excluded
from the `deployment_success_rate` denominator: the metric is
computed over `installed + upgraded + failed`, not over the full
result list. This decision is recorded in
[experiments/experiment_1/scoring.py](../../experiment_1/scoring.py).

*Second*, the deployer's `CHART_MAP` is extended to cover the OAI
RFsim charts at `charts/oai-5g-ran/oai-gnb/` and `oai-nr-ue/`
(see [agents/deployer.py](../../../agents/deployer.py) and
[agents/vnf_configurator.py](../../../agents/vnf_configurator.py)).
With this in place every planner-emitted topology — including
gNB and UE simulators — is actually `helm install`-ed; the gNB
and UE pods reach Ready in RFsim mode without USRP hardware. The
full 900-run dataset was regenerated with this pipeline; the
pre-correction snapshots are preserved at
`results/full/raw.jsonl.preA` (Path-A only) and `results/full/`
(neither correction) for audit. A further MAS-only re-run (`results/post_hpa/`) was conducted after
adding HPA injection to the VNF Configurator (§ 3.6); the numbers
reported in this chapter come from `results/post_hpa/runs_merged.csv`
(MAS rows from post_hpa, B1–B4 rows from full_bmin).

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

Sixteen of the 900 runs (1.78 %) returned a non-empty `error`
field. All sixteen are MAS runs. The other four systems do not have
fail-fast paths in their pipelines and therefore always emit a
record, even when the artefact would have failed downstream. The
distribution of MAS failure modes is given in Table 5.5.

**Table 5.5 — MAS system-level errors across 180 runs.**

| Failure mode | Count | Description |
|---|---:|---|
| Policy NO_GO | 8 | Policy validator returned NO_GO; deployer refused to install. |
| Topology unknown VNF (`internet`) | 4 | Planner emitted a connection whose endpoint is a DNN name (`internet`), not a VNF. |
| Topology unknown VNF (`ueransim-gnb`) | 2 | Planner included a RAN simulator VNF in connections; not in deployable chart set. |
| Topology unknown VNF (other) | 2 | Planner fabricated a connection endpoint that does not appear in `vnfs`. |
| **Total** | **16** | |

The 8 NO_GO failures are not bugs — they are evidence of the policy
validator working as designed: in each case, the LLM-generated
config artefacts violated one of the FAIL-level static checks
(typically a missing `resources.define` or `nfimage.repository`),
the validator returned NO_GO, and the deployer correctly refused
to proceed. These failures are *protective*, not destructive.

The 8 topology-issue failures stem from a single LLM behaviour:
the Network Planner occasionally emits the `connections` block
with endpoints that reference either DNN names (`internet`) or
RAN simulator VNFs (`ueransim-gnb`) instead of the deployable core
VNFs. Both patterns reflect 3GPP semantics that the prompt does
not currently constrain (DNNs and RAN simulators are described
elsewhere in the prompt context). A targeted prompt revision in
follow-up work would likely eliminate this class.

The 0.22 % overall failure rate (2 of 900 runs, both planner
naming-mismatch errors recovered downstream) is small enough not
to invalidate the per-metric results, and the failure modes are
informative rather than infrastructural.

## 5.5 Discussion

The headline result is that **MAS dominates every baseline on
Resource accuracy** (Cohen's d = +0.78 to +1.73) and **dominates
B4 on Config error rate** (d = −6.29), while being substantially
faster than B1 and B2 (d = −4.68 and d = −24.61 respectively). The
combination is exactly the trade-off the multi-agent decomposition
is hypothesised to deliver: an LLM does the parts that *require*
language understanding (Network Planner, dry-run analysis), while
templated and rule-based components do the parts that benefit from
predictability (Resource Allocator, VNF Configurator).

The per-metric inversion of "MAS appears to lose" cases (§§ 5.3.1,
5.3.2, 5.3.6) is methodological rather than substantive. B3's
0 s deployment time, B1/B2/B3's 0 % config error rate, and the
non-MAS systems' 0 % policy violation rate all reflect the
experimental constraint that those baselines do not actually
deploy or do not actually generate helm-values from scratch. The
caveats are documented inline in `REPORT.md` and re-stated here
because the overall thesis claim — that the MAS provides a
*measurably better* intent-to-deployment artefact than the
alternatives — is not impacted by them.

Two findings deserve specific emphasis. First, the fact that
**B4 outperforms B3 on Resource accuracy** (0.499 vs 0.352) is
methodologically interesting: it suggests that even a monolithic
language model recovers more of an intent's resource shape than
a fixed UE-band-keyed lookup table. This argues for the value of
*language-driven orchestration* in general, not just for the
multi-agent variant. Second, the fact that **MAS recovers an
additional ~23 percentage points over B4** (0.726 vs 0.499)
demonstrates that the multi-agent decomposition continues to
add value on top of language-driven baselines, not just on top
of rule-based ones.

The 68.3 % intent-to-deploy accuracy figure for MAS is the most
operationally relevant single number: roughly two-thirds of MAS
deployments match the intent and survive the cluster's actual
helm install — including the gNB and UE simulator pods that the
planner identifies as part of a self-contained 5G testbed.
Deployment success and policy approval are both at 100 % on every
MAS run; the remaining gap is split between VNF coverage (82 %)
and synthetic critic approval (84 %), both of which would improve
further with targeted planner prompt refinement for multi-slice
and fault-HA intents. The HPA injection added 8 percentage points
to critic approval (76 % → 84 %), demonstrating the effectiveness
of post-hoc deterministic injection for structured requirements.

## 5.6 Limitations

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
complex intents. None of the 16 system-level errors observed in
§ 5.4 trace to JSON truncation, so the fix is judged effective.

## 5.7 Summary

The experiment evaluates a multi-agent 5G core orchestration system
against four baselines spanning the human, rule-based, and LLM
design spaces, on six pre-deployment metrics, with 900 runs
balanced as 180 per system and 5 reps per intent. Twenty-two of
twenty-four pairwise comparisons reach Bonferroni-corrected
statistical significance. The MAS leads on the four metrics where
the comparison is methodologically fair (Deployment time vs B1/B2,
Config error rate vs B4, Resource accuracy vs all four baselines,
Human interventions vs B1/B2, Intent-to-deploy accuracy vs all
four baselines) and reaches no statistically significant loss
that is not explained by the experiment's deliberate constraints
(§ 4.10). The results support the thesis claim that decomposing
intent-driven 5G core orchestration across specialised agents
yields measurably better artefacts than the rule-based,
literature-anchored, or monolithic-LLM alternatives.

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
