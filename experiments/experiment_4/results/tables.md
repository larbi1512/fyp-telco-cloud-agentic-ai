## Plan source (LLM vs heuristic)

| Scenario | System | LLM | Heuristic | Total | LLM share |
|---|---|---:|---:|---:|---:|
| F1 | MAS | 8 | 104 | 112 | 7.1% |
| F2 | MAS | 18 | 102 | 120 | 15.0% |
| F3 | MAS | 29 | 70 | 99 | 29.3% |
| **All** | **All** | **55** | **276** | **331** | **16.6%** |

## Detection latency: first-alert vs trigger-fingerprint

| Scenario | System | First-alert latency (s) | Fingerprint latency (s) | Δ |
|---|---|---|---|---:|
| F1 | B_static | 20.1 ± 29.8 | 20.1 ± 29.8 | +0.0 |
| F1 | MAS | 10.2 ± 23.3 | 10.2 ± 23.3 | +0.0 |
| F2 | B_static | 10.1 ± 9.2 | 10.1 ± 9.2 | +0.0 |
| F2 | MAS | 19.6 ± 32.3 | 19.6 ± 32.3 | +0.0 |
| F3 | B_static | 13.6 ± 18.5 | 13.6 ± 18.5 | +0.0 |
| F3 | MAS | 12.9 ± 27.1 | 12.9 ± 27.1 | +0.0 |
