"""Tests for Step 17: the processing boundary now runs ingestion AND feature
extraction (app.services.batch_processor._run_feature_extraction), not just
ingestion. No test here asserts that detection happened — this step only
prepares data; nothing is classified, scored or persisted as a result.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1.detection import get_upload_config
from app.db.database import get_db
from app.main import app
from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus
from app.services import batch_processor
from app.services.batch_processor import ProcessingSummary, _run_feature_extraction, start_processing
from app.services.feature_extraction import FeatureStatus
from app.services.upload_service import UploadConfig


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


class TestPipelineInvocation:
    def test_processing_invokes_ingestion(self, db_session, upload_dir, monkeypatch, tmp_path):
        calls = []
        real_ingest = batch_processor.ingest_batch

        def spy_ingest(path):
            calls.append(path)
            yield from real_ingest(path)

        monkeypatch.setattr(batch_processor, "ingest_batch", spy_ingest)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), b"a,b\n1,2\n")

        start_processing(db_session, batch.id, upload_dir)

        assert calls == [upload_dir / f"{batch.id}.csv"]

    def test_processing_invokes_feature_extraction(self, db_session, upload_dir, monkeypatch):
        calls = []
        real_extract = batch_processor.extract_features

        def spy_extract(record):
            calls.append(record)
            return real_extract(record)

        monkeypatch.setattr(batch_processor, "extract_features", spy_extract)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), b"a,b\n1,2\n3,4\n")

        start_processing(db_session, batch.id, upload_dir)

        assert len(calls) == 2
        assert all(hasattr(record, "raw_features") for record in calls)

    def test_valid_records_reach_normalization(self, db_session, upload_dir, monkeypatch):
        results = []
        real_extract = batch_processor.extract_features

        def spy_extract(record):
            normalized = real_extract(record)
            results.append(normalized)
            return normalized

        monkeypatch.setattr(batch_processor, "extract_features", spy_extract)
        batch = with_uploaded_file(
            upload_dir,
            add_batch(db_session),
            b"source_ip,destination_ip,source_port\n10.0.0.1,10.0.0.2,80\n",
        )

        start_processing(db_session, batch.id, upload_dir)

        assert len(results) == 1
        assert results[0].features["source_port"] == 80.0
        assert results[0].source_ip == "10.0.0.1"


class TestRecordLevelHandling:
    def test_missing_optional_values_remain_valid_and_batch_keeps_processing(self, db_session, upload_dir):
        # No recognized generic columns at all — every feature is MISSING,
        # which is a normal outcome, not a failure.
        batch = with_uploaded_file(upload_dir, add_batch(db_session), b"custom_a,custom_b\n1,2\n")

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING

    def test_malformed_values_retain_step16_semantics(self, db_session, upload_dir, monkeypatch):
        captured = []
        real_extract = batch_processor.extract_features

        def spy_extract(record):
            normalized = real_extract(record)
            captured.append(normalized)
            return normalized

        monkeypatch.setattr(batch_processor, "extract_features", spy_extract)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), b"bytes,label\nnot-a-number,normal\n")

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING  # malformed field != processing failure
        assert captured[0].feature_status["byte_count"] == FeatureStatus.MALFORMED
        assert captured[0].features["byte_count"] is None

    def test_unsupported_values_retain_step16_semantics(self, db_session, upload_dir, monkeypatch):
        captured = []
        real_extract = batch_processor.extract_features

        def spy_extract(record):
            normalized = real_extract(record)
            captured.append(normalized)
            return normalized

        monkeypatch.setattr(batch_processor, "extract_features", spy_extract)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), b"protocol,label\nQUIC,normal\n")

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING  # unsupported != attack, != failure
        assert captured[0].feature_status["protocol_number"] == FeatureStatus.UNSUPPORTED
        assert captured[0].protocol_name == "quic"  # text preserved, not discarded

    def test_a_mix_of_valid_missing_and_malformed_fields_in_one_row_all_coexist(self, db_session, upload_dir, monkeypatch):
        captured = []
        real_extract = batch_processor.extract_features

        def spy_extract(record):
            normalized = real_extract(record)
            captured.append(normalized)
            return normalized

        monkeypatch.setattr(batch_processor, "extract_features", spy_extract)
        batch = with_uploaded_file(
            upload_dir,
            add_batch(db_session),
            b"source_port,bytes,custom\n80,oops,xyz\n",
        )

        start_processing(db_session, batch.id, upload_dir)

        normalized = captured[0]
        assert normalized.feature_status["source_port"] == FeatureStatus.PRESENT
        assert normalized.feature_status["byte_count"] == FeatureStatus.MALFORMED
        assert normalized.feature_status["flow_duration"] == FeatureStatus.MISSING
        assert normalized.unknown_features == {"custom": "xyz"}


class TestBatchLevelFailurePolicy:
    def test_structural_ingestion_failure_still_fails_the_batch(self, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session), b"a,b\n1,2\n3,4,5\n")

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.FAILED

    def test_missing_upload_file_still_fails_safely(self, db_session, upload_dir):
        batch = add_batch(db_session)  # no file written

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.FAILED
        assert result.error_message is not None
        assert str(upload_dir) not in result.error_message

    def test_unexpected_extraction_error_fails_safely_without_leaking_details(self, db_session, upload_dir, monkeypatch):
        def boom(_record):
            raise RuntimeError("disk I/O error at C:\\secret\\path\\traffic.csv")

        monkeypatch.setattr(batch_processor, "extract_features", boom)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), b"a,b\n1,2\n")

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.FAILED
        assert result.error_message is not None
        assert "secret" not in result.error_message
        assert "RuntimeError" not in result.error_message

    def test_a_single_malformed_or_unsupported_field_never_fails_the_batch(self, db_session, upload_dir):
        batch = with_uploaded_file(
            upload_dir,
            add_batch(db_session),
            b"source_port,protocol,bytes\nnot-a-port,QUIC,-5\n",
        )

        result = start_processing(db_session, batch.id, upload_dir)

        # Three independently bad field values in one row — still not a
        # processing failure, because none of them is structural.
        assert result.status == ProcessingStatus.PROCESSING


class TestNoFalseCompletionOrFakeData:
    def test_successful_feature_extraction_does_not_mark_the_batch_completed(self, db_session, upload_dir):
        batch = with_uploaded_file(
            upload_dir,
            add_batch(db_session),
            b"source_ip,destination_ip,source_port,destination_port,protocol,bytes,packets\n"
            b"10.0.0.1,10.0.0.2,80,443,tcp,1024,10\n",
        )

        result = start_processing(db_session, batch.id, upload_dir)

        assert result.status == ProcessingStatus.PROCESSING
        assert result.status != ProcessingStatus.COMPLETED

    def test_no_detection_result_rows_are_created(self, db_session, upload_dir):
        from sqlalchemy import func, select

        from app.models.detection_result import DetectionResult

        batch = with_uploaded_file(
            upload_dir, add_batch(db_session), b"source_ip,bytes\n10.0.0.1,100\n10.0.0.2,200\n"
        )

        start_processing(db_session, batch.id, upload_dir)

        assert db_session.scalar(select(func.count()).select_from(DetectionResult)) == 0

    def test_processed_and_failed_counters_are_not_misleading(self, db_session, upload_dir):
        batch = with_uploaded_file(
            upload_dir,
            add_batch(db_session, total_records=3),
            b"bytes\n100\n200\nnot-a-number\n",  # 2 valid, 1 malformed field value
        )

        result = start_processing(db_session, batch.id, upload_dir)

        # Neither counter is touched by feature extraction: they mean
        # "detection outcomes", and no detection has happened.
        assert result.processed_records == 0
        assert result.failed_records == 0
        assert result.total_records == 3  # set at upload time, left untouched

    def test_api_response_never_carries_feature_vectors_or_raw_feature_dicts(self, client, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session), b"source_ip,bytes\n10.0.0.1,100\n")

        response = client.post(url_for(batch.id))

        assert set(response.json()) == {"batch_id", "status", "message"}
        assert "features" not in response.text
        assert "raw_features" not in response.text
        assert "10.0.0.1" not in response.text


class TestStreamingAndDuplicateCalls:
    def test_feature_extraction_is_not_called_twice_per_record(self, db_session, upload_dir, monkeypatch):
        calls = []
        real_extract = batch_processor.extract_features

        def counting_extract(record):
            calls.append(record.row_number)
            return real_extract(record)

        monkeypatch.setattr(batch_processor, "extract_features", counting_extract)
        batch = with_uploaded_file(upload_dir, add_batch(db_session), b"a,b\n1,2\n3,4\n5,6\n")

        start_processing(db_session, batch.id, upload_dir)

        assert calls == [1, 2, 3]  # each row extracted exactly once, in order

    def test_rows_are_ingested_and_extracted_interleaved_not_materialized_as_a_list_first(
        self, db_session, tmp_path, monkeypatch
    ):
        events: list[str] = []

        def fake_ingest_batch(_path):
            for i in range(3):
                events.append(f"yield-{i}")
                yield _FakeRecord(i)

        def fake_extract_features(record):
            events.append(f"extract-{record.row_number}")
            return None

        monkeypatch.setattr(batch_processor, "read_header", lambda _path: ["unrecognized_column"])
        monkeypatch.setattr(batch_processor, "ingest_batch", fake_ingest_batch)
        monkeypatch.setattr(batch_processor, "extract_features", fake_extract_features)

        _run_feature_extraction(db_session, uuid.uuid4(), tmp_path / "irrelevant.csv")

        # Interleaved (yield, extract, yield, extract, ...), proving each
        # record is consumed before the next is produced — not
        # "yield every row, then extract every row".
        assert events == ["yield-0", "extract-0", "yield-1", "extract-1", "yield-2", "extract-2"]

    def test_returns_a_processing_summary_with_consistent_counts(self, db_session, tmp_path, monkeypatch):
        monkeypatch.setattr(batch_processor, "read_header", lambda _path: ["unrecognized_column"])
        monkeypatch.setattr(batch_processor, "ingest_batch", lambda _path: iter([_FakeRecord(0), _FakeRecord(1)]))
        monkeypatch.setattr(batch_processor, "extract_features", lambda record: None)

        summary = _run_feature_extraction(db_session, uuid.uuid4(), tmp_path / "irrelevant.csv")

        assert summary == ProcessingSummary(
            records_ingested=2,
            records_feature_extracted=2,
            records_persisted=0,
            feature_schema_version=summary.feature_schema_version,
            dataset_schema=None,
        )


class _FakeRecord:
    def __init__(self, row_number: int) -> None:
        self.row_number = row_number
        self.raw_features: dict[str, str] = {}


class TestConcurrencyStillWorks:
    def test_claim_for_processing_still_guards_against_a_double_claim(self, db_session):
        from app.services.batch_processor import claim_for_processing

        batch = add_batch(db_session)

        first = claim_for_processing(db_session, batch.id)
        second = claim_for_processing(db_session, batch.id)

        assert (first, second) == (True, False)

    def test_a_batch_already_processing_cannot_be_started_again_through_the_api(self, client, db_session, upload_dir):
        batch = with_uploaded_file(upload_dir, add_batch(db_session, status=ProcessingStatus.PROCESSING), b"a,b\n1,2\n")

        response = client.post(url_for(batch.id))

        assert response.status_code == 409
