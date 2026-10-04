"""Tests for the MVP ORM models: DetectionBatch, ModelMetadata, DetectionResult.

All tests use the isolated in-memory SQLite database provided by the
`db_session` fixture (see conftest.py) — never the real development
database file.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.models.detection_batch import DetectionBatch
from app.models.detection_result import DetectionResult
from app.models.enums import ProcessingStatus
from app.models.model_metadata import ModelMetadata


def test_all_three_tables_are_created(db_session):
    table_names = set(inspect(db_session.get_bind()).get_table_names())
    assert {"detection_batches", "detection_results", "model_metadata"} <= table_names


def test_insert_detection_batch(db_session):
    batch = DetectionBatch(filename="sample.csv")
    db_session.add(batch)
    db_session.commit()

    assert batch.id is not None
    assert batch.status == ProcessingStatus.PENDING
    assert batch.created_at is not None


def test_insert_model_metadata(db_session):
    model = ModelMetadata(model_name="baseline-classifier", model_version="0.1.0")
    db_session.add(model)
    db_session.commit()

    assert model.id is not None
    assert model.is_active is False
    assert model.evaluation_metrics is None


def test_detection_result_references_batch_and_model_correctly(db_session):
    batch = DetectionBatch(filename="sample.csv")
    model = ModelMetadata(model_name="baseline-classifier", model_version="0.1.0")
    db_session.add_all([batch, model])
    db_session.commit()

    result = DetectionResult(
        batch_id=batch.id,
        model_id=model.id,
        row_number=1,
        predicted_label=0,
        prediction_name="benign",
        attack_probability=0.13,
    )
    db_session.add(result)
    db_session.commit()

    assert result.batch_id == batch.id
    assert result.model_id == model.id


def test_relationships_navigate_in_both_directions(db_session):
    batch = DetectionBatch(filename="sample.csv")
    model = ModelMetadata(model_name="baseline-classifier", model_version="0.1.0")
    db_session.add_all([batch, model])
    db_session.commit()

    result = DetectionResult(
        batch_id=batch.id,
        model_id=model.id,
        row_number=1,
        predicted_label=1,
        prediction_name="attack",
        attack_probability=0.93,
    )
    db_session.add(result)
    db_session.commit()
    db_session.refresh(batch)
    db_session.refresh(model)

    assert result.batch.id == batch.id
    assert result.model.id == model.id
    assert result in batch.results
    assert result in model.results


def test_required_fields_enforce_not_null_constraint(db_session):
    # DetectionBatch.filename is required.
    db_session.add(DetectionBatch(filename=None))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_attack_probability_can_be_null(db_session):
    # A model that doesn't support predict_proba genuinely has no
    # probability to report — NULL preserves that, never a fabricated
    # placeholder like 0.0.
    batch = DetectionBatch(filename="sample.csv")
    model = ModelMetadata(model_name="baseline-classifier", model_version="0.1.0")
    db_session.add_all([batch, model])
    db_session.commit()

    result = DetectionResult(
        batch_id=batch.id,
        model_id=model.id,
        row_number=1,
        predicted_label=0,
        prediction_name="benign",
    )
    db_session.add(result)
    db_session.commit()

    assert result.attack_probability is None


def test_attack_probability_stores_numeric_value(db_session):
    batch = DetectionBatch(filename="sample.csv")
    model = ModelMetadata(model_name="baseline-classifier", model_version="0.1.0")
    db_session.add_all([batch, model])
    db_session.commit()

    result = DetectionResult(
        batch_id=batch.id,
        model_id=model.id,
        row_number=1,
        predicted_label=1,
        prediction_name="attack",
        attack_probability=0.733,
    )
    db_session.add(result)
    db_session.commit()
    db_session.refresh(result)

    assert isinstance(result.attack_probability, float)
    assert result.attack_probability == pytest.approx(0.733)


def test_out_of_range_attack_probability_is_rejected_by_database_check_constraint(db_session):
    """Range validation is expected to also happen at the API/Pydantic
    layer in a later step; this verifies the database-level backstop
    (CHECK constraint) that exists regardless of the application layer."""
    batch = DetectionBatch(filename="sample.csv")
    model = ModelMetadata(model_name="baseline-classifier", model_version="0.1.0")
    db_session.add_all([batch, model])
    db_session.commit()

    result = DetectionResult(
        batch_id=batch.id,
        model_id=model.id,
        row_number=1,
        predicted_label=1,
        prediction_name="attack",
        attack_probability=1.5,  # out of the documented 0.0-1.0 range
    )
    db_session.add(result)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_predicted_label_outside_zero_or_one_is_rejected(db_session):
    batch = DetectionBatch(filename="sample.csv")
    model = ModelMetadata(model_name="baseline-classifier", model_version="0.1.0")
    db_session.add_all([batch, model])
    db_session.commit()

    result = DetectionResult(
        batch_id=batch.id,
        model_id=model.id,
        row_number=1,
        predicted_label=2,
        prediction_name="attack",
    )
    db_session.add(result)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_prediction_name_outside_benign_or_attack_is_rejected(db_session):
    batch = DetectionBatch(filename="sample.csv")
    model = ModelMetadata(model_name="baseline-classifier", model_version="0.1.0")
    db_session.add_all([batch, model])
    db_session.commit()

    result = DetectionResult(
        batch_id=batch.id,
        model_id=model.id,
        row_number=1,
        predicted_label=0,
        prediction_name="suspicious",
    )
    db_session.add(result)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_duplicate_batch_and_row_number_is_rejected(db_session):
    batch = DetectionBatch(filename="sample.csv")
    model = ModelMetadata(model_name="baseline-classifier", model_version="0.1.0")
    db_session.add_all([batch, model])
    db_session.commit()

    db_session.add(
        DetectionResult(batch_id=batch.id, model_id=model.id, row_number=1, predicted_label=0, prediction_name="benign")
    )
    db_session.commit()

    db_session.add(
        DetectionResult(batch_id=batch.id, model_id=model.id, row_number=1, predicted_label=1, prediction_name="attack")
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_timestamps_are_populated_on_insert(db_session):
    batch = DetectionBatch(filename="sample.csv")
    db_session.add(batch)
    db_session.commit()
    db_session.refresh(batch)

    assert isinstance(batch.created_at, datetime)
    assert batch.completed_at is None


def test_detection_result_rejects_nonexistent_batch_foreign_key(db_session):
    model = ModelMetadata(model_name="baseline-classifier", model_version="0.1.0")
    db_session.add(model)
    db_session.commit()

    result = DetectionResult(
        batch_id=uuid.uuid4(),  # does not exist
        model_id=model.id,
        row_number=1,
        predicted_label=0,
        prediction_name="benign",
    )
    db_session.add(result)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
