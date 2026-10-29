"""Tests for app.services.explainability: structured explanations built
strictly from already-computed hybrid/risk values, with genuine (never
fabricated) global feature importance when available.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import joblib
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.models.detection_batch import DetectionBatch
from app.models.detection_explanation import DetectionExplanation
from app.models.enums import ModelStatus, ProcessingStatus, Severity
from app.models.hybrid_detection_result import HybridDetectionResult
from app.models.model_metadata import ModelMetadata
from app.models.risk_assessment import RiskAssessment
from app.services.explainability import (
    ExplanationPersistenceError,
    build_explanation,
    load_global_feature_importance,
    persist_explanations,
)
from app.services.feature_extraction import FEATURE_SCHEMA
from app.services.hybrid_detection import HybridSignal, combine_signals
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


def add_risk_assessment(db_session, batch, hybrid_row, **overrides) -> RiskAssessment:
    defaults = dict(
        batch_id=batch.id, row_number=1, hybrid_result_id=hybrid_row.id, risk_score=80.0,
        severity=Severity.CRITICAL, supervised_component=0.9, supervised_component_source="attack_probability",
        anomaly_component=0.7, agreement_bonus_applied=True,
    )
    row = RiskAssessment(**{**defaults, **overrides})
    db_session.add(row)
    db_session.commit()
    return row


class _FakeModel:
    def __init__(self, importances):
        if importances is not None:
            self.feature_importances_ = importances


def add_model_with_artifact(db_session, tmp_path: Path, importances=None) -> ModelMetadata:
    artifact_path = tmp_path / f"{uuid.uuid4()}.joblib"
    joblib.dump({"model": _FakeModel(importances)}, artifact_path)

    model = ModelMetadata(
        id=uuid.uuid4(), model_name="baseline", model_version="v1", model_type="random_forest",
        status=ModelStatus.READY, artifact_path=str(artifact_path),
    )
    db_session.add(model)
    db_session.commit()
    return model


def full_signal_and_results(**overrides):
    defaults = dict(
        row_number=1, supervised_available=True, predicted_label=1, prediction_name="attack",
        attack_probability=0.9, anomaly_available=True, is_anomaly=True, anomaly_score=-0.5,
    )
    signal = HybridSignal(**{**defaults, **overrides})
    outcome_result = combine_signals(signal)
    risk_result = compute_risk_score(outcome_result)
    return outcome_result, risk_result


class TestLoadGlobalFeatureImportance:
    def test_loads_real_importances_paired_with_feature_schema(self, db_session, tmp_path):
        importances = [1.0 / len(FEATURE_SCHEMA)] * len(FEATURE_SCHEMA)
        model = add_model_with_artifact(db_session, tmp_path, importances=importances)

        result = load_global_feature_importance(db_session, model.id)

        assert result is not None
        assert len(result) == len(FEATURE_SCHEMA)
        assert [entry.feature_name for entry in result] == list(FEATURE_SCHEMA)
        assert all(entry.importance == pytest.approx(importances[0]) for entry in result)

    def test_returns_none_when_model_lacks_feature_importances(self, db_session, tmp_path):
        model = add_model_with_artifact(db_session, tmp_path, importances=None)
        result = load_global_feature_importance(db_session, model.id)
        assert result is None

    def test_returns_none_when_model_id_does_not_exist(self, db_session):
        result = load_global_feature_importance(db_session, uuid.uuid4())
        assert result is None

    def test_returns_none_when_artifact_file_is_missing(self, db_session):
        model = ModelMetadata(
            id=uuid.uuid4(), model_name="missing-artifact", model_version="v1", model_type="random_forest",
            status=ModelStatus.READY, artifact_path="/does/not/exist.joblib",
        )
        db_session.add(model)
        db_session.commit()

        result = load_global_feature_importance(db_session, model.id)
        assert result is None

    def test_returns_none_on_length_mismatch(self, db_session, tmp_path):
        model = add_model_with_artifact(db_session, tmp_path, importances=[0.5, 0.5])  # too short
        result = load_global_feature_importance(db_session, model.id)
        assert result is None


class TestBuildExplanationConsistency:
    def test_measured_values_match_the_computed_outcome_and_risk(self):
        outcome_result, risk_result = full_signal_and_results()

        explanation = build_explanation(outcome_result, risk_result, feature_importance=None)

        assert explanation.measured_values["predicted_label"] == 1
        assert explanation.measured_values["attack_probability"] == pytest.approx(0.9)
        assert explanation.measured_values["is_anomaly"] is True
        assert explanation.measured_values["anomaly_score"] == pytest.approx(-0.5)
        assert explanation.measured_values["hybrid_outcome"] == outcome_result.outcome.value
        assert explanation.measured_values["risk_score"] == pytest.approx(risk_result.risk_score)
        assert explanation.measured_values["severity"] == risk_result.severity.value

    def test_missing_anomaly_result_is_recorded_as_unavailable_not_omitted(self):
        outcome_result, risk_result = full_signal_and_results(anomaly_available=False, is_anomaly=None, anomaly_score=None)

        explanation = build_explanation(outcome_result, risk_result, feature_importance=None)

        assert any("anomaly detection result was available" in msg for msg in explanation.unavailable_information)

    def test_missing_attack_probability_is_recorded_as_unavailable_and_rule_noted(self):
        outcome_result, risk_result = full_signal_and_results(attack_probability=None)

        explanation = build_explanation(outcome_result, risk_result, feature_importance=None)

        assert any("attack probability" in msg for msg in explanation.unavailable_information)
        assert any("not a fabricated probability" in msg for msg in explanation.rule_based_explanations)

    def test_feature_importance_none_is_recorded_as_unavailable(self):
        outcome_result, risk_result = full_signal_and_results()
        explanation = build_explanation(outcome_result, risk_result, feature_importance=None)
        assert explanation.feature_importance is None
        assert explanation.feature_importance_scope is None
        assert any("Feature-level explanation is unavailable" in msg for msg in explanation.unavailable_information)

    def test_feature_importance_present_is_labeled_global_scope(self):
        from app.services.explainability import FeatureImportanceEntry

        outcome_result, risk_result = full_signal_and_results()
        feature_importance = [FeatureImportanceEntry(feature_name=FEATURE_SCHEMA[0], importance=0.5)]

        explanation = build_explanation(outcome_result, risk_result, feature_importance=feature_importance)

        assert explanation.feature_importance == feature_importance
        assert explanation.feature_importance_scope == "global_feature_importance"
        assert not any("Feature-level explanation is unavailable" in msg for msg in explanation.unavailable_information)

    def test_rule_based_explanation_reflects_the_outcome(self):
        outcome_result, risk_result = full_signal_and_results()
        explanation = build_explanation(outcome_result, risk_result, feature_importance=None)
        assert any("both" in msg.lower() for msg in explanation.rule_based_explanations)

    def test_no_fabricated_per_row_causal_claims(self):
        # The explanation must never claim a specific feature "caused"
        # this row's outcome -- only a genuine global importance, when
        # available, ever appears, and it is always labeled as such.
        outcome_result, risk_result = full_signal_and_results()
        explanation = build_explanation(outcome_result, risk_result, feature_importance=None)
        as_dict = explanation.as_dict()
        serialized = str(as_dict)
        assert "caused" not in serialized.lower()
        assert "because this row's" not in serialized.lower()


class TestPersistence:
    def test_persisted_row_links_batch_hybrid_and_risk(self, db_session):
        batch = add_batch(db_session)
        hybrid_row = add_hybrid_row(db_session, batch)
        risk_row = add_risk_assessment(db_session, batch, hybrid_row)
        outcome_result, risk_result = full_signal_and_results()
        explanation = build_explanation(outcome_result, risk_result, feature_importance=None)

        rows = persist_explanations(db_session, batch.id, [(explanation, hybrid_row, risk_row.id)])

        assert len(rows) == 1
        assert rows[0].batch_id == batch.id
        assert rows[0].row_number == 1
        assert rows[0].hybrid_result_id == hybrid_row.id
        assert rows[0].risk_assessment_id == risk_row.id
        assert "measured_values" in rows[0].explanation
        assert rows[0].explanation["measured_values"]["hybrid_outcome"] == outcome_result.outcome.value

    def test_empty_input_persists_nothing(self, db_session):
        batch = add_batch(db_session)
        rows = persist_explanations(db_session, batch.id, [])
        assert rows == []

    def test_duplicate_persistence_is_rejected(self, db_session):
        batch = add_batch(db_session)
        hybrid_row = add_hybrid_row(db_session, batch)
        risk_row = add_risk_assessment(db_session, batch, hybrid_row)
        outcome_result, risk_result = full_signal_and_results()
        explanation = build_explanation(outcome_result, risk_result, feature_importance=None)

        persist_explanations(db_session, batch.id, [(explanation, hybrid_row, risk_row.id)])

        with pytest.raises(ExplanationPersistenceError):
            persist_explanations(db_session, batch.id, [(explanation, hybrid_row, risk_row.id)])

        rows = db_session.scalars(select(DetectionExplanation).where(DetectionExplanation.batch_id == batch.id)).all()
        assert len(rows) == 1

    def test_commit_failure_rolls_back(self, db_session, monkeypatch):
        batch = add_batch(db_session)
        hybrid_row = add_hybrid_row(db_session, batch)
        risk_row = add_risk_assessment(db_session, batch, hybrid_row)
        outcome_result, risk_result = full_signal_and_results()
        explanation = build_explanation(outcome_result, risk_result, feature_importance=None)

        def failing_commit():
            raise SQLAlchemyError("database is locked")

        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(ExplanationPersistenceError):
            persist_explanations(db_session, batch.id, [(explanation, hybrid_row, risk_row.id)])

        monkeypatch.undo()
        rows = db_session.scalars(select(DetectionExplanation).where(DetectionExplanation.batch_id == batch.id)).all()
        assert rows == []
