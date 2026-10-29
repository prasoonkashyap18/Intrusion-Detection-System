"""Alert endpoints: listing, detail, status history, lifecycle status
updates (Steps 36/37), and a real-time notification stream (Step 44).

No alert is ever created or deleted through this router — persistence
happens exclusively during batch/live-flow processing (`app.services.
alert_generation`, wired into `app.services.batch_processor`/`app.
services.realtime_detection`); this router only reads existing rows
and, for the one `PATCH` endpoint, delegates the actual status change
to `app.services.alert_lifecycle.transition_alert`, the single
centralized place that may write `Alert.status`.

`GET /alerts/stream` (Server-Sent Events) is best-effort, in-process,
and never the source of truth — see `app.services.
alert_notifications`'s module docstring. A client recovers anything it
missed (a disconnect, a server restart, or simply having not been
connected yet) with `GET /alerts?created_after=<timestamp>`, never by
asking the stream to replay anything.
"""

from __future__ import annotations

import asyncio
import json
import queue
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.db.database import get_db
from app.models.alert import Alert
from app.models.alert_status_history import AlertStatusHistory
from app.models.enums import AlertStatus as ModelAlertStatus
from app.models.enums import Severity as ModelSeverity
from app.schemas.alert import (
    AlertListResponse,
    AlertResponse,
    AlertStatusHistoryEntry,
    AlertStatusHistoryResponse,
    AlertStatusUpdateRequest,
)
from app.schemas.common import ErrorResponse
from app.schemas.enums import AlertStatus, Severity
from app.services.alert_lifecycle import InvalidAlertTransitionError, list_status_history, transition_alert
from app.services.alert_notifications import broker
from app.services.alert_query_service import get_alert_or_404, list_alerts

router = APIRouter(prefix="/alerts")

_SSE_HEARTBEAT_SECONDS = 15.0
_SSE_POLL_INTERVAL_SECONDS = 0.5

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


def _to_response(alert: Alert) -> AlertResponse:
    return AlertResponse.model_validate(alert)


def _to_history_response(alert_id: uuid.UUID, rows: list[AlertStatusHistory]) -> AlertStatusHistoryResponse:
    return AlertStatusHistoryResponse(
        alert_id=alert_id, items=[AlertStatusHistoryEntry.model_validate(row) for row in rows]
    )


@router.get(
    "",
    response_model=AlertListResponse,
    summary="List persisted alerts, newest first by default",
    description=(
        "Returns one page of persisted Alert rows. An empty collection is a normal 200 with "
        "`items: []`. Alerts are generated during batch/live-flow processing using explicit, "
        "configurable rules over the hybrid/risk/explanation pipeline — not every processed row "
        "has one. `created_after` (Step 44) is the real-time-stream recovery filter: after "
        "reconnecting to `GET /alerts/stream`, pass the `created_at` of the last alert you saw "
        "to retrieve anything persisted while you were disconnected."
    ),
    responses={
        422: {"model": ErrorResponse, "description": "Invalid page, page_size, or sort_by"},
        500: {"model": ErrorResponse, "description": "Alerts could not be loaded"},
    },
)
def get_alerts(
    page: int = Query(default=1, ge=1, le=1_000_000, description="1-based page number."),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Alerts per page."),
    status: AlertStatus | None = Query(default=None, description="Filter by lifecycle status."),
    severity: Severity | None = Query(default=None, description="Filter by severity."),
    batch_id: uuid.UUID | None = Query(default=None, description="Filter by originating batch."),
    created_after: datetime | None = Query(
        default=None, description="Only alerts created strictly after this UTC timestamp."
    ),
    sort_by: str = Query(default="created_at", description="One of: created_at, updated_at, severity."),
    descending: bool = Query(default=True, description="Sort order; true (default) is newest/highest first."),
    db: Session = Depends(get_db),
) -> AlertListResponse:
    result = list_alerts(
        db,
        page=page,
        page_size=page_size,
        status=ModelAlertStatus(status.value) if status is not None else None,
        severity=ModelSeverity(severity.value) if severity is not None else None,
        batch_id=batch_id,
        created_after=created_after,
        sort_by=sort_by,
        descending=descending,
    )
    return AlertListResponse(
        items=[_to_response(alert) for alert in result.items],
        page=page,
        page_size=page_size,
        total_items=result.total_items,
        total_pages=result.total_pages,
    )


@router.get(
    "/stream",
    summary="Real-time Server-Sent Events stream of newly persisted alerts",
    description=(
        "Opens a Server-Sent Events stream: one `data:` event per alert as soon as it is "
        "persisted, for as long as the client stays connected. Best-effort only — this is not "
        "the source of truth and never the only way to learn about an alert; a disconnected or "
        "newly connecting client always recovers via `GET /alerts?created_after=...`. Sends a "
        "`: heartbeat` comment periodically to keep the connection alive and let a client detect "
        "a stalled connection. This endpoint implements no authentication or authorization beyond "
        "whatever the rest of this API already has (none, at this step) — it does not invent a "
        "guarantee this application does not otherwise provide."
    ),
)
async def stream_alerts(request: Request) -> StreamingResponse:
    subscriber_id, inbox = broker.subscribe()

    async def _event_source():
        last_heartbeat = asyncio.get_event_loop().time()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    notification = await asyncio.get_event_loop().run_in_executor(
                        None, inbox.get, True, _SSE_POLL_INTERVAL_SECONDS
                    )
                except queue.Empty:
                    now = asyncio.get_event_loop().time()
                    if now - last_heartbeat >= _SSE_HEARTBEAT_SECONDS:
                        last_heartbeat = now
                        yield ": heartbeat\n\n"
                    continue
                payload = {
                    "alert_id": str(notification.alert_id),
                    "batch_id": str(notification.batch_id),
                    "severity": notification.severity,
                    "status": notification.status,
                    "title": notification.title,
                    "created_at": notification.created_at.isoformat(),
                }
                yield f"data: {json.dumps(payload)}\n\n"
        finally:
            broker.unsubscribe(subscriber_id)

    return StreamingResponse(
        _event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/{alert_id}",
    response_model=AlertResponse,
    summary="Retrieve one persisted alert by id",
    description="Returns the alert exactly as stored, including its evidence snapshot and current status.",
    responses={
        404: {"model": ErrorResponse, "description": "No alert exists with that ID"},
        422: {"model": ErrorResponse, "description": "alert_id is not a valid UUID"},
        500: {"model": ErrorResponse, "description": "The alert could not be loaded"},
    },
)
def get_alert_detail(alert_id: uuid.UUID, db: Session = Depends(get_db)) -> AlertResponse:
    return _to_response(get_alert_or_404(db, alert_id))


@router.get(
    "/{alert_id}/history",
    response_model=AlertStatusHistoryResponse,
    summary="Retrieve one alert's full status-change audit trail",
    description="Returns every recorded status transition for this alert, ordered oldest first.",
    responses={
        404: {"model": ErrorResponse, "description": "No alert exists with that ID"},
        422: {"model": ErrorResponse, "description": "alert_id is not a valid UUID"},
        500: {"model": ErrorResponse, "description": "The history could not be loaded"},
    },
)
def get_alert_history(alert_id: uuid.UUID, db: Session = Depends(get_db)) -> AlertStatusHistoryResponse:
    rows = list_status_history(db, alert_id)
    return _to_history_response(alert_id, rows)


@router.patch(
    "/{alert_id}/status",
    response_model=AlertResponse,
    summary="Transition an alert's lifecycle status",
    description=(
        "Applies one validated status transition (see backend/README.md's \"Alert Lifecycle\" "
        "section for the full transition table) and records it in the alert's audit history. "
        "A request whose `status` equals the alert's current status is a no-op and returns 200 "
        "unchanged. An unsupported transition (including any transition out of `resolved` or "
        "`false_positive`) is rejected as `409`."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "No alert exists with that ID"},
        409: {"model": ErrorResponse, "description": "The requested status transition is not valid"},
        422: {"model": ErrorResponse, "description": "alert_id is not a valid UUID, or the request body is invalid"},
        500: {"model": ErrorResponse, "description": "The status could not be updated"},
    },
)
def update_alert_status(
    alert_id: uuid.UUID,
    body: AlertStatusUpdateRequest,
    db: Session = Depends(get_db),
) -> AlertResponse:
    try:
        alert = transition_alert(db, alert_id, ModelAlertStatus(body.status.value), note=body.note)
    except InvalidAlertTransitionError as error:
        raise ApiException(409, "invalid_alert_transition", error.message) from None
    return _to_response(alert)
