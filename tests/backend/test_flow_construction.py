"""Tests for app.services.flow_construction: packet-to-flow grouping,
direction convention, idle-timeout finalization, and derived stats.
"""

from __future__ import annotations

import pytest

from app.services.flow_construction import (
    FlowConstructor,
    PacketRecord,
    compute_flow_key,
    construct_flows,
)


def tcp_packet(t, src, sport, dst, dport, length=60, flags=0):
    return PacketRecord(
        timestamp=t, source_ip=src, destination_ip=dst, protocol_number=6,
        length=length, source_port=sport, destination_port=dport, tcp_flags=flags,
    )


def udp_packet(t, src, sport, dst, dport, length=60):
    return PacketRecord(
        timestamp=t, source_ip=src, destination_ip=dst, protocol_number=17,
        length=length, source_port=sport, destination_port=dport, tcp_flags=None,
    )


def icmp_packet(t, src, dst, length=60):
    return PacketRecord(
        timestamp=t, source_ip=src, destination_ip=dst, protocol_number=1,
        length=length, source_port=None, destination_port=None, tcp_flags=None,
    )


class TestComputeFlowKey:
    def test_direction_independent(self):
        a = tcp_packet(0, "10.0.0.1", 1234, "10.0.0.2", 80)
        b = tcp_packet(1, "10.0.0.2", 80, "10.0.0.1", 1234)
        assert compute_flow_key(a) == compute_flow_key(b)

    def test_different_ports_are_different_flows(self):
        a = tcp_packet(0, "10.0.0.1", 1234, "10.0.0.2", 80)
        b = tcp_packet(0, "10.0.0.1", 5555, "10.0.0.2", 80)
        assert compute_flow_key(a) != compute_flow_key(b)

    def test_icmp_uses_no_fabricated_port(self):
        key = compute_flow_key(icmp_packet(0, "10.0.0.1", "10.0.0.2"))
        assert key[3] == -1 and key[4] == -1


class TestBidirectionalFlowConstruction:
    def test_two_directions_form_one_flow(self):
        packets = [
            tcp_packet(0.0, "10.0.0.1", 1234, "10.0.0.2", 80, flags=2),
            tcp_packet(0.1, "10.0.0.2", 80, "10.0.0.1", 1234, flags=18),
        ]
        flows = list(construct_flows(iter(packets), idle_timeout_seconds=5.0))
        assert len(flows) == 1
        assert flows[0].packet_count == 2

    def test_forward_direction_is_the_first_packets_source(self):
        packets = [
            tcp_packet(0.0, "10.0.0.1", 1234, "10.0.0.2", 80, length=100),
            tcp_packet(0.1, "10.0.0.2", 80, "10.0.0.1", 1234, length=50),
        ]
        flows = list(construct_flows(iter(packets), idle_timeout_seconds=5.0))
        flow = flows[0]
        assert flow.source_ip == "10.0.0.1"
        assert flow.forward_packet_count == 1
        assert flow.forward_byte_count == 100
        assert flow.backward_packet_count == 1
        assert flow.backward_byte_count == 50

    def test_tcp_flags_union_is_bitwise_or_of_all_packets(self):
        packets = [
            tcp_packet(0.0, "10.0.0.1", 1234, "10.0.0.2", 80, flags=2),   # SYN
            tcp_packet(0.1, "10.0.0.2", 80, "10.0.0.1", 1234, flags=18),  # SYN+ACK
            tcp_packet(0.2, "10.0.0.1", 1234, "10.0.0.2", 80, flags=16),  # ACK
        ]
        flows = list(construct_flows(iter(packets), idle_timeout_seconds=5.0))
        assert flows[0].tcp_flags_union == (2 | 18 | 16)

    def test_non_tcp_flow_has_no_tcp_flags(self):
        packets = [udp_packet(0.0, "10.0.0.1", 53, "10.0.0.2", 5353)]
        flows = list(construct_flows(iter(packets), idle_timeout_seconds=5.0))
        assert flows[0].tcp_flags_union is None
        assert flows[0].is_tcp is False


class TestPacketAndByteCounts:
    def test_counts_match_known_input(self):
        packets = [
            tcp_packet(0.0, "10.0.0.1", 1234, "10.0.0.2", 80, length=100),
            tcp_packet(0.1, "10.0.0.1", 1234, "10.0.0.2", 80, length=200),
            tcp_packet(0.2, "10.0.0.2", 80, "10.0.0.1", 1234, length=50),
        ]
        flows = list(construct_flows(iter(packets), idle_timeout_seconds=5.0))
        assert flows[0].packet_count == 3
        assert flows[0].byte_count == 350


class TestDurationAndRates:
    def test_duration_matches_first_and_last_timestamp(self):
        packets = [tcp_packet(5.0, "a", 1, "b", 2), tcp_packet(8.5, "b", 2, "a", 1)]
        flows = list(construct_flows(iter(packets), idle_timeout_seconds=5.0))
        assert flows[0].duration_seconds == pytest.approx(3.5)

    def test_single_packet_flow_has_zero_duration_and_undefined_rate(self):
        flows = list(construct_flows(iter([tcp_packet(5.0, "a", 1, "b", 2)]), idle_timeout_seconds=5.0))
        flow = flows[0]
        assert flow.duration_seconds == 0.0
        assert flow.packet_rate is None  # undefined, never fabricated as 0
        assert flow.byte_rate is None

    def test_rate_is_count_over_duration_when_duration_is_positive(self):
        packets = [tcp_packet(0.0, "a", 1, "b", 2, length=100), tcp_packet(2.0, "b", 2, "a", 1, length=100)]
        flows = list(construct_flows(iter(packets), idle_timeout_seconds=5.0))
        flow = flows[0]
        assert flow.packet_rate == pytest.approx(1.0)
        assert flow.byte_rate == pytest.approx(100.0)


class TestIdleTimeoutFinalization:
    def test_a_gap_beyond_timeout_starts_a_new_flow(self):
        packets = [
            tcp_packet(0.0, "a", 1, "b", 2),
            tcp_packet(100.0, "a", 1, "b", 2),  # same 5-tuple, long gap
        ]
        flows = list(construct_flows(iter(packets), idle_timeout_seconds=10.0))
        assert len(flows) == 2
        assert all(f.packet_count == 1 for f in flows)

    def test_a_gap_within_timeout_stays_one_flow(self):
        packets = [tcp_packet(0.0, "a", 1, "b", 2), tcp_packet(5.0, "a", 1, "b", 2)]
        flows = list(construct_flows(iter(packets), idle_timeout_seconds=10.0))
        assert len(flows) == 1
        assert flows[0].packet_count == 2

    def test_rejects_non_positive_idle_timeout(self):
        with pytest.raises(ValueError):
            FlowConstructor(idle_timeout_seconds=0)


class TestFlowConstructorIdleFlows:
    def test_idle_flows_finalizes_without_a_new_packet(self):
        constructor = FlowConstructor(idle_timeout_seconds=10.0)
        constructor.add_packet(tcp_packet(0.0, "a", 1, "b", 2))
        assert constructor.open_flow_count == 1

        finalized = list(constructor.idle_flows(now=50.0))
        assert len(finalized) == 1
        assert constructor.open_flow_count == 0

    def test_idle_flows_leaves_recently_active_flows_open(self):
        constructor = FlowConstructor(idle_timeout_seconds=10.0)
        constructor.add_packet(tcp_packet(0.0, "a", 1, "b", 2))
        finalized = list(constructor.idle_flows(now=5.0))
        assert finalized == []
        assert constructor.open_flow_count == 1


class TestFlush:
    def test_flush_finalizes_all_remaining_open_flows(self):
        constructor = FlowConstructor(idle_timeout_seconds=100.0)
        constructor.add_packet(tcp_packet(0.0, "a", 1, "b", 2))
        constructor.add_packet(udp_packet(0.0, "c", 3, "d", 4))
        flushed = list(constructor.flush())
        assert len(flushed) == 2
        assert constructor.open_flow_count == 0

    def test_nothing_is_silently_discarded(self):
        packets = [tcp_packet(float(i), "a", 1, "b", 2) for i in range(5)]
        flows = list(construct_flows(iter(packets), idle_timeout_seconds=100.0))
        assert len(flows) == 1
        assert flows[0].packet_count == 5


class TestMultipleUnrelatedFlows:
    def test_unrelated_flows_never_merge(self):
        packets = [
            tcp_packet(0.0, "10.0.0.1", 1234, "10.0.0.2", 80),
            udp_packet(0.0, "10.0.0.3", 53, "10.0.0.4", 5353),
            icmp_packet(0.0, "10.0.0.5", "10.0.0.6"),
        ]
        flows = list(construct_flows(iter(packets), idle_timeout_seconds=5.0))
        assert len(flows) == 3
        assert {f.protocol_number for f in flows} == {6, 17, 1}
