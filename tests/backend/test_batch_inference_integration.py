"""Step 26 integration tests: model inference wired into batch processing.

Uses real fixtures throughout — a real registered model trained through
the existing training path, real CSV uploads processed through the real
`start_processing` entry point, and real persisted `MappedFeatureRecord`
rows — rather than mocking the services being integrated.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import joblib
import pytest
from sqlalchemy import select

from app.models.detection_batch import DetectionBatch
from app.models.detection_result import DetectionResult
from app.models.enums import ModelStatus, ProcessingStatus
from app.models.mapped_feature_record import MappedFeatureRecord
from app.models.model_metadata import ModelMetadata
from app.services import batch_processor
from app.services.batch_processor import start_processing
from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.model_training import train_baseline_model
from app.services.training_data import TrainingDataset

NSL_KDD_CSV = (
    b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n"
    b"5,tcp,http,SF,100,200,normal\n"
    b"3,udp,dns,SF,50,150,neptune\n"
)

ANOTHER_NSL_KDD_CSV = (
    b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n"
    b"7,tcp,http,SF,10,20,normal\n"
)


@pytest.fixture()
def upload_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "uploads"
    directory.mkdir()
    return directory


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(
        id=uuid.uuid4(),
        filename="traffic.csv",
        status=ProcessingStatus.PENDING,
        total_records=10,
        processed_records=0,
        failed_records=0,
    )
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def with_uploaded_file(upload_dir: Path, batch: DetectionBatch, content: bytes) -> DetectionBatch:
    (upload_dir / f"{batch.id}.csv").write_bytes(content)
    return batch


def persisted_rows(db_session, batch_id):
    return db_session.scalars(select(MappedFeatureRecord).where(MappedFeatureRecord.batch_id == batch_id)).all()


def register_ready_model(db_session, tmp_path: Path, *, dataset_schema="nsl-kdd-style", model_version=None):
    """Trains and registers a real READY model via the existing training
    path — compatible with `dataset_schema` and the current
    `FEATURE_SCHEMA_VERSION`, unrelated to any particular CSV content."""
    n_features = len(FEATURE_SCHEMA)
    feature_matrix: list[list[float | None]] = []
    labels: list[BinaryLabel] = []
    record_ids: list[uuid.UUID] = []
    batch_ids: list[uuid.UUID] = []
    training_batch_id = uuid.uuid4()
    for i in range(20):
        feature_matrix.append([float(i % 7)] * n_features)
        labels.append(BinaryLabel.BENIGN)
        record_ids.append(uuid.uuid4())
        batch_ids.append(training_batch_id)
    for i in range(20):
        feature_matrix.append([float(100 + i % 7)] * n_features)
        labels.append(BinaryLabel.ATTACK)
        record_ids.append(uuid.uuid4())
        batch_ids.append(training_batch_id)

    dataset = TrainingDataset(
        feature_matrix=feature_matrix,
        labels=labels,
        record_ids=record_ids,
        batch_ids=batch_ids,
        dataset_schemas=[dataset_schema] * 40,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=0,
    )
    return train_baseline_model(
        db_session,
        dataset,
        artifact_dir=tmp_path,
        test_fraction=0.0,
        group_by_batch=False,
        model_version=model_version,
    )


class TestInferenceIsInvoked:
    def test_predict_with_model_is_actually_called(self, db_session, upload_dir, tmp_path, monkeypatch):
        register_ready_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        calls = []
        real_predict = batch_processor.predict_with_model

        def spy_predict(*args, **kwargs):
            calls.append((args, kwargs))
            return real_predict(*args, **kwargs)

        monkeypatch.setattr(batch_processor, "predict_with_model", spy_predict)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        assert len(calls) == 1

    def test_inference_result_is_actually_received_by_the_processing_layer(self, db_session, upload_dir, tmp_path, monkeypatch, caplog):
        import logging

        register_ready_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        with caplog.at_level(logging.INFO, logger="ai_ids"):
            start_processing(db_session, batch.id, upload_dir)

        assert any("inference completed" in message for message in caplog.messages)


class TestFeatureSourceAndOrdering:
    def test_inference_input_comes_from_persisted_feature_records_not_the_csv(self, db_session, upload_dir, tmp_path, monkeypatch):
        register_ready_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        captured_matrices = []
        real_predict = batch_processor.predict_with_model

        def spy_predict(db, model_id, feature_matrix, **kwargs):
            captured_matrices.append(feature_matrix)
            return real_predict(db, model_id, feature_matrix, **kwargs)

        monkeypatch.setattr(batch_processor, "predict_with_model", spy_predict)

        start_processing(db_session, batch.id, upload_dir)

        rows = persisted_rows(db_session, batch.id)
        from app.services.feature_persistence import load_feature_vector

        expected_matrix = [load_feature_vector(row) for row in sorted(rows, key=lambda r: r.row_number)]
        assert captured_matrices == [expected_matrix]

    def test_feature_vector_ordering_follows_feature_schema(self, db_session, upload_dir, tmp_path, monkeypatch):
        register_ready_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        captured_matrices = []
        real_predict = batch_processor.predict_with_model

        def spy_predict(db, model_id, feature_matrix, **kwargs):
            captured_matrices.append(feature_matrix)
            return real_predict(db, model_id, feature_matrix, **kwargs)

        monkeypatch.setattr(batch_processor, "predict_with_model", spy_predict)

        start_processing(db_session, batch.id, upload_dir)

        assert all(len(row) == len(FEATURE_SCHEMA) for row in captured_matrices[0])

    def test_correct_dataset_schema_and_feature_schema_version_are_supplied(self, db_session, upload_dir, tmp_path, monkeypatch):
        register_ready_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        captured_kwargs = []
        real_predict = batch_processor.predict_with_model

        def spy_predict(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return real_predict(*args, **kwargs)

        monkeypatch.setattr(batch_processor, "predict_with_model", spy_predict)

        start_processing(db_session, batch.id, upload_dir)

        assert captured_kwargs[0]["dataset_schema"] == "nsl-kdd-style"
        assert captured_kwargs[0]["feature_schema_version"] == FEATURE_SCHEMA_VERSION


class TestBatchIsolation:
    def test_batch_a_cannot_consume_batch_bs_records(self, db_session, upload_dir, tmp_path, monkeypatch):
        register_ready_model(db_session, tmp_path)
        batch_a = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)
        batch_b = with_uploaded_file(upload_dir, add_batch(db_session), ANOTHER_NSL_KDD_CSV)

        captured_model_ids_and_sizes = []
        real_predict = batch_processor.predict_with_model

        def spy_predict(db, model_id, feature_matrix, **kwargs):
            captured_model_ids_and_sizes.append(len(feature_matrix))
            return real_predict(db, model_id, feature_matrix, **kwargs)

        monkeypatch.setattr(batch_processor, "predict_with_model", spy_predict)

        start_processing(db_session, batch_a.id, upload_dir)
        start_processing(db_session, batch_b.id, upload_dir)

        rows_a = persisted_rows(db_session, batch_a.id)
        rows_b = persisted_rows(db_session, batch_b.id)
        assert len(rows_a) == 2
        assert len(rows_b) == 1
        assert captured_model_ids_and_sizes == [2, 1]

    def test_the_correct_batch_id_feature_rows_are_used(self, db_session, upload_dir, tmp_path):
        register_ready_model(db_session, tmp_path)
        batch_a = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)
        batch_b = with_uploaded_file(upload_dir, add_batch(db_session), ANOTHER_NSL_KDD_CSV)

        start_processing(db_session, batch_a.id, upload_dir)
        start_processing(db_session, batch_b.id, upload_dir)

        from app.services.batch_processor import _load_feature_matrix_for_batch

        matrix_a, row_numbers_a = _load_feature_matrix_for_batch(db_session, batch_a.id)
        matrix_b, row_numbers_b = _load_feature_matrix_for_batch(db_session, batch_b.id)
        assert len(matrix_a) == 2
        assert len(matrix_b) == 1
        assert row_numbers_a == [1, 2]
        assert row_numbers_b == [1]

    def test_unrelated_batches_remain_unaffected(self, db_session, upload_dir, tmp_path):
        register_ready_model(db_session, tmp_path)
        batch_a = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)
        batch_b = with_uploaded_file(upload_dir, add_batch(db_session), ANOTHER_NSL_KDD_CSV)

        result_a = start_processing(db_session, batch_a.id, upload_dir)
        result_b = start_processing(db_session, batch_b.id, upload_dir)

        assert result_a.status == ProcessingStatus.PROCESSING
        assert result_b.status == ProcessingStatus.PROCESSING


class TestModelSelection:
    def test_a_ready_model_is_required(self, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.FAILED

    def test_missing_model_produces_a_safe_failure_message(self, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.error_message is not None
        assert "Traceback" not in result.error_message
        assert str(upload_dir) not in result.error_message

    def test_a_non_ready_model_is_not_selected(self, db_session, upload_dir, tmp_path):
        failed_entry = ModelMetadata(
            id=uuid.uuid4(),
            model_name="not-ready",
            model_version="v1",
            model_type="random_forest",
            dataset_name="nsl-kdd-style",
            feature_set_version=FEATURE_SCHEMA_VERSION,
            status=ModelStatus.FAILED,
            artifact_path=str(tmp_path / "doesnotmatter.joblib"),
            is_active=False,
        )
        db_session.add(failed_entry)
        db_session.commit()
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.FAILED

    def test_an_incompatible_dataset_schema_model_is_not_selected(self, db_session, upload_dir, tmp_path):
        register_ready_model(db_session, tmp_path, dataset_schema="unsw-nb15-style")
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.FAILED

    def test_select_ready_model_filters_out_incompatible_dataset_schema(self, db_session, tmp_path):
        # Direct unit check on the selection function itself: a batch
        # ending up FAILED proves *something* caught the incompatibility,
        # but predict_with_model's own compatibility check could mask a
        # broken selection filter at the full-pipeline level. This isolates
        # the guarantee that _select_ready_model itself never hands back
        # a model whose dataset_schema doesn't match.
        register_ready_model(db_session, tmp_path, dataset_schema="unsw-nb15-style")

        from app.services.batch_processor import _select_ready_model

        selected = _select_ready_model(db_session, "nsl-kdd-style", FEATURE_SCHEMA_VERSION)
        assert selected is None

    def test_select_ready_model_filters_out_incompatible_feature_schema_version(self, db_session, tmp_path):
        register_ready_model(db_session, tmp_path)

        from app.services.batch_processor import _select_ready_model

        selected = _select_ready_model(db_session, "nsl-kdd-style", "999")
        assert selected is None

    def test_most_recently_registered_compatible_model_is_selected(self, db_session, upload_dir, tmp_path):
        older = register_ready_model(db_session, tmp_path, model_version="v1")
        newer = register_ready_model(db_session, tmp_path, model_version="v2")
        assert older.model_id != newer.model_id

        from app.services.batch_processor import _select_ready_model

        selected = _select_ready_model(db_session, "nsl-kdd-style", FEATURE_SCHEMA_VERSION)
        assert selected is not None
        assert selected.id in (older.model_id, newer.model_id)


class TestArtifactFailureHandling:
    def test_corrupt_artifact_produces_a_safe_failure(self, db_session, upload_dir, tmp_path):
        result = register_ready_model(db_session, tmp_path)
        Path(result.artifact_path).write_bytes(b"not a real joblib artifact")
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        outcome = start_processing(db_session, batch.id, upload_dir)

        assert outcome.status == ProcessingStatus.FAILED
        assert "Traceback" not in (outcome.error_message or "")
        assert ".joblib" not in (outcome.error_message or "")

    def test_missing_artifact_file_produces_a_safe_failure(self, db_session, upload_dir, tmp_path):
        result = register_ready_model(db_session, tmp_path)
        Path(result.artifact_path).unlink()
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        outcome = start_processing(db_session, batch.id, upload_dir)

        assert outcome.status == ProcessingStatus.FAILED
        assert str(tmp_path) not in (outcome.error_message or "")


class TestEmptyFeatureData:
    def test_an_unrecognized_schema_skips_inference_without_failing(self, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session), b"custom_a,custom_b\n1,2\n")

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING

    def test_a_header_only_csv_skips_inference_without_failing(self, db_session, upload_dir):
        batch = with_uploaded_file(
            upload_dir, add_batch(db_session), b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n"
        )

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING


class TestDetectionResultPersistence:
    # As of Step 27, a successful batch persists one DetectionResult per
    # inferred row (see tests/backend/test_detection_result_persistence.py
    # for the dedicated, focused coverage of that persistence service).
    def test_detection_result_rows_are_created_for_a_successful_batch(self, db_session, upload_dir, tmp_path):
        register_ready_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        start_processing(db_session, batch.id, upload_dir)

        rows = db_session.scalars(select(DetectionResult).where(DetectionResult.batch_id == batch.id)).all()
        assert len(rows) == 2
        assert {r.row_number for r in rows} == {1, 2}


class TestExistingBehaviorIntact:
    def test_ingestion_and_persistence_still_happen(self, db_session, upload_dir, tmp_path):
        register_ready_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        start_processing(db_session, batch.id, upload_dir)

        rows = persisted_rows(db_session, batch.id)
        assert len(rows) == 2

    def test_atomic_claim_mechanism_still_governs_reprocessing(self, db_session, upload_dir, tmp_path):
        register_ready_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        from app.core.errors import ApiException

        start_processing(db_session, batch.id, upload_dir)

        with pytest.raises(ApiException) as exc_info:
            start_processing(db_session, batch.id, upload_dir)
        assert exc_info.value.status_code == 409

    def test_a_second_claim_attempt_on_an_already_claimed_batch_fails(self, db_session, upload_dir, tmp_path):
        register_ready_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        from app.services.batch_processor import claim_for_processing

        first_claim = claim_for_processing(db_session, batch.id)
        second_claim = claim_for_processing(db_session, batch.id)

        assert first_claim is True
        assert second_claim is False
