"""
Knowledge Store
---------------
Tiny JSON/JSONL storage for observations and adapter parameters.

This is the "knowledge base" layer:
1. Observation memory: what happened in past runs.
2. Parameter memory: what the adapter currently believes.
3. Update proposal log: suggested changes before human approval.

The learner should not directly modify parameters unless apply=True and approval is explicit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import json
import time
import shutil


class JsonlObservationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, observation: Dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(observation, ensure_ascii=False) + "\n")

    def load_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []

        out = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


class ParameterStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def backup(self) -> Path:
        ts = int(time.time())
        backup_path = self.path.with_suffix(f".backup_{ts}.json")
        shutil.copy2(self.path, backup_path)
        return backup_path


class ProposalStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, proposal: Dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(proposal, ensure_ascii=False) + "\n")
