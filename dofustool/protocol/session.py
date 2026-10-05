"""Chaîne complète : segments TCP -> connexions -> frames -> messages de jeu.

Le flux d'authentification passe par le même port que le jeu mais ses frames ne
contiennent pas d'Any « type.ankama.com/… ». La première frame d'une connexion décide
donc de sa nature ; une connexion qui n'est pas du jeu est ignorée en entier : rien
n'en est décodé, renvoyé ni archivé.
"""
from dataclasses import dataclass, field

from ..capture import GAME_PORT, Segment
from .frame import unwrap
from .framing import Deframer, FramingError
from .tcp import C2S, S2C, Connection, ConnectionTracker

UNKNOWN = "inconnu"
GAME = "jeu"
IGNORED = "ignoré"


@dataclass(frozen=True, slots=True)
class Message:
    conn: int
    ts: float
    direction: str
    key: str
    body: bytes
    frame_field: int
    correlation: int | None

    @property
    def size(self) -> int:
        return len(self.body)


@dataclass(slots=True)
class ConnState:
    connection: Connection
    kind: str = UNKNOWN
    deframers: dict[str, Deframer] = field(default_factory=lambda: {C2S: Deframer(), S2C: Deframer()})
    messages: int = 0
    undecoded_frames: int = 0
    framing_error: str | None = None

    @property
    def leftover(self) -> int:
        return sum(d.leftover for d in self.deframers.values())

    @property
    def stalled_bytes(self) -> int:
        return sum(s.stalled_bytes for s in self.connection.streams.values())

    @property
    def complete_start(self) -> bool:
        """Faux si la capture a commencé en cours de connexion (début de flux manquant)."""
        return all(s.saw_syn for s in self.connection.streams.values())


class Session:
    def __init__(self, server_port: int = GAME_PORT) -> None:
        self._tracker = ConnectionTracker(server_port)
        self.states: list[ConnState] = []

    def feed(self, seg: Segment) -> list[Message]:
        pushed = self._tracker.push(seg)
        if pushed is None:
            return []
        conn, direction, data = pushed
        if conn.index == len(self.states):
            self.states.append(ConnState(conn))
        state = self.states[conn.index]
        if not data or state.kind == IGNORED or state.framing_error:
            return []
        try:
            frames = state.deframers[direction].push(data)
        except FramingError as exc:
            state.framing_error = str(exc)
            return []
        out = []
        for frame in frames:
            envelope = unwrap(frame)
            if state.kind == UNKNOWN:
                state.kind = GAME if envelope is not None else IGNORED
                if state.kind == IGNORED:
                    # Ne rien garder en mémoire du flux d'authentification.
                    state.deframers = {C2S: Deframer(), S2C: Deframer()}
                    return []
            if envelope is None:
                state.undecoded_frames += 1
                continue
            out.append(
                Message(
                    conn=conn.index,
                    ts=seg.ts,
                    direction=direction,
                    key=envelope.key,
                    body=envelope.body,
                    frame_field=envelope.frame_field,
                    correlation=envelope.correlation,
                )
            )
        state.messages += len(out)
        return out

    @property
    def game_states(self) -> list[ConnState]:
        return [s for s in self.states if s.kind == GAME]
