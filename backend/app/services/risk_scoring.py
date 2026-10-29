"""Risk scoring: a deterministic, transparent `[0, 100]` risk score +
`Severity` for one batch/row's already-computed `HybridOutcomeResult`
(`app.services.hybrid_detection`), using only the supervised prediction,
its attack probability when available, the anomaly flag/score when
available, and the hybrid outcome itself.

    HybridOutcomeResult (app.services.hybrid_detection)
        |
    compute_risk_score()  <- pure, deterministic, configurable
        |
    RiskScoreResult
        |
    persist_risk_assessments()
        |
    RiskAssessment rows

This module performs NO inference: it never calls a model, never reads
a feature vector, and never reaches past the already-persisted
`attack_probability`/`anomaly_score` values. `compute_risk_score` is a
pure function of its two arguments (`HybridOutcomeResult`,
`RiskScoringConfig`) — identical inputs and configuration always produce
an identical score, which is the entire point of this module existing as
a transparent formula rather than a second trained model.

Scoring formula (see `compute_risk_score`): each available signal
contributes `weight * normalized_value` to a running total, and the
total is divided by the sum of the weights that were actually available
— *not* by the sum of all configured weights. This is what "handle
missing signals explicitly" means here: a missing anomaly result (no
compatible detector was ever registered, or anomaly detection failed —
both ordinary, Step-31/32-documented outcomes, not errors) is excluded
from both the numerator and the denominator, so the score reflects only
the signals that genuinely exist, never a signal silently treated as 0
(which would wrongly pull every unscored row toward "no risk").

Normalizing the anomaly score: `IsolationForest.decision_function()` is
an unbounded real number with no fixed scale (lower = more anomalous,
see `app.services.anomaly_inference`) — never a probability. This module
maps it into `[0, 1]` with a fixed logistic (sigmoid) curve centered at
0, `normalize_anomaly_score`: a very negative score (strongly anomalous)
approaches `1.0`, a very positive score (strongly normal) approaches
`0.0`, and a score of exactly `0.0` maps to `0.5`. `anomaly_score_scale`
(part of `RiskScoringConfig`) controls how quickly the curve saturates;
it is the one place real-world recalibration against measured
score distributions would happen — not implemented here, since no
real-world data exists yet to recalibrate against (see backend/README.md).

Supervised signal without a probability: when `attack_probability` is
`None` (the trained model did not genuinely support `predict_proba` —
see `app.services.model_inference`), this module falls back to the
*real, measured* `predicted_label` as a binary `{0.0, 1.0}} value — never
a fabricated probability number, and `supervised_component_source`
records which case applied (`"attack_probability"` vs
`"predicted_label"` vs `"unavailable"`) so this distinction is never
lost downstream.
"""

from __future__ import annotations

import logging
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.enums import Severity
from app.models.hybrid_detection_result import HybridDetectionResult
from app.models.risk_assessment import RiskAssessment
from app.services.hybrid_detection import HybridOutcome, HybridOutcomeResult

logger = logging.getLogger("ai_ids")


@dataclass(frozen=True)
class RiskScoringConfig:
    """One fixed, documented, configurable weighting scheme. Not learned,
    not fit to data — a transparent formula a human can audit and change
    by editing these numbers, exactly like `app.services.model_training.
    RANDOM_FOREST_PARAMS`'s own "one fixed, documented configuration"
    convention. Weights need not sum to `1.0`: `compute_risk_score`
    divides by the sum of the weights actually used for a given row, not
    by a fixed total, so any positive weighting scheme produces scores in
    `[0, 100]`.
    """

    supervised_weight: float = 0.6
    anomaly_weight: float = 0.3
    agreement_bonus_weight: float = 0.1
    """Extra weight applied only for the `CONFIRMED_ATTACK` hybrid
    outcome (both signals agree) — rewards corroboration between two
    independent methods, never applied on a single signal alone."""

    anomaly_score_scale: float = 1.0
    """Controls how quickly `normalize_anomaly_score`'s sigmoid saturates
    — a larger value spreads a given range of anomaly scores across more
    of `[0, 1]`; a smaller value saturates faster. Must be > 0."""

    low_threshold: float = 25.0
    medium_threshold: float = 50.0
    high_threshold: float = 75.0
    """Severity cut points over the `[0, 100]` risk score: `< low` ->
    LOW, `< medium` -> MEDIUM, `< high` -> HIGH, otherwise CRITICAL. Must
    be strictly increasing."""


DEFAULT_RISK_SCORING_CONFIG = RiskScoringConfig()


class RiskScoringError(Exception):
    """Raised when a risk score genuinely cannot be computed — never
    worked around by fabricating a value. `message` is written to be
    safe to surface to a caller: no filesystem paths, no stack traces, no
    SQL, no raw exception text."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RiskAssessmentPersistenceError(Exception):
    """Raised when this module cannot safely persist a set of risk
    assessments — never partially, and never silently."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class RiskComponents:
    """The real, individually inspectable inputs `compute_risk_score`
    combined — kept so a risk score is never a black box: every
    component here is either a real measured value, a real
    normalization of one, or an explicit `None` for "unavailable"."""

    supervised_component: float | None
    """`attack_probability`, or the `predicted_label`-derived `{0.0,
    1.0}` fallback, or `None` if supervised was entirely unavailable."""

    supervised_component_source: str
    """`"attack_probability"`, `"predicted_label"`, or `"unavailable"`."""

    anomaly_component: float | None
    """`normalize_anomaly_score(anomaly_score)`, or `None` if no anomaly
    result was available for this row."""

    agreement_bonus_applied: bool


@dataclass(frozen=True)
class RiskScoreResult:
    row_number: int
    risk_score: float
    """Bounded `[0, 100]` by construction — see `compute_risk_score`."""

    severity: Severity
    components: RiskComponents
    hybrid_outcome: HybridOutcome


def normalize_anomaly_score(anomaly_score: float, config: RiskScoringConfig = DEFAULT_RISK_SCORING_CONFIG) -> float:
    """Maps an unbounded `IsolationForest.decision_function()` output
    into `[0, 1]` with a fixed logistic curve: `1 / (1 + exp(score /
    scale))`. Monotonically decreasing in `score` — a lower (more
    anomalous) score always yields a higher normalized value, matching
    `anomaly_score`'s own documented direction (see
    `app.services.anomaly_inference`). `scale <= 0` would make the curve
    non-monotonic or undefined, so it is rejected explicitly rather than
    silently misbehaving.
    """
    if config.anomaly_score_scale <= 0:
        raise RiskScoringError("anomaly_score_scale must be a positive number.")
    return 1.0 / (1.0 + math.exp(anomaly_score / config.anomaly_score_scale))


def _severity_for(risk_score: float, config: RiskScoringConfig) -> Severity:
    if not (config.low_threshold < config.medium_threshold < config.high_threshold):
        raise RiskScoringError("Severity thresholds must be strictly increasing (low < medium < high).")
    if risk_score < config.low_threshold:
        return Severity.LOW
    if risk_score < config.medium_threshold:
        return Severity.MEDIUM
    if risk_score < config.high_threshold:
        return Severity.HIGH
    return Severity.CRITICAL


def compute_risk_score(
    outcome_result: HybridOutcomeResult,
    config: RiskScoringConfig = DEFAULT_RISK_SCORING_CONFIG,
) -> RiskScoreResult:
    """Deterministically computes a `[0, 100]` risk score and `Severity`
    for one row's hybrid outcome. Pure function: no I/O, no randomness —
    identical `outcome_result`/`config` always yield an identical result.

    Raises `RiskScoringError` if neither the supervised nor the anomaly
    signal is available for this row (nothing to score — this is a
    genuine precondition failure, never silently scored as 0), or if
    `config` itself is invalid (see `normalize_anomaly_score`/
    `_severity_for`).
    """
    signal = outcome_result.signal

    weighted_total = 0.0
    weight_used = 0.0

    if signal.supervised_available:
        if signal.attack_probability is not None:
            supervised_value = signal.attack_probability
            supervised_source = "attack_probability"
        else:
            supervised_value = 1.0 if signal.predicted_label == 1 else 0.0
            supervised_source = "predicted_label"
        weighted_total += config.supervised_weight * supervised_value
        weight_used += config.supervised_weight
    else:
        supervised_value = None
        supervised_source = "unavailable"

    if signal.anomaly_available:
        anomaly_value = normalize_anomaly_score(signal.anomaly_score, config)
        weighted_total += config.anomaly_weight * anomaly_value
        weight_used += config.anomaly_weight
    else:
        anomaly_value = None

    agreement_bonus_applied = outcome_result.outcome == HybridOutcome.CONFIRMED_ATTACK
    if agreement_bonus_applied:
        weighted_total += config.agreement_bonus_weight * 1.0
        weight_used += config.agreement_bonus_weight

    if weight_used <= 0.0:
        raise RiskScoringError("No supervised or anomaly signal is available to compute a risk score for this row.")

    normalized = weighted_total / weight_used
    risk_score = max(0.0, min(100.0, normalized * 100.0))
    severity = _severity_for(risk_score, config)

    return RiskScoreResult(
        row_number=signal.row_number,
        risk_score=risk_score,
        severity=severity,
        components=RiskComponents(
            supervised_component=supervised_value,
            supervised_component_source=supervised_source,
            anomaly_component=anomaly_value,
            agreement_bonus_applied=agreement_bonus_applied,
        ),
        hybrid_outcome=outcome_result.outcome,
    )


def persist_risk_assessments(
    db: Session,
    batch_id: uuid.UUID,
    scored: Sequence[tuple[RiskScoreResult, HybridDetectionResult]],
) -> list[RiskAssessment]:
    """Persists one `RiskAssessment` row per `(RiskScoreResult,
    HybridDetectionResult)` pair, linking each to the hybrid row it was
    computed from. Returns `[]` without writing anything for an empty
    sequence. Commits all rows in one transaction, mirroring every other
    persistence module in this pipeline.
    """
    if not scored:
        return []

    rows = [
        RiskAssessment(
            batch_id=batch_id,
            row_number=result.row_number,
            hybrid_result_id=hybrid_row.id,
            risk_score=result.risk_score,
            severity=result.severity,
            supervised_component=result.components.supervised_component,
            supervised_component_source=result.components.supervised_component_source,
            anomaly_component=result.components.anomaly_component,
            agreement_bonus_applied=result.components.agreement_bonus_applied,
        )
        for result, hybrid_row in scored
    ]

    try:
        db.add_all(rows)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to persist risk assessments for batch %s", batch_id)
        raise RiskAssessmentPersistenceError("Unable to persist risk assessments for this batch.") from None

    return rows


def build_and_persist_risk_assessments(
    db: Session,
    batch_id: uuid.UUID,
    hybrid_results: Sequence[tuple[HybridOutcomeResult, HybridDetectionResult]],
    config: RiskScoringConfig = DEFAULT_RISK_SCORING_CONFIG,
) -> list[tuple[HybridOutcomeResult, RiskScoreResult, HybridDetectionResult, RiskAssessment]]:
    """Orchestration: scores every hybrid result this batch produced and
    persists the results, skipping (logging, not raising) any individual
    row for which `compute_risk_score` itself raises `RiskScoringError`
    (e.g. a row with neither signal available — should not occur given
    `app.services.hybrid_detection`'s own construction, but handled
    safely rather than assumed impossible) rather than failing the whole
    batch over one row. Returns each outcome paired with its score, the
    `HybridDetectionResult` it was computed from, and the persisted
    `RiskAssessment` row (with its real `id`) — enough for
    `app.services.explainability` to build and link an explanation
    without re-querying anything.
    """
    scored: list[tuple[HybridOutcomeResult, RiskScoreResult, HybridDetectionResult]] = []
    for outcome_result, hybrid_row in hybrid_results:
        try:
            scored.append((outcome_result, compute_risk_score(outcome_result, config), hybrid_row))
        except RiskScoringError:
            logger.exception(
                "Batch %s: unable to compute a risk score for row %s; skipping this row's risk assessment",
                batch_id,
                outcome_result.signal.row_number,
            )

    persisted_rows = persist_risk_assessments(
        db, batch_id, [(risk_result, hybrid_row) for _, risk_result, hybrid_row in scored]
    )
    return [
        (outcome_result, risk_result, hybrid_row, persisted_row)
        for (outcome_result, risk_result, hybrid_row), persisted_row in zip(scored, persisted_rows)
    ]


__all__ = [
    "RiskScoringConfig",
    "DEFAULT_RISK_SCORING_CONFIG",
    "RiskScoringError",
    "RiskAssessmentPersistenceError",
    "RiskComponents",
    "RiskScoreResult",
    "normalize_anomaly_score",
    "compute_risk_score",
    "persist_risk_assessments",
    "build_and_persist_risk_assessments",
]
