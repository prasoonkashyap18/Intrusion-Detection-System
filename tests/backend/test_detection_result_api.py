"""Tests for the Step 28 DetectionResult read-only API:

    GET /api/v1/detection/batches/{batch_id}/results
    GET /api/v1/detection/results/{result_id}

Rows are inserted straight into the isolated in-memory database; the
development database is never touched, and nothing here runs CSV
ingestion, feature extraction, or model inference.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.db.database import get_db
from app.main import app
from app.models.detection_batch import DetectionBatch
from app.models.detection_result import DetectionResult
from app.models.enums import ProcessingStatus
from app.models.model_metadata import ModelMetadata

BASE_TIME = datetime(2026, 10, 3, 8, 0, 0, tzinfo=timezone.utc)


def batch_results_url(batch_id) -> str:
    return f"/api/v1/detection/batches/{batch_id}/results"


def result_url(result_id) -> str:
    return f"/api/v1/detection/results/{result_id}"


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(
        id=uuid.uuid4(), filename="traffic.csv", status=ProcessingStatus.PROCESSING, total_records=1
    )
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


def add_result(db_session, batch, model, *, row_number, predicted_label=0, attack_probability=None, created_at=None):
    result = DetectionResult(
        id=uuid.uuid4(),
        batch_id=batch.id,
        model_id=model.id,
        row_number=row_number,
        predicted_label=predicted_label,
        prediction_name="attack" if predicted_label == 1 else "benign",
        attack_probability=attack_probability,
        created_at=created_at or BASE_TIME,
    )
    db_session.add(result)
    db_session.commit()
    return result


class TestEmptyResultSet:
    def test_returns_200_not_404_for_a_batch_with_no_results(self, client, db_session):
        batch = add_batch(db_session)

        response = client.get(batch_results_url(batch.id))

        assert response.status_code == 200
        assert response.json() == {
            "items": [],
            "batch_id": str(batch.id),
            "page": 1,
            "page_size": 20,
            "total_items": 0,
            "total_pages": 0,
        }


class TestSuccessfulRetrieval:
    def test_returns_all_results_for_the_batch(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        for i in range(1, 4):
            add_result(db_session, batch, model, row_number=i)

        body = client.get(batch_results_url(batch.id)).json()

        assert body["total_items"] == 3
        assert len(body["items"]) == 3

    def test_response_schema_has_the_documented_fields(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch, model, row_number=1, predicted_label=1, attack_probability=0.87)

        item = client.get(batch_results_url(batch.id)).json()["items"][0]

        assert set(item) == {
            "id",
            "batch_id",
            "model_id",
            "model_name",
            "model_version",
            "row_number",
            "predicted_label",
            "prediction_name",
            "attack_probability",
            "created_at",
        }

    def test_correct_batch_association(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch, model, row_number=1)

        item = client.get(batch_results_url(batch.id)).json()["items"][0]

        assert item["batch_id"] == str(batch.id)

    def test_correct_row_number_values(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch, model, row_number=5)
        add_result(db_session, batch, model, row_number=9)

        row_numbers = [item["row_number"] for item in client.get(batch_results_url(batch.id)).json()["items"]]

        assert row_numbers == [5, 9]

    def test_correct_predicted_label_values(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch, model, row_number=1, predicted_label=0)
        add_result(db_session, batch, model, row_number=2, predicted_label=1)

        labels = {item["row_number"]: item["predicted_label"] for item in client.get(batch_results_url(batch.id)).json()["items"]}

        assert labels == {1: 0, 2: 1}

    def test_correct_prediction_name_values(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch, model, row_number=1, predicted_label=0)
        add_result(db_session, batch, model, row_number=2, predicted_label=1)

        names = {item["row_number"]: item["prediction_name"] for item in client.get(batch_results_url(batch.id)).json()["items"]}

        assert names == {1: "benign", 2: "attack"}

    def test_correct_attack_probability_values(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch, model, row_number=1, predicted_label=1, attack_probability=0.733)

        item = client.get(batch_results_url(batch.id)).json()["items"][0]

        assert item["attack_probability"] == pytest.approx(0.733)

    def test_null_attack_probability_is_returned_as_json_null(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch, model, row_number=1, predicted_label=0, attack_probability=None)

        item = client.get(batch_results_url(batch.id)).json()["items"][0]

        assert item["attack_probability"] is None

    def test_model_identity_is_included_via_the_relationship(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session, model_name="ai-ids-baseline", model_version="20260101T000000Z")
        add_result(db_session, batch, model, row_number=1)

        item = client.get(batch_results_url(batch.id)).json()["items"][0]

        assert item["model_id"] == str(model.id)
        assert item["model_name"] == "ai-ids-baseline"
        assert item["model_version"] == "20260101T000000Z"


class TestDeterministicOrdering:
    def test_results_are_ordered_by_row_number_ascending(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        for i in [3, 1, 2]:
            add_result(db_session, batch, model, row_number=i)

        row_numbers = [item["row_number"] for item in client.get(batch_results_url(batch.id)).json()["items"]]

        assert row_numbers == [1, 2, 3]

    def test_ordering_is_stable_across_repeated_requests(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        for i in range(1, 6):
            add_result(db_session, batch, model, row_number=i)

        first = [item["id"] for item in client.get(batch_results_url(batch.id)).json()["items"]]
        second = [item["id"] for item in client.get(batch_results_url(batch.id)).json()["items"]]

        assert first == second


class TestPagination:
    def test_default_page_and_size(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        for i in range(1, 26):
            add_result(db_session, batch, model, row_number=i)

        body = client.get(batch_results_url(batch.id)).json()

        assert (body["page"], body["page_size"], body["total_items"], body["total_pages"]) == (1, 20, 25, 2)
        assert len(body["items"]) == 20

    def test_page_two_continues_without_overlap(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        for i in range(1, 6):
            add_result(db_session, batch, model, row_number=i)

        page_one = [i["row_number"] for i in client.get(batch_results_url(batch.id), params={"page": 1, "page_size": 2}).json()["items"]]
        page_two = [i["row_number"] for i in client.get(batch_results_url(batch.id), params={"page": 2, "page_size": 2}).json()["items"]]

        assert page_one == [1, 2]
        assert page_two == [3, 4]
        assert not set(page_one) & set(page_two)

    def test_last_page_holds_the_remainder(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        for i in range(1, 6):
            add_result(db_session, batch, model, row_number=i)

        body = client.get(batch_results_url(batch.id), params={"page": 3, "page_size": 2}).json()

        assert [i["row_number"] for i in body["items"]] == [5]

    def test_page_beyond_the_end_is_an_empty_200_with_real_totals(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch, model, row_number=1)

        response = client.get(batch_results_url(batch.id), params={"page": 9, "page_size": 2})

        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["total_items"] == 1

    def test_maximum_page_size_is_accepted(self, client, db_session):
        batch = add_batch(db_session)
        assert client.get(batch_results_url(batch.id), params={"page_size": 100}).status_code == 200

    @pytest.mark.parametrize("page_size", [101, 1000, 10_000_000])
    def test_page_size_above_the_maximum_is_rejected(self, client, db_session, page_size):
        batch = add_batch(db_session)
        assert client.get(batch_results_url(batch.id), params={"page_size": page_size}).status_code == 422

    @pytest.mark.parametrize("page", [0, -1, "abc", "1.5", 1_000_001])
    def test_invalid_page_is_rejected(self, client, db_session, page):
        batch = add_batch(db_session)
        response = client.get(batch_results_url(batch.id), params={"page": page})

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_request"

    @pytest.mark.parametrize("page_size", [0, -5, "abc", ""])
    def test_invalid_page_size_is_rejected(self, client, db_session, page_size):
        batch = add_batch(db_session)
        assert client.get(batch_results_url(batch.id), params={"page_size": page_size}).status_code == 422


class TestNotFound:
    def test_nonexistent_batch_returns_404(self, client):
        response = client.get(batch_results_url(uuid.uuid4()))

        assert response.status_code == 404
        assert response.json()["error"] == "batch_not_found"

    def test_malformed_batch_id_returns_422(self, client):
        response = client.get("/api/v1/detection/batches/not-a-uuid/results")

        assert response.status_code == 422

    def test_nonexistent_detection_result_returns_404(self, client):
        response = client.get(result_url(uuid.uuid4()))

        assert response.status_code == 404
        assert response.json()["error"] == "detection_result_not_found"

    def test_malformed_result_id_returns_422(self, client):
        response = client.get("/api/v1/detection/results/not-a-uuid")

        assert response.status_code == 422


class TestSingleResultRetrieval:
    def test_returns_the_matching_result(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        result = add_result(db_session, batch, model, row_number=7, predicted_label=1, attack_probability=0.5)

        body = client.get(result_url(result.id)).json()

        assert body["id"] == str(result.id)
        assert body["row_number"] == 7
        assert body["prediction_name"] == "attack"
        assert body["attack_probability"] == pytest.approx(0.5)


class TestBatchIsolation:
    def test_results_from_another_batch_never_appear(self, client, db_session):
        batch_a = add_batch(db_session)
        batch_b = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch_a, model, row_number=1)
        add_result(db_session, batch_a, model, row_number=2)
        add_result(db_session, batch_b, model, row_number=1)

        body_a = client.get(batch_results_url(batch_a.id)).json()
        body_b = client.get(batch_results_url(batch_b.id)).json()

        assert body_a["total_items"] == 2
        assert body_b["total_items"] == 1
        assert all(item["batch_id"] == str(batch_a.id) for item in body_a["items"])
        assert all(item["batch_id"] == str(batch_b.id) for item in body_b["items"])

    def test_an_arbitrary_valid_uuid_cannot_retrieve_another_batchs_results(self, client, db_session):
        real_batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, real_batch, model, row_number=1)

        unrelated_id = uuid.uuid4()
        while unrelated_id == real_batch.id:
            unrelated_id = uuid.uuid4()

        response = client.get(batch_results_url(unrelated_id))

        assert response.status_code == 404


class TestNoSideEffects:
    def test_does_not_run_model_inference(self, client, db_session, monkeypatch):
        import app.services.detection_result_service as service_module

        assert not hasattr(service_module, "predict_with_model")
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch, model, row_number=1)

        client.get(batch_results_url(batch.id))  # must not raise or touch inference

    def test_does_not_read_csv_or_run_feature_extraction(self, client, db_session):
        import app.services.detection_result_service as service_module

        assert not hasattr(service_module, "ingest_batch")
        assert not hasattr(service_module, "extract_features")
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch, model, row_number=1)

        assert client.get(batch_results_url(batch.id)).status_code == 200

    def test_retrieval_does_not_modify_the_result_row(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        result = add_result(db_session, batch, model, row_number=1, predicted_label=1, attack_probability=0.6)

        client.get(batch_results_url(batch.id))
        client.get(result_url(result.id))

        db_session.refresh(result)
        assert result.predicted_label == 1
        assert result.attack_probability == pytest.approx(0.6)
        assert result.row_number == 1

    def test_retrieval_creates_no_new_rows(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch, model, row_number=1)
        count_before = len(db_session.scalars(select(DetectionResult)).all())

        client.get(batch_results_url(batch.id))
        client.get(result_url(uuid.uuid4()))  # a 404 lookup, still read-only

        count_after = len(db_session.scalars(select(DetectionResult)).all())
        assert count_after == count_before


class TestSecurityAndFailures:
    def test_response_exposes_only_the_documented_fields(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch, model, row_number=1)

        response = client.get(batch_results_url(batch.id))

        assert "path" not in response.text.lower()
        assert ".joblib" not in response.text
        assert "features" not in response.json()["items"][0]

    def test_no_raw_feature_vector_or_csv_content_in_response(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        add_result(db_session, batch, model, row_number=1)

        response = client.get(batch_results_url(batch.id))

        assert "feature_matrix" not in response.text
        assert "raw_value" not in response.text

    def test_sql_injection_style_batch_id_is_rejected_as_invalid_uuid(self, client):
        response = client.get("/api/v1/detection/batches/' OR '1'='1/results")

        assert response.status_code == 422

    def test_database_error_on_list_returns_a_clean_500(self, client, db_session, monkeypatch):
        batch = add_batch(db_session)

        def failing_scalar(*args, **kwargs):
            raise OperationalError(
                "SELECT secret_column FROM detection_results", {}, Exception("disk I/O error at C:\\secret")
            )

        monkeypatch.setattr(db_session, "scalar", failing_scalar)

        response = client.get(batch_results_url(batch.id))

        assert response.status_code == 500
        assert response.json() == {
            "error": "detection_results_unavailable",
            "message": "Unable to load detection results for this batch.",
        }
        assert "secret" not in response.text
        assert "SELECT" not in response.text

    def test_database_error_on_single_result_returns_a_clean_500(self, client, db_session, monkeypatch):
        def failing_scalars(*args, **kwargs):
            raise OperationalError("SELECT * FROM detection_results", {}, Exception("disk I/O error at C:\\secret"))

        monkeypatch.setattr(db_session, "scalars", failing_scalars)

        response = client.get(result_url(uuid.uuid4()))

        assert response.status_code == 500
        assert "secret" not in response.text
        assert "SELECT" not in response.text

    def test_list_runs_a_bounded_number_of_queries_regardless_of_page_size(self, client, db_session):
        batch = add_batch(db_session)
        model = add_model(db_session)
        for i in range(1, 11):
            add_result(db_session, batch, model, row_number=i)

        statements: list[str] = []
        engine = db_session.get_bind()

        def record(conn, cursor, statement, *args):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            client.get(batch_results_url(batch.id))
        finally:
            event.remove(engine, "before_cursor_execute", record)

        selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
        # get_batch (1) + COUNT (1) + page (1) + selectinload model batch (1) = 4,
        # regardless of how many result rows are on the page.
        assert len(selects) <= 4


class TestExistingEndpointsUnaffected:
    def test_batch_listing_still_works(self, client, db_session):
        add_batch(db_session)
        assert client.get("/api/v1/detection/batches").status_code == 200

    def test_batch_detail_still_works(self, client, db_session):
        batch = add_batch(db_session)
        assert client.get(f"/api/v1/detection/batches/{batch.id}").status_code == 200

    def test_health_endpoint_still_works(self, client):
        assert client.get("/api/v1/health").status_code == 200
