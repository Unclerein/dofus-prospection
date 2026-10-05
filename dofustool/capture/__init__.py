"""Sources de segments TCP. Strictement passif : lecture seule, aucun envoi."""
from dataclasses import dataclass

GAME_PORT = 5555

FIN = 0x01
SYN = 0x02
RST = 0x04


@dataclass(frozen=True, slots=True)
class Segment:
    ts: float
    src: str
    sport: int
    dst: str
    dport: int
    seq: int
    flags: int
    payload: bytes
