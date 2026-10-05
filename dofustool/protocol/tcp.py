"""Réassemblage TCP par direction, d'après flow.rs de SniffSniffSquared (voir THIRD_PARTY_NOTICES.md).

Tient compte des numéros de séquence : ajoute les segments contigus, met de côté ceux
arrivés en avance, écarte les retransmissions. Ce n'est pas une pile TCP complète.
"""
from dataclasses import dataclass, field

from ..capture import SYN, Segment

C2S = "c2s"
S2C = "s2c"


def _seq_lt(a: int, b: int) -> bool:
    """a < b sur des numéros de séquence 32 bits qui bouclent."""
    return ((a - b) & 0xFFFFFFFF) >= 0x80000000


class StreamReassembler:
    def __init__(self) -> None:
        self.next_seq: int | None = None
        self.pending: dict[int, bytes] = {}
        self.saw_syn = False
        self.delivered = 0

    def push(self, seq: int, flags: int, payload: bytes) -> bytes:
        """Renvoie les octets devenus contigus grâce à ce segment (éventuellement aucun)."""
        if flags & SYN:
            self.saw_syn = True
            self.next_seq = (seq + 1) & 0xFFFFFFFF
            return b""
        if not payload:
            return b""
        if self.next_seq is None:
            self.next_seq = seq
        expected = self.next_seq
        if _seq_lt(seq, expected):
            skip = (expected - seq) & 0xFFFFFFFF
            if skip >= len(payload):
                return b""
            return self._feed(expected, payload[skip:])
        if seq == expected:
            return self._feed(seq, payload)
        self.pending.setdefault(seq, payload)
        return b""

    def _feed(self, seq: int, payload: bytes) -> bytes:
        out = [payload]
        nxt = (seq + len(payload)) & 0xFFFFFFFF
        while self.pending:
            chunk = self.pending.pop(nxt, None)
            if chunk is None:
                # Un segment en attente peut chevaucher la position courante.
                overlap = next(
                    (
                        s
                        for s, p in self.pending.items()
                        if _seq_lt(s, nxt) and _seq_lt(nxt, (s + len(p)) & 0xFFFFFFFF)
                    ),
                    None,
                )
                if overlap is None:
                    break
                chunk = self.pending.pop(overlap)[(nxt - overlap) & 0xFFFFFFFF :]
            out.append(chunk)
            nxt = (nxt + len(chunk)) & 0xFFFFFFFF
        # Les segments entièrement dépassés ne serviront plus.
        for s in [s for s in self.pending if not _seq_lt(nxt, (s + len(self.pending[s])) & 0xFFFFFFFF)]:
            del self.pending[s]
        self.next_seq = nxt
        data = b"".join(out)
        self.delivered += len(data)
        return data

    @property
    def stalled_bytes(self) -> int:
        """Octets reçus après un trou jamais comblé (perte de paquets dans la capture)."""
        return sum(len(p) for p in self.pending.values())


@dataclass(frozen=True, slots=True)
class ConnKey:
    client: str
    client_port: int
    server: str
    server_port: int


@dataclass(slots=True)
class Connection:
    key: ConnKey
    index: int
    first_ts: float
    last_ts: float = 0.0
    streams: dict[str, StreamReassembler] = field(
        default_factory=lambda: {C2S: StreamReassembler(), S2C: StreamReassembler()}
    )


class ConnectionTracker:
    """Regroupe les segments par connexion et rend les octets réassemblés de chaque direction."""

    def __init__(self, server_port: int) -> None:
        self.server_port = server_port
        self.connections: list[Connection] = []
        self._current: dict[ConnKey, Connection] = {}

    def push(self, seg: Segment) -> tuple[Connection, str, bytes] | None:
        if seg.dport == self.server_port:
            key, direction = ConnKey(seg.src, seg.sport, seg.dst, seg.dport), C2S
        elif seg.sport == self.server_port:
            key, direction = ConnKey(seg.dst, seg.dport, seg.src, seg.sport), S2C
        else:
            return None
        conn = self._current.get(key)
        # Un SYN client sur un 4-uplet déjà actif ouvre une nouvelle connexion (port réutilisé).
        reused = conn is not None and direction == C2S and seg.flags & SYN and conn.streams[C2S].delivered
        if conn is None or reused:
            conn = Connection(key=key, index=len(self.connections), first_ts=seg.ts)
            self.connections.append(conn)
            self._current[key] = conn
        conn.last_ts = seg.ts
        return conn, direction, conn.streams[direction].push(seg.seq, seg.flags, seg.payload)
