"""Explainability: a structured, non-fabricated explanation of one
batch/row's hybrid detection outcome (`app.services.hybrid_detection`)
and risk assessment (`app.services.risk_scoring`), built strictly from
values those two modules already computed, plus — only when genuinely
available — the trained supervised model's own global feature
importances.

    HybridOutcomeResult + RiskScoreResult
        |
    build_explanation()  <- measured values, rule-based statements,
        |                    explicit "unavailable" markers, optional
        |                    genuine global feature importance
    Explanation
        |
    persist_explanations()
        |
    DetectionExplanation rows

What this module explicitly does NOT do, by design: it never computes a
per-instance (this-specific-row) feature attribution — no SHAP, no LIME,
no permutation importance on this row — because none of those methods
are implemented anywhere in this codebase, and fabricating a plausible-
looking per-row attribution without a real implementation would be
exactly the kind of invented "model reasoning" the project's conventions
(see e.g. `app.services.anomaly_inference`'s module docstring) rule out.
The one feature-level fact this module *can* truthfully report is the
supervised `RandomForestClassifier`'s own `feature_importances_` — a
real, already-computed, global (whole-model, not per-row) measure of how
much each feature contributed to the model's splits during training.
Every explanation that includes it is explicitly labeled
`"global_feature_importance"` and documents that it describes the model
in general, not a causal claim about this particular row; when the
artifact cannot be loaded or the loaded model does not expose
`feature_importances_`, the explanation says so in `unavailable_
information` instead of omitting the key silently.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

import joblib
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.detection_explanation import DetectionExplanation
from app.models.hybrid_detection_result import HybridDetectionResult
from app.models.model_metadata import ModelMetadata
from app.services.feature_extraction import FEATURE_SCHEMA
from app.services.hybrid_detection import HybridOutcome, HybridOutcomeResult
from app.services.risk_scoring import RiskScoreResult

logger = logging.getLogger("ai_ids")

_OUTCOME_DESCRIPTIONS: dict[HybridOutcome, str] = {
    HybridOutcome.CONFIRMED_ATTACK: (
        "The supervised classifier predicted ATTACK and the anomaly detector independently flagged this "
        "row as anomalous; both signals agree."
    ),
    HybridOutcome.SUPERVISED_ATTACK_ONLY: (
        "The supervised classifier predicted ATTACK; the anomaly detector did not flag this row (or no "
        "anomaly result was available for it)."
    ),
    HybridOutcome.ANOMALY_ONLY: (
        "The supervised classifier predicted BENIGN, but the anomaly detector independently flagged this "
        "row as statistically unusual relative to its training baseline."
    ),
    HybridOutcome.CLEAN: (
        "The supervised classifier predicted BENIGN and the anomaly detector did not flag this row (or no "
        "anomaly result was available for it)."
    ),
}


class ExplanationPersistenceError(Exception):
    """Raised when this module cannot safely persist a set of
    explanations — never partially, and never silently."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class FeatureImportanceEntry:
    feature_name: str
    importance: float


@dataclass(frozen=True)
class Explanation:
    """A structured account with three clearly separated kinds of
    content, matching Step 35's requirement to distinguish them:

    - `measured_values`: real numbers/labels already produced by the
      supervised/anomaly/hybrid/risk pipeline — never recomputed here.
    - `rule_based_explanations`: plain statements about which hybrid
      rule fired and why the risk score landed where it did — derived
      from already-computed outcomes, not a new inference.
    - `unavailable_information`: explicit statements about what could
      not be determined (a missing anomaly result, a missing
      probability, an unavailable feature-importance), so an absence is
      never silently indistinguishable from "nothing to report."
    - `feature_importance`: `None` unless a genuine, already-trained
      model's global `feature_importances_` could be loaded — see the
      module docstring.
    """

    measured_values: dict
    rule_based_explanations: list[str]
    unavailable_information: list[str]
    feature_importance: list[FeatureImportanceEntry] | None
    feature_importance_scope: str | None
    """`"global_feature_importance"` when `feature_importance` is
    present (whole-model, not per-row) — `None` when it is absent."""

    def as_dict(self) -> dict:
        return {
            "measured_values": self.measured_values,
            "rule_based_explanations": list(self.rule_based_explanations),
            "unavailable_information": list(self.unavailable_information),
            "feature_importance": (
                [asdict(entry) for entry in self.feature_importance]
                if self.feature_importance is not None
                else None
            ),
            "feature_importance_scope": self.feature_importance_scope,
        }


def load_global_feature_importance(db: Session, model_id: uuid.UUID) -> list[FeatureImportanceEntry] | None:
    """Loads the supervised model's own trained artifact and reports its
    real `feature_importances_`, paired with `FEATURE_SCHEMA`'s feature
    names in the same fixed order training/inference already use.
    Returns `None` (never a fabricated or default-even importance list)
    whenever the registry entry, its artifact, or the loaded model
    itself does not genuinely provide this — a missing/corrupt artifact,
    a model with no `feature_importances_` attribute, or a result whose
    length does not match `FEATURE_SCHEMA` (an internal inconsistency
    that must not be papered over by truncating or padding).
    """
    registry_entry = db.get(ModelMetadata, model_id)
    if registry_entry is None or not registry_entry.artifact_path:
        return None

    try:
        payload = joblib.load(registry_entry.artifact_path)
    except Exception:
        logger.exception("Could not load model artifact %s for feature-importance explanation", model_id)
        return None

    if not isinstance(payload, dict):
        return None

    model = payload.get("model")
    importances = getattr(model, "feature_importances_", None)
    if importances is None:
        return None

    importances = list(importances)
    if len(importances) != len(FEATURE_SCHEMA):
        logger.error(
            "Model %s feature_importances_ length (%d) does not match FEATURE_SCHEMA length (%d); "
            "omitting feature-importance explanation",
            model_id,
            len(importances),
            len(FEATURE_SCHEMA),
        )
        return None

    return [
        FeatureImportanceEntry(feature_name=name, importance=float(value))
        for name, value in zip(FEATURE_SCHEMA, importances)
    ]


def build_explanation(
    outcome_result: HybridOutcomeResult,
    risk_result: RiskScoreResult,
    feature_importance: list[FeatureImportanceEntry] | None,
) -> Explanation:
    """Pure function: builds a structured `Explanation` strictly from
    values `outcome_result`/`risk_result` already computed, plus the
    already-loaded `feature_importance` (or `None`). Performs no I/O and
    no further inference of its own.
    """
    signal = outcome_result.signal

    measured_values: dict = {
        "supervised_available": signal.supervised_available,
        "predicted_label": signal.predicted_label,
        "prediction_name": signal.prediction_name,
        "attack_probability": signal.attack_probability,
        "anomaly_available": signal.anomaly_available,
        "is_anomaly": signal.is_anomaly,
        "anomaly_score": signal.anomaly_score,
        "hybrid_outcome": outcome_result.outcome.value,
        "risk_score": risk_result.risk_score,
        "severity": risk_result.severity.value,
    }

    rule_based_explanations = [_OUTCOME_DESCRIPTIONS[outcome_result.outcome]]
    if risk_result.components.agreement_bonus_applied:
        rule_based_explanations.append(
            "Risk score includes an agreement bonus because both the supervised classifier and the "
            "anomaly detector independently flagged this row."
        )
    if risk_result.components.supervised_component_source == "predicted_label":
        rule_based_explanations.append(
            "The supervised model did not provide an attack probability for this row, so its predicted "
            "label (benign/attack) was used as a binary signal instead — this is not a fabricated probability."
        )

    unavailable_information: list[str] = []
    if not signal.supervised_available:
        unavailable_information.append("No supervised detection result was available for this row.")
    if not signal.anomaly_available:
        unavailable_information.append(
            "No anomaly detection result was available for this row (no compatible anomaly detector, or "
            "anomaly detection did not succeed for this batch)."
        )
    if signal.supervised_available and signal.attack_probability is None:
        unavailable_information.append(
            "The supervised model did not provide a genuine attack probability for this row."
        )

    feature_importance_scope = None
    if feature_importance is not None:
        feature_importance_scope = "global_feature_importance"
    else:
        unavailable_information.append(
            "Feature-level explanation is unavailable: the supervised model's trained artifact could not "
            "be loaded, or it does not expose a genuine feature-importance method."
        )

    return Explanation(
        measured_values=measured_values,
        rule_based_explanations=rule_based_explanations,
        unavailable_information=unavailable_information,
        feature_importance=feature_importance,
        feature_importance_scope=feature_importance_scope,
    )


def build_and_persist_explanations(
    db: Session,
    batch_id: uuid.UUID,
    risk_results: Sequence[tuple[HybridOutcomeResult, RiskScoreResult, HybridDetectionResult, object]],
) -> list[DetectionExplanation]:
    """Orchestration: for each `(HybridOutcomeResult, RiskScoreResult,
    HybridDetectionResult, RiskAssessment)` tuple `app.services.
    risk_scoring.build_and_persist_risk_assessments` produced, loads the
    supervised model's global feature importance at most once per
    distinct model id (never once per row — a model's `feature_
    importances_` is the same for every row it scored), builds a
    structured `Explanation`, and persists it.
    """
    feature_importance_cache: dict[uuid.UUID | None, list[FeatureImportanceEntry] | None] = {}
    built: list[tuple[Explanation, HybridDetectionResult, uuid.UUID]] = []

    for outcome_result, risk_result, hybrid_row, risk_assessment in risk_results:
        model_id = outcome_result.signal.supervised_model_id
        if model_id not in feature_importance_cache:
            feature_importance_cache[model_id] = (
                load_global_feature_importance(db, model_id) if model_id is not None else None
            )
        explanation = build_explanation(outcome_result, risk_result, feature_importance_cache[model_id])
        built.append((explanation, hybrid_row, risk_assessment.id))

    return persist_explanations(db, batch_id, built)


def persist_explanations(
    db: Session,
    batch_id: uuid.UUID,
    built: Sequence[tuple[Explanation, HybridDetectionResult, uuid.UUID]],
) -> list[DetectionExplanation]:
    """Persists one `DetectionExplanation` row per `(Explanation,
    HybridDetectionResult, risk_assessment_id)` triple. Returns `[]`
    without writing anything for an empty sequence. Commits all rows in
    one transaction, mirroring every other persistence module in this
    pipeline.
    """
    if not built:
        return []

    rows = [
        DetectionExplanation(
            batch_id=batch_id,
            row_number=hybrid_row.row_number,
            hybrid_result_id=hybrid_row.id,
            risk_assessment_id=risk_assessment_id,
            explanation=explanation.as_dict(),
        )
        for explanation, hybrid_row, risk_assessment_id in built
    ]

    try:
        db.add_all(rows)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to persist detection explanations for batch %s", batch_id)
        raise ExplanationPersistenceError("Unable to persist detection explanations for this batch.") from None

    return rows


__all__ = [
    "ExplanationPersistenceError",
    "FeatureImportanceEntry",
    "Explanation",
    "load_global_feature_importance",
    "build_explanation",
    "persist_explanations",
    "build_and_persist_explanations",
]
