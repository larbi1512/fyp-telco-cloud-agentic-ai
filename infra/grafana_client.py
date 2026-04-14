"""
Grafana REST API client for dashboard queries and annotations.

Allows the agentic system to post annotations to Grafana dashboards
whenever an automated orchestration action occurs.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from config.settings import GRAFANA_URL, GRAFANA_API_KEY

logger = logging.getLogger(__name__)


class GrafanaClient:
    """Minimal Grafana HTTP API wrapper."""

    def __init__(
        self,
        grafana_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.url = (grafana_url or GRAFANA_URL).rstrip("/")
        self.api_key = api_key or GRAFANA_API_KEY
        self._session = requests.Session()
        if self.api_key:
            self._session.headers["Authorization"] = f"Bearer {self.api_key}"
        self._session.headers["Content-Type"] = "application/json"
        logger.info("GrafanaClient initialised (url=%s)", self.url)

    # ------------------------------------------------------------------ #
    #  Internal                                                             #
    # ------------------------------------------------------------------ #

    def _get(self, path: str, **kwargs: Any) -> Any:
        resp = self._session.get(f"{self.url}{path}", **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        resp = self._session.post(f"{self.url}{path}", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------ #
    #  Connectivity                                                         #
    # ------------------------------------------------------------------ #

    def ping(self) -> bool:
        """Return True if Grafana is reachable."""
        try:
            resp = self._session.get(f"{self.url}/api/health", timeout=5)
            return resp.ok
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    #  Dashboards                                                           #
    # ------------------------------------------------------------------ #

    def list_dashboards(self) -> list[dict[str, Any]]:
        """Return all dashboards (uid, title, url)."""
        results = self._get("/api/search", params={"type": "dash-db"})
        return [
            {
                "uid": d.get("uid"),
                "title": d.get("title"),
                "url": d.get("url"),
            }
            for d in results
        ]

    def get_dashboard(self, uid: str) -> dict[str, Any]:
        """Fetch a dashboard by UID."""
        return self._get(f"/api/dashboards/uid/{uid}")

    # ------------------------------------------------------------------ #
    #  Annotations                                                          #
    # ------------------------------------------------------------------ #

    def add_annotation(
        self,
        text: str,
        tags: list[str] | None = None,
        dashboard_uid: str | None = None,
        panel_id: int | None = None,
    ) -> dict[str, Any]:
        """
        Create a Grafana annotation.

        Parameters
        ----------
        text : str
            Annotation body (supports basic HTML).
        tags : list[str], optional
            Tags for filtering (e.g. ``["agent", "scaling"]``).
        dashboard_uid : str, optional
            Scope annotation to a specific dashboard.
        panel_id : int, optional
            Scope annotation to a specific panel.
        """
        payload: dict[str, Any] = {
            "text": text,
            "tags": tags or ["oss-gpt", "agent"],
            "time": int(time.time() * 1000),  # epoch ms
        }
        if dashboard_uid:
            # Resolve dashboard id from uid
            try:
                dash = self.get_dashboard(dashboard_uid)
                payload["dashboardId"] = dash["dashboard"]["id"]
            except Exception:
                logger.warning(
                    "Could not resolve dashboard uid %s; posting global annotation",
                    dashboard_uid,
                )
        if panel_id is not None:
            payload["panelId"] = panel_id

        result = self._post("/api/annotations", payload)
        logger.info("Annotation created: id=%s", result.get("id"))
        return result

    # ------------------------------------------------------------------ #
    #  Datasources                                                          #
    # ------------------------------------------------------------------ #

    def list_datasources(self) -> list[dict[str, Any]]:
        """Return configured datasources (name, type, url)."""
        results = self._get("/api/datasources")
        return [
            {
                "id": ds.get("id"),
                "name": ds.get("name"),
                "type": ds.get("type"),
                "url": ds.get("url"),
            }
            for ds in results
        ]
