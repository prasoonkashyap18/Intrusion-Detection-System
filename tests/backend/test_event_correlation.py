"""Tests for app.services.event_correlation: matching rules, temporal
boundaries, missing evidence, duplicate prevention, and persistence.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models.alert import Alert
from app.models.detection_batch import DetectionBatch
from app.models.enums import CorrelationStrength, ProcessingStatus, Severity
from app.models.event_correlation import EventCorrelation
from app.models.mapped_feature_record import MappedFeatureRecord
from app.services.event_correlation import (
    AlertContext,
    EventCorrelationConfig,
    EventCorrelationPersistenceError,
    build_and_persist_correlations_for_batch,
    find_correlations,
    load_alert_contexts,
    order_pair,
    persist_correlations,
)

BASE_TIME = datetime(2026, 10, 3, 8, 0, 0, tzinfo=timezone.utc)


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="x.csv", status=ProcessingStatus.PROCESSING, total_records=1)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def add_alert(db_session, batch, **overrides) -> Alert:
    defaults = dict(
        batch_id=batch.id, row_number=1, title="t", description="d", severity=Severity.HIGH,
        evidence={}, created_at=BASE_TIME,
    )
    alert = Alert(**{**defaults, **overrides})
    db_session.add(alert)
    db_session.commit()
    return alert


def add_feature_record(db_session, batch, row_number, *, source_ip=None, destination_ip=None) -> MappedFeatureRecord:
    def entry(raw):
        return {"value": None, "status": "present", "source_column": "x", "raw_value": raw} if raw else None

    features = {}
    if source_ip:
        features["source_ip_numeric"] = entry(source_ip)
    if destination_ip:
        features["destination_ip_numeric"] = entry(destination_ip)

    row = MappedFeatureRecord(
        batch_id=batch.id, row_number=row_number, feature_schema_version="1",
        dataset_schema="nsl-kdd-style", features=features, unknown_fields={},
    )
    db_session.add(row)
    db_session.commit()
    return row


class TestOrderPair:
    def test_always_returns_the_lexicographically_smaller_id_first(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        first, second = order_pair(a, b)
        assert str(first) < str(second)

    def test_is_symmetric(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        assert order_pair(a, b) == order_pair(b, a)


class TestFindCorrelationsSharedIP:
    def test_matching_source_ip_within_window_is_strong(self):
        a = AlertContext(uuid.uuid4(), uuid.uuid4(), 1, BASE_TIME, source_ip="10.0.0.5")
        b = AlertContext(uuid.uuid4(), uuid.uuid4(), 1, BASE_TIME + timedelta(seconds=10), source_ip="10.0.0.5")

        candidates = find_correlations([a, b], {a.alert_id, b.alert_id})

        assert len(candidates) == 1
        assert candidates[0].correlation_type == "shared_source_ip"
        assert candidates[0].strength == CorrelationStrength.STRONG
        assert candidates[0].evidence["shared_value"] == "10.0.0.5"

    def test_matching_destination_ip_within_window_is_strong(self):
        a = AlertContext(uuid.uuid4(), uuid.uuid4(), 1, BASE_TIME, destination_ip="192.168.1.1")
        b = AlertContext(uuid.uuid4(), uuid.uuid4(), 1, BASE_TIME, destination_ip="192.168.1.1")

        candidates = find_correlations([a, b], {a.alert_id, b.alert_id})

        assert any(c.correlation_type == "shared_destination_ip" and c.strength == CorrelationStrength.STRONG for c in candidates)

    def test_different_ips_never_match(self):
        a = AlertContext(uuid.uuid4(), uuid.uuid4(), 1, BASE_TIME, source_ip="10.0.0.5")
        b = AlertContext(uuid.uuid4(), uuid.uuid4(), 1, BASE_TIME, source_ip="10.0.0.6")

        candidates = find_correlations([a, b], {a.alert_id, b.alert_id})

        assert all(c.correlation_type != "shared_source_ip" for c in candidates)

    def test_matching_ip_outside_window_does_not_match(self):
        config = EventCorrelationConfig(time_window_seconds=60.0)
        a = AlertContext(uuid.uuid4(), uuid.uuid4(), 1, BASE_TIME, source_ip="10.0.0.5")
        b = AlertContext(uuid.uuid4(), uuid.uuid4(), 1, BASE_TIME + timedelta(seconds=120), source_ip="10.0.0.5")

        candidates = find_correlations([a, b], {a.alert_id, b.alert_id}, config)

        assert all(c.correlation_type != "shared_source_ip" for c in candidates)

    def test_exactly_at_the_window_boundary_matches(self):
        config = EventCorrelationConfig(time_window_seconds=60.0)
        a = AlertContext(uuid.uuid4(), uuid.uuid4(), 1, BASE_TIME, source_ip="10.0.0.5")
        b = AlertContext(uuid.uuid4(), uuid.uuid4(), 1, BASE_TIME + timedelta(seconds=60), source_ip="10.0.0.5")

        candidates = find_correlations([a, b], {a.alert_id, b.alert_id}, config)

        assert any(c.correlation_type == "shared_source_ip" for c in candidates)

    def test_missing_ip_evidence_never_fabricates_a_match(self):
        a = AlertContext(uuid.uuid4(), uuid.uuid4(), 1, BASE_TIME, source_ip=None)
        b = AlertContext(uuid.uuid4(), uuid.uuid4(), 1, BASE_TIME, source_ip=None)

        candidates = find_correlations([a, b], {a.alert_id, b.alert_id})

        assert all(c.correlation_type not in ("shared_source_ip", "shared_destination_ip") for c in candidates)


class TestFindCorrelationsProximity:
    def test_same_batch_within_row_distance_is_weak(self):
        batch_id = uuid.uuid4()
        a = AlertContext(uuid.uuid4(), batch_id, 10, BASE_TIME)
        b = AlertContext(uuid.uuid4(), batch_id, 12, BASE_TIME)

        candidates = find_correlations([a, b], {a.alert_id, b.alert_id})

        assert len(candidates) == 1
        assert candidates[0].correlation_type == "same_batch_proximity"
        assert candidates[0].strength == CorrelationStrength.WEAK
        assert candidates[0].evidence["row_distance"] == 2

    def test_same_batch_outside_row_distance_does_not_match(self):
        config = EventCorrelationConfig(max_row_distance=2)
        batch_id = uuid.uuid4()
        a = AlertContext(uuid.uuid4(), batch_id, 1, BASE_TIME)
        b = AlertContext(uuid.uuid4(), batch_id, 10, BASE_TIME)

        candidates = find_correlations([a, b], {a.alert_id, b.alert_id}, config)

        assert candidates == []

    def test_different_batches_never_match_on_proximity(self):
        a = AlertContext(uuid.uuid4(), uuid.uuid4(), 1, BASE_TIME)
        b = AlertContext(uuid.uuid4(), uuid.uuid4(), 2, BASE_TIME)

        candidates = find_correlations([a, b], {a.alert_id, b.alert_id})

        assert all(c.correlation_type != "same_batch_proximity" for c in candidates)


class TestNewAlertRequirement:
    def test_a_pair_with_neither_alert_new_is_skipped(self):
        batch_id = uuid.uuid4()
        a = AlertContext(uuid.uuid4(), batch_id, 1, BASE_TIME)
        b = AlertContext(uuid.uuid4(), batch_id, 2, BASE_TIME)

        candidates = find_correlations([a, b], new_alert_ids=set())

        assert candidates == []


class TestLoadAlertContexts:
    def test_reads_the_real_persisted_ip_evidence(self, db_session):
        batch = add_batch(db_session)
        add_feature_record(db_session, batch, 1, source_ip="10.0.0.5", destination_ip="192.168.1.1")
        alert = add_alert(db_session, batch, row_number=1)

        contexts = load_alert_contexts(db_session, [alert])

        assert contexts[0].source_ip == "10.0.0.5"
        assert contexts[0].destination_ip == "192.168.1.1"

    def test_missing_feature_record_yields_none_ips_not_an_error(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch, row_number=1)

        contexts = load_alert_contexts(db_session, [alert])

        assert contexts[0].source_ip is None
        assert contexts[0].destination_ip is None


class TestPersistCorrelations:
    def test_persists_new_candidates(self, db_session):
        batch = add_batch(db_session)
        a = add_alert(db_session, batch, row_number=1)
        b = add_alert(db_session, batch, row_number=2)
        contexts = load_alert_contexts(db_session, [a, b])
        candidates = find_correlations(contexts, {a.id, b.id})

        rows = persist_correlations(db_session, candidates)

        assert len(rows) == len(candidates)

    def test_empty_input_persists_nothing(self, db_session):
        assert persist_correlations(db_session, []) == []

    def test_repeated_call_with_the_same_candidates_does_not_duplicate(self, db_session):
        batch = add_batch(db_session)
        a = add_alert(db_session, batch, row_number=1)
        b = add_alert(db_session, batch, row_number=2)
        contexts = load_alert_contexts(db_session, [a, b])
        candidates = find_correlations(contexts, {a.id, b.id})

        persist_correlations(db_session, candidates)
        second_call_rows = persist_correlations(db_session, candidates)

        assert second_call_rows == []
        all_rows = db_session.scalars(select(EventCorrelation)).all()
        assert len(all_rows) == len(candidates)

    def test_commit_failure_rolls_back(self, db_session, monkeypatch):
        batch = add_batch(db_session)
        a = add_alert(db_session, batch, row_number=1)
        b = add_alert(db_session, batch, row_number=2)
        contexts = load_alert_contexts(db_session, [a, b])
        candidates = find_correlations(contexts, {a.id, b.id})

        def failing_commit():
            raise SQLAlchemyError("database is locked")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(EventCorrelationPersistenceError):
            persist_correlations(db_session, candidates)

        monkeypatch.undo()
        assert db_session.scalars(select(EventCorrelation)).all() == []


class TestBuildAndPersistForBatch:
    def test_finds_shared_ip_correlation_across_two_alerts_in_one_batch(self, db_session):
        batch = add_batch(db_session)
        add_feature_record(db_session, batch, 1, source_ip="10.0.0.9")
        add_feature_record(db_session, batch, 2, source_ip="10.0.0.9")
        add_alert(db_session, batch, row_number=1, created_at=BASE_TIME)
        add_alert(db_session, batch, row_number=2, created_at=BASE_TIME + timedelta(seconds=5))

        rows = build_and_persist_correlations_for_batch(db_session, batch.id)

        assert any(r.correlation_type == "shared_source_ip" for r in rows)

    def test_no_alerts_in_batch_persists_nothing(self, db_session):
        batch = add_batch(db_session)
        rows = build_and_persist_correlations_for_batch(db_session, batch.id)
        assert rows == []

    def test_is_idempotent_across_repeated_calls(self, db_session):
        batch = add_batch(db_session)
        add_feature_record(db_session, batch, 1, source_ip="10.0.0.9")
        add_feature_record(db_session, batch, 2, source_ip="10.0.0.9")
        add_alert(db_session, batch, row_number=1, created_at=BASE_TIME)
        add_alert(db_session, batch, row_number=2, created_at=BASE_TIME + timedelta(seconds=5))

        build_and_persist_correlations_for_batch(db_session, batch.id)
        second_run_rows = build_and_persist_correlations_for_batch(db_session, batch.id)

        assert second_run_rows == []

    def test_cross_batch_shared_ip_is_found(self, db_session):
        batch_a = add_batch(db_session)
        batch_b = add_batch(db_session)
        add_feature_record(db_session, batch_a, 1, source_ip="203.0.113.7")
        add_feature_record(db_session, batch_b, 1, source_ip="203.0.113.7")
        add_alert(db_session, batch_a, row_number=1, created_at=BASE_TIME)

        build_and_persist_correlations_for_batch(db_session, batch_a.id)
        add_alert(db_session, batch_b, row_number=1, created_at=BASE_TIME + timedelta(seconds=30))
        rows = build_and_persist_correlations_for_batch(db_session, batch_b.id)

        assert any(r.correlation_type == "shared_source_ip" for r in rows)
