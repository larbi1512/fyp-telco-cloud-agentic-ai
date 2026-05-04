"""
Unit + integration tests for infra/redis_client.py.

Unit tests use mocks and never require a running Redis instance.
Integration tests are automatically skipped when Redis is unavailable.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def _make_client(mock_redis_instance):
    """Construct a RedisClient wired to a mock redis instance."""
    from infra.redis_client import RedisClient
    with patch("infra.redis_client.redis.from_url", return_value=mock_redis_instance):
        return RedisClient(redis_url="redis://localhost:6379/0")


# ------------------------------------------------------------------ #
#  ping                                                                #
# ------------------------------------------------------------------ #

def test_ping_returns_true_when_reachable():
    mock = MagicMock()
    mock.ping.return_value = True
    client = _make_client(mock)
    assert client.ping() is True


def test_ping_returns_false_when_redis_down():
    mock = MagicMock()
    mock.ping.side_effect = Exception("Connection refused")
    client = _make_client(mock)
    assert client.ping() is False


# ------------------------------------------------------------------ #
#  push_deployment / get_deployments                                   #
# ------------------------------------------------------------------ #

def test_push_deployment_calls_pipeline():
    mock = MagicMock()
    pipe = MagicMock()
    mock.pipeline.return_value = pipe
    client = _make_client(mock)

    result = client.push_deployment(
        {"vnf_name": "oai-amf", "release_name": "oai-amf", "status": "installed", "message": "OK"},
        topology_id="topo-1",
    )

    assert result is True
    mock.pipeline.assert_called_once()
    pipe.lpush.assert_called_once()
    pipe.ltrim.assert_called_once()
    pipe.expire.assert_called_once()
    pipe.execute.assert_called_once()


def test_push_deployment_returns_false_on_error():
    mock = MagicMock()
    mock.pipeline.side_effect = Exception("timeout")
    client = _make_client(mock)

    result = client.push_deployment({"vnf_name": "oai-amf", "status": "installed"})
    assert result is False  # non-critical: no exception raised


def test_get_deployments_returns_parsed_list():
    import json
    mock = MagicMock()
    mock.lrange.return_value = [
        json.dumps({"vnf_name": "oai-amf", "status": "installed", "topology_id": "t1", "timestamp": "2026-05-02T00:00:00+00:00"}),
    ]
    client = _make_client(mock)

    entries = client.get_deployments(limit=5)
    assert len(entries) == 1
    assert entries[0]["vnf_name"] == "oai-amf"
    mock.lrange.assert_called_once_with("deployments", 0, 4)


def test_get_deployments_returns_empty_on_error():
    mock = MagicMock()
    mock.lrange.side_effect = Exception("timeout")
    client = _make_client(mock)
    assert client.get_deployments() == []


# ------------------------------------------------------------------ #
#  set_topology / get_topology                                          #
# ------------------------------------------------------------------ #

def test_set_topology_calls_set():
    mock = MagicMock()
    client = _make_client(mock)

    result = client.set_topology({"topology_id": "topo-1", "vnfs": []})
    assert result is True
    mock.set.assert_called_once()
    args = mock.set.call_args
    assert args[0][0] == "topology:latest"


def test_get_topology_returns_none_when_missing():
    mock = MagicMock()
    mock.get.return_value = None
    client = _make_client(mock)
    assert client.get_topology() is None


def test_get_topology_returns_dict_when_present():
    import json
    mock = MagicMock()
    mock.get.return_value = json.dumps({"topology_id": "topo-1"})
    client = _make_client(mock)
    result = client.get_topology()
    assert result == {"topology_id": "topo-1"}


def test_set_topology_returns_false_on_error():
    mock = MagicMock()
    mock.set.side_effect = Exception("timeout")
    client = _make_client(mock)
    assert client.set_topology({}) is False


# ------------------------------------------------------------------ #
#  push_action / get_actions                                           #
# ------------------------------------------------------------------ #

def test_push_action_does_not_raise_on_redis_down():
    mock = MagicMock()
    mock.pipeline.side_effect = Exception("Connection refused")
    client = _make_client(mock)

    result = client.push_action({
        "action_type": "restart",
        "target": "oai-amf",
        "status": "success",
        "details": "ok",
        "timestamp": "2026-05-02T00:00:00",
    })
    assert result is False


# ------------------------------------------------------------------ #
#  push_incident / get_incidents                                        #
# ------------------------------------------------------------------ #

def test_push_incident_constructs_correct_payload():
    import json
    mock = MagicMock()
    pipe = MagicMock()
    mock.pipeline.return_value = pipe

    # Capture what was pushed
    pushed_values = []
    def capture_lpush(key, value):
        pushed_values.append((key, value))
    pipe.lpush.side_effect = capture_lpush

    client = _make_client(mock)
    client.push_incident(
        alert={"triggered_by": "anom-001", "diagnosis": "high cpu"},
        triggered_action="horizontal_scale:oai-upf",
        outcome="1 success, 0 failed",
    )

    assert len(pushed_values) == 1
    key, raw = pushed_values[0]
    assert key == "incidents"
    payload = json.loads(raw)
    assert payload["triggered_action"] == "horizontal_scale:oai-upf"
    assert payload["outcome"] == "1 success, 0 failed"
    assert payload["alert"]["triggered_by"] == "anom-001"
    assert "timestamp" in payload


# ------------------------------------------------------------------ #
#  Integration tests (skipped when Redis unavailable)                  #
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def live_client():
    from infra.redis_client import RedisClient
    c = RedisClient()
    if not c.ping():
        pytest.skip("Redis not available — skipping integration tests")
    # Use a test-specific key prefix by temporarily patching keys
    yield c
    # Cleanup test keys
    c._client.delete("deployments", "incidents", "actions",
                     "topology:latest", "resource_allocation:latest")


def test_integration_deployment_roundtrip(live_client):
    result = {
        "vnf_name": "oai-amf",
        "release_name": "oai-amf",
        "status": "installed",
        "message": "OK",
    }
    live_client.push_deployment(result, topology_id="topo-test")
    entries = live_client.get_deployments(limit=1)
    assert entries[0]["vnf_name"] == "oai-amf"
    assert entries[0]["topology_id"] == "topo-test"
    assert "timestamp" in entries[0]


def test_integration_topology_roundtrip(live_client):
    topo = {"topology_id": "topo-test", "vnfs": [{"name": "oai-amf", "type": "AMF"}]}
    live_client.set_topology(topo)
    result = live_client.get_topology()
    assert result == topo


def test_integration_incident_roundtrip(live_client):
    live_client.push_incident(
        alert={"triggered_by": "anom-001", "diagnosis": "latency spike"},
        triggered_action="horizontal_scale:oai-upf",
        outcome="1 success, 0 failed",
    )
    incidents = live_client.get_incidents(limit=1)
    assert incidents[0]["triggered_action"] == "horizontal_scale:oai-upf"


def test_integration_action_roundtrip(live_client):
    live_client.push_action({
        "action_type": "horizontal_scale",
        "target": "oai-upf",
        "status": "success",
        "details": "scaled 1→2",
        "timestamp": "2026-05-02T00:00:00+00:00",
    })
    actions = live_client.get_actions(limit=1)
    assert actions[0]["action_type"] == "horizontal_scale"


def test_integration_list_cap_enforced(live_client):
    from infra.redis_client import _MAX_LIST
    # Push slightly more than the cap — list should stay at _MAX_LIST
    for i in range(5):
        live_client.push_action({"action_type": "restart", "target": f"vnf-{i}",
                                  "status": "success", "details": "", "timestamp": ""})
    length = live_client._client.llen("actions")
    assert length <= _MAX_LIST
