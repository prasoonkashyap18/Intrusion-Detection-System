"""Packet-to-flow conversion: groups individual packets into
bidirectional network flows, independent of where the packets came
from (a `.pcap`/`.pcapng` file — Step 41 — or a live capture — Step
42) and independent of any packet-parsing library. This module knows
nothing about `scapy`, file formats, or sockets: it only knows
`PacketRecord`, a plain dataclass either source can produce.

    PacketRecord (from app.services.pcap_parsing or app.services.live_capture)
        |
    FlowConstructor.add_packet()  <- stateful, one call per packet
        |
    FinalizedFlow  (yielded when a flow is finalized)

Flow key (direction-independent): `(protocol_number, min(ip_a, ip_b),
max(ip_a, ip_b), min(port_a, port_b), max(port_a, port_b))` — a packet's
own `(source, destination)` order never matters for which flow it
belongs to, only for which *direction* within that flow it represents.
A protocol with no concept of ports (e.g. ICMP) uses `port=-1` for both
sides of the key by convention — never a fabricated port number.

Direction convention (documented, not merely implied): the first
packet observed for a given flow key defines "forward" — every
subsequent packet whose `(source_ip, source_port)` matches that first
packet's source is also "forward"; every other packet (necessarily the
reverse direction, since the flow key already constrains both
endpoints) is "backward". This is a convention, not a claim about which
side initiated the conversation — a capture starting mid-flow has no
way to know that, and this module does not pretend otherwise.

Flow finalization policy (the explicit policy this step implements,
and the only one): a flow is finalized when a new packet for its key
arrives more than `idle_timeout_seconds` after the flow's own last
packet (`FlowConstructor.add_packet` finalizes the *old* flow and starts
a fresh one for that key before processing the new packet), or when the
caller explicitly calls `flush()` at the end of a bounded source (end of
a PCAP file) or on session shutdown (live capture stopping). There is no
other finalization trigger in this step — in particular, this module
does **not** finalize a flow early on a TCP FIN/RST packet; every TCP
flow waits out the same idle timeout as any other protocol. This is a
deliberate simplification, documented here precisely so it is never
mistaken for an oversight.

Packets are assumed to already be in non-decreasing timestamp order
within the stream handed to `add_packet` — the order a `.pcap`/`.pcapng`
file's own write convention and a live capture's own arrival order both
already guarantee. This module does not re-sort; doing so would require
buffering an unbounded number of packets before any flow could ever be
finalized, defeating the "do not load an arbitrarily large capture
entirely into memory" requirement.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

DEFAULT_IDLE_TIMEOUT_SECONDS = 120.0
_NO_PORT = -1
"""Used for both sides of the flow key for a protocol with no port
concept (e.g. ICMP) — never a real port value, and never confused with
port `0`, which is a real (if unusual) port number some protocols use."""


@dataclass(frozen=True)
class PacketRecord:
    """One packet's fields, as genuinely extracted by a parser — never
    guessed. `source_port`/`destination_port` are `None` for a protocol
    with no port concept (not `0`); `tcp_flags` is `None` for anything
    that is not TCP."""

    timestamp: float
    """Seconds since the Unix epoch, as the packet's own capture
    timestamp reports it."""

    source_ip: str
    destination_ip: str
    protocol_number: int
    """The real IANA protocol number from the IP header (e.g. 6 = TCP,
    17 = UDP, 1 = ICMP) — never inferred from port numbers."""

    length: int
    """The packet's real total length in bytes, as captured."""

    source_port: int | None = None
    destination_port: int | None = None
    tcp_flags: int | None = None
    """The TCP header's flags as a real numeric bitmask (the same
    encoding `app.services.feature_extraction._parse_tcp_flags` already
    expects for a `"tcp_flags"` value in text form), or `None` for a
    non-TCP packet."""


FlowKey = tuple[int, str, str, int, int]


def compute_flow_key(packet: PacketRecord) -> FlowKey:
    """Direction-independent flow key — see the module docstring."""
    source_port = packet.source_port if packet.source_port is not None else _NO_PORT
    destination_port = packet.destination_port if packet.destination_port is not None else _NO_PORT
    ip_low, ip_high = sorted((packet.source_ip, packet.destination_ip))
    port_low, port_high = sorted((source_port, destination_port))
    return (packet.protocol_number, ip_low, ip_high, port_low, port_high)


@dataclass(frozen=True)
class FinalizedFlow:
    """One completed flow's derived statistics — real counts and
    durations only, never a fabricated or default-even value standing
    in for something genuinely unmeasured (see `packet_rate`/`byte_rate`
    below)."""

    flow_key: FlowKey
    source_ip: str
    destination_ip: str
    source_port: int | None
    destination_port: int | None
    protocol_number: int
    is_tcp: bool

    first_timestamp: float
    last_timestamp: float
    packet_count: int
    byte_count: int
    forward_packet_count: int
    forward_byte_count: int
    backward_packet_count: int
    backward_byte_count: int
    tcp_flags_union: int | None
    """Bitwise OR of every TCP packet's flags seen in this flow, or
    `None` when `is_tcp` is `False` — there is no flags concept for a
    non-TCP flow, so this is genuinely absent, not zero."""

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.last_timestamp - self.first_timestamp)

    @property
    def packet_rate(self) -> float | None:
        """`None` (never `0.0`) when `duration_seconds` is `0` — a rate
        is mathematically undefined for an instantaneous flow, not
        genuinely zero."""
        duration = self.duration_seconds
        return self.packet_count / duration if duration > 0 else None

    @property
    def byte_rate(self) -> float | None:
        duration = self.duration_seconds
        return self.byte_count / duration if duration > 0 else None


@dataclass
class _OpenFlow:
    forward_source_ip: str
    forward_source_port: int | None
    protocol_number: int
    is_tcp: bool
    first_timestamp: float
    last_timestamp: float = 0.0
    packet_count: int = 0
    byte_count: int = 0
    forward_packet_count: int = 0
    forward_byte_count: int = 0
    backward_packet_count: int = 0
    backward_byte_count: int = 0
    tcp_flags_union: int = 0
    # The first packet's own (source, destination) pair, so a finalized
    # flow reports a real observed pair rather than the sorted key.
    first_source_ip: str = ""
    first_destination_ip: str = ""
    first_source_port: int | None = None
    first_destination_port: int | None = None

    def add_packet(self, packet: PacketRecord) -> None:
        self.last_timestamp = packet.timestamp
        self.packet_count += 1
        self.byte_count += packet.length

        is_forward = (
            packet.source_ip == self.forward_source_ip and packet.source_port == self.forward_source_port
        )
        if is_forward:
            self.forward_packet_count += 1
            self.forward_byte_count += packet.length
        else:
            self.backward_packet_count += 1
            self.backward_byte_count += packet.length

        if self.is_tcp and packet.tcp_flags is not None:
            self.tcp_flags_union |= packet.tcp_flags

    def finalize(self) -> FinalizedFlow:
        return FinalizedFlow(
            flow_key=(
                self.protocol_number,
                *sorted((self.first_source_ip, self.first_destination_ip)),
                *sorted(
                    (
                        self.first_source_port if self.first_source_port is not None else _NO_PORT,
                        self.first_destination_port if self.first_destination_port is not None else _NO_PORT,
                    )
                ),
            ),
            source_ip=self.first_source_ip,
            destination_ip=self.first_destination_ip,
            source_port=self.first_source_port,
            destination_port=self.first_destination_port,
            protocol_number=self.protocol_number,
            is_tcp=self.is_tcp,
            first_timestamp=self.first_timestamp,
            last_timestamp=self.last_timestamp,
            packet_count=self.packet_count,
            byte_count=self.byte_count,
            forward_packet_count=self.forward_packet_count,
            forward_byte_count=self.forward_byte_count,
            backward_packet_count=self.backward_packet_count,
            backward_byte_count=self.backward_byte_count,
            tcp_flags_union=self.tcp_flags_union if self.is_tcp else None,
        )


class FlowConstructor:
    """Stateful packet-to-flow accumulator. One instance per capture
    (one PCAP file's processing run, or one live `CaptureSession`) —
    never shared between two unrelated captures, since flow keys are
    only unique within one capture's own traffic.
    """

    def __init__(self, idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS) -> None:
        if idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be a positive number.")
        self.idle_timeout_seconds = idle_timeout_seconds
        self._open_flows: dict[FlowKey, _OpenFlow] = {}

    def add_packet(self, packet: PacketRecord) -> FinalizedFlow | None:
        """Feeds one packet in. Returns a `FinalizedFlow` if adding this
        packet caused an *existing* flow to idle out (that flow is
        finalized and a new one starts for this packet) — `None`
        otherwise (the packet joined an existing flow, or started a
        brand new one, with nothing yet finalized).
        """
        key = compute_flow_key(packet)
        existing = self._open_flows.get(key)

        finalized: FinalizedFlow | None = None
        if existing is not None:
            if packet.timestamp - existing.last_timestamp > self.idle_timeout_seconds:
                finalized = existing.finalize()
                existing = None

        if existing is None:
            existing = _OpenFlow(
                forward_source_ip=packet.source_ip,
                forward_source_port=packet.source_port,
                protocol_number=packet.protocol_number,
                is_tcp=packet.tcp_flags is not None,
                first_timestamp=packet.timestamp,
                first_source_ip=packet.source_ip,
                first_destination_ip=packet.destination_ip,
                first_source_port=packet.source_port,
                first_destination_port=packet.destination_port,
            )
            self._open_flows[key] = existing

        existing.add_packet(packet)
        return finalized

    def idle_flows(self, now: float) -> Iterator[FinalizedFlow]:
        """Finalizes (and removes) every open flow whose last packet is
        more than `idle_timeout_seconds` older than `now`, without
        requiring a new packet to trigger it — the mechanism live
        capture (Step 42) uses to finalize a flow that simply stops
        receiving packets, since no future packet will ever arrive to
        discover that on its own.
        """
        expired_keys = [
            key for key, flow in self._open_flows.items() if now - flow.last_timestamp > self.idle_timeout_seconds
        ]
        for key in expired_keys:
            yield self._open_flows.pop(key).finalize()

    def flush(self) -> Iterator[FinalizedFlow]:
        """Finalizes every remaining open flow — called at the end of a
        bounded source (end of a PCAP file) or on session shutdown
        (live capture stopping), so no flow is ever silently discarded.
        """
        keys = list(self._open_flows.keys())
        for key in keys:
            yield self._open_flows.pop(key).finalize()

    @property
    def open_flow_count(self) -> int:
        return len(self._open_flows)


def construct_flows(
    packets: Iterator[PacketRecord], idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS
) -> Iterator[FinalizedFlow]:
    """Convenience wrapper for a bounded packet stream (e.g. a parsed
    PCAP file): feeds every packet through one `FlowConstructor` and
    flushes all remaining flows at the end — the entire packet-to-flow
    conversion for a finite capture in one call.
    """
    constructor = FlowConstructor(idle_timeout_seconds)
    for packet in packets:
        finalized = constructor.add_packet(packet)
        if finalized is not None:
            yield finalized
    yield from constructor.flush()


__all__ = [
    "DEFAULT_IDLE_TIMEOUT_SECONDS",
    "PacketRecord",
    "FlowKey",
    "compute_flow_key",
    "FinalizedFlow",
    "FlowConstructor",
    "construct_flows",
]
