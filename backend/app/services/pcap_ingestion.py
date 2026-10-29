"""PCAP batch ingestion (Step 41): the processing-boundary equivalent
of `app.services.batch_processor._run_feature_extraction` for a batch
whose source is an uploaded `.pcap`/`.pcapng` file instead of a CSV.

    .pcap/.pcapng file (already stored by app.services.pcap_upload_service)
        |
    app.services.pcap_parsing.iter_packets()        <- streaming, one packet
        |                                               at a time
    app.services.flow_construction.construct_flows()  <- packet-to-flow
        |
    app.services.pcap_feature_mapping.flow_to_mapped_feature_set()
        |
    app.services.feature_persistence.persist_mapped_features()  <- REUSED,
        |                                                           unchanged
    MappedFeatureRecord rows (dataset_schema="pcap-flow")

Deliberately reuses `persist_mapped_features` unchanged: a packet-
derived flow's `MappedFeatureSet` is exactly the same contract a CSV
row's is, so everything already built on top of `MappedFeatureRecord`
(model inference, anomaly detection, hybrid detection, risk scoring,
explainability, alerting) works for a PCAP-sourced batch without any
of those modules knowing or caring where the row came from.

This module does not call `predict_with_model` or any later pipeline
stage itself — see `app.services.batch_processor.start_pcap_processing`
for where parsing/flow-construction/persistence connects to the
existing inference chain, mirroring exactly how `start_processing`
already connects CSV feature extraction to it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.services.feature_extraction import FEATURE_SCHEMA_VERSION
from app.services.feature_persistence import persist_mapped_features
from app.services.flow_construction import DEFAULT_IDLE_TIMEOUT_SECONDS, construct_flows
from app.services.pcap_feature_mapping import PCAP_DATASET_SCHEMA, flow_to_mapped_feature_set
from app.services.pcap_parsing import PacketParsingStats, PcapParsingError, iter_packets

logger = logging.getLogger("ai_ids")

MAX_PACKETS_PER_CAPTURE = 2_000_000
"""A hard processing limit, independent of file size — "reasonable
processing limits" per Step 41's requirements. A capture exceeding this
is rejected rather than processed partially and silently, which would
produce a misleadingly incomplete set of flows."""


@dataclass(frozen=True)
class PcapIngestionSummary:
    """Mirrors `app.services.batch_processor.ProcessingSummary`'s role
    for the CSV path — an internal, in-memory-only summary of this run,
    never persisted directly (its numbers are written onto
    `PcapCaptureMetadata` by the caller instead)."""

    packets_read: int
    packets_parsed: int
    packets_skipped: int
    """`packets_skipped_no_ip + packets_malformed` — see
    `app.services.pcap_parsing.PacketParsingStats`."""
    flows_constructed: int
    dataset_schema: str
    feature_schema_version: str


class PcapIngestionError(Exception):
    """Raised for a capture-level processing failure (too many packets,
    or the underlying parser could not open the file at all) — never
    for an individual malformed packet, which `PacketParsingStats`
    already tracks and skips. `message` is safe to surface to a caller."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def run_pcap_feature_extraction(
    db: Session,
    batch_id: uuid.UUID,
    pcap_path: Path,
    *,
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
) -> PcapIngestionSummary:
    """Parses `pcap_path`, constructs flows, and stages one
    `MappedFeatureRecord` per flow for `batch_id` (not committed here —
    see `app.services.feature_persistence`'s own transaction-boundary
    docstring; the caller commits once, exactly like the CSV path).

    Raises `PcapIngestionError` if the capture cannot be opened for
    parsing at all, or exceeds `MAX_PACKETS_PER_CAPTURE`. Raises
    `ApiException` (500) if staging a row fails unexpectedly at the
    database layer. Never fabricates a flow or a feature value for a
    packet this module could not parse — unparseable packets are
    counted in the returned summary and otherwise skipped, matching
    `app.services.pcap_parsing`'s own documented behavior.
    """
    stats = PacketParsingStats()

    try:
        packet_stream = iter_packets(pcap_path, stats)
        flows_constructed = 0
        for row_number, flow in enumerate(construct_flows(packet_stream, idle_timeout_seconds), start=1):
            if stats.packets_read > MAX_PACKETS_PER_CAPTURE:
                raise PcapIngestionError(
                    f"The capture exceeds the maximum of {MAX_PACKETS_PER_CAPTURE} packets per upload."
                )
            mapped = flow_to_mapped_feature_set(flow, row_number)
            persist_mapped_features(db, batch_id, mapped)
            flows_constructed += 1
    except PcapParsingError as error:
        raise PcapIngestionError(error.message) from None
    except SQLAlchemyError:
        logger.exception("Failed to stage PCAP-derived flow features for batch %s", batch_id)
        raise ApiException(
            500, "pcap_feature_persistence_unavailable", "Unable to persist flow features for this capture."
        ) from None

    return PcapIngestionSummary(
        packets_read=stats.packets_read,
        packets_parsed=stats.packets_parsed,
        packets_skipped=stats.packets_skipped_no_ip + stats.packets_malformed,
        flows_constructed=flows_constructed,
        dataset_schema=PCAP_DATASET_SCHEMA,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
    )


__all__ = ["MAX_PACKETS_PER_CAPTURE", "PcapIngestionSummary", "PcapIngestionError", "run_pcap_feature_extraction"]
