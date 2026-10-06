"""Retrouve dans l'archive, par leur structure, les messages connus après une rotation des clés.

La capture le fait toute seule en cours de partie (voir dofustool/identify.py) ; cette commande
sert à vérifier, ou à rattraper une connexion archivée. N'affiche que des clés, des numéros de
champ et des compteurs.

Usage : python -m dofustool.tools.reidentify [--connection N] [--write]
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from .. import db, identify
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
    parser.add_argument("--connection", type=int, help="numéro de la connexion archivée à examiner (défaut : la dernière)")
    parser.add_argument("--write", action="store_true", help="écrire les messages retrouvés dans keymap.json")
    args = parser.parse_args()

    if not ARCHIVE_PATH.exists():
        print("data/archive.sqlite absent : fais d'abord une capture.")
        return 1
    market = db.connect()
    context = identify.Context.load(market)
    market.close()
    if not context.items:
        print("Aucun item en base (données statiques non importées) : rien ne peut être vérifié.")
        return 1
    archive = sqlite3.connect(f"file:{ARCHIVE_PATH.as_posix()}?mode=ro", uri=True)
    row = archive.execute(
        "SELECT id, started_at FROM connections WHERE id = COALESCE(?, (SELECT MAX(id) FROM connections))", (args.connection,)
    ).fetchone()
    if row is None:
        print("Connexion introuvable dans l'archive.")
        return 1
    scan = identify.Scan(row[1])
    scan.feed(archive, row[0])
    archive.close()
    found, problems = identify.identify(scan, context)

    raw = identify.read_keymap(KEYMAP_PATH)
    changed = identify.changes(raw, found)
    print(f"Connexion {row[0]} du {datetime.fromtimestamp(row[1]):%d/%m/%Y %H:%M} : {len(scan.best)} clés échangées.")
    for name in identify.NAMES:
        if name in problems:
            state = problems[name]
        elif name not in found:
            state = "pas vu dans cette connexion" + (" (clé périmée)" if raw.get(name, {}).get("stale") else "")
        elif name in changed:
            state = f"retrouvé : clé {found[name].key}, champs {found[name].fields}"
        else:
            state = f"à jour (clé {found[name].key})"
        if name in found and found[name].partial:
            state += " ; effets d'équipement pas encore vus"
        print(f"  {name:17} {state}")

    prices = found.get("avg_prices")
    current = raw.get("avg_prices", {})
    new_build = prices is not None and (current.get("key") != prices.key or current.get("fields") != prices.fields)
    if not changed:
        print("keymap.json est déjà à jour.")
        return 0
    if not args.write:
        print("Relance avec --write pour écrire ces changements dans keymap.json.")
        return 0
    identify.write_keymap(KEYMAP_PATH, found if new_build else changed, new_build, db.MARKET_PATH.parent / "keymap-backups")
    print("keymap.json mis à jour (ancienne version gardée dans data/keymap-backups). Lance maintenant le backfill.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
