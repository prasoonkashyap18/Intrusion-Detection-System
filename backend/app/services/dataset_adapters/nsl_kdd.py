"""NSL-KDD / KDD Cup 99-style adapter.

Recognizes the standard 41-feature NSL-KDD column layout by its most
distinctive, long-stable column names. The official NSL-KDD distribution
files ship without a header row at all; this adapter can only recognize a
CSV that already has one (the project's ingestion layer requires a header
for every upload — see `app.services.ingestion`), consistent with how this
whole pipeline already expects headered CSVs.

Mapping confidence: NSL-KDD has no raw source/destination IP or port
columns (it is a host/service/flag-derived feature set, not a flow-capture
one), so this adapter never produces `source_ip`/`destination_ip`/
`source_port`/`destination_port`. `src_bytes`/`dst_bytes` are mapped to
`forward_byte_count`/`backward_byte_count` (NSL-KDD's own documentation
defines these as bytes sent in each direction of the connection — a
confident, direct mapping). The many derived-rate and host-count columns
(`count`, `srv_count`, `*_rate`, `dst_host_*`) have no equivalent in this
project's canonical vocabulary and are deliberately left unmapped — they
reach `CanonicalDatasetRecord.unknown_fields` rather than being forced into
a canonical field they don't actually mean.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.dataset_adapters._common import adapt_with_column_map, header_matches
from app.services.dataset_adapters.canonical import CanonicalDatasetRecord
from app.services.ingestion import NetworkFlowRecord

# The five most distinctive, long-stable NSL-KDD column names, required
# together. Any one alone is too generic (other schemas also have a
# "duration" or "service" column); this combination is not.
_REQUIRED_COLUMNS = frozenset({"protocol_type", "service", "flag", "src_bytes", "dst_bytes"})

_COLUMN_MAP: dict[str, str] = {
    "duration": "flow_duration",
    "protocol_type": "protocol",
    "src_bytes": "forward_byte_count",
    "dst_bytes": "backward_byte_count",
}

# Public NSL-KDD CSV releases vary in what they call the class column;
# "class" and "label" are the two most common.
_LABEL_COLUMNS = frozenset({"class", "label"})


class NslKddStyleAdapter:
    schema_id = "nsl-kdd-style"
    description = (
        "NSL-KDD / KDD Cup 99-style schema: host/service/flag-derived connection "
        "features, no raw IP or port columns. Detected from protocol_type, service, "
        "flag, src_bytes and dst_bytes appearing together."
    )

    def can_handle(self, header: Sequence[str]) -> bool:
        return header_matches(header, _REQUIRED_COLUMNS)

    def adapt(self, record: NetworkFlowRecord) -> CanonicalDatasetRecord:
        return adapt_with_column_map(
            record,
            schema_id=self.schema_id,
            column_map=_COLUMN_MAP,
            label_columns=_LABEL_COLUMNS,
            # No distinct attack-category column exists in NSL-KDD: the
            # class/label column already carries the specific attack name
            # (e.g. "neptune", "smurf") or "normal" — there is nothing
            # separate to extract, so this is intentionally empty.
            attack_category_columns=(),
        )
