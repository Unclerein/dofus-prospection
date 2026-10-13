"""Relit l'archive pour apprendre le sol des cartes de combat (voir dofustool/fight/terrain.py).

N'affiche que des compteurs.

Usage : python -m dofustool.tools.learn_maps
"""
import sqlite3
import sys

from .. import db
from ..archive import ARCHIVE_PATH
from ..fight import journal, terrain
from .dump_window import describe

KEYS = (terrain.MAP_CHANGE, journal.START_CELLS, journal.MOVE, journal.SHEET, journal.ACTION, journal.FIGHT_END)


def messages(archive: sqlite3.Connection):
    marks = ",".join("?" * len(KEYS))
    rows = archive.execute(f"SELECT ts, direction, key, body FROM messages WHERE direction = 's2c' AND key IN ({marks}) ORDER BY id", KEYS)
    for ts, direction, key, body in rows:
        fields = describe(bytes(body))
        if fields is not None:
            yield ts, direction, key, [tuple(pair.split("=", 1)) for pair in fields]


def main() -> int:
    if not ARCHIVE_PATH.exists():
        print(f"Archive absente : {ARCHIVE_PATH}")
        return 1
    archive = sqlite3.connect(f"file:{ARCHIVE_PATH.as_posix()}?mode=ro", uri=True)
    seen = terrain.learn(messages(archive))
    archive.close()
    conn = db.connect()
    added = terrain.store(conn, seen)
    maps, cells = conn.execute("SELECT COUNT(DISTINCT map_id), COUNT(*) FROM fight_cells").fetchone()
    conn.close()
    print(f"{len(seen)} cartes de combat vues dans l'archive, {added} cases nouvelles. En base : {maps} cartes, {cells} cases.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
