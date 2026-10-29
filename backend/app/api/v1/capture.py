"""Live packet-capture endpoints (Step 42): start/stop/status/list for
capture sessions, plus interface discovery. Delegates every real
decision to `app.services.live_capture` — this router only translates
HTTP requests into calls on that module and its results into the
response schemas below.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import get_current_user, require_role
from app.db.database import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.capture import (
    CaptureSessionListResponse,
    CaptureSessionResponse,
    InterfaceListResponse,
    StartCaptureRequest,
)
from app.schemas.common import ErrorResponse
from app.services import live_capture
from app.services.flow_construction import DEFAULT_IDLE_TIMEOUT_SECONDS
from app.services.live_capture import DEFAULT_MAX_QUEUE_SIZE
from app.services.realtime_detection import process_finalized_flow

router = APIRouter(prefix="/capture")


def _to_response(session) -> CaptureSessionResponse:
    return CaptureSessionResponse.model_validate(session)


@router.get(
    "/interfaces",
    response_model=InterfaceListResponse,
    summary="List network interfaces available for capture",
    description=(
        "Returns the real interfaces the capture backend can see on this host. Raises 503 if the "
        "packet-capture dependency or backend (Npcap/WinPcap/libpcap) is not available here — "
        "never a fabricated or cached list."
    ),
    responses={503: {"model": ErrorResponse, "description": "The capture backend is unavailable on this host"}},
)
def get_capture_interfaces(_current_user: User = Depends(get_current_user)) -> InterfaceListResponse:
    return InterfaceListResponse(interfaces=live_capture.list_interfaces())


@router.post(
    "/sessions",
    response_model=CaptureSessionResponse,
    status_code=201,
    summary="Start a live capture session on an explicit interface",
    description=(
        "Starts packet acquisition and flow construction on `interface` — always required "
        "explicitly; there is no capture-everything default. Returns 409 if a session is already "
        "running on that interface, or 503/400 if the capture cannot start (missing privileges, "
        "missing backend, or an invalid interface)."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Missing interface"},
        409: {"model": ErrorResponse, "description": "A session is already running on this interface"},
        503: {"model": ErrorResponse, "description": "The capture backend is unavailable on this host"},
    },
)
def start_capture_session(
    body: StartCaptureRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CaptureSessionResponse:
    require_role(current_user, UserRole.ADMINISTRATOR, UserRole.SECURITY_ANALYST)
    # The capture/worker threads open their own sessions (SQLAlchemy
    # sessions are never shared across threads); binding this factory
    # to the *request's own* engine — rather than defaulting to the
    # globally configured one — is what keeps a test's isolated
    # database (injected via the get_db dependency override) the one
    # those background threads actually write to as well.
    session_factory = sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)

    def _flow_processor(batch_id, flow, row_number) -> None:
        # Step 43: every finalized live flow goes through the exact
        # same inference/hybrid/risk/explanation/alert pipeline a
        # batch's rows do — see app.services.realtime_detection.
        process_finalized_flow(batch_id, flow, row_number, session_factory=session_factory)

    session = live_capture.start_capture(
        db,
        interface=body.interface,
        bpf_filter=body.bpf_filter,
        idle_timeout_seconds=body.idle_timeout_seconds or DEFAULT_IDLE_TIMEOUT_SECONDS,
        max_queue_size=body.max_queue_size or DEFAULT_MAX_QUEUE_SIZE,
        session_factory=session_factory,
        flow_processor=_flow_processor,
    )
    return _to_response(session)


@router.get(
    "/sessions",
    response_model=CaptureSessionListResponse,
    summary="List capture sessions, most recently started first",
    description="Returns every capture session this server knows about, running or finished.",
)
def get_capture_sessions(
    db: Session = Depends(get_db), _current_user: User = Depends(get_current_user)
) -> CaptureSessionListResponse:
    return CaptureSessionListResponse(items=[_to_response(s) for s in live_capture.list_sessions(db)])


@router.get(
    "/sessions/{session_id}",
    response_model=CaptureSessionResponse,
    summary="Retrieve one capture session's current status and statistics",
    responses={404: {"model": ErrorResponse, "description": "No session exists with that ID"}},
)
def get_capture_session(
    session_id: uuid.UUID, db: Session = Depends(get_db), _current_user: User = Depends(get_current_user)
) -> CaptureSessionResponse:
    return _to_response(live_capture.get_status(db, session_id))


@router.post(
    "/sessions/{session_id}/stop",
    response_model=CaptureSessionResponse,
    summary="Stop a running capture session",
    description=(
        "Signals the session to stop, waits for its capture/worker threads to finish flushing "
        "any remaining flows, and marks it `stopped`. Returns 409 if the session is not running."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "No session exists with that ID"},
        409: {"model": ErrorResponse, "description": "The session is not running"},
    },
)
def stop_capture_session(
    session_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> CaptureSessionResponse:
    require_role(current_user, UserRole.ADMINISTRATOR, UserRole.SECURITY_ANALYST)
    return _to_response(live_capture.stop_capture(db, session_id))
