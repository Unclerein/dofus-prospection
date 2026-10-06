"""Cases d'une carte de combat : coordonnées, numéros de case Dofus, disposition des salles.

Deux repères :
- **la grille** (col, row) : la carte losange redressée en carré. +col descend vers la droite
  à l'écran, +row descend vers la gauche. Les rotations de la confusion s'y appliquent telles quelles ;
- **le repère Dofus** (x, y), celui des numéros de case 0 à 559 (14 cases par ligne, 40 lignes
  en quinconce) : +x descend vers la droite, +y monte vers la droite. D'où col = x - ox, row = oy - y.

Le décalage (ox, oy) entre la disposition d'une salle et le repère Dofus se déduit des positions
réelles des combattants (fit_offset) : il n'est connu qu'avec une capture du combat.

Fichiers de disposition (maps/*.txt) : un caractère par case, une ligne par rangée de la grille.
    .  case marchable
    #  obstacle (mur, pilier)
    -  vide (hors de l'arène)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

MAPS_DIR = Path(__file__).resolve().parent / "maps"

MAP_WIDTH = 14  # cases par ligne d'une carte Dofus
MAP_HEIGHT = 20  # paires de lignes
CELL_COUNT = MAP_WIDTH * MAP_HEIGHT * 2  # 560

Cell = tuple[int, int]


class Kind(Enum):
    EMPTY = "-"
    WALKABLE = "."
    WALL = "#"


# --- numéros de case Dofus ------------------------------------------------------


def _build_coords() -> list[tuple[int, int]]:
    """Coordonnées (x, y) de chaque numéro de case, ligne par ligne : chaque paire de lignes
    avance x d'une case pour la ligne décalée, puis recule y pour la paire suivante."""
    coords = []
    start_x = start_y = 0
    for _ in range(MAP_HEIGHT):
        coords += [(start_x + i, start_y + i) for i in range(MAP_WIDTH)]
        start_x += 1
        coords += [(start_x + i, start_y + i) for i in range(MAP_WIDTH)]
        start_y -= 1
    return coords


_COORDS = _build_coords()
_IDS = {xy: cell_id for cell_id, xy in enumerate(_COORDS)}


def cell_to_xy(cell_id: int) -> tuple[int, int]:
    """Numéro de case Dofus -> (x, y). ValueError hors de 0..559."""
    if not 0 <= cell_id < CELL_COUNT:
        raise ValueError(f"case {cell_id} hors de la carte")
    return _COORDS[cell_id]


def xy_to_cell(x: int, y: int) -> int | None:
    """(x, y) -> numéro de case Dofus, ou None si le point est hors de la carte."""
    return _IDS.get((x, y))


# --- disposition d'une salle ----------------------------------------------------


@dataclass(frozen=True)
class Layout:
    name: str
    rows: tuple[tuple[Kind, ...], ...]

    @property
    def height(self) -> int:
        return len(self.rows)

    @property
    def width(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    def kind(self, cell: Cell) -> Kind:
        col, row = cell
        if 0 <= row < self.height and 0 <= col < self.width:
            return self.rows[row][col]
        return Kind.EMPTY

    def walkable(self, cell: Cell) -> bool:
        return self.kind(cell) is Kind.WALKABLE

    def cells(self, kind: Kind | None = None) -> list[Cell]:
        return [
            (col, row)
            for row, line in enumerate(self.rows)
            for col, k in enumerate(line)
            if kind is None or k is kind
        ]

    def to_text(self) -> str:
        return "\n".join("".join(k.value for k in line) for line in self.rows) + "\n"


def parse_text(name: str, text: str) -> Layout:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"disposition {name!r} vide")
    width = max(len(line) for line in lines)
    rows = []
    for number, line in enumerate(lines, start=1):
        try:
            rows.append(tuple(Kind(char) for char in line.ljust(width, Kind.EMPTY.value)))
        except ValueError:
            raise ValueError(f"disposition {name!r}, ligne {number} : caractère inconnu") from None
    return Layout(name, tuple(rows))


def parse_simulator(name: str, text: str) -> Layout:
    """Disposition exportée par le mode Édition du simulateur comteharebourg.com.

    Format : {"size": [rangées, colonnes], "details": [[rangée, première col, dernière col, type], ...]}
    avec type 1 pour marchable et 2 pour obstacle.
    """
    data = json.loads(text)
    height, width = (int(v) for v in data["size"])
    grid = [[Kind.EMPTY] * width for _ in range(height)]
    kinds = {1: Kind.WALKABLE, 2: Kind.WALL}
    for row, first, last, code in data["details"]:
        kind = kinds.get(int(code))
        if kind is None:
            raise ValueError(f"type de case inconnu : {code}")
        if not (0 <= row < height and 0 <= first <= last < width):
            raise ValueError(f"plage {row}:{first}-{last} hors de {height}x{width}")
        for col in range(first, last + 1):
            grid[row][col] = kind
    return Layout(name, tuple(tuple(line) for line in grid))


def load(name: str) -> Layout:
    return parse_text(name, (MAPS_DIR / f"{name}.txt").read_text(encoding="utf-8"))


# --- passage grille <-> repère Dofus -----------------------------------------------


@dataclass(frozen=True)
class Placement:
    """Position de la disposition dans le repère Dofus : col = x - ox, row = oy - y."""

    ox: int
    oy: int

    def to_grid(self, cell_id: int) -> Cell:
        x, y = cell_to_xy(cell_id)
        return x - self.ox, self.oy - y

    def to_cell(self, cell: Cell) -> int | None:
        col, row = cell
        return xy_to_cell(col + self.ox, self.oy - row)


def fit_offset(layout: Layout, seen: list[int]) -> list[Placement]:
    """Placements où toutes les cases occupées observées (numéros Dofus) sont marchables
    et où toute l'arène tient dans la carte. Plusieurs candidats : il faut plus d'observations."""
    points = [cell_to_xy(cell_id) for cell_id in set(seen)]
    used = layout.cells(Kind.WALKABLE) + layout.cells(Kind.WALL)
    out = []
    for ox in range(-layout.width, 34 + 1):
        for oy in range(-20, 14 + layout.height + 1):
            p = Placement(ox, oy)
            if all(layout.walkable((x - ox, oy - y)) for x, y in points) and all(
                p.to_cell(cell) is not None for cell in used
            ):
                out.append(p)
    return out
