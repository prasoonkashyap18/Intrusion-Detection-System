"""Tests for app.services.alert_generation: rule-based alert
eligibility, evidence building, persistence, and deduplication.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models.alert import Alert
from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus, Severity
from app.models.hybrid_detection_result import HybridDetectionResult
from app.models.risk_assessment import RiskAssessment
from app.services.alert_generation import (
    AlertGenerationConfig,
    AlertPersistenceError,
    build_alert_draft,
    persist_alerts,
    should_generate_alert,
)
from app.services.explainability import Explanation, build_explanation
from app.services.hybrid_detection import HybridOutcome, HybridSignal, combine_signals
from app.services.risk_scoring import compute_risk_score


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="x.csv", status=ProcessingStatus.PROCESSING, total_records=1)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def add_hybrid_row(db_session, batch, **overrides) -> HybridDetectionResult:
    defaults = dict(
        batch_id=batch.id, row_number=1, supervised_available=True, anomaly_available=True,
        outcome="confirmed_attack", contributing_signals=["supervised:attack", "anomaly:flagged"],
    )
    row = HybridDetectionResult(**{**defaults, **overrides})
    db_session.add(row)
    db_session.commit()
    return row


def add_risk_row(db_session, batch, hybrid_row, **overrides) -> RiskAssessment:
    defaults = dict(
        batch_id=batch.id, row_number=1, hybrid_result_id=hybrid_row.id, risk_score=80.0,
        severity=Severity.CRITICAL, supervised_component=0.9, supervised_component_source="attack_probability",
        anomaly_component=0.7, agreement_bonus_applied=True,
    )
    row = RiskAssessment(**{**defaults, **overrides})
    db_session.add(row)
    db_session.commit()
    return row


def full_pipeline(**signal_overrides):
    defaults = dict(
        row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack",
        attack_probability=0.9, anomaly_available=True, is_anomaly=True, anomaly_score=-0.5,
    )
    signal = HybridSignal(**{**defaults, **signal_overrides})
    outcome_result = combine_signals(signal)
    risk_result = compute_risk_score(outcome_result)
    explanation = build_explanation(outcome_result, risk_result, feature_importance=None)
    return outcome_result, risk_result, explanation


class TestShouldGenerateAlertRules:
    def test_clean_outcome_never_generates_an_alert(self):
        assert should_generate_alert(HybridOutcome.CLEAN, Severity.CRITICAL) is False

    def test_confirmed_attack_always_generates_an_alert_regardless_of_severity(self):
        assert should_generate_alert(HybridOutcome.CONFIRMED_ATTACK, Severity.LOW) is True

    def test_anomaly_only_below_min_severity_does_not_generate_an_alert(self):
        assert should_generate_alert(HybridOutcome.ANOMALY_ONLY, Severity.LOW) is False

    def test_anomaly_only_at_min_severity_generates_an_alert(self):
        assert should_generate_alert(HybridOutcome.ANOMALY_ONLY, Severity.MEDIUM) is True

    def test_supervised_attack_only_above_min_severity_generates_an_alert(self):
        assert should_generate_alert(HybridOutcome.SUPERVISED_ATTACK_ONLY, Severity.HIGH) is True

    def test_custom_config_changes_the_threshold(self):
        config = AlertGenerationConfig(min_severity=Severity.HIGH)
        assert should_generate_alert(HybridOutcome.ANOMALY_ONLY, Severity.MEDIUM, config) is False
        assert should_generate_alert(HybridOutcome.ANOMALY_ONLY, Severity.HIGH, config) is True

    def test_determinism(self):
        first = should_generate_alert(HybridOutcome.SUPERVISED_ATTACK_ONLY, Severity.HIGH)
        second = should_generate_alert(HybridOutcome.SUPERVISED_ATTACK_ONLY, Severity.HIGH)
        assert first == second


class TestBuildAlertDraft:
    def test_draft_references_the_correct_source_rows(self, db_session):
        batch = add_batch(db_session)
        hybrid_row = add_hybrid_row(db_session, batch)
        risk_row = add_risk_row(db_session, batch, hybrid_row)
        outcome_result, risk_result, explanation = full_pipeline()

        draft = build_alert_draft(outcome_result, risk_result, hybrid_row, risk_row, explanation, uuid.uuid4())

        assert draft.batch_id == batch.id
        assert draft.row_number == 1
        assert draft.hybrid_result_id == hybrid_row.id
        assert draft.risk_assessment_id == risk_row.id
        assert draft.severity == risk_result.severity

    def test_draft_evidence_never_fabricates_values(self, db_session):
        batch = add_batch(db_session)
        hybrid_row = add_hybrid_row(db_session, batch)
        risk_row = add_risk_row(db_session, batch, hybrid_row)
        outcome_result, risk_result, explanation = full_pipeline(attack_probability=None)

        draft = build_alert_draft(outcome_result, risk_result, hybrid_row, risk_row, explanation, None)

        assert draft.evidence["explanation"]["measured_values"]["attack_probability"] is None
        assert any(
            "attack probability" in msg for msg in draft.evidence["explanation"]["unavailable_information"]
        )

    def test_title_and_description_contain_no_fabricated_ip_or_indicator(self, db_session):
        batch = add_batch(db_session)
        hybrid_row = add_hybrid_row(db_session, batch)
        risk_row = add_risk_row(db_session, batch, hybrid_row)
        outcome_result, risk_result, explanation = full_pipeline()

        draft = build_alert_draft(outcome_result, risk_result, hybrid_row, risk_row, explanation, None)

        # No IP-shaped or MAC-shaped text should ever appear — this
        # pipeline never had IP evidence to work with at this layer.
        assert "192." not in draft.description
        assert "attack_type" not in draft.description.lower()


class TestPersistence:
    def test_persisted_alert_matches_the_draft(self, db_session):
        batch = add_batch(db_session)
        hybrid_row = add_hybrid_row(db_session, batch)
        risk_row = add_risk_row(db_session, batch, hybrid_row)
        outcome_result, risk_result, explanation = full_pipeline()
        draft = build_alert_draft(outcome_result, risk_result, hybrid_row, risk_row, explanation, None)

        rows = persist_alerts(db_session, [draft])

        assert len(rows) == 1
        assert rows[0].batch_id == batch.id
        assert rows[0].row_number == 1
        assert rows[0].severity == risk_result.severity
        assert rows[0].status.value == "open"

    def test_empty_input_persists_nothing(self, db_session):
        rows = persist_alerts(db_session, [])
        assert rows == []

    def test_duplicate_batch_row_is_rejected(self, db_session):
        batch = add_batch(db_session)
        hybrid_row = add_hybrid_row(db_session, batch)
        risk_row = add_risk_row(db_session, batch, hybrid_row)
        outcome_result, risk_result, explanation = full_pipeline()
        draft = build_alert_draft(outcome_result, risk_result, hybrid_row, risk_row, explanation, None)
        persist_alerts(db_session, [draft])

        with pytest.raises(AlertPersistenceError):
            persist_alerts(db_session, [draft])

        rows = db_session.scalars(select(Alert).where(Alert.batch_id == batch.id)).all()
        assert len(rows) == 1

    def test_commit_failure_rolls_back(self, db_session, monkeypatch):
        batch = add_batch(db_session)
        hybrid_row = add_hybrid_row(db_session, batch)
        risk_row = add_risk_row(db_session, batch, hybrid_row)
        outcome_result, risk_result, explanation = full_pipeline()
        draft = build_alert_draft(outcome_result, risk_result, hybrid_row, risk_row, explanation, None)

        def failing_commit():
            raise SQLAlchemyError("database is locked")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(AlertPersistenceError):
            persist_alerts(db_session, [draft])

        monkeypatch.undo()
        rows = db_session.scalars(select(Alert).where(Alert.batch_id == batch.id)).all()
        assert rows == []
