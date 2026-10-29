"""Tests for app.services.threat_intel: provider normalization,
positive/negative results, timeouts, rate limits, missing credentials,
caching, and provider failures. Uses `httpx2.MockTransport` throughout
-- no test here depends on live API credentials or network access.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx2 as httpx
import pytest

from app.core.config import Settings
from app.models.alert import Alert
from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus, Severity
from app.models.mapped_feature_record import MappedFeatureRecord
from app.models.threat_intel_lookup import ThreatIntelLookup
from app.services.threat_intel.abuseipdb_provider import AbuseIPDBProvider
from app.services.threat_intel.base import ThreatIntelResult
from app.services.threat_intel.cache import get_cached_lookup, upsert_lookup
from app.services.threat_intel.enrichment import enrich_alert, enrich_alerts, extract_supported_indicator, get_default_provider


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="x.csv", status=ProcessingStatus.PROCESSING, total_records=1)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


def add_alert(db_session, batch, **overrides) -> Alert:
    defaults = dict(batch_id=batch.id, row_number=1, title="t", description="d", severity=Severity.HIGH, evidence={})
    alert = Alert(**{**defaults, **overrides})
    db_session.add(alert)
    db_session.commit()
    return alert


def add_feature_record(db_session, batch, row_number, *, source_ip=None) -> MappedFeatureRecord:
    features = {}
    if source_ip:
        features["source_ip_numeric"] = {"value": None, "status": "present", "source_column": "x", "raw_value": source_ip}
    row = MappedFeatureRecord(
        batch_id=batch.id, row_number=row_number, feature_schema_version="1",
        dataset_schema="nsl-kdd-style", features=features, unknown_fields={},
    )
    db_session.add(row)
    db_session.commit()
    return row


def make_settings(**overrides) -> Settings:
    defaults = dict(
        threat_intel_enabled=True, threat_intel_provider="abuseipdb", threat_intel_api_key="test-key",
        threat_intel_timeout_seconds=1.0, threat_intel_max_retries=1, threat_intel_cache_ttl_seconds=3600.0,
    )
    return Settings(**{**defaults, **overrides})


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def abuseipdb_json_handler(score: float):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"ipAddress": "1.2.3.4", "abuseConfidenceScore": score, "totalReports": 3}})
    return handler


class TestAbuseIPDBProviderPositiveResult:
    def test_a_nonzero_score_is_a_found_match(self):
        provider = AbuseIPDBProvider("key", client=mock_client(abuseipdb_json_handler(87.0)))
        result = provider.lookup_ip("1.2.3.4")

        assert result.response_received is True
        assert result.found is True
        assert result.confidence == pytest.approx(0.87)
        assert result.reputation == "malicious"
        assert result.raw_result["totalReports"] == 3
        assert result.unavailable_reason is None

    def test_reference_url_points_at_the_provider(self):
        provider = AbuseIPDBProvider("key", client=mock_client(abuseipdb_json_handler(50.0)))
        result = provider.lookup_ip("1.2.3.4")
        assert "abuseipdb.com" in result.reference_url
        assert "1.2.3.4" in result.reference_url


class TestAbuseIPDBProviderNegativeResult:
    def test_a_zero_score_is_a_confirmed_negative_not_unavailable(self):
        provider = AbuseIPDBProvider("key", client=mock_client(abuseipdb_json_handler(0.0)))
        result = provider.lookup_ip("1.2.3.4")

        assert result.response_received is True
        assert result.found is False
        assert result.reputation == "clean"
        assert result.unavailable_reason is None


class TestAbuseIPDBProviderFailureModes:
    def test_timeout_is_unavailable_with_reason_timeout(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        provider = AbuseIPDBProvider("key", max_retries=0, client=mock_client(handler))
        result = provider.lookup_ip("1.2.3.4")

        assert result.response_received is False
        assert result.found is None
        assert result.unavailable_reason == "timeout"

    def test_rate_limit_is_unavailable_with_reason_rate_limited(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "too many requests"})

        provider = AbuseIPDBProvider("key", client=mock_client(handler))
        result = provider.lookup_ip("1.2.3.4")

        assert result.response_received is False
        assert result.unavailable_reason == "rate_limited"

    def test_server_error_is_unavailable_with_reason_provider_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={})

        provider = AbuseIPDBProvider("key", client=mock_client(handler))
        result = provider.lookup_ip("1.2.3.4")

        assert result.response_received is False
        assert result.unavailable_reason == "provider_error"

    def test_malformed_response_is_unavailable_not_a_crash(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "shape"})

        provider = AbuseIPDBProvider("key", client=mock_client(handler))
        result = provider.lookup_ip("1.2.3.4")

        assert result.response_received is False
        assert result.unavailable_reason == "provider_error"

    def test_retries_once_on_timeout_then_succeeds(self):
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise httpx.TimeoutException("timed out")
            return httpx.Response(200, json={"data": {"abuseConfidenceScore": 10.0}})

        provider = AbuseIPDBProvider("key", max_retries=1, client=mock_client(handler))
        result = provider.lookup_ip("1.2.3.4")

        assert attempts["count"] == 2
        assert result.response_received is True


class TestExtractSupportedIndicator:
    def test_returns_source_ip_when_present(self, db_session):
        batch = add_batch(db_session)
        add_feature_record(db_session, batch, 1, source_ip="10.0.0.1")
        alert = add_alert(db_session, batch, row_number=1)

        result = extract_supported_indicator(db_session, alert)

        assert result == ("10.0.0.1", "ip")

    def test_returns_none_for_unsupported_row(self, db_session):
        batch = add_batch(db_session)
        add_feature_record(db_session, batch, 1)
        alert = add_alert(db_session, batch, row_number=1)

        assert extract_supported_indicator(db_session, alert) is None

    def test_returns_none_when_no_feature_record_exists(self, db_session):
        batch = add_batch(db_session)
        alert = add_alert(db_session, batch, row_number=1)

        assert extract_supported_indicator(db_session, alert) is None


class TestGetDefaultProvider:
    def test_disabled_returns_none(self):
        assert get_default_provider(make_settings(threat_intel_enabled=False)) is None

    def test_missing_api_key_returns_none(self):
        assert get_default_provider(make_settings(threat_intel_api_key=None)) is None

    def test_unknown_provider_name_returns_none(self):
        assert get_default_provider(make_settings(threat_intel_provider="not_a_real_provider")) is None

    def test_configured_abuseipdb_returns_a_provider(self):
        provider = get_default_provider(make_settings())
        assert provider is not None
        assert provider.name == "abuseipdb"


class TestEnrichAlertDisabledOrUnsupported:
    def test_disabled_returns_none_and_persists_nothing(self, db_session):
        batch = add_batch(db_session)
        add_feature_record(db_session, batch, 1, source_ip="10.0.0.1")
        alert = add_alert(db_session, batch, row_number=1)

        result = enrich_alert(db_session, alert, settings=make_settings(threat_intel_enabled=False))

        assert result is None
        assert db_session.query(ThreatIntelLookup).count() == 0

    def test_unsupported_indicator_returns_none(self, db_session):
        batch = add_batch(db_session)
        add_feature_record(db_session, batch, 1)  # no IP columns
        alert = add_alert(db_session, batch, row_number=1)

        result = enrich_alert(db_session, alert, settings=make_settings())

        assert result is None

    def test_missing_credentials_persists_an_unavailable_result(self, db_session):
        batch = add_batch(db_session)
        add_feature_record(db_session, batch, 1, source_ip="10.0.0.1")
        alert = add_alert(db_session, batch, row_number=1)

        result = enrich_alert(db_session, alert, settings=make_settings(threat_intel_api_key=None))

        assert result is not None
        assert result.response_received is False
        assert result.unavailable_reason == "missing_credentials"


class TestEnrichAlertWithMockedProvider:
    def test_successful_lookup_persists_a_found_result(self, db_session):
        batch = add_batch(db_session)
        add_feature_record(db_session, batch, 1, source_ip="1.2.3.4")
        alert = add_alert(db_session, batch, row_number=1)
        provider = AbuseIPDBProvider("key", client=mock_client(abuseipdb_json_handler(90.0)))

        row = enrich_alert(db_session, alert, settings=make_settings(), provider=provider)

        assert row.response_received is True
        assert row.found is True
        assert row.alert_id == alert.id
        assert row.indicator == "1.2.3.4"

    def test_provider_exception_is_caught_and_persisted_as_unavailable(self, db_session):
        batch = add_batch(db_session)
        add_feature_record(db_session, batch, 1, source_ip="1.2.3.4")
        alert = add_alert(db_session, batch, row_number=1)

        class ExplodingProvider:
            name = "abuseipdb"

            def lookup_ip(self, ip: str) -> ThreatIntelResult:
                raise RuntimeError("simulated unexpected failure")

        row = enrich_alert(db_session, alert, settings=make_settings(), provider=ExplodingProvider())

        assert row.response_received is False
        assert row.unavailable_reason == "provider_error"

    def test_second_lookup_within_ttl_uses_the_cache_not_the_provider(self, db_session):
        batch = add_batch(db_session)
        add_feature_record(db_session, batch, 1, source_ip="1.2.3.4")
        alert_a = add_alert(db_session, batch, row_number=1)
        add_feature_record(db_session, batch, 2, source_ip="1.2.3.4")
        alert_b = add_alert(db_session, batch, row_number=2)

        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(200, json={"data": {"abuseConfidenceScore": 42.0}})

        provider = AbuseIPDBProvider("key", client=mock_client(handler))

        enrich_alert(db_session, alert_a, settings=make_settings(), provider=provider)
        enrich_alert(db_session, alert_b, settings=make_settings(), provider=provider)

        assert calls["count"] == 1
        assert db_session.query(ThreatIntelLookup).count() == 1

    def test_expired_cache_triggers_a_fresh_lookup(self, db_session):
        batch = add_batch(db_session)
        add_feature_record(db_session, batch, 1, source_ip="1.2.3.4")
        alert = add_alert(db_session, batch, row_number=1)
        stale_result = ThreatIntelResult(
            indicator="1.2.3.4", indicator_type="ip", provider_name="abuseipdb", response_received=True,
            found=True, confidence=0.5, queried_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        upsert_lookup(db_session, stale_result, alert_id=None)

        provider = AbuseIPDBProvider("key", client=mock_client(abuseipdb_json_handler(10.0)))
        row = enrich_alert(db_session, alert, settings=make_settings(threat_intel_cache_ttl_seconds=60.0), provider=provider)

        assert row.confidence == pytest.approx(0.10)

    def test_enrich_alerts_continues_past_one_alerts_failure(self, db_session):
        batch = add_batch(db_session)
        add_feature_record(db_session, batch, 1, source_ip="1.2.3.4")
        alert_a = add_alert(db_session, batch, row_number=1)
        add_feature_record(db_session, batch, 2, source_ip="5.6.7.8")
        alert_b = add_alert(db_session, batch, row_number=2)

        def handler(request: httpx.Request) -> httpx.Response:
            if "1.2.3.4" in str(request.url):
                raise RuntimeError("simulated transport crash")
            return httpx.Response(200, json={"data": {"abuseConfidenceScore": 5.0}})

        class FlakyProvider:
            name = "abuseipdb"

            def __init__(self):
                self._inner = AbuseIPDBProvider("key", client=mock_client(handler))

            def lookup_ip(self, ip: str) -> ThreatIntelResult:
                if ip == "1.2.3.4":
                    raise RuntimeError("simulated crash before any HTTP call")
                return self._inner.lookup_ip(ip)

        rows = enrich_alerts(db_session, [alert_a, alert_b], settings=make_settings(), provider=FlakyProvider())

        assert len(rows) == 2
        by_alert = {row.alert_id: row for row in rows}
        assert by_alert[alert_a.id].response_received is False
        assert by_alert[alert_b.id].response_received is True


class TestCacheLayer:
    def test_get_cached_lookup_returns_none_when_nothing_cached(self, db_session):
        assert get_cached_lookup(db_session, "abuseipdb", "1.2.3.4", ttl_seconds=3600) is None

    def test_upsert_then_get_returns_the_same_row(self, db_session):
        result = ThreatIntelResult(
            indicator="1.2.3.4", indicator_type="ip", provider_name="abuseipdb",
            response_received=True, found=True, confidence=0.5,
        )
        upsert_lookup(db_session, result, alert_id=None)

        cached = get_cached_lookup(db_session, "abuseipdb", "1.2.3.4", ttl_seconds=3600)

        assert cached is not None
        assert cached.confidence == pytest.approx(0.5)

    def test_upsert_updates_the_existing_row_rather_than_duplicating(self, db_session):
        first = ThreatIntelResult(indicator="1.2.3.4", indicator_type="ip", provider_name="abuseipdb", response_received=True, found=False, confidence=0.0)
        second = ThreatIntelResult(indicator="1.2.3.4", indicator_type="ip", provider_name="abuseipdb", response_received=True, found=True, confidence=0.9)

        upsert_lookup(db_session, first, alert_id=None)
        upsert_lookup(db_session, second, alert_id=None)

        assert db_session.query(ThreatIntelLookup).count() == 1
        row = get_cached_lookup(db_session, "abuseipdb", "1.2.3.4", ttl_seconds=3600)
        assert row.found is True
