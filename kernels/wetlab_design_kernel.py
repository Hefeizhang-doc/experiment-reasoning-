"""
Experimental Reasoning Kernel v8.2
----------------------------------
Domain-agnostic experimental thinking framework.

Core principle:
- The kernel must not know assay types, cell types, reagent names, or biology-specific labels.
- Domain-specific facts such as "suspension", "adherent", "virus", "transfection",
  "wash", "media change", etc. belong in an external adapter/config layer.
- The kernel only reasons over abstract experimental units, resources, disturbances,
  constraints, states, and review questions.

This is a reasoning kernel, not a wet-lab protocol generator.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional
import math


# =========================================================
# 0. CONFIG MODELS
# =========================================================

@dataclass
class LossFactor:
    """
    Generic loss/overage factor.

    Examples of valid domain-agnostic names:
    - "transfer_overage"
    - "dead_volume_overage"
    - "disturbance_loss"
    - "long_duration_drift"
    - "batching_overhead"

    The kernel does not know what causes them biologically/chemically.
    """
    name: str
    rate: float
    rationale: str
    count: int = 1
    source: str = "configurable_default"


@dataclass
class ReviewItem:
    severity: str           # "info" | "review" | "warning" | "blocker"
    topic: str
    message: str
    question: Optional[str] = None
    suggested_action: Optional[str] = None


@dataclass
class ContainerGeometry:
    rows: List[str]
    cols: List[int]
    preferred_rows: List[str]
    preferred_cols: List[int]
    notes: str = ""


DEFAULT_CONTAINERS: Dict[str, ContainerGeometry] = {
    "96-unit": ContainerGeometry(
        rows=list("ABCDEFGH"),
        cols=list(range(1, 13)),
        preferred_rows=list("BCDEFG"),
        preferred_cols=list(range(2, 12)),
        notes="Default guidance: avoid high-risk perimeter positions unless explicitly allowed."
    ),
    "24-unit": ContainerGeometry(
        rows=list("ABCD"),
        cols=list(range(1, 7)),
        preferred_rows=list("BC"),
        preferred_cols=list(range(2, 6)),
        notes="Preferred interior capacity is limited; review position strategy manually."
    ),
    "6-unit": ContainerGeometry(
        rows=list("AB"),
        cols=list(range(1, 4)),
        preferred_rows=list("AB"),
        preferred_cols=list(range(1, 4)),
        notes="No meaningful interior-only region; use balancing/randomization guidance instead."
    ),
}


DEFAULT_LOSS_FACTORS: Dict[str, LossFactor] = {
    "transfer_overage": LossFactor(
        name="transfer_overage",
        rate=0.08,
        rationale="Generic reserve for transfer variation, dead volume, and measurement imprecision.",
        count=1,
    ),
    "disturbance_loss": LossFactor(
        name="disturbance_loss",
        rate=0.10,
        rationale="Generic per-event material loss caused by repeated disturbance/handling events.",
        count=1,
    ),
    "long_duration_drift": LossFactor(
        name="long_duration_drift",
        rate=0.10,
        rationale="Generic global reserve for drift accumulating over long-duration experiments.",
        count=1,
    ),
    "batching_overhead": LossFactor(
        name="batching_overhead",
        rate=0.10,
        rationale="Generic reserve for labeling, batching, timing, and grouping complexity.",
        count=1,
    ),
}


# =========================================================
# 1. CONTEXT: FACTS ONLY
# =========================================================

class Context:
    """
    Collect raw facts and explicit booleans.
    No assay labels. No cell labels. No domain-specific vocabulary.
    """

    REQUIRED_FOR_BASIC_PLANNING = ["groups", "replicates"]

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        facts = {
            # Design size
            "groups": input_data.get("groups"),
            "replicates": input_data.get("replicates"),
            "controls": input_data.get("controls"),

            # Generic container/unit geometry
            "container_format": input_data.get("container_format", input_data.get("plate_format")),
            "emit_concrete_layout": bool(input_data.get("emit_concrete_layout", False)),
            "allow_nonpreferred_positions": bool(input_data.get("allow_nonpreferred_positions", False)),

            # Generic resources
            "volume_per_unit_ul": input_data.get("volume_per_unit_ul", input_data.get("volume_per_well_ul")),
            "quantity_per_unit": input_data.get("quantity_per_unit"),
            "quantity_unit": input_data.get("quantity_unit", "units"),

            # Generic time/events
            "duration_hours": input_data.get("duration_hours"),
            "disturbance_events": input_data.get("disturbance_events"),

            # Generic loss configuration
            # These let a domain adapter inject facts without the kernel knowing the domain.
            "transfer_overage_rate": input_data.get("transfer_overage_rate", 0.08),
            "disturbance_loss_per_event": input_data.get("disturbance_loss_per_event", 0.10),
            "long_duration_drift_rate": input_data.get("long_duration_drift_rate", 0.10),
            "batching_overhead_rate": input_data.get("batching_overhead_rate", 0.10),

            # Thresholds/policies
            "long_duration_threshold_hours": input_data.get("long_duration_threshold_hours", 48),
            "many_groups_threshold": input_data.get("many_groups_threshold", 8),
            "layout_reserve_fraction": input_data.get("layout_reserve_fraction", 0.10),
        }

        missing = [k for k in self.REQUIRED_FOR_BASIC_PLANNING if facts.get(k) is None]

        flags = {
            "has_many_groups": (
                facts.get("groups") is not None
                and facts["groups"] > facts["many_groups_threshold"]
            ),
            "has_low_replicates": (
                facts.get("replicates") is not None
                and facts["replicates"] < 3
            ),
            "is_long_duration": (
                facts.get("duration_hours") is not None
                and facts["duration_hours"] > facts["long_duration_threshold_hours"]
            ),
            "has_explicit_disturbance_count": facts.get("disturbance_events") is not None,
            "has_volume_info": facts.get("volume_per_unit_ul") is not None,
            "has_quantity_info": facts.get("quantity_per_unit") is not None,
            "has_container_info": facts.get("container_format") is not None,
        }

        return {
            "facts": facts,
            "flags": flags,
            "missing": missing,
        }


# =========================================================
# 2. DECISION ADVISOR: WARN, DO NOT DECIDE
# =========================================================

class DecisionAdvisor:
    """
    Surface risks and review questions.
    It does not decide for the scientist/operator.
    """

    def run(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        facts, flags, missing = ctx["facts"], ctx["flags"], ctx["missing"]
        items: List[ReviewItem] = []

        if missing:
            items.append(ReviewItem(
                severity="blocker",
                topic="missing_core_design",
                message=f"Core planning fields are missing: {missing}.",
                question="Can these be provided before estimating resources/layout?",
                suggested_action="Ask for missing fields rather than guessing."
            ))

        groups = facts.get("groups") or 0
        reps = facts.get("replicates") or 0
        controls = facts.get("controls")
        assumed_controls = controls if controls is not None else max(1, int(groups * 0.2)) if groups else 0

        if groups and reps:
            items.append(ReviewItem(
                severity="info",
                topic="unit_count_preview",
                message=f"Base experimental units before reserve: {(groups + assumed_controls) * reps}.",
                question="Do you want concrete positions/resources, or only planning constraints?",
                suggested_action="Set emit_concrete_layout=True only when concrete assignment is needed."
            ))

        if flags["has_many_groups"]:
            items.append(ReviewItem(
                severity="review",
                topic="complexity_and_batching",
                message=f"{groups} groups may increase batching, timing, and labeling burden.",
                question="Can all groups be handled within the same operational window?",
                suggested_action="Consider blocking, randomization, or splitting only after user confirmation."
            ))

        if flags["has_low_replicates"]:
            items.append(ReviewItem(
                severity="review",
                topic="replicate_count",
                message=f"{reps} replicates may be weak depending on the experimental purpose.",
                question="Is this exploratory, confirmatory, or production-critical?",
                suggested_action="Mark inference strength explicitly."
            ))

        if flags["is_long_duration"] and not flags["has_explicit_disturbance_count"]:
            items.append(ReviewItem(
                severity="review",
                topic="duration_vs_disturbance_count",
                message="Long duration can imply repeated handling/disturbance events, but event count is unknown.",
                question="How many disturbance events should loss be applied across?",
                suggested_action="Provide disturbance_events or set disturbance_loss_per_event from a domain adapter."
            ))

        return {
            "status": "BLOCKED" if any(i.severity == "blocker" for i in items) else "REVIEW_READY",
            "items": [asdict(i) for i in items],
        }


# =========================================================
# 3. GENERIC EXPLAINABLE LOSS MODEL
# =========================================================

class LossModel:
    """
    Multiplicative overage model using explicit generic factors.

    Formula:
        overage_factor = Π (1 + rate_i) ^ count_i

    Domain-specific adapters should translate their local knowledge into:
        disturbance_loss_per_event
        disturbance_events
        transfer_overage_rate
        long_duration_drift_rate
        batching_overhead_rate
    """

    def _infer_disturbance_count(self, ctx: Dict[str, Any]) -> tuple[int, str, bool]:
        facts = ctx["facts"]

        if facts.get("disturbance_events") is not None:
            return int(facts["disturbance_events"]), "explicit disturbance_events provided", False

        duration = facts.get("duration_hours")
        if duration is None:
            return 1, "no duration or event count; applied once as placeholder", True

        # Generic heuristic only. Not biology-, chemistry-, or assay-specific.
        inferred = max(1, math.floor(duration / 24))
        return inferred, "heuristically inferred from duration_hours; override with explicit disturbance_events", True

    def run(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        facts, flags = ctx["facts"], ctx["flags"]

        disturbance_count, count_rationale, inferred = self._infer_disturbance_count(ctx)

        factors: List[LossFactor] = [
            LossFactor(
                name="transfer_overage",
                rate=float(facts["transfer_overage_rate"]),
                rationale="Generic reserve for transfer/dead-volume/measurement imprecision.",
                count=1,
                source="input_or_default",
            ),
            LossFactor(
                name="disturbance_loss",
                rate=float(facts["disturbance_loss_per_event"]),
                rationale="Generic per-event loss; domain adapters should set this from local knowledge.",
                count=disturbance_count,
                source="input_or_default",
            ),
        ]

        if flags["is_long_duration"]:
            factors.append(LossFactor(
                name="long_duration_drift",
                rate=float(facts["long_duration_drift_rate"]),
                rationale="Generic global reserve for accumulated time-dependent drift.",
                count=1,
                source="input_or_default",
            ))

        if flags["has_many_groups"]:
            factors.append(LossFactor(
                name="batching_overhead",
                rate=float(facts["batching_overhead_rate"]),
                rationale="Generic reserve for grouping, labeling, timing, and batching complexity.",
                count=1,
                source="input_or_default",
            ))

        overage_factor = 1.0
        trace = []

        for f in factors:
            multiplier = (1 + f.rate) ** max(1, int(f.count))
            overage_factor *= multiplier
            trace.append({
                "name": f.name,
                "formula": f"(1 + {f.rate})^{f.count}",
                "multiplier": round(multiplier, 4),
                "rationale": f.rationale,
                "source": f.source,
            })

        questions = []
        if inferred:
            questions.append(
                "disturbance_events was inferred, not observed. Provide an explicit event count for better estimates."
            )

        return {
            "overage_factor": round(overage_factor, 4),
            "effective_overage_fraction": round(overage_factor - 1, 4),
            "factors": [asdict(f) for f in factors],
            "expansion_trace": trace,
            "questions": questions,
            "note": "The kernel is domain-agnostic; all rates and event counts should be tuned by adapters or local historical data."
        }


# =========================================================
# 4. GENERIC STATE MACHINE WITH BRANCHES
# =========================================================

class ExperimentStateMachine:
    """Generic experimental state graph with QC and recovery branches."""

    def run(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "states": [
                "designed",
                "resources_ready",
                "initialized",
                "condition_applied",
                "response_or_waiting_period",
                "checkpoint_qc",
                "measurement_or_collection",
                "data_qc",
                "completed",
                "revise_design",
                "repeat_from_initialization",
                "abort_and_document",
            ],
            "transitions": [
                {"from": "designed", "to": "resources_ready", "guard": "resources/equipment/labels/timing confirmed"},
                {"from": "resources_ready", "to": "initialized", "guard": "starting state passes pre-run QC"},
                {"from": "initialized", "to": "condition_applied", "guard": "assignment recorded and timing started"},
                {"from": "condition_applied", "to": "response_or_waiting_period", "guard": "condition applied within allowed window"},
                {"from": "response_or_waiting_period", "to": "checkpoint_qc", "guard": "checkpoint reached"},
                {"from": "checkpoint_qc", "to": "measurement_or_collection", "guard": "QC acceptable"},
                {"from": "checkpoint_qc", "to": "repeat_from_initialization", "guard": "QC failed but repeat is feasible"},
                {"from": "checkpoint_qc", "to": "revise_design", "guard": "QC failure suggests design assumption error"},
                {"from": "checkpoint_qc", "to": "abort_and_document", "guard": "unsafe/contaminated/irrecoverable state"},
                {"from": "measurement_or_collection", "to": "data_qc", "guard": "measurement/collection complete"},
                {"from": "data_qc", "to": "completed", "guard": "data complete and annotated"},
                {"from": "data_qc", "to": "revise_design", "guard": "data quality exposes design/measurement flaw"},
            ],
            "checkpoint_questions": [
                "What observable criterion means continue?",
                "What criterion means repeat from initialization?",
                "What criterion means revise design rather than repeat?",
                "What criterion means abort and document?",
            ],
        }


# =========================================================
# 5. GENERIC RESOURCE PLANNER
# =========================================================

class ResourcePlanner:
    """Compute resources only from generic facts."""

    def run(self, ctx: Dict[str, Any], loss: Dict[str, Any]) -> Dict[str, Any]:
        facts, flags = ctx["facts"], ctx["flags"]

        groups = facts.get("groups")
        reps = facts.get("replicates")
        controls = facts.get("controls")

        if groups is None or reps is None:
            return {
                "status": "NEEDS_INPUT",
                "missing": ["groups", "replicates"],
                "questions": ["How many groups and replicates are planned?"],
            }

        controls = controls if controls is not None else max(1, int(groups * 0.2))
        base_units = (groups + controls) * reps
        planning_units = math.ceil(base_units * (1 + facts["layout_reserve_fraction"]))

        out: Dict[str, Any] = {
            "groups": groups,
            "controls_assumed_or_provided": controls,
            "replicates": reps,
            "base_experimental_units": base_units,
            "planning_units_with_layout_reserve": planning_units,
            "overage_factor": loss["overage_factor"],
        }

        if flags["has_volume_info"]:
            out["bulk_liquid_to_prepare_ml"] = round(
                planning_units * facts["volume_per_unit_ul"] / 1000 * loss["overage_factor"],
                2
            )
        else:
            out.setdefault("questions", []).append(
                "What volume per experimental unit should be used for liquid resource estimation?"
            )

        if flags["has_quantity_info"]:
            out["starting_quantity_to_prepare"] = math.ceil(
                planning_units * facts["quantity_per_unit"] * loss["overage_factor"]
            )
            out["quantity_unit"] = facts["quantity_unit"]
        else:
            out.setdefault("questions", []).append(
                "What starting quantity per experimental unit should be used for non-liquid resource estimation?"
            )

        return out


# =========================================================
# 6. GENERIC LAYOUT ADVISOR
# =========================================================

class LayoutAdvisor:
    """Default output is guidance; concrete assignment is optional."""

    def run(self, ctx: Dict[str, Any], resource: Dict[str, Any]) -> Dict[str, Any]:
        facts = ctx["facts"]
        fmt = facts.get("container_format")

        if not fmt:
            return {
                "status": "NEEDS_INPUT",
                "questions": ["What container format is being used?"],
            }

        geom = DEFAULT_CONTAINERS.get(fmt)
        if not geom:
            return {
                "status": "UNKNOWN_FORMAT",
                "container_format": fmt,
                "questions": [
                    "What are the container dimensions?",
                    "Which positions are preferred, high-risk, or restricted?",
                ],
            }

        preferred_capacity = len(geom.preferred_rows) * len(geom.preferred_cols)
        total_capacity = len(geom.rows) * len(geom.cols)
        planning_units = resource.get("planning_units_with_layout_reserve")

        advice: Dict[str, Any] = {
            "status": "OK",
            "container_format": fmt,
            "position_policy": "use_preferred_positions_by_default",
            "preferred_positions": {
                "rows": geom.preferred_rows,
                "cols": geom.preferred_cols,
                "capacity": preferred_capacity,
            },
            "total_capacity": total_capacity,
            "notes": [geom.notes],
            "review_questions": [
                "Which positions are preferred, high-risk, restricted, or reserved?",
                "Should groups be randomized, blocked, or kept contiguous?",
                "Do sample IDs map unambiguously to positions?",
            ],
        }

        if planning_units is not None and planning_units > preferred_capacity and not facts["allow_nonpreferred_positions"]:
            advice["status"] = "REVIEW"
            advice["notes"].append(
                f"Planning units ({planning_units}) exceed preferred-position capacity ({preferred_capacity})."
            )
            advice["review_questions"].append(
                "Do you want to split containers, reduce design size, or allow nonpreferred positions?"
            )

        if facts["emit_concrete_layout"]:
            advice["concrete_layout"] = self._emit_layout(ctx, geom)

        return advice

    def _emit_layout(self, ctx: Dict[str, Any], geom: ContainerGeometry) -> List[Dict[str, Any]]:
        facts = ctx["facts"]
        groups = facts["groups"]
        reps = facts["replicates"]
        controls = facts.get("controls")
        controls = controls if controls is not None else max(1, int(groups * 0.2))

        rows = geom.rows if facts["allow_nonpreferred_positions"] else geom.preferred_rows
        cols = geom.cols if facts["allow_nonpreferred_positions"] else geom.preferred_cols
        positions = [f"{r}{c}" for c in cols for r in rows]

        layout: List[Dict[str, Any]] = []
        idx = 0

        for g in range(groups + controls):
            label = f"group_{g}" if g < groups else f"control_{g - groups}"
            for rep in range(reps):
                if idx >= len(positions):
                    layout.append({
                        "position": None,
                        "group": label,
                        "replicate": rep,
                        "warning": "No available preferred position; review layout constraints."
                    })
                else:
                    layout.append({
                        "position": positions[idx],
                        "group": label,
                        "replicate": rep,
                    })
                idx += 1

        return layout


# =========================================================
# 7. ORCHESTRATOR
# =========================================================

class ExperimentalReasoningKernelV82:
    def __init__(self):
        self.context = Context()
        self.decision = DecisionAdvisor()
        self.loss = LossModel()
        self.state_machine = ExperimentStateMachine()
        self.resource = ResourcePlanner()
        self.layout = LayoutAdvisor()

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        ctx = self.context.run(input_data)
        decision = self.decision.run(ctx)
        loss = self.loss.run(ctx)
        resources = self.resource.run(ctx, loss)
        layout = self.layout.run(ctx, resources)
        state_machine = self.state_machine.run(ctx)

        return {
            "context": ctx,
            "decision_advice": decision,
            "loss_model": loss,
            "state_machine": state_machine,
            "resources": resources,
            "layout_advice": layout,
        }


# =========================================================
# TOOL ENTRYPOINT
# =========================================================

def experimental_reasoning_kernel_v8_2(input_data: Dict[str, Any]) -> Dict[str, Any]:
    return ExperimentalReasoningKernelV82().run(input_data)


# Backward-compatible alias name.
def wetlab_reasoning_skill_v8_2(input_data: Dict[str, Any]) -> Dict[str, Any]:
    return experimental_reasoning_kernel_v8_2(input_data)


if __name__ == "__main__":
    from pprint import pprint

    pprint(experimental_reasoning_kernel_v8_2({
        "groups": 7,
        "replicates": 3,
        "container_format": "96-unit",
        "duration_hours": 72,
        "volume_per_unit_ul": 100,
        "quantity_per_unit": 20000,
        "quantity_unit": "units",
        "disturbance_events": 3,
        "disturbance_loss_per_event": 0.20,
        "emit_concrete_layout": False,
    }))
