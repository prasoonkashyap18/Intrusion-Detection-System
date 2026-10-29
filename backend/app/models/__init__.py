"""Importing this package registers all ORM models on the shared Base's
metadata, so Base.metadata.create_all() (see app/db/database.py) can
discover and create their tables.
"""

from app.models.detection_batch import DetectionBatch
from app.models.model_metadata import ModelMetadata
from app.models.detection_result import DetectionResult
from app.models.mapped_feature_record import MappedFeatureRecord
from app.models.anomaly_result import AnomalyResult
from app.models.hybrid_detection_result import HybridDetectionResult
from app.models.risk_assessment import RiskAssessment
from app.models.detection_explanation import DetectionExplanation

__all__ = [
    "DetectionBatch",
    "ModelMetadata",
    "DetectionResult",
    "MappedFeatureRecord",
    "AnomalyResult",
    "HybridDetectionResult",
    "RiskAssessment",
    "DetectionExplanation",
]
