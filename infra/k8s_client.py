"""
Kubernetes client wrapper for remote cluster operations.

Uses the official ``kubernetes`` Python client, configured via a
kubeconfig file that points to the remote K8s VM.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from config.settings import KUBECONFIG_PATH, K8S_NAMESPACE

logger = logging.getLogger(__name__)


class K8sClient:
    """Thin wrapper around the Kubernetes Python client."""

    def __init__(
        self,
        kubeconfig_path: str | None = None,
        default_namespace: str | None = None,
    ) -> None:
        self.kubeconfig = str(
            Path(kubeconfig_path or KUBECONFIG_PATH).expanduser()
        )
        self.default_ns = default_namespace or K8S_NAMESPACE

        # Load configuration from kubeconfig
        config.load_kube_config(config_file=self.kubeconfig)
        self._core = client.CoreV1Api()
        self._apps = client.AppsV1Api()
        logger.info(
            "K8sClient initialised (kubeconfig=%s, namespace=%s)",
            self.kubeconfig,
            self.default_ns,
        )

    # ------------------------------------------------------------------ #
    #  Pod operations                                                      #
    # ------------------------------------------------------------------ #

    def get_pods(
        self,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a simplified list of pods in *namespace*."""
        ns = namespace or self.default_ns
        kwargs: dict[str, Any] = {}
        if label_selector:
            kwargs["label_selector"] = label_selector

        resp = self._core.list_namespaced_pod(ns, **kwargs)
        pods = []
        for pod in resp.items:
            containers = []
            for cs in pod.status.container_statuses or []:
                containers.append(
                    {
                        "name": cs.name,
                        "ready": cs.ready,
                        "restart_count": cs.restart_count,
                        "image": cs.image,
                    }
                )

            pods.append(
                {
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "phase": pod.status.phase,
                    "node": pod.spec.node_name,
                    "containers": containers,
                    "creation_ts": (
                        pod.metadata.creation_timestamp.isoformat()
                        if pod.metadata.creation_timestamp
                        else None
                    ),
                }
            )
        logger.debug("get_pods(%s): returned %d pods", ns, len(pods))
        return pods

    def get_pod_logs(
        self,
        pod_name: str,
        namespace: str | None = None,
        container: str | None = None,
        tail_lines: int = 100,
    ) -> str:
        """Fetch the last *tail_lines* of logs for a pod."""
        ns = namespace or self.default_ns
        kwargs: dict[str, Any] = {"tail_lines": tail_lines}
        if container:
            kwargs["container"] = container

        try:
            logs = self._core.read_namespaced_pod_log(pod_name, ns, **kwargs)
            return logs
        except ApiException as exc:
            logger.error("Failed to get logs for %s/%s: %s", ns, pod_name, exc.reason)
            raise

    # ------------------------------------------------------------------ #
    #  Event / describe helpers                                            #
    # ------------------------------------------------------------------ #

    def get_events(
        self,
        namespace: str | None = None,
        involved_object_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent events, optionally filtered by object name."""
        ns = namespace or self.default_ns
        resp = self._core.list_namespaced_event(ns)
        events = []
        for ev in resp.items:
            if involved_object_name and (
                ev.involved_object.name != involved_object_name
            ):
                continue
            events.append(
                {
                    "reason": ev.reason,
                    "message": ev.message,
                    "type": ev.type,
                    "count": ev.count,
                    "first_ts": (
                        ev.first_timestamp.isoformat()
                        if ev.first_timestamp
                        else None
                    ),
                    "last_ts": (
                        ev.last_timestamp.isoformat()
                        if ev.last_timestamp
                        else None
                    ),
                    "object": ev.involved_object.name,
                }
            )
        return events

    # ------------------------------------------------------------------ #
    #  Deployment helpers                                                   #
    # ------------------------------------------------------------------ #

    def get_deployments(
        self,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        """List deployments with replica status."""
        ns = namespace or self.default_ns
        resp = self._apps.list_namespaced_deployment(ns)
        deps = []
        for d in resp.items:
            deps.append(
                {
                    "name": d.metadata.name,
                    "replicas": d.spec.replicas,
                    "ready_replicas": d.status.ready_replicas or 0,
                    "available_replicas": d.status.available_replicas or 0,
                }
            )
        return deps

    def scale_deployment(
        self,
        name: str,
        replicas: int,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Scale a deployment to the given replica count."""
        ns = namespace or self.default_ns
        body = {"spec": {"replicas": replicas}}
        try:
            self._apps.patch_namespaced_deployment_scale(name, ns, body)
            logger.info("Scaled %s/%s to %d replicas", ns, name, replicas)
            return {"status": "ok", "deployment": name, "replicas": replicas}
        except ApiException as exc:
            logger.error("Scale failed for %s/%s: %s", ns, name, exc.reason)
            raise

    # ------------------------------------------------------------------ #
    #  Manifest application (for dry-run and live apply)                    #
    # ------------------------------------------------------------------ #

    def apply_manifest(
        self,
        manifest: dict[str, Any],
        namespace: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Apply a single Kubernetes manifest dict.

        Supports kind: ConfigMap, Service, Deployment.
        When *dry_run* is True, the object is validated server-side
        without being persisted.
        """
        ns = namespace or self.default_ns
        kind = manifest.get("kind", "")
        name = manifest.get("metadata", {}).get("name", "unknown")
        dr = "All" if dry_run else None

        try:
            if kind == "ConfigMap":
                self._core.create_namespaced_config_map(
                    ns, manifest, dry_run=dr
                )
            elif kind == "Service":
                self._core.create_namespaced_service(
                    ns, manifest, dry_run=dr
                )
            elif kind == "Deployment":
                self._apps.create_namespaced_deployment(
                    ns, manifest, dry_run=dr
                )
            else:
                return {
                    "status": "unsupported",
                    "kind": kind,
                    "name": name,
                    "message": f"Kind '{kind}' is not handled by apply_manifest",
                }

            action = "dry-run validated" if dry_run else "applied"
            logger.info("%s %s/%s (%s)", action.capitalize(), kind, name, ns)
            return {"status": "ok", "kind": kind, "name": name, "action": action}

        except ApiException as exc:
            logger.error(
                "apply_manifest failed for %s/%s: %s", kind, name, exc.reason
            )
            return {
                "status": "error",
                "kind": kind,
                "name": name,
                "error": exc.reason,
            }

    # ------------------------------------------------------------------ #
    #  Namespace helpers                                                    #
    # ------------------------------------------------------------------ #

    def get_namespaces(self) -> list[str]:
        """Return a list of namespace names in the cluster."""
        resp = self._core.list_namespace()
        return [ns.metadata.name for ns in resp.items]

    # ------------------------------------------------------------------ #
    #  Health / connectivity check                                          #
    # ------------------------------------------------------------------ #

    def ping(self) -> bool:
        """Return True if the cluster API server is reachable."""
        try:
            self._core.list_namespace(limit=1)
            return True
        except Exception:
            return False
