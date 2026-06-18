"""
Run knowledge-base learning example.

This demonstrates:
1. Load observation memory.
2. Load current adapter parameters.
3. Generate parameter update proposal.
4. Optionally apply it with explicit approval.

Usage:
    python run_learning_example.py

To apply after review:
    python run_learning_example.py --apply
"""

from pathlib import Path
import json
import sys

from learning.knowledge_store import JsonlObservationStore, ParameterStore, ProposalStore
from learning.adapter_learner import AdapterLearner


ROOT = Path(__file__).resolve().parent


def main():
    apply = "--apply" in sys.argv

    policy = json.loads((ROOT / "configs" / "learning_policy.json").read_text(encoding="utf-8"))

    obs_store = JsonlObservationStore(ROOT / "memory" / "observations.example.jsonl")
    param_store = ParameterStore(ROOT / "parameters" / "adapter_parameters.json")
    proposal_store = ProposalStore(ROOT / "memory" / "update_proposals.jsonl")

    observations = obs_store.load_all()
    parameter_state = param_store.load()

    learner = AdapterLearner(policy)
    proposal = learner.propose_updates(observations, parameter_state)

    proposal_store.append(proposal)

    print("\n=== Update proposal ===")
    print(json.dumps(proposal, indent=2, ensure_ascii=False))

    if apply:
        if policy.get("learning", {}).get("require_human_approval", True):
            print("\nApplying because --apply was explicitly provided.")
        backup_path = param_store.backup()
        new_state = learner.apply_proposal(proposal, parameter_state, approved=True)
        param_store.save(new_state)
        print(f"\nApplied. Backup saved to: {backup_path}")
        print("\n=== New parameter state ===")
        print(json.dumps(new_state, indent=2, ensure_ascii=False))
    else:
        print("\nNot applied. Review proposal first, then rerun with --apply if approved.")


if __name__ == "__main__":
    main()
