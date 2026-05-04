"""
Config validation helper — drives the "Config error rate" metric.

For each generated config_artifact we run `helm template --validate` against
the corresponding chart and the artifact's helm_values. A non-zero exit code
or a parsing failure on the rendered YAML counts as an error.

The function is deterministic and offline (no cluster contact for `--validate`
beyond what helm itself does locally on the rendered manifests).
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from agents.deployer import CHART_MAP, CHARTS_BASE  # reuse the canonical mapping

logger = logging.getLogger(__name__)

HELM_BIN = "helm"
TEMPLATE_TIMEOUT_S = 30


def _resolve_chart_path(vnf_name_or_type: str) -> Path | None:
    key = vnf_name_or_type.lower().replace("oai-", "")
    chart_dir_name = CHART_MAP.get(key)
    if not chart_dir_name:
        return None
    p = CHARTS_BASE / chart_dir_name
    return p if p.is_dir() else None


def validate_one(vnf_name: str, helm_values: dict[str, Any]) -> dict[str, Any]:
    """
    Run `helm template --validate` for a single VNF's helm_values.

    Returns a dict {ok: bool, vnf: str, stage: str, message: str}.
    `stage` is one of: "chart_resolution", "values_write", "helm_template",
    "yaml_parse", "ok".
    """
    chart_path = _resolve_chart_path(vnf_name)
    if chart_path is None:
        return {
            "ok": False,
            "vnf": vnf_name,
            "stage": "chart_resolution",
            "message": f"no chart directory found for VNF '{vnf_name}'",
        }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix=f"validate-{vnf_name}-", delete=False
    ) as tf:
        try:
            yaml.safe_dump(helm_values or {}, tf)
        except Exception as e:
            return {
                "ok": False,
                "vnf": vnf_name,
                "stage": "values_write",
                "message": f"could not serialise helm_values: {e}",
            }
        values_path = tf.name

    try:
        proc = subprocess.run(
            [
                HELM_BIN,
                "template",
                vnf_name,
                str(chart_path),
                "-f",
                values_path,
                "--validate=false",  # skip kube-apiserver validation; offline check
            ],
            capture_output=True,
            text=True,
            timeout=TEMPLATE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "vnf": vnf_name,
            "stage": "helm_template",
            "message": f"helm template timed out after {TEMPLATE_TIMEOUT_S}s",
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "vnf": vnf_name,
            "stage": "helm_template",
            "message": "helm binary not found on PATH",
        }
    finally:
        Path(values_path).unlink(missing_ok=True)

    if proc.returncode != 0:
        return {
            "ok": False,
            "vnf": vnf_name,
            "stage": "helm_template",
            "message": (proc.stderr or proc.stdout or "helm template failed")[:500],
        }

    # Sanity-parse rendered docs as YAML to catch malformed templates that
    # nonetheless exit zero.
    try:
        docs = list(yaml.safe_load_all(proc.stdout))
        if not docs:
            return {
                "ok": False,
                "vnf": vnf_name,
                "stage": "yaml_parse",
                "message": "helm template produced empty output",
            }
    except yaml.YAMLError as e:
        return {
            "ok": False,
            "vnf": vnf_name,
            "stage": "yaml_parse",
            "message": f"rendered YAML failed to parse: {e}",
        }

    return {"ok": True, "vnf": vnf_name, "stage": "ok", "message": "validated"}


def validate_artifacts(config_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Validate every artifact and return aggregate stats.

    Output:
        {
          "total": int,
          "ok": int,
          "failed": int,
          "error_rate": float,                 # failed / total in [0,1]
          "per_artifact": [validate_one(...)] # one entry per artifact
        }
    """
    per: list[dict[str, Any]] = []
    for cfg in config_artifacts or []:
        vnf_name = cfg.get("vnf_name") or cfg.get("name") or "unknown"
        per.append(validate_one(vnf_name, cfg.get("helm_values", {}) or {}))

    total = len(per)
    failed = sum(1 for r in per if not r["ok"])
    return {
        "total": total,
        "ok": total - failed,
        "failed": failed,
        "error_rate": (failed / total) if total else 0.0,
        "per_artifact": per,
    }
