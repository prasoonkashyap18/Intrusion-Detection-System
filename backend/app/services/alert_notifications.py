"""In-memory, best-effort real-time alert notification (Step 44) — a
simple publish/subscribe broker that `GET /api/v1/alerts/stream` (SSE)
reads from, strictly *after* an alert has already been durably
persisted (see `app.services.alert_generation.persist_alerts`'s own
callers in `app.services.batch_processor`/`app.services.
realtime_detection`, both of which call `notify_new_alert` only once
their own `db.commit()` has already succeeded).

This module is **not** the source of truth for alerts — it is a
convenience so an already-connected client learns about a new alert
without polling. A disconnected or newly connecting client always
recovers via the existing, persisted `GET /api/v1/alerts` endpoint
(optionally filtered with `created_after` — see `app.services.
alert_query_service.list_alerts`), never through this broker. Losing
every subscriber (e.g. the process restarts) loses nothing durable:
every alert this module ever published still exists in the database
exactly as `app.services.alert_generation` wrote it.

Backpressure: each subscriber gets its own bounded `queue.Queue`. A
slow or stalled client that never drains its queue simply stops
receiving *new* notifications once its queue is full (the oldest
undelivered notification is dropped to make room for the newest one) —
it never blocks the publisher (a detection/alert-generation code path)
and never grows memory without bound. The dropped count is tracked per
subscriber so an SSE handler can report it if useful.
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("ai_ids")

DEFAULT_SUBSCRIBER_QUEUE_SIZE = 100


@dataclass(frozen=True)
class AlertNotification:
    """The minimal, already-public shape of a new-alert notification —
    never anything beyond what `GET /api/v1/alerts/{id}` already
    exposes; a client that wants the full record follows up with that
    endpoint (or already has everything it needs here)."""

    alert_id: uuid.UUID
    batch_id: uuid.UUID
    severity: str
    status: str
    title: str
    created_at: datetime


@dataclass
class _Subscriber:
    inbox: "queue.Queue[AlertNotification]"
    dropped_count: int = 0


class AlertNotificationBroker:
    """A process-local broker. One instance (`broker`, below) is shared
    by every request — `subscribe`/`unsubscribe`/`publish` are the
    entire interface, and all three are safe to call from any thread
    (detection code publishes from background capture/worker threads;
    SSE handlers subscribe/unsubscribe from the request-handling
    thread/task)."""

    def __init__(self, max_queue_size: int = DEFAULT_SUBSCRIBER_QUEUE_SIZE) -> None:
        self._max_queue_size = max_queue_size
        self._subscribers: dict[uuid.UUID, _Subscriber] = {}
        self._lock = threading.Lock()

    def subscribe(self) -> tuple[uuid.UUID, "queue.Queue[AlertNotification]"]:
        subscriber_id = uuid.uuid4()
        inbox: "queue.Queue[AlertNotification]" = queue.Queue(maxsize=self._max_queue_size)
        with self._lock:
            self._subscribers[subscriber_id] = _Subscriber(inbox=inbox)
        return subscriber_id, inbox

    def unsubscribe(self, subscriber_id: uuid.UUID) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def publish(self, notification: AlertNotification) -> None:
        """Best-effort fan-out to every current subscriber. Never
        raises — a notification failure (there is no subscriber, or
        every subscriber's queue is full) must never be allowed to
        interrupt the caller, which has already durably persisted the
        alert this notification merely announces.
        """
        with self._lock:
            subscribers = list(self._subscribers.values())
        for subscriber in subscribers:
            try:
                subscriber.inbox.put_nowait(notification)
            except queue.Full:
                try:
                    subscriber.inbox.get_nowait()  # drop the oldest to make room for the newest
                    subscriber.inbox.put_nowait(notification)
                except queue.Empty:
                    pass
                subscriber.dropped_count += 1
            except Exception:
                logger.exception("Failed to enqueue an alert notification for a subscriber")

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


broker = AlertNotificationBroker()


def notify_new_alert(alert) -> None:
    """Builds and publishes one `AlertNotification` for an already-
    persisted `Alert` row. Called only after the caller's own commit
    has succeeded (see the module docstring); any failure here is
    caught and logged, never propagated — a notification is a
    convenience, not a correctness requirement.
    """
    try:
        notification = AlertNotification(
            alert_id=alert.id,
            batch_id=alert.batch_id,
            severity=alert.severity.value,
            status=alert.status.value,
            title=alert.title,
            created_at=alert.created_at if alert.created_at.tzinfo else alert.created_at.replace(tzinfo=timezone.utc),
        )
        broker.publish(notification)
    except Exception:
        logger.exception("Failed to publish a real-time notification for alert %s", getattr(alert, "id", None))


def notify_new_alerts(alerts) -> None:
    for alert in alerts:
        notify_new_alert(alert)


__all__ = [
    "DEFAULT_SUBSCRIBER_QUEUE_SIZE",
    "AlertNotification",
    "AlertNotificationBroker",
    "broker",
    "notify_new_alert",
    "notify_new_alerts",
]
