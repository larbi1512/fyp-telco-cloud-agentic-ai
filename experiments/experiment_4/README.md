# Experiment 4 — Closed-Loop Runtime Evaluation

## Objective
Validate the post-deployment autonomous remediation pipeline end-to-end on
the live OAI 5G core deployment. Answer: when a fault occurs, does the
multi-agent system detect it, reason about it, and remediate it — and how
well, compared to a non-agentic baseline?

## Design Summary

| Scenario | Fault | Target | Expected Response |
|---|---|---|---|
| **F1** | CPU spike (stress-ng sidecar) | `oai-amf` | Anomaly detected → Auto-Scaler scales replicas |
| **F2** | Pod crash (`kubectl delete pod`) | `oai-upf` | Fault Recovery → pod restart / rollback |
| **F3** | Traffic surge (iperf3, 50 streams) | `oai-upf` service | SLA breach → scale or reroute |

**Systems-under-test:**
- **MAS** — full pipeline, gate auto-approves so the run is autonomous.
- **B_static** — detector pipeline still runs, but the remediation path is
  suppressed (`MAS_BYPASS_REMEDIATION=1`); the system relies on Kubernetes
  alone (HPA / restart policies) to recover.

**Metrics (per run):**
- `detection_latency_s` — fault inject → first anomaly alert
- `plan_latency_s` — first alert → first plan produced by Planner
- `action_latency_s` — first plan → first executor action recorded
- `recovery_time_s` — fault inject → 2 consecutive clean cycles after action
- `remediation_success` — boolean: did the run reach a clean state?
- `false_positives_pre_fault` — alerts fired in the baseline window
- `human_interventions` — count from `intervention_log` (gate triggers)

## Prerequisites
- Live K8s cluster reachable via `kubectl` with the OAI 5G core deployed in
  the target namespace (default `oai-5g`).
- Prometheus + Grafana scraping the cluster.
- Redis running with the topology snapshot from a recent pre-deployment
  run (see `python main.py --mode deploy`).
- LLM backend (Ollama / vLLM) reachable; the Planner uses it on each cycle.

## Running

### One scenario × one rep (smoke test)
```bash
cd ~/fyp && source venv/bin/activate
python experiments/experiment_4/run_scenario.py \
  --scenario F1 --rep 1 --system MAS \
  --baseline-cycles 2 --recovery-cycles 8 --cycle-interval 15
```

### Full smoke pass (used as Phase 1 sanity check)
```bash
python experiments/experiment_4/run_batch.py --smoke
```
Runs F1×2, F2×2, F3×1 for MAS only (~45 min).

### Full run for thesis (15 MAS + 10 baseline)
```bash
python experiments/experiment_4/run_batch.py --full
```
3 scenarios × 5 reps × 2 systems minus the duplicate F2 baseline = ~25 runs,
~5–6 hours wall time.

### Aggregate + plot
```bash
python experiments/experiment_4/analyze_results.py
```
Writes `results/runs.csv` plus `figures/*.png`.

## Output Structure
```
experiments/experiment_4/
├── inject_fault.py        # Fault injection CLI
├── run_scenario.py        # One full scenario lifecycle
├── run_batch.py           # Loop driver for smoke / full passes
├── analyze_results.py     # CSV + plots
├── results/
│   ├── runs.csv           # One row per run (all scenarios × systems × reps)
│   └── runs/*.json        # Per-run artifacts (full cycle telemetry)
└── figures/               # Bar / heatmap / box plots
```

## Known Limitations
- **B_static is not literature-anchored** the way Experiment 2's B1/B2 were;
  it is a within-system ablation (same code, planner output suppressed).
  The thesis frames it as an ablation, not as a fully separate baseline.
- **F1 fault relies on node-affinity scheduling** — if the stress-ng pod
  lands on a different node than the AMF, contention is weaker. The runner
  reads the AMF's nodeName and pins the stressor; works on most clusters
  but may need adjustment on heavily-tainted nodes.
- **Recovery detection is heuristic** — 2 consecutive clean cycles after an
  action. This may classify partial recoveries as success; documented as a
  threat to validity in Chapter 6.
