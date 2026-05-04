"""
Redis shared state store client.

Provides cross-session persistence for deployment history, incident records,
action logs, and topology snapshots. All methods are best-effort: every
call is wrapped in try/except so a Redis outage never disrupts agent logic.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import redis

from config.settings import REDIS_URL

logger = logging.getLogger(__name__)

_TTL_30D = 60 * 60 * 24 * 30   # 2 592 000 s
_TTL_7D  = 60 * 60 * 24 * 7    #   604 800 s
_MAX_LIST = 500                  # cap on list length


class RedisClient:
    """Cross-session shared state store backed by Redis.

    Key schema
    ----------
    deployments              LIST  TTL 30d  Written by Deployer
    topology:latest          STR   TTL 30d  Written by Deployer
    resource_allocation:latest STR TTL 30d  Written by Deployer
    incidents                LIST  TTL 30d  Written by Fault Recovery
    actions                  LIST  TTL 7d   Written by Auto-Scaler + Fault Recovery
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self.url = redis_url or REDIS_URL
        self._client = redis.from_url(self.url, decode_responses=True)
        logger.info("RedisClient initialised (url=%s)", self.url)

    # ------------------------------------------------------------------ #
    #  Connectivity                                                         #
    # ------------------------------------------------------------------ #

    def ping(self) -> bool:
        """Return True if Redis is reachable."""
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _lpush(self, key: str, entry: dict[str, Any], ttl: int) -> bool:
        """LPUSH + LTRIM + EXPIRE in a single pipeline round-trip."""
        try:
            pipe = self._client.pipeline()
            pipe.lpush(key, json.dumps(entry))
            pipe.ltrim(key, 0, _MAX_LIST - 1)
            pipe.expire(key, ttl)
            pipe.execute()
            return True
        except Exception as exc:
            logger.warning("RedisClient._lpush(%s) failed (non-critical): %s", key, exc)
            return False

    def _lrange(self, key: str, limit: int) -> list[dict[str, Any]]:
        """LRANGE 0..(limit-1) with JSON decoding."""
        try:
            return [json.loads(v) for v in self._client.lrange(key, 0, limit - 1)]
        except Exception as exc:
            logger.warning("RedisClient._lrange(%s) failed (non-critical): %s", key, exc)
            return []

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ #
    #  Deployments                                                          #
    # ------------------------------------------------------------------ #

    def push_deployment(self, result: dict[str, Any], topology_id: str = "") -> bool:
        """Log one VNF deployment result (from deployer_agent)."""
        entry = {**result, "topology_id": topology_id, "timestamp": self._now()}
        return self._lpush("deployments", entry, _TTL_30D)

    def get_deployments(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return up to *limit* most-recent deployment records (newest first)."""
        return self._lrange("deployments", limit)

    # ------------------------------------------------------------------ #
    #  Topology / resource allocation snapshots                            #
    # ------------------------------------------------------------------ #

    def set_topology(self, topology: dict[str, Any]) -> bool:
        """Persist the latest topology blueprint."""
        try:
            self._client.set("topology:latest", json.dumps(topology), ex=_TTL_30D)
            return True
        except Exception as exc:
            logger.warning("RedisClient.set_topology failed (non-critical): %s", exc)
            return False

    def get_topology(self) -> dict[str, Any] | None:
        """Return the last persisted topology blueprint, or None."""
        try:
            raw = self._client.get("topology:latest")
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.warning("RedisClient.get_topology failed (non-critical): %s", exc)
            return None

    def set_resource_allocation(self, allocation: dict[str, Any]) -> bool:
        """Persist the latest resource allocation."""
        try:
            self._client.set("resource_allocation:latest", json.dumps(allocation), ex=_TTL_30D)
            return True
        except Exception as exc:
            logger.warning("RedisClient.set_resource_allocation failed (non-critical): %s", exc)
            return False

    def get_resource_allocation(self) -> dict[str, Any] | None:
        """Return the last persisted resource allocation, or None."""
        try:
            raw = self._client.get("resource_allocation:latest")
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.warning("RedisClient.get_resource_allocation failed (non-critical): %s", exc)
            return None

    # ------------------------------------------------------------------ #
    #  Incidents                                                            #
    # ------------------------------------------------------------------ #

    def push_incident(
        self,
        alert: dict[str, Any],
        triggered_action: str,
        outcome: str,
    ) -> bool:
        """Log a resolved incident (anomaly alert + action taken + outcome)."""
        entry = {
            "alert": alert,
            "triggered_action": triggered_action,
            "outcome": outcome,
            "timestamp": self._now(),
        }
        return self._lpush("incidents", entry, _TTL_30D)

    def get_incidents(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return up to *limit* most-recent incident records (newest first)."""
        return self._lrange("incidents", limit)

    # ------------------------------------------------------------------ #
    #  Actions (ExecutionResult log)                                        #
    # ------------------------------------------------------------------ #

    def push_action(self, result: dict[str, Any]) -> bool:
        """Log one ExecutionResult (from auto_scaler or fault_recovery)."""
        return self._lpush("actions", result, _TTL_7D)

    def get_actions(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return up to *limit* most-recent action records (newest first)."""
        return self._lrange("actions", limit)
