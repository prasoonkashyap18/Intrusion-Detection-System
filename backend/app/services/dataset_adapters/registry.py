"""The dataset-adapter registry: every adapter this build knows about, and
schema-based selection among them.

Adding a new dataset family is meant to require only: write a
`DatasetAdapter` implementation (see `base.py`) and append an instance to
`ADAPTERS` below. Nothing in `ingestion.py`, `feature_extraction.py` or
`batch_processor.py` needs to change — see "No dataset-specific core logic"
in backend/README.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.services.dataset_adapters.base import DatasetAdapter
from app.services.dataset_adapters.cicids import CicidsStyleAdapter
from app.services.dataset_adapters.nsl_kdd import NslKddStyleAdapter
from app.services.dataset_adapters.unsw_nb15 import UnswNb15StyleAdapter

ADAPTERS: tuple[DatasetAdapter, ...] = (
    NslKddStyleAdapter(),
    UnswNb15StyleAdapter(),
    CicidsStyleAdapter(),
)


@dataclass(frozen=True)
class AdapterSelection:
    """The explicit result of trying to recognize a header. Never a bare
    adapter-or-`None`: callers must handle "no adapter matched" and
    "more than one adapter matched" without mistaking either for a
    confident identification.
    """

    adapter: DatasetAdapter | None
    """The uniquely matched adapter, or `None` if zero or more than one
    adapter recognized the header — see `candidates`."""

    candidates: tuple[str, ...]
    """`schema_id` of every adapter whose `can_handle` returned True.
    Empty means unsupported/unknown; more than one entry means an
    ambiguous header that more than one adapter's signature matched. In
    both cases `adapter` is `None`: guessing between candidates, or
    assuming a schema that matched nothing, would not be safe."""

    @property
    def is_unsupported(self) -> bool:
        """True when no adapter recognized the header at all (as opposed
        to an ambiguous match between several)."""
        return len(self.candidates) == 0

    @property
    def is_ambiguous(self) -> bool:
        """True when more than one adapter's signature matched the same
        header."""
        return len(self.candidates) > 1


def select_adapter(header: Sequence[str]) -> AdapterSelection:
    """Tries every registered adapter's `can_handle` against `header`.

    Never guesses: returns `adapter=None` when zero adapters recognize the
    header (unsupported schema) or when more than one does (an ambiguous
    overlap) — callers must not silently fall back to treating the data as
    any particular dataset in either case. Operates on header text only;
    does not read or require any CSV row data.
    """
    matches = [adapter for adapter in ADAPTERS if adapter.can_handle(header)]
    candidates = tuple(adapter.schema_id for adapter in matches)
    if len(matches) == 1:
        return AdapterSelection(adapter=matches[0], candidates=candidates)
    return AdapterSelection(adapter=None, candidates=candidates)
