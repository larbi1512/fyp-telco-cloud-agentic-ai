"""
Scenario C — Registration flood → AMF scaling.

Triggers a burst of UE re-registrations by deleting UE pods, forcing the
StatefulSet controller to recreate them. The resulting registration
spike stresses the AMF; if the success ratio drops below 99% the SLA
Compliance agent emits a Rule 4 breach.
"""

from __future__ import annotations

import time

import pytest

from .conftest import NAMESPACE, _run_ctl, wait_for_action


@pytest.mark.timeout(600)
def test_registration_flood_triggers_amf_scaling(prom, ue_registered, baseline_actions):
    _run_ctl("scale-ues", "--count", "100")
    time.sleep(30)
    _run_ctl("stress", "--kind", "reg-flood", "--rate", "50", "--burst", "200")

    # Allow re-registration to ramp and Prometheus to scrape.
    time.sleep(120)

    samples = prom.get_ue_registration_metrics(namespace=NAMESPACE)
    assert samples, "no AMF registration series; exporter healthy?"
    attempts = samples[0].get("attempts_per_sec", 0.0)
    assert attempts > 0, "expected non-zero registration attempt rate"

    action = wait_for_action(baseline_actions, timeout=360)
    assert action is not None, "framework did not respond to registration flood"
    target = (action.get("target") or action.get("vnf") or "").lower()
    assert "amf" in target, f"action did not target AMF: {action}"
