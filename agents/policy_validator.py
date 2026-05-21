from __future__ import annotations

import logging
from typing import Any

from core.llm_core import LLMCore
from core.state import OrchestratorState, ValidationReport
from infra.opa_client import OPAClient, OPABinaryNotFound, OPAEvalError
from infra.k8s_dry_run import K8sDryRunner

logger = logging.getLogger(__name__)


# Rules that originate in the Rego bundle (infra/opa/policies/). Used to seed
# the policy_checks dict so a clean OPA run still reports every check, not
# only the ones that produced a violation.
OPA_RULES: tuple[str, ...] = (
    "resource_limits",
    "mandatory_fields",
    "image_tags",
    "root_containers",
    "health_probes",
    "scaling_policy",
    "network_policy",
)


# Static Policy Checks 


def _check_resource_limits(configs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Ensure every VNF has resource limits defined.

    Checks:
    - resources.define is True
    - resources.requests.nf.cpu and .memory are present
    - resources.limits.nf.cpu and .memory are present
    """
    checked = 0
    violations = []

    for cfg in configs:
        vnf_name = cfg.get("vnf_name", "unknown")
        hv = cfg.get("helm_values", {})
        res = hv.get("resources", {})
        checked += 1

        if not res.get("define"):
            violations.append(f"{vnf_name}: resources.define is False — no resource limits will be applied")
            continue

        req = res.get("requests", {}).get("nf", {})
        lim = res.get("limits", {}).get("nf", {})

        if not req.get("cpu") or not req.get("memory"):
            violations.append(f"{vnf_name}: missing resource requests (cpu/memory)")
        if not lim.get("cpu") or not lim.get("memory"):
            violations.append(f"{vnf_name}: missing resource limits (cpu/memory)")

    if violations:
        return {
            "status": "FAIL",
            "checked": checked,
            "violations": len(violations),
            "details": "; ".join(violations),
        }
    return {
        "status": "PASS",
        "checked": checked,
        "violations": 0,
        "details": f"All {checked} VNFs have resource limits defined",
    }


def _check_root_containers(configs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Flag containers running as root (UID 0).

    OAI charts historically run as root, so this is a WARNING, not a FAIL.
    Only UPF with securityContext.privileged: true is expected.
    """
    checked = 0
    warnings = []

    for cfg in configs:
        vnf_name = cfg.get("vnf_name", "unknown")
        hv = cfg.get("helm_values", {})
        checked += 1

        # Check podSecurityContext
        psc = hv.get("podSecurityContext", {})
        if psc.get("runAsUser") == 0:
            warnings.append(f"{vnf_name}: runs as root (runAsUser=0)")

        # Check securityContext.privileged
        sc = hv.get("securityContext", {})
        if sc.get("privileged"):
            warnings.append(f"{vnf_name}: privileged container")

    if warnings:
        return {
            "status": "WARNING",
            "checked": checked,
            "violations": len(warnings),
            "details": "; ".join(warnings),
        }
    return {
        "status": "PASS",
        "checked": checked,
        "violations": 0,
        "details": f"No root/privileged containers in {checked} VNFs",
    }


def _check_mandatory_fields(configs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Ensure every VNF has mandatory Helm value fields.

    Checks: nfimage, exposedPorts, start flag.
    """
    checked = 0
    violations = []

    for cfg in configs:
        vnf_name = cfg.get("vnf_name", "unknown")
        hv = cfg.get("helm_values", {})
        checked += 1

        if "nfimage" not in hv:
            violations.append(f"{vnf_name}: missing nfimage")
        elif not hv["nfimage"].get("repository"):
            violations.append(f"{vnf_name}: nfimage.repository is empty")

        if "exposedPorts" not in hv:
            violations.append(f"{vnf_name}: missing exposedPorts")

        if "start" not in hv:
            violations.append(f"{vnf_name}: missing start flags")

    if violations:
        return {
            "status": "FAIL",
            "checked": checked,
            "violations": len(violations),
            "details": "; ".join(violations),
        }
    return {
        "status": "PASS",
        "checked": checked,
        "violations": 0,
        "details": f"All {checked} VNFs have mandatory fields",
    }


def _check_image_tags(configs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Warn if any VNF uses 'latest' or 'develop' image tags (non-reproducible).
    """
    checked = 0
    warnings = []

    for cfg in configs:
        vnf_name = cfg.get("vnf_name", "unknown")
        hv = cfg.get("helm_values", {})
        checked += 1

        tag = hv.get("nfimage", {}).get("version", "")
        if tag in ("latest", "develop", ""):
            warnings.append(f"{vnf_name}: using non-pinned image tag '{tag}'")

    if warnings:
        return {
            "status": "WARNING",
            "checked": checked,
            "violations": len(warnings),
            "details": "; ".join(warnings),
        }
    return {
        "status": "PASS",
        "checked": checked,
        "violations": 0,
        "details": f"All {checked} VNFs use pinned image tags",
    }


def _check_probes(configs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Check that readinessProbe and/or livenessProbe are enabled.
    """
    checked = 0
    warnings = []

    for cfg in configs:
        vnf_name = cfg.get("vnf_name", "unknown")
        hv = cfg.get("helm_values", {})
        checked += 1

        has_readiness = hv.get("readinessProbe", False)
        has_liveness = hv.get("livenessProbe", False)

        if not has_readiness and not has_liveness:
            warnings.append(f"{vnf_name}: no readiness or liveness probes")

    if warnings:
        return {
            "status": "WARNING",
            "checked": checked,
            "violations": len(warnings),
            "details": "; ".join(warnings),
        }
    return {
        "status": "PASS",
        "checked": checked,
        "violations": 0,
        "details": f"All {checked} VNFs have health probes configured",
    }


# Static check aggregation 


def _python_fallback(configs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Deterministic Python checks — used when the OPA binary is unavailable."""
    return {
        "resource_limits": _check_resource_limits(configs),
        "root_containers": _check_root_containers(configs),
        "mandatory_fields": _check_mandatory_fields(configs),
        "image_tags": _check_image_tags(configs),
        "health_probes": _check_probes(configs),
    }


def _opa_violations_to_check_dict(
    violations: list[dict[str, Any]],
    checked: int,
) -> dict[str, dict[str, Any]]:
    """Bucket OPA violations by `rule` field into the legacy policy_checks shape."""
    buckets: dict[str, dict[str, Any]] = {
        rule: {"status": "PASS", "checked": checked, "violations": 0, "details": ""}
        for rule in OPA_RULES
    }
    severity_to_status = {"FAIL": "FAIL", "WARNING": "WARNING", "INFO": "WARNING"}
    messages: dict[str, list[str]] = {rule: [] for rule in OPA_RULES}
    statuses: dict[str, set[str]] = {rule: set() for rule in OPA_RULES}

    for v in violations:
        rule = v.get("rule", "unknown")
        severity = v.get("severity", "WARNING").upper()
        status = severity_to_status.get(severity, "WARNING")

        # New rule that wasn't pre-seeded — accept it.
        if rule not in buckets:
            buckets[rule] = {"status": "PASS", "checked": checked, "violations": 0, "details": ""}
            messages[rule] = []
            statuses[rule] = set()

        msg = v.get("message", "")
        if msg:
            messages[rule].append(msg)
        statuses[rule].add(status)
        buckets[rule]["violations"] += 1

    for rule, bucket in buckets.items():
        if "FAIL" in statuses[rule]:
            bucket["status"] = "FAIL"
        elif "WARNING" in statuses[rule]:
            bucket["status"] = "WARNING"
        if messages[rule]:
            bucket["details"] = "; ".join(messages[rule])
        elif bucket["status"] == "PASS":
            bucket["details"] = f"All {checked} VNFs passed"

    return buckets


def run_static_policies(configs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Evaluate the OPA Rego bundle, falling back to Python checks on failure."""
    try:
        client = OPAClient()
        violations = client.violations({"config_artifacts": configs})
        logger.info(
            "OPA evaluated %d artifacts via %s → %d violation(s)",
            len(configs),
            client.binary,
            len(violations),
        )
        return _opa_violations_to_check_dict(violations, checked=len(configs))
    except OPABinaryNotFound as exc:
        logger.warning("OPA unavailable, using Python fallback: %s", exc)
    except OPAEvalError as exc:
        logger.warning("OPA evaluation failed, using Python fallback: %s", exc)
    return _python_fallback(configs)


# Decision logic 


def determine_decision(
    policy_checks: dict[str, dict[str, Any]],
    llm_conflicts: list[str] | None = None,
    dry_run: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """
    Compute the final GO / NO_GO decision.

    Rules:
    - Any static check with status FAIL → NO_GO
    - K8s dry-run with status FAILED → NO_GO
    - LLM-reported critical conflicts → NO_GO
    - Dry-run SKIPPED → noted but doesn't block (cluster unreachable)
    - Warnings are noted but don't block
    """
    reasons: list[str] = []
    has_fail = False

    for check_name, result in policy_checks.items():
        status = result.get("status", "PASS")
        if status == "FAIL":
            has_fail = True
            reasons.append(f"FAIL: {check_name} — {result.get('details', '')}")
        elif status == "WARNING":
            reasons.append(f"WARNING: {check_name} — {result.get('details', '')}")

    if dry_run:
        dr_status = dry_run.get("status", "SKIPPED")
        if dr_status == "FAILED":
            has_fail = True
            err_msgs = [e.get("message", "") for e in dry_run.get("errors", [])[:3]]
            reasons.append(
                f"FAIL: k8s_dry_run — {len(dry_run.get('errors', []))} error(s): "
                + "; ".join(m for m in err_msgs if m)
            )
        elif dr_status == "SKIPPED":
            reasons.append(
                f"NOTE: k8s_dry_run SKIPPED — {dry_run.get('reason', 'unreachable')}"
            )

    if llm_conflicts:
        for conflict in llm_conflicts:
            conflict_lower = conflict.lower()
            if any(kw in conflict_lower for kw in ("critical", "blocking", "fail")):
                has_fail = True
                reasons.append(f"FAIL (LLM): {conflict}")
            else:
                reasons.append(f"NOTE (LLM): {conflict}")

    decision = "NO_GO" if has_fail else "GO"
    if not reasons:
        reasons.append("All checks passed")

    return decision, reasons


# Agent function (LangGraph node) 


def policy_validator_agent(state: OrchestratorState) -> dict[str, Any]:
    """
    LangGraph node: Policy Validator.

    Reads:  state["config_artifacts"], state["topology"]
    Writes: state["validation_report"], state["messages"], state["current_agent"]
    """
    logger.info("Policy Validator Agent: starting")

    config_artifacts = state.get("config_artifacts")

    if not config_artifacts:
        return {
            "error": "No configuration artifacts provided — run VNF Configurator first",
            "messages": [{"role": "agent", "content": "❌ No config artifacts available for validation."}],
            "current_agent": "policy_validator",
        }

    # Determine topology_id from upstream data
    topology = state.get("topology", {}) or {}
    resource_alloc = state.get("resource_allocation", {}) or {}
    topology_id = resource_alloc.get("topology_id", topology.get("topology_id", "unknown"))

    # ── Phase 1: Static policy checks via OPA (deterministic) ──
    logger.info("Running OPA Rego checks on %d config artifacts", len(config_artifacts))
    policy_checks = run_static_policies(config_artifacts)

    for check_name, result in policy_checks.items():
        logger.info("  %s: %s (%d violations)", check_name, result["status"], result.get("violations", 0))

    # ── Phase 2: Real Kubernetes server-side dry-run ───────────
    logger.info("Running Kubernetes server-side dry-run for %d artifacts", len(config_artifacts))
    dry_run_result = K8sDryRunner(config_artifacts).run()
    logger.info(
        "Dry-run: status=%s, validated=%d, errors=%d",
        dry_run_result.get("status"),
        dry_run_result.get("validated", 0),
        len(dry_run_result.get("errors", [])),
    )

    # ── Phase 3: LLM semantic analysis (cross-VNF / 5G) ────────
    llm_conflicts: list[str] = []
    llm_notes: list[str] = []

    try:
        llm = LLMCore()

        # Summarise configs for the LLM (avoid sending full Helm values)
        config_summary = []
        for cfg in config_artifacts:
            hv = cfg.get("helm_values", {})
            res = hv.get("resources", {})
            config_summary.append({
                "vnf_name": cfg.get("vnf_name"),
                "image": hv.get("nfimage", {}).get("version"),
                "ports": hv.get("exposedPorts", {}),
                "resources_defined": res.get("define", False),
                "requests": res.get("requests", {}).get("nf", {}),
                "privileged": hv.get("securityContext", {}).get("privileged", False),
                "multus": bool(hv.get("multus")),
            })

        # Also send core ConfigMap if available
        core_config = {}
        for cfg in config_artifacts:
            cm = cfg.get("config_maps", {})
            if cm.get("core_network"):
                core_config = cm["core_network"]
                break

        # Summary of OPA results so the LLM can focus on cross-VNF semantics.
        policy_rules_summary = {
            "opa_results": {k: v["status"] for k, v in policy_checks.items()},
            "dry_run_status": dry_run_result.get("status"),
            "rules_checked": list(OPA_RULES),
        }

        llm_result = llm.invoke(
            "policy_validator",
            {
                "config_artifacts": config_summary,
                "policy_rules": policy_rules_summary,
                "cluster_state": {
                    "topology_id": topology_id,
                    "core_config_present": bool(core_config),
                    "vnf_count": len(config_artifacts),
                },
            },
            expect_json=True,
        )

        if isinstance(llm_result, dict):
            llm_conflicts = llm_result.get("conflicts", []) or []
            llm_notes = llm_result.get("reasons", []) or []
            if llm_result.get("validation_notes"):
                llm_notes.extend(llm_result["validation_notes"])
            logger.info("LLM semantic analysis: conflicts=%d, notes=%d",
                        len(llm_conflicts), len(llm_notes))
        else:
            logger.warning("LLM returned non-JSON response (non-critical)")

    except Exception as e:
        logger.warning("LLM compliance analysis failed (non-critical): %s", e)

    # Phase 4: Final decision
    decision, reasons = determine_decision(
        policy_checks, llm_conflicts, dry_run_result
    )
    logger.info("Policy Validator decision: %s", decision)

    # Aggregate violation counts across all checks (consumed by the experiment
    # harness to compute the "Policy violation rate" metric without re-walking
    # the per-check report).
    violation_count = sum(int(c.get("violations", 0)) for c in policy_checks.values())

    # Build ValidationReport
    validation_report: ValidationReport = {
        "topology_id": topology_id,
        "policy_checks": policy_checks,
        "dry_run": dry_run_result,
        "conflicts": llm_conflicts,
        "decision": decision,
        "reasons": reasons,
        "violation_count": violation_count,
    }

    # Build summary message
    icon = "PASS" if decision == "GO" else "FAIL"
    summary_lines = [
        f"{icon} **Policy Validation: {decision}** (Topology: {topology_id})",
        "",
        "   | Check | Status | Violations | Details |",
        "   |-------|--------|------------|---------|",
    ]
    for check_name, result in policy_checks.items():
        status_icon = {"PASS": "", "WARNING": "", "FAIL": ""}.get(result["status"], "❓")
        summary_lines.append(
            f"   | {check_name} | {status_icon} {result['status']} | {result.get('violations', 0)} | {result.get('details', '')[:80]} |"
        )

    dr_status = dry_run_result.get("status", "N/A")
    dr_extra = (
        f" — {dry_run_result.get('reason', '')}"
        if dr_status in ("SKIPPED", "FAILED") and dry_run_result.get("reason")
        else ""
    )
    summary_lines.append(
        f"\n   K8s dry-run: {dr_status} "
        f"(validated {dry_run_result.get('validated', 0)} manifests){dr_extra}"
    )

    if llm_conflicts:
        summary_lines.append(f"   Conflicts: {'; '.join(llm_conflicts[:3])}")
    if llm_notes:
        summary_lines.append(f"   Notes: {'; '.join(str(n) for n in llm_notes[:3])}")

    summary_lines.append(f"\n   **Decision: {decision}**")
    if decision == "NO_GO":
        fail_reasons = [r for r in reasons if r.startswith("FAIL")]
        summary_lines.append(f"   Blocking reasons: {'; '.join(fail_reasons)}")

    summary = "\n".join(summary_lines)

    return {
        "validation_report": validation_report,
        "current_agent": "policy_validator",
        "messages": [{"role": "agent", "content": summary}],
    }
