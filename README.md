# 5G Network Orchestration — Multi-Agent System

A LangGraph-based multi-agent system (MAS) that automates the full lifecycle of 5G Core Network deployment and runtime monitoring on a remote Kubernetes cluster. Agents collaborate through a shared state machine to plan, configure, validate, deploy, and continuously monitor an OpenAirInterface (OAI) 5G Core.

---

## Architecture

The system is split into two sequential pipelines, both wired as LangGraph state machines:

```
╔══════════════════════════════════════════════════════════════╗
║              PRE-DEPLOYMENT PIPELINE                         ║
║                                                              ║
║  User Intent                                                 ║
║      │                                                       ║
║      ▼                                                       ║
║  Network Planner  ──►  Resource Allocator  ──►               ║
║  (topology)            (CPU/RAM per VNF)                     ║
║                                                              ║
║      ──►  VNF Configurator  ──►  Policy Validator            ║
║           (Helm values)          (rules + dry-run)           ║
║                                                              ║
║  [HITL: Topology Approval]   [HITL: Deployment Approval]     ║
║                                                              ║
║      ──►  Deployer  (Helm install/upgrade on remote K8s)     ║
╚══════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════╗
║              POST-DEPLOYMENT LOOP  (continuous)              ║
║                                                              ║
║  KPI Monitor  ──►  Anomaly Detector  ──►  SLA Compliance     ║
║  (Prometheus)       (z-score, stats)       (rule eval)       ║
║                                                              ║
║      ──►  Planner Reasoning  ──►  Auto-Scaler                ║
║           (LLM + Redis history)    (kubectl scale)           ║
║                                                              ║
║      ──►  Fault Recovery                                     ║
║           (restart, rollback, config patch)                  ║
╚══════════════════════════════════════════════════════════════╝
```

**Human-in-the-Loop (HITL)** checkpoints pause the pre-deployment pipeline at topology review and deployment approval. The post-deployment loop runs autonomously.

**Shared state** is managed in two layers:
- LangGraph `MemorySaver` — in-session state across graph nodes
- Redis — cross-session persistence (topology, deployment history, incidents, actions)

---

## Infrastructure Requirements

| Component | Location | Details |
|---|---|---|
| Agentic VM (this machine) | `gracehopper4-oai` | Python 3.10, Ollama or vLLM, LangGraph agents |
| K8s VM (remote) | Separate VM | Kubernetes, OAI 5G Core VNFs, UERANSIM, Prometheus, Grafana |
| LLM | Local Ollama or remote vLLM | `gpt-oss:120b` via Ollama, or any OpenAI-compatible endpoint |
| Redis | Local | `redis-server` on `localhost:6379` |

---

## Setup

### 1. Clone and create virtual environment

```bash
git clone <repo-url>
cd fyp
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

Copy and edit `.env`:

```bash
cp .env.example .env   # or edit .env directly
```

Key variables:

```ini
# LLM backend: "ollama" (local) or "vllm" (OpenAI-compatible endpoint)
LLM_BACKEND=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gpt-oss:120b

# Remote Kubernetes cluster
KUBECONFIG_PATH=~/.kube/config
K8S_NAMESPACE=oai-5g

# Remote Prometheus
PROMETHEUS_URL=http://<k8s-vm-ip>:<nodeport>

# Redis (local)
REDIS_URL=redis://localhost:6379
```

### 3. Start Redis

```bash
redis-server --daemonize yes --logfile /tmp/redis.log
redis-cli ping   # should return PONG
```

### 4. Verify connectivity

```bash
# LLM
python test_llm_conn.py

# Prometheus + K8s
python test_conn.py
```

---

## Usage

### Pre-deployment pipeline (interactive)

```bash
python main.py
# or explicitly:
python main.py --mode deploy
```

You will be prompted for a natural-language deployment intent, e.g.:

```
Deploy a 5G core for 500 UEs with 2 Gbps throughput and eMBB slicing.
```

The pipeline runs through all agents and pauses at two HITL checkpoints:

1. **Topology approval** — review the generated VNF topology
2. **Deployment approval** — review resource allocation, Helm configs, and validation report

On approval, the Deployer agent runs `helm install/upgrade` against the remote cluster.

### Post-deployment monitoring loop

```bash
python main.py --mode monitor              # default 30s interval
python main.py --mode monitor --interval 60
```

Each cycle:
1. Queries Prometheus for live KPIs (CPU, memory, network, pod restarts)
2. Detects anomalies (statistical z-score + sustained threshold breach)
3. Evaluates SLA compliance rules
4. Generates a remediation plan if needed (LLM + Redis cross-session history)
5. Executes auto-scaling or fault recovery actions on the cluster
6. Prints a Rich summary table; repeats after `--interval` seconds

Stop with `Ctrl+C`.

---

## Project Structure

```
fyp/
├── main.py                   # Entry point (--mode deploy | monitor)
├── agents/                   # All 11 agent implementations
│   ├── network_planner.py
│   ├── resource_allocator.py
│   ├── vnf_configurator.py
│   ├── policy_validator.py
│   ├── deployer.py
│   ├── kpi_monitor.py
│   ├── anomaly_detector.py
│   ├── sla_compliance.py
│   ├── planner_reasoning.py
│   ├── auto_scaler.py
│   └── fault_recovery.py
├── core/
│   ├── graph.py              # LangGraph graph builders (pre + post)
│   ├── state.py              # OrchestratorState TypedDict
│   ├── llm_core.py           # Central LLM wrapper (Ollama / vLLM)
│   ├── logger.py             # Structured file + console logging
│   └── prompts/              # Per-agent YAML prompt templates (10 files)
├── config/
│   ├── settings.py           # All env-var config
│   ├── vnf_resource_profiles.yaml
│   └── sla_thresholds.yaml
├── infra/
│   ├── prometheus_client.py  # Prometheus HTTP API wrapper
│   ├── helm_manager.py       # Helm CLI wrapper
│   ├── k8s_client.py         # kubernetes-python client wrapper
│   ├── grafana_client.py     # Grafana HTTP API wrapper
│   └── redis_client.py       # Redis shared state store
├── interface/
│   └── cli.py                # Rich terminal UI (tables, panels, HITL prompts)
├── experiments/              # Evaluation scripts and baselines
│   ├── experiment_1/         # Intent translation accuracy
│   ├── experiment_2/         # Comparative: MAS vs baselines
│   └── baselines/            # B1 manual, B2 OSM-stub, B3 static HPA, B4 single-LLM
├── docs/                     # Phase design documents
├── charts/                   # Helm chart overrides for OAI VNFs
├── tests/                    # 225 unit + integration tests
└── logs/                     # Per-run log files (auto-created)
```

---

## Tests

```bash
# Full unit test suite (no live infra needed — all external deps mocked)
python -m pytest tests/ -q --ignore=tests/test_graph.py --ignore=tests/test_pre_deployment.py

# Live integration tests (require LLM + K8s + Prometheus)
python tests/test_graph.py
python tests/test_pre_deployment.py
```

Current status: **225 unit tests passing**.

---

## Agent Responsibilities

| Agent | Phase | Role |
|---|---|---|
| **Network Planner** | Pre-deploy | Parses user intent → topology blueprint (VNFs, slices, PLMN) |
| **Resource Allocator** | Pre-deploy | Maps topology to CPU/RAM requests and limits per VNF |
| **VNF Configurator** | Pre-deploy | Generates Helm `values.yaml` for each VNF |
| **Policy Validator** | Pre-deploy | Validates configs against rules + Kubernetes dry-run |
| **Deployer** | Pre-deploy | `helm install/upgrade` on remote cluster; persists topology to Redis |
| **KPI Monitor** | Post-deploy | Queries Prometheus; emits labelled `MetricEvent` stream |
| **Anomaly Detector** | Post-deploy | Robust z-score + sustained threshold + restart spike detectors |
| **SLA Compliance** | Post-deploy | Evaluates SLA rules (latency, throughput, availability) |
| **Planner Reasoning** | Post-deploy | LLM generates remediation plan; reads cross-session incidents from Redis |
| **Auto-Scaler** | Post-deploy | Executes `horizontal_scale` and `vertical_scale` actions via kubectl |
| **Fault Recovery** | Post-deploy | Executes `restart`, `rollback`, and `config_change` actions |

---

## LLM Configuration

Both Ollama (local) and vLLM (OpenAI-compatible API) are supported. Set `LLM_BACKEND` in `.env`:

| Backend | Variable | Example |
|---|---|---|
| Ollama | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | `http://localhost:11434`, `gpt-oss:120b` |
| vLLM | `VLLM_BASE_URL`, `VLLM_MODEL`, `VLLM_API_KEY` | `http://localhost:8123/v1`, `Qwen3-Coder-30B` |

The system degrades gracefully when the LLM is unreachable — all agents fall back to rule-based / statistical logic.

---

## Redis State Store

Redis is a best-effort side-channel, never on the critical path. All writes are wrapped in `try/except`; if Redis is down the system runs identically to a fresh session.

| Key | Type | Writers | Readers |
|---|---|---|---|
| `topology:latest` | String | Deployer | Monitoring bootstrap |
| `resource_allocation:latest` | String | Deployer | Monitoring bootstrap |
| `deployments` | List (capped 500) | Deployer | — |
| `incidents` | List (capped 500) | Fault Recovery | Planner Reasoning |
| `actions` | List (capped 500) | Auto-Scaler, Fault Recovery | Planner Reasoning |

Inspect live state:

```bash
redis-cli get topology:latest | python3 -m json.tool
redis-cli llen incidents
redis-cli lindex deployments 0 | python3 -m json.tool
```

---

## Logs

Each run writes a timestamped log file to `logs/run_YYYYMMDD_HHMMSS.log`:

```bash
tail -f logs/$(ls logs/ | tail -1)
```
