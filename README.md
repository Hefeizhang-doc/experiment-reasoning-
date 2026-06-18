# 🧪 Experiment Reasoning

> **Domain-agnostic experimental design & scientific reasoning framework for Claude Code**

Turn Claude into your lab partner: plan wet-lab experiments with loss-aware resource estimation, analyze outcomes to find causal signals, and let the learning layer tune your parameters from past runs — all without baking any biology, chemistry, or assay knowledge into the kernel.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-orange)](https://claude.ai/code)

---

## 🤔 What is this?

**Experiment Reasoning** is a [Claude Code](https://claude.ai/code) skill that brings structured scientific thinking to experimental workflows. It has two kernels working in tandem:

| Kernel | When | Role |
|--------|------|------|
| **Design Kernel** (v8.2) | *Before* the experiment | Layout planning, loss/resource modeling, state machine, risk warnings |
| **Loop Kernel** (v12.1) | *After* the experiment | Observation → causal signals → falsifiable hypotheses → follow-up proposals |

A **learning layer** closes the loop: it compares predictions to outcomes and proposes parameter updates (with mandatory human review). A **config adapter** translates your domain's vocabulary into kernel fields — so the kernels never need to know what a "cell," "transfection," or "96-well plate" is.

### The problem it solves

> "I keep running experiments, but my resource estimates are always off, and I don't systematically learn from failed runs."

Most experimentalists rely on intuition and static spreadsheets. This framework gives you:

- **Before the experiment**: a multiplicative loss model with explainable trace per factor, resource estimates with overage, layout advice with edge policies, and a state machine with QC checkpoints
- **After the experiment**: automated variable discovery, association mining, ranked causal signal detection, and counterfactual follow-up proposals
- **Across experiments**: a learner that attributes prediction errors to specific parameters and proposes tuning — but never auto-applies changes

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     CONFIG ADAPTER LAYER                         │
│  Translates YOUR domain words → kernel fields                    │
│  "n_groups" → "groups", "high_loss_profile" → loss rate 0.20    │
└──────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌──────────────────┐   ┌───────────────┐
│ DESIGN KERNEL │   │ EXECUTION CARD   │   │  LOOP KERNEL  │
│   (before)    │──▶│    GENERATOR     │──▶│   (after)     │
│               │   │                  │   │               │
│ • Loss model  │   │ • Recording tmpl │   │ • Variable    │
│ • Resources   │   │ • QC checkpoints │   │   discovery   │
│ • Layout      │   │ • Observation    │   │ • Causal      │
│ • State mach. │   │   template       │   │   signals     │
│ • Risk review │   │                  │   │ • Hypotheses  │
└───────────────┘   └──────────────────┘   │ • Follow-ups  │
                                           └───────────────┘
                                                  │
                     ┌────────────────────────────┘
                     ▼
           ┌──────────────────┐
           │  LEARNING LAYER  │
           │                  │
           │ • delta compute  │
           │ • attribution    │
           │ • param proposal │──▶ Human review ──▶ adapter_parameters.json
           └──────────────────┘
```

---

## ✨ Core Features

### 🔬 Design Kernel (Pre-Experiment)

- **Explainable loss model** — multiplicative overage factor `Π(1 + rate)^count` with per-factor rationale and trace
- **Resource planner** — computes bulk liquid, starting material quantity with overage applied
- **Layout advisor** — knows container geometry (96/24/6-unit), preferred interior positions, edge policies
- **State machine** — complete experimental lifecycle with QC and recovery branches
- **Decision advisor** — surfaces risks (low replicates, many groups, long duration, missing data) without deciding for you

### 🔁 Loop Kernel (Post-Experiment)

- **Automatic variable discovery** — classifies context fields as numeric or categorical, no schema required
- **Causal signal detection** — stratified association mining ranked by effect size
- **Falsifiable hypothesis generation** — each with explicit confirmation/refutation criteria
- **Intervention candidates** — reduce / control / randomize / preserve recommendations per variable
- **Abstract follow-up proposals** — A/B comparisons to test each hypothesis

### 📝 Execution Card Bridge

- Generates structured recording templates from design kernel output
- Defines QC checkpoint fields, fail routes, and fallback states
- `finalize_observation()` converts filled cards to loop-kernel-compatible Observations
- Schema-tolerant: supports v8.2 and legacy field names

### 📈 Learning Layer

- Computes prediction-vs-outcome deltas
- Attribute errors to specific adapter parameters
- Proposes parameter updates → **never auto-applies**
- Production-ready upgrade path: JSONL → SQLite → Postgres → LIMS connector

---

## 🚀 Quick Start

### Installation

```bash
# Clone into your Claude Code skills directory
git clone https://github.com/Hefeizhang-doc/experiment-reasoning-.git ~/.claude/skills/experiment-reasoning
```

Or symlink if you've cloned elsewhere:

```bash
git clone https://github.com/Hefeizhang-doc/experiment-reasoning-.git
ln -s $(pwd)/experiment-reasoning- ~/.claude/skills/experiment-reasoning
```

### Verify it's loaded

In Claude Code, the skill auto-triggers when you talk about designing experiments, analyzing outcomes, or planning wet-lab work. Just say:

> "I'm planning a 7-group experiment with 3 replicates in a 96-well plate, 72-hour duration. Help me plan resources and layout."

Claude will invoke the design kernel and walk you through loss estimates, resource requirements, layout advice, and risk review.

---

## 📖 Usage

### Design Kernel — Before the experiment

```python
from kernels.wetlab_design_kernel import experimental_reasoning_kernel_v8_2

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

**Output includes:**
```python
result["loss_model"]["overage_factor"]        # → 1.9008
result["loss_model"]["expansion_trace"]       # per-factor breakdown
result["resources"]["bulk_liquid_to_prepare_ml"]  # → 5.13
result["resources"]["starting_quantity_to_prepare"]  # → 1,026,432
result["layout_advice"]["preferred_positions"] # → rows B-G, cols 2-11
result["decision_advice"]["items"]             # → risk review items
result["state_machine"]["transitions"]         # → QC + recovery paths
```

### Config Adapter — Translating your domain vocabulary

```python
from config.adapters.config_adapter import ConfigAdapter

adapter = ConfigAdapter("config/configs/adapter_mapping.generic.json")

raw = {
    "n_groups": 7,                          # your local name
    "n_replicates": 3,                      # your local name
    "container_layout": "96_position",      # your local name
    "material_handling_profile": "high_loss", # triggers rule: disturbance_loss → 0.20
}

adapted = adapter.transform(raw)
# → {"groups": 7, "replicates": 3, "container_format": "96-unit", ...}
result = experimental_reasoning_kernel_v8_2(adapted)
```

### Execution Card → Observation → Loop Kernel

```python
from bridge.execution_card_generator import generate_execution_card, finalize_observation

# 1. Generate recording template
card = generate_execution_card(design_kernel_output, experiment_id="run_001")

# 2. After experiment, fill in outcomes
filled_outcome = {
    "observed_overage_factor": 2.05,
    "actual_bulk_liquid_used_ml": 5.8,
    "actual_deviations": ["well B3 contaminated, excluded"],
}

# 3. Finalize into Observation
observation = finalize_observation(card, filled_outcome)

# 4. Feed to scientist loop
from kernels.scientific_loop_kernel import experimental_scientist_agent_v12_1
result = experimental_scientist_agent_v12_1([observation])
```

### Learning Layer — Closing the loop

```python
from learning.run_learning_example import run_learning_cycle

# Compares predictions to outcomes, proposes adapter parameter updates
proposal = run_learning_cycle(observations=[observation])
# → {"transfer_overage_rate": 0.08 → 0.11, "rationale": "..."}

# Human reviews, then:
# python run_learning_example.py --apply   # writes to parameters/adapter_parameters.json
```

---

## 📁 Directory Structure

```
experiment-reasoning/
├── SKILL.md                          # Claude Code skill definition (auto-trigger)
├── README.md                         # You're here!
├── LICENSE                           # MIT
│
├── kernels/                          # 🔮 Domain-agnostic reasoning engines
│   ├── wetlab_design_kernel.py       #   Pre-experiment: loss, resources, layout, state machine
│   └── scientific_loop_kernel.py     #   Post-experiment: causal signals → hypotheses
│
├── bridge/                           # 🔗 Connects design output → observation input
│   └── execution_card_generator.py   #   Recording template + finalize_observation()
│
├── config/                           # ⚙️ Domain vocabulary translation layer
│   ├── README.md
│   ├── run_example_pipeline.py       #   End-to-end example
│   ├── adapters/
│   │   └── config_adapter.py         #   Field alias + conditional rule engine
│   ├── configs/
│   │   ├── adapter_mapping.generic.json      # Field aliases, defaults, rules
│   │   ├── loss_resource_policy.generic.json # Default loss rates/thresholds
│   │   ├── variable_schema.generic.json      # Optional metadata for loop kernel
│   │   ├── container_geometry.generic.json   # Custom container layouts
│   │   ├── scientist_agent_config.json       # Hypothesis generation controls
│   │   └── memory_config.local.json          # Observation storage paths
│   └── examples/
│       ├── raw_input.generic.json            # Example raw design input
│       └── observations.generic.json         # Example observations for loop
│
└── learning/                         # 📈 Parameter tuning from experience
    ├── README.md
    ├── run_learning_example.py       #   Demo learning cycle
    ├── configs/
    │   └── learning_policy.json      #   Learning behavior config
    ├── parameters/
    │   └── adapter_parameters.json   #   Current adapter parameter values
    ├── memory/
    │   └── observations.example.jsonl  # Example observation store
    └── learning/
        ├── adapter_learner.py        #   Delta → attribution → proposal
        └── knowledge_store.py        #   Observation persistence
```

---

## 🧠 Design Philosophy

### The kernel must not know biology

```python
# ❌ WRONG — domain vocabulary in the kernel
if assay == "viral_transduction":
    loss *= 1.25

# ✅ CORRECT — domain vocabulary stays in config
# adapter_mapping.generic.json:
{ "when": {"field": "material_handling_profile", "equals": "high_loss"},
  "set": {"disturbance_loss_per_event": 0.20} }
```

The kernels reason over **abstract experimental units, resources, disturbances, constraints, and states**. Domain words like "cell," "virus," "transfection," or "media change" live exclusively in config adapters and user input.

### Human-in-the-loop by design

- The **decision advisor** warns, never decides
- The **learning layer** proposes parameter updates, never auto-applies
- The **state machine** asks checkpoint questions, doesn't prescribe protocol steps
- The **execution card generator** creates recording templates, not protocols

### Progressive disclosure

| Layer | What it knows | What it doesn't know |
|-------|--------------|---------------------|
| Kernel | Units, groups, containers, loss, states | Assays, reagents, cell types, instruments |
| Adapter | Field aliases, conditional rules, default rates | Historical accuracy of those defaults |
| Learner | Prediction errors, parameter attribution | Whether a proposed change makes scientific sense |
| Human | Domain knowledge, scientific judgment | — |

---

## 🎯 When to Use

| Scenario | Use |
|----------|-----|
| Designing a new multi-group experiment | ✅ Design kernel → resource + layout planning |
| Wondering if your plate layout causes systematic error | ✅ Design kernel → layout advisor with edge policy |
| Analyzing why your last 5 runs had resource shortfalls | ✅ Loop kernel → variable mining + learning layer |
| Wanting to propose follow-up experiments systematically | ✅ Loop kernel → hypotheses + follow-up proposals |
| Moving from "follow the protocol" to "understand failure modes" | ✅ Full pipeline |
| Writing a protocol step-by-step | ❌ This is not a protocol generator |
| Analyzing clinical trial data | ❌ Designed for wet-lab/in-vivo experimental units |

---

## 📄 License

MIT — see [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgments

Built with the [Claude Code](https://claude.ai/code) skills framework. Inspired by the reproducibility crisis in experimental science and the observation that most wet-lab resource estimation is still done on the back of an envelope.
