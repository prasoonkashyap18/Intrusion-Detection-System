"""Bridges a packet-derived `FinalizedFlow` (`app.services.
flow_construction`) into the existing `MappedFeatureSet` contract
(`app.services.feature_mapping`) — the same ML-ready representation
every CSV dataset adapter already produces, so everything downstream
(persistence, inference, hybrid detection, risk scoring, explainability,
alerting, correlation, chain analysis) runs completely unmodified for a
packet-derived flow.

    FinalizedFlow (app.services.flow_construction)
        |
    flow_to_canonical_record()  <- builds a CanonicalDatasetRecord with
        |                           dataset_schema=PCAP_DATASET_SCHEMA
    map_canonical_record()       (app.services.feature_mapping, REUSED
        |                         unchanged — this module never
        |                         reimplements type parsing)
    MappedFeatureSet

`PCAP_DATASET_SCHEMA` ("pcap-flow") is a new `dataset_schema` value,
deliberately never equal to any existing dataset adapter's `schema_id`
(e.g. "nsl-kdd-style"). This is what makes a trained model's existing
compatibility check (`app.services.model_inference`/`anomaly_inference`,
comparing `dataset_schema` strings) correctly refuse to run a model
trained on CSV-derived data against packet-derived features, and vice
versa, without this module needing to implement any compatibility logic
of its own — see backend/README.md's "PCAP Ingestion" section for why
packet-derived features are not simply interchangeable with NSL-KDD-
style ones even though they share the same `FEATURE_SCHEMA` names.

No value here is ever fabricated: every `FinalizedFlow` field reused
below is something `app.services.flow_construction` actually measured
from real packets. `packet_rate`/`byte_rate`/`tcp_flags_union` are
`None` exactly when genuinely undefined or inapplicable (see that
module's docstring) and are passed through as absent canonical fields
here, which `map_canonical_record` already turns into
`FeatureStatus.MISSING` — never a fabricated zero.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.dataset_adapters.canonical import CanonicalDatasetRecord
from app.services.feature_mapping import MappedFeatureSet, map_canonical_record
from app.services.flow_construction import FinalizedFlow
from app.services.ingestion import NetworkFlowRecord

PCAP_DATASET_SCHEMA = "pcap-flow"

_SOURCE_COLUMN = "pcap"
"""Recorded as every canonical field's `source_column` — there is no
dataset column name for a packet-derived flow, but the existing
`MappedFeature.source_column` contract expects a string or `None`; this
fixed, documented value is more informative than `None` (which would
read as "this dataset provides nothing for this concept at all")."""


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def flow_to_canonical_record(flow: FinalizedFlow, row_number: int) -> CanonicalDatasetRecord:
    """Builds a `CanonicalDatasetRecord` from one finalized flow — the
    same contract `app.services.dataset_adapters` produces for a CSV
    row, so `map_canonical_record` needs no changes to accept it.
    """
    raw_fields: dict[str, str | None] = {
        "source_ip": flow.source_ip,
        "destination_ip": flow.destination_ip,
        "protocol": str(flow.protocol_number),
        "flow_duration": f"{flow.duration_seconds:.6f}",
        "packet_count": str(flow.packet_count),
        "byte_count": str(flow.byte_count),
        "packet_rate": _text(flow.packet_rate),
        "byte_rate": _text(flow.byte_rate),
        "forward_packet_count": str(flow.forward_packet_count),
        "forward_byte_count": str(flow.forward_byte_count),
        "backward_packet_count": str(flow.backward_packet_count),
        "backward_byte_count": str(flow.backward_byte_count),
        "source_port": _text(flow.source_port),
        "destination_port": _text(flow.destination_port),
        "tcp_flags": _text(flow.tcp_flags_union),
    }
    canonical_fields = {name: value for name, value in raw_fields.items() if value is not None}
    column_mapping = {name: _SOURCE_COLUMN for name in canonical_fields}

    source_record = NetworkFlowRecord(
        row_number=row_number,
        source_ip=flow.source_ip,
        destination_ip=flow.destination_ip,
        source_port=flow.source_port,
        destination_port=flow.destination_port,
        protocol=str(flow.protocol_number),
        flow_timestamp=datetime.fromtimestamp(flow.first_timestamp, tz=timezone.utc),
        raw_features=dict(canonical_fields),
    )

    return CanonicalDatasetRecord(
        row_number=row_number,
        dataset_schema=PCAP_DATASET_SCHEMA,
        source_record=source_record,
        canonical_fields=canonical_fields,
        column_mapping=column_mapping,
        unknown_fields={},
        label=None,
        attack_category=None,
    )


def flow_to_mapped_feature_set(flow: FinalizedFlow, row_number: int) -> MappedFeatureSet:
    """The one-call conversion callers actually use — builds the
    canonical record and immediately maps it with the existing,
    unmodified `app.services.feature_mapping.map_canonical_record`.
    """
    return map_canonical_record(flow_to_canonical_record(flow, row_number))


__all__ = ["PCAP_DATASET_SCHEMA", "flow_to_canonical_record", "flow_to_mapped_feature_set"]
