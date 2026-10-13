"""Décor des cartes, lu dans les fichiers du jeu installés sur ce PC.

Le serveur n'envoie jamais le décor : le client le tient de ses propres fichiers. Chaque carte y
décrit ses 560 cases : praticable ou non, laisse passer la ligne de vue ou non, case de départ d'une
équipe. On lit ces fichiers sur le disque, tels qu'ils sont installés ; on ne touche ni au client
qui tourne ni à sa mémoire.

Le résultat va dans data/static/maps.sqlite, sur ce PC seulement : ce sont des données du jeu, à ne
pas mettre dans le dépôt. La lecture demande la bibliothèque UnityPy (pip install UnityPy), dont le
reste de l'application n'a pas besoin.

Usage : python -m dofustool.staticdata.maps [--game DOSSIER]
"""
import argparse
import os
import re
import sqlite3
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

from ..fight.grid import CELL_COUNT

MAPS_PATH = Path("data") / "static" / "maps.sqlite"
MAP_DATA = Path("Dofus_Data") / "StreamingAssets" / "Content" / "Map" / "Data"
VERSION_FILE = Path("Dofus_Data") / "globalgamemanagers"
VERSION = re.compile(rb"\d{4}\.\d+\.\d+[abfp]\d+")

SCHEMA = """
CREATE TABLE IF NOT EXISTS maps (
    id    INTEGER PRIMARY KEY,
    mov   BLOB NOT NULL,   -- un bit par case : praticable
    los   BLOB NOT NULL,   -- un bit par case : laisse passer la ligne de vue
    red   BLOB NOT NULL,   -- cases de départ d'une équipe
    blue  BLOB NOT NULL    -- cases de départ de l'autre
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS maps_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def game_dir(env: dict[str, str] | None = None) -> Path:
    """Dossier où le launcher installe Dofus."""
    env = dict(os.environ) if env is None else env
    return Path(env.get("LOCALAPPDATA", "")) / "Ankama" / "Dofus-dofus3"


def pack(cells) -> bytes:
    """Un ensemble de cases en un bit par case."""
    bits = bytearray(CELL_COUNT // 8)
    for cell in cells:
        if 0 <= cell < CELL_COUNT:
            bits[cell // 8] |= 1 << (cell % 8)
    return bytes(bits)


def unpack(bits: bytes) -> frozenset[int]:
    return frozenset(cell for cell in range(min(CELL_COUNT, len(bits) * 8)) if bits[cell // 8] >> (cell % 8) & 1)


@dataclass(frozen=True)
class Terrain:
    map_id: int
    mov: frozenset[int]
    los: frozenset[int]
    red: frozenset[int]
    blue: frozenset[int]

    def walkable(self, cell: int) -> bool:
        return cell in self.mov

    def hole(self, cell: int) -> bool:
        """Case où l'on ne marche pas mais par-dessus laquelle on voit."""
        return cell not in self.mov and cell in self.los

    def wall(self, cell: int) -> bool:
        """Case qui coupe la ligne de vue."""
        return cell not in self.los


def terrain(map_id: int, cells: list[dict]) -> Terrain:
    """Le décor d'une carte à partir de la liste de cases du fichier du jeu."""
    def having(flag: str) -> frozenset[int]:
        return frozenset(cell["cellNumber"] for cell in cells if cell.get(flag))

    return Terrain(map_id, having("mov"), having("los"), having("red"), having("blue"))


def unity_version(game: Path) -> str | None:
    """Version d'Unity du jeu : les paquets de cartes ne la portent pas, il faut la donner au lecteur."""
    try:
        with open(game / VERSION_FILE, "rb") as handle:
            found = VERSION.search(handle.read(4096))
    except OSError:
        return None
    return found.group().decode() if found else None


def read_game(game: Path):
    """Le décor de chaque carte des fichiers du jeu."""
    import UnityPy
    import UnityPy.config

    version = unity_version(game)
    if version:
        UnityPy.config.FALLBACK_UNITY_VERSION = version
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for bundle in sorted((game / MAP_DATA).glob("mapdata_*.bundle")):
            for entry in UnityPy.load(str(bundle)).objects:
                if entry.type.name != "MonoBehaviour":
                    continue
                tree = entry.read_typetree()
                if isinstance(tree.get("cellsData"), list) and "id" in tree:
                    yield terrain(tree["id"], tree["cellsData"])


def open_maps(path: Path = MAPS_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def store(conn: sqlite3.Connection, terrains, version: str = "", now: float | None = None) -> int:
    """Remplace le décor enregistré par celui-ci ; renvoie le nombre de cartes."""
    rows = [(t.map_id, pack(t.mov), pack(t.los), pack(t.red), pack(t.blue)) for t in terrains]
    with conn:
        conn.execute("DELETE FROM maps")
        conn.executemany("INSERT OR REPLACE INTO maps (id, mov, los, red, blue) VALUES (?, ?, ?, ?, ?)", rows)
        conn.executemany("INSERT OR REPLACE INTO maps_meta (key, value) VALUES (?, ?)",
                         [("game_version", version), ("read_at", str(time.time() if now is None else now))])
    return len(rows)


def load(conn: sqlite3.Connection, map_id: int) -> Terrain | None:
    row = conn.execute("SELECT mov, los, red, blue FROM maps WHERE id = ?", (map_id,)).fetchone()
    return Terrain(map_id, *map(unpack, row)) if row else None


def game_version(game: Path) -> str:
    """Version du jeu installé, pour savoir quand relire les cartes."""
    try:
        return str((game / "Dofus_Data" / "app.info").stat().st_mtime_ns)
    except OSError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--game", type=Path, default=game_dir(), help="dossier d'installation de Dofus")
    args = parser.parse_args()
    if not (args.game / MAP_DATA).is_dir():
        print(f"Cartes du jeu introuvables dans {args.game}. Donne le dossier avec --game.")
        return 1
    try:
        import UnityPy  # noqa: F401
    except ImportError:
        print("Il manque la bibliothèque UnityPy : pip install UnityPy")
        return 1
    started = time.time()
    conn = open_maps()
    count = store(conn, read_game(args.game), game_version(args.game))
    conn.close()
    print(f"{count} cartes lues en {time.time() - started:.0f} s, écrites dans {MAPS_PATH}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
