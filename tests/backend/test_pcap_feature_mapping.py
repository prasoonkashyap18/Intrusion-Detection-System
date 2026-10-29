"""Tests for app.services.pcap_feature_mapping: FinalizedFlow ->
MappedFeatureSet via the existing, unmodified map_canonical_record.
"""

from __future__ import annotations

from app.services.feature_extraction import FEATURE_SCHEMA, FeatureStatus
from app.services.flow_construction import FinalizedFlow
from app.services.pcap_feature_mapping import PCAP_DATASET_SCHEMA, flow_to_mapped_feature_set


def make_flow(**overrides) -> FinalizedFlow:
    defaults = dict(
        flow_key=(6, "10.0.0.1", "10.0.0.2", 80, 1234),
        source_ip="10.0.0.1", destination_ip="10.0.0.2", source_port=1234, destination_port=80,
        protocol_number=6, is_tcp=True, first_timestamp=0.0, last_timestamp=2.0,
        packet_count=3, byte_count=300, forward_packet_count=2, forward_byte_count=200,
        backward_packet_count=1, backward_byte_count=100, tcp_flags_union=18,
    )
    return FinalizedFlow(**{**defaults, **overrides})


class TestFlowToMappedFeatureSet:
    def test_dataset_schema_is_pcap_flow(self):
        mapped = flow_to_mapped_feature_set(make_flow(), row_number=1)
        assert mapped.dataset_schema == PCAP_DATASET_SCHEMA

    def test_all_measured_values_are_present(self):
        mapped = flow_to_mapped_feature_set(make_flow(), row_number=1)
        for name in ("source_port", "destination_port", "flow_duration", "packet_count", "byte_count", "tcp_flags"):
            assert mapped.features[name].status == FeatureStatus.PRESENT
            assert mapped.features[name].value is not None

    def test_ip_addresses_are_numerically_encoded(self):
        mapped = flow_to_mapped_feature_set(make_flow(), row_number=1)
        assert mapped.features["source_ip_numeric"].value == float(int.from_bytes(bytes([10, 0, 0, 1])))
        assert mapped.features["destination_ip_numeric"].value == float(int.from_bytes(bytes([10, 0, 0, 2])))

    def test_undefined_rate_is_missing_not_zero(self):
        # A single-timestamp flow (zero duration) has an undefined rate.
        flow = make_flow(first_timestamp=5.0, last_timestamp=5.0)
        mapped = flow_to_mapped_feature_set(flow, row_number=1)
        assert mapped.features["packet_rate"].status == FeatureStatus.MISSING
        assert mapped.features["packet_rate"].value is None
        assert mapped.features["byte_rate"].status == FeatureStatus.MISSING

    def test_non_tcp_flow_has_missing_tcp_flags_not_zero(self):
        flow = make_flow(protocol_number=17, is_tcp=False, tcp_flags_union=None, source_port=53, destination_port=5353)
        mapped = flow_to_mapped_feature_set(flow, row_number=1)
        assert mapped.features["tcp_flags"].status == FeatureStatus.MISSING
        assert mapped.features["tcp_flags"].value is None

    def test_icmp_flow_has_missing_ports_not_zero(self):
        flow = make_flow(protocol_number=1, is_tcp=False, tcp_flags_union=None, source_port=None, destination_port=None)
        mapped = flow_to_mapped_feature_set(flow, row_number=1)
        assert mapped.features["source_port"].status == FeatureStatus.MISSING
        assert mapped.features["destination_port"].status == FeatureStatus.MISSING

    def test_feature_vector_follows_feature_schema_order(self):
        mapped = flow_to_mapped_feature_set(make_flow(), row_number=1)
        vector = mapped.feature_vector()
        assert len(vector) == len(FEATURE_SCHEMA)

    def test_row_number_is_preserved(self):
        mapped = flow_to_mapped_feature_set(make_flow(), row_number=42)
        assert mapped.row_number == 42

    def test_protocol_number_is_the_real_ip_header_value(self):
        mapped = flow_to_mapped_feature_set(make_flow(protocol_number=17, is_tcp=False, tcp_flags_union=None), row_number=1)
        assert mapped.features["protocol_number"].value == 17
        assert mapped.features["protocol_number"].status == FeatureStatus.PRESENT

    def test_byte_and_packet_counts_match_the_flow_exactly(self):
        flow = make_flow(packet_count=7, byte_count=777, forward_packet_count=4, backward_packet_count=3)
        mapped = flow_to_mapped_feature_set(flow, row_number=1)
        assert mapped.features["packet_count"].value == 7.0
        assert mapped.features["byte_count"].value == 777.0
        assert mapped.features["forward_packet_count"].value == 4.0
        assert mapped.features["backward_packet_count"].value == 3.0
