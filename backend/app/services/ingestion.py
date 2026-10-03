"""CSV ingestion: reads a stored batch's CSV and turns its rows into a
validated, dataset-independent internal representation (`NetworkFlowRecord`)
that a later step will map into ML-ready features.

    Stored CSV -> safe CSV reader -> CSV rows -> validated row structure
    -> NetworkFlowRecord

This module performs NO machine learning. It does not predict, classify,
score or create `DetectionResult` rows — it only reads and validates. It is
independent of the SQLAlchemy ORM: `NetworkFlowRecord` is a plain dataclass,
not tied to any database model, so a later step can consume it without a
database session.

Dataset independence: only a small set of generic networking column names
are recognized (see `_FIELD_ALIASES`). Every other column survives ingestion
untouched in `raw_features`, keyed by its original header text. This module
does not know about, or special-case, any specific public IDS dataset —
mapping a dataset's own column names onto the generic fields is Step 16's
job; this step only provides the mechanism (header -> generic field) for it
to build on.

Streaming: `ingest_batch` is a generator that yields one `NetworkFlowRecord`
per data row as the caller consumes it; rows are never collected into a list
here, so a very large CSV does not require a correspondingly large amount of
memory for that part. See `ingest_batch`'s docstring for the one bounded,
whole-file read this module does first, to pick a text encoding.
"""

from __future__ import annotations

import csv
import ipaddress
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.core.errors import ApiException
from app.services.csv_validation import ENCODINGS, MIN_COLUMNS, lines_without_nul

logger = logging.getLogger("ai_ids")


@dataclass(frozen=True)
class NetworkFlowRecord:
    """One CSV data row, validated for structure and lightly typed where a
    column could be confidently identified as a generic network-flow field.

    Every recognized field is optional: a dataset that does not provide it,
    or whose value could not be safely interpreted (e.g. text in a port
    column), simply leaves that field `None` rather than guessing — the
    original text for every column, recognized or not, is always preserved
    in `raw_features`, so no information is lost and nothing is fabricated.
    """

    row_number: int
    """1-based index among data rows (the header is not counted), for
    traceability in logs — never shown to end users."""

    source_ip: str | None
    destination_ip: str | None
    source_port: int | None
    destination_port: int | None
    protocol: str | None
    flow_timestamp: datetime | None

    raw_features: dict[str, str] = field(default_factory=dict)
    """Every column's original text, keyed by its original header, exactly
    as the CSV wrote it. Includes columns that were also mapped to a typed
    field above, so the original text is never lost even when parsing
    succeeded."""


# Generic networking column names this step recognizes, normalized (stripped,
# lowercased) -> the NetworkFlowRecord field they map to. Intentionally small:
# this is the seed Step 16 will expand with dataset-specific mappings, not an
# attempt to enumerate every public IDS dataset's column names now.
_FIELD_ALIASES: dict[str, str] = {
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
    "timestamp": "flow_timestamp",
    "flow_timestamp": "flow_timestamp",
}


def _normalize_header(name: str) -> str:
    return name.strip().lower()


def _build_header_map(header: list[str]) -> dict[int, str]:
    """Maps column index -> generic field name, for headers recognized by
    `_FIELD_ALIASES`. Unrecognized columns, and a duplicate alias seen a
    second time, are left unmapped (they still reach `raw_features`)."""
    mapped: dict[int, str] = {}
    seen_fields: set[str] = set()
    for index, column in enumerate(header):
        generic_field = _FIELD_ALIASES.get(_normalize_header(column))
        if generic_field is None or generic_field in seen_fields:
            continue
        mapped[index] = generic_field
        seen_fields.add(generic_field)
    return mapped


def _parse_ip(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None
    return value


def _parse_port(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        port = int(value)
    except ValueError:
        return None
    return port if 0 <= port <= 65535 else None


def _parse_protocol(value: str) -> str | None:
    value = value.strip()
    return value or None


def _parse_timestamp(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        # Conservative on purpose: only an unambiguous ISO 8601 string is
        # accepted. Anything else (epoch seconds, dataset-specific formats)
        # is preserved as raw text rather than guessed at.
        return datetime.fromisoformat(value)
    except ValueError:
        return None


_FIELD_PARSERS = {
    "source_ip": _parse_ip,
    "destination_ip": _parse_ip,
    "source_port": _parse_port,
    "destination_port": _parse_port,
    "protocol": _parse_protocol,
    "flow_timestamp": _parse_timestamp,
}


def ingest_batch(csv_path: Path) -> Iterator[NetworkFlowRecord]:
    """Streams one `NetworkFlowRecord` per data row of a stored batch CSV.

    A generator: nothing in this function runs until the caller starts
    iterating. The row-by-row parse (`_read_records`) holds only one row in
    memory at a time — the file's rows are never collected into a list here.
    Picking the text encoding (`_detect_encoding`) does read the file's raw
    bytes once first: encoding can only be confirmed by seeing whether the
    *whole* file decodes, and deciding that mid-stream would mean re-reading
    from the start after already handing some rows to the caller, which
    would silently yield them twice. That one bounded read (the same
    upload-size limit already enforced at upload time applies) buys
    correctness; the per-row parse that follows it is what stays streaming.

    Raises ApiException (never a bare exception) for every controlled
    failure mode: a missing or unreadable file, a file that cannot be
    decoded as text, no header, too few columns, or a data row whose width
    does not match the header. Messages are written for end users; row
    numbers and file details beyond that go to the server log only. This
    function validates CSV *structure* only — unexpected per-field content
    (e.g. text in a port column) does not fail ingestion, it is simply
    preserved as raw, unmapped text (see `NetworkFlowRecord.raw_features`).
    """
    if not csv_path.is_file():
        logger.error("Ingestion requested for a missing file: %s", csv_path.name)
        raise ApiException(404, "ingestion_file_missing", "The uploaded file for this batch could not be found.")

    encoding = _detect_encoding(csv_path)
    yield from _read_records(csv_path, encoding)


def _detect_encoding(csv_path: Path) -> str:
    try:
        raw = csv_path.read_bytes()
    except OSError:
        logger.exception("Could not read upload file %s", csv_path.name)
        raise ApiException(500, "ingestion_unreadable", "The uploaded file could not be read.") from None

    for encoding in ENCODINGS:
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise ApiException(400, "ingestion_invalid_csv", "The file could not be read as text.")  # pragma: no cover - latin-1 never fails


def read_header(csv_path: Path) -> list[str]:
    """Reads just a stored batch CSV's header row, applying the same
    structural rules `ingest_batch` applies (blank lines skipped, at least
    `MIN_COLUMNS` columns required) but stopping as soon as the header is
    found rather than continuing into the data rows.

    For callers that only need column names — `app.services.
    dataset_profiling` and `app.services.dataset_adapters.select_adapter`
    — so identifying a dataset's schema never requires streaming the whole
    file. Raises the same `ApiException` codes as `ingest_batch`, for the
    same reasons: a missing/unreadable file, a file that cannot be decoded
    as text, or no usable header row.
    """
    if not csv_path.is_file():
        logger.error("Header requested for a missing file: %s", csv_path.name)
        raise ApiException(404, "ingestion_file_missing", "The uploaded file for this batch could not be found.")

    encoding = _detect_encoding(csv_path)
    with csv_path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.reader(lines_without_nul(handle), strict=True)
        return _read_header_row(reader, csv_path)


def _read_header_row(reader: Iterator[list[str]], csv_path: Path) -> list[str]:
    """Shared by `ingest_batch` and `read_header`: finds the first
    non-blank row of an open CSV reader and validates it has enough
    columns to be a usable header."""
    try:
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue  # blank line, consistent with upload validation
            if len(row) < MIN_COLUMNS:
                raise ApiException(
                    400,
                    "ingestion_invalid_csv",
                    f"CSV must have at least {MIN_COLUMNS} comma-separated columns.",
                )
            return row
    except csv.Error:
        logger.exception("Malformed CSV in %s near line %d", csv_path.name, reader.line_num)
        raise ApiException(400, "ingestion_invalid_csv", f"CSV is malformed near line {reader.line_num}.") from None
    raise ApiException(400, "ingestion_invalid_csv", "CSV has no header row.")


def _read_records(csv_path: Path, encoding: str) -> Iterator[NetworkFlowRecord]:
    with csv_path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.reader(lines_without_nul(handle), strict=True)
        header = _read_header_row(reader, csv_path)
        header_map = _build_header_map(header)
        row_number = 0

        try:
            for row in reader:
                if not any(cell.strip() for cell in row):
                    continue  # blank line, consistent with upload validation

                if len(row) != len(header):
                    logger.error(
                        "Row width mismatch in %s at line %d: expected %d columns, got %d",
                        csv_path.name,
                        reader.line_num,
                        len(header),
                        len(row),
                    )
                    raise ApiException(
                        400,
                        "ingestion_invalid_csv",
                        f"Line {reader.line_num} has {len(row)} columns but the header has {len(header)}.",
                    )

                row_number += 1
                yield _build_record(row_number, header, header_map, row)
        except csv.Error:
            logger.exception("Malformed CSV in %s near line %d", csv_path.name, reader.line_num)
            raise ApiException(400, "ingestion_invalid_csv", f"CSV is malformed near line {reader.line_num}.") from None


def _build_record(row_number: int, header: list[str], header_map: dict[int, str], row: list[str]) -> NetworkFlowRecord:
    fields: dict[str, object] = {
        "source_ip": None,
        "destination_ip": None,
        "source_port": None,
        "destination_port": None,
        "protocol": None,
        "flow_timestamp": None,
    }
    raw_features: dict[str, str] = {}

    for index, cell in enumerate(row):
        column_name = header[index]
        raw_features[column_name] = cell
        generic_field = header_map.get(index)
        if generic_field is not None:
            fields[generic_field] = _FIELD_PARSERS[generic_field](cell)

    return NetworkFlowRecord(
        row_number=row_number,
        source_ip=fields["source_ip"],
        destination_ip=fields["destination_ip"],
        source_port=fields["source_port"],
        destination_port=fields["destination_port"],
        protocol=fields["protocol"],
        flow_timestamp=fields["flow_timestamp"],
        raw_features=raw_features,
    )
