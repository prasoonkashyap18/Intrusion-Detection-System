"""Tests for app.services.detection_result_persistence: an InferenceResult
-> persisted DetectionResult rows, transactionally and without rerunning
inference or feature extraction.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models.detection_batch import DetectionBatch
from app.models.detection_result import DetectionResult
from app.models.enums import ProcessingStatus
from app.models.mapped_feature_record import MappedFeatureRecord
from app.models.model_metadata import ModelMetadata
from app.services.detection_result_persistence import (
    DetectionResultPersistenceError,
    persist_detection_results,
)
from app.services.model_inference import InferenceResult, Prediction


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="x.csv", status=ProcessingStatus.PROCESSING, total_records=1)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def add_model(db_session, **overrides) -> ModelMetadata:
    defaults = dict(id=uuid.uuid4(), model_name="baseline", model_version="v1", model_type="random_forest")
    model = ModelMetadata(**{**defaults, **overrides})
    db_session.add(model)
    db_session.commit()
    return model


def make_inference_result(model_id, predictions: list[Prediction]) -> InferenceResult:
    return InferenceResult(
        model_id=model_id,
        model_name="baseline",
        model_version="v1",
        model_type="random_forest",
        feature_schema_version="1",
        dataset_schema="nsl-kdd-style",
        sample_count=len(predictions),
        predictions=predictions,
    )


def persisted_rows(db_session, batch_id):
    return db_session.scalars(select(DetectionResult).where(DetectionResult.batch_id == batch_id)).all()


class TestSuccessfulPersistence:
    def test_one_result_per_prediction(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        predictions = [
            Prediction(predicted_label=0, prediction_name="benign", attack_probability=0.1),
            Prediction(predicted_label=1, prediction_name="attack", attack_probability=0.9),
            Prediction(predicted_label=0, prediction_name="benign", attack_probability=0.2),
        ]
        inference_result = make_inference_result(model.id, predictions)

        rows = persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1, 2, 3])

        assert len(rows) == 3
        assert len(persisted_rows(db_session, batch.id)) == 3

    def test_correct_batch_association(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        inference_result = make_inference_result(
            model.id, [Prediction(predicted_label=0, prediction_name="benign")]
        )

        persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])

        row = persisted_rows(db_session, batch.id)[0]
        assert row.batch_id == batch.id
        assert row.model_id == model.id

    def test_row_number_is_preserved_in_order(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        predictions = [
            Prediction(predicted_label=0, prediction_name="benign"),
            Prediction(predicted_label=1, prediction_name="attack"),
        ]
        inference_result = make_inference_result(model.id, predictions)

        persist_detection_results(db_session, batch.id, inference_result, row_numbers=[5, 9])

        rows = {r.row_number: r for r in persisted_rows(db_session, batch.id)}
        assert set(rows) == {5, 9}
        assert rows[5].prediction_name == "benign"
        assert rows[9].prediction_name == "attack"

    def test_predicted_label_and_name_persist_correctly(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        inference_result = make_inference_result(
            model.id, [Prediction(predicted_label=1, prediction_name="attack")]
        )

        persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])

        row = persisted_rows(db_session, batch.id)[0]
        assert row.predicted_label == 1
        assert row.prediction_name == "attack"

    def test_attack_probability_persists_the_real_value(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        inference_result = make_inference_result(
            model.id, [Prediction(predicted_label=1, prediction_name="attack", attack_probability=0.733)]
        )

        persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])

        row = persisted_rows(db_session, batch.id)[0]
        assert row.attack_probability == pytest.approx(0.733)


class TestNoFabricatedProbability:
    def test_null_probability_stays_null_when_genuinely_unavailable(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        inference_result = make_inference_result(
            model.id, [Prediction(predicted_label=0, prediction_name="benign", attack_probability=None)]
        )

        persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])

        row = persisted_rows(db_session, batch.id)[0]
        assert row.attack_probability is None

    def test_a_null_probability_is_never_coerced_to_zero(self, db_session):
        # 0.0 is a legitimate, real probability value distinct from "no
        # probability available" — this proves the persistence layer
        # doesn't collapse the two.
        batch = add_batch(db_session)
        model = add_model(db_session)
        predictions = [
            Prediction(predicted_label=0, prediction_name="benign", attack_probability=None),
            Prediction(predicted_label=0, prediction_name="benign", attack_probability=0.0),
        ]
        inference_result = make_inference_result(model.id, predictions)

        persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1, 2])

        rows = {r.row_number: r for r in persisted_rows(db_session, batch.id)}
        assert rows[1].attack_probability is None
        assert rows[2].attack_probability == 0.0


class TestBatchIsolation:
    def test_results_from_two_batches_never_mix(self, db_session):
        batch_a = add_batch(db_session)
        batch_b = add_batch(db_session)
        model = add_model(db_session)

        persist_detection_results(
            db_session, batch_a.id, make_inference_result(model.id, [Prediction(0, "benign")]), row_numbers=[1]
        )
        persist_detection_results(
            db_session, batch_b.id, make_inference_result(model.id, [Prediction(1, "attack")]), row_numbers=[1]
        )

        rows_a = persisted_rows(db_session, batch_a.id)
        rows_b = persisted_rows(db_session, batch_b.id)
        assert len(rows_a) == 1 and len(rows_b) == 1
        assert rows_a[0].prediction_name == "benign"
        assert rows_b[0].prediction_name == "attack"
        assert rows_a[0].batch_id != rows_b[0].batch_id


class TestDuplicateRowProtection:
    def test_persisting_the_same_batch_and_row_number_twice_is_rejected(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        inference_result = make_inference_result(model.id, [Prediction(0, "benign")])
        persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])

        with pytest.raises(DetectionResultPersistenceError):
            persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])

    def test_a_rejected_duplicate_attempt_leaves_no_extra_rows(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        inference_result = make_inference_result(model.id, [Prediction(0, "benign")])
        persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])

        with pytest.raises(DetectionResultPersistenceError):
            persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])

        assert len(persisted_rows(db_session, batch.id)) == 1


class TestEmptyInferenceResult:
    def test_an_empty_inference_result_persists_nothing_and_does_not_raise(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        inference_result = make_inference_result(model.id, [])

        rows = persist_detection_results(db_session, batch.id, inference_result, row_numbers=[])

        assert rows == []
        assert persisted_rows(db_session, batch.id) == []


class TestTransactionRollback:
    def test_a_commit_failure_leaves_no_partial_results(self, db_session, monkeypatch):
        batch = add_batch(db_session)
        model = add_model(db_session)
        predictions = [
            Prediction(predicted_label=0, prediction_name="benign"),
            Prediction(predicted_label=1, prediction_name="attack"),
        ]
        inference_result = make_inference_result(model.id, predictions)

        def failing_commit():
            raise SQLAlchemyError("database is locked")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(DetectionResultPersistenceError):
            persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1, 2])

        monkeypatch.undo()
        assert persisted_rows(db_session, batch.id) == []

    def test_all_rows_commit_in_a_single_transaction(self, db_session, monkeypatch):
        # A per-row commit loop would leave an earlier row durable even
        # when a later one fails — this proves there is exactly one
        # commit for the whole batch of predictions, so a failure at any
        # point rolls back everything, not just what came after it.
        batch = add_batch(db_session)
        model = add_model(db_session)
        predictions = [
            Prediction(predicted_label=0, prediction_name="benign"),
            Prediction(predicted_label=1, prediction_name="attack"),
            Prediction(predicted_label=0, prediction_name="benign"),
        ]
        inference_result = make_inference_result(model.id, predictions)

        real_commit = db_session.commit
        commit_calls = []

        def spy_commit():
            commit_calls.append(1)
            return real_commit()

        monkeypatch.setattr(db_session, "commit", spy_commit)

        persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1, 2, 3])

        assert len(commit_calls) == 1
        assert len(persisted_rows(db_session, batch.id)) == 3

    def test_row_number_mismatch_raises_before_any_row_is_added(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        predictions = [
            Prediction(predicted_label=0, prediction_name="benign"),
            Prediction(predicted_label=1, prediction_name="attack"),
        ]
        inference_result = make_inference_result(model.id, predictions)

        with pytest.raises(DetectionResultPersistenceError):
            persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])  # only one, needs two

        assert persisted_rows(db_session, batch.id) == []


class TestNoRereadOrRerun:
    def test_persistence_never_queries_mapped_feature_record(self, db_session, monkeypatch):
        import app.services.detection_result_persistence as persistence_module

        assert "MappedFeatureRecord" not in dir(persistence_module)

        batch = add_batch(db_session)
        model = add_model(db_session)
        # Seed an unrelated MappedFeatureRecord row to prove it is never
        # touched by this call — if persistence read features itself, a
        # row_number mismatch here would surface differently.
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

        inference_result = make_inference_result(model.id, [Prediction(0, "benign")])
        persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])

        assert db_session.query(MappedFeatureRecord).count() == before_count

    def test_persistence_does_not_call_predict_with_model(self, db_session, monkeypatch):
        import app.services.detection_result_persistence as persistence_module

        assert not hasattr(persistence_module, "predict_with_model")

    def test_existing_mapped_feature_record_data_is_unchanged(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        record = MappedFeatureRecord(
            batch_id=batch.id,
            row_number=1,
            feature_schema_version="1",
            dataset_schema="nsl-kdd-style",
            features={"duration": {"value": 5.0, "status": "present", "source_column": "duration", "raw_value": "5"}},
            unknown_fields={},
        )
        db_session.add(record)
        db_session.commit()
        before_features = dict(record.features)

        inference_result = make_inference_result(model.id, [Prediction(0, "benign")])
        persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])

        db_session.refresh(record)
        assert record.features == before_features

    def test_existing_detection_batch_fields_are_unchanged(self, db_session):
        batch = add_batch(db_session, total_records=7, processed_records=0, failed_records=0)
        model = add_model(db_session)
        inference_result = make_inference_result(model.id, [Prediction(0, "benign")])

        persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])

        db_session.refresh(batch)
        assert batch.status == ProcessingStatus.PROCESSING
        assert batch.total_records == 7
        assert batch.processed_records == 0
        assert batch.failed_records == 0


class TestNoLeakage:
    def test_prediction_name_never_leaks_into_row_number_or_label_fields(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        inference_result = make_inference_result(model.id, [Prediction(1, "attack", attack_probability=0.5)])

        persist_detection_results(db_session, batch.id, inference_result, row_numbers=[3])

        row = persisted_rows(db_session, batch.id)[0]
        assert row.row_number == 3
        assert isinstance(row.predicted_label, int)
        assert row.prediction_name == "attack"


class TestSafeErrorMessages:
    def test_mismatch_error_message_has_no_internal_detail(self, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        inference_result = make_inference_result(model.id, [Prediction(0, "benign"), Prediction(1, "attack")])

        with pytest.raises(DetectionResultPersistenceError) as exc_info:
            persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])

        message = exc_info.value.message
        assert "Traceback" not in message
        assert "SELECT" not in message.upper()

    def test_commit_failure_error_message_is_safe(self, db_session, monkeypatch):
        batch = add_batch(db_session)
        model = add_model(db_session)
        inference_result = make_inference_result(model.id, [Prediction(0, "benign")])

        def failing_commit():
            raise SQLAlchemyError("database is locked at C:\\secret\\path\\db.sqlite")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(DetectionResultPersistenceError) as exc_info:
            persist_detection_results(db_session, batch.id, inference_result, row_numbers=[1])

        monkeypatch.undo()
        message = exc_info.value.message
        assert "secret" not in message
        assert "database is locked" not in message
        assert "Traceback" not in message
