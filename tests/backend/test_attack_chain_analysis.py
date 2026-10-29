"""Tests for app.services.attack_chain_analysis: connected-component
construction, classification, deterministic ordering, traceability,
deduplication, and alert-lifecycle isolation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models.alert import Alert
from app.models.attack_chain import AttackChain
from app.models.detection_batch import DetectionBatch
from app.models.enums import AlertStatus, ChainClassification, CorrelationStrength, ProcessingStatus, Severity
from app.models.event_correlation import EventCorrelation
from app.services.attack_chain_analysis import (
    AttackChainConfig,
    AttackChainPersistenceError,
    ChainComponent,
    build_and_persist_chains_for_batch,
    classify_chain,
    compute_member_signature,
    find_connected_components,
    persist_chains,
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


def add_edge(db_session, a: Alert, b: Alert, *, strength=CorrelationStrength.STRONG, correlation_type="shared_source_ip") -> EventCorrelation:
    first, second = (a, b) if str(a.id) < str(b.id) else (b, a)
    edge = EventCorrelation(
        first_alert_id=first.id, second_alert_id=second.id, correlation_type=correlation_type,
        strength=strength, evidence={},
    )
    db_session.add(edge)
    db_session.commit()
    return edge


class TestComputeMemberSignature:
    def test_is_order_independent(self):
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        assert compute_member_signature([a, b, c]) == compute_member_signature([c, a, b])

    def test_different_members_yield_different_signatures(self):
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        assert compute_member_signature([a, b]) != compute_member_signature([a, c])

    def test_deterministic_across_calls(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        assert compute_member_signature([a, b]) == compute_member_signature([a, b])


class TestFindConnectedComponents:
    def test_two_alerts_joined_by_an_edge_form_one_component(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        edge = _fake_edge(a, b, CorrelationStrength.STRONG, "shared_source_ip")

        components = find_connected_components([a, b], [edge])

        assert len(components) == 1
        assert set(components[0].member_alert_ids) == {a, b}

    def test_unconnected_alerts_form_no_component(self):
        a, b = uuid.uuid4(), uuid.uuid4()

        components = find_connected_components([a, b], [])

        assert components == []

    def test_transitively_connected_alerts_form_one_component(self):
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        edges = [
            _fake_edge(a, b, CorrelationStrength.WEAK, "same_batch_proximity"),
            _fake_edge(b, c, CorrelationStrength.WEAK, "same_batch_proximity"),
        ]

        components = find_connected_components([a, b, c], edges)

        assert len(components) == 1
        assert set(components[0].member_alert_ids) == {a, b, c}

    def test_min_chain_size_excludes_a_singleton(self):
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        edge = _fake_edge(a, b, CorrelationStrength.STRONG, "shared_source_ip")
        # c has no edge at all -- it must never appear in any component.

        components = find_connected_components([a, b, c], [edge])

        assert len(components) == 1
        all_members = {m for comp in components for m in comp.member_alert_ids}
        assert c not in all_members

    def test_component_has_strong_edge_flag_is_accurate(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        strong_edge = _fake_edge(a, b, CorrelationStrength.STRONG, "shared_source_ip")
        weak_only_components = find_connected_components(
            [a, b], [_fake_edge(a, b, CorrelationStrength.WEAK, "same_batch_proximity")]
        )
        strong_components = find_connected_components([a, b], [strong_edge])

        assert weak_only_components[0].has_strong_edge is False
        assert strong_components[0].has_strong_edge is True

    def test_deterministic_ordering_of_components_and_members(self):
        a, b, c, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        edges = [_fake_edge(a, b, CorrelationStrength.STRONG, "shared_source_ip"), _fake_edge(c, d, CorrelationStrength.STRONG, "shared_source_ip")]

        first = find_connected_components([a, b, c, d], edges)
        second = find_connected_components([d, c, b, a], list(reversed(edges)))

        assert [c.member_alert_ids for c in first] == [c.member_alert_ids for c in second]


class TestClassifyChain:
    def test_strong_edge_present_is_corroborated(self):
        component = ChainComponent(member_alert_ids=(uuid.uuid4(),), correlation_types=("shared_source_ip",), has_strong_edge=True)
        assert classify_chain(component) == ChainClassification.CORROBORATED

    def test_only_weak_edges_is_heuristic(self):
        component = ChainComponent(member_alert_ids=(uuid.uuid4(),), correlation_types=("same_batch_proximity",), has_strong_edge=False)
        assert classify_chain(component) == ChainClassification.HEURISTIC


class TestPersistChains:
    def test_persists_a_new_chain_with_ordered_events(self, db_session):
        batch = add_batch(db_session)
        a = add_alert(db_session, batch, row_number=1, created_at=BASE_TIME + timedelta(seconds=10))
        b = add_alert(db_session, batch, row_number=2, created_at=BASE_TIME)
        add_edge(db_session, a, b)

        component = ChainComponent(member_alert_ids=(a.id, b.id), correlation_types=("shared_source_ip",), has_strong_edge=True)
        rows = persist_chains(db_session, [component], {a.id: a, b.id: b})

        assert len(rows) == 1
        chain = rows[0]
        assert chain.classification == ChainClassification.CORROBORATED
        ordered_alert_ids = [e.alert_id for e in sorted(chain.events, key=lambda e: e.sequence_index)]
        assert ordered_alert_ids == [b.id, a.id]  # b.created_at is earlier

    def test_empty_input_persists_nothing(self, db_session):
        assert persist_chains(db_session, [], {}) == []

    def test_duplicate_member_set_is_not_persisted_twice(self, db_session):
        batch = add_batch(db_session)
        a = add_alert(db_session, batch, row_number=1)
        b = add_alert(db_session, batch, row_number=2)
        add_edge(db_session, a, b)
        component = ChainComponent(member_alert_ids=(a.id, b.id), correlation_types=("shared_source_ip",), has_strong_edge=True)

        persist_chains(db_session, [component], {a.id: a, b.id: b})
        second_attempt_rows = persist_chains(db_session, [component], {a.id: a, b.id: b})

        assert second_attempt_rows == []
        assert len(db_session.scalars(select(AttackChain)).all()) == 1

    def test_commit_failure_rolls_back(self, db_session, monkeypatch):
        batch = add_batch(db_session)
        a = add_alert(db_session, batch, row_number=1)
        b = add_alert(db_session, batch, row_number=2)
        component = ChainComponent(member_alert_ids=(a.id, b.id), correlation_types=("shared_source_ip",), has_strong_edge=True)

        def failing_commit():
            raise SQLAlchemyError("database is locked")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(AttackChainPersistenceError):
            persist_chains(db_session, [component], {a.id: a, b.id: b})

        monkeypatch.undo()
        assert db_session.scalars(select(AttackChain)).all() == []


class TestBuildAndPersistForBatch:
    def test_builds_a_chain_from_a_correlated_pair_in_the_same_batch(self, db_session):
        batch = add_batch(db_session)
        a = add_alert(db_session, batch, row_number=1)
        b = add_alert(db_session, batch, row_number=2)
        add_edge(db_session, a, b)

        chains = build_and_persist_chains_for_batch(db_session, batch.id)

        assert len(chains) == 1
        assert {e.alert_id for e in chains[0].events} == {a.id, b.id}

    def test_no_alerts_persists_nothing(self, db_session):
        batch = add_batch(db_session)
        assert build_and_persist_chains_for_batch(db_session, batch.id) == []

    def test_uncorrelated_alerts_form_no_chain(self, db_session):
        batch = add_batch(db_session)
        add_alert(db_session, batch, row_number=1)
        add_alert(db_session, batch, row_number=2)

        chains = build_and_persist_chains_for_batch(db_session, batch.id)

        assert chains == []

    def test_repeated_analysis_of_the_same_evidence_is_idempotent(self, db_session):
        batch = add_batch(db_session)
        a = add_alert(db_session, batch, row_number=1)
        b = add_alert(db_session, batch, row_number=2)
        add_edge(db_session, a, b)

        build_and_persist_chains_for_batch(db_session, batch.id)
        second_run_chains = build_and_persist_chains_for_batch(db_session, batch.id)

        assert second_run_chains == []
        assert len(db_session.scalars(select(AttackChain)).all()) == 1

    def test_chain_expands_across_batches_via_correlation(self, db_session):
        batch_a = add_batch(db_session)
        batch_b = add_batch(db_session)
        a = add_alert(db_session, batch_a, row_number=1)
        b = add_alert(db_session, batch_b, row_number=1)
        add_edge(db_session, a, b)

        chains = build_and_persist_chains_for_batch(db_session, batch_a.id)

        assert len(chains) == 1
        assert {e.alert_id for e in chains[0].events} == {a.id, b.id}


class TestAlertLifecycleIsolation:
    def test_chain_analysis_never_changes_alert_status(self, db_session):
        batch = add_batch(db_session)
        a = add_alert(db_session, batch, row_number=1, status=AlertStatus.OPEN)
        b = add_alert(db_session, batch, row_number=2, status=AlertStatus.OPEN)
        add_edge(db_session, a, b)

        build_and_persist_chains_for_batch(db_session, batch.id)

        db_session.refresh(a)
        db_session.refresh(b)
        assert a.status == AlertStatus.OPEN
        assert b.status == AlertStatus.OPEN


def _fake_edge(a: uuid.UUID, b: uuid.UUID, strength: CorrelationStrength, correlation_type: str):
    first, second = (a, b) if str(a) < str(b) else (b, a)

    class _FakeEdge:
        def __init__(self):
            self.id = uuid.uuid4()
            self.first_alert_id = first
            self.second_alert_id = second
            self.strength = strength
            self.correlation_type = correlation_type

    return _FakeEdge()
