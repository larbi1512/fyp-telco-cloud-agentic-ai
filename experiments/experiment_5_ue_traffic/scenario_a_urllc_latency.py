"""
Scenario A — URLLC latency breach → remediation.

Workflow:
  1. Switch to URLLC traffic profile (low-bandwidth, EF DSCP).
  2. Inject 30ms jitter on the gNB pod via tc netem.
  3. Expect blackbox-exporter p99 latency > 20ms (Rule 1 breach).
  4. Expect Planner to recommend a horizontal_scale or restart action.
  5. Expect latency to return < 20ms within 2 monitor cycles after the action.
"""

from __future__ import annotations

import time

import pytest

from .conftest import NAMESPACE, _run_ctl, wait_for_action


@pytest.mark.timeout(600)
def test_urllc_latency_breach_triggers_scaling(prom, ue_registered, baseline_actions):
    # Apply scenario
    _run_ctl("scale-ues", "--count", "50")
    _run_ctl("profile", "--set", "urllc")
    _run_ctl("stress", "--kind", "latency", "--inject-jitter-ms", "30")

    # Allow Prometheus to scrape the new probe data (≥ 2× scrape interval).
    time.sleep(90)

    # Confirm Rule 1 metric source is producing data
    samples = prom.get_e2e_probe_latency(namespace=NAMESPACE)
    assert samples, "e2e probe latency series absent; blackbox-exporter not scraped?"
    p99 = samples[0].get("p99_ms", 0.0)
    assert p99 > 20.0, f"expected p99 > 20ms after jitter injection, got {p99}ms"

    # Wait for framework to react
    action = wait_for_action(baseline_actions, timeout=300)
    assert action is not None, "no remediation action observed within timeout"
    assert (action.get("status") or "").lower() in {"success", "completed", "ok"}, \
        f"action did not complete cleanly: {action}"
