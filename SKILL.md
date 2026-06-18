---
name: experiment-reasoning
description: Domain-agnostic experimental design and scientific reasoning framework. Use when planning wet-lab experiments, analyzing experimental outcomes, proposing follow-up hypotheses, or learning from past runs. Two kernels: design (before experiment) and loop (after experiment).
---

# Experiment Reasoning System

Two-kernel system for experimental design and scientific learning. Domain-agnostic — the kernels know nothing about specific assays, cell types, or reagents. Domain vocabulary lives in config adapters.

## When to Use

- Designing a new experiment and need layout, resource, loss, and risk guidance
- Analyzing past experimental outcomes to find which variables drove error
- Proposing follow-up experiments based on causal signals
- Learning adapter parameters (loss rates, overage factors) from accumulated runs
- Moving from "just follow the protocol" to "understand why this experiment might fail"

## Architecture

```
config adapter             execution card              learning layer
      │                         │                          │
      ▼                         ▼                          ▼
┌──────────────┐    ┌─────────────────────┐    ┌──────────────────┐
│ design kernel│───▶│    run experiment   │───▶│   loop kernel    │
│  (before)    │    │  (wet lab / in vivo)│    │    (after)       │
└──────────────┘    └─────────────────────┘    └──────────────────┘
```

| File | Location | Role |
|:---|:---|:---|
| `wetlab_design_kernel.py` | `kernels/` | Pre-experiment: layout, loss, resources, state machine, risk |
| `scientific_loop_kernel.py` | `kernels/` | Post-experiment: observation → causal signals → hypotheses → follow-up proposals |
| `execution_card_generator.py` | `bridge/` | Bridge: design output → structured recording template → filled observation |
| `config/` | `config/` | Adapter: translates domain vocabulary to kernel fields |
| `learning/` | `learning/` | Learner: compares predictions to outcomes, proposes parameter updates |

## Design Kernel (Before Experiment)

**Input**: generic experimental design facts. No domain vocabulary.

```python
from kernels.wetlab_design_kernel import experimental_reasoning_kernel_v8_2

# Raw design (already translated by config adapter)
design = {
    "groups": 7,
    "replicates": 3,
    "container_format": "96-unit",
    "duration_hours": 72,
    "volume_per_unit_ul": 100,
    "quantity_per_unit": 20000,
    "disturbance_events": 3,
    "disturbance_loss_per_event": 0.20,
}

result = experimental_reasoning_kernel_v8_2(design)
```

**Output** contains:
- `loss_model` — multiplicative overage factor with explainable trace per factor
- `state_machine` — states, transitions, checkpoint questions
- `resources` — how much liquid and starting material to prepare
- `layout_advice` — container capacity, preferred positions, edge policy
- `decision_advice` — review items with severity, questions, suggested actions

**What it checks:**
- Groups × replicates → enough capacity? Need to split?
- Long duration → disturbance count provided? Inferred? Flagged if guessed.
- Low replicates → statistical power concern?
- Layout → preferred positions sufficient? Edge override needed?

## Loop Kernel (After Experiment)

**Input**: past observations with context, predictions, outcomes, and deltas.

```python
from kernels.scientific_loop_kernel import experimental_scientist_agent_v12_1

agent_input = {
    "observations": [ ... ],  # filled execution cards
    "target_delta": "target_error",
    "variable_schema": { ... },  # optional, auto-discovered if absent
}

result = experimental_scientist_agent_v12_1(agent_input)
```

**Output** contains:
- `discovered_variables` — auto-classified as numeric or categorical
- `causal_signals` — which variables associate with target error, ranked by effect size
- `hypotheses` — falsifiable statements with confirmation/refutation criteria
- `candidate_interventions` — reduce, control, randomize, or preserve each variable
- `follow_up_proposals` — abstract A/B comparisons to test each hypothesis

## Full Pipeline

```
1. Raw domain input → config adapter → design kernel
2. Design kernel output → execution card generator → structured template
3. Run experiment, fill the card
4. Filled card → finalize_observation() → v12.1 Observation
5. Observations → loop kernel → hypotheses + follow-up proposals
6. Loop kernel output → learning layer → adapter parameter update proposal
7. Human review → approve → write back to adapter config
```

## Config Adapter

Located in `config/`. Translates local terms into kernel fields:

- `adapter_mapping.generic.json` — field aliases, conditional rules (e.g. `material_handling_profile: "high_loss"` → `disturbance_loss_per_event: 0.20`)
- `loss_resource_policy.generic.json` — default rates and thresholds
- `variable_schema.generic.json` — optional metadata for scientist loop variable discovery
- `container_geometry.generic.json` — custom container layouts

## Learning Layer

Located in `learning/`. Does NOT auto-update parameters:

1. Observation + prediction → compute deltas
2. Attribution → identify which parameter caused the error
3. Proposal → suggest new parameter value
4. Human review → approve/reject
5. Approved → write to `parameters/adapter_parameters.json`

## Important Boundaries

- **Kernels know nothing about biology, chemistry, or any specific assay.** Domain words like "cell", "virus", "transfection", "media change" must stay in config or user input.
- **Design kernel handles wet-lab constraints** (containers, edge evaporation, liquid handling loss). The loop kernel is fully domain-agnostic.
- **Execution card generator does not generate protocols.** It generates structured recording templates.
- **Learning layer never auto-applies updates.** Human review required.
