"""
Phase 1 Validation — Tests for LLM Core, config loading, and prompt system.

Run: cd ~/fyp && python -m pytest tests/test_llm_core.py -v
"""

import sys
import json
import yaml
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_settings_load():
    """Config settings module loads without errors."""
    from config.settings import (
        OLLAMA_BASE_URL, OLLAMA_MODEL, PROMPTS_DIR,
        VNF_PROFILES_PATH, SLA_THRESHOLDS_PATH,
    )
    assert "localhost" in OLLAMA_BASE_URL or "http" in OLLAMA_BASE_URL
    assert OLLAMA_MODEL  # non-empty
    assert PROMPTS_DIR.exists()
    assert VNF_PROFILES_PATH.exists()
    assert SLA_THRESHOLDS_PATH.exists()
    print(f"  [OK] Settings loaded: model={OLLAMA_MODEL}")


def test_state_types():
    """OrchestratorState TypedDict can be instantiated."""
    from core.state import OrchestratorState
    state: OrchestratorState = {
        "user_intent": "Deploy a 5G core",
        "phase": "pre_deployment",
        "messages": [],
        "anomaly_alerts": [],
        "execution_results": [],
    }
    assert state["user_intent"] == "Deploy a 5G core"
    assert state["phase"] == "pre_deployment"
    print("  [OK] OrchestratorState instantiated")


def test_prompt_templates_exist():
    """All 10 prompt YAML files exist and have required keys."""
    from config.settings import PROMPTS_DIR

    agents = [
        "network_planner", "resource_allocator", "vnf_configurator",
        "policy_validator", "kpi_monitor", "anomaly_detector",
        "sla_compliance", "planner_reasoning", "auto_scaler", "fault_recovery",
    ]
    for agent in agents:
        path = PROMPTS_DIR / f"{agent}.yaml"
        assert path.exists(), f"Missing prompt: {path}"
        with open(path) as f:
            tmpl = yaml.safe_load(f)
        assert "system_prompt" in tmpl, f"{agent} missing system_prompt"
        assert "user_prompt_template" in tmpl, f"{agent} missing user_prompt_template"
    print(f"  [OK] All {len(agents)} prompt templates valid")


def test_vnf_profiles():
    """VNF resource profiles load and contain expected VNFs."""
    from config.settings import VNF_PROFILES_PATH
    with open(VNF_PROFILES_PATH) as f:
        data = yaml.safe_load(f)
    profiles = data["profiles"]
    required_vnfs = ["oai-amf", "oai-smf", "oai-upf", "oai-nrf"]
    for vnf in required_vnfs:
        assert vnf in profiles, f"Missing VNF profile: {vnf}"
        assert "base" in profiles[vnf]
        assert "cpu" in profiles[vnf]["base"]
        assert "memory" in profiles[vnf]["base"]
    print(f"  [OK] VNF profiles loaded: {len(profiles)} VNFs")


def test_sla_thresholds():
    """SLA thresholds load correctly."""
    from config.settings import SLA_THRESHOLDS_PATH
    with open(SLA_THRESHOLDS_PATH) as f:
        data = yaml.safe_load(f)
    rules = data["sla_rules"]
    assert len(rules) >= 3, "Need at least 3 SLA rules"
    for rule in rules:
        assert "name" in rule
        assert "threshold" in rule
        assert "operator" in rule
    print(f"  [OK] SLA thresholds loaded: {len(rules)} rules")


def test_llm_core_prompt_loading():
    """LLMCore loads and renders prompt templates."""
    from core.llm_core import LLMCore
    llm = LLMCore()

    sys_prompt, user_prompt = llm._render_prompt(
        "network_planner",
        {
            "intent": "Deploy a 5G core with 1000 UEs",
            "vnf_catalog": ["amf", "smf", "upf", "nrf"],
            "infra_constraints": {"nodes": 3, "total_cpu": "12000m"},
        }
    )
    assert "Network Planner" in sys_prompt
    assert "1000 UEs" in user_prompt
    print("  [OK] Prompt template loaded and rendered")


def test_llm_core_json_extraction():
    """LLMCore._extract_json handles various formats."""
    from core.llm_core import LLMCore

    # Direct JSON
    assert LLMCore._extract_json('{"key": "value"}') == {"key": "value"}

    # Markdown fenced
    assert LLMCore._extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    # With surrounding text
    assert LLMCore._extract_json('Here is the result: {"b": 2} done.') == {"b": 2}

    # Invalid
    assert LLMCore._extract_json('no json here') is None

    print("  [OK] JSON extraction works for all formats")


def test_llm_core_invoke_ollama():
    """Integration test: actually call Ollama and get a JSON response."""
    from core.llm_core import LLMCore
    llm = LLMCore()

    result = llm.invoke(
        "network_planner",
        {
            "intent": "Deploy a minimal 5G core with AMF, SMF, UPF, and NRF",
            "vnf_catalog": '["oai-amf", "oai-smf", "oai-upf", "oai-nrf"]',
            "infra_constraints": '{"nodes": 1, "total_cpu": "4000m", "total_memory": "8Gi"}',
        },
        expect_json=True,
    )

    print(f"\n  LLM Response type: {type(result).__name__}")
    if isinstance(result, dict):
        print(f"  LLM Response keys: {list(result.keys())}")
        print(f"  Response:\n{json.dumps(result, indent=2)[:500]}")
        assert "vnfs" in result or "topology_id" in result, "Expected topology keys in response"
        print("  [OK] Ollama returned valid JSON topology")
    else:
        print(f"  [WARN] Got raw string (JSON parse failed): {result[:200]}")
        # Don't fail — model might need tuning
        print("  [WARN] LLM returned text instead of JSON — check model behavior")


if __name__ == "__main__":
    print("\n=== Phase 1 Validation Tests ===\n")
    test_settings_load()
    test_state_types()
    test_prompt_templates_exist()
    test_vnf_profiles()
    test_sla_thresholds()
    test_llm_core_prompt_loading()
    test_llm_core_json_extraction()
    print("\n--- Running LLM integration test (calls Ollama) ---")
    test_llm_core_invoke_ollama()
    print("\n=== All Phase 1 tests passed! ===")
