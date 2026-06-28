"""
Scenario B — eMBB throughput saturation → HPA-style scaling.

Drives `upf_throughput_mbps` past the saturation point relative to the
UPF's allocated CPU. Expects SLA Rule 2 to trip and the framework to
respond with a horizontal_scale action on `oai-upf`.
"""

from __future__ import annotations

import time

import pytest

from .conftest import NAMESPACE, _run_ctl, wait_for_action


@pytest.mark.timeout(900)
def test_embb_saturation_triggers_scaling(prom, ue_registered, baseline_actions):
    _run_ctl("scale-ues", "--count", "20")
    _run_ctl("profile", "--set", "embb")
    _run_ctl("stress",
             "--kind", "throughput",
             "--target-mbps", "1500",
             "--duration", "600")

    # Wait for traffic to ramp and Prometheus to scrape several samples.
    time.sleep(120)

    # Rule 2 uses container_network_transmit_bytes_total on oai-upf-* pods.
    samples = prom.instant_query(
        f'sum(rate(container_network_transmit_bytes_total'
        f'{{namespace="{NAMESPACE}", pod=~"oai-upf.*"}}[5m])) * 8 / 1e6'
    )
    assert samples, "no UPF throughput samples"
    mbps = float(samples[0].get("value", [None, "0"])[1])
    assert mbps > 1.0, f"throughput too low ({mbps} Mbps); iperf3 not flowing?"

    action = wait_for_action(baseline_actions, timeout=480)
    assert action is not None, "no scaling action observed"
    target = action.get("target") or action.get("vnf") or ""
    assert "upf" in target.lower(), f"action did not target UPF: {action}"
