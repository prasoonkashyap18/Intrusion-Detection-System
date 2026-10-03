"""The dataset-adapter contract: how a dataset-specific CSV schema becomes
the project's canonical representation, without the core pipeline
(ingestion, feature extraction, batch processing) knowing any dataset's
column names.

    NetworkFlowRecord (app.services.ingestion)
        |
    DatasetAdapter.adapt()
        |
    CanonicalDatasetRecord (canonical.py)

Adapters are selected from CSV header characteristics alone (see
`registry.select_adapter`) — never from a user-supplied "dataset" string,
which would let a request simply claim to be any dataset it likes. An
adapter that is not confident it recognizes a header must say so
(`can_handle` returns `False`); selection returns an explicit "no adapter
matched" result rather than guessing — see `registry.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.services.dataset_adapters.canonical import CanonicalDatasetRecord
from app.services.ingestion import NetworkFlowRecord


class DatasetAdapter(Protocol):
    """One dataset family's translation from its own CSV column names to
    the project's canonical vocabulary. Implementations are small,
    stateless and independent of each other: adding one never requires
    changing another, `ingestion.py`, `feature_extraction.py`, or
    `batch_processor.py` — see "No dataset-specific core logic" in
    backend/README.md.
    """

    #: Short, stable identifier (e.g. "nsl-kdd-style"), used as
    #: `CanonicalDatasetRecord.dataset_schema` and in logs. Never shown to
    #: end users as a verified claim about the data's real origin — it
    #: names which adapter's mapping was applied, nothing more.
    schema_id: str

    #: One sentence a developer can read to know what this adapter is for.
    description: str

    def can_handle(self, header: Sequence[str]) -> bool:
        """True if this adapter recognizes `header` as its dataset family.

        Must be conservative: a false positive silently mis-maps a
        dataset this adapter does not actually understand. Implementations
        require several of their most distinctive column names together,
        never a single generic one — a column called "protocol" alone
        appears in many schemas and proves nothing about which one this
        is. Operates on header text only, never on file content, so
        identifying a dataset never requires reading more than one CSV row.
        """
        ...

    def adapt(self, record: NetworkFlowRecord) -> CanonicalDatasetRecord:
        """Translates one already-ingested row into the canonical
        representation. Must not re-parse or re-validate CSV structure —
        that is ingestion's job and is already done by the time a
        `NetworkFlowRecord` exists; must not parse values into numbers —
        that is `feature_extraction.py`'s job. This method only renames
        columns and (when confidently recognized) pulls out label/
        attack-category text.
        """
        ...
