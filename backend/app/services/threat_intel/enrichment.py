"""Orchestrates threat-intelligence enrichment for one `Alert`: decides
whether a supported indicator exists for it, serves a cached result
when still fresh, otherwise calls the configured provider — all
best-effort, never interrupting detection or alert generation.

    Alert (+ its MappedFeatureRecord, for IP evidence)
        |
    extract_supported_indicator()  <- only "ip", only when genuinely present
        |
    get_cached_lookup() / provider.lookup_ip()
        |
    upsert_lookup()
        |
    ThreatIntelLookup row

Enrichment is entirely optional and disabled by default
(`settings.threat_intel_enabled = False`), and `enrich_alert` never
raises: a disabled feature, a missing API key, a provider timeout, a
rate limit, or any other unexpected failure all result in a cached
"unavailable" row (or, if persistence itself fails, a logged no-op) —
never an exception that could interrupt `app.services.batch_processor`.
See `backend/README.md`'s "Threat Intelligence" section for the exact
environment variables this reads and what remains unconfigured by
default.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings as default_settings
from app.models.alert import Alert
from app.models.mapped_feature_record import MappedFeatureRecord
from app.models.threat_intel_lookup import ThreatIntelLookup
from app.services.threat_intel.abuseipdb_provider import AbuseIPDBProvider
from app.services.threat_intel.base import ThreatIntelProvider, ThreatIntelResult
from app.services.threat_intel.cache import ThreatIntelPersistenceError, get_cached_lookup, upsert_lookup

logger = logging.getLogger("ai_ids")

_SOURCE_IP_FEATURE = "source_ip_numeric"
_DESTINATION_IP_FEATURE = "destination_ip_numeric"


def _ip_raw_value(features: dict, feature_name: str) -> str | None:
    entry = features.get(feature_name)
    if not isinstance(entry, dict):
        return None
    raw_value = entry.get("raw_value")
    return raw_value if isinstance(raw_value, str) and raw_value else None


def extract_supported_indicator(db: Session, alert: Alert) -> tuple[str, str] | None:
    """Returns `(indicator, "ip")` for the alert's row's source IP, or
    failing that its destination IP, read from the real, persisted
    `MappedFeatureRecord.features[...]["raw_value"]` for that exact
    `(batch_id, row_number)` — never another row's, never fabricated.
    Returns `None` when neither exists (an unsupported-indicator row,
    e.g. an NSL-KDD-style dataset with no IP columns at all) — the
    caller must not call a provider in that case.
    """
    feature_row = db.scalars(
        select(MappedFeatureRecord).where(
            MappedFeatureRecord.batch_id == alert.batch_id, MappedFeatureRecord.row_number == alert.row_number
        )
    ).first()
    if feature_row is None:
        return None

    source_ip = _ip_raw_value(feature_row.features, _SOURCE_IP_FEATURE)
    if source_ip is not None:
        return (source_ip, "ip")

    destination_ip = _ip_raw_value(feature_row.features, _DESTINATION_IP_FEATURE)
    if destination_ip is not None:
        return (destination_ip, "ip")

    return None


def get_default_provider(settings: Settings) -> ThreatIntelProvider | None:
    """Builds the configured provider from `settings`, or returns
    `None` when threat intelligence is disabled, no provider is
    configured, or (for a provider that requires one) no API key is
    set — every one of those is a normal "unavailable" condition the
    caller handles explicitly, never an error."""
    if not settings.threat_intel_enabled:
        return None
    if settings.threat_intel_provider == "abuseipdb":
        if not settings.threat_intel_api_key:
            return None
        return AbuseIPDBProvider(
            settings.threat_intel_api_key,
            timeout_seconds=settings.threat_intel_timeout_seconds,
            max_retries=settings.threat_intel_max_retries,
        )
    return None


def _unavailable_result(indicator: str, provider_name: str, reason: str) -> ThreatIntelResult:
    return ThreatIntelResult(
        indicator=indicator,
        indicator_type="ip",
        provider_name=provider_name,
        response_received=False,
        found=None,
        unavailable_reason=reason,
    )


def enrich_alert(
    db: Session,
    alert: Alert,
    *,
    settings: Settings = default_settings,
    provider: ThreatIntelProvider | None = None,
) -> ThreatIntelLookup | None:
    """Best-effort enrichment for one alert. Returns the resulting
    `ThreatIntelLookup` row, or `None` when enrichment did not apply at
    all (disabled, or no supported indicator for this alert's row) —
    `None` is a normal outcome, never a failure signal by itself.

    `provider`, when supplied, is used instead of
    `get_default_provider(settings)` — the seam tests use to inject a
    fake/mocked provider without touching real configuration or the
    network.
    """
    if not settings.threat_intel_enabled:
        return None

    indicator_info = extract_supported_indicator(db, alert)
    if indicator_info is None:
        return None
    indicator, indicator_type = indicator_info

    active_provider = provider if provider is not None else get_default_provider(settings)
    provider_name = active_provider.name if active_provider is not None else settings.threat_intel_provider

    cached = get_cached_lookup(db, provider_name, indicator, ttl_seconds=settings.threat_intel_cache_ttl_seconds)
    if cached is not None:
        return cached

    if active_provider is None:
        result = _unavailable_result(indicator, provider_name, "missing_credentials")
    else:
        try:
            result = active_provider.lookup_ip(indicator)
        except Exception:
            logger.exception("Threat-intelligence provider %s failed unexpectedly", provider_name)
            result = _unavailable_result(indicator, provider_name, "provider_error")

    try:
        return upsert_lookup(db, result, alert_id=alert.id)
    except ThreatIntelPersistenceError:
        logger.exception("Could not persist a threat-intelligence lookup for alert %s", alert.id)
        return None


def enrich_alerts(
    db: Session,
    alerts: list[Alert],
    *,
    settings: Settings = default_settings,
    provider: ThreatIntelProvider | None = None,
) -> list[ThreatIntelLookup]:
    """Best-effort enrichment for several alerts, one at a time, never
    letting one alert's failure stop the rest. Returns only the rows
    that were actually produced (skips `None`s)."""
    if not settings.threat_intel_enabled:
        return []

    results = []
    for alert in alerts:
        try:
            row = enrich_alert(db, alert, settings=settings, provider=provider)
        except Exception:
            logger.exception("Unexpected error enriching alert %s with threat intelligence", alert.id)
            continue
        if row is not None:
            results.append(row)
    return results


__all__ = [
    "extract_supported_indicator",
    "get_default_provider",
    "enrich_alert",
    "enrich_alerts",
]
