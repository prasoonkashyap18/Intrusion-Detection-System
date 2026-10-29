"""SOC dashboard overview metrics (Step 45): plain aggregate `COUNT`
queries over already-persisted rows — this module performs no
analysis of its own and never estimates, samples, or fabricates a
number. Every value `get_overview` returns is the real result of a
`SELECT COUNT(...)` against the exact tables the rest of this
application already writes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.models.alert import Alert
from app.models.anomaly_result import AnomalyResult
from app.models.capture_session import CaptureSession
from app.models.detection_batch import DetectionBatch
from app.models.detection_result import DetectionResult
from app.models.enums import AlertStatus, CaptureSessionStatus, Severity

logger = logging.getLogger("ai_ids")

_OPEN_ALERT_STATUSES = (AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED, AlertStatus.INVESTIGATING)


@dataclass(frozen=True)
class DashboardOverview:
    total_batches: int
    total_detection_results: int
    total_anomaly_results: int
    total_alerts: int
    open_alerts: int
    severity_counts: dict[Severity, int]
    active_capture_sessions: int


def get_overview(db: Session) -> DashboardOverview:
    """Returns real, measured counts across the detection/alert/
    capture tables. Raises `ApiException` 500 on a database failure —
    never returns a partial or zero-filled result silently."""
    try:
        total_batches = db.scalar(select(func.count()).select_from(DetectionBatch)) or 0
        total_detection_results = db.scalar(select(func.count()).select_from(DetectionResult)) or 0
        total_anomaly_results = db.scalar(select(func.count()).select_from(AnomalyResult)) or 0
        total_alerts = db.scalar(select(func.count()).select_from(Alert)) or 0
        open_alerts = (
            db.scalar(select(func.count()).select_from(Alert).where(Alert.status.in_(_OPEN_ALERT_STATUSES))) or 0
        )

        severity_rows = db.execute(select(Alert.severity, func.count()).group_by(Alert.severity)).all()
        severity_counts = {severity: 0 for severity in Severity}
        for severity, count in severity_rows:
            severity_counts[severity] = count

        active_capture_sessions = (
            db.scalar(
                select(func.count()).select_from(CaptureSession).where(
                    CaptureSession.status == CaptureSessionStatus.RUNNING
                )
            )
            or 0
        )
    except SQLAlchemyError:
        logger.exception("Failed to compute dashboard overview")
        raise ApiException(500, "dashboard_unavailable", "Unable to load dashboard metrics.") from None

    return DashboardOverview(
        total_batches=total_batches,
        total_detection_results=total_detection_results,
        total_anomaly_results=total_anomaly_results,
        total_alerts=total_alerts,
        open_alerts=open_alerts,
        severity_counts=severity_counts,
        active_capture_sessions=active_capture_sessions,
    )


__all__ = ["DashboardOverview", "get_overview"]
