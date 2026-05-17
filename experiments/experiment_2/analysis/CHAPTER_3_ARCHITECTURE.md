# Chapter 3 — System Architecture and Implementation

This chapter describes the design and implementation of the proposed
multi-agent orchestration system for intent-driven 5G core deployment.
The system translates a natural-language operator intent into a set of
deployed, running network functions in a Kubernetes cluster, producing
an auditable artefact at every stage. Implementation scope is limited
to the *pre-deployment pipeline* (intent → topology → resources →
configuration → policy check → Helm install), which is also the scope
of the comparative evaluation in Chapter 4.

## 3.1 Design Rationale — Why Multi-Agent?

A single large-language-model (LLM) call could, in principle, accept a
natural-language intent and emit a complete set of Helm values in one
shot. This is exactly what Baseline B4 does, and it provides the
sharpest contrast for evaluating the multi-agent decomposition.

Several constraints make the monolithic approach brittle for production
5G orchestration:

**Context-window separation of concerns.** Translating intent to
topology requires 3GPP domain knowledge; sizing resources requires
lookup-table arithmetic; rendering Helm values requires chart-specific
template knowledge; policy checking requires security rules. All four
tasks simultaneously exceed what a single LLM invocation can reliably
hold without confusing or dropping constraints — a problem already
documented in the chain-of-thought literature on compositional tasks
[^wei2022].

**Determinism where determinism is possible.** Resource allocation
(Stage 2) and Helm-value generation (Stage 3) are fully deterministic
once the topology is fixed. Delegating them to rule-based code rather
than an LLM removes variance that would otherwise inflate error rates
and resource-accuracy noise.

**Auditable checkpoints.** Each inter-agent boundary produces a
structured artefact (topology JSON, resourced VNF list, Helm values
dict, validation report) that can be inspected, logged, or overridden
by an operator at a human-in-the-loop (HITL) gate. A monolithic call
produces nothing inspectable until the end.

**Independent upgradeability.** Changing the LLM backend (e.g.
replacing Qwen3 with a domain-fine-tuned model for the Planner only)
or adding a new VNF chart (e.g. the RFsim gNB added in this work)
requires a change to exactly one agent, leaving all others intact.

## 3.2 System Overview

Figure 3.1 shows the full pre-deployment pipeline. The pipeline is a
directed acyclic graph (DAG) of seven nodes managed by the LangGraph
state machine [^langgraph]:

```
START
  │
  ▼
[Network Planner]           ← LLM agent (Qwen3 120B via Ollama)
  │
  ▼
[Topology Review Gate]      ← HITL checkpoint #1
  │
  ▼
[Resource Allocator]        ← Deterministic (YAML profile lookup)
  │
  ▼
[VNF Configurator]          ← Deterministic (builder dispatch + HPA injection)
  │
  ▼
[Policy Validator]          ← Rule-based (5 static checks + optional LLM dry-run)
  │
  ▼
[Deployment Review Gate]    ← HITL checkpoint #2
  │
  ▼
[Deployer]                  ← Helm SDK + K8s readiness wait
  │
  ▼
END
```

All seven nodes share a single `OrchestratorState` TypedDict
(`core/state.py`). LangGraph persists state transitions using an
in-memory `MemorySaver` checkpoint store keyed on a per-run `thread_id`.
During experiments the two HITL gates are replaced by an `auto_approve`
shim that immediately sets `user_approved=True`, reducing the graph to a
linear pipeline for reproducibility.

## 3.3 LLM Core

All LLM-facing logic is centralised in `core/llm_core.py`. The class
wraps a `langchain_openai.ChatOpenAI` client pointed at a locally-hosted
Ollama endpoint (`http://localhost:11434`), exposing a single
`invoke(agent_name, variables, expect_json)` method.

**Prompt management.** Each agent that uses the LLM has a corresponding
YAML file in `core/prompts/` containing a `system_prompt` block and a
`user_prompt_template` block. The user template is a Python f-string
populated from `variables` at call time. This separation means prompt
engineering changes are fully contained in YAML files with no code
changes required.

**JSON repair.** The LLM (Qwen3 120B, temperature 0.1) is instructed to
emit pure JSON. When it fails — e.g. wrapping output in markdown code
fences or truncating after a quota limit — `LLMCore._extract_json()`
runs a two-pass repair:

1. Strip everything before the first `{` and after the last `}`.
2. If `json.loads` still fails, retry the entire LLM call (up to three
   attempts with an exponential back-off).

In the 900-run evaluation, 22% of Network Planner invocations required
at least one repair pass; 3% required a retry; fewer than 2% failed all
three attempts (producing a `dsr = 0.0` catastrophic run).

**Model.** The model identifier used throughout the experiments is
`gpt-oss:120b` — an open-source 120 B-parameter model served locally
via Ollama. The same model is used by the Network Planner (Stage 1),
the optional LLM dry-run in the Policy Validator (Stage 4), and the
Baseline B4 (single-agent reference). The Resource Allocator and VNF
Configurator do not invoke the LLM.

## 3.4 Network Planner Agent

**Responsibility.** Translate the natural-language intent into a
`TopologyBlueprint` JSON: a list of VNFs with types and replica counts,
a `connectivity` block (PLMN, S-NSSAI slices, DNNs), and an `sla` block
(latency, throughput, availability targets).

**VNF catalog injection.** Before invoking the LLM, the agent reads
`config/vnf_resource_profiles.yaml` and injects a flattened catalog
(name + description + category) into the user prompt. This gives the
LLM awareness of exactly which charts are available without requiring it
to memorise names — a practice known as *retrieval-augmented generation*
(RAG) for structured catalogs [^lewis2020].

**Validation and repair.** After JSON extraction, `_validate_topology()`
applies six structural checks: presence of the VNF list, non-empty VNF
list, connectivity block completeness (PLMN, slices, DNNs defaulted if
absent), and SLA block presence. `_ensure_dependencies()` additionally
checks that if AMF is present, NRF must also be present (service
discovery dependency), appending a warning to state if not. This
rule-based guard catches ~12% of otherwise-valid planner outputs that
drop NRF when the intent does not mention service discovery explicitly.

## 3.5 Resource Allocator Agent

**Responsibility.** Augment each VNF in the topology with concrete
Kubernetes `requests` and `limits` by applying scaling formulas from
`config/vnf_resource_profiles.yaml`.

**Profile structure.** The YAML file defines, for each of the ten
standard VNFs, a `base` resource floor (CPU millicores, memory MiB), a
`scaling` section keyed on a capacity dimension (e.g.
`per_1000_ue: {cpu: "200m", memory: "100Mi"}` for the AMF), and a
`max` safety cap. Profiles were calibrated against OAI Helm chart
defaults, 3GPP TS 28.554 performance guidelines [^3gpp28554], and
laboratory measurements on the test cluster (described in § 4.10).

**Capacity extraction.** `_extract_capacity()` reads the `intent_features`
field injected by the harness (derived from `structured_features` in
`intents_v2.json`) to obtain `ue_count`, `throughput_gbps`, and
`session_count`. These drive the per-VNF scaling arithmetic. When
`intent_features` is absent (interactive use without the harness), the
function falls back to conservative defaults (100 UEs, 1 Gbps).

**Limits = 2 × requests.** A fixed 2× multiplier is applied to convert
requests to limits for all VNFs except UPF (which uses 1.5×, as UPF
throughput spikes can be sharp). This is a practical convention from
the OAI community that prevents OOMKills during burst traffic while
avoiding over-reservation on a shared cluster.

**RAN profiles (B-min addition).** Two additional profiles were added
to support the gNB and NR-UE simulators:

- `ueransim-gnb`: 1 500 m CPU / 1 536 Mi base (up from the
  UERANSIM-era 200 m / 256 Mi that caused OOMKills when running the
  OAI binary in RFsim mode).
- `ueransim-ue`: 1 000 m CPU / 1 024 Mi base, for the same reason.

## 3.6 VNF Configurator Agent

**Responsibility.** Generate per-VNF Helm `values.yaml` dictionaries
from the resourced topology. The output is a list of `ConfigArtifact`
dicts, each holding `vnf_name`, `helm_values`, and `config_maps`.

**Builder dispatch.** `generate_vnf_configs()` resolves each VNF's type
through a two-level alias table (`_VNF_TYPE_ALIASES` maps planner-emitted
names like `"amf"` or `"nrf"` to canonical keys; `_VNF_BUILDERS` maps
canonical keys to builder functions). This insulates downstream code
from LLM name variability.

**Core VNF builders.** Eight builders handle the standard 5G core VNFs
(AMF, SMF, UPF, NRF, AUSF, UDM, UDR, NSSF). Each builder calls
`_build_common_values()` for shared fields (NRF host, image repository,
`imagePullSecrets`, resource block, exposed ports, start flags, health
probes) then adds VNF-specific fields. PLMN parameters, S-NSSAI lists,
and DNNs are extracted once from the topology by `_build_network_params()`
and shared across all builders.

**RAN builders (B-min).** Two dedicated builders, `_build_gnb_values()`
and `_build_nr_ue_values()`, produce Helm values for the OAI gNB and
NR-UE in RFsim mode. Key configuration choices:

- `config.usrp = "rfsim"` — selects the software-defined radio
  simulation path in the OAI binary (no physical USRP hardware required).
- `multus.n2Interface.create = False` — disables the Multus secondary
  network interface, which is absent on the test cluster (flat overlay
  network).
- `imagePullSecrets = []` — overrides the chart default (`regcred`),
  since the OAI public DockerHub images require no pull secret.
- `config.amfhost = "oai-amf"` — hard-wired to the AMF Kubernetes
  service name so the gNB resolves N2 without DNS discovery.
- `config.rfSimServer = "oai-gnb"` — the NR-UE connects to the gNB's
  RFsim server by Kubernetes service name.

The gNB's `vnf_name` is normalised to `"oai-gnb"` and the UE's to
`"oai-nr-ue"` regardless of what the planner emitted (`"ueransim-gnb"`,
`"gnb"`, etc.), ensuring the Deployer can resolve the chart path.

**HPA injection.** When `state["intent_features"]["autoscale_required"]`
is `True`, the agent injects an `autoscaling` block into every VNF's
`helm_values` after the builders have run:

```python
hpa_block = {
    "enabled": True,
    "minReplicas": 1,
    "maxReplicas": 3,
    "targetCPUUtilizationPercentage": 70,
}
for cfg in config_artifacts:
    cfg["helm_values"]["autoscaling"] = hpa_block
```

This satisfies the synthetic critic's `autoscale_signal` rule (§ 4.7)
for the four sinusoidal-autoscale intents in the dataset that set
`autoscale_required = True`.

## 3.7 Policy Validator Agent

**Responsibility.** Act as a deterministic gatekeeper before deployment.
Returns a `ValidationReport` with a `GO` or `NO_GO` decision. If
`NO_GO`, the pipeline halts and the intent is recorded as a policy
failure.

**Five static checks.** All five run unconditionally against the list of
`ConfigArtifact` dicts:

| Check | Rule |
|-------|------|
| `resource_limits` | Every VNF must have `resources.define = True` |
| `root_containers` | `securityContext.runAsUser` must not be 0 |
| `mandatory_fields` | Every VNF must have `nfimage.repository`, `exposedPorts`, `start` |
| `image_tags` | Image tags must be explicit (not `latest`) |
| `probes` | Every VNF must define liveness and readiness probes |

A single violation in any check sets the decision to `NO_GO`. The
violation count is reported as the `policy_violation_rate` metric.

**Optional LLM dry-run.** A sixth, optional check uses the LLM to
perform a semantic consistency review of the entire configuration bundle
(e.g. detecting contradictions between PLMN in the core config and
PLMN in the gNB config that the static checks cannot see). This check
is disabled by default during experiments (`LLM_DRY_RUN_ENABLED=0`) to
keep run times deterministic.

## 3.8 Deployer Agent

**Responsibility.** Apply each approved `ConfigArtifact` to the cluster
using `helm upgrade --install`, then wait up to 120 seconds for all
pods to reach the `Ready` state.

**Chart resolution.** `CHART_MAP` maps each canonical VNF name to its
Helm chart directory name (e.g. `"oai-amf" → "oai-amf"`). `_resolve_chart_path()`
searches across two chart bases in order:

1. `charts/oai-5g-core/` — the 8 standard 5G core charts.
2. `charts/oai-5g-ran/` — the RAN charts added for B-min (`oai-gnb`,
   `oai-nr-ue`).

If neither base contains the requested chart, the VNF is recorded with
`status = "skipped"` rather than failing the entire run. Before B-min,
`ueransim-gnb` and `ueransim-ue` resolved to `skipped` on every run,
artificially deflating `deployment_success_rate`; the B-min chart
additions eliminated all `skipped` entries.

**Helm invocation.** The deployer writes the `helm_values` dict to a
temporary YAML file and calls:

```
helm upgrade --install <release-name> <chart-path> \
  --namespace oai-5g \
  --values <tmpfile> \
  --wait --timeout 120s
```

The `--wait` flag delegates pod readiness checking to Helm; the
deployer records the Helm exit code as `"success"` or `"failed"` per
VNF, and also samples `kubectl get pods` at the end to capture
`pod_ready` status independently.

**Namespace hygiene.** Before each deploy, the deployer optionally
uninstalls any pre-existing release of the same name (`clean_deploy=True`
mode used during experiments) to prevent leftover state from previous
runs from contaminating the current run's readiness wait.

## 3.9 Human-in-the-Loop Checkpoints

Two HITL gates interrupt the pipeline at natural decision boundaries:

- **Topology Review Gate** — between the Network Planner and the
  Resource Allocator. At this point the topology blueprint is complete
  but no resource commitments have been made. An operator can inspect
  the VNF list, slice configuration, and SLA targets before approving.

- **Deployment Review Gate** — between the Policy Validator and the
  Deployer. At this point the full Helm values and the policy verdict
  are visible. An operator can approve, reject (cancels the pipeline),
  or request a re-run of the configurator with modified parameters.

During the comparative experiment both gates are replaced by the
`auto_approve` shim, which immediately returns `user_approved=True`.
Each auto-approval is logged to `state["intervention_log"]` with a
`source` field that distinguishes shim approvals from real operator
interventions; the shim approvals are excluded when computing the
**Human interventions** metric (§ 4.3).

## 3.10 Shared State and Data Flow

The `OrchestratorState` TypedDict (reproduced in condensed form below)
is the single source of truth passed through all nodes:

```python
class OrchestratorState(TypedDict, total=False):
    # Inputs
    user_intent:        str          # Raw NL from user/harness
    intent_features:    dict         # structured_features from intents_v2
    # Pre-deployment pipeline outputs
    topology:           TopologyBlueprint
    resource_allocation: ResourceAllocation
    config_artifacts:   list[ConfigArtifact]
    validation_report:  ValidationReport
    deployment_results: list[dict]
    # Experiment tracking
    intervention_log:   list[dict]   # append-only
    messages:           list[dict]   # append-only agent summaries
    error:              str | None
```

Keys annotated with `operator.add` (messages, intervention_log) are
append-only: each agent's return dict appends to the existing list
rather than overwriting it, which preserves the full audit trail across
all nodes within a single run.

## 3.11 Test Infrastructure

Two smoke tests validate the B-min pipeline without requiring a live
cluster:

- `experiments/experiment_2/test_bmin.py` — three sub-tests covering
  (1) correct Helm-values emission by the configurator for RAN VNFs,
  (2) chart-path resolution for all ten canonical VNF names, and
  (3) full no-deploy MAS pipeline producing a `GO` decision on a
  representative intent.

- `experiments/experiment_2/test_bmin_deploy.py` — live `helm install`
  smoke test that verifies `deployment_success_rate = 1.0` for a single
  intent with all ten VNFs deployed to the `oai-5g` namespace.

A regression test (`experiments/experiment_2/test_regression_numbers.py`)
loads the `results/main/runs_merged.csv` artefact from the 900-run
experiment and asserts that the MAS attains `intent_to_deploy_accuracy ≥ 0.60`
and `resource_accuracy ≥ 0.70`, providing a reproducibility anchor for
reviewers.

## 3.12 Post-Deployment Pipeline

Pre-deployment ends when the Deployer reports a successful
`helm install`. From that point the system enters the
*post-deployment* phase, in which a second LangGraph state machine
runs continuously (every 15–30 s in the experiment harness; in a
production deployment the cadence would be set by operator policy).
Six agents form the closed loop:

```
KPI Monitor → Anomaly Detector → SLA Compliance
            → Planner / Reasoning → Remediation HITL Gate
            → Auto-Scaler  /  Fault Recovery → END
```

The pipeline is implemented in `core/graph.py` via
`build_post_deployment_graph()`. Two design constraints distinguish
it from the pre-deployment graph:

  - **Idempotency under repetition.** Each cycle starts from the
    LangGraph checkpointer's persisted state; metric streams,
    anomaly alerts, and execution results accumulate across cycles
    via `Annotated[..., operator.add]` reducers. Agents must
    therefore tolerate the cumulative state and contribute only
    *new* information per cycle (the Anomaly Detector's
    fingerprint cache, § 3.14, is the canonical example).

  - **Action-side branching.** A conditional edge after the
    Planner routes to the Remediation HITL Gate only when a plan
    exists; quiet cycles bypass the gate entirely. After the gate,
    a second conditional edge dispatches the action to either the
    Auto-Scaler (`horizontal_scale`, `vertical_scale`) or the
    Fault Recovery agent (`restart`, `rollback`, `config_change`).

The remainder of § 3.12–3.19 describes each agent in turn, then
§ 3.20 gives the cross-cycle persistence model.

## 3.13 KPI Monitor Agent

The KPI Monitor is the entry point of every post-deployment cycle.
It queries Prometheus for the four infrastructure metrics needed by
the downstream detectors and one SLA-feeder metric:

  - `cpu_utilization` (per-VNF, normalised against the resource
    request),
  - `memory_utilization` (per-VNF, normalised against the
    resource limit),
  - `network_rx_mbps` (per-VNF, derived from
    `container_network_receive_bytes_total`),
  - `pod_restarts` (cumulative count from
    `kube_pod_container_status_restarts_total`),
  - `availability_pct` (cluster-level, derived from the `up{}`
    series).

Each measurement is wrapped as a `MetricEvent` with a
`threshold_status` field — `normal` / `warning` / `critical` —
computed against the per-metric thresholds in
`config/sla_thresholds.yaml`. The threshold pre-classification
saves the Anomaly Detector a redundant comparison and gives the
Auto-Scaler a fast path to "obvious" load events.

The Monitor also produces a brief LLM-augmented narrative for the
operator console (the `messages` channel of the orchestrator
state); the narrative is best-effort and never participates in
downstream control flow.

## 3.14 Anomaly Detector Agent

The Anomaly Detector consumes the `MetricEvent` stream and applies
three layered statistical tests. Each test produces an
`AnomalyAlert` with `type`, `confidence`, `affected_metrics`,
`suggested_cause`, and `suggested_actions` fields.

**Detector 1 — Robust point outlier.** A median-and-MAD-based
z-score is computed against a 15-minute Prometheus history. The
implementation requires at least 30 samples (the Iglewicz &
Hoaglin minimum for stable MAD) before a verdict; below the
threshold the metric is silently passed through. Above z ≥ 3 the
alert is emitted at `medium`, `high`, or `critical` severity. A
secondary cap downgrades severity by one band when z exceeds 50 —
super-extreme z-scores are treated as evidence of a sparse or
misaligned baseline rather than as a real signal, an issue the
implementation surfaced repeatedly during early cluster bring-up
when Prometheus had not yet accumulated stable history (§ 5.7.5).

**Detector 2 — Sustained overload.** When the metric's
`threshold_status` is `critical` and at least 80% of the
five-minute history sits above the critical threshold, a
`sustained_overload` alert is emitted at fixed `0.90` confidence.
This detector targets steady-state pressure that the point
detector misses because the median of a saturated series is also
saturated.

**Detector 3 — Restart spike.** When the per-VNF restart counter
has incremented by 2 or more inside the last hour, an
`instability` alert is emitted; severity is `high` for delta < 5
and `critical` otherwise.

**Cross-cycle deduplication.** Every alert is fingerprinted as
`type | vnf | metric_name`. A module-level cache records the
last-fired UTC time per fingerprint. When the same fingerprint
re-emits within a 3-minute window the agent suppresses it. The
suppression cache is garbage-collected at twice the suppression
window so it cannot grow without bound across long monitoring
sessions. The thesis evaluates dedup explicitly: without it, F2
runs accumulated 18 alerts in the cumulative list across six
cycles; with it, only the *first* fingerprint per fault is
retained, giving the Planner a clean trigger and giving the
recovery heuristic (§ 3.20) a stable signal to track.

A best-effort LLM enhancement merges narrative `suggested_cause`
and `suggested_actions` fields after the deterministic detectors
have produced their alerts; the enhancement never modifies the
alert's `type`, `confidence`, `affected_metrics`, or `alert_id`,
so the LLM cannot silently change the meaning of an alert.

## 3.15 SLA Compliance Agent

The SLA Compliance agent maps live metrics onto the contractual
SLA rules declared in `config/sla_thresholds.yaml`. Each rule has
a measurement window, an operator (`<`, `<=`, `>`, `>=`), and a
threshold; the agent reports per-rule `status`
(`compliant` / `warning` / `breach` / `unknown`),
`compliance_pct` over the window, a `trend` derivative, and a
linear-extrapolation `time_to_breach_min`.

Two design choices in this agent are worth surfacing.

**Primary + fallback PromQL.** Four of six SLA rules require OAI
control-plane probes that are not exposed by the default OAI 5G
charts. The agent therefore carries two PromQL builders per
rule: a *primary* query against the control-plane metric, and a
*fallback* query against an infrastructure proxy (e.g.
`avg(up{pod=~"oai-amf.*"}) * 100` for AMF registration success).
When the primary returns no samples the agent automatically falls
back and records `data_source="fallback"` in the
`SLAStatus` so downstream agents and human reviewers can
distinguish the two regimes. Two rules
(`amf_registration_success_pct`, `upf_packet_loss_pct`) have
operationally meaningful fallbacks; the remaining two are reported
as `unknown` until OAI exposes the underlying metrics.

**Traffic-active gate.** The `upf_throughput_mbps` rule is gated
on a `_TRAFFIC_FLOOR_MBPS` constant set at 1.0 Mbps. Below the
floor the rule short-circuits to `unknown` with
`data_source="traffic_below_floor"` — control-plane chatter
alone does not constitute meaningful throughput, and reporting it
as a "breach" would feed the Planner a permanent triggering
signal that the workload cannot satisfy in the absence of UEs
(§ 5.7.5). The gate is intentionally simple: it does not attempt
to *predict* whether traffic will resume, only to decline a
verdict when there is nothing to evaluate.

The agent also issues an LLM call to annotate breach contexts
with trade-off analysis; the LLM output is purely advisory and is
never used for routing.

## 3.16 Planner / Reasoning Agent

The Planner is the central reasoner of the post-deployment loop.
It selects a *trigger* from the available signals, asks the LLM
for a remediation plan, validates the plan against an action
whitelist and the live topology, and returns a single
`RemediationPlan` (or `None` when the cycle is quiet).

**Trigger selection.** Triggers are ranked deterministically:
SLA breach > anomaly alert (high confidence ≥ 0.85) > SLA
warning > anomaly alert (medium confidence ≥ 0.70). The
highest-ranked trigger becomes the focus of the cycle; lower-
ranked triggers are listed in the prompt context but do not drive
action selection.

**LLM call.** The agent constructs a JSON-only prompt containing
the trigger payload, a *filtered* metric stream, the alert and
SLA lists, the available-actions whitelist, the last three
incidents from Redis (§ 3.20), and the last five executor results
from session state. The metric stream is filtered to VNFs
implicated by the trigger or by an alert/breach — typically
reducing a 40-metric prompt to under 10 metrics — so that the
prompt fits inside the 8 K-token context window of the open-source
reasoning models the experiment uses (Qwen3-Coder 30B, Qwen2.5
14B, etc.). The output is capped at 1500 tokens via an explicit
`max_tokens` override on `LLMCore.invoke`; this leaves at least
~6.5 K tokens of headroom for the input prompt across all
realistic cluster sizes.

**Validation.** The returned plan is checked against the
five-action whitelist (`horizontal_scale`, `vertical_scale`,
`restart`, `rollback`, `config_change`), each action's `target`
must name a real VNF in the topology, and the confidence is
clamped to $[0, 1]$. A target that does not match exactly but is
a strict prefix or suffix of a topology VNF name is *snapped* to
the nearest match — this absorbs a common LLM error mode
(`oai-amf-deployment` instead of `oai-amf`) without falling back
to the heuristic.

**Heuristic fallback.** When the LLM is unavailable, returns
unparseable text, or produces a plan that fails validation, the
agent synthesises a deterministic single-action plan from the
trigger payload (e.g. `horizontal_scale` for compute saturation,
`rollback` for restart spikes, `restart` for availability
breaches). The fallback plan carries fixed confidence 0.55 and is
always logged with `source="heuristic"` so downstream analysis
can distinguish LLM-driven from deterministic decisions. In the
Experiment 4 batch, the heuristic was used in roughly half of all
plans because the planner's LLM occasionally hits the 8 K-token
limit even after metric filtering — see § 5.7.3 for the impact on
results.

## 3.17 Remediation HITL Gate

The pre-deployment pipeline (§ 3.9) places HITL gates at the
Topology Review and Deployment Review boundaries. The post-
deployment pipeline carries an analogous third gate: the
**Remediation Review Gate**, which interposes between the Planner
and the executors when a plan is risky.

The gate is implemented as a conditional LangGraph node
(`remediation_review_gate` in `core/graph.py`). It marks the cycle
as requiring approval when *either*:

  - the plan contains a `rollback` or `config_change` action —
    these are the two action types whose blast radius extends
    beyond the immediate VNF (rollback reverses a Helm release;
    config_change touches a ConfigMap that may be shared), or
  - the planner's `confidence` is below 0.60 — a threshold that
    matches the heuristic-fallback fixed value, so any
    heuristic-derived plan triggers the gate.

When neither condition is met the gate auto-approves the plan;
the cycle proceeds to the executor without operator
intervention. The Experiment 4 harness sets
`HITL_REMEDIATION_AUTO_APPROVE=1` so the gate always
auto-approves — operator-in-the-loop evaluation is left to a
follow-up study — but the gate's classifier still runs and its
verdict is recorded in `intervention_log` for traceability.

## 3.18 Auto-Scaler Agent

The Auto-Scaler executes the two compute-side action types:
`horizontal_scale` and `vertical_scale`. For horizontal scaling
the agent calls
`K8sClient.scale_deployment(vnf_name, replicas)` with the
`delta`-corrected target replica count, bounded by
`_MIN_REPLICAS = 1` and `_MAX_REPLICAS = 10` (the bounds are
deliberately conservative to prevent runaway scaling under a
mis-classified trigger). After the patch the agent waits 15 s
for the deployment controller to settle and then verifies pod
readiness; the action result (`success` / `failed`) is appended
to `state["execution_results"]` and persisted via
`RedisClient.push_action`.

Vertical scaling is implemented as a `kubectl patch` against the
container's resource specification. The patch creates a
configuration drift versus the Helm release state — a known
limitation that the thesis flags in § 6.x as future work
(reconciliation via `helm upgrade --reuse-values`).

## 3.19 Fault Recovery Agent

The Fault Recovery agent executes the three "control-plane"
action types: `restart`, `rollback`, and `config_change`. Restart
issues a deployment-level rollout (`kubectl rollout restart`)
that drains and re-creates the pods one at a time, preserving
service continuity for VNFs with replicas > 1. Rollback calls
`HelmManager.rollback_release(name, revision-1)` to revert the
last upgrade; the operation requires the original release name to
match the deployment name, a constraint that holds for OAI charts
but is not architecturally guaranteed.

Configuration changes are the most invasive of the three actions:
the agent edits a single key inside a ConfigMap and triggers a
deployment rollout to pick it up. The same drift caveat that
applies to the Auto-Scaler's vertical-scaling patch applies here.

All Fault Recovery actions emit an `ExecutionResult` with
`status` and a free-text `details` field; the result is appended
to `state["execution_results"]` and pushed to the Redis
`actions` list with a 7-day TTL.

## 3.20 Cross-Cycle Persistence

Three slices of post-deployment state cross the boundary between
monitoring cycles:

  - **Anomaly fingerprint cache** — module-level state in
    `agents/anomaly_detector.py`, with a 3-minute window and a
    twice-window garbage-collection sweep. This is the primary
    source of cross-cycle deduplication.
  - **Redis incidents list** — written by the Planner at the end
    of every cycle (`alert`, `triggered_action`, `outcome` triples)
    and read at the start of every subsequent cycle. The agent
    reads at most three recent incidents to keep the prompt under
    the model's context budget. The mechanism is best-effort: a
    Redis outage degrades the agent gracefully to "no past
    context", with a log warning at `WARNING` level.
  - **Redis actions list** — written by the Auto-Scaler and Fault
    Recovery agents after each successful or failed execution. Used
    by the Planner as an action-history feed and by the experiment
    harness as a cross-check on `state["execution_results"]`.

The OrchestratorState TypedDict (§ 3.10) is extended in the
post-deployment phase with the following fields:

```python
class OrchestratorState(TypedDict, total=False):
    # ... pre-deployment fields as in § 3.10
    current_metrics:    list[MetricEvent]
    anomaly_alerts:     Annotated[list[AnomalyAlert], operator.add]
    sla_status:         list[SLAStatus]
    remediation_plan:   RemediationPlan | None
    execution_results:  Annotated[list[ExecutionResult], operator.add]
    requires_approval:  bool          # set by HITL gates
    user_approved:      bool | None   # set by CLI / experiment harness
```

Three of these fields use the `operator.add` reducer and are
therefore append-only across cycles; this is what gives the
runtime evaluation harness (§ 5.7) its visibility into the full
incident timeline. The `remediation_plan` field is overwritten
each cycle — the most recent plan is the only one a downstream
executor will act on — and the `requires_approval` /
`user_approved` flags are reset by the dedicated `user_decision_*`
nodes after each gate has been resolved.

---

[^wei2022]: Wei et al. (2022). "Chain-of-thought prompting elicits reasoning in large language models." *NeurIPS 2022*.
[^langgraph]: LangChain Inc. (2024). *LangGraph: Building Stateful, Multi-Actor Applications with LLMs.*
[^lewis2020]: Lewis et al. (2020). "Retrieval-augmented generation for knowledge-intensive NLP tasks." *NeurIPS 2020*.
[^iglewicz1993]: Iglewicz, B. & Hoaglin, D. C. *How to Detect and Handle Outliers.* ASQC Quality Press, 1993.
[^3gpp28554]: 3GPP TS 28.554 V17.5.0 (2022). *Management and orchestration; 5G end to end Key Performance Indicators.*
