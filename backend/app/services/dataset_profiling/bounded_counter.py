"""A bounded value counter: the profiler's one memory-safety primitive for
"how many distinct X are there" questions over a potentially large CSV.

Tracks exact counts for up to `limit` distinct values. Once that many
distinct values have been seen, it stops accepting *new* keys and reports
itself `truncated` — but keeps updating the count for every key it already
has, so those counts stay exact for however many rows were processed.
`truncated` is the flag callers must check before treating `distinct_count`
as a complete picture; see `app.services.dataset_profiling.profile` for how
`ColumnProfile.unique_count_is_exact` / `LabelSummary.counts_truncated`
surface this.
"""

from __future__ import annotations


class BoundedValueCounter:
    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self._limit = limit
        self._counts: dict[str, int] = {}
        self.truncated = False

    def add(self, value: str) -> None:
        if value in self._counts:
            self._counts[value] += 1
            return
        if len(self._counts) >= self._limit:
            self.truncated = True
            return
        self._counts[value] = 1

    @property
    def counts(self) -> dict[str, int]:
        """A copy — callers must not be able to mutate internal state."""
        return dict(self._counts)

    @property
    def distinct_count(self) -> int:
        """Exact while `not truncated`; a lower bound once `truncated`."""
        return len(self._counts)
