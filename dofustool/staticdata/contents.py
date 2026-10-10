"""Conteneurs de ressources : ce que donne un sachet, un tonneau ou un sac quand on l'utilise.

Un Sachet d'Aubergines donne 10 Aubergine, un Sac de blé 50 Blé. Le dofus.sqlite ne relie pas un
objet à ce qu'il donne : la liste vient de l'API publique de DofusDB (effet 209, « donne un objet »),
gardée dans data/market.sqlite. Seuls deux types d'objets sont retenus, Conteneur et Sac de
ressources : le même effet sert aussi à des essences de gardien, des viandes, des friandises ou des
cadeaux, qui ne se comportent pas comme de simples paquets d'une ressource.

Usage : python -m dofustool.staticdata.contents
"""
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import db

API = "https://api.dofusdb.fr/items"
GIVES_ITEM = 209  # effet « donne un objet » : value = l'objet, diceSide = la quantité
KINDS = ("Conteneur", "Sac de ressources")
PAGE = 50
MAX_PAGES = 40
_HEADERS = {"User-Agent": "dofustool"}


def parse(item: dict) -> tuple[int, int, int] | None:
    """(conteneur, objet donné, quantité) si l'objet donne un seul type d'objet, en quantité connue."""
    gives = [e for e in item.get("possibleEffects") or [] if e.get("effectId") == GIVES_ITEM]
    if len(gives) != 1:
        return None  # un lot de plusieurs objets différents n'est pas un paquet d'une ressource
    content, quantity = gives[0].get("value"), gives[0].get("diceSide") or gives[0].get("diceNum")
    if not isinstance(item.get("id"), int) or not isinstance(content, int) or not isinstance(quantity, int) or quantity < 1:
        return None
    return item["id"], content, quantity


def fetch_all() -> list[tuple[int, int, int]]:
    """Tous les objets de DofusDB qui donnent un seul type d'objet à l'utilisation."""
    out, skip = [], 0
    for _ in range(MAX_PAGES):
        query = urllib.parse.urlencode(
            {"possibleEffects.effectId": GIVES_ITEM, "$limit": PAGE, "$skip": skip, "$select[]": ["id", "possibleEffects"]}, doseq=True
        )
        request = urllib.request.Request(f"{API}?{query}", headers=_HEADERS)
        with urllib.request.urlopen(request, timeout=30) as response:
            page = json.load(response)
        rows = page.get("data") or []
        out += [found for found in map(parse, rows) if found is not None]
        skip += len(rows)
        if not rows or skip >= int(page.get("total") or 0):
            break
        time.sleep(0.15)  # rester léger pour l'API
    return out


def store(conn: sqlite3.Connection, rows: list[tuple[int, int, int]], version: str | None = None) -> int:
    """Remplace la liste des conteneurs. Ne garde que les deux types voulus, et ce que cette base connaît."""
    kinds = dict(conn.execute(f"SELECT id, type_name FROM items WHERE type_name IN ({','.join('?' * len(KINDS))})", KINDS))
    known = {row[0] for row in conn.execute("SELECT id FROM items")}
    kept = [(container, content, quantity) for container, content, quantity in rows if container in kinds and content in known]
    with conn:
        conn.execute("DELETE FROM item_contents")
        conn.executemany("INSERT OR REPLACE INTO item_contents VALUES (?, ?, ?)", kept)
        conn.execute("INSERT OR REPLACE INTO template_meta VALUES ('contents_version', ?)", (version or "",))
    return len(kept)


def due(conn: sqlite3.Connection) -> bool:
    """À (re)demander : jamais lue, ou lue pour une autre version du jeu que celle connue de DofusDB."""
    meta = dict(conn.execute("SELECT key, value FROM template_meta WHERE key IN ('contents_version', 'version')"))
    return "contents_version" not in meta or meta["contents_version"] != meta.get("version", "")


def refresh(conn: sqlite3.Connection) -> int:
    version = conn.execute("SELECT value FROM template_meta WHERE key = 'version'").fetchone()
    return store(conn, fetch_all(), version[0] if version else None)


def main() -> int:
    conn = db.connect()
    try:
        count = refresh(conn)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"Conteneurs non lus ({exc}) : à relancer plus tard.")
        return 1
    finally:
        conn.close()
    print(f"Conteneurs de ressources enregistrés : {count}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
