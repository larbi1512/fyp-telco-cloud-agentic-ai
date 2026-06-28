"""
Shared resource-sizing reference for the stronger baselines.

The `resource_accuracy` metric scores per-VNF requests against an oracle that
applies the scaling formulas in config/vnf_resource_profiles.yaml to the intent's
load. A raw LLM with no sizing guidance emits generic oversized defaults and
scores ~0. To make the stronger baselines (confucius, ossgpt, lin) *competitive*
— and faithful to their papers, which all assume access to tool documentation /
API schemas / retrieved system state — we expose the SAME profile table + formula
+ this deployment's load as a reference block injected into their generation
prompts. The LLM still computes the numbers (no oracle output is leaked), so any
residual gap vs. MAS reflects LLM arithmetic noise rather than missing knowledge.

B4 (the naive single-LLM baseline) deliberately does NOT get this block, so the
ablation "naive LLM → poor sizing" is preserved.
"""

from __future__ import annotations

from typing import Any

from experiments.common.resource_oracle import VNF_TYPE_TO_PROFILE, _load_profiles

# Load features worth surfacing to the model (the formula inputs).
_LOAD_FEATURES = [
    "ue_count",
    "throughput_gbps",
    "session_count",
    "subscriber_count",
    "slices",
    "ha_required",
]


def resource_sizing_reference(intent: dict[str, Any]) -> str:
    """Build the per-VNF sizing reference block for an intent (kept in sync with
    the oracle by reading the same profiles YAML)."""
    profiles = _load_profiles()
    feat = intent.get("structured_features") or {}

    lines: list[str] = [
        "RESOURCE SIZING REFERENCE",
        "Compute each VNF's resources.requests by applying the formula below to THIS "
        "deployment's load. resources.requests is NOT a safety reservation — it is "
        "exactly base + scaling. Put ALL safety headroom in resources.limits (= 1.5x "
        "requests). Do NOT round requests up to 'safe' values; at low load most VNFs "
        "should sit at or very near their base values.",
        "Formula: requests = base + (load_units / unit) * per_unit_increment, capped at max.",
        "",
        "Worked example (DIFFERENT load — for method only): at 2000 UE, 2 Gbps:",
        "  amf  requests = 100m + (2000/1000)*200m = 500m cpu; 256Mi + (2000/1000)*100Mi = 456Mi; limits = 750m/684Mi",
        "  upf  requests = 500m + (2/1)*800m = 2100m cpu; 512Mi + (2/1)*256Mi = 1024Mi; limits = 3150m/1536Mi",
        "",
        "This deployment's load:",
    ]
    for k in _LOAD_FEATURES:
        v = feat.get(k)
        if k == "slices" and isinstance(v, list):
            v = len(v)
        if v is not None:
            lines.append(f"  - {k}: {v}")

    lines.append("")
    lines.append("Per-VNF profiles (cpu in millicores 'm'; memory in 'Mi'/'Gi'):")
    for vtype, pname in VNF_TYPE_TO_PROFILE.items():
        p = profiles.get(pname)
        if not p:
            continue
        base = p.get("base", {})
        mx = p.get("max", {})
        scaling = p.get("scaling", {})
        if scaling:
            key = next(iter(scaling))
            sv = scaling[key]
            sc_desc = f"+{sv.get('cpu')} cpu / +{sv.get('memory')} mem per [{key}]"
        else:
            sc_desc = "no load scaling"
        lines.append(
            f"  - {vtype}: base {base.get('cpu')}/{base.get('memory')}; "
            f"{sc_desc}; max {mx.get('cpu')}/{mx.get('memory')}"
        )

    lines.append("")
    lines.append(
        "If ha_required is true, size ~1.5x more conservatively. Compute each VNF's "
        "numbers from its own profile and the load above — do not reuse a single "
        "generic value across VNFs."
    )
    return "\n".join(lines)
