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

from app.schemas.batch import DetectionBatchResponse
from app.schemas.detection import DetectionResultBase, DetectionResultResponse
from app.schemas.enums import ProcessingStatus, Severity
from app.schemas.model import ModelMetadataResponse


def _valid_detection_kwargs(**overrides):
    kwargs = dict(
        predicted_class="normal",
        confidence=0.75,
        severity=Severity.LOW,
        source_ip="10.0.0.1",
        destination_ip="10.0.0.2",
        source_port=1234,
        destination_port=80,
        protocol="TCP",
        flow_timestamp=datetime.now(timezone.utc),
    )
    kwargs.update(overrides)
    return kwargs


def test_valid_detection_result_data_is_accepted():
    schema = DetectionResultBase(**_valid_detection_kwargs())
    assert schema.predicted_class == "normal"
    assert schema.confidence == 0.75
    assert schema.severity == Severity.LOW


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_confidence_within_range_is_accepted(value):
    schema = DetectionResultBase(**_valid_detection_kwargs(confidence=value))
    assert schema.confidence == value


@pytest.mark.parametrize("value", [-0.1, 1.1, -5, 2])
def test_confidence_outside_range_is_rejected(value):
    with pytest.raises(ValidationError):
        DetectionResultBase(**_valid_detection_kwargs(confidence=value))


@pytest.mark.parametrize("value", ["low", "medium", "high", "critical"])
def test_valid_severity_values_are_accepted(value):
    schema = DetectionResultBase(**_valid_detection_kwargs(severity=value))
    assert schema.severity == Severity(value)


def test_invalid_severity_value_is_rejected():
    with pytest.raises(ValidationError):
        DetectionResultBase(**_valid_detection_kwargs(severity="super-critical"))


@pytest.mark.parametrize("value", ["pending", "processing", "completed", "failed"])
def test_valid_batch_statuses_are_accepted(value):
    schema = DetectionBatchResponse(
        id=uuid.uuid4(),
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
        DetectionBatchResponse(
            id=uuid.uuid4(),
            filename="sample.csv",
            status="archived",
            total_records=0,
            processed_records=0,
            failed_records=0,
            created_at=datetime.now(timezone.utc),
        )


def test_optional_network_fields_can_be_null():
    schema = DetectionResultBase(
        predicted_class="normal",
        confidence=0.5,
        severity=Severity.LOW,
    )
    assert schema.source_ip is None
    assert schema.destination_ip is None
    assert schema.source_port is None
    assert schema.destination_port is None
    assert schema.protocol is None
    assert schema.flow_timestamp is None


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
    from app.models.enums import Severity as ORMSeverity
    from app.models.model_metadata import ModelMetadata

    batch = DetectionBatch(filename="sample.csv")
    model = ModelMetadata(model_name="baseline-classifier", model_version="0.1.0")
    db_session.add_all([batch, model])
    db_session.commit()

    result = DetectionResult(
        batch_id=batch.id,
        model_id=model.id,
        predicted_class="dos",
        confidence=0.93,
        severity=ORMSeverity.HIGH,
    )
    db_session.add(result)
    db_session.commit()
    db_session.refresh(result)

    response = DetectionResultResponse.model_validate(result)

    assert response.id == result.id
    assert response.batch_id == batch.id
    assert response.model_id == model.id
    assert response.predicted_class == "dos"
    assert response.severity == Severity.HIGH


def test_detection_result_response_serializes_to_json_compatible_data(db_session):
    from app.models.detection_batch import DetectionBatch
    from app.models.detection_result import DetectionResult
    from app.models.enums import Severity as ORMSeverity
    from app.models.model_metadata import ModelMetadata

    batch = DetectionBatch(filename="sample.csv")
    model = ModelMetadata(model_name="baseline-classifier", model_version="0.1.0")
    db_session.add_all([batch, model])
    db_session.commit()

    result = DetectionResult(
        batch_id=batch.id,
        model_id=model.id,
        predicted_class="normal",
        confidence=0.42,
        severity=ORMSeverity.LOW,
    )
    db_session.add(result)
    db_session.commit()
    db_session.refresh(result)

    response = DetectionResultResponse.model_validate(result)
    payload = json.loads(response.model_dump_json())

    assert isinstance(payload["id"], str)
    assert isinstance(payload["batch_id"], str)
    assert isinstance(payload["model_id"], str)
    assert isinstance(payload["confidence"], float)
    assert payload["severity"] == "low"
    assert payload["source_ip"] is None


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
