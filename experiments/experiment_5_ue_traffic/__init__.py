"""Experiment 5 — UE traffic-driven validation scenarios.

Three reproducible scenarios that exercise the full closed loop
(KPI Monitor → Anomaly Detector → SLA Compliance → Planner → Scaler /
Fault Recovery) under realistic UERANSIM-driven traffic:

  - scenario_a_urllc_latency:   URLLC + gNB jitter → Rule 1 breach → scale
  - scenario_b_embb_saturation: eMBB throughput floor → Rule 2 breach → scale
  - scenario_c_reg_flood:       Registration burst → Rule 4 breach → AMF scale

Each scenario is a pytest entry point that:
  1. Asserts the chart is deployed and UEs are registered.
  2. Captures Redis incident/action counts as a baseline.
  3. Injects the stress via scripts/traffic_ctl.py.
  4. Polls Prometheus + Redis until the framework reacts, or times out.
  5. Asserts the expected remediation action was logged.
"""
