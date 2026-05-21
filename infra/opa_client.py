"""Thin wrapper around the `opa eval` subprocess.

Used by the Policy Validator to evaluate Rego policies under
`infra/opa/policies/` against a config-artifacts input document.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY_DIR = REPO_ROOT / "infra" / "opa" / "policies"
DEFAULT_VENDORED_BIN = REPO_ROOT / "bin" / "opa"


class OPABinaryNotFound(RuntimeError):
    """Raised when the `opa` binary cannot be located."""


class OPAEvalError(RuntimeError):
    """Raised when `opa eval` exits non-zero or returns unparseable JSON."""


class OPAClient:
    """Resolve the OPA binary and run `opa eval` queries against a policy bundle."""

    def __init__(
        self,
        policy_dir: str | os.PathLike[str] | None = None,
        binary: str | os.PathLike[str] | None = None,
    ) -> None:
        self.policy_dir = Path(policy_dir) if policy_dir else DEFAULT_POLICY_DIR
        self.binary = self._resolve_binary(binary)

    @staticmethod
    def _resolve_binary(explicit: str | os.PathLike[str] | None) -> Path:
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit))
        env_bin = os.environ.get("OPA_BINARY")
        if env_bin:
            candidates.append(Path(env_bin))
        candidates.append(DEFAULT_VENDORED_BIN)
        path_bin = shutil.which("opa")
        if path_bin:
            candidates.append(Path(path_bin))

        for cand in candidates:
            if cand.is_file() and os.access(cand, os.X_OK):
                return cand

        raise OPABinaryNotFound(
            "OPA binary not found. Run scripts/install_opa.sh or set OPA_BINARY."
        )

    def evaluate(self, input_doc: dict[str, Any], query: str) -> Any:
        """Run `opa eval -d <policy_dir> -i - <query>` and return the parsed result.

        Returns the JSON-decoded `result[0].expressions[0].value` field, or `None`
        if OPA produced no result (no matching rules / undefined query).
        """
        if not self.policy_dir.exists():
            raise OPAEvalError(f"Policy dir does not exist: {self.policy_dir}")

        cmd = [
            str(self.binary),
            "eval",
            "-d", str(self.policy_dir),
            "-I",
            "--format", "json",
            query,
        ]
        try:
            proc = subprocess.run(
                cmd,
                input=json.dumps(input_doc),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as exc:
            raise OPABinaryNotFound(f"OPA binary disappeared: {self.binary}") from exc
        except subprocess.TimeoutExpired as exc:
            raise OPAEvalError("opa eval timed out after 30s") from exc

        if proc.returncode != 0:
            raise OPAEvalError(
                f"opa eval failed (exit {proc.returncode}): {proc.stderr.strip()}"
            )

        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise OPAEvalError(f"opa eval returned invalid JSON: {exc}") from exc

        results = parsed.get("result") or []
        if not results:
            return None
        expressions = results[0].get("expressions") or []
        if not expressions:
            return None
        return expressions[0].get("value")

    def violations(self, input_doc: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the list of violations from `data.fyp.violations`.

        Each violation is a dict with keys: rule, severity, vnf, message.
        """
        result = self.evaluate(input_doc, "data.fyp.violations")
        if result is None:
            return []
        if not isinstance(result, list):
            raise OPAEvalError(
                f"data.fyp.violations did not return a list, got: {type(result).__name__}"
            )
        return result
