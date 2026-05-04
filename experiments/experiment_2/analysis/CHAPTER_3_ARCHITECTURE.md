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
loads the `results/full_bmin/runs.csv` artefact from the 900-run
experiment and asserts that the MAS attains `intent_to_deploy_accuracy ≥ 0.60`
and `resource_accuracy ≥ 0.70`, providing a reproducibility anchor for
reviewers.

---

[^wei2022]: Wei et al. (2022). "Chain-of-thought prompting elicits reasoning in large language models." *NeurIPS 2022*.
[^langgraph]: LangChain Inc. (2024). *LangGraph: Building Stateful, Multi-Actor Applications with LLMs.*
[^lewis2020]: Lewis et al. (2020). "Retrieval-augmented generation for knowledge-intensive NLP tasks." *NeurIPS 2020*.
[^3gpp28554]: 3GPP TS 28.554 V17.5.0 (2022). *Management and orchestration; 5G end to end Key Performance Indicators.*
