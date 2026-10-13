"""Sol des cartes de combat, appris en regardant les combats.

Le serveur n'envoie jamais le décor d'une carte. Mais tout ce qu'un combattant occupe est forcément
praticable : cases de départ, cases d'un chemin, arrivée d'une poussée, case d'une invocation. En les
cumulant par carte de combat, le sol se dessine combat après combat.

Limites : une case jamais vue n'est pas connue pour autant comme un obstacle, et rien ne distingue
un mur (qui coupe la ligne de vue) d'un trou (qui ne la coupe pas).
"""
import sqlite3
from collections import defaultdict

from . import journal

MAP_CHANGE = "jow"  # le joueur arrive sur une carte ; à l'entrée en combat, c'est la carte de combat

SCHEMA = """
CREATE TABLE IF NOT EXISTS fight_cells (
    map_id  INTEGER NOT NULL,
    cell    INTEGER NOT NULL,
    PRIMARY KEY (map_id, cell)
) WITHOUT ROWID;
"""


def learn(messages) -> dict[int, set[int]]:
    """Cases praticables vues, par carte de combat, dans une suite de messages (délai, sens, clé, champs)."""
    seen: dict[int, set[int]] = defaultdict(set)
    current: int | None = None  # carte où se trouve le joueur
    fighting = False  # entre l'entrée en combat (cases de départ) et la fin
    for when, direction, key, fields in messages:
        if direction != "s2c":
            continue
        if key == MAP_CHANGE:
            current, fighting = journal.number(fields, "1"), False
            continue
        item = journal.event(when, direction, key, fields)
        if item is None or current is None:
            continue
        if item.kind == "sides":
            fighting = True
        elif item.kind == "end":
            fighting = False
        if not fighting:
            continue
        cells: list[int] = []
        if item.kind in ("sides", "move"):
            cells = [cell for part in item.path for cell in part]
        elif item.kind in ("sheet", "summon", "push") and item.cell is not None:
            cells = [item.cell]
        seen[current].update(cell for cell in cells if 0 <= cell < 560)
    return {map_id: cells for map_id, cells in seen.items() if cells}


def store(conn: sqlite3.Connection, seen: dict[int, set[int]]) -> int:
    """Ajoute ces cases à la base ; renvoie le nombre de cases nouvelles."""
    conn.executescript(SCHEMA)
    before = conn.total_changes
    conn.executemany("INSERT OR IGNORE INTO fight_cells (map_id, cell) VALUES (?, ?)",
                     [(map_id, cell) for map_id, cells in seen.items() for cell in cells])
    conn.commit()
    return conn.total_changes - before


def walkable(conn: sqlite3.Connection, map_id: int) -> set[int]:
    """Cases de cette carte de combat déjà vues praticables."""
    conn.executescript(SCHEMA)
    return {cell for (cell,) in conn.execute("SELECT cell FROM fight_cells WHERE map_id = ?", (map_id,))}
