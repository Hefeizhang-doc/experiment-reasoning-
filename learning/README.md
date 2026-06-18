# Science Learning Knowledge Base Pack

This is the learning/memory layer for your domain-agnostic science reasoning system.

It answers:

> How do I let the adapter learn?

By storing observations and turning prediction errors into **parameter update proposals**.

## Three layers

```text
1. Observation memory
   stores what happened

2. Parameter memory
   stores what the adapter currently believes

3. Learner
   compares predicted vs observed, then proposes parameter updates
```

## Important

This does **not** train the LLM.

It learns by updating adapter parameters such as:

```json
{
  "transfer_overage_rate": 0.08,
  "disturbance_loss_per_event": 0.10,
  "long_duration_drift_rate": 0.10,
  "batching_overhead_rate": 0.10
}
```

The kernel stays unchanged.

## Folder structure

```text
science_learning_kb_pack/
├── README.md
├── run_learning_example.py
├── configs/
│   └── learning_policy.json
├── parameters/
│   └── adapter_parameters.json
├── memory/
│   └── observations.example.jsonl
└── learning/
    ├── knowledge_store.py
    └── adapter_learner.py
```

## Observation format

Each JSONL line looks like:

```json
{
  "experiment_id": "run_001",
  "context": {"x1": 1, "x2": 24},
  "adapter_params_used": {
    "transfer_overage_rate": 0.08,
    "disturbance_loss_per_event": 0.10
  },
  "prediction": {"predicted_factor": 1.20},
  "outcome": {"observed_factor": 1.25},
  "deltas": {"target_error": 0.05},
  "attribution": {
    "transfer_overage_rate": 0.3,
    "disturbance_loss_per_event": 0.7
  }
}
```

### What is attribution?

Attribution tells the learner which parameter is likely responsible for the error.

If you omit attribution, the learner splits blame evenly and marks confidence lower.

## Run example

```bash
python run_learning_example.py
```

This prints an update proposal but does not apply it.

To apply after human review:

```bash
python run_learning_example.py --apply
```

## Why proposal-only?

Because otherwise the agent can drift.

Bad:

```text
error observed -> automatically overwrite adapter config
```

Good:

```text
error observed -> proposal -> human review -> write config
```

## How it connects to the rest

```text
raw input
  ↓
config adapter reads parameters/adapter_parameters.json
  ↓
reasoning kernel predicts
  ↓
actual observation gets logged
  ↓
learner proposes adapter parameter update
  ↓
approved update writes back to parameters/adapter_parameters.json
```

## Production upgrade path

Replace JSONL with:

- SQLite for local product
- Postgres for team/lab
- ELN/LIMS connector for real lab records
- Vector database for similar-case retrieval

But the learning loop stays the same.
