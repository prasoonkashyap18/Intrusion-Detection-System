"""Modular threat-intelligence integration (Step 40).

Provider-independent interface in `base.py`; a configurable AbuseIPDB
adapter in `abuseipdb_provider.py` behind that interface; a persisted
cache in `cache.py`; and the best-effort orchestration callers actually
use in `enrichment.py`. See `backend/README.md`'s "Threat Intelligence"
section for configuration, supported indicators, and limitations.
"""

from __future__ import annotations

from app.services.threat_intel.base import ThreatIntelProvider, ThreatIntelResult, ThreatIntelUnavailableError
from app.services.threat_intel.enrichment import enrich_alert, enrich_alerts, get_default_provider

__all__ = [
    "ThreatIntelProvider",
    "ThreatIntelResult",
    "ThreatIntelUnavailableError",
    "enrich_alert",
    "enrich_alerts",
    "get_default_provider",
]
