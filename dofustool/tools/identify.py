"""Aide à l'identification des messages pendant une capture live (écoute passive).

Tape une étiquette puis Entrée juste AVANT de faire l'action en jeu (« ouvre HDV »,
« cours du marché item X »…). L'outil liste les messages reçus dans les secondes qui
suivent, avec leur structure et leurs valeurs numériques. Les chaînes et octets bruts
ne sont jamais affichés, ni le contenu du handshake, ni rien du flux d'authentification.
Tout le flux de jeu est archivé, avec les étiquettes. Ligne vide ou Ctrl+C pour quitter.

Usage : python -m dofustool.tools.identify [--window 4] [--iface NOM] [--list-ifaces]
"""
import argparse
import sys
import threading
import time
from collections import deque

from ..archive import Archive
from ..protocol.schema import DOFUS_PROTO, Schema, render
from ..protocol.session import Message, Session
from ..protocol.tcp import C2S

# Début de connexion dont le contenu n'est jamais affiché (peut contenir le jeton de session).
HANDSHAKE_MESSAGES = 30
HANDSHAKE_SECONDS = 2.0


def describe(msg: Message, rank: int, age: float, schema: Schema | None) -> str:
    sens = "C>S" if msg.direction == C2S else "S>C"
    head = f"{sens} {msg.key:<5} {msg.size:>6} o"
    if rank <= HANDSHAKE_MESSAGES or age < HANDSHAKE_SECONDS:
        return f"{head}  (handshake : contenu masqué)"
    if schema is None:
        return head
    described, mismatches = schema.describe(msg.key, msg.body)
    flag = "  [désaccord schéma]" if mismatches else ""
    text = render(described)
    return f"{head}  {text[:400]}{'…' if len(text) > 400 else ''}{flag}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--window", type=float, default=4.0, help="secondes observées après chaque étiquette")
    parser.add_argument("--iface", help="interface réseau (défaut : celle de scapy)")
    parser.add_argument("--list-ifaces", action="store_true")
    args = parser.parse_args()

    from scapy.all import conf  # import tardif : lent au chargement

    from ..capture.live_source import LiveSource

    if args.list_ifaces:
        for iface in conf.ifaces.values():
            print(f"{iface.name:<40} {iface.ip or '-':<16} {iface.description}")
        return 0

    schema = Schema.load() if DOFUS_PROTO.exists() else None
    if schema is None:
        print("data/static/dofus.proto absent : seules les clés et tailles seront affichées.")

    source = LiveSource(iface=args.iface)
    session = Session()
    archive = Archive()
    recent: deque[tuple[Message, int, float]] = deque(maxlen=20000)
    lock = threading.Lock()

    def pump() -> None:
        ids: dict[int, int | None] = {}
        ranks: dict[int, int] = {}
        local = Archive()  # une connexion SQLite par thread
        for segment in source:
            for msg in session.feed(segment):
                conn = session.states[msg.conn].connection
                if msg.conn not in ids:
                    ids[msg.conn] = local.open_connection(conn.first_ts, conn.key.client_port, conn.key.server, "live")
                    print(f"\n[flux de jeu détecté, connexion {msg.conn}]", flush=True)
                if ids[msg.conn] is not None:
                    local.add(ids[msg.conn], msg)
                ranks[msg.conn] = ranks.get(msg.conn, 0) + 1
                with lock:
                    recent.append((msg, ranks[msg.conn], msg.ts - conn.first_ts))
            local.commit()
        local.close()

    source.start()
    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    print(f"Écoute passive sur « {source.iface} », tcp port 5555. Lance le jeu puis saisis tes étiquettes.")
    try:
        while True:
            label = input("étiquette> ").strip()
            if not label:
                break
            start = time.time()
            archive.add_label(start, label)
            archive.commit()
            print(f"  … fais l'action maintenant ({args.window:.0f} s)")
            time.sleep(args.window)
            with lock:
                seen = [entry for entry in recent if start - 0.3 <= entry[0].ts <= start + args.window]
            print(f"  {len(seen)} message(s) :")
            for msg, rank, age in seen:
                print(f"   +{msg.ts - start:5.2f}s  {describe(msg, rank, age, schema)}")
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        source.stop()
        thread.join(timeout=5)
        archive.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
