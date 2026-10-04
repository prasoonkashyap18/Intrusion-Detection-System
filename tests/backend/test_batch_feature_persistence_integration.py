"""Tests for Step 21: the processing boundary now persists mapped features
(app.models.mapped_feature_record.MappedFeatureRecord) for a recognized
dataset schema, via app.services.feature_persistence, integrated into
app.services.batch_processor._run_feature_extraction / start_processing.

No test here trains a model, classifies traffic, or asserts that detection
happened — persistence only stores the deterministic feature representation
Steps 16/18/20 already compute. No batch is ever expected to reach
`completed` here.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.detection import get_upload_config
from app.db.database import get_db
from app.main import app
from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus
from app.models.mapped_feature_record import MappedFeatureRecord
from app.services import batch_processor
from app.services.batch_processor import start_processing
from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.model_training import train_baseline_model
from app.services.training_data import TrainingDataset
from app.services.upload_service import UploadConfig

NSL_KDD_CSV = (
    b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n"
    b"5,tcp,http,SF,100,200,normal\n"
    b"3,udp,dns,SF,50,150,neptune\n"
)


def url_for(batch_id) -> str:
    return f"/api/v1/detection/batches/{batch_id}/process"


@pytest.fixture()
def upload_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "uploads"
    directory.mkdir()
    return directory


@pytest.fixture()
def client(db_session, upload_dir: Path):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_upload_config] = lambda: UploadConfig(directory=upload_dir, max_bytes=1024 * 1024)
    yield TestClient(app)
    app.dependency_overrides.clear()


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


def register_ready_nsl_kdd_model(db_session, tmp_path: Path):
    """Registers a real, trained, READY model compatible with the
    `nsl-kdd-style` dataset schema and the current `FEATURE_SCHEMA_VERSION`
    — Step 26 wires model inference into processing, so a batch processed
    through a recognized schema now needs a compatible model to finish
    processing successfully (see batch_processor's "Inference boundary").
    Unrelated to the actual NSL_KDD_CSV content above; only the schema
    identity and feature-schema version need to line up.
    """
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
        dataset_schemas=["nsl-kdd-style"] * 40,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        excluded_unmappable_label_count=0,
    )
    return train_baseline_model(db_session, dataset, artifact_dir=tmp_path, test_fraction=0.0, group_by_batch=False)


class TestRecognizedSchemaPersistence:
    def test_a_recognized_schema_persists_one_row_per_data_row(self, db_session, upload_dir, tmp_path):
        register_ready_nsl_kdd_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        rows = persisted_rows(db_session, batch.id)
        assert len(rows) == 2
        assert {r.row_number for r in rows} == {1, 2}

    def test_persisted_rows_carry_the_matched_dataset_schema(self, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        start_processing(db_session, batch.id, upload_dir)

        rows = persisted_rows(db_session, batch.id)
        assert all(r.dataset_schema == "nsl-kdd-style" for r in rows)


class TestUnrecognizedSchemaNoPersistence:
    def test_an_unsupported_schema_persists_nothing_but_still_succeeds(self, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session), b"custom_a,custom_b\n1,2\n3,4\n")

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        assert persisted_rows(db_session, batch.id) == []


class TestEmptyBatchBehavior:
    def test_a_header_only_csv_persists_nothing_and_does_not_fail(self, db_session, upload_dir):
        batch = with_uploaded_file(
            upload_dir, add_batch(db_session), b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n"
        )

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        assert persisted_rows(db_session, batch.id) == []


class TestMultipleRecordsInOneBatch:
    def test_every_data_row_gets_its_own_persisted_record(self, db_session, upload_dir):
        content = b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n" + b"".join(
            f"{i},tcp,http,SF,{i},{i},normal\n".encode() for i in range(10)
        )
        batch = with_uploaded_file(upload_dir, add_batch(db_session), content)

        start_processing(db_session, batch.id, upload_dir)

        rows = persisted_rows(db_session, batch.id)
        assert len(rows) == 10
        assert sorted(r.row_number for r in rows) == list(range(1, 11))


class TestMultipleBatchesIsolation:
    def test_two_batches_processed_separately_never_mix_feature_rows(self, db_session, upload_dir):
        batch_a = with_uploaded_file(
            upload_dir, add_batch(db_session), b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n1,tcp,http,SF,1,1,normal\n"
        )
        batch_b = with_uploaded_file(
            upload_dir, add_batch(db_session), b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n2,udp,dns,SF,2,2,neptune\n"
        )

        start_processing(db_session, batch_a.id, upload_dir)
        start_processing(db_session, batch_b.id, upload_dir)

        rows_a = persisted_rows(db_session, batch_a.id)
        rows_b = persisted_rows(db_session, batch_b.id)
        assert len(rows_a) == 1 and len(rows_b) == 1
        assert rows_a[0].features["protocol_number"]["raw_value"] == "tcp"
        assert rows_b[0].features["protocol_number"]["raw_value"] == "udp"
        assert rows_a[0].batch_id != rows_b[0].batch_id


class TestTransactionRollback:
    def test_an_unexpected_mapping_error_leaves_zero_persisted_rows(self, db_session, upload_dir, monkeypatch):
        calls = {"n": 0}
        real_map = batch_processor.map_canonical_record

        def failing_on_second_row(canonical):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("disk I/O error at C:\\secret\\path\\traffic.csv")
            return real_map(canonical)

        monkeypatch.setattr(batch_processor, "map_canonical_record", failing_on_second_row)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.FAILED
        assert persisted_rows(db_session, batch.id) == []  # the first row's insert was rolled back too

    def test_rollback_error_message_is_safe_and_generic(self, db_session, upload_dir, monkeypatch):
        def boom(canonical):
            raise RuntimeError("disk I/O error at C:\\secret\\path\\traffic.csv")

        monkeypatch.setattr(batch_processor, "map_canonical_record", boom)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.error_message == "An unexpected error occurred while starting processing."
        assert "secret" not in result.error_message
        assert "RuntimeError" not in result.error_message
        assert "traffic.csv" not in result.error_message


class TestDatabaseFailureHandling:
    def test_a_commit_failure_during_persistence_marks_the_batch_failed_safely(self, db_session, upload_dir, monkeypatch):
        real_commit = db_session.commit
        calls = {"n": 0}

        def commit_fails_only_for_the_persistence_step():
            calls["n"] += 1
            if calls["n"] == 2:  # 1st = claim_for_processing, 2nd = feature persistence
                raise SQLAlchemyError("database is locked")
            return real_commit()

        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)
        monkeypatch.setattr(db_session, "commit", commit_fails_only_for_the_persistence_step)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.FAILED
        assert "database is locked" not in (result.error_message or "")
        assert "SQLAlchemyError" not in (result.error_message or "")


class TestDuplicateReprocessingProtection:
    def test_a_batch_already_processed_cannot_be_reprocessed_through_the_api(self, client, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        first = client.post(url_for(batch.id))
        second = client.post(url_for(batch.id))

        assert first.status_code == 200
        assert second.status_code == 409
        assert len(persisted_rows(db_session, batch.id)) == 2  # not duplicated by the second attempt

    def test_no_filesystem_paths_or_stack_traces_in_the_api_response(self, client, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        response = client.post(url_for(batch.id))

        assert str(upload_dir) not in response.text
        assert "Traceback" not in response.text


class TestNoFalseCompletionRegression:
    def test_a_batch_with_persisted_features_still_stays_processing_not_completed(self, db_session, upload_dir, tmp_path):
        register_ready_nsl_kdd_model(db_session, tmp_path)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        assert result.status != ProcessingStatus.COMPLETED

    def test_processed_and_failed_counters_remain_untouched_by_persistence(self, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session, total_records=2), NSL_KDD_CSV)

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.processed_records == 0
        assert result.failed_records == 0
        assert result.total_records == 2

    def test_no_detection_result_rows_are_created(self, db_session, upload_dir):
        from sqlalchemy import func

        from app.models.detection_result import DetectionResult

        batch = with_uploaded_file(upload_dir, add_batch(db_session), NSL_KDD_CSV)

        start_processing(db_session, batch.id, upload_dir)

        assert db_session.scalar(select(func.count()).select_from(DetectionResult)) == 0
