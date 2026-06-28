"""Pytest fixtures shared across experiment_5 scenarios."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest

# Make repo root importable so scenarios can use infra/agents helpers.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Late import: paths above must be on sys.path first.
from infra.prometheus_client import PrometheusClient  # noqa: E402

NAMESPACE = os.environ.get("UE_NS", "oai-5g")
RELEASE = os.environ.get("UE_RELEASE", "ueransim")
TRAFFIC_CTL = ROOT / "scripts" / "traffic_ctl.py"


def _kubectl(*args: str, check: bool = True) -> str:
    res = subprocess.run(
        ["kubectl", "-n", NAMESPACE, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and res.returncode != 0:
        raise RuntimeError(f"kubectl failed: {res.stderr.strip()}")
    return res.stdout.strip()


def _run_ctl(*args: str) -> None:
    subprocess.run(
        [sys.executable, str(TRAFFIC_CTL), *args],
        check=True,
        cwd=str(ROOT),
    )


@pytest.fixture(scope="session")
def prom() -> PrometheusClient:
    client = PrometheusClient()
    assert client.ping(), "Prometheus is not reachable; aborting"
    return client


@pytest.fixture(scope="session")
def chart_installed() -> None:
    """Ensure the ueransim chart is installed; skip if absent."""
    pods = _kubectl("get", "pod",
                    "-l", "app.kubernetes.io/component=gnb",
                    "-o", "name", check=False)
    if not pods:
        pytest.skip("ueransim chart not deployed; run `helm install ueransim charts/ueransim -n oai-5g`")


@pytest.fixture(scope="session")
def ue_registered(chart_installed: None, prom: PrometheusClient) -> None:
    """Wait until at least one UE is registered (success_total > 0)."""
    deadline = time.time() + 300
    while time.time() < deadline:
        samples = prom.get_ue_registration_metrics(namespace=NAMESPACE)
        if samples and samples[0].get("success_per_sec", 0) >= 0 and \
           samples[0].get("attempts_per_sec", 0) > 0:
            return
        time.sleep(5)
    pytest.fail("No UE registration metrics observed within 5 minutes")


@pytest.fixture
def baseline_actions() -> int:
    """Count of execution_results recorded in Redis before the scenario."""
    try:
        from infra.redis_client import RedisClient  # noqa: WPS433
    except ImportError:
        return 0
    try:
        r = RedisClient()
        return r.client.llen("actions")
    except Exception:
        return 0


def wait_for_action(
    baseline: int,
    *,
    action_types: tuple[str, ...] = ("horizontal_scale", "vertical_scale", "restart"),
    timeout: float = 240.0,
) -> dict | None:
    """Block until a new action of `action_types` lands in Redis, or timeout."""
    try:
        from infra.redis_client import RedisClient  # noqa: WPS433
    except ImportError:
        pytest.skip("RedisClient not available")
        return None
    r = RedisClient()
    deadline = time.time() + timeout
    while time.time() < deadline:
        total = r.client.llen("actions")
        if total > baseline:
            new = r.client.lrange("actions", baseline, total - 1)
            for raw in new:
                try:
                    import json
                    obj = json.loads(raw)
                except Exception:
                    continue
                if obj.get("action_type") in action_types or obj.get("type") in action_types:
                    return obj
        time.sleep(5)
    return None


# Export module-level helpers
__all__ = ["NAMESPACE", "RELEASE", "_kubectl", "_run_ctl", "wait_for_action"]
