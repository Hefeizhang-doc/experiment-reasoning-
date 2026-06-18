"""
Generic Adapter Learner
-----------------------
Turns observation errors into parameter update proposals.

Domain-agnostic assumptions:
- The learner does not know what the parameters mean.
- It only knows:
  predicted_factor
  observed_factor
  adapter_params_used
  optional attribution weights

Core idea:
    if observed_factor > predicted_factor:
        parameters attributed to the error should increase.
    if observed_factor < predicted_factor:
        parameters attributed to the error should decrease.

It produces proposals first. Applying proposals requires explicit approval.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional
import json
import time
import math


@dataclass
class ParameterUpdate:
    parameter: str
    old_value: float
    proposed_value: float
    relative_change: float
    support_n: int
    confidence: str
    rationale: str


class AdapterLearner:
    def __init__(self, policy: Dict[str, Any]):
        self.policy = policy
        learning = policy.get("learning", {})
        self.learning_rate = float(learning.get("learning_rate", 0.20))
        self.max_step = float(learning.get("max_single_update_fraction", 0.15))
        self.min_n = int(learning.get("min_observations_before_update", 3))
        self.min_value = float(learning.get("min_parameter_value", 0.0))
        self.max_value = float(learning.get("max_parameter_value", 2.0))

    def propose_updates(
        self,
        observations: List[Dict[str, Any]],
        parameter_state: Dict[str, Any],
        eligible_parameters: Optional[List[str]] = None,
    ) -> Dict[str, Any]:

        params = parameter_state.get("parameters", {})
        eligible = eligible_parameters or list(params.keys())

        # Accumulate weighted log error by parameter.
        # log(observed/predicted) is symmetric and multiplicative-friendly.
        accum: Dict[str, List[float]] = {p: [] for p in eligible}

        skipped = []

        for obs in observations:
            pred = obs.get("prediction", {}).get("predicted_factor")
            actual = obs.get("outcome", {}).get("observed_factor")

            if not pred or not actual or pred <= 0 or actual <= 0:
                skipped.append({
                    "experiment_id": obs.get("experiment_id"),
                    "reason": "missing_or_invalid_predicted_or_observed_factor"
                })
                continue

            log_error = math.log(actual / pred)

            used = obs.get("adapter_params_used", {})
            attribution = obs.get("attribution")

            candidates = [p for p in eligible if p in used or p in params]
            if not candidates:
                continue

            if attribution:
                total_weight = sum(float(attribution.get(p, 0.0)) for p in candidates)
                if total_weight <= 0:
                    weights = {p: 1.0 / len(candidates) for p in candidates}
                    confidence_hint = "low"
                else:
                    weights = {p: float(attribution.get(p, 0.0)) / total_weight for p in candidates}
                    confidence_hint = "medium"
            else:
                weights = {p: 1.0 / len(candidates) for p in candidates}
                confidence_hint = "low"

            for p, w in weights.items():
                if p in accum:
                    accum[p].append(log_error * w)

        updates: List[ParameterUpdate] = []

        for p, errors in accum.items():
            if not errors:
                continue

            n = len(errors)
            mean_error = sum(errors) / n

            old = float(params[p]["value"])

            # Convert error into relative parameter update.
            raw_relative_change = self.learning_rate * mean_error

            # Cap update.
            relative_change = max(-self.max_step, min(self.max_step, raw_relative_change))

            proposed = old * (1.0 + relative_change)
            proposed = max(self.min_value, min(self.max_value, proposed))

            if n < self.min_n:
                confidence = "low"
            elif abs(mean_error) < 0.03:
                confidence = "low"
            else:
                confidence = "medium"

            updates.append(ParameterUpdate(
                parameter=p,
                old_value=round(old, 6),
                proposed_value=round(proposed, 6),
                relative_change=round(relative_change, 6),
                support_n=n,
                confidence=confidence,
                rationale=(
                    f"Mean multiplicative prediction error assigned to {p!r} was "
                    f"{round(mean_error, 4)} in log space across {n} observations. "
                    f"Update is capped at ±{self.max_step} per proposal."
                )
            ))

        proposal = {
            "created_at": int(time.time()),
            "mode": self.policy.get("learning", {}).get("mode", "proposal_only"),
            "updates": [asdict(u) for u in updates],
            "skipped": skipped,
            "notes": [
                "This is a parameter update proposal, not an automatic truth.",
                "Human review is required before applying changes.",
                "Attribution quality controls how meaningful the update is."
            ]
        }
        return proposal

    def apply_proposal(
        self,
        proposal: Dict[str, Any],
        parameter_state: Dict[str, Any],
        approved: bool = False,
    ) -> Dict[str, Any]:

        if not approved:
            raise PermissionError("Refusing to apply proposal without explicit approval.")

        state = json.loads(json.dumps(parameter_state))  # deep copy
        params = state.get("parameters", {})

        for upd in proposal.get("updates", []):
            p = upd["parameter"]
            if p not in params:
                continue

            params[p]["value"] = upd["proposed_value"]
            params[p]["source"] = "learned_from_observations"
            params[p]["n_updates"] = int(params[p].get("n_updates", 0)) + 1

        state.setdefault("history", []).append({
            "applied_at": int(time.time()),
            "proposal": proposal
        })

        state["version"] = int(state.get("version", 0)) + 1
        return state
