import json

import pytest

from dofustool.fight import grid, harebourg as hb
from dofustool.fight.grid import Kind, Placement
from dofustool.fight.harebourg import Rotation, Verdict

# Petite arène pour des cas lisibles : un obstacle en (3, 1), un trou en (0, 4).
SMALL = grid.parse_text(
    "test",
    """
.....
...#.
.....
.....
-....
""",
)


# --- numéros de case Dofus -------------------------------------------------------


def test_cell_ids_round_trip():
    assert grid.cell_to_xy(0) == (0, 0)
    assert grid.cell_to_xy(13) == (13, 13)
    assert grid.cell_to_xy(14) == (1, 0)  # ligne décalée : une demi-case plus bas à droite
    assert grid.cell_to_xy(28) == (1, -1)
    assert grid.cell_to_xy(559) == (33, -6)
    assert all(grid.xy_to_cell(*grid.cell_to_xy(i)) == i for i in range(grid.CELL_COUNT))
    assert grid.xy_to_cell(0, 1) is None
    with pytest.raises(ValueError):
        grid.cell_to_xy(560)


def test_placement_and_fit():
    layout = grid.load("comte")
    true = Placement(6, 8)  # l'arène remplit presque toute la carte : seuls 14 placements tiennent
    cells = [true.to_cell(c) for c in layout.cells(Kind.WALKABLE)]
    assert None not in cells  # l'arène tient dans la carte à cette position
    assert all(true.to_grid(cell_id) == c for cell_id, c in zip(cells, layout.cells(Kind.WALKABLE)))
    # Avec assez de positions observées, il ne reste que le bon placement.
    assert grid.fit_offset(layout, cells) == [true]
    assert true in grid.fit_offset(layout, cells[:3])


# --- dispositions ------------------------------------------------------------------


def test_comte_layout():
    layout = grid.load("comte")
    assert (layout.width, layout.height) == (22, 22)
    assert len(layout.cells(Kind.WALKABLE)) > 250 and len(layout.cells(Kind.WALL)) > 10
    assert grid.parse_text("x", layout.to_text()) == grid.Layout("x", layout.rows)


def test_simulator_export():
    text = json.dumps({"size": [2, 4], "details": [[0, 1, 3, 1], [1, 0, 0, 2]]})
    layout = grid.parse_simulator("sim", text)
    assert layout.to_text() == "-...\n#---\n"
    with pytest.raises(ValueError):
        grid.parse_simulator("sim", json.dumps({"size": [1, 1], "details": [[0, 0, 0, 7]]}))


# --- confusion ---------------------------------------------------------------------


def test_rotation_cycle_and_inverse():
    assert [r.bumped() for r in Rotation] == [Rotation.CLOCKWISE, Rotation.HALF, Rotation.COUNTERCLOCKWISE, Rotation.STRAIGHT]
    assert Rotation.CLOCKWISE.inverse is Rotation.COUNTERCLOCKWISE and Rotation.HALF.inverse is Rotation.HALF
    for r in Rotation:
        for offset in [(2, 0), (1, 3), (-2, 5)]:
            assert hb.rotate(hb.rotate(offset, r), r.inverse) == offset


def test_clockwise_is_clockwise_on_screen():
    # À l'écran : x = col - row, y = col + row (y vers le bas). Le haut de l'écran est (-1, -1),
    # la gauche (-1, 1), la droite (1, -1), le bas (1, 1).
    up, right, down, left = (-1, -1), (1, -1), (1, 1), (-1, 1)
    assert hb.rotate(up, Rotation.CLOCKWISE) == right
    assert hb.rotate(right, Rotation.CLOCKWISE) == down
    # Vérifié en jeu par harebourg-ux : en 90° horaire, pour un monstre juste au-dessus,
    # on clique à gauche du personnage.
    me = (10, 10)
    target = (me[0] + up[0], me[1] + up[1])
    assert hb.aim(me, target, Rotation.CLOCKWISE) == (me[0] + left[0], me[1] + left[1])
    assert hb.aim(me, target, Rotation.HALF) == (me[0] + down[0], me[1] + down[1])
    assert hb.landing(me, hb.aim(me, (13, 7), Rotation.COUNTERCLOCKWISE), Rotation.COUNTERCLOCKWISE) == (13, 7)


def test_rotation_for_life():
    expected = {100: Rotation.CLOCKWISE, 90: Rotation.CLOCKWISE, 89: Rotation.COUNTERCLOCKWISE, 75: Rotation.COUNTERCLOCKWISE,
                74: Rotation.HALF, 45: Rotation.HALF, 44: Rotation.COUNTERCLOCKWISE, 30: Rotation.COUNTERCLOCKWISE,
                29: Rotation.CLOCKWISE, 1: Rotation.CLOCKWISE}  # fmt: skip
    assert {pct: hb.rotation_for_life(pct) for pct in expected} == expected


def test_shots():
    me = (1, 1)
    # Viser l'obstacle : on peut cliquer, mais le sort échoue en critique.
    s = hb.shot_at(SMALL, me, (3, 1), Rotation.HALF)
    assert (s.aim, s.clickable, s.critical_failure, s.melee) == ((-1, 1), False, True, False)
    s = hb.shot_at(SMALL, me, (2, 1), Rotation.CLOCKWISE)
    assert (s.aim, s.clickable, s.critical_failure, s.melee) == ((1, 0), True, False, True)
    s = hb.cast_on(SMALL, (1, 3), (2, 3), Rotation.CLOCKWISE)
    assert (s.landing, s.critical_failure) == ((1, 4), False)
    s = hb.cast_on(SMALL, (1, 3), (1, 2), Rotation.COUNTERCLOCKWISE)
    assert (s.landing, s.critical_failure) == ((0, 3), False)
    s = hb.cast_on(SMALL, (1, 4), (2, 4), Rotation.HALF)
    assert (s.landing, s.critical_failure) == ((0, 4), True)  # le trou


# --- symétries et Air du Temps ------------------------------------------------------


def test_swap_by_round_parity():
    comte = (2, 2)
    odd = hb.swap(SMALL, 1, (2, 3), comte)
    assert (odd.mover, odd.destination, odd.verdict) == ("attaquant", (2, 1), Verdict.SAFE)
    even = hb.swap(SMALL, 2, (2, 3), comte)
    assert (even.mover, even.destination, even.verdict) == ("comte", (2, 4), Verdict.SAFE)
    # Tour pair : le Comte envoyé sur l'obstacle lance Air du Temps.
    assert hb.swap(SMALL, 2, (3, 2), (3, 3)).verdict is Verdict.WIPE
    assert hb.swap(SMALL, 4, (0, 3), (0, 2)).verdict is Verdict.WIPE  # dans le trou
    assert hb.swap(SMALL, 2, (4, 2), (2, 2)).verdict is Verdict.WIPE  # hors de l'arène
    # Tour impair : c'est l'attaquant qui part ; destination impossible signalée, effet à confirmer.
    assert hb.swap(SMALL, 3, (1, 2), (0, 2)).verdict is Verdict.RISKY
    assert hb.swap(SMALL, 2, (2, 3), comte, frozenset({(2, 4)})).verdict is Verdict.OCCUPIED


def test_swap_map_and_mi_temps():
    comte = (2, 2)
    plan = hb.swap_map(SMALL, 2, comte, frozenset({(0, 0)}))
    assert comte not in plan and (0, 0) not in plan and (3, 1) not in plan
    assert plan[(3, 2)].verdict is Verdict.SAFE and plan[(4, 2)].verdict is Verdict.WIPE  # (6, 2) : hors de l'arène
    assert plan[(2, 1)].destination == (2, 0)
    assert sorted(hb.mi_temps(SMALL, comte)) == sorted(
        [(2, 2), (3, 2), (4, 2), (1, 2), (0, 2), (2, 3), (2, 4), (2, 1), (2, 0)]
    )
