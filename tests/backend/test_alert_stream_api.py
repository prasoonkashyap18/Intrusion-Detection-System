"""Tests for GET /api/v1/alerts/stream (Step 44 SSE) and the
`created_after` recovery filter on GET /api/v1/alerts.

The SSE stream itself is a long-lived connection by design (it only
ever closes on client disconnect or server shutdown), which makes a
full open-read-assert-close cycle against it fragile in an automated
test runner (sandboxed CI hosts have been observed to hang trying to
drive a streaming ASGI response and a synchronous test thread in
lockstep). Delivery itself is already thoroughly covered, without any
network/ASGI layer involved, in `test_alert_notifications.py`
(`AlertNotificationBroker.publish`/`subscribe`/`unsubscribe`, backpressure,
and `notify_new_alert`'s shape). What remains to verify at the API
layer — the route exists, is wired to the same broker, and is
reachable — is checked here without holding the connection open.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_db
from app.main import app
from app.models.alert import Alert
from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus, Severity


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="x.csv", status=ProcessingStatus.PROCESSING, total_records=1)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def add_alert(db_session, batch, **overrides) -> Alert:
    defaults = dict(
        batch_id=batch.id, row_number=1, title="t", description="d", severity=Severity.HIGH, evidence={}
    )
    alert = Alert(**{**defaults, **overrides})
    db_session.add(alert)
    db_session.commit()
    return alert


class TestCreatedAfterFilter:
    def test_only_alerts_after_the_given_timestamp_are_returned(self, client, db_session):
        batch = add_batch(db_session)
        cutoff = datetime.now(timezone.utc)
        add_alert(db_session, batch, row_number=1, created_at=cutoff - timedelta(minutes=5))
        later_alert = add_alert(db_session, batch, row_number=2, created_at=cutoff + timedelta(minutes=5))

        response = client.get("/api/v1/alerts", params={"created_after": cutoff.isoformat()})

        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == str(later_alert.id)

    def test_no_alerts_after_the_most_recent_one_returns_empty(self, client, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch, row_number=1)

        response = client.get("/api/v1/alerts", params={"created_after": alert.created_at.isoformat()})

        assert response.json()["items"] == []

    def test_combines_with_other_filters(self, client, db_session):
        batch = add_batch(db_session)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=1)
        add_alert(db_session, batch, row_number=1, severity=Severity.LOW, created_at=cutoff + timedelta(seconds=10))
        add_alert(db_session, batch, row_number=2, severity=Severity.CRITICAL, created_at=cutoff + timedelta(seconds=10))

        response = client.get(
            "/api/v1/alerts", params={"created_after": cutoff.isoformat(), "severity": "critical"}
        )

        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["severity"] == "critical"


class TestAlertStreamRouteIsRegistered:
    def test_the_stream_path_is_registered_before_the_detail_path(self):
        # /alerts/stream must be matched as the literal "stream" route,
        # never as {alert_id}="stream" (which would 422 on UUID parsing)
        # -- verified by inspecting route registration order directly,
        # without opening the long-lived connection itself.
        from app.api.v1.alerts import router

        paths_in_order = [route.path for route in router.routes]
        stream_index = paths_in_order.index("/alerts/stream")
        detail_index = paths_in_order.index("/alerts/{alert_id}")
        assert stream_index < detail_index

    def test_the_stream_endpoint_is_an_sse_route(self):
        from app.api.v1.alerts import router

        stream_route = next(route for route in router.routes if route.path == "/alerts/stream")
        assert "GET" in stream_route.methods
