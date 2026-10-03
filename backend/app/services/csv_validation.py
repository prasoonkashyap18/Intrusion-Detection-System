"""Structural validation of uploaded CSV files.

"Valid" here means a well-formed, comma-delimited table: a header row, at
least one data row, and every data row has as many cells as the header. It
deliberately knows nothing about IDS datasets — dataset-specific columns are
validated by the later data-processing step.

Uploaded content is untrusted. It is only ever parsed as text and counted:
no value is evaluated, executed or used to build paths or commands.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import ApiException

# A one-column file is almost always prose or a log, not tabular network data.
MIN_COLUMNS = 2

# Fallback encoding for CSVs exported on Windows (some public IDS datasets are
# not valid UTF-8). latin-1 decodes every byte, so rows are still counted.
# Public (not prefixed): app.services.ingestion reads the same stored files
# and must try encodings in this same order to see the same text.
ENCODINGS = ("utf-8-sig", "latin-1")

_MAX_FILENAME_LENGTH = 255
_UNSAFE_FILENAME_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


@dataclass(frozen=True)
class CsvSummary:
    column_count: int
    data_row_count: int


def sanitize_filename(raw: str | None) -> str:
    """Returns a display-safe filename (never used as a filesystem path).

    Drops any directory part (both separator styles), control characters and
    characters invalid in filenames, and caps the length so it fits the
    database column.
    """
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE_FILENAME_CHARS.sub("_", name).strip(" .")
    if not name:
        return "upload.csv"
    if len(name) > _MAX_FILENAME_LENGTH:
        stem, dot, extension = name.rpartition(".")
        name = (stem[: _MAX_FILENAME_LENGTH - len(extension) - 1] + dot + extension) if dot else name[:_MAX_FILENAME_LENGTH]
    return name


def has_csv_extension(filename: str) -> bool:
    return filename.lower().endswith(".csv") and len(filename) > len(".csv")


def inspect_csv(path: Path) -> CsvSummary:
    """Validates structure and counts data rows. Raises ApiException (400) if invalid."""
    for encoding in ENCODINGS:
        try:
            return _scan(path, encoding)
        except UnicodeDecodeError:
            continue
    raise _invalid("The file could not be read as text.")  # pragma: no cover - latin-1 never fails


def _scan(path: Path, encoding: str) -> CsvSummary:
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.reader(lines_without_nul(handle), strict=True)
        header: list[str] | None = None
        data_rows = 0
        try:
            for row in reader:
                if not any(cell.strip() for cell in row):
                    continue  # blank line
                if header is None:
                    header = row
                    if len(header) < MIN_COLUMNS:
                        raise _invalid(
                            f"CSV must have at least {MIN_COLUMNS} comma-separated columns."
                        )
                    continue
                if len(row) != len(header):
                    raise _invalid(
                        f"Line {reader.line_num} has {len(row)} columns but the header has {len(header)}."
                    )
                data_rows += 1
        except csv.Error:
            raise _invalid(f"CSV is malformed near line {reader.line_num}.") from None

    if header is None:
        raise _invalid("CSV has no header row.")
    if data_rows == 0:
        raise ApiException(400, "no_data_rows", "CSV contains no data rows.")
    return CsvSummary(column_count=len(header), data_row_count=data_rows)


def lines_without_nul(lines: Iterator[str]) -> Iterator[str]:
    """Guards a line iterator against embedded NUL bytes (a reliable binary-file
    signal). Public so app.services.ingestion can apply the same guard while
    streaming the same stored files."""
    for line in lines:
        if "\x00" in line:
            raise _invalid("The file is not a text CSV.")
        yield line


def _invalid(message: str) -> ApiException:
    return ApiException(400, "invalid_csv", message)
