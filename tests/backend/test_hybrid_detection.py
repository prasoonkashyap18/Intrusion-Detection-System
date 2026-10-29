"""Tests for app.services.hybrid_detection: combining already-persisted
DetectionResult/AnomalyResult rows into a deterministic HybridOutcome,
and persisting the result.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models.anomaly_result import AnomalyResult
from app.models.detection_batch import DetectionBatch
from app.models.detection_result import DetectionResult
from app.models.enums import ProcessingStatus
from app.models.hybrid_detection_result import HybridDetectionResult
from app.models.model_metadata import ModelMetadata
from app.services.hybrid_detection import (
    HybridDetectionPersistenceError,
    HybridOutcome,
    HybridSignal,
    build_and_persist_hybrid_results,
    combine_signals,
    load_hybrid_signals_for_batch,
    persist_hybrid_results,
)


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="x.csv", status=ProcessingStatus.PROCESSING, total_records=1)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def add_supervised_model(db_session, **overrides) -> ModelMetadata:
    defaults = dict(id=uuid.uuid4(), model_name="baseline", model_version="v1", model_type="random_forest")
    model = ModelMetadata(**{**defaults, **overrides})
    db_session.add(model)
    db_session.commit()
    return model


def add_anomaly_model(db_session, **overrides) -> ModelMetadata:
    defaults = dict(id=uuid.uuid4(), model_name="anomaly", model_version="v1", model_type="isolation_forest")
    model = ModelMetadata(**{**defaults, **overrides})
    db_session.add(model)
    db_session.commit()
    return model


def add_detection_result(db_session, batch, model, **overrides) -> DetectionResult:
    defaults = dict(batch_id=batch.id, model_id=model.id, row_number=1, predicted_label=0, prediction_name="benign")
    row = DetectionResult(**{**defaults, **overrides})
    db_session.add(row)
    db_session.commit()
    return row


def add_anomaly_result(db_session, batch, model, **overrides) -> AnomalyResult:
    defaults = dict(batch_id=batch.id, model_id=model.id, row_number=1, is_anomaly=False, anomaly_score=0.1)
    row = AnomalyResult(**{**defaults, **overrides})
    db_session.add(row)
    db_session.commit()
    return row


class TestCombineSignalsRules:
    def test_supervised_attack_and_anomaly_flagged_is_confirmed_attack(self):
        signal = HybridSignal(
            row_number=1,
            supervised_available=True,
            predicted_label=1,
            prediction_name="attack",
            anomaly_available=True,
            is_anomaly=True,
            anomaly_score=-0.5,
        )
        result = combine_signals(signal)
        assert result.outcome == HybridOutcome.CONFIRMED_ATTACK
        assert "supervised:attack" in result.contributing_signals
        assert "anomaly:flagged" in result.contributing_signals

    def test_supervised_attack_and_anomaly_not_flagged_is_supervised_attack_only(self):
        signal = HybridSignal(
            row_number=1,
            supervised_available=True,
            predicted_label=1,
            prediction_name="attack",
            anomaly_available=True,
            is_anomaly=False,
            anomaly_score=0.5,
        )
        result = combine_signals(signal)
        assert result.outcome == HybridOutcome.SUPERVISED_ATTACK_ONLY

    def test_supervised_benign_and_anomaly_flagged_is_anomaly_only(self):
        signal = HybridSignal(
            row_number=1,
            supervised_available=True,
            predicted_label=0,
            prediction_name="benign",
            anomaly_available=True,
            is_anomaly=True,
            anomaly_score=-0.3,
        )
        result = combine_signals(signal)
        assert result.outcome == HybridOutcome.ANOMALY_ONLY

    def test_supervised_benign_and_anomaly_not_flagged_is_clean(self):
        signal = HybridSignal(
            row_number=1,
            supervised_available=True,
            predicted_label=0,
            prediction_name="benign",
            anomaly_available=True,
            is_anomaly=False,
            anomaly_score=0.4,
        )
        result = combine_signals(signal)
        assert result.outcome == HybridOutcome.CLEAN

    def test_missing_anomaly_result_with_supervised_attack_is_supervised_attack_only(self):
        signal = HybridSignal(row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack")
        result = combine_signals(signal)
        assert result.outcome == HybridOutcome.SUPERVISED_ATTACK_ONLY
        assert "anomaly:unavailable" in result.contributing_signals

    def test_missing_anomaly_result_with_supervised_benign_is_clean(self):
        signal = HybridSignal(row_number=1, supervised_available=True, predicted_label=0, prediction_name="benign")
        result = combine_signals(signal)
        assert result.outcome == HybridOutcome.CLEAN
        assert "anomaly:unavailable" in result.contributing_signals

    def test_missing_supervised_result_with_anomaly_flagged_is_anomaly_only(self):
        signal = HybridSignal(row_number=1, supervised_available=False, anomaly_available=True, is_anomaly=True, anomaly_score=-0.5)
        result = combine_signals(signal)
        assert result.outcome == HybridOutcome.ANOMALY_ONLY
        assert "supervised:unavailable" in result.contributing_signals

    def test_missing_both_signals_is_clean(self):
        signal = HybridSignal(row_number=1, supervised_available=False, anomaly_available=False)
        result = combine_signals(signal)
        assert result.outcome == HybridOutcome.CLEAN
        assert "supervised:unavailable" in result.contributing_signals
        assert "anomaly:unavailable" in result.contributing_signals

    def test_determinism_same_input_same_output(self):
        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack",
            anomaly_available=True, is_anomaly=True, anomaly_score=-0.5,
        )
        first = combine_signals(signal)
        second = combine_signals(signal)
        assert first.outcome == second.outcome
        assert first.contributing_signals == second.contributing_signals


class TestOriginalSignalsPreserved:
    def test_attack_probability_and_anomaly_score_are_carried_through_unchanged(self):
        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack",
            attack_probability=0.87, anomaly_available=True, is_anomaly=True, anomaly_score=-1.234,
        )
        result = combine_signals(signal)
        assert result.signal.attack_probability == pytest.approx(0.87)
        assert result.signal.anomaly_score == pytest.approx(-1.234)

    def test_anomaly_score_is_never_treated_as_a_probability(self):
        # A large positive anomaly_score (far outside [0, 1]) must survive
        # unchanged — never clamped or reinterpreted as a probability.
        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=0, prediction_name="benign",
            anomaly_available=True, is_anomaly=False, anomaly_score=42.0,
        )
        result = combine_signals(signal)
        assert result.signal.anomaly_score == 42.0


class TestLoadHybridSignalsForBatch:
    def test_matches_detection_and_anomaly_rows_by_row_number(self, db_session):
        batch = add_batch(db_session)
        sup_model = add_supervised_model(db_session)
        anom_model = add_anomaly_model(db_session)
        add_detection_result(db_session, batch, sup_model, row_number=1, predicted_label=1, prediction_name="attack")
        add_anomaly_result(db_session, batch, anom_model, row_number=1, is_anomaly=True, anomaly_score=-0.2)

        signals = load_hybrid_signals_for_batch(db_session, batch.id)

        assert len(signals) == 1
        assert signals[0].supervised_available and signals[0].anomaly_available
        assert signals[0].predicted_label == 1
        assert signals[0].is_anomaly is True

    def test_row_with_only_supervised_result_is_handled_safely(self, db_session):
        batch = add_batch(db_session)
        sup_model = add_supervised_model(db_session)
        add_detection_result(db_session, batch, sup_model, row_number=1)

        signals = load_hybrid_signals_for_batch(db_session, batch.id)

        assert len(signals) == 1
        assert signals[0].supervised_available is True
        assert signals[0].anomaly_available is False

    def test_row_with_only_anomaly_result_is_handled_safely(self, db_session):
        batch = add_batch(db_session)
        anom_model = add_anomaly_model(db_session)
        add_anomaly_result(db_session, batch, anom_model, row_number=1)

        signals = load_hybrid_signals_for_batch(db_session, batch.id)

        assert len(signals) == 1
        assert signals[0].supervised_available is False
        assert signals[0].anomaly_available is True

    def test_batch_isolation(self, db_session):
        batch_a = add_batch(db_session)
        batch_b = add_batch(db_session)
        sup_model = add_supervised_model(db_session)
        add_detection_result(db_session, batch_a, sup_model, row_number=1, predicted_label=1, prediction_name="attack")
        add_detection_result(db_session, batch_b, sup_model, row_number=1, predicted_label=0, prediction_name="benign")

        signals_a = load_hybrid_signals_for_batch(db_session, batch_a.id)
        signals_b = load_hybrid_signals_for_batch(db_session, batch_b.id)

        assert signals_a[0].predicted_label == 1
        assert signals_b[0].predicted_label == 0

    def test_deterministic_row_number_ordering(self, db_session):
        batch = add_batch(db_session)
        sup_model = add_supervised_model(db_session)
        add_detection_result(db_session, batch, sup_model, row_number=3)
        add_detection_result(db_session, batch, sup_model, row_number=1)
        add_detection_result(db_session, batch, sup_model, row_number=2)

        signals = load_hybrid_signals_for_batch(db_session, batch.id)

        assert [s.row_number for s in signals] == [1, 2, 3]


class TestPersistence:
    def test_persisted_rows_match_computed_outcomes(self, db_session):
        batch = add_batch(db_session)
        sup_model = add_supervised_model(db_session)
        anom_model = add_anomaly_model(db_session)
        add_detection_result(db_session, batch, sup_model, row_number=1, predicted_label=1, prediction_name="attack")
        add_anomaly_result(db_session, batch, anom_model, row_number=1, is_anomaly=True, anomaly_score=-0.5)

        results = build_and_persist_hybrid_results(db_session, batch.id)

        assert len(results) == 1
        outcome_result, hybrid_row = results[0]
        assert outcome_result.outcome == HybridOutcome.CONFIRMED_ATTACK
        assert hybrid_row.outcome == "confirmed_attack"
        assert hybrid_row.batch_id == batch.id
        assert hybrid_row.row_number == 1
        assert hybrid_row.contributing_signals == outcome_result.contributing_signals

    def test_empty_batch_persists_nothing(self, db_session):
        batch = add_batch(db_session)
        results = build_and_persist_hybrid_results(db_session, batch.id)
        assert results == []

    def test_duplicate_persistence_attempt_is_rejected(self, db_session):
        batch = add_batch(db_session)
        sup_model = add_supervised_model(db_session)
        add_detection_result(db_session, batch, sup_model, row_number=1)
        signal = load_hybrid_signals_for_batch(db_session, batch.id)[0]
        outcome_result = combine_signals(signal)

        persist_hybrid_results(db_session, batch.id, [outcome_result])

        with pytest.raises(HybridDetectionPersistenceError):
            persist_hybrid_results(db_session, batch.id, [outcome_result])

        rows = db_session.scalars(select(HybridDetectionResult).where(HybridDetectionResult.batch_id == batch.id)).all()
        assert len(rows) == 1

    def test_commit_failure_rolls_back(self, db_session, monkeypatch):
        batch = add_batch(db_session)
        sup_model = add_supervised_model(db_session)
        add_detection_result(db_session, batch, sup_model, row_number=1)
        signal = load_hybrid_signals_for_batch(db_session, batch.id)[0]
        outcome_result = combine_signals(signal)

        def failing_commit():
            raise SQLAlchemyError("database is locked")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(HybridDetectionPersistenceError):
            persist_hybrid_results(db_session, batch.id, [outcome_result])

        monkeypatch.undo()
        rows = db_session.scalars(select(HybridDetectionResult).where(HybridDetectionResult.batch_id == batch.id)).all()
        assert rows == []

    def test_detection_result_and_anomaly_result_rows_are_unaffected(self, db_session):
        batch = add_batch(db_session)
        sup_model = add_supervised_model(db_session)
        anom_model = add_anomaly_model(db_session)
        det = add_detection_result(db_session, batch, sup_model, row_number=1, predicted_label=1, prediction_name="attack", attack_probability=0.9)
        anom = add_anomaly_result(db_session, batch, anom_model, row_number=1, is_anomaly=True, anomaly_score=-0.7)

        build_and_persist_hybrid_results(db_session, batch.id)

        db_session.refresh(det)
        db_session.refresh(anom)
        assert det.attack_probability == pytest.approx(0.9)
        assert anom.anomaly_score == pytest.approx(-0.7)
