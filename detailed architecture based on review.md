
## Why Multi-Agent System (MAS) Over a Single Monolithic Agent?

### The Problem with a Single Agent Approach

A single monolithic agent attempting to handle all aspects of 5G network orchestration would face critical limitations:

| Challenge | Single Agent Problem | MAS Solution |
|-----------|---------------------|--------------|
| **Cognitive Overload** | One LLM context window cannot hold network topology, resource specs, configurations, real-time metrics, and SLA rules simultaneously | Each agent has a focused context window optimized for its specific domain |
| **Latency** | Sequential processing creates bottlenecks; a single point of failure | Parallel processing where independent agents work concurrently |
| **Scalability** | Cannot scale reasoning for different problem types | Agents can be scaled independently based on workload |
| **Specialization** | General-purpose reasoning lacks domain depth | Each agent can use specialized algorithms (ML for anomaly detection, optimization for scaling) |
| **Fault Isolation** | Single failure crashes entire system | Failure in one agent doesn't affect others; graceful degradation |
| **Maintainability** | Monolithic codebase is hard to update | Modular agents can be updated, tested, and deployed independently |

### MAS Communication Patterns in This Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                         ORCHESTRATION CONTROL PLANE                               │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                   │
│   ┌─────────────────────────────────────────────────────────────────────────┐    │
│   │                     AGENT COORDINATION LAYER (LangGraph)                 │    │
│   │                                                                          │    │
│   │   ┌────────────────────┐              ┌────────────────────────────┐    │    │
│   │   │   CLI / Chat UI    │◄────────────►│        LLM Core            │    │    │
│   │   │  (User Interface)  │   Reasoning  │   (Central Brain)          │    │    │
│   │   │                    │   & Planning │   - Intent Interpretation  │    │    │
│   │   │  - Natural Language│              │   - Decision Making        │    │    │
│   │   │  - Confirmations   │              │   - Plan Generation        │    │    │
│   │   │  - Status Updates  │              │   - Explanation Generation │    │    │
│   │   └────────┬───────────┘              └─────────────┬──────────────┘    │    │
│   │            │                                        │                    │    │
│   │            │         HUMAN-IN-THE-LOOP              │                    │    │
│   │            │◄──────────────────────────────────────►│                    │    │
│   │            │      (Validation Checkpoints)          │                    │    │
│   │            │                                        │                    │    │
│   │   ┌────────▼────────────────────────────────────────▼──────────────┐    │    │
│   │   │                   Graph State Machine                           │    │    │
│   │   │        (Orchestrates Agent Transitions & Workflows)             │    │    │
│   │   └─────────────────────────────┬──────────────────────────────────┘    │    │
│   │                                 │                                        │    │
│   └─────────────────────────────────┼────────────────────────────────────────┘    │
│                                     │ MCP                                         │
│                                     ▼                                             │
│                      ┌─────────────────────────┐                                  │
│                      │   Shared State Store    │                                  │
│                      │  (Redis / PostgreSQL)   │                                  │
│                      │  - User Intent          │                                  │
│                      │  - Master Topology      │                                  │
│                      │  - Config Files         │                                  │
│                      │  - Action Logs          │                                  │
│                      │  - Global Metrics       │                                  │
│                      │  - Conversation History │                                  │
│                      └─────────────────────────┘                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Communication Protocols:**

1. **A2A (Agent-to-Agent)**: Direct message passing between agents in a pipeline or event-driven flow
2. **MCP (Model Context Protocol)**: Standardized interface for agents to access shared state and LLM Core

---

# Agent Specifications

---

## AGENT COORDINATION LAYER (Central Orchestration Hub)

The **Agent Coordination Layer** is the central nervous system of the Multi-Agent System. It encapsulates the **LLM Core**, the **CLI/Chat Interface**, and the **Graph State Machine** that orchestrates all agent interactions. This layer is responsible for interpreting user intent, coordinating agent workflows, and ensuring human oversight through validation checkpoints.

### Why the Coordination Layer is the Core of the System

| Responsibility | Description |
|----------------|-------------|
| **Unified Entry Point** | All user interactions flow through the CLI, ensuring a single point of control |
| **Centralized Reasoning** | The LLM Core provides consistent reasoning and decision-making across all agents |
| **Workflow Orchestration** | LangGraph manages state transitions, ensuring agents execute in the correct order |
| **Human Oversight** | Every critical action requires user validation before execution |
| **Audit Trail** | All decisions, confirmations, and actions are logged for traceability |

---

### Component 1: CLI / Chat Interface

> **Role**: The Human Gateway — Provides conversational interface for user interaction and validation

#### Why This Component is Necessary
The CLI is the **only way** users interact with the system. It translates natural language into structured intents, presents plans for approval, and collects confirmations before executing any action. This ensures the human always remains in control.

#### Capabilities

| Capability               | Description                                             |
| ------------------------ | ------------------------------------------------------- |
| **Intent Capture**       | Parse natural language requests into structured intents |
| **Plan Presentation**    | Display generated plans in human-readable format        |
| **Confirmation Prompts** | Request explicit user approval before critical actions  |
| **Status Updates**       | Stream real-time progress and status to the user        |
| **Error Reporting**      | Present errors with explanations and suggested fixes    |
| **Conversation History** | Maintain context across multiple interactions           |

---

### Component 2: LLM Core (Central Brain)

> **Role**: The Reasoning Engine — Provides intelligence for intent interpretation, planning, and decision-making

#### Why This Component is Necessary
The LLM Core is the **central intelligence** that powers all agents. Rather than each agent having its own LLM, a centralized LLM Core ensures consistent reasoning, reduces resource usage, and maintains context across the entire system.

#### Responsibilities

| Responsibility | Description | Used By |
|----------------|-------------|---------|
| **Intent Interpretation** | Convert natural language to structured actions | CLI, Network Planner |
| **Plan Generation** | Create remediation and deployment plans | Planner/Reasoning Agent |
| **Configuration Reasoning** | Validate and complete configuration templates | VNF Configurator |
| **Anomaly Explanation** | Provide root cause analysis in natural language | Anomaly Detector |
| **Decision Justification** | Explain why certain actions are recommended | All agents (for user transparency) |
| **Conversation Management** | Maintain dialogue state and context | CLI Interface |

#### LLM Core Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                           LLM CORE                                 │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│   ┌─────────────────────────────────────────────────────────────┐  │
│   │                    PROMPT MANAGEMENT                        │  │
│   │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │  │
│   │  │ System       │  │ Agent-       │  │ Context          │   │  │
│   │  │ Prompts      │  │ Specific     │  │ Injection        │   │  │
│   │  │              │  │ Templates    │  │ (RAG)            │   │  │
│   │  └──────────────┘  └──────────────┘  └──────────────────┘   │  │
│   └─────────────────────────────────────────────────────────────┘  │
│                                                                    │
│   ┌─────────────────────────────────────────────────────────────┐  │
│   │                    LLM INTERFACE                            │  │
│   │  ┌──────────────────────────────────────────────────────┐   │  │
│   │  │  Model: Claude / GPT-4 / Local LLM (Ollama)          │   │  │
│   │  │                                                      │   │  │
│   │  │  Max Tokens: Configurable per task type              │   │  │
│   │  └──────────────────────────────────────────────────────┘   │  │
│   └─────────────────────────────────────────────────────────────┘  │
│                                                                    │
│   ┌─────────────────────────────────────────────────────────────┐  │
│   │                    OUTPUT PROCESSING                        │  │
│   │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │  │
│   │  │ JSON         │  │ Validation   │  │ Explanation      │   │  │
│   │  │ Parsing      │  │ & Retry      │  │ Generation       │   │  │
│   │  └──────────────┘  └──────────────┘  └──────────────────┘   │  │
│   └─────────────────────────────────────────────────────────────┘  │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

#### Prompt Templates Library

| Template Name                | Purpose                                         |
| ---------------------------- | ----------------------------------------------- |
| `intent_parser.txt`          | Extract structured intent from natural language |
| `topology_planner.txt`       | Design VNF topology based on requirements       |
| `resource_calculator.txt`    | Calculate resource requirements                 |
| `config_validator.txt`       | Validate 5G configurations                      |
| `anomaly_analyst.txt`        | Analyze anomalies and suggest causes            |
| `plan_explainer.txt`         | Generate human-readable explanations            |
| `confirmation_generator.txt` | Create clear confirmation prompts               |

---

### Component 3: Graph State Machine (LangGraph Orchestrator)

> **Role**: The Traffic Controller — Manages agent state transitions and workflow execution

#### Why This Component is Necessary
Without a state machine, agents would need complex peer-to-peer coordination. LangGraph provides a **declarative workflow** that defines exactly how agents interact, when to pause for human input, and how to handle errors.

#### State Machine Design

```
                                    ┌─────────────────┐
                                    │   START NODE    │
                                    │  (User Intent)  │
                                    └────────┬────────┘
                                             │
                                             ▼
                              ┌──────────────────────────────┐
                              │      INTENT PARSING NODE     │
                              │   (LLM Core: Parse Intent)   │
                              └──────────────┬───────────────┘
                                             │
                                             ▼
                              ┌──────────────────────────────┐
                              │   CONFIRMATION CHECKPOINT    │◄──┐
                              │  "Is this what you meant?"   │   │
                              └──────────────┬───────────────┘   │
                                             │                   │
                           ┌─────────────────┼─────────────────┐ │
                           │ YES             │ NO              │ │
                           ▼                 ▼                 │ │
              ┌────────────────────┐  ┌─────────────────┐      │ │
              │   PLANNING NODES   │  │ CLARIFICATION   │──────┘ │
              │  (Agent Pipeline)  │  │     NODE        │        │
              └─────────┬──────────┘  └─────────────────┘        │
                        │                                        │
                        ▼                                        │
              ┌──────────────────────────────┐                   │
              │   PLAN REVIEW CHECKPOINT     │                   │
              │  "Approve this plan?"        │                   │
              └──────────────┬───────────────┘                   │
                             │                                   │
           ┌─────────────────┼──────────────────┐                │
           │ APPROVE         │ MODIFY           │ REJECT         │
           ▼                 ▼                  ▼                │
  ┌────────────────┐  ┌─────────────────┐  ┌────────────────┐   │
  │ EXECUTION NODE │  │ MODIFICATION    │  │ CANCEL NODE    │   │
  │ (Deploy/Scale) │  │     NODE        │──┘                │   │
  └────────┬───────┘  └─────────────────┘                   │   │
           │                                                │   │
           ▼                                                │   │
  ┌──────────────────────────────┐                          │   │
  │   EXECUTION CHECKPOINT       │                          │   │
  │  "Confirm execution?"        │                          │   │
  └──────────────┬───────────────┘                          │   │
                 │                                          │   │
    ┌────────────┴────────────┐                             │   │
    │ CONFIRM       │ ABORT   │                             │   │
    ▼               ▼         │                             │   │
┌────────────┐  ┌────────────┐│                             │   │
│  EXECUTE   │  │  ROLLBACK  ││                             │   │
│   ACTION   │  │    NODE    ││                             │   │
└─────┬──────┘  └────────────┘│                             │   │
      │                       │                              │   │
      ▼                       ▼                              │   │
┌──────────────────────────────────┐                        │   │
│           END NODE               │◄───────────────────────┴───┘
│  (Report Results to User)        │
└──────────────────────────────────┘
```



#### Human-in-the-Loop Checkpoints

| Checkpoint                  | Trigger Condition            | User Options               | Timeout Behavior  |
| --------------------------- | ---------------------------- | -------------------------- | ----------------- |
| **Intent Confirmation**     | After intent parsing         | Confirm / Clarify / Cancel | Wait indefinitely |
| **Plan Approval**           | After plan generation        | Approve / Modify / Reject  | Wait indefinitely |
| **Execution Confirmation**  | Before deployment/scaling    | Execute / Abort            | Wait indefinitely |
| **Error Acknowledgment**    | After any error              | Retry / Skip / Cancel      | Wait indefinitely |
| **Runtime Action Approval** | Before auto-scaling/recovery | Approve / Deny / Delay     | Configurable      |

---

### Human-in-the-Loop (HITL) Interaction Patterns

The system is designed with **mandatory human oversight** at every critical decision point. The user must explicitly approve actions through the CLI before the system executes them.

#### HITL Design Principles

| Principle | Implementation |
|-----------|----------------|
| **No Silent Actions** | Every action is announced and explained before execution |
| **Explicit Consent** | Critical actions require typed confirmation (e.g., "yes", "deploy", "scale") |
| **Undo Capability** | Every action presents a rollback option if it fails |
| **Explanation First** | The LLM explains WHY an action is recommended before asking for approval |
| **Progressive Disclosure** | Show summary first, details on request |


---

### Coordination Layer Implementation Details

#### Technology Stack

| Component | Technology | Justification |
|-----------|------------|---------------|
| **CLI Framework** | Rich (Python) or Ink (Node.js) | Beautiful terminal UI with progress bars, tables, colors |
| **State Machine** | LangGraph | Native support for HITL, checkpoints, and agent coordination |
| **LLM Integration** | LangChain + Ollama/Claude API | Flexible model switching, prompt management |
| **Message Queue** | Redis Pub/Sub | Real-time event streaming between agents |
| **State Persistence** | PostgreSQL + Redis | Durable state for recovery, fast cache for active sessions |

#### Error Handling in Coordination Layer

```
ERROR OCCURRED IN AGENT
         │
         ▼
┌─────────────────────────────────────────────┐
│ 1. Capture error context and stack trace    │
└─────────────────────────┬───────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────┐
│ 2. LLM generates human-readable explanation │
└─────────────────────────┬───────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────┐
│ 3. Present to user via CLI                  │
│    "Error: UPF deployment failed.           │
│     Cause: Insufficient memory on nodes.    │
│     Suggestion: Add node or reduce UPF RAM" │
└─────────────────────────┬───────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────┐
│ 4. User chooses action:                     │
│    [r] Retry  [s] Skip  [m] Modify  [c] Cancel
└─────────────────────────────────────────────┘
```

---

## PRE-DEPLOYMENT AGENTS (Configuration Phase)

> **Flow Pattern**: Sequential Pipeline (A2A)
> 
> `User Intent → Network Planner → Resource Allocator → VNF Configurator → Policy & Validator → Kubernetes`

These agents operate in a **linear pipeline** to design and validate the network **before** it goes live. Each agent's output becomes the next agent's input, ensuring a clean separation of concerns.

---

### 1. Network Planner Agent

> **Role**: The Architect — Interprets human intent and designs the network blueprint

#### Why This Agent is Necessary
Without a dedicated planner, the system would need to understand both high-level business requirements AND low-level technical specifications simultaneously. This agent isolates the **intent translation** problem from resource/configuration details.

#### Inputs

| Input                      | Source                       | Format      | Description                                                                                |
| -------------------------- | ---------------------------- | ----------- | ------------------------------------------------------------------------------------------ |
| User Intent                | CLI / API / Natural Language | JSON / Text | High-level requirements like "Deploy a 5G core with 1000 UE capacity and 10ms latency SLA" |
| Available VNF Catalog      | Shared State Store           | JSON        | List of deployable VNFs (AMF, SMF, UPF, NRF, AUSF, UDM, etc.)                              |
| Infrastructure Constraints | Kubernetes API               | YAML        | Cluster node count, available namespaces, network policies                                 |
|                            |                              |             |                                                                                            |

#### Algorithm / Logic
- s

```
ALGORITHM: Intent-to-Topology Translation
─────────────────────────────────────────

1. PARSE user intent using LLM:
   - Extract: capacity requirements, latency SLA, geographic constraints
   - Identify: required network functions (e.g., "5G core" → AMF, SMF, UPF, NRF, UDM, AUSF)

2. BUILD dependency graph:
   - Map VNF dependencies (e.g., AMF depends on NRF for service discovery)
   - Determine interface requirements (N1, N2, N4, SBI)

3. GENERATE placement strategy:
   - Apply heuristics for co-location (e.g., SMF and UPF should be close for N4 efficiency)
   - Consider anti-affinity rules (e.g., redundant AMFs on different nodes)

4. OUTPUT Network Topology Blueprint as JSON
```

#### LLM Core Integration

| Task | Prompt Template | Expected Output |
|------|-----------------|-----------------|
| Intent Parsing | "Given this user request: '{intent}', extract the following: 1) Required VNFs, 2) Capacity requirements, 3) SLA constraints" | Structured JSON with extracted parameters |
| Topology Reasoning | "For a 5G core supporting {capacity} UEs with {latency}ms latency, recommend the optimal VNF placement strategy" | Placement recommendations with justification |

#### Outputs

| Output | Destination | Format | Description |
|--------|-------------|--------|-------------|
| Network Topology Blueprint | Resource Allocator Agent (A2A) | JSON | Graph structure defining VNFs, their connections, and interface requirements |
| Deployment Strategy | Shared State Store | JSON | High-level decisions logged for auditability |

**Example Output:**
```json
{
  "topology_id": "5gc-prod-001",
  "vnfs": [
    {"name": "oai-amf", "type": "AMF", "interfaces": ["N1", "N2", "SBI"]},
    {"name": "oai-smf", "type": "SMF", "interfaces": ["N4", "SBI"]},
    {"name": "oai-upf", "type": "UPF", "interfaces": ["N3", "N4", "N6"]}
  ],
  "connections": [
    {"from": "oai-amf", "to": "oai-smf", "interface": "SBI"},
    {"from": "oai-smf", "to": "oai-upf", "interface": "N4"}
  ],
  "sla": {"max_latency_ms": 10, "min_throughput_gbps": 1}
}
```

---

### 2. Resource Allocator Agent
- allocate the cluster + resources instead

> **Role**: The Accountant — Calculates and assigns compute resources to each VNF

#### Why This Agent is Necessary
Resource allocation requires mathematical optimization that is distinct from topology planning. Separating this allows for specialized algorithms (bin-packing, constraint satisfaction) without overloading the planner's context.

#### Inputs

| Input                        | Source                      | Format      | Description                                                |
| ---------------------------- | --------------------------- | ----------- | ---------------------------------------------------------- |
| Network Topology Blueprint   | Network Planner Agent (A2A) | JSON        | List of VNFs to be deployed with their relationships       |
| VNF Resource Profiles        | Lookup Table / Database     | YAML        | Baseline CPU/RAM requirements per VNF type                 |
| Cluster Capacity             | Kubernetes API              | JSON        | Available resources per node (allocatable CPU, memory)     |
| Historical Performance Data  | Shared State Store          | Time-series | Past resource consumption patterns for similar deployments |

#### Lookup Table: VNF Resource Profiles

The **lookup table** is a configuration database that maps VNF types to their baseline resource requirements. This is built from multiple sources:

##### Sources for Building the Lookup Table

| Source                     | Description                       | How to Use                                           | Example                                               |
| -------------------------- | --------------------------------- | ---------------------------------------------------- | ----------------------------------------------------- |
| **Helm Charts**            | Default values in official charts | Extract `resources:` sections from values.yaml       | `oai-cn5g-fed/charts/oai-5g-core/oai-amf/values.yaml` |
| ** Documentation**         | Official performance guides       | Read recommended specs for different capacity levels | OAI GitLab wiki, performance reports                  |
| **Academic Papers**        | Published benchmarking studies    | Reference baseline numbers                           | Search "5G core NFV performance benchmark"            |
| **Historical Deployments** | Past production data              | Analyze trends from Shared State Store               | Your metrics history database                         |

##### Example: Extracting from OAI Helm Charts

```bash

resources:
  limits:
    cpu: 100m      # ← baseline
    memory: 256Mi  # ← Baseline memory
  requests:
    cpu: 100m
    memory: 256Mi
```


#### Algorithm / Logic

```
ALGORITHM: Resource Calculation with Bin-Packing
────────────────────────────────────────────────

1. LOAD VNF resource profiles from lookup table (Shared State Store):
   profiles = shared_state.get("vnf_profiles")
   
   # Example loaded data:
   # - AMF: base 100m CPU, 256Mi RAM (scales: +200m CPU per 1000 UEs)
   # - SMF: base 100m CPU, 256Mi RAM (scales: +150m CPU per 500 sessions)
   # - UPF: base 500m CPU, 512Mi RAM (scales: +800m CPU per 1 Gbps)

1. CALCULATE required resources per VNF:

2. VALIDATE against cluster capacity:

3. APPLY node affinity/anti-affinity:
   - Co-locate SMF and UPF for N4 performance
   - Spread AMF replicas across nodes for HA

5. OUTPUT Resourced Topology
```

#### LLM Core Integration

| Task | Prompt Template | Expected Output |
|------|-----------------|-----------------|
| Workload Prediction | "Based on historical data showing {patterns}, predict resource needs for {vnf_type} serving {capacity} users during {time_period}" | Resource scaling recommendations |
| Cost Optimization | "Given resource options {options}, recommend the most cost-effective allocation that meets SLA {sla}" | Optimized resource allocation |

#### Outputs

| Output | Destination | Format | Description |
|--------|-------------|--------|-------------|
| Resourced Topology | VNF Configurator Agent (A2A) | JSON | Topology enriched with CPU/RAM requests and limits |
| Node Affinity Rules | VNF Configurator Agent (A2A) | YAML | Kubernetes nodeSelector and affinity specifications |

**Example Output:**
```json
{
  "topology_id": "5gc-prod-001",
  "vnfs": [
    {
      "name": "oai-amf",
      "resources": {
        "requests": {"cpu": "500m", "memory": "512Mi"},
        "limits": {"cpu": "750m", "memory": "768Mi"}
      },
      "affinity": {"nodeSelector": {"zone": "control-plane"}}
    },
    {
      "name": "oai-upf",
      "resources": {
        "requests": {"cpu": "2000m", "memory": "2Gi"},
        "limits": {"cpu": "3000m", "memory": "3Gi"}
      },
      "affinity": {"nodeSelector": {"zone": "user-plane", "network": "high-bandwidth"}}
    }
  ]
}
```

---

### 3. VNF Configurator Agent

> **Role**: The Engineer — Generates the actual technical configuration files

#### Why This Agent is Necessary
Configuration generation requires deep knowledge of 5G protocols (PLMN IDs, DNNs, SUPI ranges) and Kubernetes manifests. This specialized knowledge shouldn't pollute the resource allocation logic.

#### Inputs

| Input                       | Source                         | Format | Description                                        |
| --------------------------- | ------------------------------ | ------ | -------------------------------------------------- |
| Resourced Topology          | Resource Allocator Agent (A2A) | JSON   | VNFs with allocated resources and placement rules  |
| VNF Configuration Templates | Template Store                 |  YAML  | Base Helm values and ConfigMap templates           |
| Network Parameters          | Shared State Store             | JSON   | PLMN ID, DNN configurations, IP ranges, slice info |

#### Algorithm / Logic

```
ALGORITHM: Template-Based Configuration Generation
──────────────────────────────────────────────────

1. LOAD Helm chart templates for each VNF type:
   - oai-amf/values.yaml.j2
   - oai-smf/values.yaml.j2
   - oai-upf/values.yaml.j2

2. FOR each vnf in resourced_topology:
   
   # Merge resource specifications 
   # Fill in 5G-specific parameters
   # Generate interface configurations
   # Render final YAML
   
2. GENERATE ConfigMaps for runtime configuration:

3. OUTPUT Configuration Artifacts
```

#### LLM Core Integration

| Task | Prompt Template | Expected Output |
|------|-----------------|-----------------|
| Configuration Validation | "Validate this 5G configuration: {config}. Check for: 1) PLMN format correctness, 2) IP address conflicts, 3) Interface consistency" | Validation report with errors/warnings |
| Template Completion | "Complete this AMF configuration template with appropriate values for a network supporting {slice_types}" | Filled configuration values |

#### Outputs

| Output | Destination | Format | Description |
|--------|-------------|--------|-------------|
| Helm Values Files | Policy & Validator Agent (A2A) | YAML | values.yaml for each VNF Helm chart |
| ConfigMaps | Policy & Validator Agent (A2A) | YAML | Kubernetes ConfigMap manifests |
| Secrets | Policy & Validator Agent (A2A) | YAML | Encrypted credentials and certificates |

**Example Output (values.yaml for AMF):**
```yaml
# Generated by VNF Configurator Agent
# Topology: 5gc-prod-001

image:
  repository: oaisoftwarealliance/oai-amf
  tag: v2.0.1

resources:
  requests:
    cpu: "500m"
    memory: "512Mi"
  limits:
    cpu: "750m"
    memory: "768Mi"

config:
  plmnId: "00101"
  amfName: "OAI-AMF"
  guami:
    mcc: "001"
    mnc: "01"
    amfRegionId: "01"
    amfSetId: "001"
    amfPointer: "01"
  
  supportedPlmnList:
    - mcc: "001"
      mnc: "01"
      tac: "0001"
  
  interfaces:
    n2:
      port: 38412
      protocol: sctp
    sbi:
      port: 8080
      protocol: http2
```

---

### 4. Policy & Validator Agent

> **Role**: The Auditor — Ensures security compliance and validates before deployment

#### Why This Agent is Necessary
Security and compliance checks require a different mindset than configuration generation. This agent acts as a **gatekeeper**, preventing misconfigurations from reaching production.

#### Inputs

| Input | Source | Format | Description |
|-------|--------|--------|-------------|
| Configuration Artifacts | VNF Configurator Agent (A2A) | YAML | All Helm values, ConfigMaps, Secrets |
| Policy Rules | OPA Policy Store | Rego | Open Policy Agent rules (security, compliance) |
| Kubernetes Cluster State | Kubernetes API | JSON | Current cluster resources, namespaces, RBAC |

#### Algorithm / Logic

```
ALGORITHM: Two-Phase Validation (Static + Dynamic)
─────────────────────────────────────────────────

PHASE 1: STATIC ANALYSIS (Policy Checks)
────────────────────────────────────────

1. LOAD OPA policy rules:
   - no_root_containers.rego: Containers must not run as root
   - resource_limits.rego: All pods must have resource limits
   - network_policies.rego: Ingress/egress must be defined
   - secret_management.rego: No plaintext secrets in ConfigMaps

2. FOR each configuration_artifact:
   violations = opa_evaluate(artifact, policies)
   
   IF violations.severity == "CRITICAL":
       REJECT deployment
       RETURN PolicyViolationError(violations)
   
   IF violations.severity == "WARNING":
       LOG warning for operator review

3. GENERATE policy compliance report

PHASE 2: DYNAMIC ANALYSIS (Dry Run)
──────────────────────────────────

4. EXECUTE Kubernetes dry-run:

5. CHECK for conflicts with existing resources:
   - Service port collisions
   - PVC name conflicts
   - ConfigMap overwrites

6. GENERATE validation report with:
   - Policy compliance status
   - Dry-run results
   - Conflict analysis
   - GO / NO-GO decision

7. IF approved:
   TRIGGER Kubernetes Interface for actual deployment
```

#### LLM Core Integration

| Task | Prompt Template | Expected Output |
|------|-----------------|-----------------|
| Policy Interpretation | "Interpret this compliance requirement: '{requirement}' and generate an OPA Rego rule" | Rego policy code |
| Violation Explanation | "Explain this policy violation in simple terms: {violation}. Suggest remediation steps." | Human-readable explanation |

#### Outputs

| Output | Destination | Format | Description |
|--------|-------------|--------|-------------|
| Policy Compliance Report | Shared State Store | JSON | List of checked policies and their status |
| Validation Report | Shared State Store | JSON | Dry-run results and conflict analysis |
| Deployment Approval | Kubernetes Interface | Boolean + Manifests | GO/NO-GO decision with approved artifacts |

**Example Output (Validation Report):**
```json
{
  "topology_id": "5gc-prod-001",
  "timestamp": "2026-01-16T10:30:00Z",
  "policy_checks": {
    "no_root_containers": {"status": "PASS", "checked": 5, "violations": 0},
    "resource_limits": {"status": "PASS", "checked": 5, "violations": 0},
    "network_policies": {"status": "WARNING", "message": "Egress policy not defined for UPF"}
  },
  "dry_run": {
    "status": "SUCCESS",
    "manifests_validated": 12,
    "errors": []
  },
  "conflicts": [],
  "decision": "GO",
  "approved_by": "policy-validator-agent-v1.2"
}
```

---

## POST-DEPLOYMENT AGENTS (Runtime Phase)

> **Flow Pattern**: Event-Driven Control Loop
> 
> `Prometheus → KPI Monitor → [Anomaly Detector | SLA Compliance] → Planner/Reasoning → [Auto-Scaler | Fault Recovery] → Kubernetes`

These agents operate in a **continuous loop** to maintain system health. They react to events and collaborate to diagnose and remediate issues.

---

### 5. KPI Monitor Agent

> **Role**: The Observer — Continuously tracks performance metrics

#### Why This Agent is Necessary
Metric collection and normalization is a high-frequency operation that would overwhelm an LLM. This agent handles the data pipeline, only escalating meaningful events to reasoning agents.

#### Inputs

| Input | Source | Format | Description |
|-------|--------|--------|-------------|
| Prometheus Metrics | Prometheus API | PromQL Results | Raw time-series metrics (CPU, memory, latency, throughput) |
| VNF Custom Telemetry | VNF Pods (Metrics Endpoints) | JSON | 5G-specific metrics (registration rate, PDU sessions, handovers) |
| Metric Definitions | Configuration | YAML | KPI definitions and thresholds |

#### Metrics Tracked

| Category | Metrics | Source |
|----------|---------|--------|
| **Infrastructure** | CPU utilization, Memory usage, Network I/O | Kubernetes metrics-server |
| **5G Control Plane** | AMF registration rate, Authentication success rate, Mobility events | AMF telemetry |
| **5G User Plane** | UPF throughput, Packet loss rate, Jitter, Latency | UPF telemetry |
| **Session Management** | PDU session establishment rate, Session duration | SMF telemetry |

#### Algorithm / Logic

```
ALGORITHM: Metric Collection and Event Generation
────────────────────────────────────────────────

LOOP ():

1. QUERY Prometheus for defined metrics:

2. NORMALIZE metrics into standard format:

3. APPLY threshold checks:
 

4. EMIT Metric Events to downstream agents:
   - Send to Anomaly Detector Agent (A2A)
   - Send to SLA Compliance Agent (A2A)

5. STORE metrics history in Shared State Store
```

#### LLM Core Integration

| Task | Prompt Template | Expected Output |
|------|-----------------|-----------------|
| Metric Correlation | "These metrics changed together: {metrics}. What business impact does this indicate?" | Natural language impact summary |
| Dashboard Summary | "Generate a status summary for the network based on these KPIs: {kpis}" | Executive summary text |

#### Outputs

| Output | Destination | Format | Description |
|--------|-------------|--------|-------------|
| Metric Events | Anomaly Detector, SLA Compliance (A2A) | JSON Stream | Normalized, timestamped metric data |
| Threshold Alerts | Shared State Store | JSON | Static threshold violations |
| Metrics History | Shared State Store (Time-series DB) | InfluxDB/Prometheus | Historical data for trend analysis |

---

### 6. Anomaly Detector Agent

> **Role**: The Detective — Identifies unusual patterns that static thresholds miss

#### Why This Agent is Necessary
Static thresholds can't catch complex anomalies like "latency rising while throughput drops" or gradual performance degradation. This agent uses ML models for pattern recognition.

#### Inputs

| Input | Source | Format | Description |
|-------|--------|--------|-------------|
| Real-time Metric Events | KPI Monitor Agent (A2A) | JSON Stream | Continuous flow of normalized metrics |
| Historical Baselines | Shared State Store | Time-series | Normal behavior patterns for comparison |
| Anomaly Model | ML Model Store | Pickle/ONNX | Trained anomaly detection model |

#### Algorithm / Logic

```
ALGORITHM: Multi-variate Anomaly Detection
──────────────────────────────────────────

MODEL: Isolation Forest (or LSTM Autoencoder for sequential patterns)

1. RECEIVE metric event stream from KPI Monitor

2. MAINTAIN sliding window of recent metrics:

3. FEATURE ENGINEERING:

4. RUN anomaly detection:

5. EMIT Anomaly Alert to Planner Agent (A2A)

6. LOG anomaly for model retraining
```

#### ML Model Details

| Model | Use Case | Training Data |
|-------|----------|---------------|
| **Isolation Forest** | Point anomalies, outliers | Historical metrics with labeled anomalies |
| **LSTM Autoencoder** | Sequential pattern anomalies | Time-series of normal operation |
| **Correlation Analysis** | Relationship breakdowns | Metric pairs that should correlate |

#### LLM Core Integration

| Task | Prompt Template | Expected Output |
|------|-----------------|-----------------|
| Root Cause Analysis | "Anomaly detected: {anomaly_details}. Historical context: {context}. What is the most likely root cause?" | Root cause hypothesis with confidence |
| Investigation Steps | "For this anomaly type: {type}, suggest diagnostic commands and checks" | Ordered list of investigation steps |

#### Outputs

| Output | Destination | Format | Description |
|--------|-------------|--------|-------------|
| Anomaly Alerts | Planner/Reasoning Agent (A2A) | JSON | Anomaly details with confidence and suggested cause |
| Investigation Recommendations | Shared State Store | Markdown | Steps for manual investigation if needed |

**Example Output:**
```json
{
  "alert_id": "anom-2026-01-16-001",
  "type": "correlation_breakdown",
  "confidence": 0.87,
  "description": "UPF latency increasing while throughput is stable - unusual pattern",
  "affected_metrics": [
    {"name": "upf_latency_p99", "current": 45, "baseline": 12, "unit": "ms"},
    {"name": "upf_throughput", "current": 850, "baseline": 850, "unit": "Mbps"}
  ],
  "suggested_cause": "Possible network congestion or packet processing issue - CPU is not bottleneck",
  "suggested_actions": [
    "Check UPF network interface saturation",
    "Analyze packet processing queue depths",
    "Consider horizontal scaling"
  ]
}
```

---

### 7. SLA Compliance Agent

> **Role**: The Enforcer — Ensures contractual SLA objectives are met

#### Why This Agent is Necessary
SLA monitoring requires business context that the Anomaly Detector doesn't have. This agent maps technical metrics to business commitments and predicts breaches before they occur.

#### Inputs

| Input | Source | Format | Description |
|-------|--------|--------|-------------|
| Metric Events | KPI Monitor Agent (A2A) | JSON Stream | Real-time performance data |
| Anomaly Alerts | Anomaly Detector Agent (A2A) | JSON | Detected anomalies that may impact SLA |
| SLA Definitions | Shared State Store | JSON | Contractual SLA terms (latency < 20ms, uptime > 99.9%) |
| Current SLA Status | Shared State Store | JSON | Running SLA compliance calculations |

#### Algorithm / Logic

```
ALGORITHM: SLA Compliance Monitoring and Prediction
───────────────────────────────────────────────────

1. LOAD SLA definitions:
   sla_rules = [
       {"metric": "latency_p99", "threshold": 20, "unit": "ms", "operator": "<"},
       {"metric": "availability", "threshold": 99.9, "unit": "%", "operator": ">="},
       {"metric": "throughput", "threshold": 1000, "unit": "Mbps", "operator": ">="}
   ]

2. FOR each incoming metric_event:
   
   # Real-time compliance check

3. PREDICTIVE ANALYSIS (every 5 minutes):
   
   # Trend-based breach prediction
   FOR each sla_rule:
       trend = calculate_trend(
           metric=sla_rule.metric,
           window="1h",
           method="linear_regression"
       )
       
       time_to_breach = predict_breach_time(trend, sla_rule.threshold)
       
       IF time_to_breach < 30 minutes:
           GENERATE ProactiveWarning(sla_rule, time_to_breach)

4. CALCULATE SLA compliance percentage:
   compliance = {
       "period": "current_month",
       "metrics": {
           "latency": {"compliant_time": 720, "total_time": 721, "percentage": 99.86},
           "availability": {"uptime_minutes": 43190, "total_minutes": 43200, "percentage": 99.98}
       }
   }

5. EMIT notifications to Planner Agent (A2A)
```

#### LLM Core Integration

| Task | Prompt Template | Expected Output |
|------|-----------------|-----------------|
| SLA Trade-off Analysis | "SLA metrics {metrics} are at risk. Available actions: {actions}. Analyze trade-offs." | Recommended action with justification |
| Breach Explanation | "SLA breach occurred: {details}. Generate customer-facing explanation and mitigation plan." | Formatted incident report |

#### Outputs

| Output | Destination | Format | Description |
|--------|-------------|--------|-------------|
| Breach Notifications | Planner/Reasoning Agent (A2A) | JSON | Immediate SLA violations |
| Proactive Warnings | Planner/Reasoning Agent (A2A) | JSON | Predicted future breaches |
| Compliance Reports | Shared State Store | JSON/PDF | Periodic SLA compliance summaries |

---

### 8. Planner / Reasoning Agent (LLM-Based)

> **Role**: The Strategist — Analyzes complex situations and formulates remediation plans

#### Why This Agent is Necessary
This is where the LLM's reasoning capabilities shine. Simple issues can be handled by rule-based agents, but complex, multi-factor problems require the LLM to synthesize information and generate novel solutions.

#### Inputs

| Input | Source | Format | Description |
|-------|--------|--------|-------------|
| Anomaly Alerts | Anomaly Detector Agent (A2A) | JSON | Detected anomalies requiring analysis |
| Breach Notifications | SLA Compliance Agent (A2A) | JSON | SLA violations needing remediation |
| System Context | Shared State Store (MCP) | JSON | Current topology, recent actions, historical incidents |
| Available Actions | Action Catalog | YAML | List of possible remediation actions |

#### Algorithm / Logic

```
ALGORITHM: LLM-Powered Remediation Planning
───────────────────────────────────────────

1. RECEIVE alert from Anomaly Detector or SLA Compliance Agent

2. GATHER context from Shared State Store (via MCP):
   context = {
       "current_topology": fetch_topology(),
       "recent_actions": fetch_actions(last="1h"),
       "similar_incidents": search_incidents(alert.type),
       "current_metrics": fetch_metrics(vnfs=alert.affected_vnfs)
   }

3. CONSTRUCT prompt for LLM Core:
   prompt = f"""
   ## Current Situation
   Alert: {alert.description}
   Affected Components: {alert.affected_metrics}
   
   ## System Context
   - Current deployment: {context.current_topology}
   - Recent changes: {context.recent_actions}
   - Similar past incidents: {context.similar_incidents}
   - Current resource utilization: {context.current_metrics}
   
   ## Available Actions
   {available_actions}
   
   ## Task
   Analyze the root cause and generate a remediation plan.
   Consider:
   1. Is this a resource issue (scale up/out)?
   2. Is this a configuration issue (parameter tuning)?
   3. Is this a fault that requires recovery (restart/failover)?
   
   Output a structured remediation plan.
   """

3. INVOKE LLM Core for reasoning:

4. PARSE LLM response into structured Remediation Plan:

5. VALIDATE plan against safety constraints:
   IF plan violates safety_rules:
       ESCALATE to human operator
   ELSE:
       EMIT plan to Auto-Scaler / Fault Recovery Agent (A2A)

6. LOG decision rationale to Shared State Store
```



#### Outputs

| Output | Destination | Format | Description |
|--------|-------------|--------|-------------|
| Remediation Plan | Auto-Scaler / Fault Recovery Agent (A2A) | JSON | Structured action plan with validation criteria |
| Decision Rationale | Shared State Store | Markdown | Explanation of reasoning for auditability |

**Example Output:**
```json
{
  "plan_id": "rem-2026-01-16-042",
  "triggered_by": "anom-2026-01-16-001",
  "diagnosis": "UPF latency is high due to packet processing queue saturation. CPU is not the bottleneck (40% utilized). This indicates the single UPF instance cannot handle the concurrent session load efficiently.",
  "confidence": 0.82,
  "recommended_actions": [
    {
      "order": 1,
      "type": "horizontal_scale",
      "target": "oai-upf",
      "action": "scale_replicas",
      "from": 1,
      "to": 2,
      "reason": "Distribute session load across multiple UPF instances"
    }
  ],
  "expected_outcome": "Latency should drop to baseline (<15ms) within 5 minutes of scaling",
  "rollback_plan": "If latency doesn't improve, scale back to 1 replica and investigate network path",
  "validation_metric": "upf_latency_p99 < 20ms for 5 consecutive minutes"
}
```

---

### 9. Auto-Scaler Agent

> **Role**: The Executor (Scaling) — Dynamically scales VNF instances

#### Why This Agent is Necessary
Scaling operations require careful coordination with Kubernetes and must be executed atomically. Separating this from the Planner allows for specialized execution logic and retry mechanisms.

#### Inputs

| Input | Source | Format | Description |
|-------|--------|--------|-------------|
| Remediation Plan (Scaling Actions) | Planner Agent (A2A) | JSON | Scaling instructions with targets and parameters |
| Current Deployment State | Kubernetes API | JSON | Current replica counts, pod statuses |
| Resource Availability | Kubernetes API | JSON | Available cluster capacity for scaling |

#### Algorithm / Logic

```
ALGORITHM: Intelligent Scaling Execution
───────────────────────────────────────

1. RECEIVE scaling action from Remediation Plan

2. VALIDATE scaling is possible:

3. CHOOSE scaling method:

4. WAIT for scaling to complete:

5. VALIDATE scaling outcome:

6. WRITE Execution Log to Shared State Store
```

#### LLM Core Integration

| Task | Prompt Template | Expected Output |
|------|-----------------|-----------------|
| Scaling Pattern Learning | "Based on these scaling events {events} and outcomes {outcomes}, identify patterns for proactive scaling" | Scaling recommendations |
| Failure Analysis | "Scaling action {action} failed with error {error}. Suggest alternative approaches." | Alternative scaling strategy |

#### Outputs

| Output | Destination | Format | Description |
|--------|-------------|--------|-------------|
| Scaling Execution Result | Shared State Store | JSON | Success/failure status with details |
| HPA/VPA Adjustments | Kubernetes API | YAML | Updated autoscaler configurations |
| Execution Logs | Shared State Store | JSON | Detailed action logs for auditing |

---

### 10. Fault Recovery Agent

> **Role**: The Executor (Recovery) — Detects and recovers from failures

#### Why This Agent is Necessary
Fault recovery requires rapid response and specialized knowledge of failure modes. This agent handles restarts, failovers, and rollbacks independently of scaling operations.

#### Inputs

| Input | Source | Format | Description |
|-------|--------|--------|-------------|
| Remediation Plan (Recovery Actions) | Planner Agent (A2A) | JSON | Recovery instructions (restart, rollback, failover) |
| Health Check Results | Kubernetes API | JSON | Pod health status, liveness/readiness probe results |
| Failure Events | Kubernetes Events | JSON | CrashLoopBackOff, OOMKilled, etc. |

#### Algorithm / Logic

```
ALGORITHM: Fault Detection and Recovery
───────────────────────────────────────

1. CONTINUOUS HEALTH MONITORING:
   WATCH kubernetes events for:
       - CrashLoopBackOff
       - OOMKilled
       - ImagePullBackOff
       - FailedScheduling
       - Liveness probe failures

2. ON failure_event RECEIVED:
   
   # Classify failure severity
   severity = classify_failure(failure_event)
   
   IF severity == "SELF_HEALING":
       # Kubernetes will handle (restart count < 3)
       LOG and MONITOR
       RETURN
   
   IF severity == "REQUIRES_INTERVENTION":
       # Too many restarts, need intelligent recovery
       
       # Gather diagnostic info
       diagnostics = {
           "pod_logs": kubectl.logs(failure_event.pod),
           "pod_describe": kubectl.describe(failure_event.pod),
           "events": kubectl.get_events(failure_event.namespace),
           "previous_state": fetch_from_shared_state(failure_event.deployment)
       }
       
       # Determine recovery action
       IF failure_event.type == "OOMKilled":
           action = {"type": "increase_memory", "factor": 1.5}
       
       ELIF failure_event.type == "CrashLoopBackOff":
           # Check if recent config change
           IF recent_config_change(failure_event.deployment):
               action = {"type": "rollback_config"}
           ELSE:
               action = {"type": "restart_with_diagnostics"}
       
       ELIF failure_event.type == "FailedScheduling":
           action = {"type": "scale_down_other", "to_free": required_resources}

3. EXECUTE recovery action:
   MATCH action.type:
       CASE "restart":
           kubectl.delete_pod(failure_event.pod)  # Let deployment recreate
       
       CASE "rollback_config":
           previous_config = fetch_previous_config(failure_event.deployment)
           kubectl.apply(previous_config)
       
       CASE "rollback_deployment":
           kubectl.rollout_undo(failure_event.deployment)
       
       CASE "failover":
           # For stateful VNFs, trigger failover to standby
           activate_standby(failure_event.deployment)

4. VALIDATE recovery:
   WAIT for pod to become Ready
   CHECK health endpoints
   VERIFY metrics return to normal

5. GENERATE Incident Report for Shared State Store
```

#### LLM Core Integration

| Task | Prompt Template | Expected Output |
|------|-----------------|-----------------|
| Root Cause Analysis | "Pod crashed with these logs: {logs}. Previous incidents: {history}. Identify root cause." | Root cause with confidence |
| Recovery Plan Generation | "For failure type {type} in component {component}, generate recovery procedure" | Step-by-step recovery plan |
| Incident Learning | "Analyze this resolved incident: {incident}. What preventive measures should be added?" | Prevention recommendations |

#### Outputs

| Output | Destination | Format | Description |
|--------|-------------|--------|-------------|
| Recovery Execution Result | Shared State Store | JSON | Success/failure with recovery details |
| Incident Reports | Shared State Store | Markdown | Detailed incident analysis and resolution |
| Automated Remediation Actions | Kubernetes API | kubectl commands | Direct cluster modifications |

---

## Agent Interaction Matrix

| Agent | Sends To | Receives From | Protocol |
|-------|----------|---------------|----------|
| **Network Planner** | Resource Allocator | User Intent, Shared State | A2A, MCP |
| **Resource Allocator** | VNF Configurator | Network Planner | A2A |
| **VNF Configurator** | Policy & Validator | Resource Allocator | A2A |
| **Policy & Validator** | Kubernetes Interface | VNF Configurator, OPA | A2A |
| **KPI Monitor** | Anomaly Detector, SLA Compliance | Prometheus | A2A |
| **Anomaly Detector** | Planner | KPI Monitor | A2A |
| **SLA Compliance** | Planner | KPI Monitor, Anomaly Detector | A2A |
| **Planner/Reasoning** | Auto-Scaler, Fault Recovery | Anomaly Detector, SLA Compliance, Shared State | A2A, MCP |
| **Auto-Scaler** | Kubernetes Interface, Shared State | Planner | A2A |
| **Fault Recovery** | Kubernetes Interface, Shared State | Planner, K8s Events | A2A |

---

## Technology Stack Summary

| Component | Technology | Purpose |
|-----------|------------|---------|
| Agent Framework | LangGraph | Agent lifecycle, routing, state management |
| LLM Core | Claude / Qwen / GPT-4 | Complex reasoning, natural language understanding |
| Shared State | Redis + PostgreSQL | Fast cache + persistent storage (includes lookup tables) |
| Monitoring | Prometheus + Grafana | Metrics collection and visualization |
| ML Models | Scikit-learn (Isolation Forest), TensorFlow (LSTM) | Anomaly detection |
| Policy Engine | Open Policy Agent (OPA) | Security and compliance validation |
| Kubernetes Interface | Python kubernetes client, Helm SDK | Cluster operations |
| 5G Core | OpenAirInterface (OAI) | AMF, SMF, UPF, NRF, etc. |
| Lookup Tables | YAML configs + benchmarking data | VNF resource profiles, scaling formulas |

---

## Practical Implementation: Where to Start

### For Your FYP Project

1. **Extract Initial Lookup Table**:
   ```bash
   cd c:\Users\Larbi\Desktop\fyp\oai-cn5g\oai-cn5g-fed\charts\oai-5g-core
   
   # Parse all values.yaml files
   grep -A 5 "resources:" oai-amf/values.yaml oai-smf/values.yaml oai-upf/values.yaml
   ```

2. **Run Baseline Benchmarks**:
   - Deploy OAI 5G core on your Minikube
   - Use `oai-gnbsim` (in your workspace) to simulate UEs
   - Monitor with Prometheus:
     ```promql
     rate(container_cpu_usage_seconds_total{pod=~"oai-amf.*"}[5m])
     container_memory_working_set_bytes{pod=~"oai-amf.*"}
     ```

3. **Create Your Lookup Table**:
   ```yaml
   # code/config/vnf_resource_profiles.yaml
   version: "1.0"
   calibrated_date: "2026-01-16"
   environment: "minikube-local"
   
   profiles:
     oai-amf:
       base: {cpu: "100m", memory: "256Mi"}
       scaling: {per_1000_ue: {cpu: "200m", memory: "100Mi"}}
       max_recommended: {cpu: "2000m", memory: "2Gi"}
       notes: "Based on OAI v2.0.1 Helm defaults + local benchmarks"
   ```

4. **Integrate with Resource Allocator Agent**:
   ```python
   import yaml
   
   class ResourceAllocator:
       def __init__(self, lookup_table_path):
           with open(lookup_table_path) as f:
               self.profiles = yaml.safe_load(f)['profiles']
       
       def calculate_resources(self, vnf_type, capacity):
           profile = self.profiles[vnf_type]
           base_cpu = self._parse_cpu(profile['base']['cpu'])
           
           # Apply scaling formula
           if vnf_type == 'oai-amf':
               scaling_factor = capacity['ue_count'] / 1000
               scaled_cpu = base_cpu + scaling_factor * self._parse_cpu(
                   profile['scaling']['per_1000_ue']['cpu']
               )
           
           return {'cpu': f"{scaled_cpu}m", 'memory': profile['base']['memory']}
   ```

---

## Summary: Why MAS Wins

1. **Specialization**: Each agent masters its domain (ML for anomaly detection, LLM for reasoning, rule-based for validation)

2. **Parallelism**: KPI Monitor, Anomaly Detector, and SLA Compliance can work simultaneously

3. **Fault Tolerance**: If the Anomaly Detector fails, KPI Monitor and SLA Compliance still function

4. **Scalability**: High-volume metric processing (KPI Monitor) can be scaled independently of LLM-heavy reasoning (Planner)

5. **Maintainability**: Update the Anomaly Detector's ML model without touching configuration logic

6. **Auditability**: Clear separation allows precise logging of "who decided what and why"



- deploy attach UI
- after deploying 
- get IP address and use it on the Agents to deploy
- try with oss-gpt 
