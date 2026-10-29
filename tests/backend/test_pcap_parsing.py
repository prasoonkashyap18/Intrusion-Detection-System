"""Tests for app.services.pcap_parsing: real-content format detection,
packet extraction for IPv4/IPv6/TCP/UDP/ICMP, and safe handling of
empty, malformed, truncated, and unsupported files.

Fixtures are built in-memory with `scapy` at test time (never a
checked-in binary capture file) and written to `tmp_path`, so nothing
here depends on a committed PCAP fixture or real captured traffic.
"""

from __future__ import annotations

import pytest

from app.services.pcap_parsing import (
    PacketParsingStats,
    PcapParsingError,
    PcapValidationError,
    detect_capture_format,
    has_pcap_extension,
    iter_packets,
    validate_capture_file,
)

_ETH = dict(src="00:11:22:33:44:55", dst="66:77:88:99:aa:bb")


def _write_pcap(tmp_path, packets, name="capture.pcap"):
    from scapy.all import wrpcap

    path = tmp_path / name
    wrpcap(str(path), packets)
    return path


class TestHasPcapExtension:
    def test_accepts_pcap_and_pcapng(self):
        assert has_pcap_extension("traffic.pcap")
        assert has_pcap_extension("traffic.pcapng")
        assert has_pcap_extension("TRAFFIC.PCAP")

    def test_rejects_other_extensions(self):
        assert not has_pcap_extension("traffic.csv")
        assert not has_pcap_extension("traffic.txt")
        assert not has_pcap_extension("traffic")


class TestDetectCaptureFormat:
    def test_detects_real_pcap_content(self, tmp_path):
        from scapy.all import IP, TCP, Ether

        path = _write_pcap(tmp_path, [Ether(**_ETH) / IP(src="10.0.0.1", dst="10.0.0.2") / TCP()])
        assert detect_capture_format(path) == "pcap"

    def test_rejects_content_that_is_not_a_capture(self, tmp_path):
        path = tmp_path / "fake.pcap"
        path.write_bytes(b"this is not a capture file at all, just text")
        assert detect_capture_format(path) is None

    def test_an_extension_lie_is_not_trusted(self, tmp_path):
        # A ".pcap"-named file whose content is not actually a capture
        # must still be detected as invalid -- content over filename.
        path = tmp_path / "definitely_not_a_capture.pcap"
        path.write_bytes(b"\x00\x01\x02\x03" + b"garbage" * 10)
        assert detect_capture_format(path) is None

    def test_missing_file_returns_none_not_an_exception(self, tmp_path):
        assert detect_capture_format(tmp_path / "does_not_exist.pcap") is None


class TestValidateCaptureFile:
    def test_valid_capture_passes(self, tmp_path):
        from scapy.all import IP, TCP, Ether

        path = _write_pcap(tmp_path, [Ether(**_ETH) / IP(src="10.0.0.1", dst="10.0.0.2") / TCP()])
        assert validate_capture_file(path) == "pcap"

    def test_empty_file_is_rejected(self, tmp_path):
        path = tmp_path / "empty.pcap"
        path.write_bytes(b"")
        with pytest.raises(PcapValidationError) as exc_info:
            validate_capture_file(path)
        assert exc_info.value.status_code == 400

    def test_unrecognized_content_is_rejected_with_415(self, tmp_path):
        path = tmp_path / "fake.pcap"
        path.write_bytes(b"not a real capture" * 5)
        with pytest.raises(PcapValidationError) as exc_info:
            validate_capture_file(path)
        assert exc_info.value.status_code == 415

    def test_truncated_header_is_rejected(self, tmp_path):
        path = tmp_path / "truncated.pcap"
        path.write_bytes(b"\xa1\xb2")  # only 2 of 4 magic bytes
        with pytest.raises(PcapValidationError) as exc_info:
            validate_capture_file(path)
        assert exc_info.value.status_code == 415


class TestIterPacketsProtocols:
    def test_ipv4_tcp_packet_fields(self, tmp_path):
        from scapy.all import IP, TCP, Ether

        pkt = Ether(**_ETH) / IP(src="10.0.0.1", dst="10.0.0.2") / TCP(sport=1111, dport=80, flags="S")
        path = _write_pcap(tmp_path, [pkt])
        stats = PacketParsingStats()
        records = list(iter_packets(path, stats))

        assert len(records) == 1
        record = records[0]
        assert record.source_ip == "10.0.0.1"
        assert record.destination_ip == "10.0.0.2"
        assert record.protocol_number == 6
        assert record.source_port == 1111
        assert record.destination_port == 80
        assert record.tcp_flags == 2  # SYN

    def test_ipv4_udp_packet_has_no_tcp_flags(self, tmp_path):
        from scapy.all import IP, UDP, Ether

        pkt = Ether(**_ETH) / IP(src="10.0.0.1", dst="10.0.0.2") / UDP(sport=53, dport=5353)
        path = _write_pcap(tmp_path, [pkt])
        stats = PacketParsingStats()
        records = list(iter_packets(path, stats))

        assert records[0].protocol_number == 17
        assert records[0].tcp_flags is None

    def test_ipv6_udp_packet_fields(self, tmp_path):
        from scapy.all import IPv6, UDP, Ether

        pkt = Ether(**_ETH) / IPv6(src="::1", dst="::2") / UDP(sport=1234, dport=80)
        path = _write_pcap(tmp_path, [pkt])
        stats = PacketParsingStats()
        records = list(iter_packets(path, stats))

        assert records[0].source_ip == "::1"
        assert records[0].destination_ip == "::2"
        assert records[0].protocol_number == 17

    def test_icmp_packet_has_no_ports(self, tmp_path):
        from scapy.all import ICMP, IP, Ether

        pkt = Ether(**_ETH) / IP(src="10.0.0.1", dst="10.0.0.2") / ICMP()
        path = _write_pcap(tmp_path, [pkt])
        stats = PacketParsingStats()
        records = list(iter_packets(path, stats))

        assert records[0].protocol_number == 1
        assert records[0].source_port is None
        assert records[0].destination_port is None

    def test_non_ip_packet_is_skipped_not_misrepresented(self, tmp_path):
        from scapy.all import ARP, Ether

        pkt = Ether(**_ETH) / ARP()
        path = _write_pcap(tmp_path, [pkt])
        stats = PacketParsingStats()
        records = list(iter_packets(path, stats))

        assert records == []
        assert stats.packets_read == 1
        assert stats.packets_skipped_no_ip == 1

    def test_mixed_capture_counts_are_consistent(self, tmp_path):
        from scapy.all import ARP, ICMP, IP, IPv6, TCP, UDP, Ether

        packets = [
            Ether(**_ETH) / IP(src="10.0.0.1", dst="10.0.0.2") / TCP(sport=1, dport=2),
            Ether(**_ETH) / IP(src="10.0.0.3", dst="10.0.0.4") / UDP(sport=3, dport=4),
            Ether(**_ETH) / IPv6(src="::1", dst="::2") / UDP(sport=5, dport=6),
            Ether(**_ETH) / IP(src="10.0.0.5", dst="10.0.0.6") / ICMP(),
            Ether(**_ETH) / ARP(),
        ]
        path = _write_pcap(tmp_path, packets)
        stats = PacketParsingStats()
        records = list(iter_packets(path, stats))

        assert stats.packets_read == 5
        assert stats.packets_parsed == 4
        assert stats.packets_skipped_no_ip == 1
        assert len(records) == 4


class TestEmptyAndMalformedCaptures:
    def test_empty_capture_yields_no_packets_not_an_error(self, tmp_path):
        from scapy.all import wrpcap

        path = tmp_path / "empty_valid.pcap"
        wrpcap(str(path), [])
        stats = PacketParsingStats()
        records = list(iter_packets(path, stats))
        assert records == []
        assert stats.packets_read == 0

    def test_opening_a_non_capture_file_raises_a_clear_error(self, tmp_path):
        path = tmp_path / "garbage.pcap"
        path.write_bytes(b"\xa1\xb2\xc3\xd4" + b"\x00" * 2)  # magic ok, but truncated global header
        stats = PacketParsingStats()
        with pytest.raises(PcapParsingError):
            list(iter_packets(path, stats))
