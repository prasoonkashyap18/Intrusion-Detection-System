"""Alert generation: a deterministic, rule-based decision about whether
one row's already-computed `HybridOutcomeResult` + `RiskScoreResult` +
`Explanation` (Steps 33-35) warrants surfacing as an `Alert`, and the
persistence of that decision.

    HybridOutcomeResult + RiskScoreResult + Explanation
        |
    should_generate_alert()  <- pure, deterministic, configurable
        |
    build_alert_draft()
        |
    persist_alerts()
        |
    Alert rows

This module performs NO inference, no risk scoring, and no
explanation-building of its own: its only inputs are objects
`app.services.hybrid_detection`/`risk_scoring`/`explainability` already
produced (see `app.services.batch_processor._run_alert_generation`).
`should_generate_alert` is a pure function — identical inputs and
`AlertGenerationConfig` always yield the same decision.

Rule (see `AlertGenerationConfig`): a row is alert-eligible when its
hybrid outcome is not `CLEAN` *and* its severity is at or above a
configured minimum, **except** `CONFIRMED_ATTACK` always qualifies
regardless of severity (two independent detectors agreeing is itself
the reason to alert, even if the computed risk score happens to be
low). This is the explicit mechanism that satisfies "avoid generating
alerts for every benign record without a legitimate rule-based reason"
— a `CLEAN` row, or a flagged-but-low-severity row, produces no alert
at all, not a suppressed or hidden one.

Evidence: `evidence` on a persisted `Alert` is built entirely from the
already-computed `Explanation.as_dict()` plus the hybrid outcome's
`contributing_signals` — never a new fact, never a fabricated IP,
attack type, indicator, or timestamp. `title`/`description` are built
from a small, fixed set of templates filled in with those same real
values; they are never free-text model output.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.enums import Severity
from app.models.hybrid_detection_result import HybridDetectionResult
from app.models.risk_assessment import RiskAssessment
from app.services.explainability import _OUTCOME_DESCRIPTIONS, Explanation
from app.services.hybrid_detection import HybridOutcome, HybridOutcomeResult
from app.services.risk_scoring import RiskScoreResult

logger = logging.getLogger("ai_ids")

_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}

_OUTCOME_TITLES: dict[HybridOutcome, str] = {
    HybridOutcome.CONFIRMED_ATTACK: "Confirmed attack signal",
    HybridOutcome.SUPERVISED_ATTACK_ONLY: "Supervised classifier flagged attack",
    HybridOutcome.ANOMALY_ONLY: "Anomalous traffic flagged",
    HybridOutcome.CLEAN: "Clean traffic",
}


class AlertPersistenceError(Exception):
    """Raised when this module cannot safely persist a set of alerts —
    never partially, and never silently."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class AlertGenerationConfig:
    """One fixed, documented, overridable rule set — not learned, not
    fit to data, mirroring every other configurable-formula module in
    this pipeline (`app.services.risk_scoring.RiskScoringConfig`)."""

    excluded_outcomes: frozenset[HybridOutcome] = frozenset({HybridOutcome.CLEAN})
    """Outcomes that never generate an alert by themselves."""

    min_severity: Severity = Severity.MEDIUM
    """The minimum severity (see `_SEVERITY_ORDER`) required to alert on
    a non-excluded outcome, unless `always_alert_outcomes` applies."""

    always_alert_outcomes: frozenset[HybridOutcome] = frozenset({HybridOutcome.CONFIRMED_ATTACK})
    """Outcomes that always alert regardless of `min_severity` — two
    independent detectors agreeing is itself the reason to alert."""


DEFAULT_ALERT_GENERATION_CONFIG = AlertGenerationConfig()


@dataclass(frozen=True)
class AlertDraft:
    """Everything needed to persist one `Alert` row — built strictly
    from already-computed values, never from a fresh computation."""

    batch_id: uuid.UUID
    row_number: int
    detection_result_id: uuid.UUID | None
    anomaly_result_id: uuid.UUID | None
    hybrid_result_id: uuid.UUID
    risk_assessment_id: uuid.UUID
    explanation_id: uuid.UUID | None
    title: str
    description: str
    severity: Severity
    evidence: dict


def should_generate_alert(
    outcome: HybridOutcome,
    severity: Severity,
    config: AlertGenerationConfig = DEFAULT_ALERT_GENERATION_CONFIG,
) -> bool:
    """Pure function: the entire alert-eligibility rule. Identical
    `(outcome, severity, config)` always yields the same decision."""
    if outcome in config.always_alert_outcomes:
        return True
    if outcome in config.excluded_outcomes:
        return False
    return _SEVERITY_ORDER[severity] >= _SEVERITY_ORDER[config.min_severity]


def build_alert_draft(
    outcome_result: HybridOutcomeResult,
    risk_result: RiskScoreResult,
    hybrid_row: HybridDetectionResult,
    risk_row: RiskAssessment,
    explanation: Explanation,
    explanation_id: uuid.UUID | None,
) -> AlertDraft:
    """Pure function: builds one `AlertDraft` strictly from values
    already computed by Steps 33-35. Performs no I/O."""
    signal = outcome_result.signal
    title = f"{_OUTCOME_TITLES[outcome_result.outcome]} (row {signal.row_number}, {risk_result.severity.value} risk)"

    description_parts = [_OUTCOME_DESCRIPTIONS[outcome_result.outcome]]
    description_parts.append(f"Risk score: {risk_result.risk_score:.1f}/100 ({risk_result.severity.value}).")
    if signal.supervised_available:
        description_parts.append(
            f"Supervised classifier prediction: {signal.prediction_name}"
            + (f" (attack probability {signal.attack_probability:.2f})." if signal.attack_probability is not None else ".")
        )
    if signal.anomaly_available:
        description_parts.append(
            f"Anomaly detector: {'flagged' if signal.is_anomaly else 'did not flag'} this row "
            f"(anomaly score {signal.anomaly_score:.4f})."
        )

    evidence = {
        "hybrid_outcome": outcome_result.outcome.value,
        "contributing_signals": list(outcome_result.contributing_signals),
        "risk_score": risk_result.risk_score,
        "severity": risk_result.severity.value,
        "explanation": explanation.as_dict(),
    }

    return AlertDraft(
        batch_id=hybrid_row.batch_id,
        row_number=signal.row_number,
        detection_result_id=signal.detection_result_id,
        anomaly_result_id=signal.anomaly_result_id,
        hybrid_result_id=hybrid_row.id,
        risk_assessment_id=risk_row.id,
        explanation_id=explanation_id,
        title=title,
        description=" ".join(description_parts),
        severity=risk_result.severity,
        evidence=evidence,
    )


def persist_alerts(db: Session, drafts: Sequence[AlertDraft]) -> list[Alert]:
    """Persists one `Alert` row per draft. Returns `[]` without writing
    anything for an empty sequence. Commits all rows in one transaction,
    mirroring every other persistence module in this pipeline — either
    every row is written, or (on a database error, including a
    duplicate `(batch_id, row_number)` pair) none are.
    """
    if not drafts:
        return []

    rows = [
        Alert(
            batch_id=draft.batch_id,
            row_number=draft.row_number,
            detection_result_id=draft.detection_result_id,
            anomaly_result_id=draft.anomaly_result_id,
            hybrid_result_id=draft.hybrid_result_id,
            risk_assessment_id=draft.risk_assessment_id,
            explanation_id=draft.explanation_id,
            title=draft.title,
            description=draft.description,
            severity=draft.severity,
            evidence=draft.evidence,
        )
        for draft in drafts
    ]

    try:
        db.add_all(rows)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to persist alerts")
        raise AlertPersistenceError("Unable to persist alerts for this batch.") from None

    return rows


def build_and_persist_alerts(
    db: Session,
    risk_results: Sequence[tuple[HybridOutcomeResult, RiskScoreResult, HybridDetectionResult, RiskAssessment]],
    explanation_results: Sequence[tuple[Explanation, object]],
    config: AlertGenerationConfig = DEFAULT_ALERT_GENERATION_CONFIG,
) -> list[Alert]:
    """Orchestration: for each row (in the same order
    `app.services.risk_scoring.build_and_persist_risk_assessments` and
    `app.services.explainability.build_and_persist_explanations`
    produced them — both iterate the same `hybrid_results` sequence in
    order, so a positional zip is correct and avoids a re-query),
    decides eligibility via `should_generate_alert` and persists a
    draft for every eligible row. Rows that are not eligible simply
    produce no draft — never a suppressed or hidden one.
    """
    drafts = []
    for (outcome_result, risk_result, hybrid_row, risk_row), (explanation, explanation_row) in zip(
        risk_results, explanation_results
    ):
        if not should_generate_alert(outcome_result.outcome, risk_result.severity, config):
            continue
        drafts.append(
            build_alert_draft(outcome_result, risk_result, hybrid_row, risk_row, explanation, explanation_row.id)
        )

    return persist_alerts(db, drafts)


__all__ = [
    "AlertPersistenceError",
    "AlertGenerationConfig",
    "DEFAULT_ALERT_GENERATION_CONFIG",
    "AlertDraft",
    "should_generate_alert",
    "build_alert_draft",
    "persist_alerts",
    "build_and_persist_alerts",
]
