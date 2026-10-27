"""Aide à la visée du Comte : géométrie écran <-> cases et plan affiché au survol."""
import pytest

from dofustool.fight import grid
from dofustool.fight.harebourg import Verdict
from dofustool.overlay import aim

FRAME = aim.Frame(ox=40.0, oy=500.0, width=86.0)
LAYOUT = grid.load("comte")


def cell(col: int, row: int) -> int:
    """Case Dofus d'une case de la salle dessinée (colonne, rangée)."""
    return aim._to_cell((col, row))


def test_every_cell_centre_maps_back_to_its_cell():
    for c in (0, 13, 14, 27, 300, 559):
        assert FRAME.cell_at(*FRAME.centre(c)) == c
    # près du bord d'un losange, on reste dans la case ; un peu au-delà, on passe à la voisine
    cx, cy = FRAME.centre(300)
    assert FRAME.cell_at(cx + 40, cy) == 300 and FRAME.cell_at(cx + 46, cy) != 300
    assert FRAME.cell_at(-5000, -5000) is None
    assert len(FRAME.diamond(300)) == 4


def test_calibration_from_two_or_three_hovered_cells():
    points = [(c, *FRAME.centre(c)) for c in (260, 341)]
    found, error = aim.calibrate(points)
    assert error < 1e-6 and abs(found.width - 86) < 1e-6 and abs(found.ox - 40) < 1e-6
    noisy = [(c, x + dx, y) for (c, x, y), dx in zip(points + [(402, *FRAME.centre(402))], (3, -2, 1))]
    found, error = aim.calibrate(noisy)
    assert error < aim.MAX_RESIDUAL and abs(found.width - 86) < 2
    with pytest.raises(ValueError):
        aim.calibrate([(260, 1.0, 2.0), (260, 1.0, 2.0)])


def test_calibration_file(tmp_path):
    path = tmp_path / "calib.json"
    assert aim.load_frame((1920, 1080), path) is None
    aim.save_frame((1920, 1080), FRAME, path)
    aim.save_frame((1280, 720), aim.Frame(1, 2, 3), path)
    assert aim.load_frame((1920, 1080), path) == FRAME and aim.load_frame((800, 600), path) is None
    aim.save_frame((1920, 1080), aim.Frame(0, 0, 0), path)  # calibrage effacé (recommencé)
    assert aim.load_frame((1920, 1080), path) is None


def state(me, comte, confusion, round_number, others=()):
    fighters = [{"kind": "moi", "cell": me}, {"kind": "monstre", "monster": aim.COMTE_MONSTER, "cell": comte}, *others]
    return {"active": True, "map": aim.COMTE_MAP, "confusion": confusion, "round": round_number, "fighters": fighters}


def test_delock_with_my_double_as_on_the_winning_try():
    # Tour 18 de l'essai gagné : moi en (11,13), le Comte 4 cases d'un côté, mon double 4 cases de l'autre, confusion 180°.
    me, comte, double = cell(11, 13), cell(15, 13), cell(7, 13)
    s = state(me, comte, 180, 18, [{"kind": "invocation", "mine": True, "cell": double}])
    p = aim.plan(s, comte, LAYOUT)
    assert p.aim == double and p.aim_ok  # je vise la case du double pour toucher le Comte
    assert (p.landing, p.mover, p.verdict) == (double, "le Comte", Verdict.DELOCK_SUMMON)
    assert comte in p.blast and not p.me_in_blast  # à 4 cases, je survis à l'explosion


def test_odd_round_sends_me_out_of_the_room():
    me, comte = cell(2, 12), cell(1, 12)  # le Comte collé au bord gauche : je partirais dans le décor
    p = aim.plan(state(me, comte, 0, 5), comte, LAYOUT)
    assert (p.mover, p.verdict) == ("toi", Verdict.WIPE)
    safe = aim.plan(state(cell(10, 12), cell(11, 12), 90, 5), cell(11, 12), LAYOUT)
    assert (safe.mover, safe.verdict, safe.landing) == ("toi", Verdict.SAFE, cell(12, 12))


def test_plain_cell_and_when_nothing_is_shown():
    me = cell(10, 12)
    p = aim.plan(state(me, cell(15, 4), 90, 2), cell(12, 12), LAYOUT)
    assert p.landing is None and p.verdict is None and p.aim is not None
    assert aim.plan(state(me, cell(15, 4), 90, 2), me, LAYOUT) is None  # je me survole moi-même
    assert aim.plan({**state(me, cell(15, 4), None, 2)}, cell(12, 12), LAYOUT) is None  # pas de confusion
    assert aim.plan({**state(me, cell(15, 4), 90, 2), "map": 1}, cell(12, 12), LAYOUT) is None  # autre carte
    assert aim.plan({"active": False}, cell(12, 12), LAYOUT) is None
