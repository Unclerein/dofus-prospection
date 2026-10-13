"""Sol des cartes de combat appris des combats, avec des nombres inventés."""
import sqlite3

from dofustool.fight import journal, terrain

LINES = """
    0.100 s2c jow 5 | 1=111
    0.200 s2c jnz 25 | 1=[10,24] 2=1 4=900
    1.000 s2c jow 5 | 1=222
    1.100 s2c jxk 54 | 1.1=[310,352] 1.2=[300,257]
    1.200 s2c jwj 90 | 1.1=-1 1.2.3=311
    1.300 s2c jxr 0 | 
    2.000 s2c jnz 25 | 1=[300,314,329] 2=1 4=900
    2.100 s2c jsq 30 | 5.1=-1 5.2=311 5.3=339 22=5 36=900
    2.200 s2c jsq 99 | 12.2.1.2.3=900 12.2.1.5.3.1=-2 12.2.1.5.3.2.3=231 22=181 36=900
    2.300 s2c jsq 40 | 2.2.2=111 2.5=45 2.6=-1 22=300 36=900
    3.000 s2c jwp 50 | 1=3
    3.100 s2c jnz 25 | 1=[400,414] 2=1 4=900
"""


def test_only_cells_stood_on_during_the_fight_are_learnt():
    seen = terrain.learn(m for m in map(journal.parse_line, LINES.splitlines()) if m)
    # ni la carte du monde, ni la case visée par un sort, ni ce qui suit la fin du combat
    assert seen == {222: {310, 352, 300, 257, 311, 314, 329, 339, 231}}


def test_store_adds_each_cell_once():
    conn = sqlite3.connect(":memory:")
    assert terrain.store(conn, {222: {300, 314}}) == 2
    assert terrain.store(conn, {222: {314, 329}, 333: {5}}) == 2
    assert terrain.walkable(conn, 222) == {300, 314, 329} and terrain.walkable(conn, 999) == set()
