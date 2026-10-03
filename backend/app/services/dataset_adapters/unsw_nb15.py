"""UNSW-NB15-style adapter.

Recognizes the UNSW-NB15 column layout by its most distinctive, long-stable
column names: `srcip`, `dstip`, `sport`, `dsport` and `proto` together are a
combination specific to this dataset family — NSL-KDD has no IP/port
columns at all, and other schemas that do carry source/destination IP and
port information spell them differently (e.g. "Source IP", "Source Port"
with a space, which normalizes to `source_ip`/`source_port`, not `srcip`/
`sport`).

Mapping confidence: `srcip`/`dstip`/`sport`/`dsport`/`proto`/`dur` map
directly and confidently to this project's canonical vocabulary.
`sbytes`/`dbytes`/`spkts`/`dpkts` are UNSW-NB15's own documented "source to
destination" / "destination to source" byte and packet counts — mapped to
`forward_byte_count`/`backward_byte_count`/`forward_packet_count`/
`backward_packet_count`. The many TTL, window, jitter, round-trip-time and
`ct_*` connection-count columns have no equivalent in this project's
canonical vocabulary and are deliberately left unmapped, reaching
`CanonicalDatasetRecord.unknown_fields` rather than being forced into a
canonical field they don't actually mean.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.dataset_adapters._common import adapt_with_column_map, header_matches
from app.services.dataset_adapters.canonical import CanonicalDatasetRecord
from app.services.ingestion import NetworkFlowRecord

_REQUIRED_COLUMNS = frozenset({"srcip", "dstip", "sport", "dsport", "proto"})

_COLUMN_MAP: dict[str, str] = {
    "srcip": "source_ip",
    "dstip": "destination_ip",
    "sport": "source_port",
    "dsport": "destination_port",
    "proto": "protocol",
    "dur": "flow_duration",
    "sbytes": "forward_byte_count",
    "dbytes": "backward_byte_count",
    "spkts": "forward_packet_count",
    "dpkts": "backward_packet_count",
}

_LABEL_COLUMNS = frozenset({"label"})
_ATTACK_CATEGORY_COLUMNS = frozenset({"attack_cat"})


class UnswNb15StyleAdapter:
    schema_id = "unsw-nb15-style"
    description = (
        "UNSW-NB15-style schema: raw flow capture with source/destination IP and "
        "port columns. Detected from srcip, dstip, sport, dsport and proto "
        "appearing together."
    )

    def can_handle(self, header: Sequence[str]) -> bool:
        return header_matches(header, _REQUIRED_COLUMNS)

    def adapt(self, record: NetworkFlowRecord) -> CanonicalDatasetRecord:
        return adapt_with_column_map(
            record,
            schema_id=self.schema_id,
            column_map=_COLUMN_MAP,
            label_columns=_LABEL_COLUMNS,
            attack_category_columns=_ATTACK_CATEGORY_COLUMNS,
        )
