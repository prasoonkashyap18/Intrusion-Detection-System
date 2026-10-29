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
from app.models.alert import Alert
from app.models.alert_status_history import AlertStatusHistory
from app.models.event_correlation import EventCorrelation
from app.models.attack_chain import AttackChain, AttackChainEvent
from app.models.threat_intel_lookup import ThreatIntelLookup
from app.models.pcap_capture_metadata import PcapCaptureMetadata
from app.models.capture_session import CaptureSession
from app.models.user import User
from app.models.user_session import UserSession

__all__ = [
    "DetectionBatch",
    "ModelMetadata",
    "DetectionResult",
    "MappedFeatureRecord",
    "AnomalyResult",
    "HybridDetectionResult",
    "RiskAssessment",
    "DetectionExplanation",
    "Alert",
    "AlertStatusHistory",
    "EventCorrelation",
    "AttackChain",
    "AttackChainEvent",
    "ThreatIntelLookup",
    "PcapCaptureMetadata",
    "CaptureSession",
    "User",
    "UserSession",
]
