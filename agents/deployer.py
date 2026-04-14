"""
Deployer Agent — Applies agent-generated configs to the remote K8s cluster.

Pipeline position: Final node in the pre-deployment chain (Phase 4 integration)
  ... → Policy Validator → (HITL) → [Deployer] → END

Input:  OrchestratorState with config_artifacts (Helm values per VNF)
Output: OrchestratorState with deployment_results

Workflow:
  1. Write each VNF's helm_values to a temporary YAML file.
  2. For each VNF, run helm install (or upgrade if release exists).
  3. Wait briefly and verify pod health via K8sClient.
  4. Post a Grafana annotation summarizing the deployment.
  5. Return structured deployment_results.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import yaml

from config.settings import K8S_NAMESPACE, PROJECT_ROOT
from core.state import OrchestratorState
from infra.k8s_client import K8sClient
from infra.helm_manager import HelmManager
from infra.grafana_client import GrafanaClient

logger = logging.getLogger(__name__)

# Directory for writing temporary Helm values files
DEPLOY_DIR = Path("/tmp/fyp_deploy")

# Map of VNF type → chart subdirectory under charts/oai-5g-core/
CHART_MAP = {
    "nrf": "oai-nrf",
    "amf": "oai-amf",
    "smf": "oai-smf",
    "upf": "oai-upf",
    "ausf": "oai-ausf",
    "udm": "oai-udm",
    "udr": "oai-udr",
    "nssf": "oai-nssf",
}

CHARTS_BASE = PROJECT_ROOT / "charts" / "oai-5g-core"


def _write_values_file(vnf_name: str, helm_values: dict[str, Any]) -> Path:
    """Write Helm values dict to a YAML file and return its path."""
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    path = DEPLOY_DIR / f"{vnf_name}-values.yaml"
    with open(path, "w") as f:
        yaml.dump(helm_values, f, default_flow_style=False)
    logger.info("Wrote values file: %s", path)
    return path


def _resolve_chart_path(vnf_name: str) -> str | None:
    """Resolve the local chart directory for a VNF."""
    vnf_type = vnf_name.lower().replace("oai-", "")
    chart_dir_name = CHART_MAP.get(vnf_type)
    if not chart_dir_name:
        return None
    chart_path = CHARTS_BASE / chart_dir_name
    if chart_path.is_dir():
        return str(chart_path)
    return None


def _check_release_exists(helm: HelmManager, release_name: str, namespace: str) -> bool:
    """Return True if a Helm release already exists."""
    try:
        releases = helm.list_releases(namespace=namespace)
        return any(r.get("name") == release_name for r in releases)
    except Exception:
        return False


def teardown_releases(
    helm: HelmManager | None = None,
    namespace: str | None = None,
) -> list[dict[str, Any]]:
    """
    Uninstall all known OAI Helm releases from the namespace.

    Used to ensure a clean-slate cluster before re-deploying
    (e.g. between experiment runs).

    Returns a list of {release, status, message} dicts.
    """
    ns = namespace or K8S_NAMESPACE
    if helm is None:
        helm = HelmManager()

    results: list[dict[str, Any]] = []
    for release_name in CHART_MAP.values():
        if not _check_release_exists(helm, release_name, ns):
            logger.debug("Release %s not found — skipping teardown", release_name)
            continue
        try:
            helm.uninstall_release(release_name, namespace=ns)
            logger.info("Teardown: uninstalled %s from %s", release_name, ns)
            results.append({"release": release_name, "status": "uninstalled", "message": "OK"})
        except Exception as e:
            logger.warning("Teardown: failed to uninstall %s: %s", release_name, e)
            results.append({"release": release_name, "status": "failed", "message": str(e)[:200]})
    return results


def deployer_agent(state: OrchestratorState) -> dict[str, Any]:
    """
    LangGraph node: Deployer.

    Reads:  state["config_artifacts"], state["validation_report"]
    Writes: state["deployment_results"], state["messages"],
            state["current_agent"], state["phase"]

    If state["clean_deploy"] is True (default), all existing OAI
    releases are uninstalled before deploying to guarantee a clean slate.
    """
    logger.info("Deployer Agent: starting")

    config_artifacts = state.get("config_artifacts", [])
    validation = state.get("validation_report") or {}
    namespace = K8S_NAMESPACE

    if not config_artifacts:
        return {
            "error": "No config artifacts to deploy — run the pre-deployment pipeline first",
            "messages": [{"role": "agent", "content": " No config artifacts available for deployment."}],
            "current_agent": "deployer",
        }

    # Safety: refuse to deploy if validation decision was NO_GO
    if validation.get("decision") == "NO_GO":
        return {
            "error": "Cannot deploy — policy validation returned NO_GO",
            "messages": [{"role": "agent", "content": "Deployment blocked: policy validation is NO_GO."}],
            "current_agent": "deployer",
        }

    # ── Initialise infra clients ──
    try:
        helm = HelmManager()
        k8s = K8sClient()
    except Exception as e:
        logger.error("Failed to initialise infra clients: %s", e)
        return {
            "error": f"Infrastructure client init failed: {e}",
            "messages": [{"role": "agent", "content": f"Cannot connect to cluster: {e}"}],
            "current_agent": "deployer",
        }

    # ── Teardown existing releases for clean slate ──
    clean_deploy = state.get("clean_deploy", True)
    if clean_deploy:
        logger.info("Clean deploy enabled — tearing down existing releases")
        teardown_results = teardown_releases(helm=helm, namespace=namespace)
        if teardown_results:
            uninstalled = [r["release"] for r in teardown_results if r["status"] == "uninstalled"]
            if uninstalled:
                logger.info("Teardown complete: removed %s", ", ".join(uninstalled))

    # ── Deploy each VNF ──
    deployment_results: list[dict[str, Any]] = []
    deployed_releases: list[str] = []

    for cfg in config_artifacts:
        vnf_name = cfg.get("vnf_name", "unknown")
        helm_values = cfg.get("helm_values", {})

        # Resolve chart path
        chart_path = _resolve_chart_path(vnf_name)
        if not chart_path:
            result = {
                "vnf_name": vnf_name,
                "release_name": vnf_name,
                "status": "skipped",
                "message": f"No chart found for {vnf_name}",
            }
            deployment_results.append(result)
            logger.warning("Skipping %s — no chart found", vnf_name)
            continue

        # Write values file
        values_path = _write_values_file(vnf_name, helm_values)

        # Use the oai- prefixed chart name as the release name
        # to stay consistent with existing K8s resource annotations
        vnf_type = vnf_name.lower().replace("oai-", "")
        release_name = CHART_MAP.get(vnf_type, vnf_name)

        # Use upgrade --install (idempotent: installs if new, upgrades if exists)
        try:
            logger.info("Deploying release: %s (upgrade --install)", release_name)
            deploy_result = helm.upgrade_chart(
                release_name=release_name,
                chart_path=chart_path,
                namespace=namespace,
                values_file=str(values_path),
                extra_args=["--install", "--create-namespace"],
            )
            action = "installed"

            deployment_results.append({
                "vnf_name": vnf_name,
                "release_name": release_name,
                "status": action,
                "message": deploy_result.get("output", "")[:200],
            })
            deployed_releases.append(release_name)

        except Exception as e:
            logger.error("Deployment failed for %s: %s", vnf_name, e)
            deployment_results.append({
                "vnf_name": vnf_name,
                "release_name": release_name,
                "status": "failed",
                "message": str(e)[:200],
            })

    # ── Pod health verification ──
    logger.info("Waiting 2mins for pods to initialise before health check...")
    time.sleep(120)

    pod_health: list[dict[str, Any]] = []
    try:
        pods = k8s.get_pods(namespace=namespace)
        for pod in pods:
            pod_health.append({
                "name": pod["name"],
                "phase": pod["phase"],
                "ready": all(c.get("ready", False) for c in pod.get("containers", [])),
            })
    except Exception as e:
        logger.warning("Pod health check failed: %s", e)

    # ── Grafana annotation ──
    try:
        grafana = GrafanaClient()
        if grafana.ping():
            success_count = sum(1 for r in deployment_results if r["status"] in ("installed", "upgraded"))
            fail_count = sum(1 for r in deployment_results if r["status"] == "failed")
            annotation_text = (
                f"<b>OSS-GPT Deployment</b><br>"
                f"VNFs deployed: {success_count}, failed: {fail_count}<br>"
                f"Releases: {', '.join(deployed_releases)}"
            )
            grafana.add_annotation(
                text=annotation_text,
                tags=["oss-gpt", "deployment", "phase4"],
            )
            logger.info("Grafana annotation posted")
    except Exception as e:
        logger.warning("Grafana annotation failed (non-critical): %s", e)

    # ── Build summary message ──
    success = sum(1 for r in deployment_results if r["status"] in ("installed", "upgraded"))
    failed = sum(1 for r in deployment_results if r["status"] == "failed")
    skipped = sum(1 for r in deployment_results if r["status"] == "skipped")

    summary_lines = [
        f"**Deployment Complete** (Namespace: `{namespace}`)",
        f"   Deployed: {success} | Failed: {failed} | Skipped: {skipped}",
        "",
        "   | VNF | Release | Status | Message |",
        "   |-----|---------|--------|---------|",
    ]
    for r in deployment_results:
        icon = {"installed": "installed", "upgraded": "upgraded ", "failed": "failed", "skipped": "skipped"}.get(r["status"], "❓")
        summary_lines.append(
            f"   | {r['vnf_name']} | {r['release_name']} | {icon} {r['status']} | {r['message'][:60]} |"
        )

    if pod_health:
        running = sum(1 for p in pod_health if p["phase"] == "Running" and p["ready"])
        total = len(pod_health)
        summary_lines.append(f"\n   **Pod Health:** {running}/{total} pods running and ready")

    summary = "\n".join(summary_lines)

    return {
        "deployment_results": deployment_results,
        "phase": "post_deployment",
        "current_agent": "deployer",
        "messages": [{"role": "agent", "content": summary}],
    }
