"""
Experimental Scientist Agent v12.1
----------------------------------
Domain-agnostic scientist loop.

Change from v12:
- Removed hard-coded wet-lab variable defaults such as disturbance_events,
  duration_hours, groups, replicates, container_format, etc.
- Variable discovery is now automatic from observation.context.
- Optional variable_schema can be supplied by an adapter, but the core kernel
  does not assume any domain vocabulary.

Core loop:
    observations
      -> variable discovery
      -> association / causal-signal mining
      -> falsifiable hypotheses
      -> counterfactual candidates
      -> abstract follow-up proposals

This is not an execution protocol generator.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple
import statistics
import math


# =========================================================
# 0. DATA MODELS
# =========================================================

@dataclass
class Observation:
    """
    Generic normalized observation.

    context:
        Input variables / experimental conditions / measured setup facts.
        The kernel does not assume any fixed field names.

    prediction:
        What the prior model/kernel expected.

    outcome:
        What was observed.

    deltas:
        Named prediction errors or outcome deviations.
        Example: {"target_error": 0.12}
    """
    experiment_id: str
    context: Dict[str, Any]
    prediction: Dict[str, Any]
    outcome: Dict[str, Any]
    deltas: Dict[str, float]


@dataclass
class VariableInfo:
    name: str
    kind: str                 # "numeric" | "categorical"
    description: Optional[str] = None
    role: Optional[str] = None # "input" | "condition" | "metadata" | "candidate_cause"


@dataclass
class CausalSignal:
    variable: str
    variable_kind: str
    effect_name: str
    estimated_effect: float
    n: int
    confidence: str
    rationale: str


@dataclass
class Hypothesis:
    id: str
    statement: str
    variable: str
    expected_direction: str
    evidence: List[Dict[str, Any]]
    confidence: str
    falsification_criterion: str


@dataclass
class CandidateIntervention:
    id: str
    variable: str
    change: str
    rationale: str
    expected_effect: Dict[str, Any]
    review_questions: List[str]


@dataclass
class ExperimentProposal:
    id: str
    hypothesis_id: str
    comparison: Dict[str, Any]
    controlled_variables: List[str]
    measured_outcomes: List[str]
    confirm_if: str
    refute_if: str
    risk_notes: List[str]
    human_review_required: bool = True


# =========================================================
# 1. MEMORY STORE
# =========================================================

class ExperimentMemory:
    """
    Minimal in-memory store.

    Production replacements:
    - JSONL
    - SQLite/Postgres
    - ELN/LIMS connector
    - vector memory for similar-run retrieval
    """

    def __init__(self):
        self.observations: List[Observation] = []

    def record(self, observation: Observation) -> None:
        self.observations.append(observation)

    def query(self, filters: Optional[Dict[str, Any]] = None) -> List[Observation]:
        if not filters:
            return list(self.observations)

        out = []
        for obs in self.observations:
            ok = True
            for k, v in filters.items():
                if obs.context.get(k) != v:
                    ok = False
                    break
            if ok:
                out.append(obs)
        return out


# =========================================================
# 2. VARIABLE DISCOVERY
# =========================================================

class VariableDiscovery:
    """
    Infers candidate variables from observation.context.

    The core does not know domain-specific names.
    It only asks:
        - Is this variable numeric?
        - Is this variable categorical?
        - Does it vary across observations?
        - Is it present often enough?
    """

    NON_CAUSAL_DEFAULT_EXCLUDES = {
        "experiment_id",
        "run_id",
        "sample_id",
        "timestamp",
        "date",
        "notes",
        "comment",
        "operator_notes",
    }

    def discover(
        self,
        observations: List[Observation],
        variable_schema: Optional[Dict[str, Dict[str, Any]]] = None,
        min_presence_fraction: float = 0.5,
        max_categorical_cardinality: int = 20,
    ) -> Dict[str, List[VariableInfo]]:

        variable_schema = variable_schema or {}

        if not observations:
            return {"numeric": [], "categorical": []}

        all_keys = set()
        for obs in observations:
            all_keys.update(obs.context.keys())

        numeric_vars: List[VariableInfo] = []
        categorical_vars: List[VariableInfo] = []

        n_obs = len(observations)

        for key in sorted(all_keys):
            if key in self.NON_CAUSAL_DEFAULT_EXCLUDES:
                continue

            schema = variable_schema.get(key, {})
            if schema.get("exclude", False):
                continue

            values = [
                obs.context.get(key)
                for obs in observations
                if key in obs.context and obs.context.get(key) is not None
            ]

            if len(values) / n_obs < min_presence_fraction:
                continue

            # Skip constants; no contrast means no signal.
            unique_values = set(self._safe_hashable(v) for v in values)
            if len(unique_values) < 2:
                continue

            forced_kind = schema.get("kind")

            if forced_kind == "numeric":
                numeric_vars.append(VariableInfo(
                    name=key,
                    kind="numeric",
                    description=schema.get("description"),
                    role=schema.get("role", "candidate_cause"),
                ))
                continue

            if forced_kind == "categorical":
                categorical_vars.append(VariableInfo(
                    name=key,
                    kind="categorical",
                    description=schema.get("description"),
                    role=schema.get("role", "candidate_cause"),
                ))
                continue

            if self._is_numeric(values):
                numeric_vars.append(VariableInfo(
                    name=key,
                    kind="numeric",
                    description=schema.get("description"),
                    role=schema.get("role", "candidate_cause"),
                ))
            elif self._is_categorical(values, max_categorical_cardinality):
                categorical_vars.append(VariableInfo(
                    name=key,
                    kind="categorical",
                    description=schema.get("description"),
                    role=schema.get("role", "candidate_cause"),
                ))

        return {
            "numeric": numeric_vars,
            "categorical": categorical_vars,
        }

    def _is_numeric(self, values: List[Any]) -> bool:
        for v in values:
            if isinstance(v, bool):
                return False
            if not isinstance(v, (int, float)):
                return False
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return False
        return True

    def _is_categorical(self, values: List[Any], max_cardinality: int) -> bool:
        allowed = (str, int, bool)
        if not all(isinstance(v, allowed) for v in values):
            return False
        return len(set(values)) <= max_cardinality

    def _safe_hashable(self, value: Any) -> Any:
        try:
            hash(value)
            return value
        except TypeError:
            return repr(value)


# =========================================================
# 3. CAUSAL SIGNAL MINER
# =========================================================

class CausalSignalMiner:
    """
    Finds candidate associations between discovered variables and target deltas.

    This does not prove causality.
    It produces candidate causal signals for follow-up testing.
    """

    def __init__(self, min_n_for_medium_confidence: int = 3):
        self.min_n = min_n_for_medium_confidence
        self.discovery = VariableDiscovery()

    def mine(
        self,
        observations: List[Observation],
        target_delta: str = "target_error",
        variable_schema: Optional[Dict[str, Dict[str, Any]]] = None,
        numeric_variables: Optional[List[str]] = None,
        categorical_variables: Optional[List[str]] = None,
    ) -> Dict[str, Any]:

        if not observations:
            return {
                "discovered_variables": {"numeric": [], "categorical": []},
                "signals": [],
                "warnings": ["No observations provided."]
            }

        discovered = self.discovery.discover(
            observations=observations,
            variable_schema=variable_schema,
        )

        if numeric_variables is not None:
            numeric_infos = [
                VariableInfo(name=v, kind="numeric", description=(variable_schema or {}).get(v, {}).get("description"))
                for v in numeric_variables
            ]
        else:
            numeric_infos = discovered["numeric"]

        if categorical_variables is not None:
            categorical_infos = [
                VariableInfo(name=v, kind="categorical", description=(variable_schema or {}).get(v, {}).get("description"))
                for v in categorical_variables
            ]
        else:
            categorical_infos = discovered["categorical"]

        signals: List[CausalSignal] = []

        # Numeric variables: high-vs-low contrast around median.
        for info in numeric_infos:
            var = info.name
            pairs = []

            for obs in observations:
                if var in obs.context and target_delta in obs.deltas:
                    try:
                        x = obs.context[var]
                        if isinstance(x, bool):
                            continue
                        pairs.append((float(x), float(obs.deltas[target_delta])))
                    except (TypeError, ValueError):
                        pass

            if len(pairs) < 2:
                continue

            xs = [p[0] for p in pairs]
            median_x = statistics.median(xs)

            high = [y for x, y in pairs if x >= median_x]
            low = [y for x, y in pairs if x < median_x]

            if not high or not low:
                continue

            effect = statistics.mean(high) - statistics.mean(low)
            confidence = "medium" if len(pairs) >= self.min_n else "low"

            signals.append(CausalSignal(
                variable=var,
                variable_kind="numeric",
                effect_name=target_delta,
                estimated_effect=round(effect, 4),
                n=len(pairs),
                confidence=confidence,
                rationale=(
                    f"Higher values of {var!r} were associated with "
                    f"{round(effect, 4)} higher {target_delta} than lower values."
                )
            ))

        # Categorical variables: each category against global mean.
        for info in categorical_infos:
            var = info.name
            buckets: Dict[Any, List[float]] = {}

            for obs in observations:
                if var in obs.context and target_delta in obs.deltas:
                    value = obs.context[var]
                    if isinstance(value, (str, int, bool)):
                        buckets.setdefault(value, []).append(float(obs.deltas[target_delta]))

            if len(buckets) < 2:
                continue

            all_values = [v for vals in buckets.values() for v in vals]
            global_mean = statistics.mean(all_values)

            for category, vals in buckets.items():
                if not vals:
                    continue

                effect = statistics.mean(vals) - global_mean
                confidence = "medium" if len(vals) >= self.min_n else "low"

                signals.append(CausalSignal(
                    variable=f"{var}={category}",
                    variable_kind="categorical",
                    effect_name=target_delta,
                    estimated_effect=round(effect, 4),
                    n=len(vals),
                    confidence=confidence,
                    rationale=(
                        f"Category {var}={category!r} differed from the global mean "
                        f"{target_delta} by {round(effect, 4)}."
                    )
                ))

        signals.sort(key=lambda s: abs(s.estimated_effect), reverse=True)

        warnings = []
        if not signals:
            warnings.append(
                "No candidate signals found. More observations, more variable contrast, or a variable_schema may be needed."
            )

        return {
            "discovered_variables": {
                "numeric": [asdict(v) for v in discovered["numeric"]],
                "categorical": [asdict(v) for v in discovered["categorical"]],
            },
            "signals": signals,
            "warnings": warnings,
        }


# =========================================================
# 4. HYPOTHESIS GENERATOR
# =========================================================

class HypothesisGenerator:
    """Converts candidate signals into falsifiable hypotheses."""

    def generate(self, signals: List[CausalSignal], max_hypotheses: int = 5) -> List[Hypothesis]:
        hypotheses: List[Hypothesis] = []

        for i, signal in enumerate(signals[:max_hypotheses]):
            direction = "increase" if signal.estimated_effect > 0 else "decrease"

            hypotheses.append(Hypothesis(
                id=f"H{i+1}",
                statement=(
                    f"Variable {signal.variable!r} may causally {direction} "
                    f"{signal.effect_name}."
                ),
                variable=signal.variable,
                expected_direction=direction,
                evidence=[asdict(signal)],
                confidence=signal.confidence,
                falsification_criterion=(
                    f"If deliberately changing or controlling {signal.variable!r} "
                    f"does not shift {signal.effect_name} in the expected direction "
                    f"under controlled conditions, weaken or reject this hypothesis."
                )
            ))

        return hypotheses


# =========================================================
# 5. COUNTERFACTUAL ENGINE
# =========================================================

class CounterfactualEngine:
    """Produces abstract counterfactual candidates."""

    def propose_counterfactuals(self, hypothesis: Hypothesis) -> List[CandidateIntervention]:
        var = hypothesis.variable
        expected = hypothesis.expected_direction

        if expected == "increase":
            return [
                CandidateIntervention(
                    id=f"CF_{hypothesis.id}_reduce_or_control",
                    variable=var,
                    change=f"reduce_or_control({var})",
                    rationale=(
                        f"If {var!r} increases the target error, reducing or controlling it "
                        f"should reduce the target error."
                    ),
                    expected_effect={
                        "direction": "target_error_decreases",
                        "basis": "inverse of observed candidate signal",
                    },
                    review_questions=[
                        f"Can {var!r} be changed without changing the scientific question?",
                        "Which variables must be held constant during the comparison?",
                        "What threshold counts as meaningful improvement?",
                    ],
                ),
                CandidateIntervention(
                    id=f"CF_{hypothesis.id}_randomize_or_block",
                    variable=var,
                    change=f"randomize_or_block({var})",
                    rationale=(
                        f"If {var!r} cannot be reduced, randomizing/blocking it can test "
                        f"whether it drives bias or variance."
                    ),
                    expected_effect={
                        "direction": "bias_or_variance_decreases",
                        "basis": "control of suspected confounder",
                    },
                    review_questions=[
                        f"Can observations/runs be blocked or randomized by {var!r}?",
                        "Would randomization create operational or interpretability problems?",
                        "How will identifiers be tracked after randomization?",
                    ],
                ),
            ]

        return [
            CandidateIntervention(
                id=f"CF_{hypothesis.id}_preserve_or_increase",
                variable=var,
                change=f"preserve_or_increase({var})",
                rationale=(
                    f"If {var!r} appears protective, reducing it may worsen the target error."
                ),
                expected_effect={
                    "direction": "target_error_stays_same_or_decreases",
                    "basis": "protective candidate signal",
                },
                review_questions=[
                    f"Is it practical and valid to preserve/increase {var!r}?",
                    "Could the protective signal be confounded?",
                    "What minimal comparison would verify this effect?",
                ],
            )
        ]


# =========================================================
# 6. FOLLOW-UP EXPERIMENT DESIGNER
# =========================================================

class FollowUpExperimentDesigner:
    """
    Designs abstract comparisons to test hypotheses.

    It intentionally does not output step-by-step procedures.
    """

    def design(
        self,
        hypothesis: Hypothesis,
        intervention: CandidateIntervention,
        target_outcome: str = "target_error",
    ) -> ExperimentProposal:

        variable = intervention.variable

        return ExperimentProposal(
            id=f"P_{hypothesis.id}_{intervention.id}",
            hypothesis_id=hypothesis.id,
            comparison={
                "baseline_arm": f"current_or_baseline({variable})",
                "test_arm": intervention.change,
                "comparison_type": "controlled_A_B_or_blocked_comparison",
            },
            controlled_variables=[
                "starting_conditions",
                "measurement_method",
                "timing_or_order_where_relevant",
                "operator_or_batch_where_relevant",
                "all_non_target_variables_as_far_as_possible",
            ],
            measured_outcomes=[
                target_outcome,
                "variance_between_replicates_or_repeated_runs",
                "qc_failure_rate",
                "unexpected_side_effects",
            ],
            confirm_if=(
                f"The test arm changes {target_outcome} in the expected direction "
                f"without unacceptable side effects or QC failures."
            ),
            refute_if=(
                f"The test arm does not shift {target_outcome}, or the apparent effect "
                f"disappears after controlling confounders."
            ),
            risk_notes=[
                "Human/domain expert review required before execution.",
                "Avoid changing multiple candidate causal variables at once unless using a formal factorial design.",
                "Record actual deviations, not only planned settings.",
                "Treat this as hypothesis testing, not proof from one run.",
            ],
            human_review_required=True,
        )


# =========================================================
# 7. SCIENTIST LOOP ORCHESTRATOR
# =========================================================

class ExperimentalScientistAgentV121:
    """
    Complete domain-agnostic scientist loop.
    """

    def __init__(self):
        self.memory = ExperimentMemory()
        self.signal_miner = CausalSignalMiner()
        self.hypothesis_generator = HypothesisGenerator()
        self.counterfactual = CounterfactualEngine()
        self.designer = FollowUpExperimentDesigner()

    def record_observation(self, observation: Observation | Dict[str, Any]) -> None:
        if isinstance(observation, dict):
            observation = Observation(**observation)
        self.memory.record(observation)

    def run(
        self,
        observations: Optional[List[Observation | Dict[str, Any]]] = None,
        target_delta: str = "target_error",
        max_hypotheses: int = 5,
        variable_schema: Optional[Dict[str, Dict[str, Any]]] = None,
        numeric_variables: Optional[List[str]] = None,
        categorical_variables: Optional[List[str]] = None,
    ) -> Dict[str, Any]:

        if observations:
            for obs in observations:
                self.record_observation(obs)

        obs_list = self.memory.query()

        mined = self.signal_miner.mine(
            observations=obs_list,
            target_delta=target_delta,
            variable_schema=variable_schema,
            numeric_variables=numeric_variables,
            categorical_variables=categorical_variables,
        )

        signals = mined["signals"]
        hypotheses = self.hypothesis_generator.generate(signals, max_hypotheses=max_hypotheses)

        interventions: List[CandidateIntervention] = []
        proposals: List[ExperimentProposal] = []

        for h in hypotheses:
            h_interventions = self.counterfactual.propose_counterfactuals(h)
            interventions.extend(h_interventions)

            for intervention in h_interventions:
                proposals.append(self.designer.design(h, intervention, target_outcome=target_delta))

        return {
            "status": "READY" if obs_list else "NEEDS_OBSERVATIONS",
            "n_observations": len(obs_list),
            "target_delta": target_delta,
            "discovered_variables": mined["discovered_variables"],
            "causal_signals": [asdict(s) for s in signals],
            "hypotheses": [asdict(h) for h in hypotheses],
            "candidate_interventions": [asdict(i) for i in interventions],
            "follow_up_proposals": [asdict(p) for p in proposals],
            "warnings": mined["warnings"],
            "notes": [
                "Variable names come from observation.context or an optional variable_schema.",
                "The core has no domain-specific default variables.",
                "Signals are candidates, not proof of causality.",
                "A domain adapter may provide readable variable names and descriptions, but the kernel does not require them.",
            ],
        }


# =========================================================
# 8. TOOL ENTRYPOINT
# =========================================================

def experimental_scientist_agent_v12_1(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expected input:
    {
        "observations": [
            {
                "experiment_id": "run_001",
                "context": {"x1": 1.0, "x2": "A", ...},
                "prediction": {...},
                "outcome": {...},
                "deltas": {"target_error": 0.12}
            }
        ],
        "target_delta": "target_error",
        "variable_schema": {
            "x1": {"kind": "numeric", "description": "optional human-readable description"},
            "x2": {"kind": "categorical"}
        }
    }
    """
    agent = ExperimentalScientistAgentV121()
    return agent.run(
        observations=input_data.get("observations", []),
        target_delta=input_data.get("target_delta", "target_error"),
        max_hypotheses=input_data.get("max_hypotheses", 5),
        variable_schema=input_data.get("variable_schema"),
        numeric_variables=input_data.get("numeric_variables"),
        categorical_variables=input_data.get("categorical_variables"),
    )


# Backward-compatible alias.
def experimental_scientist_agent_v12(input_data: Dict[str, Any]) -> Dict[str, Any]:
    return experimental_scientist_agent_v12_1(input_data)


# =========================================================
# 9. GENERIC EXAMPLE
# =========================================================

if __name__ == "__main__":
    from pprint import pprint

    example = {
        "target_delta": "target_error",
        "observations": [
            {
                "experiment_id": "run_001",
                "context": {
                    "x1": 1,
                    "x2": 24,
                    "x3": 4,
                    "x4": "A",
                },
                "prediction": {"expected": 1.20},
                "outcome": {"observed": 1.25},
                "deltas": {"target_error": 0.05},
            },
            {
                "experiment_id": "run_002",
                "context": {
                    "x1": 3,
                    "x2": 72,
                    "x3": 7,
                    "x4": "A",
                },
                "prediction": {"expected": 1.55},
                "outcome": {"observed": 1.90},
                "deltas": {"target_error": 0.35},
            },
            {
                "experiment_id": "run_003",
                "context": {
                    "x1": 2,
                    "x2": 72,
                    "x3": 10,
                    "x4": "B",
                },
                "prediction": {"expected": 1.45},
                "outcome": {"observed": 1.75},
                "deltas": {"target_error": 0.30},
            },
        ],
        "variable_schema": {
            "x1": {"kind": "numeric", "description": "generic numeric input 1"},
            "x2": {"kind": "numeric", "description": "generic numeric input 2"},
            "x3": {"kind": "numeric", "description": "generic numeric input 3"},
            "x4": {"kind": "categorical", "description": "generic categorical input"},
        }
    }

    pprint(experimental_scientist_agent_v12_1(example))
