"""Real-time detection (Step 43): runs one finalized live flow through
the *exact same* supervised/anomaly/hybrid/risk/explanation/alert
pipeline Step 26-39 already built for batches — reusing every pure
algorithm and every list-based persistence function unchanged, just
orchestrated per single row instead of per whole batch.

    FinalizedFlow (app.services.flow_construction, from
        app.services.live_capture's worker thread)
        |
    process_finalized_flow()
        |
    MappedFeatureRecord -> DetectionResult? -> AnomalyResult? ->
    HybridDetectionResult -> RiskAssessment -> DetectionExplanation ->
    Alert?  (each optional stage skipped, never fabricated, when its
             own precondition isn't met — see below)
        |
    (then, batch-wide but already-idempotent: event correlation,
     attack-chain analysis — app.services.event_correlation /
     attack_chain_analysis, unchanged)

Why this is not a second copy of the detection algorithms: every
number this module produces comes from calling the same functions the
batch path calls — `predict_with_model`, `predict_anomalies_with_model`,
`combine_signals`, `compute_risk_score`, `build_explanation`,
`should_generate_alert`, `build_alert_draft` — and every write goes
through the same list-based `persist_*` functions those modules already
expose (`persist_detection_results([row_number])`,
`persist_hybrid_results([outcome_result])`, etc., called with a single-
element list instead of a whole batch's worth). The only thing this
module adds is the per-row orchestration `app.services.batch_processor`
otherwise does per-batch — never a reimplementation of what any of
those functions compute.

Why `app.services.batch_processor` itself is untouched: that module's
functions all assume "the whole batch's features are already
persisted" (e.g. `_load_feature_matrix_for_batch` loads every row for a
`batch_id`), which is correct for a finite CSV/PCAP upload but wrong for
a live session where rows arrive one at a time over an unbounded
session lifetime — reusing them as-is here would mean re-running
inference, hybrid detection, etc. on every already-processed row each
time a new flow arrives (duplicate-key errors on every retry). This
module's single-row persistence calls avoid that by construction.

Feature-schema compatibility is validated by the exact same mechanism
the batch path relies on: `predict_with_model`/`predict_anomalies_with_model`
compare the supplied `dataset_schema`/`feature_schema_version` against
the trained artifact's own recorded values and raise
`InferenceError`/`AnomalyInferenceError` on any mismatch — this module
never second-guesses or duplicates that check, it only catches the
exception and treats "no compatible model" as a normal, logged,
non-fatal outcome (the row simply has no `DetectionResult`), never as
a benign result and never as a crash.

Idempotency: `process_finalized_flow` checks for an already-persisted
`MappedFeatureRecord` for `(batch_id, row_number)` *before* doing
anything else and returns a no-op `RealtimeFlowResult` if one exists —
a retried call for a flow this function already fully or partially
processed never re-runs inference, never re-scores, and never risks a
`UniqueConstraint` violation from any downstream table.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.anomaly_result import AnomalyResult
from app.models.detection_result import DetectionResult
from app.models.enums import ModelStatus
from app.models.mapped_feature_record import MappedFeatureRecord
from app.models.model_metadata import ModelMetadata
from app.services.alert_generation import (
    build_alert_draft,
    persist_alerts,
    should_generate_alert,
)
from app.services.alert_notifications import notify_new_alerts
from app.services.anomaly_inference import AnomalyInferenceError, predict_anomalies_with_model
from app.services.anomaly_result_persistence import AnomalyResultPersistenceError, persist_anomaly_results
from app.services.attack_chain_analysis import build_and_persist_chains_for_batch
from app.services.detection_result_persistence import DetectionResultPersistenceError, persist_detection_results
from app.services.event_correlation import build_and_persist_correlations_for_batch
from app.services.explainability import build_explanation, load_global_feature_importance, persist_explanations
from app.services.feature_persistence import load_feature_vector, persist_mapped_features
from app.services.flow_construction import FinalizedFlow
from app.services.hybrid_detection import HybridSignal, combine_signals, persist_hybrid_results
from app.services.model_inference import InferenceError, predict_with_model
from app.services.pcap_feature_mapping import PCAP_DATASET_SCHEMA, flow_to_mapped_feature_set
from app.services.risk_scoring import compute_risk_score, persist_risk_assessments

logger = logging.getLogger("ai_ids")


@dataclass(frozen=True)
class RealtimeFlowResult:
    """A truthful account of what actually ran for one flow — never a
    claim that a stage succeeded when it was skipped or failed."""

    batch_id: uuid.UUID
    row_number: int
    already_processed: bool = False
    supervised_ran: bool = False
    anomaly_ran: bool = False
    hybrid_ran: bool = False
    alert_generated: bool = False
    failure: str | None = None
    """Set when an unexpected (not "no compatible model") error
    occurred in the hybrid/risk/explanation/alert stage — the flow's
    features and whatever inference did succeed are still persisted;
    only the derived layer is incomplete. Never set for the ordinary
    "no compatible model" case, which is not a failure."""


def _select_ready_model(db: Session, model_type: str, dataset_schema: str, feature_schema_version: str) -> ModelMetadata | None:
    return db.scalars(
        select(ModelMetadata)
        .where(
            ModelMetadata.status == ModelStatus.READY,
            ModelMetadata.model_type == model_type,
            ModelMetadata.dataset_name == dataset_schema,
            ModelMetadata.feature_set_version == feature_schema_version,
        )
        .order_by(ModelMetadata.created_at.desc(), ModelMetadata.id.desc())
    ).first()


def _load_single_hybrid_signal(db: Session, batch_id: uuid.UUID, row_number: int) -> HybridSignal:
    detection = db.scalars(
        select(DetectionResult).where(DetectionResult.batch_id == batch_id, DetectionResult.row_number == row_number)
    ).first()
    anomaly = db.scalars(
        select(AnomalyResult).where(AnomalyResult.batch_id == batch_id, AnomalyResult.row_number == row_number)
    ).first()
    return HybridSignal(
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


def process_finalized_flow(
    batch_id: uuid.UUID,
    flow: FinalizedFlow,
    row_number: int,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    dataset_schema: str = PCAP_DATASET_SCHEMA,
    feature_schema_version: str | None = None,
) -> RealtimeFlowResult:
    """Runs one finalized flow through feature persistence, supervised
    inference, anomaly detection, hybrid detection, risk scoring,
    explainability, and alert generation — each stage best-effort and
    independently skippable, exactly mirroring `app.services.
    batch_processor`'s own conventions, just for one row. Opens and
    closes its own database session (this is the function `app.
    services.live_capture`'s worker thread calls as its `FlowProcessor`
    — see that module's own threading discipline).
    """
    from app.services.feature_extraction import FEATURE_SCHEMA_VERSION

    feature_schema_version = feature_schema_version or FEATURE_SCHEMA_VERSION
    db = session_factory()
    try:
        existing = db.scalars(
            select(MappedFeatureRecord).where(
                MappedFeatureRecord.batch_id == batch_id, MappedFeatureRecord.row_number == row_number
            )
        ).first()
        if existing is not None:
            return RealtimeFlowResult(batch_id=batch_id, row_number=row_number, already_processed=True)

        mapped = flow_to_mapped_feature_set(flow, row_number)
        record = persist_mapped_features(db, batch_id, mapped)
        db.commit()
        feature_vector = load_feature_vector(record)

        result = RealtimeFlowResult(batch_id=batch_id, row_number=row_number)
        result = _run_supervised_inference(db, batch_id, row_number, feature_vector, dataset_schema, feature_schema_version, result)
        result = _run_anomaly_inference(db, batch_id, row_number, feature_vector, dataset_schema, feature_schema_version, result)
        result = _run_derived_analysis(db, batch_id, row_number, result)
        return result
    finally:
        db.close()


def _run_supervised_inference(
    db: Session, batch_id: uuid.UUID, row_number: int, feature_vector: list,
    dataset_schema: str, feature_schema_version: str, result: RealtimeFlowResult,
) -> RealtimeFlowResult:
    model = _select_ready_model(db, "random_forest", dataset_schema, feature_schema_version)
    if model is None:
        return result
    try:
        inference_result = predict_with_model(
            db, model.id, [feature_vector], feature_schema_version=feature_schema_version, dataset_schema=dataset_schema
        )
        persist_detection_results(db, batch_id, inference_result, [row_number])
        db.commit()
    except (InferenceError, DetectionResultPersistenceError):
        db.rollback()
        logger.exception("Batch %s row %s: supervised inference failed; continuing without it", batch_id, row_number)
        return result
    return RealtimeFlowResult(**{**result.__dict__, "supervised_ran": True})


def _run_anomaly_inference(
    db: Session, batch_id: uuid.UUID, row_number: int, feature_vector: list,
    dataset_schema: str, feature_schema_version: str, result: RealtimeFlowResult,
) -> RealtimeFlowResult:
    model = _select_ready_model(db, "isolation_forest", dataset_schema, feature_schema_version)
    if model is None:
        return result
    try:
        anomaly_result = predict_anomalies_with_model(
            db, model.id, [feature_vector], feature_schema_version=feature_schema_version, dataset_schema=dataset_schema
        )
        persist_anomaly_results(db, batch_id, anomaly_result, [row_number])
        db.commit()
    except (AnomalyInferenceError, AnomalyResultPersistenceError):
        db.rollback()
        logger.exception("Batch %s row %s: anomaly detection failed; continuing without it", batch_id, row_number)
        return result
    return RealtimeFlowResult(**{**result.__dict__, "anomaly_ran": True})


def _run_derived_analysis(db: Session, batch_id: uuid.UUID, row_number: int, result: RealtimeFlowResult) -> RealtimeFlowResult:
    """Hybrid detection, risk scoring, explainability, alert
    generation, event correlation, and attack-chain analysis for this
    one row — one combined best-effort stage, mirroring
    `app.services.batch_processor._run_hybrid_risk_and_explanations`'s
    own single try/except boundary. A failure here never erases the
    feature row or whatever inference already succeeded and committed.
    """
    try:
        signal = _load_single_hybrid_signal(db, batch_id, row_number)
        outcome_result = combine_signals(signal)
        hybrid_rows = persist_hybrid_results(db, batch_id, [outcome_result])
        db.commit()
        hybrid_row = hybrid_rows[0]

        risk_result = compute_risk_score(outcome_result)
        risk_rows = persist_risk_assessments(db, batch_id, [(risk_result, hybrid_row)])
        db.commit()
        risk_row = risk_rows[0]

        feature_importance = (
            load_global_feature_importance(db, signal.supervised_model_id)
            if signal.supervised_model_id is not None
            else None
        )
        explanation = build_explanation(outcome_result, risk_result, feature_importance)
        explanation_rows = persist_explanations(db, batch_id, [(explanation, hybrid_row, risk_row.id)])
        db.commit()
        explanation_row = explanation_rows[0] if explanation_rows else None

        alert_generated = False
        if should_generate_alert(outcome_result.outcome, risk_result.severity):
            draft = build_alert_draft(
                outcome_result, risk_result, hybrid_row, risk_row, explanation,
                explanation_row.id if explanation_row is not None else None,
            )
            alerts = persist_alerts(db, [draft])
            db.commit()
            alert_generated = bool(alerts)
            if alerts:
                notify_new_alerts(alerts)  # only after the commit above has already succeeded
                try:
                    build_and_persist_correlations_for_batch(db, batch_id)
                    build_and_persist_chains_for_batch(db, batch_id)
                except Exception:
                    logger.exception(
                        "Batch %s row %s: correlation/chain analysis failed; continuing", batch_id, row_number
                    )

        return RealtimeFlowResult(
            **{**result.__dict__, "hybrid_ran": True, "alert_generated": alert_generated}
        )
    except Exception:
        db.rollback()
        logger.exception("Batch %s row %s: hybrid/risk/explanation/alert stage failed", batch_id, row_number)
        return RealtimeFlowResult(**{**result.__dict__, "failure": "derived_analysis_failed"})


__all__ = ["RealtimeFlowResult", "process_finalized_flow"]
