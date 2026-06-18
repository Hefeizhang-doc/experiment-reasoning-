"""
Execution Card Generator
------------------------
Bridge between a design/reasoning kernel and a scientific-loop kernel.

Problem it solves:
    design kernel output
        -> execution card / run record template
        -> filled execution card
        -> v12.1-compatible Observation

It does NOT generate protocols.
It does NOT tell the operator how to execute.
It only generates a structured card saying:
    1. What should be recorded during execution
    2. Which checkpoint/QC fields should be filled
    3. Which state to fall back to if a checkpoint fails
    4. How the filled card becomes an Observation for scientific_loop_kernel

Expected upstream:
    wetlab_design_kernel.py or any domain-agnostic design kernel output.

Expected downstream:
    scientific_loop_kernel.py / experimental_scientist_agent_v12_1.py
    Observation shape:
    {
        "experiment_id": "...",
        "context": {...},
        "prediction": {...},
        "outcome": {...},
        "deltas": {"target_error": ...}
    }
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Callable
from pathlib import Path
import json
import math
import time
import uuid


# =========================================================
# 0. UTILITIES
# =========================================================

def _now_unix() -> int:
    return int(time.time())


def _new_id(prefix: str = "run") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def _dig(d: Dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _set_if_present(out: Dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        out[key] = value


def _compact_none(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


# =========================================================
# 1. DATA MODELS
# =========================================================

@dataclass
class RecordingField:
    name: str
    kind: str                         # "number" | "string" | "boolean" | "object" | "list"
    required: bool
    description: str
    expected_from_prediction: Optional[str] = None
    unit: Optional[str] = None


@dataclass
class CheckpointCard:
    checkpoint_id: str
    state: str
    record_fields: List[RecordingField]
    pass_criteria: List[str]
    fail_routes: List[Dict[str, str]]
    notes: List[str]


@dataclass
class ExecutionCard:
    card_id: str
    created_at: int
    design_source: str
    context: Dict[str, Any]
    prediction: Dict[str, Any]
    recording_fields: List[RecordingField]
    checkpoints: List[CheckpointCard]
    observation_template: Dict[str, Any]
    instructions: List[str]


# =========================================================
# 2. PREDICTION EXTRACTOR
# =========================================================

class PredictionExtractor:
    """
    Extracts values from design kernel output that should later be compared
    against actual execution results.

    It is schema-tolerant:
    - supports v8.2-style output
    - supports older wet-lab style field names
    - ignores missing fields
    """

    DEFAULT_PATHS = {
        # Generic v8.2 style
        "predicted_overage_factor": [
            "loss_model.overage_factor",
        ],
        "effective_overage_fraction": [
            "loss_model.effective_overage_fraction",
        ],
        "base_experimental_units": [
            "resources.base_experimental_units",
        ],
        "planning_units_with_layout_reserve": [
            "resources.planning_units_with_layout_reserve",
        ],
        "bulk_liquid_to_prepare_ml": [
            "resources.bulk_liquid_to_prepare_ml",
            # legacy/v6 names
            "resources.media_to_prepare_ml",
            "reagents.media_volume_ml",
        ],
        "starting_quantity_to_prepare": [
            "resources.starting_quantity_to_prepare",
            # legacy wet-lab-ish names, kept only as compatibility paths
            "resources.biological_material_to_prepare_units",
            "resources.cells_to_harvest",
            "plan.cells_needed",
        ],
        "quantity_unit": [
            "resources.quantity_unit",
            "context.facts.quantity_unit",
        ],
    }

    def extract(self, design_output: Dict[str, Any]) -> Dict[str, Any]:
        prediction: Dict[str, Any] = {}

        for canonical_key, paths in self.DEFAULT_PATHS.items():
            for path in paths:
                val = _dig(design_output, path)
                if val is not None:
                    prediction[canonical_key] = val
                    break

        # Keep loss trace if available; useful for later attribution.
        loss_trace = _dig(design_output, "loss_model.expansion_trace")
        if loss_trace is not None:
            prediction["loss_expansion_trace"] = loss_trace

        # Keep layout summary if available; actual positions need not be compared numerically.
        layout_status = _dig(design_output, "layout_advice.status")
        layout_policy = _dig(design_output, "layout_advice.position_policy") or _dig(design_output, "layout_advice.edge_policy")
        if layout_status or layout_policy:
            prediction["layout_summary"] = _compact_none({
                "status": layout_status,
                "policy": layout_policy,
            })

        return prediction


# =========================================================
# 3. CONTEXT EXTRACTOR
# =========================================================

class ContextExtractor:
    """
    Extracts context fields that the scientist loop can later mine as variables.

    The context should include design variables and policy variables, not actual outcomes.
    """

    def extract(self, design_output: Dict[str, Any]) -> Dict[str, Any]:
        # v8.2 style: context.facts is the cleanest source.
        facts = _dig(design_output, "context.facts")
        if isinstance(facts, dict):
            context = dict(facts)
        else:
            context = {}

        # Add resource-level design fields if absent.
        resources = design_output.get("resources", {})
        if isinstance(resources, dict):
            for k in [
                "groups",
                "controls_assumed_or_provided",
                "replicates",
                "base_experimental_units",
                "planning_units_with_layout_reserve",
                "overage_factor",
            ]:
                if k in resources and k not in context:
                    context[k] = resources[k]

        # Add layout policy summary if available.
        layout = design_output.get("layout_advice", {})
        if isinstance(layout, dict):
            for k in ["container_format", "position_policy", "edge_policy", "status"]:
                if k in layout and k not in context:
                    context[k] = layout[k]

        # Add loss factor rates/counts in a generic flat way.
        factors = _dig(design_output, "loss_model.factors")
        if isinstance(factors, list):
            for f in factors:
                if not isinstance(f, dict):
                    continue
                name = f.get("name")
                if not name:
                    continue
                if _is_number(f.get("rate")):
                    context[f"{name}_rate"] = f["rate"]
                if _is_number(f.get("count")):
                    context[f"{name}_count"] = f["count"]

        return context


# =========================================================
# 4. CHECKPOINT EXTRACTOR
# =========================================================

class CheckpointExtractor:
    """
    Turns state_machine output into checkpoint cards.

    If no state machine exists, creates one generic checkpoint.
    """

    def extract(self, design_output: Dict[str, Any]) -> List[CheckpointCard]:
        sm = design_output.get("state_machine", {})
        checkpoint_questions = sm.get("checkpoint_questions", []) if isinstance(sm, dict) else []
        transitions = sm.get("transitions", []) if isinstance(sm, dict) else []

        fail_routes = []
        for t in transitions:
            if not isinstance(t, dict):
                continue
            if t.get("from") == "checkpoint_qc" and t.get("to") != "measurement_or_collection":
                fail_routes.append({
                    "to_state": str(t.get("to")),
                    "guard": str(t.get("guard", "")),
                })

        if not fail_routes:
            fail_routes = [
                {
                    "to_state": "repeat_from_initialization",
                    "guard": "QC failed but repeat is feasible",
                },
                {
                    "to_state": "revise_design",
                    "guard": "QC failure suggests design assumption error",
                },
                {
                    "to_state": "abort_and_document",
                    "guard": "unsafe, invalid, or irrecoverable run state",
                },
            ]

        pass_criteria = checkpoint_questions or [
            "Define observable criteria for continue/repeat/revise/abort before execution.",
        ]

        checkpoint = CheckpointCard(
            checkpoint_id="checkpoint_qc_1",
            state="checkpoint_qc",
            record_fields=[
                RecordingField(
                    name="checkpoint_passed",
                    kind="boolean",
                    required=True,
                    description="Whether the checkpoint met pre-defined acceptance criteria.",
                ),
                RecordingField(
                    name="checkpoint_reason",
                    kind="string",
                    required=False,
                    description="Short explanation for pass/fail decision.",
                ),
                RecordingField(
                    name="deviation_notes",
                    kind="string",
                    required=False,
                    description="Actual deviations from plan, including timing, materials, handling, measurement, or environment.",
                ),
                RecordingField(
                    name="fallback_state_if_failed",
                    kind="string",
                    required=False,
                    description="If checkpoint failed, record chosen fallback state.",
                ),
            ],
            pass_criteria=pass_criteria,
            fail_routes=fail_routes,
            notes=[
                "This checkpoint card is not a protocol step.",
                "It defines what must be recorded so the run can become a scientific-loop observation.",
            ],
        )

        return [checkpoint]


# =========================================================
# 5. EXECUTION CARD GENERATOR
# =========================================================

class ExecutionCardGenerator:
    """
    Main bridge:
        design_output -> execution card -> v12.1 observation template
    """

    def __init__(
        self,
        prediction_extractor: Optional[PredictionExtractor] = None,
        context_extractor: Optional[ContextExtractor] = None,
        checkpoint_extractor: Optional[CheckpointExtractor] = None,
    ):
        self.prediction_extractor = prediction_extractor or PredictionExtractor()
        self.context_extractor = context_extractor or ContextExtractor()
        self.checkpoint_extractor = checkpoint_extractor or CheckpointExtractor()

    def generate(
        self,
        design_output: Dict[str, Any],
        experiment_id: Optional[str] = None,
        design_source: str = "design_kernel",
    ) -> Dict[str, Any]:

        experiment_id = experiment_id or _new_id("run")
        context = self.context_extractor.extract(design_output)
        prediction = self.prediction_extractor.extract(design_output)
        checkpoints = self.checkpoint_extractor.extract(design_output)
        recording_fields = self._build_recording_fields(prediction)
        observation_template = self._build_observation_template(
            experiment_id=experiment_id,
            context=context,
            prediction=prediction,
            recording_fields=recording_fields,
        )

        card = ExecutionCard(
            card_id=f"card_{experiment_id}",
            created_at=_now_unix(),
            design_source=design_source,
            context=context,
            prediction=prediction,
            recording_fields=recording_fields,
            checkpoints=checkpoints,
            observation_template=observation_template,
            instructions=[
                "Fill outcome fields after execution; do not overwrite prediction fields.",
                "Record actual values and deviations, not only planned values.",
                "If checkpoint fails, record fallback_state_if_failed using one of the listed fail routes.",
                "After filling the card, call finalize_observation(card, filled_outcome) to produce a v12.1 Observation.",
            ],
        )

        return self._serialize_card(card)

    def _build_recording_fields(self, prediction: Dict[str, Any]) -> List[RecordingField]:
        fields: List[RecordingField] = [
            RecordingField(
                name="observed_overage_factor",
                kind="number",
                required="predicted_overage_factor" in prediction,
                description="Actual total overage/loss factor observed or reconstructed after run.",
                expected_from_prediction="predicted_overage_factor",
            ),
            RecordingField(
                name="actual_bulk_liquid_used_ml",
                kind="number",
                required="bulk_liquid_to_prepare_ml" in prediction,
                description="Actual bulk liquid consumed/prepared/needed, using the same meaning as prediction.",
                expected_from_prediction="bulk_liquid_to_prepare_ml",
                unit="ml",
            ),
            RecordingField(
                name="actual_starting_quantity_used",
                kind="number",
                required="starting_quantity_to_prepare" in prediction,
                description="Actual starting quantity consumed/prepared/needed, using the same meaning as prediction.",
                expected_from_prediction="starting_quantity_to_prepare",
                unit=prediction.get("quantity_unit"),
            ),
            RecordingField(
                name="resource_shortfall",
                kind="number",
                required=False,
                description="Positive amount if planned resources were insufficient; 0 if none.",
            ),
            RecordingField(
                name="qc_failure_rate",
                kind="number",
                required=False,
                description="Fraction of units/checkpoints that failed QC, if measurable.",
            ),
            RecordingField(
                name="variance_between_replicates_or_runs",
                kind="number",
                required=False,
                description="Observed variance/dispersion metric, if available.",
            ),
            RecordingField(
                name="actual_deviations",
                kind="list",
                required=False,
                description="List of deviations from planned conditions or assumptions.",
            ),
            RecordingField(
                name="operator_notes",
                kind="string",
                required=False,
                description="Free-text notes. Store only if allowed by your privacy policy.",
            ),
        ]

        # Keep only fields that are meaningful, but always keep QC/deviation fields.
        return fields

    def _build_observation_template(
        self,
        experiment_id: str,
        context: Dict[str, Any],
        prediction: Dict[str, Any],
        recording_fields: List[RecordingField],
    ) -> Dict[str, Any]:

        outcome = {f.name: None for f in recording_fields}
        deltas = {
            "target_error": None,
            "overage_factor_error": None,
            "bulk_liquid_error_ml": None,
            "starting_quantity_error": None,
            "resource_shortfall": None,
            "qc_failure_rate": None,
        }

        return {
            "experiment_id": experiment_id,
            "context": context,
            "prediction": prediction,
            "outcome": outcome,
            "deltas": deltas,
            "attribution": {},
            "metadata": {
                "status": "TEMPLATE_NOT_FILLED",
                "created_at": _now_unix(),
            },
        }

    def _serialize_card(self, card: ExecutionCard) -> Dict[str, Any]:
        d = asdict(card)
        return d


# =========================================================
# 6. FINALIZE FILLED CARD -> V12.1 OBSERVATION
# =========================================================

class ObservationFinalizer:
    """
    Converts a filled execution card to v12.1-compatible Observation.

    Required:
        card generated by ExecutionCardGenerator
        filled_outcome dict with actual values

    Output:
        {
            experiment_id,
            context,
            prediction,
            outcome,
            deltas
        }
    """

    def finalize(
        self,
        card: Dict[str, Any],
        filled_outcome: Dict[str, Any],
        attribution: Optional[Dict[str, float]] = None,
        target_delta: str = "target_error",
    ) -> Dict[str, Any]:

        template = card.get("observation_template", {})
        experiment_id = template.get("experiment_id") or _new_id("run")
        context = dict(template.get("context", {}))
        prediction = dict(template.get("prediction", {}))
        outcome = dict(template.get("outcome", {}))
        outcome.update(filled_outcome)

        deltas = self._compute_deltas(prediction, outcome)

        # Default target_error: prefer overage factor error, then normalized resource error.
        if target_delta not in deltas or deltas.get(target_delta) is None:
            deltas[target_delta] = self._choose_target_error(deltas)

        observation = {
            "experiment_id": experiment_id,
            "context": context,
            "prediction": prediction,
            "outcome": outcome,
            "deltas": deltas,
            "attribution": attribution or {},
            "metadata": {
                "status": "FILLED",
                "finalized_at": _now_unix(),
                "source_card_id": card.get("card_id"),
            },
        }
        return observation

    def _compute_deltas(self, prediction: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Optional[float]]:
        deltas: Dict[str, Optional[float]] = {
            "target_error": None,
            "overage_factor_error": None,
            "bulk_liquid_error_ml": None,
            "starting_quantity_error": None,
            "resource_shortfall": None,
            "qc_failure_rate": None,
        }

        pred_factor = prediction.get("predicted_overage_factor")
        obs_factor = outcome.get("observed_overage_factor")
        if _is_number(pred_factor) and _is_number(obs_factor):
            deltas["overage_factor_error"] = round(float(obs_factor) - float(pred_factor), 6)

        pred_liquid = prediction.get("bulk_liquid_to_prepare_ml")
        actual_liquid = outcome.get("actual_bulk_liquid_used_ml")
        if _is_number(pred_liquid) and _is_number(actual_liquid):
            deltas["bulk_liquid_error_ml"] = round(float(actual_liquid) - float(pred_liquid), 6)

        pred_qty = prediction.get("starting_quantity_to_prepare")
        actual_qty = outcome.get("actual_starting_quantity_used")
        if _is_number(pred_qty) and _is_number(actual_qty):
            deltas["starting_quantity_error"] = round(float(actual_qty) - float(pred_qty), 6)

        if _is_number(outcome.get("resource_shortfall")):
            deltas["resource_shortfall"] = float(outcome["resource_shortfall"])

        if _is_number(outcome.get("qc_failure_rate")):
            deltas["qc_failure_rate"] = float(outcome["qc_failure_rate"])

        return deltas

    def _choose_target_error(self, deltas: Dict[str, Optional[float]]) -> Optional[float]:
        for key in [
            "overage_factor_error",
            "bulk_liquid_error_ml",
            "starting_quantity_error",
            "resource_shortfall",
            "qc_failure_rate",
        ]:
            val = deltas.get(key)
            if val is not None:
                return val
        return None


# =========================================================
# 7. FUNCTION ENTRYPOINTS
# =========================================================

def generate_execution_card(
    design_output: Dict[str, Any],
    experiment_id: Optional[str] = None,
    design_source: str = "design_kernel",
) -> Dict[str, Any]:
    return ExecutionCardGenerator().generate(
        design_output=design_output,
        experiment_id=experiment_id,
        design_source=design_source,
    )


def finalize_observation(
    card: Dict[str, Any],
    filled_outcome: Dict[str, Any],
    attribution: Optional[Dict[str, float]] = None,
    target_delta: str = "target_error",
) -> Dict[str, Any]:
    return ObservationFinalizer().finalize(
        card=card,
        filled_outcome=filled_outcome,
        attribution=attribution,
        target_delta=target_delta,
    )


# =========================================================
# 8. JSON CLI HELPERS
# =========================================================

def generate_card_from_json_file(
    design_output_path: str | Path,
    output_path: Optional[str | Path] = None,
    experiment_id: Optional[str] = None,
) -> Dict[str, Any]:
    design_output_path = Path(design_output_path)
    design_output = json.loads(design_output_path.read_text(encoding="utf-8"))

    card = generate_execution_card(
        design_output=design_output,
        experiment_id=experiment_id,
        design_source=str(design_output_path),
    )

    if output_path:
        Path(output_path).write_text(json.dumps(card, indent=2, ensure_ascii=False), encoding="utf-8")

    return card


def finalize_observation_from_json_files(
    card_path: str | Path,
    filled_outcome_path: str | Path,
    output_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    card = json.loads(Path(card_path).read_text(encoding="utf-8"))
    filled_outcome = json.loads(Path(filled_outcome_path).read_text(encoding="utf-8"))

    observation = finalize_observation(card, filled_outcome)

    if output_path:
        Path(output_path).write_text(json.dumps(observation, indent=2, ensure_ascii=False), encoding="utf-8")

    return observation


# =========================================================
# 9. EXAMPLE
# =========================================================

if __name__ == "__main__":
    from pprint import pprint

    # Minimal design-kernel-like output.
    example_design_output = {
        "context": {
            "facts": {
                "groups": 7,
                "replicates": 3,
                "container_format": "96-unit",
                "duration_hours": 72,
                "volume_per_unit_ul": 100,
                "quantity_per_unit": 20000,
                "quantity_unit": "units",
                "disturbance_events": 3,
            }
        },
        "loss_model": {
            "overage_factor": 1.9008,
            "effective_overage_fraction": 0.9008,
            "expansion_trace": [
                {"name": "transfer_overage", "formula": "(1 + 0.08)^1"},
                {"name": "disturbance_loss", "formula": "(1 + 0.20)^3"},
                {"name": "long_duration_drift", "formula": "(1 + 0.10)^1"},
            ],
            "factors": [
                {"name": "transfer_overage", "rate": 0.08, "count": 1},
                {"name": "disturbance_loss", "rate": 0.20, "count": 3},
                {"name": "long_duration_drift", "rate": 0.10, "count": 1},
            ],
        },
        "resources": {
            "groups": 7,
            "controls_assumed_or_provided": 1,
            "replicates": 3,
            "base_experimental_units": 24,
            "planning_units_with_layout_reserve": 27,
            "overage_factor": 1.9008,
            "bulk_liquid_to_prepare_ml": 5.13,
            "starting_quantity_to_prepare": 1026432,
            "quantity_unit": "units",
        },
        "state_machine": {
            "checkpoint_questions": [
                "What observable criterion means continue?",
                "What criterion means repeat from initialization?",
                "What criterion means revise design rather than repeat?",
                "What criterion means abort and document?",
            ],
            "transitions": [
                {"from": "checkpoint_qc", "to": "measurement_or_collection", "guard": "QC acceptable"},
                {"from": "checkpoint_qc", "to": "repeat_from_initialization", "guard": "QC failed but repeat feasible"},
                {"from": "checkpoint_qc", "to": "revise_design", "guard": "assumption error suspected"},
                {"from": "checkpoint_qc", "to": "abort_and_document", "guard": "irrecoverable state"},
            ],
        },
    }

    card = generate_execution_card(example_design_output, experiment_id="run_demo_001")
    print("\n=== EXECUTION CARD ===")
    pprint(card)

    filled = {
        "observed_overage_factor": 2.05,
        "actual_bulk_liquid_used_ml": 5.8,
        "actual_starting_quantity_used": 1100000,
        "resource_shortfall": 0,
        "qc_failure_rate": 0.04,
        "actual_deviations": ["example deviation note"],
    }

    obs = finalize_observation(card, filled)
    print("\n=== FINAL OBSERVATION ===")
    pprint(obs)
