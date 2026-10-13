"""Journal de combat : lu depuis des lignes d'extrait, avec des nombres inventés."""
from dofustool.fight import journal

ME, FOE, PET = 900, -1, -2

LINES = f"""# en-tête
    0.200 s2c kqo 9 | 1=1500 3=1400 4=7
    0.300 s2c jwj 90 | 1.1={ME} 1.2.3=300 1.3.4.7.4.2=1 1.3.4.7.4.1.1=6 1.3.4.7.4.2=44 1.3.4.7.4.5.7=400 1.3.4.7.4.5.7=500 1.3.4.7.4.2=11 1.3.4.7.4.5.3=1 1.3.4.7.4.5.5=990 1.3.4.7.4.5.7=9 1.3.4.7.4.2=95 1.3.4.7.4.5=LEN0
    0.300 s2c jwj 90 | 1.1={FOE} 1.2.3=310 1.3.4.6.3.3=489 1.3.4.7.4.2=44 1.3.4.7.4.3.2=160 1.3.4.7.4.3.2=80 1.3.4.7.4.2=11 1.3.4.7.4.3=LEN0 1.3.4.7.4.2=120 1.3.4.7.4.3.2=100
    1.000 s2c jxr 0 | 
    1.100 s2c jvj 15 | 2=430 3={ME} 4=150 7=1
    2.000 c2s jsr 7 | 1=111 2=203
    2.100 s2c jsq 40 | 2.2.1=5 2.2.2=111 2.5=203 2.6={FOE} 22=300 36={ME}
    2.100 s2c jsq 20 | 13.2=-4 13.3={ME} 22=102 36={ME}
    2.100 s2c jsq 30 | 22=93 36={ME} 37.1=5 37.4=4 37.5={FOE} 37.6=50
    2.100 s2c jsq 24 | 22=2999 36={ME} 39.2={ME} 39.4=25
    3.000 s2c jsq 99 | 12.2.1.1.3.5=77 12.2.1.2.3={ME} 12.2.1.2.4.2=44 12.2.1.2.4.3.2=16 12.2.1.2.4.3.2=120 12.2.1.2.4.2=11 12.2.1.2.4.3.2=30 12.2.1.5.3.1={PET} 12.2.1.5.3.2.3=231 22=181 36={ME}
    4.000 s2c jvj 18 | 2=290 3={FOE} 6=1 7=1
    4.500 s2c jsq 30 | 22=100 36={FOE} 37.5={ME} 37.6=12
    4.600 s2c jtk 20 | 1.1=2 1.4.2=96 1.4.3.2=400 2={ME}
    4.600 s2c jtk 20 | 1.1=2 1.4.2=1 1.4.3.2=9 2={ME}
    4.700 s2c jtk 20 | 1.1=2 1.4.2=96 1.4.3.2=391 2={ME}
    4.700 s2c jsq 27 | 22=100 36={FOE} 37.2=9 37.5={ME}
    4.800 s2c jsq 27 | 22=3793 25.1=222 25.2=3 25.5=310 36={ME}
    5.000 s2c jvj 15 | 2=430 3={ME} 4=150 7=2
    6.000 s2c jsq 30 | 5.1={FOE} 5.2=100 5.3=128 22=5 36={PET}
    6.000 s2c jsq 30 | 22=80 36={PET} 37.4=-1 37.5={FOE} 37.6=30
    6.100 s2c jsq 24 | 3.2={FOE} 22=103 36={PET}
    7.000 s2c jwp 50 | 1=3
    8.000 s2c jsq 30 | 22=100 36={FOE} 37.5={ME} 37.6=999
"""


def test_parse_line():
    assert journal.parse_line("# en-tête") is None
    when, direction, key, fields = journal.parse_line("    4.500 s2c jsq 30 | 22=100 37.6=12 9=LEN4")
    assert (when, direction, key) == (4.5, "s2c", "jsq")
    assert journal.number(fields, "37.6") == 12 and journal.number(fields, "9") is None and journal.number(fields, "1") is None


def test_fight_is_read_turn_by_turn():
    (fight,) = journal.read_extract(LINES)
    assert fight.ended and fight.rounds == 2
    kinds = [e.kind for e in fight.events]
    assert kinds == ["sheet", "sheet", "start", "turn", "cast", "spent", "damage", "heal", "summon", "turn", "damage", "shield", "shield", "damage", "trigger", "turn", "push", "damage", "death", "end"]
    cast = fight.events[4]
    assert (cast.source, cast.target, cast.spell, cast.cell) == (ME, FOE, 111, 203)
    assert [e.element for e in fight.events if e.kind == "damage"] == ["air", "neutre", "neutre", "poussée"]
    me, foe, pet = fight.fighters[ME], fight.fighters[FOE], fight.fighters[PET]
    assert (me.dealt, me.taken, me.healed, me.dead) == (50, 12, 25, False)  # le coup reçu après la fin ne compte pas
    assert (me.absorbed, me.shield) == (9, 391)  # le coup pris par le bouclier ne touche pas aux points de vie
    assert (foe.taken, foe.dead) == (80, True)
    assert pet.summoner == ME and pet.dealt == 30
    assert (me.cell, foe.cell, pet.cell) == (300, 128, 231)  # placement, case d'arrivée de la poussée, case d'invocation
    # vie de base + vitalité ; la mienne part de ce que j'avais en entrant, pas du maximum
    assert (me.max_life, me.start_life, me.life) == (1500, 1400, 1413)
    assert (foe.monster, foe.max_life, foe.life) == (489, 80, 0)
    assert (pet.max_life, pet.life) == (150, 150)


def test_nothing_outside_a_fight():
    assert journal.read_extract("    1.000 s2c jsq 30 | 22=100 36=-1 37.5=900 37.6=12\n") == []
