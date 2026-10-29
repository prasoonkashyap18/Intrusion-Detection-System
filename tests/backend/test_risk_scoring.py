"""Tests for app.services.risk_scoring: a deterministic [0, 100] risk
score + Severity from a HybridOutcomeResult, with explicit, non-zero
handling of missing signals.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus, Severity
from app.models.hybrid_detection_result import HybridDetectionResult
from app.models.risk_assessment import RiskAssessment
from app.services.hybrid_detection import HybridOutcomeResult, HybridSignal, combine_signals
from app.services.risk_scoring import (
    RiskAssessmentPersistenceError,
    RiskScoringConfig,
    RiskScoringError,
    compute_risk_score,
    normalize_anomaly_score,
    persist_risk_assessments,
)


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="x.csv", status=ProcessingStatus.PROCESSING, total_records=1)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def add_hybrid_row(db_session, batch, **overrides) -> HybridDetectionResult:
    defaults = dict(
        batch_id=batch.id,
        row_number=1,
        supervised_available=True,
        anomaly_available=True,
        outcome="confirmed_attack",
        contributing_signals=["supervised:attack", "anomaly:flagged"],
    )
    row = HybridDetectionResult(**{**defaults, **overrides})
    db_session.add(row)
    db_session.commit()
    return row


def make_outcome_result(signal: HybridSignal) -> HybridOutcomeResult:
    return combine_signals(signal)


class TestNormalizeAnomalyScore:
    def test_zero_maps_to_half(self):
        assert normalize_anomaly_score(0.0) == pytest.approx(0.5)

    def test_very_negative_score_approaches_one(self):
        assert normalize_anomaly_score(-50.0) > 0.999

    def test_very_positive_score_approaches_zero(self):
        assert normalize_anomaly_score(50.0) < 0.001

    def test_monotonically_decreasing(self):
        values = [normalize_anomaly_score(s) for s in (-5.0, -1.0, 0.0, 1.0, 5.0)]
        assert values == sorted(values, reverse=True)

    def test_rejects_non_positive_scale(self):
        config = RiskScoringConfig(anomaly_score_scale=0.0)
        with pytest.raises(RiskScoringError):
            normalize_anomaly_score(1.0, config)


class TestComputeRiskScoreBounds:
    def test_score_is_always_within_zero_to_one_hundred(self):
        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack",
            attack_probability=1.0, anomaly_available=True, is_anomaly=True, anomaly_score=-100.0,
        )
        result = compute_risk_score(make_outcome_result(signal))
        assert 0.0 <= result.risk_score <= 100.0

    def test_maximal_signals_produce_a_high_score(self):
        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack",
            attack_probability=1.0, anomaly_available=True, is_anomaly=True, anomaly_score=-100.0,
        )
        result = compute_risk_score(make_outcome_result(signal))
        assert result.risk_score > 90.0
        assert result.severity == Severity.CRITICAL

    def test_minimal_signals_produce_a_low_score(self):
        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=0, prediction_name="benign",
            attack_probability=0.0, anomaly_available=True, is_anomaly=False, anomaly_score=100.0,
        )
        result = compute_risk_score(make_outcome_result(signal))
        assert result.risk_score < 10.0
        assert result.severity == Severity.LOW

    def test_score_never_goes_negative_or_above_bound_at_extreme_config(self):
        config = RiskScoringConfig(supervised_weight=5.0, anomaly_weight=5.0, agreement_bonus_weight=5.0)
        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack",
            attack_probability=1.0, anomaly_available=True, is_anomaly=True, anomaly_score=-100.0,
        )
        result = compute_risk_score(make_outcome_result(signal), config)
        assert 0.0 <= result.risk_score <= 100.0


class TestSeverityThresholds:
    def test_severity_boundaries_use_configured_thresholds(self):
        config = RiskScoringConfig(low_threshold=20.0, medium_threshold=40.0, high_threshold=60.0)
        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=0, prediction_name="benign", attack_probability=0.5
        )
        result = compute_risk_score(make_outcome_result(signal), config)
        assert result.risk_score == pytest.approx(50.0)
        assert result.severity == Severity.HIGH  # 40 <= 50 < 60

    def test_rejects_non_increasing_thresholds(self):
        config = RiskScoringConfig(low_threshold=50.0, medium_threshold=40.0, high_threshold=60.0)
        signal = HybridSignal(row_number=1, supervised_available=True, predicted_label=0, prediction_name="benign", attack_probability=0.5)
        with pytest.raises(RiskScoringError):
            compute_risk_score(make_outcome_result(signal), config)


class TestMissingSignalHandling:
    def test_missing_anomaly_signal_is_not_treated_as_zero_risk(self):
        # Two rows with the same supervised signal but one also has a
        # highly anomalous (very risky) anomaly score. If a missing
        # anomaly value were silently treated as 0 risk, the row missing
        # anomaly data would score identically to one with a "not at all
        # anomalous" (very high, i.e. normal) anomaly_score -- this test
        # proves that is NOT what happens: the missing-signal row is
        # scored purely on its available (supervised) signal, renormalized,
        # not diluted by a phantom zero.
        missing_signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack", attack_probability=0.5
        )
        normal_anomaly_signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack", attack_probability=0.5,
            anomaly_available=True, is_anomaly=False, anomaly_score=100.0,  # strongly "normal"
        )
        missing_result = compute_risk_score(make_outcome_result(missing_signal))
        present_result = compute_risk_score(make_outcome_result(normal_anomaly_signal))
        # If missing were treated as a 0-risk anomaly component, the
        # missing-signal score would be LOWER than the real-but-near-zero
        # anomaly component score. Instead, with the anomaly weight
        # excluded entirely, the missing-signal score should equal
        # exactly the supervised-only contribution (50.0).
        assert missing_result.risk_score == pytest.approx(50.0)
        assert missing_result.components.anomaly_component is None
        assert present_result.components.anomaly_component is not None

    def test_missing_attack_probability_falls_back_to_predicted_label_not_fabricated_probability(self):
        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack",
            attack_probability=None,
        )
        result = compute_risk_score(make_outcome_result(signal))
        assert result.components.supervised_component == 1.0
        assert result.components.supervised_component_source == "predicted_label"

    def test_both_signals_missing_raises_rather_than_fabricating_a_score(self):
        signal = HybridSignal(row_number=1, supervised_available=False, anomaly_available=False)
        with pytest.raises(RiskScoringError):
            compute_risk_score(make_outcome_result(signal))

    def test_supervised_unavailable_scores_purely_on_anomaly(self):
        signal = HybridSignal(
            row_number=1, supervised_available=False, anomaly_available=True, is_anomaly=True, anomaly_score=-100.0,
        )
        result = compute_risk_score(make_outcome_result(signal))
        assert result.components.supervised_component is None
        assert result.components.supervised_component_source == "unavailable"
        assert result.risk_score > 90.0


class TestAgreementBonus:
    def test_confirmed_attack_outcome_applies_the_bonus(self):
        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack", attack_probability=0.5,
            anomaly_available=True, is_anomaly=True, anomaly_score=0.0,
        )
        result = compute_risk_score(make_outcome_result(signal))
        assert result.components.agreement_bonus_applied is True

    def test_non_confirmed_outcome_does_not_apply_the_bonus(self):
        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack", attack_probability=0.5,
            anomaly_available=True, is_anomaly=False, anomaly_score=0.0,
        )
        result = compute_risk_score(make_outcome_result(signal))
        assert result.components.agreement_bonus_applied is False

    def test_confirmed_attack_scores_higher_than_supervised_attack_only_with_otherwise_equal_inputs(self):
        confirmed = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack", attack_probability=0.5,
            anomaly_available=True, is_anomaly=True, anomaly_score=0.0,
        )
        attack_only = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack", attack_probability=0.5,
            anomaly_available=True, is_anomaly=False, anomaly_score=0.0,
        )
        confirmed_result = compute_risk_score(make_outcome_result(confirmed))
        attack_only_result = compute_risk_score(make_outcome_result(attack_only))
        assert confirmed_result.risk_score > attack_only_result.risk_score


class TestDeterminism:
    def test_identical_inputs_and_config_produce_identical_scores(self):
        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack", attack_probability=0.77,
            anomaly_available=True, is_anomaly=True, anomaly_score=-0.42,
        )
        outcome_result = make_outcome_result(signal)
        first = compute_risk_score(outcome_result)
        second = compute_risk_score(outcome_result)
        assert first.risk_score == second.risk_score
        assert first.severity == second.severity


class TestPersistence:
    def test_persisted_row_matches_computed_result(self, db_session):
        batch = add_batch(db_session)
        hybrid_row = add_hybrid_row(db_session, batch)
        signal = HybridSignal(
            row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack", attack_probability=0.9,
            anomaly_available=True, is_anomaly=True, anomaly_score=-0.5,
        )
        result = compute_risk_score(make_outcome_result(signal))

        rows = persist_risk_assessments(db_session, batch.id, [(result, hybrid_row)])

        assert len(rows) == 1
        assert rows[0].batch_id == batch.id
        assert rows[0].row_number == 1
        assert rows[0].hybrid_result_id == hybrid_row.id
        assert rows[0].risk_score == pytest.approx(result.risk_score)
        assert rows[0].severity == result.severity

    def test_empty_input_persists_nothing(self, db_session):
        batch = add_batch(db_session)
        rows = persist_risk_assessments(db_session, batch.id, [])
        assert rows == []

    def test_duplicate_persistence_is_rejected(self, db_session):
        batch = add_batch(db_session)
        hybrid_row = add_hybrid_row(db_session, batch)
        signal = HybridSignal(row_number=1, supervised_available=True, predicted_label=0, prediction_name="benign", attack_probability=0.1)
        result = compute_risk_score(make_outcome_result(signal))
        persist_risk_assessments(db_session, batch.id, [(result, hybrid_row)])

        with pytest.raises(RiskAssessmentPersistenceError):
            persist_risk_assessments(db_session, batch.id, [(result, hybrid_row)])

        rows = db_session.scalars(select(RiskAssessment).where(RiskAssessment.batch_id == batch.id)).all()
        assert len(rows) == 1

    def test_commit_failure_rolls_back(self, db_session, monkeypatch):
        batch = add_batch(db_session)
        hybrid_row = add_hybrid_row(db_session, batch)
        signal = HybridSignal(row_number=1, supervised_available=True, predicted_label=0, prediction_name="benign", attack_probability=0.1)
        result = compute_risk_score(make_outcome_result(signal))

        def failing_commit():
            raise SQLAlchemyError("database is locked")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(RiskAssessmentPersistenceError):
            persist_risk_assessments(db_session, batch.id, [(result, hybrid_row)])

        monkeypatch.undo()
        rows = db_session.scalars(select(RiskAssessment).where(RiskAssessment.batch_id == batch.id)).all()
        assert rows == []
