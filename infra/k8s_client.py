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

    def patch_deployment_resources(
        self,
        name: str,
        container_name: str,
        factor: float = 1.5,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """
        Multiply the resource requests/limits of *container_name* inside
        the Deployment *name* by *factor*.

        Uses a strategic-merge patch on the Deployment spec.  If the
        container's existing limits cannot be read, applies sensible
        defaults (500m CPU, 512Mi memory) before multiplying.
        """
        ns = namespace or self.default_ns

        # Read the current Deployment to find existing resource values.
        try:
            dep = self._apps.read_namespaced_deployment(name, ns)
        except ApiException as exc:
            logger.error(
                "patch_deployment_resources: read failed for %s/%s: %s",
                ns, name, exc.reason,
            )
            raise

        # Locate the target container.
        containers = dep.spec.template.spec.containers or []
        target = None
        for c in containers:
            if c.name == container_name:
                target = c
                break

        if target is None:
            raise ValueError(
                f"Container '{container_name}' not found in Deployment '{name}' "
                f"(available: {[c.name for c in containers]})"
            )

        # Extract current limits (fall back to defaults).
        current_limits = {}
        if target.resources and target.resources.limits:
            current_limits = dict(target.resources.limits)
        current_cpu = current_limits.get("cpu", "500m")
        current_mem = current_limits.get("memory", "512Mi")

        # Parse, multiply, and format back.
        new_cpu = self._scale_cpu(current_cpu, factor)
        new_mem = self._scale_memory(current_mem, factor)

        # Build strategic-merge patch.
        patch_body = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": container_name,
                                "resources": {
                                    "limits": {"cpu": new_cpu, "memory": new_mem},
                                    "requests": {"cpu": new_cpu, "memory": new_mem},
                                },
                            }
                        ]
                    }
                }
            }
        }

        try:
            self._apps.patch_namespaced_deployment(name, ns, body=patch_body)
            logger.info(
                "Patched resources for %s/%s container=%s: "
                "cpu %s→%s, mem %s→%s (factor=%.2f)",
                ns, name, container_name,
                current_cpu, new_cpu, current_mem, new_mem, factor,
            )
            return {
                "status": "ok",
                "deployment": name,
                "container": container_name,
                "old": {"cpu": current_cpu, "memory": current_mem},
                "new": {"cpu": new_cpu, "memory": new_mem},
            }
        except ApiException as exc:
            logger.error(
                "patch_deployment_resources failed for %s/%s: %s",
                ns, name, exc.reason,
            )
            raise

    def rollout_restart_deployment(
        self,
        name: str,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """
        Trigger a rolling restart of a Deployment by annotating the pod
        template with the current timestamp (same mechanism as
        ``kubectl rollout restart``).
        """
        ns = namespace or self.default_ns
        from datetime import datetime

        patch_body = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": datetime.utcnow().isoformat(),
                        }
                    }
                }
            }
        }
        try:
            self._apps.patch_namespaced_deployment(name, ns, body=patch_body)
            logger.info("Rollout restart triggered for %s/%s", ns, name)
            return {"status": "ok", "deployment": name, "action": "rollout_restart"}
        except ApiException as exc:
            logger.error("Rollout restart failed for %s/%s: %s", ns, name, exc.reason)
            raise

    # ------------------------------------------------------------------ #
    #  ConfigMap patching                                                    #
    # ------------------------------------------------------------------ #

    def patch_configmap(
        self,
        name: str,
        updates: dict[str, str],
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """
        Merge *updates* into an existing ConfigMap's ``data`` field.

        Uses a strategic-merge patch so only the specified keys are
        changed; all other keys in the ConfigMap are preserved.
        """
        ns = namespace or self.default_ns
        patch_body = {"data": updates}

        try:
            self._core.patch_namespaced_config_map(name, ns, body=patch_body)
            logger.info(
                "Patched ConfigMap %s/%s (keys=%s)",
                ns, name, list(updates.keys()),
            )
            return {
                "status": "ok",
                "config_map": name,
                "updated_keys": list(updates.keys()),
            }
        except ApiException as exc:
            logger.error(
                "patch_configmap failed for %s/%s: %s", ns, name, exc.reason,
            )
            raise

    # ------------------------------------------------------------------ #
    #  Resource-string helpers                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _scale_cpu(current: str, factor: float) -> str:
        """Scale a Kubernetes CPU string by *factor*, returning millicores."""
        s = str(current).strip()
        if s.endswith("m"):
            millicores = float(s[:-1])
        else:
            millicores = float(s) * 1000
        new_m = int(millicores * factor)
        return f"{new_m}m"

    @staticmethod
    def _scale_memory(current: str, factor: float) -> str:
        """Scale a Kubernetes memory string by *factor*, preserving the unit."""
        s = str(current).strip()
        for suffix in ("Gi", "Mi", "Ki", "G", "M", "K"):
            if s.endswith(suffix):
                numeric = float(s[: -len(suffix)])
                new_val = int(numeric * factor)
                return f"{new_val}{suffix}"
        # Plain bytes
        return str(int(float(s) * factor))

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
