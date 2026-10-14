"""Règles du combat du Comte Harebourg (Frigost 3), en coordonnées de grille (voir fight.grid).

Sources : guides communautaires et harebourg-ux (github.com/Drayken/harebourg-ux), dont les règles
de confusion ont été vérifiées en jeu sur Dofus 3. Rien n'est repris de son code. Les points marqués
« à confirmer » le seront avec une capture du combat.

Confusion
- En début de tour, chaque personnage lance Comtoise sur lui-même et reçoit une confusion : ses sorts
  sont tournés autour de lui de 90° horaire, 180° ou 90° contre horaire. Elle est fixée pour le tour,
  quoi qu'il arrive à ses PV ensuite. Le chat de combat fait foi ; la table des PV n'est qu'indicative.
- Chaque ligne de dégâts infligée au contact ajoute 90° horaire, en boucle :
  90° horaire -> 180° -> 90° contre horaire -> tout droit -> 90° horaire. Un monstre qui a reçu
  10 coups au contact ne fait plus tourner.
- « Horaire » tourne la case d'arrivée dans le sens des aiguilles d'une montre à l'écran.
- Un sort qui arrive hors de la carte ou sur une case non marchable est un échec critique ;
  au contact, l'échec critique termine le tour.
- Les invocations ne sont jamais confuses.

Le Comte
- Tours impairs : frapper le Comte envoie l'attaquant à l'opposé, symétrique par rapport au Comte.
- Tours pairs : c'est le Comte qui part à l'opposé, symétrique par rapport à l'attaquant.
- Si le Comte arrive sur un obstacle ou hors de l'arène, il lance Air du Temps : toute l'équipe meurt.
- Mi-temps : en début de tour, une croix de taille 3 autour du Comte, centre compris, pour un tour.
  Un allié qui commence son tour dessus meurt.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .grid import Cell, Layout

MELEE_LIMIT = 10  # coups au contact au-delà desquels un monstre ne fait plus tourner
MI_TEMPS_SIZE = 3


class Rotation(Enum):
    """Déviation des sorts, en degrés dans le sens horaire."""

    STRAIGHT = 0
    CLOCKWISE = 90
    HALF = 180
    COUNTERCLOCKWISE = 270

    def bumped(self) -> Rotation:
        """Après une ligne de dégâts au contact : +90° horaire."""
        return Rotation((self.value + 90) % 360)

    @property
    def inverse(self) -> Rotation:
        return Rotation((360 - self.value) % 360)

    @property
    def label(self) -> str:
        return LABELS[self]


LABELS = {
    Rotation.STRAIGHT: "tout droit",
    Rotation.CLOCKWISE: "90° horaire",
    Rotation.HALF: "180°",
    Rotation.COUNTERCLOCKWISE: "90° contre horaire",
}

# Confusion de début de tour selon les PV, du plus haut au plus bas : (PV minimum en %, confusion).
# Indicatif seulement : aux bornes, le jeu peut donner la confusion voisine.
LIFE_BANDS: tuple[tuple[int, Rotation], ...] = (
    (90, Rotation.CLOCKWISE),
    (75, Rotation.COUNTERCLOCKWISE),
    (45, Rotation.HALF),
    (30, Rotation.COUNTERCLOCKWISE),
    (0, Rotation.CLOCKWISE),
)


def rotation_for_life(percent: float) -> Rotation:
    """Confusion attendue en début de tour pour ce pourcentage de PV (indicatif)."""
    for floor, rotation in LIFE_BANDS:
        if percent >= floor:
            return rotation
    return LIFE_BANDS[-1][1]


def rotate(offset: Cell, rotation: Rotation) -> Cell:
    """Tourne un écart de cases. Un quart de tour horaire : (col, row) -> (-row, col)."""
    col, row = offset
    for _ in range(rotation.value // 90):
        col, row = -row, col
    return col, row


def landing(me: Cell, cursor: Cell, rotation: Rotation) -> Cell:
    """Case où tombe un sort lancé sur `cursor` par un personnage en `me`."""
    col, row = rotate((cursor[0] - me[0], cursor[1] - me[1]), rotation)
    return me[0] + col, me[1] + row


def aim(me: Cell, target: Cell, rotation: Rotation) -> Cell:
    """Case à cliquer pour que le sort tombe sur `target`."""
    return landing(me, target, rotation.inverse)


def is_melee(a: Cell, b: Cell) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1


@dataclass(frozen=True, slots=True)
class Shot:
    aim: Cell  # case à cliquer
    landing: Cell  # case où le sort tombe
    clickable: bool  # la case à cliquer est une case marchable de l'arène
    critical_failure: bool  # le sort tombe hors de l'arène ou sur un obstacle
    melee: bool  # cible au contact : chaque ligne de dégâts ajoutera 90° horaire


def shot_at(layout: Layout, me: Cell, target: Cell, rotation: Rotation) -> Shot:
    """Où cliquer pour toucher `target`."""
    cell = aim(me, target, rotation)
    return Shot(cell, target, layout.walkable(cell), not layout.walkable(target), is_melee(me, target))


def cast_on(layout: Layout, me: Cell, cursor: Cell, rotation: Rotation) -> Shot:
    """Où tombe un sort lancé sur `cursor`."""
    cell = landing(me, cursor, rotation)
    return Shot(cursor, cell, layout.walkable(cursor), not layout.walkable(cell), is_melee(me, cell))


# --- le Comte : symétries et Air du Temps --------------------------------------------


class Verdict(Enum):
    SAFE = "sans danger"
    OCCUPIED = "case occupée"  # effet à confirmer : c'est peut-être ainsi qu'on lève l'invulnérabilité
    RISKY = "destination impossible pour l'attaquant"  # effet à confirmer
    WIPE = "Air du Temps : toute l'équipe meurt"


@dataclass(frozen=True, slots=True)
class Swap:
    mover: str  # « attaquant » (tours impairs) ou « comte » (tours pairs)
    destination: Cell
    verdict: Verdict


def mirror(cell: Cell, center: Cell) -> Cell:
    return 2 * center[0] - cell[0], 2 * center[1] - cell[1]


def swap(layout: Layout, round_number: int, attacker: Cell, comte: Cell, occupied: frozenset[Cell] = frozenset()) -> Swap:
    """Ce qui arrive si le personnage en `attacker` frappe le Comte pendant le tour `round_number`."""
    if round_number % 2:
        destination = mirror(attacker, comte)
        mover = "attaquant"
        blocked = Verdict.RISKY
    else:
        destination = mirror(comte, attacker)
        mover = "comte"
        blocked = Verdict.WIPE
    if not layout.walkable(destination):
        verdict = blocked
    elif destination in occupied:
        verdict = Verdict.OCCUPIED
    else:
        verdict = Verdict.SAFE
    return Swap(mover, destination, verdict)


def swap_map(
    layout: Layout, round_number: int, comte: Cell, occupied: frozenset[Cell] = frozenset()
) -> dict[Cell, Swap]:
    """Pour chaque case marchable libre : ce qui arriverait en frappant le Comte depuis cette case."""
    return {
        cell: swap(layout, round_number, cell, comte, occupied)
        for cell in layout.cells()
        if layout.walkable(cell) and cell != comte and cell not in occupied
    }


def mi_temps(layout: Layout, comte: Cell) -> list[Cell]:
    """Cases de la croix Mi-temps autour du Comte, centre compris, limitées aux cases marchables."""
    cells = [comte]
    for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        cells += [(comte[0] + dc * k, comte[1] + dr * k) for k in range(1, MI_TEMPS_SIZE + 1)]
    return [cell for cell in cells if layout.walkable(cell)]
