"""Écrit la structure des messages du jeu sur une tranche horaire, pour étudier un moment précis (un combat).

Un message par ligne : délai depuis le début de la tranche, sens, clé, taille, puis ses champs. Seuls
la structure et les nombres sortent : numéros de champ, entiers, flottants, tailles. Jamais une
chaîne de caractères ni un octet brut : un champ de texte n'apparaît que par sa longueur (« LEN12 »).
Les premiers messages de chaque connexion (la poignée de main, qui peut porter le jeton) sont écartés.

Le fichier écrit va dans data/, hors du dépôt. Il ne contient pas le chat, mais il décrit ce que le
joueur a fait : à ne donner qu'à qui doit l'étudier.

Usage : python -m dofustool.tools.dump_window --from "2026-10-13 14:14" --to "2026-10-13 14:27" [--out data/extrait.txt]
"""
import argparse
import sqlite3
import struct
import sys
from datetime import datetime
from pathlib import Path

from ..archive import ARCHIVE_PATH
from ..protocol.wire import I32, I64, LEN, VARINT, WireError, iter_fields, to_signed

HANDSHAKE_S = 30.0  # messages écartés au début de chaque connexion
MAX_DEPTH = 8
MAX_FIELDS = 400  # au-delà, le message est résumé : une liste d'inventaire n'apprend rien sur un combat


def looks_like_text(chunk: bytes) -> bool:
    """Vrai si ces octets se lisent comme du texte : on n'en sort alors que la longueur."""
    if len(chunk) < 3:
        return False
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        return False
    readable = sum(1 for c in text if c.isalpha() or c in " '-_.,:!?")
    return readable >= 0.8 * len(text)


def describe(body: bytes, depth: int = 0, prefix: str = "", out: list[str] | None = None) -> list[str] | None:
    """Champs d'un message, à plat : « 2.1=57 ». None si ce n'est pas un message lisible."""
    out = [] if out is None else out
    try:
        for number, wire_type, value in iter_fields(body):
            name = f"{prefix}{number}"
            if wire_type == VARINT:
                out.append(f"{name}={to_signed(value)}")
            elif wire_type == I32:
                raw = value if isinstance(value, (bytes, bytearray)) else int(value).to_bytes(4, "little")
                out.append(f"{name}=f{struct.unpack('<f', raw)[0]:.4g}")
            elif wire_type == I64:
                raw = value if isinstance(value, (bytes, bytearray)) else int(value).to_bytes(8, "little")
                out.append(f"{name}=d{struct.unpack('<d', raw)[0]:.6g}")
            elif wire_type == LEN:
                chunk = bytes(value)
                inner = None
                if chunk and depth < MAX_DEPTH and not looks_like_text(chunk):
                    inner = describe(chunk, depth + 1, name + ".", [])
                if inner:
                    out.extend(inner)
                else:
                    out.append(f"{name}=LEN{len(chunk)}")
            else:
                return None
            if len(out) > MAX_FIELDS:
                return out
    except (WireError, struct.error, ValueError):
        return None
    return out


def dump(archive: sqlite3.Connection, start: float, end: float) -> tuple[list[str], int]:
    """(lignes, nombre de messages écartés car trop proches du début de leur connexion)."""
    lines, skipped = [], 0
    rows = archive.execute(
        "SELECT m.ts, m.direction, m.key, m.size, m.body, c.started_at FROM messages m JOIN connections c ON c.id = m.connection_id "
        "WHERE m.ts >= ? AND m.ts < ? ORDER BY m.id",
        (start, end),
    )
    for ts, direction, key, size, body, started_at in rows:
        if ts - started_at < HANDSHAKE_S:
            skipped += 1
            continue
        fields = describe(bytes(body))
        if fields is None:
            shown = f"illisible LEN{len(body)}"
        elif len(fields) > MAX_FIELDS:
            shown = " ".join(fields[:40]) + f" … ({len(fields)}+ champs)"
        else:
            shown = " ".join(fields)
        lines.append(f"{ts - start:9.3f} {direction} {key} {size} | {shown}")
    return lines, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", required=True, help='début, heure de ce PC : "AAAA-MM-JJ HH:MM"')
    parser.add_argument("--to", dest="end", required=True, help='fin, même format')
    parser.add_argument("--out", type=Path, default=ARCHIVE_PATH.parent / "extrait.txt")
    parser.add_argument("--archive", type=Path, default=ARCHIVE_PATH)
    args = parser.parse_args()
    try:
        start, end = (datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp() for text in (args.start, args.end))
    except ValueError:
        print('Dates attendues sous la forme "2026-10-13 14:14".')
        return 1
    if not args.archive.exists():
        print(f"Archive absente : {args.archive}")
        return 1
    archive = sqlite3.connect(f"file:{args.archive.as_posix()}?mode=ro", uri=True)
    lines, skipped = dump(archive, start, end)
    archive.close()
    header = f"# {args.start} -> {args.end} · {len(lines)} messages · {skipped} écartés (début de connexion) · structure et nombres seulement"
    args.out.write_text("\n".join([header, *lines]) + "\n", encoding="utf-8")
    print(f"{len(lines)} messages écrits dans {args.out} ({args.out.stat().st_size // 1024} Ko).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
