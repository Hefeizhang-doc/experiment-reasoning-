"""
Generic Config Adapter
----------------------
Loads adapter_mapping.generic.json and transforms raw input into kernel input.

This is intentionally simple:
- aliases rename fields
- defaults fill missing values
- rules set generic kernel fields when conditions match

No domain vocabulary is required by this adapter.
Domain-specific adapters can use the same shape but different config files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


class ConfigAdapter:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))

    def transform(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(raw)

        # 1. aliases: local field -> kernel field
        for src, dst in self.config.get("field_aliases", {}).items():
            if src in raw and dst not in out:
                out[dst] = raw[src]

        # 2. defaults
        for k, v in self.config.get("defaults", {}).items():
            out.setdefault(k, v)

        # 3. conditional rules
        for rule in self.config.get("rules", []):
            condition = rule.get("when", {})
            field = condition.get("field")
            expected = condition.get("equals")

            if field in raw and raw[field] == expected:
                out.update(rule.get("set", {}))

        return out


def load_variable_schema(path: str | Path) -> Dict[str, Dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("variables", data)
