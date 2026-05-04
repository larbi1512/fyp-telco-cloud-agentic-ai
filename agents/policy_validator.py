from __future__ import annotations

import logging
from typing import Any

from core.llm_core import LLMCore
from core.state import OrchestratorState, ValidationReport

logger = logging.getLogger(__name__)


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


def run_static_policies(configs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Run all static policy checks and return a policy_checks dict."""
    return {
        "resource_limits": _check_resource_limits(configs),
        "root_containers": _check_root_containers(configs),
        "mandatory_fields": _check_mandatory_fields(configs),
        "image_tags": _check_image_tags(configs),
        "health_probes": _check_probes(configs),
    }


# Decision logic 


def determine_decision(
    policy_checks: dict[str, dict[str, Any]],
    llm_conflicts: list[str] | None = None,
) -> tuple[str, list[str]]:
    """
    Compute the final GO / NO_GO decision.

    Rules:
    - Any static check with status FAIL → NO_GO
    - LLM-reported critical conflicts → NO_GO
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

    # LLM-reported conflicts
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

    # ── Phase 1: Static policy checks (deterministic) ──────
    logger.info("Running static policy checks on %d config artifacts", len(config_artifacts))
    policy_checks = run_static_policies(config_artifacts)

    for check_name, result in policy_checks.items():
        logger.info("  %s: %s (%d violations)", check_name, result["status"], result.get("violations", 0))

    # ── Phase 2: LLM compliance analysis (optional) ────────
    llm_dry_run = {"status": "SKIPPED", "errors": []}
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

        # Build a simple policy rules summary for the LLM
        policy_rules_summary = {
            "static_check_results": {
                k: v["status"] for k, v in policy_checks.items()
            },
            "rules_checked": [
                "resource_limits: All VNFs must have CPU/memory limits",
                "root_containers: Containers should not run as root (WARNING level)",
                "mandatory_fields: nfimage, exposedPorts, start must be present",
                "image_tags: Should use pinned versions, not 'latest' or 'develop'",
                "health_probes: readinessProbe or livenessProbe should be set",
            ],
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
            llm_dry_run = llm_result.get("dry_run", {"status": "SUCCESS", "errors": []})
            llm_conflicts = llm_result.get("conflicts", [])
            llm_notes = llm_result.get("reasons", [])
            if llm_result.get("validation_notes"):
                llm_notes.extend(llm_result["validation_notes"])
            logger.info("LLM analysis: dry_run=%s, conflicts=%d",
                        llm_dry_run.get("status"), len(llm_conflicts))
        else:
            logger.warning("LLM returned non-JSON response (non-critical)")

    except Exception as e:
        logger.warning("LLM compliance analysis failed (non-critical): %s", e)
        llm_dry_run = {"status": "SKIPPED", "errors": [str(e)]}

    # Phase 3: Final decision 
    decision, reasons = determine_decision(policy_checks, llm_conflicts)
    logger.info("Policy Validator decision: %s", decision)

    # Aggregate violation counts across all checks (consumed by the experiment
    # harness to compute the "Policy violation rate" metric without re-walking
    # the per-check report).
    violation_count = sum(int(c.get("violations", 0)) for c in policy_checks.values())

    # Build ValidationReport
    validation_report: ValidationReport = {
        "topology_id": topology_id,
        "policy_checks": policy_checks,
        "dry_run": llm_dry_run,
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

    summary_lines.append(f"\n   Dry-run: {llm_dry_run.get('status', 'N/A')}")

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
