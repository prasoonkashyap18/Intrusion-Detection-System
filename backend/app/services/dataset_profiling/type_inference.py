"""Conservative, streaming-friendly type inference for a column's raw text
values.

Classification is advisory only: it never coerces, overwrites, or discards
the original text anywhere else in the pipeline. A value like `"00123"` is
classified `INTEGER` without ever being parsed into `123` and losing its
leading zeros — nothing here replaces or stores a converted value as "the"
value; it only labels what a value *looks like*, for reporting purposes.

`ColumnTypeTracker` folds each observed value's category into a small
running set as it streams by, so inferring a column's type never requires
holding its values in memory.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime

from app.services.dataset_profiling.profile import InferredType

_BOOLEAN_VALUES = frozenset({"true", "false"})

# Python's float() accepts these as valid floats, but a CSV cell reading
# "inf"/"nan" is not meaningfully "numeric" data for profiling purposes —
# classified as TEXT instead, and excluded from min/max (see profiler.py).
_NON_NUMERIC_FLOAT_TEXT = frozenset({"inf", "-inf", "+inf", "infinity", "-infinity", "+infinity", "nan", "-nan", "+nan"})


def classify_value(text: str) -> InferredType | None:
    """Classifies one value's apparent type.

    Returns `None` for an empty/whitespace-only value — the caller should
    not fold that into a column's running type state; an absent value is
    evidence of nothing about the column's type.
    """
    stripped = text.strip()
    if not stripped:
        return None
    if _looks_like_ip(stripped):
        return InferredType.IP_ADDRESS
    if _looks_like_timestamp(stripped):
        return InferredType.TIMESTAMP
    if stripped.lower() in _BOOLEAN_VALUES:
        return InferredType.BOOLEAN
    if _looks_like_integer(stripped):
        return InferredType.INTEGER
    if _looks_like_float(stripped):
        return InferredType.FLOAT
    return InferredType.TEXT


def _looks_like_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _looks_like_timestamp(text: str) -> bool:
    # Conservative, consistent with app.services.ingestion's own
    # flow_timestamp parsing: only an unambiguous ISO 8601 string counts.
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return False
    return True


def _looks_like_integer(text: str) -> bool:
    body = text[1:] if text and text[0] in "+-" else text
    return bool(body) and body.isdigit()


def _looks_like_float(text: str) -> bool:
    if text.lower() in _NON_NUMERIC_FLOAT_TEXT:
        return False
    try:
        float(text)
    except ValueError:
        return False
    return True


class ColumnTypeTracker:
    """Incremental, O(1)-memory type classification for one column."""

    def __init__(self) -> None:
        self._observed: set[InferredType] = set()

    def observe(self, text: str) -> None:
        category = classify_value(text)
        if category is not None:
            self._observed.add(category)

    @property
    def inferred_type(self) -> InferredType:
        if not self._observed:
            return InferredType.EMPTY
        if len(self._observed) == 1:
            return next(iter(self._observed))
        return InferredType.MIXED
