"""PCAP/PCAPNG file validation and packet parsing — the only module in
this codebase that imports `scapy`. Everything downstream
(`app.services.flow_construction`, `app.services.pcap_feature_mapping`)
works from the plain `PacketRecord` dataclass this module produces, so
a different parsing library could replace this one module alone.

Dependency choice: `scapy` (added to `backend/requirements.txt`) was
chosen because it reads and writes `.pcap`/`.pcapng` files in pure
Python (`scapy.utils.PcapReader`, used streaming — never `rdpcap`,
which loads an entire file into memory) and — the reason it was picked
over a file-only library like `dpkt` — the *same* library also performs
live capture (`scapy.sendrecv.sniff`), which Step 42 needs; one
dependency instead of two. Live capture additionally needs a packet-
capture backend installed on the OS (Npcap/WinPcap on Windows, libpcap
on Linux/macOS) — see `app.services.live_capture`'s module docstring
and backend/README.md's "Live Traffic Capture" section for exactly what
that means and how its absence is reported.

Validation inspects real file content, never trusts a filename or
client-supplied content type: `detect_capture_format` reads only the
first 4 bytes and checks them against the real PCAP/PCAPNG magic
numbers.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import ApiException
from app.services.flow_construction import PacketRecord

logger = logging.getLogger("ai_ids")

# Classic PCAP magic numbers (big/little-endian, microsecond and
# nanosecond-resolution variants) and the PCAPNG Section Header Block's
# fixed block-type magic — the complete, real set of values defined by
# the two formats' own specifications, not a guess.
_PCAP_MAGICS = frozenset({b"\xa1\xb2\xc3\xd4", b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\x3c\x4d", b"\x4d\x3c\xb2\xa1"})
_PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"

SUPPORTED_EXTENSIONS = frozenset({".pcap", ".pcapng"})

# Only protocols with a genuine, well-known concept of "port" — matches
# app.services.flow_construction's own convention of using no fabricated
# port for anything else (e.g. ICMP).
_PORT_PROTOCOLS = frozenset({6, 17})  # tcp, udp


class PcapValidationError(ApiException):
    """A 415/400-shaped `ApiException` for a file that is not a
    genuine, usable capture — raised before any packet is ever parsed."""


class PcapParsingError(Exception):
    """Raised only when the capture itself cannot be processed safely
    at all (the underlying reader could not even be opened) — never for
    an individual malformed packet, which is tracked in
    `PacketParsingStats` instead and simply skipped. `message` is safe
    to surface to a caller."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class PacketParsingStats:
    """Real, measured counters for one parsing run — see the module
    docstring's "Malformed packets should not crash..." requirement.
    Mutated in place by `iter_packets` as it streams through a file."""

    packets_read: int = 0
    packets_parsed: int = 0
    packets_skipped_no_ip: int = 0
    """A packet with no IP/IPv6 layer at all (e.g. ARP) — cannot form a
    flow (no network-layer 5-tuple), counted and skipped, never
    misrepresented as IPv4."""
    packets_malformed: int = 0
    """A packet scapy itself could not parse/dissect — counted and
    skipped; the capture as a whole keeps processing."""


def detect_capture_format(path: Path) -> str | None:
    """Reads only the first 4 bytes of `path` and returns `"pcap"`,
    `"pcapng"`, or `None` if neither magic number matches — the file's
    real content, never its extension or declared content type.
    """
    try:
        with path.open("rb") as handle:
            header = handle.read(4)
    except OSError:
        return None

    if header in _PCAP_MAGICS:
        return "pcap"
    if header == _PCAPNG_MAGIC:
        return "pcapng"
    return None


def has_pcap_extension(filename: str) -> bool:
    lowered = filename.lower()
    return any(lowered.endswith(extension) for extension in SUPPORTED_EXTENSIONS)


def validate_capture_file(path: Path) -> str:
    """Raises `PcapValidationError` (400/415) for an empty or
    unrecognized file; returns the detected format (`"pcap"`/
    `"pcapng"`) for a genuine capture. Called after the file is fully
    stored, so this inspects real, complete content — never a partial
    upload or the client's claims about it.
    """
    try:
        size = path.stat().st_size
    except OSError:
        raise PcapValidationError(500, "pcap_unavailable", "The uploaded capture could not be read.") from None

    if size == 0:
        raise PcapValidationError(400, "empty_file", "The uploaded capture is empty.")

    capture_format = detect_capture_format(path)
    if capture_format is None:
        raise PcapValidationError(
            415, "unsupported_media_type", "The uploaded file is not a recognized PCAP or PCAPNG capture."
        )
    return capture_format


def _protocol_number_from_packet(packet) -> int | None:
    from scapy.layers.inet import IP
    from scapy.layers.inet6 import IPv6

    if packet.haslayer(IP):
        return int(packet[IP].proto)
    if packet.haslayer(IPv6):
        return int(packet[IPv6].nh)
    return None


def packet_to_record(packet, stats: PacketParsingStats) -> PacketRecord | None:
    from scapy.layers.inet import ICMP, TCP, UDP, IP
    from scapy.layers.inet6 import IPv6

    if packet.haslayer(IP):
        ip_layer = packet[IP]
    elif packet.haslayer(IPv6):
        ip_layer = packet[IPv6]
    else:
        stats.packets_skipped_no_ip += 1
        return None

    protocol_number = _protocol_number_from_packet(packet)
    if protocol_number is None:
        stats.packets_skipped_no_ip += 1
        return None

    source_port: int | None = None
    destination_port: int | None = None
    tcp_flags: int | None = None
    if packet.haslayer(TCP):
        source_port = int(packet[TCP].sport)
        destination_port = int(packet[TCP].dport)
        tcp_flags = int(packet[TCP].flags)
    elif packet.haslayer(UDP):
        source_port = int(packet[UDP].sport)
        destination_port = int(packet[UDP].dport)
    # ICMP and any other protocol: no port concept — left None, never 0.

    length = int(len(packet))

    return PacketRecord(
        timestamp=float(packet.time),
        source_ip=str(ip_layer.src),
        destination_ip=str(ip_layer.dst),
        protocol_number=protocol_number,
        length=length,
        source_port=source_port,
        destination_port=destination_port,
        tcp_flags=tcp_flags,
    )


def iter_packets(path: Path, stats: PacketParsingStats) -> Iterator[PacketRecord]:
    """Streams `PacketRecord`s from the capture at `path` one at a time
    (via `scapy.utils.PcapReader`, never `rdpcap`) — the entire file is
    never materialized in memory at once. Mutates `stats` in place as
    it goes, so a caller can inspect partial progress even if the
    capture turns out to be truncated partway through.

    Raises `PcapParsingError` only if the reader itself cannot be
    opened at all (the file is not the format it claimed to be, despite
    passing `validate_capture_file`'s magic-number check, or is
    truncated before even one complete record) — every other packet-
    level problem is counted in `stats` and skipped, never raised.
    """
    from scapy.utils import PcapReader

    try:
        reader = PcapReader(str(path))
    except Exception as error:
        logger.exception("Could not open capture file for parsing")
        raise PcapParsingError("The capture file could not be opened for parsing.") from error

    try:
        while True:
            try:
                packet = reader.read_packet()
            except EOFError:
                break
            except StopIteration:
                break
            except Exception:
                logger.warning("Skipping one unparseable packet in the capture")
                stats.packets_read += 1
                stats.packets_malformed += 1
                continue

            stats.packets_read += 1
            record = packet_to_record(packet, stats)
            if record is not None:
                stats.packets_parsed += 1
                yield record
    finally:
        reader.close()


__all__ = [
    "PcapValidationError",
    "PcapParsingError",
    "PacketParsingStats",
    "SUPPORTED_EXTENSIONS",
    "detect_capture_format",
    "has_pcap_extension",
    "validate_capture_file",
    "iter_packets",
    "packet_to_record",
]
