"""
Runner Protocol + the MAS runner.

A `BaselineRunner` is anything that takes an intent dict (one row from
intents_v2.json) and returns a RunArtifact (the dict consumed by metrics.score).

This module ships the MAS runner. Each baseline (B1–B4) lives under
experiments/baselines/ and registers itself in the RUNNERS dict at the bottom.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Protocol

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END

from agents import (
    network_planner_agent,
    policy_validator_agent,
    resource_allocator_agent,
    vnf_configurator_agent,
)
from agents.deployer import CHART_MAP
from core.state import OrchestratorState

# Re-use the existing automated graph builder from Experiment 1 — same MAS,
# same auto-approval pattern, but we now also drop interventions into
# state.intervention_log via a thin shim.
from experiments.experiment_1.run_experiment import auto_approve, build_automated_graph


def _build_no_deploy_graph() -> Any:
    """Same MAS pre-deployment pipeline but stops before the Deployer node.
    Used when the harness is in --no-deploy mode (smoke tests, fast iteration).
    """
    builder = StateGraph(OrchestratorState)
    builder.add_node("network_planner", network_planner_agent)
    builder.add_node("topology_review_gate", auto_approve)
    builder.add_node("resource_allocator", resource_allocator_agent)
    builder.add_node("vnf_configurator", vnf_configurator_agent)
    builder.add_node("policy_validator", policy_validator_agent)
    builder.add_edge(START, "network_planner")
    builder.add_edge("network_planner", "topology_review_gate")
    builder.add_edge("topology_review_gate", "resource_allocator")
    builder.add_edge("resource_allocator", "vnf_configurator")
    builder.add_edge("vnf_configurator", "policy_validator")
    builder.add_edge("policy_validator", END)
    return builder.compile(checkpointer=MemorySaver())

logger = logging.getLogger(__name__)


class RunArtifact(dict):
    """Just a dict alias for now; documents the expected shape."""


class BaselineRunner(Protocol):
    name: str

    def run(self, intent: dict[str, Any], *, deploy: bool = True) -> RunArtifact: ...


# MAS runner

class MASRunner:
    """Runs the existing LangGraph multi-agent system."""

    name = "mas"

    def __init__(self) -> None:
        # Build both variants once — pick at run time based on the deploy flag.
        self._graph_with_deploy, self._memory = build_automated_graph()
        self._graph_no_deploy = _build_no_deploy_graph()

    def run(self, intent: dict[str, Any], *, deploy: bool = True) -> RunArtifact:
        thread_id = str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        graph = self._graph_with_deploy if deploy else self._graph_no_deploy

        initial_state: OrchestratorState = {
            "user_intent": intent["prompt"],
            "clean_deploy": deploy,
            "requires_approval": False,
            "messages": [],
            "intervention_log": [],
            "intent_features": intent.get("structured_features") or {},
        }

        start = time.time()
        error: str | None = None
        try:
            for _ in graph.stream(initial_state, config, stream_mode="values"):
                pass
            state_values = graph.get_state(config).values
        except Exception as e:
            logger.error("MASRunner failed for intent %s: %s", intent.get("id"), e)
            error = str(e)
            state_values = {}

        wall_clock_s = time.time() - start

        return RunArtifact({
            "system": self.name,
            "intent_id": intent.get("id"),
            "run_id": thread_id,
            "topology": state_values.get("topology") or {},
            "resource_allocation": state_values.get("resource_allocation") or {},
            "config_artifacts": state_values.get("config_artifacts") or [],
            "validation_report": state_values.get("validation_report") or {},
            "deployment_results": state_values.get("deployment_results") or [],
            "intervention_log": state_values.get("intervention_log") or [],
            "wall_clock_s": wall_clock_s,
            "deployment_wait_s": None,  # populated by run_comparative.py if it waits for pods
            "release_names": list(CHART_MAP.values()),
            "error": error or state_values.get("error"),
        })


# Registry — baselines append themselves at import time

RUNNERS: dict[str, BaselineRunner] = {}


def register(runner: BaselineRunner) -> None:
    RUNNERS[runner.name] = runner


def get_runner(name: str) -> BaselineRunner:
    if name not in RUNNERS:
        # lazy-import baselines so this module can be loaded standalone for tests
        try:  # pragma: no cover - imported only when used
            import experiments.baselines  # noqa: F401  (registers all baselines)
        except ImportError:
            pass
    if name not in RUNNERS:
        raise KeyError(f"unknown runner: {name!r}; available: {sorted(RUNNERS)}")
    return RUNNERS[name]


# Always register MAS
register(MASRunner())
