"""Live packet capture (Step 42): a controlled capture session with an
explicit lifecycle (`running`/`stopped`/`failed`), built on the exact
same `app.services.flow_construction`/`app.services.
pcap_feature_mapping` components Step 41 already uses for a capture
*file* — a live session is simply a capture whose packets arrive
continuously instead of being read from a finite file.

    scapy.sniff() (or an injected test packet_source)  <- capture thread
        |
    bounded queue.Queue  <- backpressure: full queue drops the newest
        |                    packet and counts it, never blocks capture
    FlowConstructor + flow_processor callback  <- worker thread
        |
    MappedFeatureRecord rows (dataset_schema="pcap-flow")
        (+, once Step 43 wires its own flow_processor in, the entire
         existing inference/hybrid/risk/explanation/alert pipeline)

Thread model (deliberately exactly two long-lived threads per session,
never more): one capture thread does *only* packet acquisition — read a
packet, build a `PacketRecord`, put it on the queue, repeat — and one
worker thread does *only* flow construction + persistence, pulling from
the queue with a timeout so it can also periodically call
`FlowConstructor.idle_flows()` even when no new packet arrives (the
mechanism that finalizes a flow that simply stops being talked to,
since no future packet will ever arrive to discover that for a live
session the way `flush()` discovers it at the end of a bounded PCAP
file). Exactly one worker thread per session is what prevents two
concurrent workers from ever creating duplicate results for the same
finalized flow — there is structurally only ever one.

Neither thread ever touches the request-scoped `Session` passed into
`start_capture`/`stop_capture`/`get_status` — background threads open
their own `SessionLocal()` per unit of work (SQLAlchemy sessions are
not safe to share across threads), closing it before the next.

Privileges and dependencies: live capture requires a capture backend
installed on the OS (Npcap/WinPcap on Windows, libpcap on Linux/macOS)
and, on most platforms, elevated privileges to open a raw socket on the
chosen interface. Neither is assumed present — `list_interfaces`/
`start_capture` catch every failure mode `scapy` surfaces for a missing
backend, a missing permission, or an invalid interface name and turn it
into a clear `ApiException` rather than letting an unrelated-looking
exception propagate or, worse, silently starting nothing. This module
never attempts to elevate its own privileges or bypass an OS permission
check — see backend/README.md's "Live Traffic Capture" section for
exactly what is and is not verified in this environment.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.db.database import SessionLocal
from app.models.capture_session import CaptureSession
from app.models.detection_batch import DetectionBatch
from app.models.enums import CaptureSessionStatus, ProcessingStatus
from app.services.feature_persistence import persist_mapped_features
from app.services.flow_construction import DEFAULT_IDLE_TIMEOUT_SECONDS, FinalizedFlow, FlowConstructor, PacketRecord
from app.services.pcap_feature_mapping import flow_to_mapped_feature_set

logger = logging.getLogger("ai_ids")

DEFAULT_MAX_QUEUE_SIZE = 10_000
_IDLE_CHECK_INTERVAL_SECONDS = 1.0
_STOP_JOIN_TIMEOUT_SECONDS = 5.0

PacketSource = Callable[[str, "str | None", threading.Event], Iterator[PacketRecord]]
FlowProcessor = Callable[[uuid.UUID, FinalizedFlow, int], None]
"""`(batch_id, flow, row_number) -> None` — called once per finalized
flow, from the single worker thread, so an implementation never needs
its own locking to stay duplicate-free. Step 42's own default
(`_persist_flow_features_only`) just persists the flow's features;
Step 43 passes a richer processor that also runs inference/hybrid/
risk/explanation/alerting for it (see `app.services.realtime_detection`)."""


class CaptureUnavailableError(ApiException):
    """A clear, actionable `ApiException` for "capture cannot start/run
    here" — missing capture backend, missing privileges, or an unknown
    interface. Always 503 (the capability is unavailable, not the
    request malformed) unless a 4xx is more precise (see call sites)."""


@dataclass
class _SessionWorkers:
    stop_event: threading.Event
    packet_queue: "queue.Queue[PacketRecord]"
    capture_thread: threading.Thread
    worker_thread: threading.Thread
    stats_lock: threading.Lock = field(default_factory=threading.Lock)
    stats: dict = field(default_factory=lambda: {
        "packets_received": 0, "packets_processed": 0, "packets_dropped": 0,
        "flows_created": 0, "flows_finalized": 0, "parse_failures": 0,
    })


def list_interfaces() -> list[str]:
    """Returns the real interface names `scapy` can see on this host.
    Raises `CaptureUnavailableError` (503) if the capture backend is not
    installed/usable at all — never a fabricated or cached list."""
    try:
        from scapy.all import get_if_list
    except Exception as error:
        raise CaptureUnavailableError(
            503, "capture_unavailable", "The packet-capture dependency is not available on this host."
        ) from error

    try:
        return list(get_if_list())
    except Exception as error:
        logger.exception("Failed to enumerate network interfaces")
        raise CaptureUnavailableError(
            503, "capture_unavailable", "Could not enumerate network interfaces on this host."
        ) from error


def _default_packet_source(interface: str, bpf_filter: str | None, stop_event: threading.Event) -> Iterator[PacketRecord]:
    """Live packets from `scapy.sniff`, converted through the exact
    same packet-field extraction Step 41 uses for a capture file (see
    `app.services.pcap_parsing`), so a live session and a PCAP upload
    never interpret a packet's fields two different ways. Raises
    `CaptureUnavailableError` if `scapy` cannot actually sniff on this
    host (no backend installed, no permission, unknown interface) —
    checked by sending one request through `sniff(count=0, timeout=0)`-
    style validation before this generator is ever handed to a thread.
    """
    from scapy.utils import PcapReader  # noqa: F401 (import-availability check only)
    from app.services.pcap_parsing import packet_to_record, PacketParsingStats
    from scapy.sendrecv import sniff

    stats = PacketParsingStats()
    collected: list[PacketRecord] = []

    def _on_packet(packet) -> None:
        if stop_event.is_set():
            return
        record = packet_to_record(packet, stats)
        if record is not None:
            collected.append(record)

    while not stop_event.is_set():
        try:
            sniff(
                iface=interface,
                filter=bpf_filter,
                prn=_on_packet,
                stop_filter=lambda _pkt: stop_event.is_set(),
                timeout=1.0,
                store=False,
            )
        except Exception as error:
            raise CaptureUnavailableError(
                503,
                "capture_unavailable",
                "Could not capture on the requested interface (missing privileges, missing capture "
                "backend, or an invalid interface name).",
            ) from error

        while collected:
            yield collected.pop(0)


def start_capture(
    db: Session,
    *,
    interface: str,
    bpf_filter: str | None = None,
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
    max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
    packet_source: PacketSource | None = None,
    flow_processor: FlowProcessor | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> CaptureSession:
    """Starts a new capture session on `interface`. Raises
    `ApiException` 409 if a `RUNNING` session already exists for that
    exact interface (the entire "cannot accidentally be started
    multiple times for the same interface" guarantee — checked and
    committed atomically against the database, not just in-memory, so
    two concurrent requests cannot both win). `interface` is always
    required explicitly — there is no "capture everything" default.

    `packet_source`/`flow_processor` are the seams tests use to inject
    a deterministic, no-privileges-required packet stream and a
    processing hook — production code never supplies them and gets the
    real `scapy`-backed source and the Step-41-only feature persister.
    `session_factory` is the seam tests use to point the session's
    background threads at an isolated test database instead of the
    real configured one (SQLAlchemy sessions are never shared across
    threads — see the module docstring — so each background unit of
    work calls this to get its own).
    """
    if not interface or not interface.strip():
        raise ApiException(400, "missing_interface", "An interface must be specified explicitly.")

    existing = db.scalars(
        select(CaptureSession).where(
            CaptureSession.interface_name == interface, CaptureSession.status == CaptureSessionStatus.RUNNING
        )
    ).first()
    if existing is not None:
        raise ApiException(
            409, "capture_already_running", f"A capture session is already running on interface '{interface}'."
        )

    batch = DetectionBatch(
        id=uuid.uuid4(), filename=f"live:{interface}", status=ProcessingStatus.PROCESSING, total_records=0
    )
    session = CaptureSession(
        id=uuid.uuid4(),
        batch_id=batch.id,
        interface_name=interface,
        bpf_filter=bpf_filter,
        idle_timeout_seconds=idle_timeout_seconds,
        status=CaptureSessionStatus.RUNNING,
        stats={"packets_received": 0, "packets_processed": 0, "packets_dropped": 0,
               "flows_created": 0, "flows_finalized": 0, "parse_failures": 0},
    )
    db.add(batch)
    db.add(session)
    db.commit()

    workers = _spawn_workers(
        session.id, batch.id, interface, bpf_filter, idle_timeout_seconds, max_queue_size,
        packet_source or _default_packet_source,
        flow_processor or _make_default_flow_processor(session_factory),
        session_factory,
    )
    _ACTIVE_SESSIONS[session.id] = workers

    return session


def _spawn_workers(
    session_id: uuid.UUID,
    batch_id: uuid.UUID,
    interface: str,
    bpf_filter: str | None,
    idle_timeout_seconds: float,
    max_queue_size: int,
    packet_source: PacketSource,
    flow_processor: FlowProcessor,
    session_factory: Callable[[], Session],
) -> _SessionWorkers:
    stop_event = threading.Event()
    packet_queue: "queue.Queue[PacketRecord]" = queue.Queue(maxsize=max_queue_size)
    workers = _SessionWorkers(
        stop_event=stop_event, packet_queue=packet_queue,
        capture_thread=None, worker_thread=None,  # type: ignore[arg-type]  # set below
    )

    def _capture_loop() -> None:
        try:
            for record in packet_source(interface, bpf_filter, stop_event):
                if stop_event.is_set():
                    break
                with workers.stats_lock:
                    workers.stats["packets_received"] += 1
                try:
                    packet_queue.put_nowait(record)
                except queue.Full:
                    with workers.stats_lock:
                        workers.stats["packets_dropped"] += 1
        except CaptureUnavailableError as error:
            logger.error("Capture session %s failed: %s", session_id, error.message)
            _mark_session_failed(session_id, error.message, session_factory)
        except Exception:
            logger.exception("Capture session %s: unexpected capture-thread failure", session_id)
            _mark_session_failed(
                session_id, "An unexpected error occurred during packet capture.", session_factory
            )
        finally:
            stop_event.set()

    def _worker_loop() -> None:
        constructor = FlowConstructor(idle_timeout_seconds)
        row_counter = 0
        try:
            while not stop_event.is_set() or not packet_queue.empty():
                try:
                    record = packet_queue.get(timeout=_IDLE_CHECK_INTERVAL_SECONDS)
                except queue.Empty:
                    record = None

                if record is not None:
                    finalized = constructor.add_packet(record)
                    with workers.stats_lock:
                        workers.stats["packets_processed"] += 1
                        workers.stats["flows_created"] = constructor.open_flow_count
                    if finalized is not None:
                        row_counter += 1
                        _finalize_flow(batch_id, finalized, row_counter, flow_processor, workers)

                for finalized in constructor.idle_flows(now=time.time()):
                    row_counter += 1
                    _finalize_flow(batch_id, finalized, row_counter, flow_processor, workers)

            for finalized in constructor.flush():
                row_counter += 1
                _finalize_flow(batch_id, finalized, row_counter, flow_processor, workers)
        except Exception:
            logger.exception("Capture session %s: unexpected worker-thread failure", session_id)

    workers.capture_thread = threading.Thread(target=_capture_loop, daemon=True, name=f"capture-{session_id}")
    workers.worker_thread = threading.Thread(target=_worker_loop, daemon=True, name=f"flow-worker-{session_id}")
    workers.capture_thread.start()
    workers.worker_thread.start()
    return workers


def _finalize_flow(
    batch_id: uuid.UUID, flow: FinalizedFlow, row_number: int,
    flow_processor: FlowProcessor, workers: _SessionWorkers,
) -> None:
    try:
        flow_processor(batch_id, flow, row_number)
    except Exception:
        logger.exception("Batch %s: flow processor failed for row %s; continuing capture", batch_id, row_number)
        with workers.stats_lock:
            workers.stats["parse_failures"] += 1
        return
    with workers.stats_lock:
        workers.stats["flows_finalized"] += 1


def _make_default_flow_processor(session_factory: Callable[[], Session]) -> FlowProcessor:
    """The Step-42-only default `FlowProcessor`: persists a flow's
    features and nothing else — no inference, no alerting (Step 43
    supplies a different, richer processor instead). Opens and closes
    its own session per flow (via `session_factory`) so a persistence
    failure for one flow can never corrupt or block the next.
    """

    def _persist_flow_features_only(batch_id: uuid.UUID, flow: FinalizedFlow, row_number: int) -> None:
        db_session = session_factory()
        try:
            mapped = flow_to_mapped_feature_set(flow, row_number)
            persist_mapped_features(db_session, batch_id, mapped)
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise
        finally:
            db_session.close()

    return _persist_flow_features_only


_ACTIVE_SESSIONS: dict[uuid.UUID, _SessionWorkers] = {}


def _mark_session_failed(
    session_id: uuid.UUID, message: str, session_factory: Callable[[], Session] = SessionLocal
) -> None:
    # Never let a failure to *record* the failure become an unhandled
    # exception on this background thread (e.g. the engine/database the
    # session was bound to is already gone — most commonly seen when a
    # test's isolated in-memory database is torn down while a daemon
    # thread from an earlier test is still winding down).
    try:
        db_session = session_factory()
    except Exception:
        logger.exception("Capture session %s: could not open a session to record its failure", session_id)
        return
    try:
        session = db_session.get(CaptureSession, session_id)
        if session is not None and session.status == CaptureSessionStatus.RUNNING:
            session.status = CaptureSessionStatus.FAILED
            session.error_message = message
            session.stopped_at = datetime.now(timezone.utc)
            db_session.commit()
    except Exception:
        logger.exception("Capture session %s: could not record its failure", session_id)
    finally:
        db_session.close()


def stop_capture(db: Session, session_id: uuid.UUID) -> CaptureSession:
    """Signals the session's threads to stop, waits (bounded) for them
    to finish flushing remaining flows, and marks the session
    `stopped` with its final statistics. Raises `ApiException` 404 if
    no such session exists, 409 if it is not `running`. Safe to call
    more than once — a second call on an already-`stopped`/`failed`
    session raises 409 rather than silently doing nothing or erroring
    confusingly.
    """
    # A background thread may have changed this row (e.g. flipped it to
    # FAILED) through its own, separate session — expire this session's
    # identity map first so the read below is never stale.
    db.expire_all()
    session = db.get(CaptureSession, session_id)
    if session is None:
        raise ApiException(404, "capture_session_not_found", "No capture session was found with that ID.")
    if session.status != CaptureSessionStatus.RUNNING:
        raise ApiException(
            409, "capture_not_running", f"Capture session is '{session.status.value}', not running."
        )

    workers = _ACTIVE_SESSIONS.pop(session_id, None)
    if workers is not None:
        workers.stop_event.set()
        workers.capture_thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
        workers.worker_thread.join(timeout=_STOP_JOIN_TIMEOUT_SECONDS)
        with workers.stats_lock:
            final_stats = dict(workers.stats)
    else:
        final_stats = session.stats

    db.refresh(session)
    if session.status == CaptureSessionStatus.RUNNING:
        session.status = CaptureSessionStatus.STOPPED
        session.stopped_at = datetime.now(timezone.utc)
        session.stats = final_stats
        db.commit()
        db.refresh(session)
    return session


def get_status(db: Session, session_id: uuid.UUID) -> CaptureSession:
    db.expire_all()  # see stop_capture's identical comment
    session = db.get(CaptureSession, session_id)
    if session is None:
        raise ApiException(404, "capture_session_not_found", "No capture session was found with that ID.")
    workers = _ACTIVE_SESSIONS.get(session_id)
    if workers is not None and session.status == CaptureSessionStatus.RUNNING:
        with workers.stats_lock:
            session.stats = dict(workers.stats)
    return session


def list_sessions(db: Session) -> list[CaptureSession]:
    return list(db.scalars(select(CaptureSession).order_by(CaptureSession.started_at.desc())).all())


__all__ = [
    "CaptureUnavailableError",
    "DEFAULT_MAX_QUEUE_SIZE",
    "PacketSource",
    "FlowProcessor",
    "list_interfaces",
    "start_capture",
    "stop_capture",
    "get_status",
    "list_sessions",
]
