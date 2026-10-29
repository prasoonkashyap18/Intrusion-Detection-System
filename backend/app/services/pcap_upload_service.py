"""Registers an uploaded `.pcap`/`.pcapng` file as a pending
`DetectionBatch` + `PcapCaptureMetadata` row. Mirrors `app.services.
upload_service.register_upload`'s exact structure and conventions
(chunked storage with a size cap, atomic rename only after validation,
cleanup on any failure) for the CSV path — the PCAP-specific
differences are the extension/content check (`app.services.
pcap_parsing`, which inspects real file bytes rather than trusting the
filename or client-supplied content type) and the extra metadata row.

Storage layout: `<upload_dir>/<batch_id>.<pcap|pcapng>` — the same
"name generated from the batch's own UUID" convention as the CSV path,
so the client's filename never touches the filesystem.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.core.errors import ApiException
from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus
from app.models.pcap_capture_metadata import PcapCaptureMetadata
from app.services.csv_validation import sanitize_filename
from app.services.flow_construction import DEFAULT_IDLE_TIMEOUT_SECONDS
from app.services.pcap_parsing import PcapValidationError, detect_capture_format, has_pcap_extension

logger = logging.getLogger("ai_ids")

_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class PcapUploadConfig:
    directory: Path
    max_bytes: int
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS


def register_pcap_upload(
    *,
    db: Session,
    config: PcapUploadConfig,
    source: BinaryIO,
    filename: str | None,
    content_type: str | None,
) -> DetectionBatch:
    """Stores the upload and creates its pending batch + PCAP metadata
    row, or raises `ApiException`. The extension check is an early,
    cheap rejection; the authoritative check is `detect_capture_format`
    on the fully-stored file's real bytes, run *after* storage — a
    file that merely has a `.pcap` name but is not genuinely one is
    still rejected (415) and removed.
    """
    if not filename or not filename.strip():
        raise ApiException(400, "missing_filename", "The uploaded file has no filename.")
    display_name = sanitize_filename(filename)
    if not has_pcap_extension(display_name):
        raise ApiException(415, "unsupported_media_type", "Only .pcap or .pcapng files are supported.")

    batch_id = uuid.uuid4()
    # The stored extension always matches the client's own (validated)
    # extension; the real format is re-derived from content afterward
    # and recorded on PcapCaptureMetadata regardless of what either says.
    suffix = ".pcapng" if display_name.lower().endswith(".pcapng") else ".pcap"
    final_path = config.directory / f"{batch_id}{suffix}"
    part_path = config.directory / f"{batch_id}{suffix}.part"
    stored_final = False

    try:
        config.directory.mkdir(parents=True, exist_ok=True)
        size = _store(source, part_path, config.max_bytes)
        if size == 0:
            raise ApiException(400, "empty_file", "The uploaded capture is empty.")

        capture_format = detect_capture_format(part_path)
        if capture_format is None:
            raise PcapValidationError(
                415, "unsupported_media_type", "The uploaded file is not a recognized PCAP or PCAPNG capture."
            )

        os.replace(part_path, final_path)
        stored_final = True

        created_at = datetime.now(timezone.utc)
        batch = DetectionBatch(
            id=batch_id,
            filename=display_name,
            status=ProcessingStatus.PENDING,
            total_records=0,
            processed_records=0,
            failed_records=0,
            error_message=None,
            created_at=created_at,
            completed_at=None,
        )
        db.add(batch)
        db.add(
            PcapCaptureMetadata(
                batch_id=batch_id,
                original_filename=display_name,
                capture_format=capture_format,
                idle_timeout_seconds=config.idle_timeout_seconds,
            )
        )
        db.commit()
    except ApiException:
        db.rollback()
        _discard(part_path, final_path if stored_final else None)
        raise
    except Exception:
        logger.exception("Failed to register uploaded capture (batch %s)", batch_id)
        db.rollback()
        _discard(part_path, final_path if stored_final else None)
        raise ApiException(500, "upload_failed", "Unable to register the uploaded capture.") from None

    logger.info("Registered PCAP batch %s: %d bytes, format=%s", batch_id, size, capture_format)
    return batch


def _store(source: BinaryIO, destination: Path, max_bytes: int) -> int:
    size = 0
    with destination.open("xb") as out:
        while chunk := source.read(_CHUNK_BYTES):
            size += len(chunk)
            if size > max_bytes:
                limit_mb = max_bytes / (1024 * 1024)
                raise ApiException(
                    413,
                    "file_too_large",
                    f"Uploaded capture exceeds the maximum allowed size of {limit_mb:g} MB.",
                )
            out.write(chunk)
    return size


def _discard(*paths: Path | None) -> None:
    for path in paths:
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove leftover upload file %s", path.name)


__all__ = ["PcapUploadConfig", "register_pcap_upload"]
