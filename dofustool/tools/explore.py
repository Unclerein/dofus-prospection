"""Découpe une capture hors ligne, archive le flux de jeu et affiche les statistiques par clé.

N'affiche que de la structure : clés, compteurs, tailles, délais. Jamais de chaîne ni
d'octet brut issus des messages, et rien du flux d'authentification.

Usage : python -m dofustool.tools.explore [capture.pcapng] [--no-archive]
"""
import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from ..archive import ARCHIVE_PATH, Archive
from ..capture.pcap_source import PcapSource
from ..protocol.session import GAME, Session
from ..protocol.tcp import C2S

DEFAULT_CAPTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "capture.pcapng"


@dataclass(slots=True)
class KeyStats:
    key: str
    direction: str
    count: int = 0
    total: int = 0
    largest: int = 0
    first_offset: float = 0.0
    first_rank: int = 0

    @property
    def mean(self) -> float:
        return self.total / self.count


def run(capture: Path, archive: Archive | None) -> tuple[Session, dict[tuple[str, str], KeyStats], int]:
    session = Session()
    stats: dict[tuple[str, str], KeyStats] = {}
    archive_ids: dict[int, int | None] = {}
    archived = rank = 0
    for segment in PcapSource(capture):
        for msg in session.feed(segment):
            rank += 1
            conn = session.states[msg.conn].connection
            entry = stats.get((msg.key, msg.direction))
            if entry is None:
                entry = stats[(msg.key, msg.direction)] = KeyStats(
                    msg.key, msg.direction, first_offset=msg.ts - conn.first_ts, first_rank=rank
                )
            entry.count += 1
            entry.total += msg.size
            entry.largest = max(entry.largest, msg.size)
            if archive is not None:
                if msg.conn not in archive_ids:
                    archive_ids[msg.conn] = archive.open_connection(
                        conn.first_ts, conn.key.client_port, conn.key.server, capture.name
                    )
                if archive_ids[msg.conn] is not None:
                    archive.add(archive_ids[msg.conn], msg)
                    archived += 1
    return session, stats, archived


def report(session: Session, stats: dict[tuple[str, str], KeyStats]) -> bool:
    """Affiche le rapport. Renvoie True si tout le flux de jeu est découpé sans reste."""
    ok = True
    print(f"Connexions sur le port de jeu : {len(session.states)}")
    for state in session.states:
        conn = state.connection
        duration = conn.last_ts - conn.first_ts
        if state.kind != GAME:
            print(f"  [{conn.index}] {state.kind:<7} durée {duration:6.1f} s  (contenu non lu)")
            continue
        delivered = sum(s.delivered for s in conn.streams.values())
        consumed = sum(d.consumed for d in state.deframers.values())
        frames = sum(d.frames for d in state.deframers.values())
        clean = (
            state.leftover == 0
            and state.stalled_bytes == 0
            and state.undecoded_frames == 0
            and state.framing_error is None
            and consumed == delivered
        )
        ok = ok and clean
        print(
            f"  [{conn.index}] {state.kind:<7} durée {duration:6.1f} s  octets {delivered}  frames {frames}  "
            f"messages {state.messages}"
        )
        print(
            f"        découpé {consumed}/{delivered} octets ({consumed / delivered:.2%})  reste {state.leftover}  "
            f"trous TCP {state.stalled_bytes} o  frames non décodées {state.undecoded_frames}  "
            f"début complet {'oui' if state.complete_start else 'NON'}"
            + (f"  ERREUR {state.framing_error}" if state.framing_error else "")
        )
    if not session.game_states:
        print("Aucun flux de jeu trouvé.")
        return False

    print(f"\nClés distinctes : {len({k for k, _ in stats})}   (C>S = client vers serveur, S>C = serveur vers client)")
    print(f"{'clé':<6} {'sens':<4} {'nombre':>6} {'taille moy.':>11} {'taille max':>10} {'1er à (s)':>10} {'rang':>5}")
    for s in sorted(stats.values(), key=lambda s: s.first_rank):
        sens = "C>S" if s.direction == C2S else "S>C"
        print(f"{s.key:<6} {sens:<4} {s.count:>6} {s.mean:>11.1f} {s.largest:>10} {s.first_offset:>10.3f} {s.first_rank:>5}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("capture", nargs="?", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--no-archive", action="store_true", help="ne pas écrire dans data/archive.sqlite")
    args = parser.parse_args()

    archive = None if args.no_archive else Archive()
    session, stats, archived = run(args.capture, archive)
    ok = report(session, stats)
    if archive is not None:
        total = archive.count()
        archive.close()
        print(f"\nArchive {ARCHIVE_PATH.name} : {archived} messages ajoutés, {total} au total.")
    print("\nRésultat : " + ("flux de jeu découpé à 100 %, sans reste." if ok else "DÉCOUPAGE INCOMPLET."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
