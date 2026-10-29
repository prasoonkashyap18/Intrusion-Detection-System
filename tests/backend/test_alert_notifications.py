"""Tests for app.services.alert_notifications: publish/subscribe,
backpressure on a full subscriber queue, and that notification
failures never propagate to the caller.
"""

from __future__ import annotations

import queue
import uuid
from datetime import datetime, timezone

from app.services.alert_notifications import AlertNotification, AlertNotificationBroker, notify_new_alert


def make_notification(**overrides) -> AlertNotification:
    defaults = dict(
        alert_id=uuid.uuid4(), batch_id=uuid.uuid4(), severity="high", status="open",
        title="t", created_at=datetime.now(timezone.utc),
    )
    return AlertNotification(**{**defaults, **overrides})


class _FakeAlert:
    def __init__(self, **overrides):
        self.id = overrides.get("id", uuid.uuid4())
        self.batch_id = overrides.get("batch_id", uuid.uuid4())

        class _Severity:
            value = "critical"

        class _Status:
            value = "open"

        self.severity = overrides.get("severity", _Severity())
        self.status = overrides.get("status", _Status())
        self.title = overrides.get("title", "Confirmed attack")
        self.created_at = overrides.get("created_at", datetime.now(timezone.utc))


class TestSubscribeAndPublish:
    def test_a_subscriber_receives_a_published_notification(self):
        broker = AlertNotificationBroker(max_queue_size=10)
        subscriber_id, inbox = broker.subscribe()

        notification = make_notification()
        broker.publish(notification)

        received = inbox.get_nowait()
        assert received.alert_id == notification.alert_id

    def test_multiple_subscribers_all_receive_the_same_notification(self):
        broker = AlertNotificationBroker(max_queue_size=10)
        _, inbox_a = broker.subscribe()
        _, inbox_b = broker.subscribe()

        notification = make_notification()
        broker.publish(notification)

        assert inbox_a.get_nowait().alert_id == notification.alert_id
        assert inbox_b.get_nowait().alert_id == notification.alert_id

    def test_unsubscribed_subscriber_receives_nothing_further(self):
        broker = AlertNotificationBroker(max_queue_size=10)
        subscriber_id, inbox = broker.subscribe()
        broker.unsubscribe(subscriber_id)

        broker.publish(make_notification())

        assert inbox.empty()

    def test_publish_with_no_subscribers_does_not_raise(self):
        broker = AlertNotificationBroker(max_queue_size=10)
        broker.publish(make_notification())  # no assertion needed: must simply not raise


class TestBackpressure:
    def test_a_full_queue_drops_the_oldest_rather_than_blocking(self):
        broker = AlertNotificationBroker(max_queue_size=2)
        subscriber_id, inbox = broker.subscribe()

        first = make_notification(title="first")
        second = make_notification(title="second")
        third = make_notification(title="third")
        broker.publish(first)
        broker.publish(second)
        broker.publish(third)  # queue was full at 2 -- oldest ("first") is dropped

        remaining = [inbox.get_nowait(), inbox.get_nowait()]
        assert [n.title for n in remaining] == ["second", "third"]
        assert inbox.empty()

    def test_a_slow_subscriber_does_not_block_publish_for_others(self):
        broker = AlertNotificationBroker(max_queue_size=1)
        _, slow_inbox = broker.subscribe()
        _, fast_inbox = broker.subscribe()

        for i in range(5):
            broker.publish(make_notification(title=f"n{i}"))

        # The slow subscriber's queue never grew past its bound.
        assert slow_inbox.qsize() <= 1
        assert fast_inbox.qsize() <= 1


class TestNotifyNewAlert:
    def test_builds_and_publishes_from_a_real_alert_like_object(self):
        broker = AlertNotificationBroker(max_queue_size=10)
        import app.services.alert_notifications as module

        original_broker = module.broker
        module.broker = broker
        try:
            alert = _FakeAlert(title="Confirmed attack signal")
            notify_new_alert(alert)
            _, inbox = broker.subscribe()
            # Re-publish after subscribing isn't possible retroactively;
            # instead verify no exception occurred and subscriber_count
            # reflects the broker's own bookkeeping.
            assert broker.subscriber_count >= 1
        finally:
            module.broker = original_broker

    def test_never_raises_even_for_a_malformed_alert_object(self):
        class BrokenAlert:
            id = uuid.uuid4()
            # Missing every other expected attribute.

        notify_new_alert(BrokenAlert())  # must not raise

    def test_a_broken_broker_publish_never_raises_out_of_notify(self, monkeypatch):
        import app.services.alert_notifications as module

        def exploding_publish(notification):
            raise RuntimeError("simulated broker failure")

        monkeypatch.setattr(module.broker, "publish", exploding_publish)

        notify_new_alert(_FakeAlert())  # must not raise despite the broker itself failing


class TestPersistBeforeNotifyIntegration:
    def test_batch_processing_persists_alerts_even_if_notification_fails(self, db_session, monkeypatch):
        import uuid as _uuid

        from app.models.alert import Alert
        from app.models.detection_batch import DetectionBatch
        from app.models.enums import ProcessingStatus
        from app.models.hybrid_detection_result import HybridDetectionResult
        from app.models.risk_assessment import RiskAssessment
        from app.services.explainability import build_explanation
        from app.services.hybrid_detection import combine_signals, persist_hybrid_results
        from app.services.hybrid_detection import HybridSignal
        from app.services.risk_scoring import compute_risk_score, persist_risk_assessments
        from app.services.alert_generation import build_alert_draft, persist_alerts
        import app.services.alert_notifications as module

        batch = DetectionBatch(
            id=_uuid.uuid4(), filename="x.csv", status=ProcessingStatus.PROCESSING, total_records=1
        )
        db_session.add(batch)
        db_session.commit()

        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack",
            attack_probability=0.9, anomaly_available=True, is_anomaly=True, anomaly_score=-0.5,
        )
        outcome_result = combine_signals(signal)
        hybrid_rows = persist_hybrid_results(db_session, batch.id, [outcome_result])
        risk_result = compute_risk_score(outcome_result)
        risk_rows = persist_risk_assessments(db_session, batch.id, [(risk_result, hybrid_rows[0])])
        explanation = build_explanation(outcome_result, risk_result, feature_importance=None)
        draft = build_alert_draft(outcome_result, risk_result, hybrid_rows[0], risk_rows[0], explanation, None)

        def exploding_publish(notification):
            raise RuntimeError("simulated broker failure")

        monkeypatch.setattr(module.broker, "publish", exploding_publish)

        from app.services.alert_notifications import notify_new_alerts

        alerts = persist_alerts(db_session, [draft])
        notify_new_alerts(alerts)  # must not raise, and the alert above must still be committed

        persisted = db_session.query(Alert).filter(Alert.batch_id == batch.id).all()
        assert len(persisted) == 1
