"""
LLM configuration registry for Experiment 3.

Each entry defines how to configure LLMCore for a specific model.
Keys are stable short IDs used as the 'system' field in runs.csv.
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# All LLMs under comparison
LLM_CONFIGS: dict[str, dict] = {
    # ── Small models (≤ 7B) ──────────────────────────────────────────── #
    "llama32_3b": {
        "name": "Llama 3.2 3B",
        "backend": "ollama",
        "model": "llama3.2:3b",
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "size_b": 3,
        "description": "Meta's small open model — lower bound of usability",
    },
    "gemma3_4b": {
        "name": "Gemma 3 4B",
        "backend": "ollama",
        "model": "gemma3:4b",
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "size_b": 4,
        "description": "Google's compact 4B instruction-tuned model",
    },
    "deepseek_r1_7b": {
        "name": "DeepSeek-R1 7B",
        "backend": "ollama",
        "model": "deepseek-r1:7b",
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "size_b": 7,
        "description": "Reasoning-tuned 7B distillation of DeepSeek-R1",
    },
    "qwen25_7b": {
        "name": "Qwen2.5 7B",
        "backend": "ollama",
        "model": "qwen2.5:7b",
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "size_b": 7,
        "description": "Small 7B general-purpose model",
    },
    # ── Medium models (14–30B) ─────────────────────────────────────────── #
    "qwen25_14b": {
        "name": "Qwen2.5 14B",
        "backend": "ollama",
        "model": "qwen2.5:14b",
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "size_b": 14,
        "description": "Mid-size general-purpose model",
    },
    "mistral22b": {
        "name": "Mistral-Small 3.1",
        "backend": "ollama",
        "model": "mistral-small3.1:latest",
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "size_b": 22,
        "description": "Mid-size instruction-tuned model",
    },
    "qwen_coder30b": {
        "name": "Qwen3-Coder 30B",
        "backend": "vllm",
        "model": os.getenv("VLLM_MODEL", "Qwen3-Coder-30B-A3B-Instruct"),
        "base_url": os.getenv("VLLM_BASE_URL", "http://localhost:8123/v1"),
        "api_key": os.getenv("VLLM_API_KEY", "EMPTY"),
        "size_b": 30,
        "description": "Coding-specialized 30B MoE model via local vLLM",
    },
    # ── Large models (≥ 100B) ──────────────────────────────────────────── #
    "gpt120b": {
        "name": "GPT-OSS 120B",
        "backend": "ollama",
        "model": "gpt-oss:120b",
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "size_b": 120,
        "description": "Large open-source 120B model",
    },
}

# Ordered by parameter count for consistent table/chart ordering
LLM_ORDER = [
    "llama32_3b",
    "gemma3_4b",
    "deepseek_r1_7b",
    "qwen25_7b",
    "qwen25_14b",
    "mistral22b",
    "qwen_coder30b",
    "gpt120b",
]

# Smoke-test subset: 4 intents covering each complexity level
SMOKE_INTENT_IDS = ["S01", "M01", "M03", "C03"]
