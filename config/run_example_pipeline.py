"""
Example runner for the config pack.

Usage:
1. Put experimental_reasoning_kernel_v8_2_1.py and experimental_scientist_agent_v12_1.py
   next to this file or on PYTHONPATH.
2. Run:
   python run_example_pipeline.py
"""

from pathlib import Path
import json

from adapters.config_adapter import ConfigAdapter, load_variable_schema

try:
    from experimental_reasoning_kernel_v8_2_1 import experimental_reasoning_kernel_v8_2
except ImportError:
    experimental_reasoning_kernel_v8_2 = None

try:
    from experimental_scientist_agent_v12_1 import experimental_scientist_agent_v12_1
except ImportError:
    experimental_scientist_agent_v12_1 = None


ROOT = Path(__file__).resolve().parent


def main():
    adapter = ConfigAdapter(ROOT / "configs" / "adapter_mapping.generic.json")

    raw = json.loads((ROOT / "examples" / "raw_input.generic.json").read_text(encoding="utf-8"))
    adapted = adapter.transform(raw)

    print("\n=== Adapted kernel input ===")
    print(json.dumps(adapted, indent=2, ensure_ascii=False))

    if experimental_reasoning_kernel_v8_2:
        print("\n=== Reasoning kernel output ===")
        result = experimental_reasoning_kernel_v8_2(adapted)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("\n[skip] experimental_reasoning_kernel_v8_2_1.py not found on PYTHONPATH.")

    observations = json.loads((ROOT / "examples" / "observations.generic.json").read_text(encoding="utf-8"))

    if experimental_scientist_agent_v12_1:
        print("\n=== Scientist agent output ===")
        sci = experimental_scientist_agent_v12_1(observations)
        print(json.dumps(sci, indent=2, ensure_ascii=False))
    else:
        print("\n[skip] experimental_scientist_agent_v12_1.py not found on PYTHONPATH.")


if __name__ == "__main__":
    main()
