"""Tests for POST /api/v1/detection/upload.

Every test uses the isolated in-memory database from conftest.py and a
temporary upload directory — never the development database or
backend/data/uploads/.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.v1.detection import get_upload_config
from app.db.database import get_db
from app.main import app
from app.models.detection_batch import DetectionBatch
from app.models.detection_result import DetectionResult
from app.services.csv_validation import sanitize_filename
from app.services.upload_service import UploadConfig

URL = "/api/v1/detection/upload"
SMALL_CSV = b"duration,protocol,bytes\n1,tcp,100\n2,udp,200\n3,tcp,300\n"


@pytest.fixture()
def upload_dir(tmp_path: Path) -> Path:
    return tmp_path / "uploads"


@pytest.fixture()
def max_bytes() -> int:
    return 1024 * 1024


@pytest.fixture()
def client(db_session, upload_dir: Path, max_bytes: int):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_upload_config] = lambda: UploadConfig(directory=upload_dir, max_bytes=max_bytes)
    # Not used as a context manager on purpose: that would run the lifespan,
    # which initialises the real development database.
    yield TestClient(app)
    app.dependency_overrides.clear()


def upload(client: TestClient, content: bytes, filename: str = "traffic.csv", content_type: str = "text/csv"):
    return client.post(URL, files={"file": (filename, content, content_type)})


def batches(db_session) -> list[DetectionBatch]:
    return list(db_session.scalars(select(DetectionBatch)))


def stored_files(upload_dir: Path) -> list[Path]:
    return sorted(upload_dir.glob("*")) if upload_dir.exists() else []


class TestSuccessfulUpload:
    def test_registers_a_batch_and_returns_it(self, client, db_session):
        response = upload(client, SMALL_CSV)

        assert response.status_code == 201
        body = response.json()
        assert body["filename"] == "traffic.csv"
        assert body["total_records"] == 3
        assert uuid.UUID(body["batch_id"])
        assert [str(b.id) for b in batches(db_session)] == [body["batch_id"]]

    def test_counts_every_data_row_of_a_larger_file(self, client):
        content = b"a,b,c\n" + b"".join(f"{i},x,{i * 2}\n".encode() for i in range(1500))

        assert upload(client, content).json()["total_records"] == 1500

    def test_batch_starts_pending_and_unanalyzed(self, client, db_session):
        body = upload(client, SMALL_CSV).json()

        assert body["status"] == "pending"
        assert body["processed_records"] == 0
        assert body["failed_records"] == 0
        batch = batches(db_session)[0]
        assert batch.status.value == "pending"
        assert (batch.total_records, batch.processed_records, batch.failed_records) == (3, 0, 0)
        assert batch.error_message is None
        assert batch.completed_at is None

    def test_creates_no_detection_results(self, client, db_session):
        upload(client, SMALL_CSV)

        assert db_session.scalar(select(func.count()).select_from(DetectionResult)) == 0

    def test_created_at_is_timezone_aware_utc(self, client):
        created_at = upload(client, SMALL_CSV).json()["created_at"]

        assert created_at.endswith("Z") or created_at.endswith("+00:00")

    def test_stores_the_file_under_the_batch_id_only(self, client, upload_dir):
        body = upload(client, SMALL_CSV).json()

        files = stored_files(upload_dir)
        assert [f.name for f in files] == [f"{body['batch_id']}.csv"]
        assert files[0].read_bytes() == SMALL_CSV

    def test_two_uploads_of_the_same_file_create_two_batches(self, client, db_session):
        first = upload(client, SMALL_CSV).json()["batch_id"]
        second = upload(client, SMALL_CSV).json()["batch_id"]

        assert first != second
        assert len(batches(db_session)) == 2

    def test_does_not_require_any_dataset_specific_columns(self, client):
        assert upload(client, b"anything,goes\n1,2\n").status_code == 201

    @pytest.mark.parametrize("content_type", ["application/vnd.ms-excel", "text/plain", "application/octet-stream", ""])
    def test_accepts_the_content_types_browsers_send_for_csv(self, client, content_type):
        assert upload(client, SMALL_CSV, content_type=content_type).status_code == 201

    def test_ignores_blank_lines_and_a_byte_order_mark(self, client):
        content = b"\xef\xbb\xbf\n\na,b\n1,2\n\n3,4\n\n"

        assert upload(client, content).json()["total_records"] == 2

    def test_accepts_quoted_fields_containing_commas_and_newlines(self, client):
        content = b'a,b\n"x, y","line1\nline2"\n3,4\n'

        assert upload(client, content).json()["total_records"] == 2

    def test_accepts_non_utf8_text(self, client):
        content = "label,n\nWeb Attack \u2013 Brute Force,1\n".encode("cp1252")

        assert upload(client, content).status_code == 201

    def test_extension_check_is_case_insensitive(self, client):
        assert upload(client, SMALL_CSV, filename="TRAFFIC.CSV").status_code == 201


class TestRejectedUploads:
    def assert_rejected(self, response, status: int, error: str, db_session, upload_dir: Path):
        assert response.status_code == status
        body = response.json()
        assert body["error"] == error
        assert isinstance(body["message"], str) and body["message"]
        assert set(body) == {"error", "message"}
        assert batches(db_session) == []
        assert stored_files(upload_dir) == []

    def test_missing_file(self, client, db_session, upload_dir):
        self.assert_rejected(client.post(URL), 400, "missing_file", db_session, upload_dir)

    def test_form_without_a_file_field(self, client, db_session, upload_dir):
        self.assert_rejected(client.post(URL, data={"other": "x"}), 400, "missing_file", db_session, upload_dir)

    def test_empty_file(self, client, db_session, upload_dir):
        self.assert_rejected(upload(client, b""), 400, "empty_file", db_session, upload_dir)

    def test_missing_filename(self, client, db_session, upload_dir):
        response = client.post(URL, files={"file": ("", SMALL_CSV, "text/csv")})

        assert response.status_code in (400, 422)
        assert batches(db_session) == []

    @pytest.mark.parametrize("filename", ["traffic.txt", "traffic.xlsx", "traffic", "traffic.csv.exe", ".csv", "csv"])
    def test_non_csv_extension(self, client, db_session, upload_dir, filename):
        self.assert_rejected(upload(client, SMALL_CSV, filename=filename), 415, "unsupported_media_type", db_session, upload_dir)

    @pytest.mark.parametrize("content_type", ["application/pdf", "image/png", "application/zip"])
    def test_clearly_non_csv_content_type(self, client, db_session, upload_dir, content_type):
        self.assert_rejected(upload(client, SMALL_CSV, content_type=content_type), 415, "unsupported_media_type", db_session, upload_dir)

    def test_header_only_csv(self, client, db_session, upload_dir):
        self.assert_rejected(upload(client, b"a,b,c\n"), 400, "no_data_rows", db_session, upload_dir)

    def test_header_followed_only_by_blank_lines(self, client, db_session, upload_dir):
        self.assert_rejected(upload(client, b"a,b,c\n\n  \n,,\n"), 400, "no_data_rows", db_session, upload_dir)

    def test_whitespace_only_file_has_no_header(self, client, db_session, upload_dir):
        self.assert_rejected(upload(client, b"  \n\n"), 400, "invalid_csv", db_session, upload_dir)

    def test_rows_with_a_different_column_count(self, client, db_session, upload_dir):
        self.assert_rejected(upload(client, b"a,b,c\n1,2,3\n4,5\n"), 400, "invalid_csv", db_session, upload_dir)

    def test_unterminated_quote(self, client, db_session, upload_dir):
        self.assert_rejected(upload(client, b'a,b\n"1,2\n3,4\n'), 400, "invalid_csv", db_session, upload_dir)

    def test_stray_quote_inside_a_field(self, client, db_session, upload_dir):
        self.assert_rejected(upload(client, b'a,b\n1,"x"y\n'), 400, "invalid_csv", db_session, upload_dir)

    def test_single_column_text_is_not_tabular(self, client, db_session, upload_dir):
        self.assert_rejected(upload(client, b"just a note\nanother line\n"), 400, "invalid_csv", db_session, upload_dir)

    def test_binary_content_renamed_to_csv(self, client, db_session, upload_dir):
        self.assert_rejected(upload(client, b"PK\x03\x04\x00\x00,\x00\x00,\x00\n1,2\n"), 400, "invalid_csv", db_session, upload_dir)

    @pytest.mark.parametrize("max_bytes", [100])
    def test_oversized_file(self, client, db_session, upload_dir, max_bytes):
        content = b"a,b\n" + b"1,2\n" * 100

        self.assert_rejected(upload(client, content), 413, "file_too_large", db_session, upload_dir)

    @pytest.mark.parametrize("max_bytes", [len(SMALL_CSV)])
    def test_file_exactly_at_the_limit_is_accepted(self, client, max_bytes):
        assert upload(client, SMALL_CSV).status_code == 201

    def test_rejection_does_not_echo_file_content(self, client):
        response = upload(client, b"secret-token,b\n1,2,3\n")

        assert "secret-token" not in response.text


class TestSecurity:
    @pytest.mark.parametrize(
        "filename",
        ["../../etc/passwd.csv", "..\\..\\windows\\system32\\evil.csv", "/abs/path/evil.csv", "C:\\evil.csv", "a/../../b.csv"],
    )
    def test_path_traversal_filenames_cannot_escape_the_upload_directory(self, client, db_session, upload_dir, tmp_path, filename):
        response = upload(client, SMALL_CSV, filename=filename)

        assert response.status_code == 201
        batch_id = response.json()["batch_id"]
        assert [f.name for f in stored_files(upload_dir)] == [f"{batch_id}.csv"]
        # Nothing was written anywhere else under the temp tree.
        written = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
        assert written == {f"uploads/{batch_id}.csv"}
        assert "/" not in batches(db_session)[0].filename and "\\" not in batches(db_session)[0].filename

    def test_stored_name_never_contains_the_client_filename(self, client, upload_dir):
        upload(client, SMALL_CSV, filename="my-secret-capture.csv")

        assert "my-secret-capture" not in " ".join(f.name for f in stored_files(upload_dir))

    def test_response_exposes_no_filesystem_path(self, client, upload_dir):
        response = upload(client, SMALL_CSV)

        text = response.text
        assert str(upload_dir) not in text
        assert upload_dir.name not in text
        assert set(response.json()) == {
            "batch_id",
            "filename",
            "status",
            "total_records",
            "processed_records",
            "failed_records",
            "created_at",
        }

    def test_csv_formula_cells_are_stored_as_inert_text(self, client, upload_dir):
        content = b"a,b\n=cmd|' /C calc'!A0,@SUM(1)\n"

        body = upload(client, content).json()

        assert body["total_records"] == 1
        assert (upload_dir / f"{body['batch_id']}.csv").read_bytes() == content


class TestFailureHandling:
    def test_database_failure_does_not_report_success_or_leave_files(self, client, db_session, upload_dir, monkeypatch):
        def failing_commit():
            raise RuntimeError("database is locked: INSERT INTO detection_batches secret-sql")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        response = upload(client, SMALL_CSV)

        assert response.status_code == 500
        assert response.json() == {"error": "upload_failed", "message": "Unable to register the uploaded file."}
        assert "secret-sql" not in response.text and "INSERT" not in response.text
        assert batches(db_session) == []
        assert stored_files(upload_dir) == []

    def test_storage_failure_does_not_report_success_or_create_a_batch(self, client, db_session, tmp_path, max_bytes):
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("a regular file where the upload directory should be")
        app.dependency_overrides[get_upload_config] = lambda: UploadConfig(directory=blocker / "uploads", max_bytes=max_bytes)

        response = upload(client, SMALL_CSV)

        assert response.status_code == 500
        assert response.json()["error"] == "upload_failed"
        assert str(blocker) not in response.text
        assert batches(db_session) == []

    def test_failed_rename_leaves_no_batch_and_no_part_file(self, client, db_session, upload_dir, monkeypatch):
        def failing_replace(source, destination):
            raise OSError("disk error at C:\\secret\\path")

        monkeypatch.setattr("app.services.upload_service.os.replace", failing_replace)

        response = upload(client, SMALL_CSV)

        assert response.status_code == 500
        assert "secret" not in response.text
        assert batches(db_session) == []
        assert stored_files(upload_dir) == []

    def test_a_rejected_upload_does_not_block_a_later_valid_one(self, client, db_session):
        assert upload(client, b"a,b\n").status_code == 400

        assert upload(client, SMALL_CSV).status_code == 201
        assert len(batches(db_session)) == 1


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("traffic.csv", "traffic.csv"),
            ("../../etc/passwd.csv", "passwd.csv"),
            ("C:\\Users\\me\\flows.csv", "flows.csv"),
            ("we<ir>d:na|me?.csv", "we_ir_d_na_me_.csv"),
            ("tab\tand\nnewline.csv", "tab_and_newline.csv"),
            (None, "upload.csv"),
            ("", "upload.csv"),
            ("///", "upload.csv"),
        ],
    )
    def test_removes_directories_and_unsafe_characters(self, raw, expected):
        assert sanitize_filename(raw) == expected

    def test_caps_length_while_keeping_the_extension(self):
        result = sanitize_filename("a" * 400 + ".csv")

        assert len(result) <= 255
        assert result.endswith(".csv")
