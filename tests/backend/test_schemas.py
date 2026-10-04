"""Tests for Pydantic API schemas (app/schemas/).

Schemas are independent of the SQLAlchemy ORM models; the ORM-conversion
tests below only use ORM instances to prove `from_attributes` works,
via the isolated in-memory `db_session` fixture from conftest.py.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.batch import DetectionBatchSummary
from app.schemas.detection import DetectionResultBase, DetectionResultResponse
from app.schemas.enums import ProcessingStatus
from app.schemas.model import ModelMetadataResponse


def _valid_detection_kwargs(**overrides):
    kwargs = dict(
        row_number=1,
        predicted_label=1,
        prediction_name="attack",
        attack_probability=0.75,
    )
    kwargs.update(overrides)
    return kwargs


def test_valid_detection_result_data_is_accepted():
    schema = DetectionResultBase(**_valid_detection_kwargs())
    assert schema.row_number == 1
    assert schema.predicted_label == 1
    assert schema.prediction_name == "attack"
    assert schema.attack_probability == 0.75


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_attack_probability_within_range_is_accepted(value):
    schema = DetectionResultBase(**_valid_detection_kwargs(attack_probability=value))
    assert schema.attack_probability == value


@pytest.mark.parametrize("value", [-0.1, 1.1, -5, 2])
def test_attack_probability_outside_range_is_rejected(value):
    with pytest.raises(ValidationError):
        DetectionResultBase(**_valid_detection_kwargs(attack_probability=value))


@pytest.mark.parametrize("value", [0, 1])
def test_valid_predicted_label_values_are_accepted(value):
    schema = DetectionResultBase(**_valid_detection_kwargs(predicted_label=value))
    assert schema.predicted_label == value


def test_invalid_predicted_label_value_is_rejected():
    with pytest.raises(ValidationError):
        DetectionResultBase(**_valid_detection_kwargs(predicted_label=2))


@pytest.mark.parametrize("value", ["pending", "processing", "completed", "failed"])
def test_valid_batch_statuses_are_accepted(value):
    schema = DetectionBatchSummary(
        batch_id=uuid.uuid4(),
        filename="sample.csv",
        status=value,
        total_records=0,
        processed_records=0,
        failed_records=0,
        created_at=datetime.now(timezone.utc),
    )
    assert schema.status == ProcessingStatus(value)


def test_invalid_batch_status_is_rejected():
    with pytest.raises(ValidationError):
        DetectionBatchSummary(
            batch_id=uuid.uuid4(),
            filename="sample.csv",
            status="archived",
            total_records=0,
            processed_records=0,
            failed_records=0,
            created_at=datetime.now(timezone.utc),
        )


def test_attack_probability_can_be_null():
    schema = DetectionResultBase(
        row_number=1,
        predicted_label=0,
        prediction_name="benign",
    )
    assert schema.attack_probability is None


def test_model_metadata_can_contain_null_evaluation_metrics():
    schema = ModelMetadataResponse(
        id=uuid.uuid4(),
        model_name="baseline-classifier",
        model_version="0.1.0",
        is_active=False,
        created_at=datetime.now(timezone.utc),
    )
    assert schema.evaluation_metrics is None


def test_detection_result_response_converts_from_orm_object(db_session):
    from app.models.detection_batch import DetectionBatch
    from app.models.detection_result import DetectionResult
    from app.models.model_metadata import ModelMetadata

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
    db_session.refresh(result)

    response = DetectionResultResponse.model_validate(result)

    assert response.id == result.id
    assert response.batch_id == batch.id
    assert response.model_id == model.id
    assert response.prediction_name == "attack"
    assert response.attack_probability == pytest.approx(0.93)


def test_detection_result_response_serializes_to_json_compatible_data(db_session):
    from app.models.detection_batch import DetectionBatch
    from app.models.detection_result import DetectionResult
    from app.models.model_metadata import ModelMetadata

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
    db_session.refresh(result)

    response = DetectionResultResponse.model_validate(result)
    payload = json.loads(response.model_dump_json())

    assert isinstance(payload["id"], str)
    assert isinstance(payload["batch_id"], str)
    assert isinstance(payload["model_id"], str)
    assert payload["prediction_name"] == "benign"
    assert payload["attack_probability"] is None


def test_model_metadata_response_omits_artifact_path(db_session):
    from app.models.model_metadata import ModelMetadata

    model = ModelMetadata(
        model_name="baseline-classifier",
        model_version="0.1.0",
        artifact_path="/internal/storage/models/baseline-classifier-0.1.0.joblib",
    )
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)

    response = ModelMetadataResponse.model_validate(model)

    assert "artifact_path" not in response.model_dump()
    assert "artifact_path" not in json.loads(response.model_dump_json())
