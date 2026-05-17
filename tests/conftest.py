"""Shared pytest fixtures for the FYP test suite."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_anomaly_dedup_cache():
    """
    The anomaly detector keeps a module-level alert-suppression cache that
    persists across test cases in the same process. Reset it before every
    test so dedup behaviour is deterministic.
    """
    from agents import anomaly_detector
    anomaly_detector._seen_alerts.clear()
    yield
    anomaly_detector._seen_alerts.clear()
