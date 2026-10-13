"""Décor des cartes : mise en forme et stockage, sans les fichiers du jeu."""
import sqlite3

from dofustool.staticdata import maps


def cell(number, mov=1, los=1, red=0, blue=0):
    return {"cellNumber": number, "mov": mov, "los": los, "red": red, "blue": blue, "visible": 1}


def test_pack_round_trip():
    cells = {0, 7, 8, 300, 559}
    assert len(maps.pack(cells)) == 70 and maps.unpack(maps.pack(cells)) == cells
    assert maps.unpack(maps.pack({-1, 560, 12})) == {12}  # hors de la carte : ignoré


def test_terrain_tells_floor_hole_and_wall():
    land = maps.terrain(42, [cell(10, red=1), cell(11, blue=1), cell(12, mov=0), cell(13, mov=0, los=0)])
    assert land.walkable(10) and not land.walkable(12)
    assert land.hole(12) and not land.hole(13) and not land.hole(10)
    assert land.wall(13) and not land.wall(12)
    assert (land.red, land.blue) == ({10}, {11})


def test_store_replaces_and_load_reads_back():
    conn = sqlite3.connect(":memory:")
    conn.executescript(maps.SCHEMA)
    assert maps.store(conn, [maps.terrain(1, [cell(5)]), maps.terrain(2, [cell(6, mov=0)])], "v1", now=10.0) == 2
    assert maps.store(conn, [maps.terrain(2, [cell(7, red=1)])], "v2", now=20.0) == 1
    assert maps.load(conn, 1) is None
    land = maps.load(conn, 2)
    assert (land.mov, land.red, land.blue) == ({7}, {7}, set())
    assert dict(conn.execute("SELECT key, value FROM maps_meta")) == {"game_version": "v2", "read_at": "20.0"}


def test_unity_version_is_read_from_the_game_folder(tmp_path):
    (tmp_path / "Dofus_Data").mkdir()
    (tmp_path / "Dofus_Data" / "globalgamemanagers").write_bytes(b"\x00\x01abc\x006000.3.16f1\x00zz")
    assert maps.unity_version(tmp_path) == "6000.3.16f1"
    assert maps.unity_version(tmp_path / "absent") is None
