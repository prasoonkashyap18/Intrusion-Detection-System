"""Tests for app.services.anomaly_result_persistence: an
AnomalyInferenceResult -> persisted AnomalyResult rows, transactionally
and without rerunning inference or feature extraction. Mirrors
test_detection_result_persistence.py's structure for the anomaly
counterpart.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models.anomaly_result import AnomalyResult
from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus
from app.models.mapped_feature_record import MappedFeatureRecord
from app.models.model_metadata import ModelMetadata
from app.services.anomaly_inference import AnomalyInferenceResult, AnomalyPrediction
from app.services.anomaly_result_persistence import (
    AnomalyResultPersistenceError,
    persist_anomaly_results,
)


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="x.csv", status=ProcessingStatus.PROCESSING, total_records=1)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def add_model(db_session, **overrides) -> ModelMetadata:
    defaults = dict(id=uuid.uuid4(), model_name="anomaly-baseline", model_version="v1", model_type="isolation_forest")
    model = ModelMetadata(**{**defaults, **overrides})
    db_session.add(model)
    db_session.commit()
    return model


def make_anomaly_result(model_id, predictions: list[AnomalyPrediction]) -> AnomalyInferenceResult:
    return AnomalyInferenceResult(
        model_id=model_id,
        model_name="anomaly-baseline",
        model_version="v1",
        model_type="isolation_forest",
        feature_schema_version="1",
        dataset_schema="nsl-kdd-style",
        sample_count=len(predictions),
        predictions=predictions,
    )


def persisted_rows(db_session, batch_id):
    return db_session.scalars(select(AnomalyResult).where(AnomalyResult.batch_id == batch_id)).all()


class TestSuccessfulPersistence:
    def test_one_result_per_prediction(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        predictions = [
            AnomalyPrediction(is_anomaly=False, anomaly_score=0.3),
            AnomalyPrediction(is_anomaly=True, anomaly_score=-0.2),
            AnomalyPrediction(is_anomaly=False, anomaly_score=0.1),
        ]
        anomaly_result = make_anomaly_result(model.id, predictions)

        rows = persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1, 2, 3])

        assert len(rows) == 3
        assert len(persisted_rows(db_session, batch.id)) == 3

    def test_correct_batch_and_model_association(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        anomaly_result = make_anomaly_result(model.id, [AnomalyPrediction(is_anomaly=False, anomaly_score=0.5)])

        persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1])

        row = persisted_rows(db_session, batch.id)[0]
        assert row.batch_id == batch.id
        assert row.model_id == model.id

    def test_row_number_is_preserved_in_order(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        predictions = [
            AnomalyPrediction(is_anomaly=False, anomaly_score=0.4),
            AnomalyPrediction(is_anomaly=True, anomaly_score=-0.6),
        ]
        anomaly_result = make_anomaly_result(model.id, predictions)

        persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[5, 9])

        rows = {r.row_number: r for r in persisted_rows(db_session, batch.id)}
        assert set(rows) == {5, 9}
        assert rows[5].is_anomaly is False
        assert rows[9].is_anomaly is True

    def test_is_anomaly_and_score_persist_correctly(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        anomaly_result = make_anomaly_result(
            model.id, [AnomalyPrediction(is_anomaly=True, anomaly_score=-0.733)]
        )

        persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1])

        row = persisted_rows(db_session, batch.id)[0]
        assert row.is_anomaly is True
        assert row.anomaly_score == pytest.approx(-0.733)


class TestScoreValidity:
    def test_anomaly_score_is_never_coerced_or_clamped(self, db_session):
        # anomaly_score has no [0, 1] bound (unlike attack_probability) —
        # proves an out-of-[0,1]-range real score persists unchanged.
        batch = add_batch(db_session)
        model = add_model(db_session)
        predictions = [
            AnomalyPrediction(is_anomaly=True, anomaly_score=-5.42),
            AnomalyPrediction(is_anomaly=False, anomaly_score=3.14),
        ]
        anomaly_result = make_anomaly_result(model.id, predictions)

        persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1, 2])

        rows = {r.row_number: r for r in persisted_rows(db_session, batch.id)}
        assert rows[1].anomaly_score == pytest.approx(-5.42)
        assert rows[2].anomaly_score == pytest.approx(3.14)

    def test_anomaly_score_column_rejects_null(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        row = AnomalyResult(batch_id=batch.id, model_id=model.id, row_number=1, is_anomaly=False, anomaly_score=None)
        db_session.add(row)

        with pytest.raises(SQLAlchemyError):
            db_session.commit()
        db_session.rollback()


class TestBatchIsolation:
    def test_results_from_two_batches_never_mix(self, db_session):
        batch_a = add_batch(db_session)
        batch_b = add_batch(db_session)
        model = add_model(db_session)

        persist_anomaly_results(
            db_session, batch_a.id, make_anomaly_result(model.id, [AnomalyPrediction(False, 0.2)]), row_numbers=[1]
        )
        persist_anomaly_results(
            db_session, batch_b.id, make_anomaly_result(model.id, [AnomalyPrediction(True, -0.2)]), row_numbers=[1]
        )

        rows_a = persisted_rows(db_session, batch_a.id)
        rows_b = persisted_rows(db_session, batch_b.id)
        assert len(rows_a) == 1 and len(rows_b) == 1
        assert rows_a[0].is_anomaly is False
        assert rows_b[0].is_anomaly is True
        assert rows_a[0].batch_id != rows_b[0].batch_id


class TestDuplicateRowProtection:
    def test_persisting_the_same_batch_and_row_number_twice_is_rejected(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        anomaly_result = make_anomaly_result(model.id, [AnomalyPrediction(False, 0.1)])
        persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1])

        with pytest.raises(AnomalyResultPersistenceError):
            persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1])

    def test_a_rejected_duplicate_attempt_leaves_no_extra_rows(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        anomaly_result = make_anomaly_result(model.id, [AnomalyPrediction(False, 0.1)])
        persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1])

        with pytest.raises(AnomalyResultPersistenceError):
            persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1])

        assert len(persisted_rows(db_session, batch.id)) == 1


class TestEmptyAnomalyResult:
    def test_an_empty_anomaly_result_persists_nothing_and_does_not_raise(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        anomaly_result = make_anomaly_result(model.id, [])

        rows = persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[])

        assert rows == []
        assert persisted_rows(db_session, batch.id) == []


class TestTransactionRollback:
    def test_a_commit_failure_leaves_no_partial_results(self, db_session, monkeypatch):
        batch = add_batch(db_session)
        model = add_model(db_session)
        predictions = [
            AnomalyPrediction(is_anomaly=False, anomaly_score=0.3),
            AnomalyPrediction(is_anomaly=True, anomaly_score=-0.4),
        ]
        anomaly_result = make_anomaly_result(model.id, predictions)

        def failing_commit():
            raise SQLAlchemyError("database is locked")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(AnomalyResultPersistenceError):
            persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1, 2])

        monkeypatch.undo()
        assert persisted_rows(db_session, batch.id) == []

    def test_all_rows_commit_in_a_single_transaction(self, db_session, monkeypatch):
        batch = add_batch(db_session)
        model = add_model(db_session)
        predictions = [
            AnomalyPrediction(is_anomaly=False, anomaly_score=0.3),
            AnomalyPrediction(is_anomaly=True, anomaly_score=-0.4),
            AnomalyPrediction(is_anomaly=False, anomaly_score=0.1),
        ]
        anomaly_result = make_anomaly_result(model.id, predictions)

        real_commit = db_session.commit
        commit_calls = []

        def spy_commit():
            commit_calls.append(1)
            return real_commit()

        monkeypatch.setattr(db_session, "commit", spy_commit)

        persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1, 2, 3])

        assert len(commit_calls) == 1
        assert len(persisted_rows(db_session, batch.id)) == 3

    def test_row_number_mismatch_raises_before_any_row_is_added(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        predictions = [
            AnomalyPrediction(is_anomaly=False, anomaly_score=0.3),
            AnomalyPrediction(is_anomaly=True, anomaly_score=-0.4),
        ]
        anomaly_result = make_anomaly_result(model.id, predictions)

        with pytest.raises(AnomalyResultPersistenceError):
            persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1])  # only one, needs two

        assert persisted_rows(db_session, batch.id) == []


class TestNoRereadOrRerun:
    def test_persistence_never_queries_mapped_feature_record(self, db_session):
        import app.services.anomaly_result_persistence as persistence_module

        assert "MappedFeatureRecord" not in dir(persistence_module)

        batch = add_batch(db_session)
        model = add_model(db_session)
        db_session.add(
            MappedFeatureRecord(
                batch_id=batch.id,
                row_number=1,
                feature_schema_version="1",
                dataset_schema="nsl-kdd-style",
                features={},
                unknown_fields={},
            )
        )
        db_session.commit()
        before_count = db_session.query(MappedFeatureRecord).count()

        anomaly_result = make_anomaly_result(model.id, [AnomalyPrediction(False, 0.1)])
        persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1])

        assert db_session.query(MappedFeatureRecord).count() == before_count

    def test_persistence_does_not_call_predict_anomalies_with_model(self, db_session):
        import app.services.anomaly_result_persistence as persistence_module

        assert not hasattr(persistence_module, "predict_anomalies_with_model")

    def test_existing_detection_batch_fields_are_unchanged(self, db_session):
        batch = add_batch(db_session, total_records=7, processed_records=0, failed_records=0)
        model = add_model(db_session)
        anomaly_result = make_anomaly_result(model.id, [AnomalyPrediction(False, 0.1)])

        persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1])

        db_session.refresh(batch)
        assert batch.status == ProcessingStatus.PROCESSING
        assert batch.total_records == 7
        assert batch.processed_records == 0
        assert batch.failed_records == 0


class TestNoFabricatedRiskOrSeverity:
    def test_anomaly_result_model_has_no_risk_or_severity_attribute(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        anomaly_result = make_anomaly_result(model.id, [AnomalyPrediction(True, -0.5)])

        persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1])

        row = persisted_rows(db_session, batch.id)[0]
        for forbidden in ("risk_score", "severity", "confidence", "attack_probability", "predicted_label"):
            assert not hasattr(row, forbidden)


class TestSafeErrorMessages:
    def test_mismatch_error_message_has_no_internal_detail(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        anomaly_result = make_anomaly_result(model.id, [AnomalyPrediction(False, 0.1), AnomalyPrediction(True, -0.2)])

        with pytest.raises(AnomalyResultPersistenceError) as exc_info:
            persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1])

        message = exc_info.value.message
        assert "Traceback" not in message
        assert "SELECT" not in message.upper()

    def test_commit_failure_error_message_is_safe(self, db_session, monkeypatch):
        batch = add_batch(db_session)
        model = add_model(db_session)
        anomaly_result = make_anomaly_result(model.id, [AnomalyPrediction(False, 0.1)])

        def failing_commit():
            raise SQLAlchemyError("database is locked at C:\\secret\\path\\db.sqlite")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(AnomalyResultPersistenceError) as exc_info:
            persist_anomaly_results(db_session, batch.id, anomaly_result, row_numbers=[1])

        monkeypatch.undo()
        message = exc_info.value.message
        assert "secret" not in message
        assert "database is locked" not in message
        assert "Traceback" not in message
