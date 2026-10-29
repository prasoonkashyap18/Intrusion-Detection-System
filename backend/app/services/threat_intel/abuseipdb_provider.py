"""AbuseIPDB adapter: a configurable integration with a public IP
reputation provider, behind the `ThreatIntelProvider` interface
(`base.py`) so another provider can be added later without touching
any caller.

Uses `httpx2` (this project's available HTTP client — see `backend/
requirements.txt`; it is a drop-in, API-compatible build of `httpx`)
with an explicit, bounded timeout and at most one retry, only on a
timeout, never on a 4xx/5xx response (retrying a rate-limit or
malformed-request response would not help and would waste the
provider's quota). Every failure mode — missing credentials (checked by
the caller before this class is even constructed — see
`app.services.threat_intel.enrichment.get_default_provider`), a
timeout, a non-2xx response, a rate limit (`429`), or a response this
adapter cannot parse — is caught here and turned into a `ThreatIntelResult`
with `response_received=False`; nothing ever propagates as an exception
to a caller (see `base.ThreatIntelProvider`'s docstring for why).

`reputation` is this adapter's own documented bucketing of AbuseIPDB's
real `abuseConfidenceScore` (0-100): `"clean"` for `0`, `"suspicious"`
for `1-74`, `"malicious"` for `75-100` — a transparent, fixed mapping of
a real reported number, never an invented label. `category` is left
`None`: AbuseIPDB's `/check` endpoint does not return a category field,
and this adapter does not invent one.
"""

from __future__ import annotations

import logging

import httpx2 as httpx

from app.services.threat_intel.base import ThreatIntelResult

logger = logging.getLogger("ai_ids")

ABUSEIPDB_CHECK_URL = "https://api.abuseipdb.com/api/v2/check"
PROVIDER_NAME = "abuseipdb"


def _reputation_for_score(score: float) -> str:
    if score <= 0:
        return "clean"
    if score < 75:
        return "suspicious"
    return "malicious"


class AbuseIPDBProvider:
    """A configured AbuseIPDB client. Construct via
    `app.services.threat_intel.enrichment.get_default_provider`, which
    is the only place an API key is read from configuration — this
    class never reads environment variables itself, so it stays
    trivially testable with a fake key and a mocked transport.
    """

    name = PROVIDER_NAME

    def __init__(self, api_key: str, *, timeout_seconds: float = 5.0, max_retries: int = 1, client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, max_retries)
        # A caller-supplied client (e.g. one built with httpx.MockTransport
        # in tests) is used as-is; otherwise a real client with this
        # provider's own timeout is built lazily per call so no socket is
        # held open between lookups.
        self._client = client

    def lookup_ip(self, ip: str) -> ThreatIntelResult:
        attempts = 0
        last_error: str | None = None

        while attempts <= self._max_retries:
            attempts += 1
            try:
                response = self._request(ip)
            except httpx.TimeoutException:
                last_error = "timeout"
                continue
            except httpx.RequestError:
                logger.exception("AbuseIPDB request failed for an indicator")
                return self._unavailable(ip, "network_error")

            if response.status_code == 429:
                return self._unavailable(ip, "rate_limited")
            if response.status_code != 200:
                logger.error("AbuseIPDB returned status %s for an indicator lookup", response.status_code)
                return self._unavailable(ip, "provider_error")

            return self._parse_response(ip, response)

        return self._unavailable(ip, last_error or "timeout")

    def _request(self, ip: str) -> httpx.Response:
        client = self._client or httpx.Client(timeout=self._timeout_seconds)
        owns_client = self._client is None
        try:
            return client.get(
                ABUSEIPDB_CHECK_URL,
                params={"ipAddress": ip, "maxAgeInDays": "90"},
                headers={"Key": self._api_key, "Accept": "application/json"},
            )
        finally:
            if owns_client:
                client.close()

    def _parse_response(self, ip: str, response: httpx.Response) -> ThreatIntelResult:
        try:
            payload = response.json()
            data = payload["data"]
            score = float(data["abuseConfidenceScore"])
        except (ValueError, KeyError, TypeError):
            logger.exception("AbuseIPDB response could not be parsed for an indicator")
            return self._unavailable(ip, "provider_error")

        return ThreatIntelResult(
            indicator=ip,
            indicator_type="ip",
            provider_name=self.name,
            response_received=True,
            found=score > 0,
            confidence=score / 100.0,
            category=None,
            reputation=_reputation_for_score(score),
            reference_url=f"https://www.abuseipdb.com/check/{ip}",
            raw_result=data,
        )

    def _unavailable(self, ip: str, reason: str) -> ThreatIntelResult:
        return ThreatIntelResult(
            indicator=ip,
            indicator_type="ip",
            provider_name=self.name,
            response_received=False,
            found=None,
            unavailable_reason=reason,
        )


__all__ = ["AbuseIPDBProvider", "PROVIDER_NAME"]
