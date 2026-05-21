"""
Prometheus HTTP API client for querying remote 5G Core metrics.

Uses ``prometheus-api-client`` to connect to the Prometheus instance
running on the remote Kubernetes VM.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from prometheus_api_client import PrometheusConnect

from config.settings import PROMETHEUS_URL

logger = logging.getLogger(__name__)


class PrometheusClient:
    """Query live metrics from a remote Prometheus server."""

    def __init__(self, prometheus_url: str | None = None) -> None:
        self.url = prometheus_url or PROMETHEUS_URL
        self._prom = PrometheusConnect(url=self.url, disable_ssl=True)
        logger.info("PrometheusClient initialised (url=%s)", self.url)

    # ------------------------------------------------------------------ #
    #  Connectivity                                                         #
    # ------------------------------------------------------------------ #

    def ping(self) -> bool:
        """Return True if the Prometheus server is reachable."""
        try:
            self._prom.check_prometheus_connection()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    #  Raw query helpers                                                     #
    # ------------------------------------------------------------------ #

    def instant_query(self, promql: str) -> list[dict[str, Any]]:
        """Execute an instant PromQL query and return the result vector."""
        result = self._prom.custom_query(promql)
        return result

    def range_query(
        self,
        promql: str,
        start: datetime | None = None,
        end: datetime | None = None,
        step: str = "30s",
    ) -> list[dict[str, Any]]:
        """Execute a range query over *[start, end]* with *step* resolution."""
        now = datetime.utcnow()
        _start = start or (now - timedelta(minutes=15))
        _end = end or now
        result = self._prom.custom_query_range(
            promql,
            start_time=_start,
            end_time=_end,
            step=step,
        )
        return result

    # ------------------------------------------------------------------ #
    #  Pre-built 5G Core metric helpers                                     #
    # ------------------------------------------------------------------ #

    def get_cpu_usage(
        self,
        namespace: str = "free5gc",
        duration: str = "5m",
    ) -> list[dict[str, Any]]:
        """
        Aggregate CPU usage rate per container in *namespace*.

        Returns a list of ``{pod, container, cpu_cores}`` dicts.
        """
        query = (
            f'sum by (pod, container) ('
            f'rate(container_cpu_usage_seconds_total'
            f'{{namespace="{namespace}", container!=""}}[{duration}]))'
        )
        raw = self.instant_query(query)
        results = []
        for item in raw:
            metric = item.get("metric", {})
            value = item.get("value", [None, "0"])
            results.append(
                {
                    "pod": metric.get("pod", ""),
                    "container": metric.get("container", ""),
                    "cpu_cores": float(value[1]),
                }
            )
        return results

    def get_memory_usage(
        self,
        namespace: str = "free5gc",
    ) -> list[dict[str, Any]]:
        """
        Current working-set memory per container in *namespace*.

        Returns a list of ``{pod, container, memory_bytes}`` dicts.
        """
        query = (
            f'sum by (pod, container) ('
            f'container_memory_working_set_bytes'
            f'{{namespace="{namespace}", container!=""}})'
        )
        raw = self.instant_query(query)
        results = []
        for item in raw:
            metric = item.get("metric", {})
            value = item.get("value", [None, "0"])
            results.append(
                {
                    "pod": metric.get("pod", ""),
                    "container": metric.get("container", ""),
                    "memory_bytes": float(value[1]),
                }
            )
        return results

    def get_network_receive_bytes(
        self,
        namespace: str = "free5gc",
        duration: str = "5m",
    ) -> list[dict[str, Any]]:
        """
        Network receive throughput (bytes/s) per pod in *namespace*.
        """
        query = (
            f'sum by (pod) ('
            f'rate(container_network_receive_bytes_total'
            f'{{namespace="{namespace}"}}[{duration}]))'
        )
        raw = self.instant_query(query)
        results = []
        for item in raw:
            metric = item.get("metric", {})
            value = item.get("value", [None, "0"])
            results.append(
                {
                    "pod": metric.get("pod", ""),
                    "rx_bytes_per_sec": float(value[1]),
                }
            )
        return results

    def get_pod_restart_count(
        self,
        namespace: str = "free5gc",
    ) -> list[dict[str, Any]]:
        """Total container restart counts per pod in *namespace*."""
        query = (
            f'sum by (pod) ('
            f'kube_pod_container_status_restarts_total'
            f'{{namespace="{namespace}"}})'
        )
        raw = self.instant_query(query)
        results = []
        for item in raw:
            metric = item.get("metric", {})
            value = item.get("value", [None, "0"])
            results.append(
                {
                    "pod": metric.get("pod", ""),
                    "restarts": int(float(value[1])),
                }
            )
        return results

    # ------------------------------------------------------------------ #
    #  Application-layer metrics (OAI exporters)                          #
    # ------------------------------------------------------------------ #

    def get_amf_registration_rate(
        self,
        namespace: str = "free5gc",
        duration: str = "5m",
    ) -> list[dict[str, Any]]:
        """
        Per-second AMF registration rate aggregated across all AMF replicas.

        Requires the OAI AMF Prometheus exporter to expose
        ``amf_registration_total``. Returns an empty list when no samples
        are available (e.g. exporter not deployed).

        Returns a list of ``{vnf, registrations_per_sec}`` dicts (typically
        one element since the query sums across replicas).
        """
        query = (
            f'sum(rate(amf_registration_total'
            f'{{namespace="{namespace}"}}[{duration}]))'
        )
        raw = self.instant_query(query)
        results = []
        for item in raw:
            value = item.get("value", [None, "0"])
            try:
                rate = float(value[1])
            except (TypeError, ValueError):
                continue
            results.append({"vnf": "oai-amf", "registrations_per_sec": rate})
        return results

    def get_upf_session_count(
        self,
        namespace: str = "free5gc",
    ) -> list[dict[str, Any]]:
        """
        Current number of active N4 sessions on the UPF.

        Requires the OAI UPF Prometheus exporter to expose
        ``upf_n4_sessions``. Returns an empty list when no samples are
        available (e.g. exporter not deployed).

        Returns a list of ``{vnf, sessions}`` dicts (typically one element
        since the query sums across replicas).
        """
        query = (
            f'sum(upf_n4_sessions'
            f'{{namespace="{namespace}"}})'
        )
        raw = self.instant_query(query)
        results = []
        for item in raw:
            value = item.get("value", [None, "0"])
            try:
                count = int(float(value[1]))
            except (TypeError, ValueError):
                continue
            results.append({"vnf": "oai-upf", "sessions": count})
        return results
