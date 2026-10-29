"""Tests for app.services.alert_lifecycle: centralized, validated
AlertStatus transitions with an append-only audit trail.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import ApiException
from app.models.alert import Alert
from app.models.alert_status_history import AlertStatusHistory
from app.models.detection_batch import DetectionBatch
from app.models.enums import AlertStatus, ProcessingStatus, Severity
from app.services.alert_lifecycle import (
    VALID_TRANSITIONS,
    InvalidAlertTransitionError,
    is_valid_transition,
    list_status_history,
    transition_alert,
)


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="x.csv", status=ProcessingStatus.PROCESSING, total_records=1)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def add_alert(db_session, batch, **overrides) -> Alert:
    defaults = dict(
        batch_id=batch.id, row_number=1, title="t", description="d", severity=Severity.HIGH,
        status=AlertStatus.OPEN, evidence={},
    )
    alert = Alert(**{**defaults, **overrides})
    db_session.add(alert)
    db_session.commit()
    return alert


class TestValidTransitionTable:
    def test_open_can_reach_every_other_state(self):
        assert VALID_TRANSITIONS[AlertStatus.OPEN] == {
            AlertStatus.ACKNOWLEDGED, AlertStatus.INVESTIGATING, AlertStatus.RESOLVED, AlertStatus.FALSE_POSITIVE
        }

    def test_resolved_and_false_positive_are_terminal(self):
        assert VALID_TRANSITIONS[AlertStatus.RESOLVED] == frozenset()
        assert VALID_TRANSITIONS[AlertStatus.FALSE_POSITIVE] == frozenset()

    def test_is_valid_transition_same_status_is_always_valid(self):
        for status in AlertStatus:
            assert is_valid_transition(status, status) is True

    def test_is_valid_transition_rejects_out_of_terminal(self):
        assert is_valid_transition(AlertStatus.RESOLVED, AlertStatus.OPEN) is False
        assert is_valid_transition(AlertStatus.FALSE_POSITIVE, AlertStatus.INVESTIGATING) is False


class TestValidTransitions:
    def test_open_to_acknowledged(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        updated = transition_alert(db_session, alert.id, AlertStatus.ACKNOWLEDGED)

        assert updated.status == AlertStatus.ACKNOWLEDGED

    def test_acknowledged_to_investigating_to_resolved(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch, status=AlertStatus.ACKNOWLEDGED)

        transition_alert(db_session, alert.id, AlertStatus.INVESTIGATING)
        updated = transition_alert(db_session, alert.id, AlertStatus.RESOLVED)

        assert updated.status == AlertStatus.RESOLVED

    def test_open_directly_to_false_positive(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        updated = transition_alert(db_session, alert.id, AlertStatus.FALSE_POSITIVE, note="not a real attack")

        assert updated.status == AlertStatus.FALSE_POSITIVE

    def test_updated_at_changes_on_a_real_transition(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)
        original_updated_at = alert.updated_at

        updated = transition_alert(db_session, alert.id, AlertStatus.ACKNOWLEDGED)

        assert updated.updated_at >= original_updated_at


class TestInvalidTransitions:
    def test_resolved_to_open_is_rejected(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch, status=AlertStatus.RESOLVED)

        with pytest.raises(InvalidAlertTransitionError):
            transition_alert(db_session, alert.id, AlertStatus.OPEN)

    def test_false_positive_to_investigating_is_rejected(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch, status=AlertStatus.FALSE_POSITIVE)

        with pytest.raises(InvalidAlertTransitionError):
            transition_alert(db_session, alert.id, AlertStatus.INVESTIGATING)

    def test_a_rejected_transition_does_not_change_status(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch, status=AlertStatus.RESOLVED)

        with pytest.raises(InvalidAlertTransitionError):
            transition_alert(db_session, alert.id, AlertStatus.OPEN)

        db_session.refresh(alert)
        assert alert.status == AlertStatus.RESOLVED

    def test_a_rejected_transition_writes_no_history_row(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch, status=AlertStatus.RESOLVED)

        with pytest.raises(InvalidAlertTransitionError):
            transition_alert(db_session, alert.id, AlertStatus.OPEN)

        assert list_status_history(db_session, alert.id) == []

    def test_unknown_alert_id_raises_404(self, db_session):
        with pytest.raises(ApiException) as exc_info:
            transition_alert(db_session, uuid.uuid4(), AlertStatus.ACKNOWLEDGED)
        assert exc_info.value.status_code == 404


class TestIdempotentRepeatedRequests:
    def test_requesting_the_current_status_is_a_no_op(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch, status=AlertStatus.OPEN)

        updated = transition_alert(db_session, alert.id, AlertStatus.OPEN)

        assert updated.status == AlertStatus.OPEN
        assert list_status_history(db_session, alert.id) == []

    def test_repeating_a_successful_transition_request_is_safe(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        transition_alert(db_session, alert.id, AlertStatus.ACKNOWLEDGED)
        # The caller retries the identical request after the first one
        # already succeeded -- this must not raise or create a second
        # history entry, since the alert is now already acknowledged.
        updated = transition_alert(db_session, alert.id, AlertStatus.ACKNOWLEDGED)

        assert updated.status == AlertStatus.ACKNOWLEDGED
        assert len(list_status_history(db_session, alert.id)) == 1


class TestAuditHistory:
    def test_history_records_previous_and_new_status(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        transition_alert(db_session, alert.id, AlertStatus.ACKNOWLEDGED)

        history = list_status_history(db_session, alert.id)
        assert len(history) == 1
        assert history[0].previous_status == AlertStatus.OPEN
        assert history[0].new_status == AlertStatus.ACKNOWLEDGED

    def test_history_records_a_note_when_supplied(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        transition_alert(db_session, alert.id, AlertStatus.FALSE_POSITIVE, note="benign test traffic")

        history = list_status_history(db_session, alert.id)
        assert history[0].note == "benign test traffic"

    def test_history_is_ordered_oldest_first(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        transition_alert(db_session, alert.id, AlertStatus.ACKNOWLEDGED)
        transition_alert(db_session, alert.id, AlertStatus.INVESTIGATING)
        transition_alert(db_session, alert.id, AlertStatus.RESOLVED)

        history = list_status_history(db_session, alert.id)
        assert [h.new_status for h in history] == [
            AlertStatus.ACKNOWLEDGED, AlertStatus.INVESTIGATING, AlertStatus.RESOLVED
        ]

    def test_unknown_alert_id_raises_404_for_history_too(self, db_session):
        with pytest.raises(ApiException) as exc_info:
            list_status_history(db_session, uuid.uuid4())
        assert exc_info.value.status_code == 404


class TestEvidencePreservation:
    def test_evidence_and_source_references_are_unchanged_by_a_status_change(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch, evidence={"hybrid_outcome": "confirmed_attack", "risk_score": 90.0})
        detection_result_id = alert.detection_result_id

        transition_alert(db_session, alert.id, AlertStatus.INVESTIGATING)
        db_session.refresh(alert)

        assert alert.evidence == {"hybrid_outcome": "confirmed_attack", "risk_score": 90.0}
        assert alert.detection_result_id == detection_result_id


class TestFailureHandling:
    def test_commit_failure_rolls_back_and_leaves_status_unchanged(self, db_session, monkeypatch):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch)

        def failing_commit():
            raise SQLAlchemyError("database is locked")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(ApiException) as exc_info:
            transition_alert(db_session, alert.id, AlertStatus.ACKNOWLEDGED)
        assert exc_info.value.status_code == 500

        monkeypatch.undo()
        db_session.rollback()
        db_session.refresh(alert)
        assert alert.status == AlertStatus.OPEN
        assert db_session.query(AlertStatusHistory).filter(AlertStatusHistory.alert_id == alert.id).count() == 0
