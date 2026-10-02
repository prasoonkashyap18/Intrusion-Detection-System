"""Registers an uploaded CSV as a pending DetectionBatch.

This step only stores and counts the file. Nothing is analyzed: no model runs
and no DetectionResult rows are created, so the batch stays `pending` with
zero processed and failed records until a later processing step exists.

Storage layout: `<upload_dir>/<batch_id>.csv`. The name is generated here from
the batch's own UUID, so the client's filename never touches the filesystem
and a later step can locate a batch's file from its ID alone.
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
from app.services.csv_validation import has_csv_extension, inspect_csv, sanitize_filename

logger = logging.getLogger("ai_ids")

_CHUNK_BYTES = 1024 * 1024

# Browsers and operating systems label CSVs inconsistently (Windows commonly
# sends application/vnd.ms-excel). The extension and content checks are the
# real gate; this only rejects types that are clearly something else.
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "text/csv",
        "application/csv",
        "text/x-csv",
        "application/x-csv",
        "text/comma-separated-values",
        "application/vnd.ms-excel",
        "text/plain",
        "application/octet-stream",
    }
)


@dataclass(frozen=True)
class UploadConfig:
    directory: Path
    max_bytes: int


def register_upload(
    *,
    db: Session,
    config: UploadConfig,
    source: BinaryIO,
    filename: str | None,
    content_type: str | None,
) -> DetectionBatch:
    """Stores the upload and creates its pending batch, or raises ApiException."""
    if not filename or not filename.strip():
        raise ApiException(400, "missing_filename", "The uploaded file has no filename.")
    display_name = sanitize_filename(filename)
    if not has_csv_extension(display_name):
        raise ApiException(415, "unsupported_media_type", "Only CSV files are supported.")
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type and media_type not in ALLOWED_CONTENT_TYPES:
        raise ApiException(415, "unsupported_media_type", "Only CSV files are supported.")

    batch_id = uuid.uuid4()
    final_path = config.directory / f"{batch_id}.csv"
    part_path = config.directory / f"{batch_id}.csv.part"
    stored_final = False

    try:
        config.directory.mkdir(parents=True, exist_ok=True)
        size = _store(source, part_path, config.max_bytes)
        if size == 0:
            raise ApiException(400, "empty_file", "The uploaded file is empty.")
        summary = inspect_csv(part_path)

        os.replace(part_path, final_path)
        stored_final = True

        created_at = datetime.now(timezone.utc)
        batch = DetectionBatch(
            id=batch_id,
            filename=display_name,
            status=ProcessingStatus.PENDING,
            total_records=summary.data_row_count,
            processed_records=0,
            failed_records=0,
            error_message=None,
            created_at=created_at,
            completed_at=None,
        )
        db.add(batch)
        db.commit()
    except ApiException:
        db.rollback()
        _discard(part_path, final_path if stored_final else None)
        raise
    except Exception:
        # Details go to the log only; the client gets a generic message.
        logger.exception("Failed to register uploaded CSV (batch %s)", batch_id)
        db.rollback()
        _discard(part_path, final_path if stored_final else None)
        raise ApiException(500, "upload_failed", "Unable to register the uploaded file.") from None

    logger.info("Registered batch %s: %d records, %d bytes", batch_id, summary.data_row_count, size)
    return batch


def _store(source: BinaryIO, destination: Path, max_bytes: int) -> int:
    """Copies the upload to disk in chunks, stopping as soon as the limit is exceeded."""
    size = 0
    with destination.open("xb") as out:
        while chunk := source.read(_CHUNK_BYTES):
            size += len(chunk)
            if size > max_bytes:
                limit_mb = max_bytes / (1024 * 1024)
                raise ApiException(
                    413,
                    "file_too_large",
                    f"Uploaded file exceeds the maximum allowed size of {limit_mb:g} MB.",
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
