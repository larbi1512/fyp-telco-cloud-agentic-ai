"""
Orchestrator State — the single TypedDict passed through all LangGraph nodes.

Every agent reads from / writes to specific keys in this state.
LangGraph manages state transitions and persistence automatically.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict


# Reusable sub-structures 

class SliceInfo(TypedDict, total=False):
    sst: int            # Slice Service Type (e.g., 1 for eMBB)
    sd: str             # Slice Differentiator (e.g., "000001")
    description: str

class ConnectivitySpecs(TypedDict, total=False):
    plmn: dict[str, str]       # {"mcc": "001", "mnc": "01"}
    slices: list[SliceInfo]
    dnns: list[str]

class VNFSpec(TypedDict, total=False):
    """Specification for a single VNF in the topology."""
    name: str                          # e.g. "oai-amf"
    type: str                          # e.g. "amf"
    replicas: int
    resources: dict[str, dict[str, str]]  # {requests: {cpu, memory}, limits: {cpu, memory}}
    interfaces: list[dict[str, Any]]
    supported_slices: list[dict[str, Any]]
    affinity: dict[str, Any]
    config: dict[str, Any]             # VNF-specific config (PLMN, DNN, etc.)


class TopologyBlueprint(TypedDict, total=False):
    """Network topology produced by the Network Planner."""
    topology_id: str
    connectivity: ConnectivitySpecs
    vnfs: list[VNFSpec]
    connections: list[dict[str, str]]  # [{from, to, interface}]
    sla: dict[str, Any]


class ResourceAllocation(TypedDict, total=False):
    """Resourced topology produced by the Resource Allocator."""
    topology_id: str
    vnfs: list[VNFSpec]                # VNFs now enriched with resource specs
    node_assignments: dict[str, str]   # vnf_name -> node


class ConfigArtifact(TypedDict, total=False):
    """Configuration artifact produced by VNF Configurator."""
    vnf_name: str
    helm_values: dict[str, Any]        # Rendered values.yaml content
    config_maps: dict[str, Any]
    secrets: list[str]                 # Secret names (values never stored in state)


class ValidationReport(TypedDict, total=False):
    """Validation report produced by the Policy Validator."""
    topology_id: str
    policy_checks: dict[str, dict[str, Any]]
    dry_run: dict[str, Any]
    conflicts: list[str]
    decision: Literal["GO", "NO_GO"]
    reasons: list[str]
    violation_count: int


class MetricEvent(TypedDict, total=False):
    """A single metric data point from the KPI Monitor."""
    timestamp: str
    vnf_name: str
    metric_name: str
    value: float
    unit: str
    threshold_status: Literal["normal", "warning", "critical"]


class AnomalyAlert(TypedDict, total=False):
    """Alert produced by the Anomaly Detector."""
    alert_id: str
    type: str
    confidence: float
    description: str
    affected_metrics: list[dict[str, Any]]
    suggested_cause: str
    suggested_actions: list[str]


class SLAStatus(TypedDict, total=False):
    """SLA compliance status from the SLA Compliance agent."""
    rule: str
    current_value: float
    threshold: float
    status: Literal["compliant", "warning", "breach"]
    time_to_breach_min: float | None
    compliance_pct: float


class RemediationPlan(TypedDict, total=False):
    """Plan produced by the Planner/Reasoning agent."""
    plan_id: str
    triggered_by: str
    diagnosis: str
    confidence: float
    recommended_actions: list[dict[str, Any]]
    expected_outcome: str
    rollback_plan: str
    validation_metric: str


class ExecutionResult(TypedDict, total=False):
    """Result of executing a remediation action."""
    action_type: str
    target: str
    status: Literal["success", "failed", "rolled_back"]
    details: str
    timestamp: str


# Append-only message list helper 

def _merge_lists(left: list, right: list) -> list:
    """Merge two lists by appending (used as a reducer for LangGraph)."""
    return left + right


# Main orchestrator state 

class OrchestratorState(TypedDict, total=False):
    """
    Central state passed through the entire LangGraph workflow.

    Keys annotated with `Annotated[..., operator.add]` are append-only:
    new values are appended rather than overwritten.
    """

    # User interaction
    user_intent: str                   # Raw natural language from user
    parsed_intent: dict[str, Any]      # Structured intent from LLM parsing
    intent_features: dict[str, Any]    # structured_features from intents_v2 (injected by harness)
    
    # Pre-deployment pipeline 
    topology: TopologyBlueprint | None
    resource_allocation: ResourceAllocation | None
    config_artifacts: list[ConfigArtifact]
    validation_report: ValidationReport | None

    # Post-deployment monitoring 
    current_metrics: list[MetricEvent]
    anomaly_alerts: Annotated[list[AnomalyAlert], operator.add]
    sla_status: list[SLAStatus]
    remediation_plan: RemediationPlan | None
    execution_results: Annotated[list[ExecutionResult], operator.add]

    # Deployment results (Phase 4)
    deployment_results: list[dict[str, Any]]  # Per-VNF deployment outcomes

    # Conversation & control flow
    messages: Annotated[list[dict[str, str]], operator.add]  # {role, content}
    current_agent: str                 # Name of the active agent node
    requires_approval: bool            # True when HITL checkpoint is active
    user_approved: bool | None         # User's HITL decision
    error: str | None                  # Last error message (if any)
    phase: Literal["pre_deployment", "deploying", "post_deployment", "idle"]

    # Append-only log of HITL gate triggers and synthetic-critic rejections.
    # Each entry: {"source": "topology_review_gate"|"deployment_review_gate"|"synthetic_critic",
    #              "would_approve": bool, "reasons": list[str], "ts": iso8601}.
    # Read by the experiment harness to compute the "Human interventions" metric.
    intervention_log: Annotated[list[dict[str, Any]], operator.add]
