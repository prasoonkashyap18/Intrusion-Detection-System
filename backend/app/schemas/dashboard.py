"""API schema for the SOC dashboard overview endpoint (Step 45).

Every field here is a real, measured count from persisted rows —
`app.services.dashboard_service` never estimates or fabricates one.
"""

from __future__ import annotations

from pydantic import BaseModel


class SeverityDistribution(BaseModel):
    low: int
    medium: int
    high: int
    critical: int


class DashboardOverviewResponse(BaseModel):
    total_batches: int
    total_detection_results: int
    total_anomaly_results: int
    total_alerts: int
    open_alerts: int
    """Alerts whose status is `open`, `acknowledged`, or `investigating`
    — i.e. not yet `resolved`/`false_positive`."""
    alert_severity_distribution: SeverityDistribution
    active_capture_sessions: int
