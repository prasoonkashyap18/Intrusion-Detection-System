"""SOC dashboard overview endpoint (Step 45). Read-only; available to
any authenticated role (`ADMINISTRATOR`, `SECURITY_ANALYST`, or
`READ_ONLY_VIEWER`) — viewing dashboard metrics is exactly the kind of
"explicitly permitted security information" a Read-Only Viewer may see.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.common import ErrorResponse
from app.schemas.dashboard import DashboardOverviewResponse, SeverityDistribution
from app.services.dashboard_service import DashboardOverview, get_overview

router = APIRouter(prefix="/dashboard")


@router.get(
    "/overview",
    response_model=DashboardOverviewResponse,
    summary="Real, measured SOC overview metrics",
    description=(
        "Every field is a real COUNT over already-persisted rows — total detections, alert "
        "severity distribution, open-alert count, and active capture sessions. Never an "
        "estimate or a fabricated figure; an empty system returns all zeros, not an error."
    ),
    responses={401: {"model": ErrorResponse, "description": "Not authenticated"}},
)
def get_dashboard_overview(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> DashboardOverviewResponse:
    overview = get_overview(db)
    return DashboardOverviewResponse(
        total_batches=overview.total_batches,
        total_detection_results=overview.total_detection_results,
        total_anomaly_results=overview.total_anomaly_results,
        total_alerts=overview.total_alerts,
        open_alerts=overview.open_alerts,
        alert_severity_distribution=SeverityDistribution(
            low=_count_for(overview, "low"),
            medium=_count_for(overview, "medium"),
            high=_count_for(overview, "high"),
            critical=_count_for(overview, "critical"),
        ),
        active_capture_sessions=overview.active_capture_sessions,
    )


def _count_for(overview: DashboardOverview, severity_value: str) -> int:
    for severity, count in overview.severity_counts.items():
        if severity.value == severity_value:
            return count
    return 0
