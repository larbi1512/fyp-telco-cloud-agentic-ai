"""
Helm CLI wrapper for remote cluster operations.

Executes ``helm`` commands via subprocess, targeting the remote
Kubernetes cluster through the ``KUBECONFIG`` environment variable.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from config.settings import KUBECONFIG_PATH, K8S_NAMESPACE

logger = logging.getLogger(__name__)


class HelmManager:
    """Execute Helm operations against a remote Kubernetes cluster."""

    def __init__(
        self,
        kubeconfig_path: str | None = None,
        default_namespace: str | None = None,
    ) -> None:
        self.kubeconfig = str(
            Path(kubeconfig_path or KUBECONFIG_PATH).expanduser()
        )
        self.default_ns = default_namespace or K8S_NAMESPACE
        logger.info(
            "HelmManager initialised (kubeconfig=%s, namespace=%s)",
            self.kubeconfig,
            self.default_ns,
        )

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _run(
        self,
        args: list[str],
        timeout: int = 300,
    ) -> subprocess.CompletedProcess[str]:
        """
        Run a helm command with KUBECONFIG injected into the environment.

        Returns the CompletedProcess; raises on non-zero exit.
        """
        env = {**os.environ, "KUBECONFIG": self.kubeconfig}
        cmd = ["helm", *args]
        logger.debug("Running: %s", " ".join(cmd))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode != 0:
            logger.error("Helm command failed:\n  stdout: %s\n  stderr: %s",
                         result.stdout.strip(), result.stderr.strip())
            result.check_returncode()  # raises CalledProcessError
        return result

    # ------------------------------------------------------------------ #
    #  Helm operations                                                      #
    # ------------------------------------------------------------------ #

    def install_chart(
        self,
        release_name: str,
        chart_path: str,
        namespace: str | None = None,
        values_file: str | None = None,
        extra_args: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        ``helm install`` a chart.

        Parameters
        ----------
        release_name : str
            Helm release name (e.g. ``"oai-amf"``).
        chart_path : str
            Path or repo reference to the chart.
        namespace : str, optional
            Target namespace (defaults to ``self.default_ns``).
        values_file : str, optional
            Path to a ``values.yaml`` override file.
        extra_args : list[str], optional
            Additional CLI flags (e.g. ``["--create-namespace"]``).
        """
        ns = namespace or self.default_ns
        args = ["install", release_name, chart_path, "-n", ns]
        if values_file:
            args.extend(["-f", values_file])
        if extra_args:
            args.extend(extra_args)

        result = self._run(args)
        logger.info("Helm install succeeded: %s in %s", release_name, ns)
        return {
            "status": "installed",
            "release": release_name,
            "namespace": ns,
            "output": result.stdout.strip(),
        }

    def upgrade_chart(
        self,
        release_name: str,
        chart_path: str,
        namespace: str | None = None,
        values_file: str | None = None,
        extra_args: list[str] | None = None,
    ) -> dict[str, Any]:
        """``helm upgrade`` an existing release."""
        ns = namespace or self.default_ns
        args = ["upgrade", release_name, chart_path, "-n", ns]
        if values_file:
            args.extend(["-f", values_file])
        if extra_args:
            args.extend(extra_args)

        result = self._run(args)
        logger.info("Helm upgrade succeeded: %s in %s", release_name, ns)
        return {
            "status": "upgraded",
            "release": release_name,
            "namespace": ns,
            "output": result.stdout.strip(),
        }

    def rollback_release(
        self,
        release_name: str,
        revision: int | None = None,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """``helm rollback`` a release to a previous revision."""
        ns = namespace or self.default_ns
        args = ["rollback", release_name]
        if revision is not None:
            args.append(str(revision))
        args.extend(["-n", ns])

        result = self._run(args)
        logger.info("Helm rollback succeeded: %s in %s", release_name, ns)
        return {
            "status": "rolled_back",
            "release": release_name,
            "namespace": ns,
            "revision": revision,
            "output": result.stdout.strip(),
        }

    def uninstall_release(
        self,
        release_name: str,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """``helm uninstall`` a release."""
        ns = namespace or self.default_ns
        args = ["uninstall", release_name, "-n", ns]
        result = self._run(args)
        logger.info("Helm uninstall succeeded: %s in %s", release_name, ns)
        return {
            "status": "uninstalled",
            "release": release_name,
            "namespace": ns,
            "output": result.stdout.strip(),
        }

    # ------------------------------------------------------------------ #
    #  Query helpers                                                        #
    # ------------------------------------------------------------------ #

    def list_releases(
        self,
        namespace: str | None = None,
        all_namespaces: bool = False,
    ) -> list[dict[str, Any]]:
        """``helm list`` releases as structured dicts."""
        args = ["list", "-o", "json"]
        if all_namespaces:
            args.append("--all-namespaces")
        else:
            ns = namespace or self.default_ns
            args.extend(["-n", ns])

        result = self._run(args)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            logger.warning("Could not parse helm list output as JSON")
            return []

    def get_release_status(
        self,
        release_name: str,
        namespace: str | None = None,
    ) -> str:
        """Return the raw ``helm status`` output."""
        ns = namespace or self.default_ns
        result = self._run(["status", release_name, "-n", ns])
        return result.stdout.strip()

    # ------------------------------------------------------------------ #
    #  Dry-run / template                                                   #
    # ------------------------------------------------------------------ #

    def dry_run_install(
        self,
        release_name: str,
        chart_path: str,
        namespace: str | None = None,
        values_file: str | None = None,
    ) -> str:
        """
        ``helm install --dry-run`` — validates the chart without applying.

        Returns the rendered YAML output.
        """
        ns = namespace or self.default_ns
        args = ["install", release_name, chart_path, "-n", ns, "--dry-run"]
        if values_file:
            args.extend(["-f", values_file])

        result = self._run(args)
        return result.stdout.strip()
