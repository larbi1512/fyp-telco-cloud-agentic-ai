"""
LLM Core — Central reasoning engine for all agents.

Wraps Ollama (via langchain_ollama) and provides:
  - YAML-based prompt template loading
  - Structured JSON output parsing with retry
  - Agent-specific invocation interface
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from config.settings import (
    LLM_BACKEND,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_TEMPERATURE,
    OLLAMA_MAX_TOKENS,
    VLLM_BASE_URL,
    VLLM_MODEL,
    VLLM_API_KEY,
    VLLM_MAX_TOKENS,
    PROMPTS_DIR,
)

logger = logging.getLogger(__name__)


class LLMCore:
    """
    Centralised LLM interface used by every agent.

    Usage:
        llm = LLMCore()
        result = llm.invoke("network_planner", {"intent": "Deploy 5G core ..."})
    """

    def __init__(self) -> None:
        if LLM_BACKEND == "vllm":
            logger.info("Initializing LLMCore with vLLM Backend (OpenAI API)")
            self._model = ChatOpenAI(
                base_url=VLLM_BASE_URL,
                model=VLLM_MODEL,
                api_key=VLLM_API_KEY,
                temperature=OLLAMA_TEMPERATURE,
                max_tokens=VLLM_MAX_TOKENS,
            )
        else:
            logger.info("Initializing LLMCore with Ollama Backend")
            self._model = ChatOllama(
                base_url=OLLAMA_BASE_URL,
                model=OLLAMA_MODEL,
                temperature=OLLAMA_TEMPERATURE,
                num_predict=OLLAMA_MAX_TOKENS,
            )
        self._prompt_cache: dict[str, dict[str, str]] = {}

    # Prompt management

    def _load_prompt(self, agent_name: str) -> dict[str, str]:
        """Load and cache a prompt template from core/prompts/<agent>.yaml."""
        if agent_name in self._prompt_cache:
            return self._prompt_cache[agent_name]

        path = PROMPTS_DIR / f"{agent_name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Prompt template not found: {path}")

        with open(path, "r") as f:
            template = yaml.safe_load(f)

        # Expected keys: system_prompt, user_prompt_template
        required = {"system_prompt", "user_prompt_template"}
        if not required.issubset(template.keys()):
            raise ValueError(
                f"Prompt YAML for '{agent_name}' must contain: {required}"
            )

        self._prompt_cache[agent_name] = template
        return template

    def _render_prompt(
        self, agent_name: str, variables: dict[str, Any]
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) with variables interpolated."""
        template = self._load_prompt(agent_name)
        system = template["system_prompt"]
        user = template["user_prompt_template"]

        # Simple {variable} replacement
        for key, value in variables.items():
            placeholder = "{" + key + "}"
            str_value = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            system = system.replace(placeholder, str_value)
            user = user.replace(placeholder, str_value)

        return system, user

    # Core invocation 

    def invoke(
        self,
        agent_name: str,
        variables: dict[str, Any],
        expect_json: bool = True,
        max_retries: int = 2,
        max_tokens: int | None = None,
    ) -> dict[str, Any] | str:
        """
        Invoke the LLM with an agent-specific prompt.

        Args:
            agent_name:  Name matching a YAML file in core/prompts/
            variables:   Dict of values to interpolate into the prompt
            expect_json: If True, parse the response as JSON (with retry)
            max_retries: Number of retries if JSON parsing fails
            max_tokens:  Optional per-call output cap. Overrides the
                         default max_tokens (vLLM) / num_predict (Ollama).
                         Useful for agents like the Planner whose JSON
                         outputs are small and whose prompts are large.

        Returns:
            Parsed JSON dict or raw string depending on expect_json.
        """
        system_prompt, user_prompt = self._render_prompt(agent_name, variables)

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        # Resolve the model handle to use for this invocation. When the
        # caller supplies max_tokens we override the token limit.
        # For Ollama, bind() passes kwargs to Client.chat() which rejects
        # num_predict — so we create a fresh ChatOllama with num_predict set.
        # For vLLM (ChatOpenAI), bind(max_tokens=...) works fine.
        if max_tokens is not None:
            if LLM_BACKEND == "ollama":
                model_for_call = ChatOllama(
                    base_url=OLLAMA_BASE_URL,
                    model=OLLAMA_MODEL,
                    temperature=OLLAMA_TEMPERATURE,
                    num_predict=max_tokens,
                )
            else:
                model_for_call = self._model.bind(max_tokens=max_tokens)
        else:
            model_for_call = self._model

        for attempt in range(1, max_retries + 2):
            logger.info(
                "LLM invoke [%s] attempt %d/%d", agent_name, attempt, max_retries + 1
            )
            response = model_for_call.invoke(messages)
            raw_text: str = response.content

            if not expect_json:
                return raw_text

            parsed = self._extract_json(raw_text)
            if parsed is not None:
                return parsed

            # Append retry hint and try again
            logger.warning(
                "JSON parse failed for %s (attempt %d). Raw: %s",
                agent_name,
                attempt,
                raw_text[:300],
            )
            messages.append(HumanMessage(
                content=(
                    "Your previous response was not valid JSON. "
                    "Please respond with ONLY a valid JSON object, no extra text."
                )
            ))

        # All retries exhausted — return raw text
        logger.error("All %d JSON parse attempts failed for %s", max_retries + 1, agent_name)
        return raw_text

    def chat(self, system_prompt: str, user_message: str) -> str:
        """Simple chat call without prompt templates (used by CLI)."""
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]
        response = self._model.invoke(messages)
        return response.content

    # JSON extraction 

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        """
        Extract a JSON object from LLM output.
        Handles markdown code fences and leading/trailing text.
        """
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from ```json ... ``` blocks
        pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding first { ... } block
        brace_start = text.find("{")
        if brace_start != -1:
            depth = 0
            for i, ch in enumerate(text[brace_start:], start=brace_start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[brace_start : i + 1])
                        except json.JSONDecodeError:
                            break
        return None
