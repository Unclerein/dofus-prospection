"""Récupère sur DofusDB les caractéristiques de base des équipements vus à l'HDV.

Le dofus.sqlite ne permet pas de relier un item à ses caractéristiques. Cette commande
interroge l'API publique de DofusDB et met le résultat en cache dans data/market.sqlite.
Elle ne fait jamais partie de la capture : une panne de DofusDB ne coûte aucune donnée.

Une fiche est redemandée dans trois cas : elle manque ; la version du jeu connue de DofusDB a
changé depuis qu'elle a été lue (une mise à jour peut modifier n'importe quel équipement) ; les
annonces relevées en jeu la contredisent (une ligne que presque toutes portent et qu'elle ignore,
ou l'inverse).

Usage : python -m dofustool.staticdata.effects [--all-equipment]
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

from .. import db

API = "https://api.dofusdb.fr/items/{item_id}"
VERSION_API = "https://api.dofusdb.fr/version"
_HEADERS = {"User-Agent": "dofustool"}
VERSION_CHECK_S = 6 * 3600.0  # la version du jeu n'est redemandée à DofusDB qu'à cet intervalle
# Fiche contredite par les annonces : au moins tant d'annonces, dont cette part porte une ligne inconnue de la fiche.
CONTRADICTION_MIN_LISTINGS = 3
CONTRADICTION_SHARE = 0.6
MISSING_LINE_MIN_LISTINGS = 5  # une ligne de la fiche qu'aucune annonce ne porte : il en faut plus pour conclure
# Un exo que tout le monde pose ressemble à une ligne de base : la même fiche n'est redemandée qu'une fois par jour.
CONTRADICTION_RETRY_S = 24 * 3600.0


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


def fetch_version() -> str:
    """Version du jeu dont DofusDB publie les données."""
    request = urllib.request.Request(VERSION_API, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=20) as response:
        version = json.load(response)
    if not isinstance(version, (str, int, float)) or not str(version).strip():
        raise ValueError("version de DofusDB illisible")
    return str(version).strip()


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM template_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def version_check_due(conn: sqlite3.Connection, now: float) -> bool:
    checked = _meta(conn, "checked_at")
    return checked is None or now - float(checked) >= VERSION_CHECK_S


def note_version(conn: sqlite3.Connection, version: str, now: float) -> bool:
    """Retient la version du jeu connue de DofusDB. Renvoie True si elle a changé.

    Toutes les fiches lues jusqu'ici sont alors à redemander. La première vérification compte comme un
    changement : on ne sait pas de quelle version datent les fiches déjà en cache.
    """
    changed = _meta(conn, "version") != version
    with conn:
        conn.execute("INSERT OR REPLACE INTO template_meta VALUES ('checked_at', ?)", (repr(now),))
        if changed:
            conn.execute("INSERT OR REPLACE INTO template_meta VALUES ('version', ?)", (version,))
            conn.execute("INSERT OR REPLACE INTO template_meta VALUES ('stale_before', ?)", (repr(now),))
    return changed


def mark_refreshed(conn: sqlite3.Connection) -> None:
    """Signale à l'interface que des fiches déjà connues ont changé : ses calculs sont à refaire."""
    with conn:
        conn.execute("INSERT OR REPLACE INTO template_meta VALUES ('refreshed_at', ?)", (repr(time.time()),))


def stale_items(conn: sqlite3.Connection, all_equipment: bool = False) -> list[int]:
    """Fiches lues avant le dernier changement de version du jeu, celles des objets en vente d'abord."""
    limit = _meta(conn, "stale_before")
    if limit is None:
        return []
    scope = "" if all_equipment else "AND f.item_id IN (SELECT item_id FROM hdv_listings)"
    return [
        row[0]
        for row in conn.execute(
            f"SELECT f.item_id FROM item_effects_fetched f WHERE f.fetched_at < ? {scope} "
            "ORDER BY (SELECT MAX(captured_at) FROM hdv_listings h WHERE h.item_id = f.item_id) DESC, f.item_id",
            (float(limit),),
        )
    ]


def contradicted_items(conn: sqlite3.Connection, now: float) -> list[int]:
    """Fiches que les annonces relevées en jeu contredisent : le jeu a changé l'objet, la fiche non.

    Une ligne que la plupart des annonces portent et que la fiche ignore, ou une ligne à valeur positive
    de la fiche qu'aucune annonce ne porte. Seules les annonces relevées après la lecture de la fiche comptent.
    """
    fetched = dict(conn.execute("SELECT item_id, fetched_at FROM item_effects_fetched"))
    known: dict[int, set[int]] = {}
    expected: dict[int, set[int]] = {}
    for item_id, effect_id, high in conn.execute("SELECT item_id, effect_id, max_value FROM item_effects"):
        known.setdefault(item_id, set()).add(effect_id)
        if high > 0:
            expected.setdefault(item_id, set()).add(effect_id)
    seen: dict[int, Counter] = {}
    listings: Counter = Counter()
    for item_id, effects, captured_at in conn.execute("SELECT item_id, effects, captured_at FROM hdv_current WHERE effects != '[]'"):
        read = fetched.get(item_id)
        if read is None or captured_at <= read or now - read < CONTRADICTION_RETRY_S:
            continue
        listings[item_id] += 1
        seen.setdefault(item_id, Counter()).update({effect_id for effect_id, value in json.loads(effects) if value})
    out = []
    for item_id, count in listings.items():
        carried = seen[item_id]
        unknown = count >= CONTRADICTION_MIN_LISTINGS and any(
            n >= CONTRADICTION_SHARE * count for effect_id, n in carried.items() if effect_id not in known.get(item_id, set())
        )
        gone = count >= MISSING_LINE_MIN_LISTINGS and any(effect_id not in carried for effect_id in expected.get(item_id, ()))
        if unknown or gone:
            out.append(item_id)
    return sorted(out)


def pending_items(conn: sqlite3.Connection, now: float, all_equipment: bool = False) -> list[int]:
    """Tout ce qu'il faut demander à DofusDB : fiches manquantes, puis contredites, puis périmées."""
    out: dict[int, None] = {}
    for item_id in (*missing_items(conn, all_equipment), *contradicted_items(conn, now), *stale_items(conn, all_equipment)):
        out.setdefault(item_id)
    return list(out)


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
    now = time.time()
    try:
        if note_version(conn, fetch_version(), now):
            print(f"Version du jeu connue de DofusDB : {_meta(conn, 'version')}. Les fiches déjà lues sont rafraîchies.")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"Version de DofusDB non lue ({exc}) : seules les fiches manquantes sont demandées.")
    todo = pending_items(conn, now, args.all_equipment)
    done = 0
    for item_id in todo:
        try:
            store(conn, item_id, fetch(item_id))
            done += 1
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
            print(f"Item {item_id} : échec ({exc}). Il sera retenté au prochain lancement.")
        time.sleep(0.2)  # rester léger pour l'API
    if done:
        mark_refreshed(conn)
    print(f"Caractéristiques de base récupérées : {done} sur {len(todo)} item(s) à traiter.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
