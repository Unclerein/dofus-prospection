"""Récupère sur DofusDB les caractéristiques de base des équipements vus à l'HDV.

Le dofus.sqlite ne permet pas de relier un item à ses caractéristiques. Cette commande
interroge l'API publique de DofusDB, une seule fois par item, et met le résultat en cache
dans data/market.sqlite. Elle ne fait jamais partie de la capture : une panne de DofusDB
ne coûte aucune donnée.

Usage : python -m dofustool.staticdata.effects [--all-equipment]
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request

from .. import db

API = "https://api.dofusdb.fr/items/{item_id}"
_HEADERS = {"User-Agent": "dofustool"}


def parse_effects(payload: dict) -> list[tuple[int, int, int]]:
    """Renvoie (id d'effet, min, max). DofusDB met « to » à 0 pour une valeur fixe."""
    out: dict[int, tuple[int, int]] = {}
    for effect in payload.get("effects") or []:
        low, high = int(effect.get("from") or 0), int(effect.get("to") or 0)
        if not high:
            high = low
        out[int(effect["effectId"])] = (min(low, high), max(low, high))
    return [(effect_id, low, high) for effect_id, (low, high) in out.items()]


def fetch(item_id: int) -> list[tuple[int, int, int]]:
    request = urllib.request.Request(API.format(item_id=item_id), headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=20) as response:
        return parse_effects(json.load(response))


def store(conn: sqlite3.Connection, item_id: int, effects: list[tuple[int, int, int]]) -> None:
    with conn:
        conn.execute("DELETE FROM item_effects WHERE item_id = ?", (item_id,))
        conn.executemany("INSERT INTO item_effects VALUES (?, ?, ?, ?)", [(item_id, *e) for e in effects])
        conn.execute("INSERT OR REPLACE INTO item_effects_fetched VALUES (?, ?)", (item_id, time.time()))


def missing_items(conn: sqlite3.Connection, all_equipment: bool = False) -> list[int]:
    scope = "" if all_equipment else "AND i.id IN (SELECT item_id FROM hdv_listings)"
    return [
        row[0]
        for row in conn.execute(
            "SELECT i.id FROM items i WHERE i.category_id = 0 AND i.exchangeable "
            f"AND i.id NOT IN (SELECT item_id FROM item_effects_fetched) {scope} ORDER BY i.id"
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all-equipment", action="store_true", help="tous les équipements, pas seulement ceux vus à l'HDV")
    args = parser.parse_args()
    conn = db.connect()
    todo = missing_items(conn, args.all_equipment)
    done = 0
    for item_id in todo:
        try:
            store(conn, item_id, fetch(item_id))
            done += 1
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
            print(f"Item {item_id} : échec ({exc}). Il sera retenté au prochain lancement.")
        time.sleep(0.2)  # rester léger pour l'API
    print(f"Caractéristiques de base récupérées : {done} sur {len(todo)} item(s) à traiter.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
