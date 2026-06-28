# Experiment 5 — UE traffic-driven SLA validation

Three pytest scenarios that exercise the framework's closed loop under
real UERANSIM traffic. Each one (a) injects a stress vector, (b) waits
for a Prometheus signal to materialize, and (c) asserts that the
framework writes a remediation action to Redis.

## Prerequisites

```bash
# 1. OAI 5G Core is running in `oai-5g`
kubectl get pod -n oai-5g | grep -E 'amf|smf|upf'

# 2. UERANSIM chart is installed (with the synthetic exporter)
helm install ueransim ../../charts/ueransim -n oai-5g

# 3. The monitor loop is running
cd ../.. && python main.py --mode monitor --interval 30 &
```

## Running

```bash
cd ../..
venv/bin/python -m pytest -v experiments/experiment_5_ue_traffic/
# or one at a time
venv/bin/python -m pytest -v experiments/experiment_5_ue_traffic/scenario_a_urllc_latency.py
```

All scenarios use the same fixtures from [conftest.py](conftest.py). If
the chart is not installed, scenarios are `skip`ped rather than failed.

## Configuration knobs

| Env var | Default | Meaning |
|---|---|---|
| `UE_NS` | `oai-5g` | Kubernetes namespace for the chart |
| `UE_RELEASE` | `ueransim` | Helm release name |
| `PROMETHEUS_URL` | `http://localhost:9090` | Read from `config.settings` |
