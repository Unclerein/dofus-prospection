"""Tests sur des octets synthétiques : aucune donnée issue d'une capture réelle."""
import struct

import pytest

from dofustool.archive import Archive
from dofustool.capture import SYN, Segment
from dofustool.capture.packet import LINKTYPE_ETHERNET, parse_tcp
from dofustool.protocol.frame import unwrap
from dofustool.protocol.framing import Deframer, FramingError
from dofustool.protocol.session import GAME, IGNORED, Session
from dofustool.protocol.tcp import C2S, S2C, StreamReassembler
from dofustool.protocol.wire import LEN, VARINT, WireError, iter_fields, read_varint, to_signed


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def ld(number: int, payload: bytes) -> bytes:
    return varint(number << 3 | LEN) + varint(len(payload)) + payload


def vi(number: int, value: int) -> bytes:
    return varint(number << 3 | VARINT) + varint(value)


def any_(key: str, body: bytes = b"") -> bytes:
    return ld(1, b"type.ankama.com/" + key.encode()) + (ld(2, body) if body else b"")


def framed(frame: bytes) -> bytes:
    return varint(len(frame)) + frame


REQUEST = ld(1, ld(1, any_("abc", vi(1, 42))) + vi(2, 7))
EVENT = ld(2, ld(3, any_("xyz", vi(3, 300))))
RESPONSE = ld(3, ld(1, any_("rsp")) + vi(2, 7))
AUTH_LIKE = ld(1, ld(1, b"opaque") + vi(2, 1))


# --- wire -------------------------------------------------------------------

def test_read_varint():
    assert read_varint(b"\x96\x01", 0) == (150, 2)
    with pytest.raises(WireError):
        read_varint(b"\x96", 0)
    with pytest.raises(WireError):
        read_varint(b"\xff" * 11, 0)


def test_to_signed():
    assert to_signed((1 << 64) - 1) == -1
    assert to_signed(5) == 5


def test_iter_fields():
    buf = vi(1, 150) + ld(2, b"ab") + b"\x1d" + struct.pack("<I", 9)
    assert list(iter_fields(buf)) == [(1, 0, 150), (2, 2, b"ab"), (3, 5, 9)]
    with pytest.raises(WireError):
        list(iter_fields(b"\x12\x05ab"))


# --- framing ----------------------------------------------------------------

def test_deframer_handles_split_and_coalesced_frames():
    stream = framed(REQUEST) + framed(b"\x08" * 300) + framed(EVENT)
    deframer = Deframer()
    frames = []
    for i in range(0, len(stream), 7):
        frames += deframer.push(stream[i : i + 7])
    assert frames == [REQUEST, b"\x08" * 300, EVENT]
    assert deframer.leftover == 0
    assert deframer.consumed == len(stream)


def test_deframer_keeps_incomplete_tail():
    deframer = Deframer()
    assert deframer.push(framed(EVENT)[:-1]) == []
    assert deframer.leftover == len(framed(EVENT)) - 1


def test_deframer_rejects_garbage_length():
    with pytest.raises(FramingError):
        Deframer().push(b"\xff" * 12)


# --- réassemblage TCP -------------------------------------------------------

def test_reassembly_in_order_after_syn():
    r = StreamReassembler()
    assert r.push(1000, SYN, b"") == b""
    assert r.push(1001, 0, b"abc") == b"abc"
    assert r.push(1004, 0, b"def") == b"def"
    assert r.saw_syn and r.stalled_bytes == 0


def test_reassembly_out_of_order_and_retransmit():
    r = StreamReassembler()
    r.push(0, SYN, b"")
    assert r.push(4, 0, b"def") == b""
    assert r.stalled_bytes == 3
    assert r.push(1, 0, b"abc") == b"abcdef"
    assert r.push(1, 0, b"abc") == b""  # retransmission pure
    assert r.push(5, 0, b"efgh") == b"gh"  # chevauchement partiel
    assert r.stalled_bytes == 0


def test_reassembly_sequence_wraparound():
    r = StreamReassembler()
    r.push(0xFFFFFFFD, SYN, b"")
    assert r.push(0xFFFFFFFE, 0, b"abcd") == b"abcd"
    assert r.push(2, 0, b"ef") == b"ef"


def test_reassembly_pending_overlapping_segment():
    r = StreamReassembler()
    r.push(0, SYN, b"")
    assert r.push(3, 0, b"cdef") == b""
    assert r.push(1, 0, b"abc") == b"abcdef"


# --- enveloppe --------------------------------------------------------------

def test_unwrap_request_event_response():
    req = unwrap(REQUEST)
    assert (req.key, req.frame_field, req.correlation) == ("abc", 1, 7)
    assert req.body == vi(1, 42)
    evt = unwrap(EVENT)
    assert (evt.key, evt.frame_field, evt.correlation) == ("xyz", 2, None)
    rsp = unwrap(RESPONSE)
    assert (rsp.key, rsp.body, rsp.correlation) == ("rsp", b"", 7)


def test_unwrap_negative_correlation():
    frame = ld(1, ld(1, any_("abc")) + vi(2, (1 << 64) - 1))
    assert unwrap(frame).correlation == -1


def test_unwrap_rejects_non_game_frames():
    assert unwrap(AUTH_LIKE) is None
    assert unwrap(b"\xff\xff") is None
    assert unwrap(b"") is None


# --- session ----------------------------------------------------------------

def segments(client_port: int, server: str, c2s: bytes, s2c: bytes, t: float):
    client = "10.0.0.2"
    yield Segment(t, client, client_port, server, 5555, 100, SYN, b"")
    yield Segment(t + 0.01, server, 5555, client, client_port, 500, SYN, b"")
    yield Segment(t + 0.02, client, client_port, server, 5555, 101, 0, c2s)
    yield Segment(t + 0.03, server, 5555, client, client_port, 501, 0, s2c)


def test_session_ignores_auth_flow_and_decodes_game_flow():
    session = Session()
    messages = []
    for seg in segments(40000, "192.0.2.1", framed(AUTH_LIKE), framed(AUTH_LIKE) * 2, 0.0):
        messages += session.feed(seg)
    for seg in segments(40001, "192.0.2.2", framed(REQUEST), framed(EVENT) + framed(RESPONSE), 1.0):
        messages += session.feed(seg)

    assert [s.kind for s in session.states] == [IGNORED, GAME]
    assert [(m.conn, m.direction, m.key) for m in messages] == [(1, C2S, "abc"), (1, S2C, "xyz"), (1, S2C, "rsp")]
    game = session.states[1]
    assert game.leftover == 0 and game.undecoded_frames == 0 and game.complete_start
    assert session.states[0].messages == 0 and session.states[0].leftover == 0


def test_session_counts_undecodable_frame_in_game_flow():
    session = Session()
    for seg in segments(40001, "192.0.2.2", framed(REQUEST) + framed(AUTH_LIKE), b"", 0.0):
        session.feed(seg)
    assert session.states[0].kind == GAME
    assert session.states[0].undecoded_frames == 1


# --- archive ----------------------------------------------------------------

def test_archive_is_idempotent_per_connection():
    session = Session()
    messages = [m for seg in segments(40001, "192.0.2.2", framed(REQUEST), framed(EVENT), 5.0) for m in session.feed(seg)]
    archive = Archive(":memory:")
    conn_id = archive.open_connection(5.0, 40001, "192.0.2.2", "test")
    for m in messages:
        archive.add(conn_id, m)
    assert archive.count() == 2
    assert archive.open_connection(5.0, 40001, "192.0.2.2", "test") is None
    row = archive._db.execute("SELECT direction, key, size, correlation, body FROM messages ORDER BY id").fetchone()
    assert row == (C2S, "abc", 2, 7, vi(1, 42))


# --- extraction des segments ------------------------------------------------

def ethernet_ipv4_tcp(sport: int, dport: int, seq: int, payload: bytes, padding: bytes = b"") -> bytes:
    tcp = struct.pack("!HHIIHHHH", sport, dport, seq, 0, 5 << 12 | 0x18, 1024, 0, 0) + payload
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 0, 0, 64, 6, 0, bytes([10, 0, 0, 2]), bytes([192, 0, 2, 1]))
    return b"\x00" * 12 + b"\x08\x00" + ip + tcp + padding


def test_parse_tcp_strips_ethernet_padding_and_filters_ports():
    raw = ethernet_ipv4_tcp(40000, 5555, 1234, b"hi", padding=b"\x00" * 4)
    seg = parse_tcp(raw, LINKTYPE_ETHERNET, 1.5, frozenset({5555}))
    assert (seg.src, seg.sport, seg.dst, seg.dport, seg.seq, seg.payload) == ("10.0.0.2", 40000, "192.0.2.1", 5555, 1234, b"hi")
    assert parse_tcp(raw, LINKTYPE_ETHERNET, 1.5, frozenset({443})) is None
    assert parse_tcp(raw[:20], LINKTYPE_ETHERNET, 1.5, frozenset({5555})) is None
