"""Feature extraction & normalization: NetworkFlowRecord -> NormalizedFlowFeatures.

    NetworkFlowRecord
        |
    extract_raw_features()   <- type-safe parsing of generic network-flow
        |                        concepts from record.raw_features; no
        |                        scaling, no statistics, no ML algorithm
    RawFeatureSet              knowledge
        |
    normalize_features()     <- the hook a future fitted scaler plugs into;
        |                        today this is the identity transform
    NormalizedFlowFeatures     (see "Normalization" below)

This module performs NO machine learning. It does not predict, classify,
score, assess risk/threat, or create `DetectionResult` rows — it only
extracts and normalizes features. It does not depend on any particular ML
algorithm (no Random Forest/XGBoost/Isolation Forest/neural-network
assumptions); `NormalizedFlowFeatures` is a plain, algorithm-agnostic input
contract a future model step can consume.

Why this reads `record.raw_features` directly, not `NetworkFlowRecord`'s own
typed fields: `app.services.ingestion` already types 6 fields it recognizes
(source_ip, destination_ip, source_port, destination_port, protocol,
flow_timestamp), but it folds "column missing" and "column present but
unparseable" into the same `None` — a deliberate ingestion-layer choice (see
its docstring) that loses exactly the missing-vs-malformed distinction this
layer is required to make. Reading directly from `raw_features` (which keeps
every column's original text, matched or not) restores that distinction
without changing ingestion's behavior, and also means adding another
generic feature here never requires touching the ingestion layer.

Dataset independence: like `ingestion.py`, this module recognizes a small,
explicitly generic set of column-name aliases (`_COLUMN_ALIASES`) for common
network-flow concepts (duration, packet/byte counts, forward/backward
stats, TCP flags, ...). There is no `if dataset == "..."` branch anywhere in
this module. Any raw_features column that does not match one of these
generic aliases is preserved, untouched, in `RawFeatureSet.unknown_features`
/ `NormalizedFlowFeatures.unknown_features` — nothing is silently discarded.
Mapping a *specific* dataset's own column names (e.g. a public dataset that
names this concept something this module's generic aliases don't cover) is
intentionally left to a future dataset-adapter mechanism, not implemented
here.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from app.services.ingestion import NetworkFlowRecord

# Deterministic, explicit feature order. A model's input vector must be
# built from this tuple (see `NormalizedFlowFeatures.feature_vector`), never
# from dict iteration order, so adding a feature here is a conscious,
# reviewed change to the model's input contract.
FEATURE_SCHEMA: tuple[str, ...] = (
    "source_port",
    "destination_port",
    "source_ip_numeric",
    "destination_ip_numeric",
    "protocol_number",
    "flow_duration",
    "packet_count",
    "byte_count",
    "packet_rate",
    "byte_rate",
    "forward_packet_count",
    "forward_byte_count",
    "backward_packet_count",
    "backward_byte_count",
    "tcp_flags",
)


class FeatureStatus(str, Enum):
    """Why a schema feature does or does not have a value. Distinguishing
    these is the point of this layer — collapsing them all into "0" or
    "None" would hide the difference between "this dataset doesn't provide
    this" and "this dataset provided garbage"."""

    #: A value was found and parsed into this feature's expected type.
    PRESENT = "present"
    #: No source column for this feature was found, or it was present but
    #: empty. Not every dataset provides every concept; this is normal.
    MISSING = "missing"
    #: A source column was found and had a non-empty value, but it did not
    #: parse as this feature's expected type (e.g. non-numeric text in a
    #: byte-count column, or a negative value for an inherently
    #: non-negative count). Never silently coerced to 0 or any other value.
    MALFORMED = "malformed"
    #: A source column was found and its value is syntactically valid data,
    #: but this module has no deterministic encoding for it yet (e.g. a
    #: protocol name outside the small known table, or textual TCP flags
    #: like "SF" rather than a numeric bitmask). The original text is not
    #: discarded — see `unknown_features`/`raw_features` on the record.
    UNSUPPORTED = "unsupported"


# Generic network-flow column-name aliases this module recognizes,
# normalized (stripped, lowercased, internal whitespace collapsed to a
# single underscore) -> the FEATURE_SCHEMA entry (or "source_ip"/
# "destination_ip"/"protocol" context field) they map to. Intentionally a
# small, dataset-agnostic seed — see the module docstring.
_COLUMN_ALIASES: dict[str, str] = {
    "source_ip": "source_ip",
    "src_ip": "source_ip",
    "destination_ip": "destination_ip",
    "dst_ip": "destination_ip",
    "dest_ip": "destination_ip",
    "source_port": "source_port",
    "src_port": "source_port",
    "destination_port": "destination_port",
    "dst_port": "destination_port",
    "dest_port": "destination_port",
    "protocol": "protocol",
    "proto": "protocol",
    "duration": "flow_duration",
    "flow_duration": "flow_duration",
    "dur": "flow_duration",
    "packet_count": "packet_count",
    "packets": "packet_count",
    "total_packets": "packet_count",
    "byte_count": "byte_count",
    "bytes": "byte_count",
    "total_bytes": "byte_count",
    "packet_rate": "packet_rate",
    "packets_per_second": "packet_rate",
    "byte_rate": "byte_rate",
    "bytes_per_second": "byte_rate",
    "fwd_packets": "forward_packet_count",
    "forward_packets": "forward_packet_count",
    "fwd_pkts": "forward_packet_count",
    "fwd_bytes": "forward_byte_count",
    "forward_bytes": "forward_byte_count",
    "fwd_byts": "forward_byte_count",
    "bwd_packets": "backward_packet_count",
    "backward_packets": "backward_packet_count",
    "bwd_pkts": "backward_packet_count",
    "bwd_bytes": "backward_byte_count",
    "backward_bytes": "backward_byte_count",
    "bwd_byts": "backward_byte_count",
    "tcp_flags": "tcp_flags",
    "flags": "tcp_flags",
}

# IANA-assigned protocol numbers (never invented) for the handful of
# protocols that appear in essentially every network-flow dataset. Anything
# else is FeatureStatus.UNSUPPORTED, not guessed at.
_PROTOCOL_NUMBERS: dict[str, int] = {
    "icmp": 1,
    "tcp": 6,
    "udp": 17,
    "ipv6-icmp": 58,
    "icmpv6": 58,
}
_PROTOCOL_NAMES_BY_NUMBER: dict[int, str] = {number: name for name, number in _PROTOCOL_NUMBERS.items() if name != "icmpv6"}

# A non-negative integer is a plausible IANA protocol number even when it
# isn't one of the handful named above (IANA defines well over a hundred).
_MAX_PROTOCOL_NUMBER = 255

_PORT_FEATURES = frozenset({"source_port", "destination_port"})
_MAX_PORT = 65535
_NUMERIC_FEATURES = (
    frozenset(FEATURE_SCHEMA)
    - {"source_ip_numeric", "destination_ip_numeric", "protocol_number", "tcp_flags"}
    - _PORT_FEATURES
)


def _normalize_column(name: str) -> str:
    return "_".join(name.strip().lower().split())


@dataclass(frozen=True)
class RawFeatureSet:
    """Step 1 output: generic network-flow concepts parsed from one record's
    raw CSV text into the right primitive type. No scaling or statistical
    transformation has been applied — see `normalize_features`."""

    row_number: int
    source_ip: str | None
    destination_ip: str | None
    protocol: str | None
    """Canonicalized (stripped, lowercased) protocol text, when present —
    independent of whether a numeric encoding could be produced for it."""

    values: dict[str, float]
    """FEATURE_SCHEMA entries with FeatureStatus.PRESENT, holding their
    parsed numeric value. A feature absent from this dict has no value —
    look it up in `status` to find out why."""

    status: dict[str, FeatureStatus]
    """Every FEATURE_SCHEMA entry, always — this dict's keys are exactly
    `set(FEATURE_SCHEMA)` regardless of outcome."""

    unknown_features: dict[str, str]
    """Every `raw_features` column that did not match a known alias,
    verbatim, keyed by its original header text. Nothing from the source
    CSV is silently dropped: it is either parsed above or preserved here."""


class FeatureScaler(Protocol):
    """The interface a future fitted scaler (e.g. min-max or z-score
    parameters learned from training data) will implement to plug into
    `normalize_features`. This module deliberately does not compute or
    store any such parameters itself — see "Normalization" in the module
    docstring and backend/README.md."""

    def __call__(self, feature_name: str, value: float) -> float: ...


@dataclass(frozen=True)
class NormalizedFlowFeatures:
    """Step 2 output: the model input contract. `features` holds exactly
    the keys in FEATURE_SCHEMA, in that order (Python dicts preserve
    insertion order, but never rely on that — use `feature_vector()`)."""

    row_number: int
    source_ip: str | None
    destination_ip: str | None
    protocol_name: str | None
    features: dict[str, float | int | None]
    feature_status: dict[str, FeatureStatus]
    unknown_features: dict[str, str]

    def feature_vector(self) -> list[float | int | None]:
        """The feature values in the one true order (`FEATURE_SCHEMA`),
        independent of `dict` iteration order. This is what a future model
        step should consume — never `features.values()` directly."""
        return [self.features[name] for name in FEATURE_SCHEMA]


def extract_raw_features(record: NetworkFlowRecord) -> RawFeatureSet:
    """Parses the generic network-flow concepts this module recognizes out
    of `record.raw_features`. Deterministic: the same record always yields
    the same `RawFeatureSet` (same keys, same values, same status)."""
    status: dict[str, FeatureStatus] = dict.fromkeys(FEATURE_SCHEMA, FeatureStatus.MISSING)
    values: dict[str, float] = {}
    unknown_features: dict[str, str] = {}

    source_ip: str | None = None
    destination_ip: str | None = None
    protocol: str | None = None

    for column, raw_value in record.raw_features.items():
        target = _COLUMN_ALIASES.get(_normalize_column(column))
        text = raw_value.strip()

        if target is None:
            unknown_features[column] = raw_value
            continue

        if target == "source_ip":
            source_ip, status["source_ip_numeric"] = _parse_ip_for_status(text)
        elif target == "destination_ip":
            destination_ip, status["destination_ip_numeric"] = _parse_ip_for_status(text)
        elif target == "protocol":
            protocol, number, protocol_status = _parse_protocol(text)
            status["protocol_number"] = protocol_status
            if number is not None:
                values["protocol_number"] = number
        elif target in _PORT_FEATURES:
            parsed, field_status = _parse_port(text)
            status[target] = field_status
            if parsed is not None:
                values[target] = parsed
        elif target == "tcp_flags":
            parsed, field_status = _parse_tcp_flags(text)
            status[target] = field_status
            if parsed is not None:
                values[target] = parsed
        elif target in _NUMERIC_FEATURES:
            parsed, field_status = _parse_non_negative_number(text)
            status[target] = field_status
            if parsed is not None:
                values[target] = parsed
        # else: an alias exists but points somewhere this function does not
        # handle — unreachable with the alias table above, kept defensive.

    if source_ip is not None:
        values["source_ip_numeric"] = float(int(ipaddress.ip_address(source_ip)))
    if destination_ip is not None:
        values["destination_ip_numeric"] = float(int(ipaddress.ip_address(destination_ip)))

    return RawFeatureSet(
        row_number=record.row_number,
        source_ip=source_ip,
        destination_ip=destination_ip,
        protocol=protocol,
        values=values,
        status=status,
        unknown_features=unknown_features,
    )


def _parse_ip_for_status(text: str) -> tuple[str | None, FeatureStatus]:
    if not text:
        return None, FeatureStatus.MISSING
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return None, FeatureStatus.MALFORMED
    return text, FeatureStatus.PRESENT


def _parse_protocol(text: str) -> tuple[str | None, int | None, FeatureStatus]:
    """Returns (canonical protocol text, IANA number, status). The text is
    kept even when no number can be produced, so it is never lost."""
    if not text:
        return None, None, FeatureStatus.MISSING

    canonical = text.lower()
    if canonical in _PROTOCOL_NUMBERS:
        return canonical, _PROTOCOL_NUMBERS[canonical], FeatureStatus.PRESENT

    if canonical.isdigit():
        number = int(canonical)
        if 0 <= number <= _MAX_PROTOCOL_NUMBER:
            return _PROTOCOL_NAMES_BY_NUMBER.get(number, canonical), number, FeatureStatus.PRESENT

    # Valid-looking text (e.g. "QUIC", "ESP"), just not one of the handful
    # of protocols this module can confidently number — preserved as text,
    # not guessed at.
    return canonical, None, FeatureStatus.UNSUPPORTED


def _parse_port(text: str) -> tuple[float | None, FeatureStatus]:
    if not text:
        return None, FeatureStatus.MISSING
    try:
        port = int(text)
    except ValueError:
        return None, FeatureStatus.MALFORMED
    if not (0 <= port <= _MAX_PORT):
        return None, FeatureStatus.MALFORMED
    return float(port), FeatureStatus.PRESENT


def _parse_tcp_flags(text: str) -> tuple[float | None, FeatureStatus]:
    """A numeric bitmask (e.g. "24") is a value this module can represent
    directly. Textual flag combinations (e.g. "SF", "APRSF" — a common
    representation in some datasets) are syntactically valid network data,
    just not something this module converts to a number — UNSUPPORTED, not
    MALFORMED, and the original text stays available on the source record."""
    if not text:
        return None, FeatureStatus.MISSING
    try:
        flags = int(text)
    except ValueError:
        return None, FeatureStatus.UNSUPPORTED
    if flags < 0:
        return None, FeatureStatus.MALFORMED
    return float(flags), FeatureStatus.PRESENT


def _parse_non_negative_number(text: str) -> tuple[float | None, FeatureStatus]:
    if not text:
        return None, FeatureStatus.MISSING
    try:
        value = float(text)
    except ValueError:
        return None, FeatureStatus.MALFORMED
    if value < 0:
        return None, FeatureStatus.MALFORMED
    return value, FeatureStatus.PRESENT


def normalize_features(raw: RawFeatureSet, scaler: FeatureScaler | None = None) -> NormalizedFlowFeatures:
    """Turns a `RawFeatureSet` into the model-ready `NormalizedFlowFeatures`.

    Today this is the identity transform unless a `scaler` is supplied: no
    mean/std/min/max is computed here, because this module has no access to
    (and should not assume) a representative dataset to fit one from — see
    "Normalization" in the module docstring. `scaler`, when given, is
    applied only to features with FeatureStatus.PRESENT; MISSING/MALFORMED/
    UNSUPPORTED features stay `None` regardless — a scaler must never turn
    "we don't have this value" into a number.
    """
    features: dict[str, float | int | None] = {}
    for name in FEATURE_SCHEMA:
        if raw.status[name] != FeatureStatus.PRESENT:
            features[name] = None
            continue
        value = raw.values[name]
        features[name] = scaler(name, value) if scaler is not None else value

    return NormalizedFlowFeatures(
        row_number=raw.row_number,
        source_ip=raw.source_ip,
        destination_ip=raw.destination_ip,
        protocol_name=raw.protocol,
        features=features,
        feature_status=dict(raw.status),
        unknown_features=dict(raw.unknown_features),
    )


def extract_features(record: NetworkFlowRecord, scaler: FeatureScaler | None = None) -> NormalizedFlowFeatures:
    """Convenience: `normalize_features(extract_raw_features(record), scaler)`."""
    return normalize_features(extract_raw_features(record), scaler)
