# Science Reasoning Config Pack

This pack is the missing configuration layer around the domain-agnostic kernels:

- `experimental_reasoning_kernel_v8_2_1.py`
- `experimental_scientist_agent_v12_1.py`

The kernel should stay clean. Domain-specific vocabulary belongs in adapters/configs.

## Files

### `configs/adapter_mapping.generic.json`

Maps local/raw input names into kernel fields.

Example:

```json
{
  "n_groups": 7,
  "n_replicates": 3,
  "container_layout": "96_position"
}
```

becomes:

```json
{
  "groups": 7,
  "replicates": 3,
  "container_format": "96-unit"
}
```

It also injects generic policy parameters such as:

```json
{
  "transfer_overage_rate": 0.08,
  "disturbance_loss_per_event": 0.10,
  "layout_reserve_fraction": 0.10
}
```

### `configs/loss_resource_policy.generic.json`

Stores default rates and thresholds. In production, these should be updated from local historical data, but only after human review.

### `configs/variable_schema.generic.json`

Optional metadata for the scientist loop. The core can discover variables automatically, but schema helps explain them.

### `configs/scientist_agent_config.json`

Controls hypothesis generation and variable discovery behavior.

### `configs/container_geometry.generic.json`

Optional external geometry config. The current kernel has built-in defaults; this file is for refactoring or custom containers.

### `configs/memory_config.local.json`

Defines where observations should be stored.

### `adapters/config_adapter.py`

Tiny adapter implementation:
1. Field aliases
2. Defaults
3. Conditional rules

## Architecture

```text
raw input
  ↓
config adapter
  ↓
experimental reasoning kernel
  ↓
prediction / resource / layout / review output
  ↓
observation memory
  ↓
scientist agent
  ↓
hypotheses / counterfactuals / follow-up proposals
```

## Important boundary

Do not put domain-specific vocabulary inside the kernel.

Correct:

```text
domain adapter:
  local concept -> disturbance_loss_per_event
  local container -> container_format
  local resource unit -> quantity_per_unit
```

Incorrect:

```text
kernel:
  if assay == ...
  if cell_type == ...
  if instrument == ...
```

## Minimal usage

```python
from adapters.config_adapter import ConfigAdapter
from experimental_reasoning_kernel_v8_2_1 import experimental_reasoning_kernel_v8_2

adapter = ConfigAdapter("configs/adapter_mapping.generic.json")

raw = {
    "n_groups": 7,
    "n_replicates": 3,
    "container_layout": "96_position",
    "unit_volume_ul": 100,
    "per_unit_quantity": 20000,
    "duration_h": 72,
    "disturbance_events": 3,
    "material_handling_profile": "high_loss"
}

adapted = adapter.transform(raw)
result = experimental_reasoning_kernel_v8_2(adapted)
```
