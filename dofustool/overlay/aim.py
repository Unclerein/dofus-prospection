"""Aide à la visée du combat du Comte Harebourg : ce que l'overlay dessine sur la case survolée.

Deux morceaux, sans fenêtre ni Windows :
- la géométrie : passage d'une case Dofus à un point de la zone de jeu, et retour, une fois le cadrage
  calibré (le cadrage est fixe tant qu'on ne zoome pas) ;
- le plan : pour une case ou un ennemi survolé, la case où cliquer compte tenu de ma confusion, et si
  c'est le Comte, la case où arrive celui qui est téléporté, avec le danger éventuel.
"""
import json
import math
from dataclasses import dataclass
from pathlib import Path

from ..fight import grid, harebourg

COMTE_MAP = 112206595  # salle du Comte Harebourg
COMTE_MONSTER = 3416
COMTE_PLACEMENT = grid.Placement(7, 6)  # décalage entre la salle dessinée (maps/comte.txt) et les cases Dofus

CALIBRATION_PATH = Path(__file__).resolve().parents[2] / "data" / "aim_calibration.json"
MAX_RESIDUAL = 0.25  # écart toléré entre les points de calibrage et la grille calculée, en largeurs de case


# --- géométrie --------------------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    """Cadrage de la grille dans la zone de jeu : centre de la case (0, 0) et largeur d'une case, en pixels.

    Une case est un losange deux fois plus large que haut. +x descend vers la droite, +y monte vers la droite."""

    ox: float
    oy: float
    width: float

    def centre(self, cell: int) -> tuple[float, float]:
        x, y = grid.cell_to_xy(cell)
        return self.ox + self.width * (x + y) / 2, self.oy + self.width * (x - y) / 4

    def diamond(self, cell: int) -> list[tuple[float, float]]:
        cx, cy = self.centre(cell)
        half_w, half_h = self.width / 2, self.width / 4
        return [(cx, cy - half_h), (cx + half_w, cy), (cx, cy + half_h), (cx - half_w, cy)]

    def cell_at(self, px: float, py: float) -> int | None:
        """La case sous ce point, ou None hors de la carte."""
        a, b = (px - self.ox) / self.width, (py - self.oy) / self.width
        x, y = a + 2 * b, a - 2 * b
        best, best_d = None, math.inf
        for cx in (math.floor(x), math.ceil(x)):
            for cy in (math.floor(y), math.ceil(y)):
                cell = grid.xy_to_cell(cx, cy)
                if cell is None:
                    continue
                mx, my = self.centre(cell)
                # distance « losange » : un point est dans la case si |dx|/(w/2) + |dy|/(w/4) <= 1
                d = abs(px - mx) / (self.width / 2) + abs(py - my) / (self.width / 4)
                if d < best_d:
                    best, best_d = cell, d
        return best if best_d <= 1.0001 else None


def calibrate(points: list[tuple[int, float, float]]) -> tuple[Frame, float]:
    """Cadrage qui place au mieux ces cases sous ces points (case, x, y) ; et l'écart moyen, en largeurs de case.

    Il faut deux cases différentes au moins (trois inconnues, deux équations par point)."""
    rows, values = [], []
    for cell, px, py in points:
        x, y = grid.cell_to_xy(cell)
        rows += [(1.0, 0.0, (x + y) / 2), (0.0, 1.0, (x - y) / 4)]
        values += [px, py]
    # moindres carrés à la main : 3 inconnues, équations normales
    ata = [[sum(r[i] * r[j] for r in rows) for j in range(3)] for i in range(3)]
    atb = [sum(r[i] * v for r, v in zip(rows, values)) for i in range(3)]
    ox, oy, width = _solve3(ata, atb)
    frame = Frame(ox, oy, width)
    if width <= 0:
        raise ValueError("calibrage impossible : les points ne forment pas une grille")
    error = sum(math.dist(frame.centre(cell), (px, py)) for cell, px, py in points) / len(points) / width
    return frame, error


def _solve3(a: list[list[float]], b: list[float]) -> tuple[float, float, float]:
    """Système 3 × 3 par élimination de Gauss ; ValueError s'il n'a pas de solution unique."""
    m = [row[:] + [v] for row, v in zip(a, b)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-9:
            raise ValueError("calibrage impossible : il faut deux cases différentes")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(3):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [m[r][k] - f * m[col][k] for k in range(4)]
    return tuple(m[i][3] / m[i][i] for i in range(3))


def load_frame(size: tuple[int, int], path: Path = CALIBRATION_PATH) -> Frame | None:
    """Cadrage enregistré pour cette taille de zone de jeu."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get(f"{size[0]}x{size[1]}")
        return Frame(float(raw["ox"]), float(raw["oy"]), float(raw["width"])) if raw else None
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return None


def save_frame(size: tuple[int, int], frame: Frame, path: Path = CALIBRATION_PATH) -> None:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved = saved if isinstance(saved, dict) else {}
    except (OSError, ValueError):
        saved = {}
    saved[f"{size[0]}x{size[1]}"] = {"ox": frame.ox, "oy": frame.oy, "width": frame.width}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, indent=1), encoding="utf-8")


# --- plan ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    target: int  # case survolée
    aim: int | None  # case où cliquer ; None si elle tombe hors de la carte
    aim_ok: bool  # la case où cliquer est une case du sol (sinon le clic n'est pas possible)
    landing: int | None = None  # case d'arrivée de celui qui est téléporté, si la cible est le Comte
    mover: str = ""  # « toi » ou « le Comte »
    verdict: harebourg.Verdict | None = None
    blast: tuple[int, ...] = ()  # cases où les alliés meurent (invocation qui explose)
    me_in_blast: bool = False


def _to_cell(cell: harebourg.Cell) -> int | None:
    x, y = cell[0] + COMTE_PLACEMENT.ox, COMTE_PLACEMENT.oy - cell[1]
    return grid.xy_to_cell(x, y)


def _to_grid(cell: int) -> harebourg.Cell:
    x, y = grid.cell_to_xy(cell)
    return x - COMTE_PLACEMENT.ox, COMTE_PLACEMENT.oy - y


def plan(state: dict, target: int, layout: grid.Layout) -> Plan | None:
    """Ce qu'il faut montrer quand je survole `target`, d'après l'état du combat écrit par la capture.

    None hors de la salle du Comte, sans confusion connue, ou sans ma case."""
    if not state.get("active") or state.get("map") != COMTE_MAP or state.get("confusion") is None:
        return None
    fighters = [f for f in state.get("fighters", []) if not f.get("dead") and f.get("cell") is not None]
    me = next((f["cell"] for f in fighters if f["kind"] == "moi"), None)
    if me is None or target == me:
        return None
    rotation = harebourg.Rotation(state["confusion"] % 360)
    shot = harebourg.shot_at(layout, _to_grid(me), _to_grid(target), rotation)
    aim = _to_cell(shot.aim)
    comte = next((f["cell"] for f in fighters if f.get("monster") == COMTE_MONSTER), None)
    if comte is None or target != comte:
        return Plan(target, aim, shot.clickable)
    others = [f for f in fighters if f["cell"] not in (me, comte)]
    occupied = frozenset(_to_grid(f["cell"]) for f in others)
    summons = frozenset(_to_grid(f["cell"]) for f in others if f.get("kind") == "invocation" and f.get("mine"))
    swap = harebourg.swap(layout, int(state.get("round") or 1), _to_grid(me), _to_grid(comte), occupied, summons)
    blast = tuple(c for c in (_to_cell(g) for g in swap.blast) if c is not None)
    return Plan(
        target, aim, shot.clickable, landing=_to_cell(swap.destination), mover="toi" if swap.mover == "attaquant" else "le Comte",
        verdict=swap.verdict, blast=blast, me_in_blast=me in blast,
    )  # fmt: skip
