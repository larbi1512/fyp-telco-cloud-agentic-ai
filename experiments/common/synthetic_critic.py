"""
Synthetic critic — plays the role of an experienced operator who reviews the
artifact at each HITL gate and decides whether they would approve it.

Drives two metrics:
  - Human interventions (#): every "would_approve = False" verdict counts as
    an intervention that a real operator would have raised.
  - Intent-to-deploy accuracy: the critic's approval is one of the four
    AND-clauses in the composite definition.

Design principles:
  1. Deterministic checklist runs first and is the source of truth for
     fundamentals (slice coverage, mandatory fields beyond the policy
     validator's 5, required VNFs given the slices, HA replica hint, autoscale
     hint when the intent demands it). The checklist alone is enough to
     produce a verdict.
  2. An optional LLM rationale runs *only when the checklist passes* and adds
     a sanity layer for things rules cannot easily encode (e.g. naming sanity).
     Disabled by default to keep experiments fast and reproducible; toggle via
     SYNTHETIC_CRITIC_USE_LLM=1.
  3. The LLM, when used, is forced through the vLLM Qwen3 fallback so it is a
     *different* model from the gpt-oss planner that produced the artifact.
     This reduces model-self-bias.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


REQUIRED_CORE_VNFS = {"amf", "smf", "upf", "nrf"}

# A slice mix that includes anything beyond plain eMBB requires NSSF in the
# topology to manage selection.
SLICE_REQUIRES_NSSF_THRESHOLD = 1  # >1 distinct slice → NSSF required


_KNOWN_SHORT_TYPES = {"amf", "smf", "upf", "nrf", "ausf", "udm", "udr", "nssf", "pcf"}


def _vnf_types(topology: dict[str, Any]) -> set[str]:
    out = set()
    for v in topology.get("vnfs") or []:
        raw_type = (v.get("type") or "").lower().replace("oai-", "")
        # If type is a known short code, use it; else fall back to name
        # (guards against small models emitting verbose type strings)
        if raw_type in _KNOWN_SHORT_TYPES:
            out.add(raw_type)
        else:
            t = (v.get("name") or "").lower().replace("oai-", "")
            if t:
                out.add(t)
    return out


def _slice_count(topology: dict[str, Any]) -> int:
    return len(topology.get("connectivity", {}).get("slices") or [])


def _max_replicas(topology: dict[str, Any]) -> int:
    return max(
        (int(v.get("replicas") or 1) for v in topology.get("vnfs") or []),
        default=1,
    )


def _checklist(
    topology: dict[str, Any],
    config_artifacts: list[dict[str, Any]],
    validation_report: dict[str, Any],
    intent_features: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run the deterministic rule set. Returns one verdict per rule."""
    findings: list[dict[str, Any]] = []
    vnf_types = _vnf_types(topology)
    slices = _slice_count(topology)
    max_replicas = _max_replicas(topology)
    feat = intent_features or {}

    # 1. Required core VNFs are present
    missing_core = REQUIRED_CORE_VNFS - vnf_types
    findings.append({
        "rule": "required_core_vnfs",
        "ok": not missing_core,
        "detail": f"missing core VNFs: {sorted(missing_core)}" if missing_core
                  else f"all required core VNFs present ({sorted(REQUIRED_CORE_VNFS)})",
    })

    # 2. Multi-slice intents must include NSSF
    feat_slices = feat.get("slices") or []
    intent_slice_count = len(feat_slices)
    needs_nssf = intent_slice_count > SLICE_REQUIRES_NSSF_THRESHOLD
    has_nssf = "nssf" in vnf_types
    findings.append({
        "rule": "nssf_when_multislice",
        "ok": (not needs_nssf) or has_nssf,
        "detail": (
            f"intent has {intent_slice_count} slices; NSSF "
            f"{'present' if has_nssf else 'MISSING'}"
        ),
    })

    # 3. HA intent → at least one VNF should have replicas > 1
    if feat.get("ha_required"):
        findings.append({
            "rule": "ha_replicas",
            "ok": max_replicas > 1,
            "detail": f"intent demands HA; max replicas across VNFs = {max_replicas}",
        })

    # 4. Autoscale intent → artifact should mention autoscaling somewhere
    #    (replicas range hint, an autoscaling block in any helm_values, or
    #    sla.autoscale flag).
    if feat.get("autoscale_required"):
        autoscale_signal = False
        for v in topology.get("vnfs") or []:
            if "autoscale" in str(v).lower() or "hpa" in str(v).lower():
                autoscale_signal = True
                break
        if not autoscale_signal:
            for cfg in config_artifacts or []:
                if "autoscaling" in (cfg.get("helm_values") or {}):
                    autoscale_signal = True
                    break
        if not autoscale_signal:
            sla = topology.get("sla") or {}
            if sla.get("autoscale") or sla.get("scaling"):
                autoscale_signal = True
        findings.append({
            "rule": "autoscale_signal",
            "ok": autoscale_signal,
            "detail": "autoscaling/HPA hint "
                      + ("found" if autoscale_signal else "MISSING"),
        })

    # 5. Slice count in topology should match (or exceed) intent slices —
    #    the planner can choose to expand but should not silently collapse.
    if intent_slice_count:
        findings.append({
            "rule": "slice_count_match",
            "ok": slices >= intent_slice_count,
            "detail": f"topology slices={slices}, intent slices={intent_slice_count}",
        })

    # 6. Naming sanity: every VNF has a non-empty name
    bad_names = [
        v.get("name", "<unnamed>")
        for v in topology.get("vnfs") or []
        if not v.get("name") or not isinstance(v.get("name"), str)
    ]
    findings.append({
        "rule": "vnf_naming",
        "ok": not bad_names,
        "detail": "all VNFs have names" if not bad_names
                  else f"{len(bad_names)} VNFs without names",
    })

    # 7. Validation decision must be GO
    decision = (validation_report or {}).get("decision")
    findings.append({
        "rule": "policy_go",
        "ok": decision == "GO",
        "detail": f"policy decision = {decision}",
    })

    # 8. Config artifacts present, one per VNF
    artifact_count = len(config_artifacts or [])
    vnf_count = len(topology.get("vnfs") or [])
    findings.append({
        "rule": "artifact_completeness",
        "ok": artifact_count >= vnf_count > 0,
        "detail": f"{artifact_count} config artifacts vs {vnf_count} VNFs",
    })

    return findings


def _llm_sanity_check(
    intent_text: str,
    topology: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Optional second-opinion LLM pass using the vLLM (Qwen3) backend so it is a
    different model from the planner. Disabled unless SYNTHETIC_CRITIC_USE_LLM=1.

    Returns {"would_approve": bool, "reason": str} or None on failure / disabled.
    """
    if os.getenv("SYNTHETIC_CRITIC_USE_LLM", "0") != "1":
        return None

    try:
        # Force the vLLM backend by temporarily flipping the env. We do not
        # mutate global config; we instantiate a fresh ChatOpenAI directly.
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, SystemMessage

        from config.settings import VLLM_BASE_URL, VLLM_MODEL, VLLM_API_KEY

        model = ChatOpenAI(
            base_url=VLLM_BASE_URL,
            model=VLLM_MODEL,
            api_key=VLLM_API_KEY,
            temperature=0.0,
            max_tokens=512,
        )

        sys_msg = SystemMessage(content=(
            "You are an experienced 5G core operator reviewing an AI-generated "
            "topology before deployment. Reply with ONLY a JSON object of the "
            "form {\"would_approve\": true|false, \"reason\": \"<one sentence>\"}. "
            "Reject only if a real operator would push back; minor stylistic "
            "issues are not grounds for rejection."
        ))
        user_msg = HumanMessage(content=(
            f"Intent: {intent_text}\n\n"
            f"Topology summary: {len(topology.get('vnfs') or [])} VNFs, "
            f"slices={_slice_count(topology)}, "
            f"vnf_types={sorted(_vnf_types(topology))}."
        ))
        resp = model.invoke([sys_msg, user_msg])
        from core.llm_core import LLMCore
        parsed = LLMCore._extract_json(resp.content)
        if not isinstance(parsed, dict):
            return None
        return {
            "would_approve": bool(parsed.get("would_approve", True)),
            "reason": str(parsed.get("reason", ""))[:300],
        }
    except Exception as e:
        logger.warning("synthetic_critic LLM sanity check failed: %s", e)
        return None


def critic_verdict(
    intent_text: str,
    topology: dict[str, Any] | None,
    config_artifacts: list[dict[str, Any]] | None,
    validation_report: dict[str, Any] | None,
    intent_features: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Top-level entry. Returns:

        {
          "would_approve": bool,
          "reasons": list[str],               # human-readable failure reasons
          "checklist": list[dict],            # full per-rule findings
          "llm_opinion": dict | None,         # only if SYNTHETIC_CRITIC_USE_LLM=1
        }
    """
    topology = topology or {}
    config_artifacts = config_artifacts or []
    validation_report = validation_report or {}
    intent_features = intent_features or {}

    findings = _checklist(topology, config_artifacts, validation_report, intent_features)
    failed = [f for f in findings if not f["ok"]]
    would_approve = not failed

    llm = _llm_sanity_check(intent_text, topology) if would_approve else None
    if llm is not None and not llm["would_approve"]:
        would_approve = False
        failed.append({"rule": "llm_sanity", "ok": False, "detail": llm["reason"]})

    return {
        "would_approve": would_approve,
        "reasons": [f["detail"] for f in failed],
        "checklist": findings,
        "llm_opinion": llm,
    }
