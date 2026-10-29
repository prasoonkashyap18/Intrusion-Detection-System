"""Hybrid detection: combines one batch/row's already-persisted
supervised `DetectionResult` (Step 27) and `AnomalyResult` (Step 32) into
a single, deterministic `HybridOutcome` plus the list of raw signal
facts that produced it.

    DetectionResult (supervised)  \\
                                    >  combine_signals()  ->  HybridOutcomeResult
    AnomalyResult (unsupervised)  /                               |
                                                       persist_hybrid_results()
                                                                    |
                                                      HybridDetectionResult rows

This module performs NO inference of its own: it never calls
`predict_with_model`/`predict_anomalies_with_model`, never reads
`MappedFeatureRecord`, and never re-derives a prediction — its only
inputs are rows the supervised/anomaly pipelines already persisted (see
`app.services.batch_processor`). `combine_signals` is a pure function:
given the same `HybridSignal`, it always returns the same outcome —
required so later risk scoring (Step 34) can itself be deterministic.

The four outcomes below are mutually exclusive and exhaustive over the
two booleans {supervised flags ATTACK, anomaly flags}, with explicit,
separate handling for either signal being unavailable rather than
silently treating "unavailable" as "did not flag":

    supervised=ATTACK, anomaly=flagged      -> CONFIRMED_ATTACK
    supervised=ATTACK, anomaly=not-flagged  -> SUPERVISED_ATTACK_ONLY
      (also used, with a recorded "anomaly:unavailable" signal, when no
      anomaly result exists for this row)
    supervised=BENIGN, anomaly=flagged      -> ANOMALY_ONLY
    supervised=BENIGN, anomaly=not-flagged  -> CLEAN
      (also used, with "anomaly:unavailable", when no anomaly result
      exists)

A row with no supervised result at all (should not occur for a batch
whose inference succeeded — see batch_processor's inference boundary,
which fails the batch rather than skipping persistence — but handled
safely rather than assumed impossible) is treated as "supervised:
unavailable" and falls back to ANOMALY_ONLY/CLEAN based on the anomaly
signal alone; `supervised_available=False` on the persisted row makes
this explicit rather than indistinguishable from a real BENIGN
prediction.
"""

from __future__ import annotations

import enum
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.anomaly_result import AnomalyResult
from app.models.detection_result import DetectionResult
from app.models.hybrid_detection_result import HybridDetectionResult

logger = logging.getLogger("ai_ids")


class HybridOutcome(str, enum.Enum):
    CONFIRMED_ATTACK = "confirmed_attack"
    SUPERVISED_ATTACK_ONLY = "supervised_attack_only"
    ANOMALY_ONLY = "anomaly_only"
    CLEAN = "clean"


class HybridDetectionPersistenceError(Exception):
    """Raised when this module cannot safely persist a set of hybrid
    results — never partially, and never silently. `message` is written
    to be safe to surface to a caller: no filesystem paths, no stack
    traces, no SQL, no raw exception text."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class HybridSignal:
    """The two independent, already-persisted signals for one
    `(batch_id, row_number)` pair — the only input `combine_signals`
    needs. `attack_probability` and `anomaly_score` are carried through
    unchanged for later consumers (risk scoring, explainability); this
    module itself only reads `predicted_label`/`is_anomaly`."""

    row_number: int

    supervised_available: bool
    detection_result_id: uuid.UUID | None = None
    supervised_model_id: uuid.UUID | None = None
    predicted_label: int | None = None
    prediction_name: str | None = None
    attack_probability: float | None = None

    anomaly_available: bool = False
    anomaly_result_id: uuid.UUID | None = None
    is_anomaly: bool | None = None
    anomaly_score: float | None = None


@dataclass(frozen=True)
class HybridOutcomeResult:
    """`combine_signals`'s pure output: the outcome plus the plain,
    structured facts that led to it — never a prose explanation (see
    `app.services.explainability`, Step 35, for that)."""

    signal: HybridSignal
    outcome: HybridOutcome
    contributing_signals: list[str] = field(default_factory=list)


def combine_signals(signal: HybridSignal) -> HybridOutcomeResult:
    """Deterministically combines one row's supervised + anomaly signals.
    Pure function: no I/O, no randomness — the same `HybridSignal` always
    yields the same `HybridOutcomeResult`.
    """
    facts: list[str] = []

    if signal.supervised_available:
        supervised_attack = signal.predicted_label == 1
        facts.append("supervised:attack" if supervised_attack else "supervised:benign")
    else:
        supervised_attack = False
        facts.append("supervised:unavailable")

    if signal.anomaly_available:
        anomaly_flagged = bool(signal.is_anomaly)
        facts.append("anomaly:flagged" if anomaly_flagged else "anomaly:not_flagged")
    else:
        anomaly_flagged = False
        facts.append("anomaly:unavailable")

    if supervised_attack and anomaly_flagged:
        outcome = HybridOutcome.CONFIRMED_ATTACK
    elif supervised_attack:
        outcome = HybridOutcome.SUPERVISED_ATTACK_ONLY
    elif anomaly_flagged:
        outcome = HybridOutcome.ANOMALY_ONLY
    else:
        outcome = HybridOutcome.CLEAN

    return HybridOutcomeResult(signal=signal, outcome=outcome, contributing_signals=facts)


def load_hybrid_signals_for_batch(db: Session, batch_id: uuid.UUID) -> list[HybridSignal]:
    """Loads *only* `batch_id`'s own persisted `DetectionResult` and
    `AnomalyResult` rows and matches them by `row_number` — the entire
    batch-isolation guarantee is the `batch_id` filter below; another
    batch's rows are never a match. Returns one `HybridSignal` per
    `row_number` present in either set, in ascending `row_number` order,
    deterministically (never dict/insertion order).
    """
    detection_rows = db.scalars(select(DetectionResult).where(DetectionResult.batch_id == batch_id)).all()
    anomaly_rows = db.scalars(select(AnomalyResult).where(AnomalyResult.batch_id == batch_id)).all()

    detection_by_row = {row.row_number: row for row in detection_rows}
    anomaly_by_row = {row.row_number: row for row in anomaly_rows}

    row_numbers = sorted(set(detection_by_row) | set(anomaly_by_row))

    signals = []
    for row_number in row_numbers:
        detection = detection_by_row.get(row_number)
        anomaly = anomaly_by_row.get(row_number)
        signals.append(
            HybridSignal(
                row_number=row_number,
                supervised_available=detection is not None,
                detection_result_id=detection.id if detection is not None else None,
                supervised_model_id=detection.model_id if detection is not None else None,
                predicted_label=detection.predicted_label if detection is not None else None,
                prediction_name=detection.prediction_name if detection is not None else None,
                attack_probability=detection.attack_probability if detection is not None else None,
                anomaly_available=anomaly is not None,
                anomaly_result_id=anomaly.id if anomaly is not None else None,
                is_anomaly=anomaly.is_anomaly if anomaly is not None else None,
                anomaly_score=anomaly.anomaly_score if anomaly is not None else None,
            )
        )
    return signals


def persist_hybrid_results(
    db: Session,
    batch_id: uuid.UUID,
    outcome_results: Sequence[HybridOutcomeResult],
) -> list[HybridDetectionResult]:
    """Persists one `HybridDetectionResult` row per `outcome_results`
    entry. Returns `[]` without writing anything for an empty sequence.
    Commits all rows in one transaction, mirroring `persist_detection_
    results`/`persist_anomaly_results`: either every row is written, or
    (on a database error, including a duplicate `(batch_id, row_number)`
    pair) none are.
    """
    if not outcome_results:
        return []

    rows = [
        HybridDetectionResult(
            batch_id=batch_id,
            row_number=result.signal.row_number,
            detection_result_id=result.signal.detection_result_id,
            anomaly_result_id=result.signal.anomaly_result_id,
            supervised_available=result.signal.supervised_available,
            anomaly_available=result.signal.anomaly_available,
            outcome=result.outcome.value,
            contributing_signals=list(result.contributing_signals),
        )
        for result in outcome_results
    ]

    try:
        db.add_all(rows)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Failed to persist hybrid detection results for batch %s", batch_id)
        raise HybridDetectionPersistenceError("Unable to persist hybrid detection results for this batch.") from None

    return rows


def build_and_persist_hybrid_results(
    db: Session, batch_id: uuid.UUID
) -> list[tuple[HybridOutcomeResult, HybridDetectionResult]]:
    """Orchestration: loads this batch's signals, combines each
    deterministically, persists the results, and returns each in-memory
    `HybridOutcomeResult` paired with the `HybridDetectionResult` row it
    was persisted as (so callers — risk scoring — never have to re-derive
    the same signals from the freshly persisted rows, and have the row's
    `id` available for their own foreign key).
    """
    signals = load_hybrid_signals_for_batch(db, batch_id)
    if not signals:
        return []

    outcome_results = [combine_signals(signal) for signal in signals]
    persisted_rows = persist_hybrid_results(db, batch_id, outcome_results)
    return list(zip(outcome_results, persisted_rows))


__all__ = [
    "HybridOutcome",
    "HybridDetectionPersistenceError",
    "HybridSignal",
    "HybridOutcomeResult",
    "combine_signals",
    "load_hybrid_signals_for_batch",
    "persist_hybrid_results",
    "build_and_persist_hybrid_results",
]
