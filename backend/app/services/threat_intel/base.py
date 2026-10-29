"""Provider-independent threat-intelligence lookup contract.

`ThreatIntelResult` is the single normalized shape every provider
adapter (see `abuseipdb_provider.py`) must produce — never a raw
provider payload leaking into the rest of the system. It deliberately
distinguishes four outcomes a caller must never conflate:

- a confirmed match (`response_received=True`, `found=True`)
- a confirmed negative result (`response_received=True`, `found=False`
  — the provider genuinely responded and reported nothing against this
  indicator; never treated as proof the indicator is safe, only as
  "this provider found nothing")
- an unavailable lookup (`response_received=False`, `found=None`,
  `unavailable_reason` set — missing credentials, a timeout, a rate
  limit, or a provider error; never silently treated as a negative
  result)
- an unsupported indicator (the caller never calls a provider at all —
  see `app.services.threat_intel.enrichment.extract_supported_indicator`)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

SUPPORTED_INDICATOR_TYPES = ("ip",)
"""The only indicator types anything in this codebase currently
extracts from persisted data — see `app.services.threat_intel.
enrichment.extract_supported_indicator`. A domain indicator is not
extracted anywhere yet, so it is not listed as supported."""


class ThreatIntelUnavailableError(Exception):
    """Raised by a provider adapter only for a genuinely unexpected
    failure it cannot itself translate into a `ThreatIntelResult` with
    `response_received=False` — callers in this codebase always catch
    this and build that result themselves rather than letting it
    propagate. `message` is safe to log: no API keys, no raw response
    bodies."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ThreatIntelResult:
    """The normalized result of one lookup attempt, whatever its
    outcome. Every field other than `indicator`/`indicator_type`/
    `provider_name`/`response_received`/`found`/`queried_at` is `None`
    unless the provider itself genuinely returned that piece of
    information — never a default, placeholder, or invented value."""

    indicator: str
    indicator_type: str
    provider_name: str
    response_received: bool
    found: bool | None
    confidence: float | None = None
    category: str | None = None
    reputation: str | None = None
    reference_url: str | None = None
    raw_result: dict | None = None
    unavailable_reason: str | None = None
    queried_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ThreatIntelProvider(Protocol):
    """A provider-independent interface. Every adapter implementing
    this (see `abuseipdb_provider.AbuseIPDBProvider`) must itself catch
    every failure mode (timeout, rate limit, network error, malformed
    response) and return a `ThreatIntelResult` with
    `response_received=False` and a documented `unavailable_reason` —
    never let an exception propagate to the caller, since a threat-
    intelligence failure must never interrupt detection or alerting
    (see `app.services.threat_intel.enrichment`)."""

    name: str

    def lookup_ip(self, ip: str) -> ThreatIntelResult: ...


__all__ = [
    "SUPPORTED_INDICATOR_TYPES",
    "ThreatIntelUnavailableError",
    "ThreatIntelResult",
    "ThreatIntelProvider",
]
