"""Real Kubernetes server-side dry-run for VNF configuration artifacts.

For each artifact we:
  1. Locate the matching Helm chart under `charts/oai-5g-core/` or `charts/oai-5g-ran/`.
  2. Render the chart with `helm template -f <values_file>`.
  3. Send every rendered manifest to the cluster via the kubernetes Python
     ``DynamicClient`` with ``dry_run="All"`` — the API server validates
     the resource (admission controllers, schema, defaulting) without
     persisting it.

The runner degrades gracefully: missing ``helm`` binary, missing kubeconfig,
or an unreachable cluster all produce a ``status: SKIPPED`` result with a
clear ``reason``. The pipeline keeps moving.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


REPO_ROOT = Path(__file__).resolve().parent.parent
CHART_ROOTS = [
    REPO_ROOT / "charts" / "oai-5g-core",
    REPO_ROOT / "charts" / "oai-5g-ran",
]


def _find_chart(vnf_name: str) -> Path | None:
    for root in CHART_ROOTS:
        cand = root / vnf_name
        if (cand / "Chart.yaml").is_file():
            return cand
    return None


def _extract_api_message(exc) -> str:
    """Pull the most useful message out of a kubernetes ApiException."""
    import json as _json
    body = getattr(exc, "body", None)
    if body:
        try:
            data = _json.loads(body)
            msg = data.get("message") or ""
            if msg:
                return msg[:500]
        except (ValueError, TypeError):
            pass
    if exc.reason:
        return f"{exc.reason} (status {exc.status})"
    return f"API error (status {exc.status})"


class K8sDryRunner:
    """Run a real server-side dry-run for each VNF configuration artifact."""

    def __init__(
        self,
        config_artifacts: list[dict[str, Any]],
        namespace: str | None = None,
        release_prefix: str = "",
    ) -> None:
        # Default: render with release name == vnf_name. This matches how
        # OAI charts are deployed in production (one Helm release per NF
        # named after the NF), so SSA upserts cleanly against an existing
        # release instead of tripping the immutable spec.selector check.
        self.config_artifacts = config_artifacts
        self.release_prefix = release_prefix
        self.namespace = namespace or os.environ.get("K8S_NAMESPACE", "oai-5g")
        self._unreachable_reason: str | None = None
        self._dyn = None
        self._api_client = None

        self._init_clients()

    def _init_clients(self) -> None:
        if shutil.which("helm") is None:
            self._unreachable_reason = "helm binary not found on PATH"
            return

        try:
            from kubernetes import client, config
            from kubernetes.dynamic import DynamicClient

            kubeconfig = os.environ.get("KUBECONFIG") or os.path.expanduser(
                os.environ.get("KUBECONFIG_PATH", "~/.kube/config")
            )
            if not Path(kubeconfig).is_file() or Path(kubeconfig).stat().st_size == 0:
                self._unreachable_reason = f"kubeconfig missing or empty: {kubeconfig}"
                return

            config.load_kube_config(config_file=kubeconfig)
            self._api_client = client.ApiClient()

            try:
                client.CoreV1Api(self._api_client).list_namespace(limit=1, _request_timeout=5)
            except Exception as exc:  # noqa: BLE001 — cluster unreachable
                self._unreachable_reason = f"cluster unreachable: {exc}"
                return

            self._dyn = DynamicClient(self._api_client)
            logger.info(
                "K8sDryRunner ready (kubeconfig=%s, namespace=%s)",
                kubeconfig,
                self.namespace,
            )
        except Exception as exc:  # noqa: BLE001
            self._unreachable_reason = f"client init failed: {exc}"

    @property
    def unreachable(self) -> bool:
        return self._unreachable_reason is not None

    def run(self) -> dict[str, Any]:
        """Render + server-side-validate each VNF. Returns a structured report."""
        if self.unreachable:
            logger.warning("K8s dry-run skipped: %s", self._unreachable_reason)
            return {
                "status": "SKIPPED",
                "reason": self._unreachable_reason,
                "errors": [],
                "validated": 0,
                "vnfs": [],
            }

        errors: list[dict[str, Any]] = []
        validated = 0
        per_vnf: list[dict[str, Any]] = []

        for cfg in self.config_artifacts:
            vnf_result = self._dry_run_vnf(cfg)
            per_vnf.append(vnf_result)
            errors.extend(vnf_result.get("errors", []))
            validated += vnf_result.get("validated", 0)

        status = "FAILED" if errors else "SUCCESS"
        return {
            "status": status,
            "reason": "" if status == "SUCCESS" else f"{len(errors)} manifest error(s)",
            "errors": errors,
            "validated": validated,
            "vnfs": per_vnf,
        }

    def _dry_run_vnf(self, cfg: dict[str, Any]) -> dict[str, Any]:
        vnf_name = cfg.get("vnf_name", "unknown")
        chart_path = _find_chart(vnf_name)
        if chart_path is None:
            return {
                "vnf": vnf_name,
                "validated": 0,
                "errors": [{
                    "vnf": vnf_name,
                    "stage": "chart_lookup",
                    "message": f"no Helm chart found for VNF '{vnf_name}' under {CHART_ROOTS}",
                }],
            }

        try:
            manifests = self._helm_render(vnf_name, chart_path, cfg.get("helm_values", {}))
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            return {
                "vnf": vnf_name,
                "validated": 0,
                "errors": [{
                    "vnf": vnf_name,
                    "stage": "helm_template",
                    "message": stderr[:500] or f"helm template failed (exit {exc.returncode})",
                }],
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "vnf": vnf_name,
                "validated": 0,
                "errors": [{
                    "vnf": vnf_name,
                    "stage": "helm_render",
                    "message": str(exc),
                }],
            }

        return self._validate_manifests(vnf_name, manifests)

    def _helm_render(
        self,
        vnf_name: str,
        chart_path: Path,
        helm_values: dict[str, Any],
    ) -> list[dict[str, Any]]:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as fh:
            yaml.safe_dump(helm_values, fh)
            values_file = fh.name

        try:
            release_name = (
                f"{self.release_prefix}-{vnf_name}" if self.release_prefix else vnf_name
            )
            cmd = [
                "helm", "template",
                release_name,
                str(chart_path),
                "-f", values_file,
                "-n", self.namespace,
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
        finally:
            try:
                os.unlink(values_file)
            except OSError:
                pass

        docs = [
            doc for doc in yaml.safe_load_all(proc.stdout)
            if isinstance(doc, dict) and doc.get("kind") and doc.get("apiVersion")
        ]
        return docs

    def _validate_manifests(
        self,
        vnf_name: str,
        manifests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        from kubernetes.client.exceptions import ApiException

        errors: list[dict[str, Any]] = []
        validated = 0

        for doc in manifests:
            kind = doc.get("kind", "Unknown")
            name = doc.get("metadata", {}).get("name", "unknown")
            try:
                resource = self._dyn.resources.get(
                    api_version=doc["apiVersion"], kind=kind
                )
                ns = doc.get("metadata", {}).get("namespace") or self.namespace
                if not getattr(resource, "namespaced", True):
                    ns = None

                # Server-side apply gives upsert semantics, so the dry-run
                # validates the manifest whether or not the resource already
                # exists in the cluster. `dry_run="All"` runs the full
                # admission chain without persisting.
                resource.server_side_apply(
                    body=doc,
                    name=name,
                    namespace=ns,
                    field_manager="fyp-policy-validator",
                    force_conflicts=True,
                    dry_run="All",
                )
                validated += 1
            except ApiException as exc:
                detail = _extract_api_message(exc)
                msg = f"{kind}/{name}: {detail}"
                errors.append({
                    "vnf": vnf_name,
                    "stage": "k8s_dry_run",
                    "kind": kind,
                    "name": name,
                    "message": msg,
                })
            except Exception as exc:  # noqa: BLE001
                errors.append({
                    "vnf": vnf_name,
                    "stage": "k8s_dry_run",
                    "kind": kind,
                    "name": name,
                    "message": str(exc)[:500],
                })

        return {
            "vnf": vnf_name,
            "validated": validated,
            "manifest_count": len(manifests),
            "errors": errors,
        }
