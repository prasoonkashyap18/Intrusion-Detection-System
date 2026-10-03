"""CICIDS-style adapter (e.g. CICIDS2017 / CSE-CIC-IDS2018 flow exports).

Recognizes the CICFlowMeter-style column layout by its most distinctive,
widely-reproduced column names: "Flow Duration", "Total Fwd Packets",
"Total Backward Packets" and "Label" together.

Mapping confidence is intentionally the most conservative of this step's
three adapters. CICIDS releases are not a single fixed schema — public
mirrors and the different dataset years (2017/2018/...) vary in exact
column spelling, spacing and which optional columns (raw source/
destination IP and port in particular) are included or stripped for
privacy. This adapter therefore:

- requires only the small combination of column names that are
  consistently present across releases to recognize the schema at all;
- maps only the handful of columns this project is confident mean the
  same thing in every release (flow duration, forward/backward packet and
  byte totals, and an optional IP/port/protocol set that some releases
  include);
- leaves everything else — the 70+ statistical flow features most CICIDS
  releases also carry (IAT, window sizes, flag counts, active/idle times,
  ...) — unmapped, reaching `CanonicalDatasetRecord.unknown_fields` rather
  than guessed at. This project's canonical schema was never meant to
  absorb CICIDS' full, much larger feature set; see Step 18's
  documentation for why that is not a gap this adapter should paper over.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.dataset_adapters._common import adapt_with_column_map, header_matches
from app.services.dataset_adapters.canonical import CanonicalDatasetRecord
from app.services.ingestion import NetworkFlowRecord

_REQUIRED_COLUMNS = frozenset({"flow_duration", "total_fwd_packets", "total_backward_packets", "label"})

_COLUMN_MAP: dict[str, str] = {
    "flow_duration": "flow_duration",
    "total_fwd_packets": "forward_packet_count",
    "total_backward_packets": "backward_packet_count",
    "total_length_of_fwd_packets": "forward_byte_count",
    "total_length_of_bwd_packets": "backward_byte_count",
    # Present in some CICIDS releases (e.g. the flow-labelled GeneratedLabelledFlows
    # files) and absent in others (some distributions drop raw IPs for privacy);
    # mapped when present, simply unmatched — not missing a required column — when not.
    "source_ip": "source_ip",
    "src_ip": "source_ip",
    "destination_ip": "destination_ip",
    "dst_ip": "destination_ip",
    "source_port": "source_port",
    "src_port": "source_port",
    "destination_port": "destination_port",
    "dst_port": "destination_port",
    "protocol": "protocol",
}

_LABEL_COLUMNS = frozenset({"label"})


class CicidsStyleAdapter:
    schema_id = "cicids-style"
    description = (
        "CICIDS-style schema (CICFlowMeter flow export): Flow Duration, Total Fwd "
        "Packets, Total Backward Packets and Label appearing together. Only a small, "
        "high-confidence subset of its much larger column set is mapped; the rest is "
        "preserved as unknown fields."
    )

    def can_handle(self, header: Sequence[str]) -> bool:
        return header_matches(header, _REQUIRED_COLUMNS)

    def adapt(self, record: NetworkFlowRecord) -> CanonicalDatasetRecord:
        return adapt_with_column_map(
            record,
            schema_id=self.schema_id,
            column_map=_COLUMN_MAP,
            label_columns=_LABEL_COLUMNS,
            # CICIDS' Label column already carries the specific attack
            # name (e.g. "DDoS", "PortScan") or "BENIGN" — there is no
            # separate category column to extract.
            attack_category_columns=(),
        )
