"""Retrouve dans l'archive, par sa structure, le message des prix moyens après une rotation des clés.

Cherche un gros message serveur -> client fait d'un seul champ répété dont chaque entrée
porte deux entiers : un identifiant d'item connu et un prix. N'affiche que des clés, des
numéros de champ et des compteurs.

Usage : python -m dofustool.tools.reidentify [--since AAAA-MM-JJ] [--write]
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from .. import db
from ..archive import ARCHIVE_PATH
from ..messages import KEYMAP_PATH, avg_prices
from ..protocol.tcp import S2C
from ..protocol.wire import LEN, VARINT, WireError, iter_fields

MIN_KNOWN_RATIO = 0.9


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    entries: int
    fields: dict[str, int]
    known_ratio: float | None  # part des identifiants connus de la base ; None si la base est vide

    @property
    def confident(self) -> bool:
        return self.known_ratio is not None and self.known_ratio >= MIN_KNOWN_RATIO


def match_avg_prices(key: str, body: bytes, known_items: set[int]) -> Candidate | None:
    """Renvoie un candidat si le corps a la structure d'une liste de prix moyens."""
    try:
        top = list(iter_fields(body))
        if len(top) < avg_prices.MIN_ENTRIES or len({n for n, _, _ in top}) != 1 or top[0][1] != LEN:
            return None
        values: dict[int, list[int]] = {}
        presence: Counter[int] = Counter()
        for _, wire_type, entry in top:
            if wire_type != LEN:
                return None
            for number, sub_type, value in iter_fields(entry):
                if sub_type != VARINT:
                    return None
                values.setdefault(number, []).append(value)
                presence[number] += 1
    except WireError:
        return None
    if len(values) != 2:
        return None
    # L'identifiant est présent dans chaque entrée et unique ; le prix peut manquer.
    ids = [n for n in values if presence[n] == len(top) and len(set(values[n])) == len(top)]
    if not ids:
        return None
    if len(ids) == 2 and known_items:
        ids.sort(key=lambda n: -len(known_items.intersection(values[n])))
    item_field = ids[0]
    price_field = next(n for n in values if n != item_field)
    ratio = len(known_items.intersection(values[item_field])) / len(top) if known_items else None
    return Candidate(key, len(top), {"entries": top[0][0], "item_id": item_field, "price": price_field}, ratio)


def find_candidates(archive: sqlite3.Connection, known_items: set[int], since: float = 0.0) -> list[Candidate]:
    found = []
    # Le plus gros message de chaque clé suffit : la liste du catalogue pèse des dizaines de Ko.
    for key, body, _ in archive.execute(
        "SELECT key, body, MAX(size) FROM messages WHERE direction = ? AND ts >= ? AND size >= 5000 GROUP BY key",
        (S2C, since),
    ):
        candidate = match_avg_prices(key, body, known_items)
        if candidate is not None:
            found.append(candidate)
    return sorted(found, key=lambda c: (-(c.known_ratio or 0), -c.entries))


def write_keymap(candidate: Candidate) -> None:
    keymap = json.loads(KEYMAP_PATH.read_text(encoding="utf-8"))
    keymap["avg_prices"] = {"key": candidate.key, "fields": candidate.fields}
    KEYMAP_PATH.write_text(json.dumps(keymap, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", help="ne regarder que les messages archivés depuis cette date (AAAA-MM-JJ)")
    parser.add_argument("--write", action="store_true", help="écrire le candidat retenu dans keymap.json")
    args = parser.parse_args()

    if not ARCHIVE_PATH.exists():
        print("data/archive.sqlite absent : fais d'abord une capture.")
        return 1
    since = datetime.strptime(args.since, "%Y-%m-%d").timestamp() if args.since else 0.0
    market = db.connect()
    known = {row[0] for row in market.execute("SELECT id FROM items")}
    market.close()
    if not known:
        print("Aucun item en base (données statiques non importées) : les identifiants ne peuvent pas être vérifiés.")
    archive = sqlite3.connect(f"file:{ARCHIVE_PATH.as_posix()}?mode=ro", uri=True)
    candidates = find_candidates(archive, known, since)
    archive.close()

    if not candidates:
        print("Aucun message n'a la structure des prix moyens. Vérifie que la capture va au-delà du choix du personnage.")
        return 1
    current = json.loads(KEYMAP_PATH.read_text(encoding="utf-8")).get("avg_prices")
    for c in candidates:
        ratio = "non vérifié" if c.known_ratio is None else f"{c.known_ratio:.1%} d'items connus"
        print(f"clé {c.key} : {c.entries} entrées, champs {c.fields}, {ratio}")
    best = candidates[0]
    if current == {"key": best.key, "fields": best.fields}:
        print("keymap.json est déjà à jour pour avg_prices.")
        return 0
    if not best.confident or sum(c.confident for c in candidates) != 1:
        print("Pas de candidat unique et sûr : rien n'est écrit. Voir MAINTENANCE.md, identification manuelle.")
        return 1
    if args.write:
        write_keymap(best)
        print(f"keymap.json mis à jour : avg_prices -> {best.key}. Lance maintenant le backfill.")
    else:
        print(f"Candidat retenu : {best.key}. Relance avec --write pour l'écrire dans keymap.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
