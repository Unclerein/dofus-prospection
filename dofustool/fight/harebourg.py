"""Règles du combat du Comte Harebourg (Frigost 3), en coordonnées de grille (voir fight.grid).

Sources : la description officielle du sort Carillon (données du jeu, 3.7), sept essais capturés en
octobre 2026, et harebourg-ux (github.com/Drayken/harebourg-ux), dont rien n'est repris du code.

Confusion
- En début de tour, chaque personnage lance Comtoise sur lui-même et reçoit une confusion : ses sorts
  sont tournés autour de lui. L'angle dépend de la tranche de PV (90 % et plus, 75, 60, 45, 30, moins) ;
  le serveur l'annonce, et la capture le lit tel quel. Les lancers capturés tombent là où la rotation
  le prévoit (220 sur 238 ; les autres sont des sorts qui ne tombent pas sur la case visée).
- Chaque ligne de dégâts infligée au contact ajoute 90° horaire, en boucle :
  90° horaire -> 180° -> 90° contre horaire -> tout droit -> 90° horaire. Un monstre qui a reçu
  10 coups au contact ne fait plus tourner.
- « Horaire » tourne la case d'arrivée dans le sens des aiguilles d'une montre à l'écran.
- Un sort qui arrive hors de la carte ou sur une case non marchable est un échec critique ;
  au contact, l'échec critique termine le tour.
- Les invocations ne sont jamais confuses.

Le Comte (vérifié sur les captures)
- Tours impairs (Alternance, sort Fuseau) : frapper le Comte envoie l'attaquant à l'opposé, symétrique
  par rapport au Comte. S'il prend la place d'une entité, le Comte leur inflige 81 à 100 dommages Feu.
- Tours pairs (Mi-temps, sort Gousset) : c'est le Comte qui part à l'opposé, symétrique par rapport à
  l'attaquant. S'il prend la place d'une entité, celle-ci part sur l'ancienne case du Comte, et le
  Comte perd son invulnérabilité. Si l'entité est une invocation alliée, elle explose : 701 à 800
  dommages Terre aux ennemis, et les alliés dans un cercle de taille 3 autour d'elle meurent.
- Si celui qui est téléporté arrive dans le décor (hors du sol), le Comte tue toute l'équipe
  (Air du temps). Vu deux fois : l'attaquant renvoyé hors de la salle en tour impair.
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
    SWAP_FIRE = "échange avec une entité : 81 à 100 dommages Feu à vous deux"
    DELOCK = "le Comte prend la place d'une entité et perd son invulnérabilité"
    DELOCK_SUMMON = "le Comte prend la place d'une invocation et perd son invulnérabilité ; elle explose et tue les alliés à 3 cases ou moins"
    WIPE = "Air du temps : toute l'équipe meurt"


BLAST_SIZE = 3  # cercle de l'explosion d'une invocation alliée échangée avec le Comte


@dataclass(frozen=True, slots=True)
class Swap:
    mover: str  # « attaquant » (tours impairs) ou « comte » (tours pairs)
    destination: Cell
    verdict: Verdict
    blast: tuple[Cell, ...] = ()  # cases où les alliés meurent, si une invocation explose


def mirror(cell: Cell, center: Cell) -> Cell:
    return 2 * center[0] - cell[0], 2 * center[1] - cell[1]


def swap(
    layout: Layout, round_number: int, attacker: Cell, comte: Cell, occupied: frozenset[Cell] = frozenset(),
    summons: frozenset[Cell] = frozenset(),
) -> Swap:  # fmt: skip
    """Ce qui arrive si le personnage en `attacker` frappe le Comte pendant le tour `round_number`.

    occupied : cases des autres combattants ; summons : celles des invocations alliées, parmi elles."""
    odd = bool(round_number % 2)
    destination = mirror(attacker, comte) if odd else mirror(comte, attacker)
    mover = "attaquant" if odd else "comte"
    if not layout.walkable(destination):
        return Swap(mover, destination, Verdict.WIPE)
    if destination not in occupied:
        return Swap(mover, destination, Verdict.SAFE)
    if odd:
        return Swap(mover, destination, Verdict.SWAP_FIRE)
    if destination in summons:  # l'invocation arrive sur l'ancienne case du Comte et explose là
        blast = tuple(c for c in layout.cells() if abs(c[0] - comte[0]) + abs(c[1] - comte[1]) <= BLAST_SIZE and layout.walkable(c))
        return Swap(mover, destination, Verdict.DELOCK_SUMMON, blast)
    return Swap(mover, destination, Verdict.DELOCK)


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
