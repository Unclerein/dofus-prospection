"""Combat suivi en direct : mon dernier élément lancé, état écrit pour l'affichage. Nombres inventés."""
from dofustool.fight import journal, live

ME, OTHER, FOE = 900, 700, -1


def feed(tracker, text, me=None):
    for line in text.strip().splitlines():
        when, direction, key, fields = journal.parse_line(line)
        tracker.feed_fields(when, direction, key, fields, me)


START = f"""
    0.300 s2c jwj 90 | 1.1={ME} 1.2.3=300 1.3.4.7.4.2=44 1.3.4.7.4.5.7=400 1.3.4.7.4.5.7=1000
    0.300 s2c jwj 90 | 1.1={OTHER} 1.2.3=301 1.3.4.7.4.2=44 1.3.4.7.4.5.7=400 1.3.4.7.4.5.7=900
    0.300 s2c jwj 90 | 1.1={FOE} 1.2.3=310 1.3.4.6.3.3=489 1.3.4.7.4.2=44 1.3.4.7.4.3.2=160 1.3.4.7.4.3.2=80
    1.000 s2c jxr 0 |
    1.100 s2c jvj 15 | 2=430 3={ME} 4=150 7=1
"""


def test_last_element_follows_my_own_casts_only():
    tracker = live.Tracker()
    feed(tracker, START, me=ME)
    assert tracker.state() == {"active": True, "round": 1, "element": None, "fighters": [
        {"kind": "moi", "monster": None, "life": 1000, "max_life": 1000, "shield": 0, "dead": False, "cell": 300, "mine": False},
        {"kind": "joueur", "monster": None, "life": 900, "max_life": 900, "shield": 0, "dead": False, "cell": 301, "mine": False},
        {"kind": "monstre", "monster": 489, "life": 80, "max_life": 80, "shield": 0, "dead": False, "cell": 310, "mine": False},
    ], "map": None, "confusion": None}
    feed(tracker, f"""
    2.000 c2s jsr 7 | 1=111 2=310
    2.100 s2c jsq 40 | 2.2.2=111 2.5=310 2.6={FOE} 22=300 36={ME}
    2.100 s2c jsq 30 | 22=99 36={ME} 37.4=2 37.5={FOE} 37.6=20
    """)
    assert tracker.element == "feu" and tracker.known == {111: "feu"} and tracker.learnt and tracker.dirty
    # un effet à moi qui se relance seul, le sort d'un autre joueur, un coup reçu : rien ne change
    feed(tracker, f"""
    2.200 s2c jsq 40 | 2.2.2=8395 2.5=300 2.6={ME} 22=300 36={ME}
    2.300 s2c jsq 30 | 22=98 36={ME} 37.4=4 37.5={FOE} 37.6=5
    3.000 s2c jsq 40 | 2.2.2=222 2.5=310 2.6={FOE} 22=300 36={OTHER}
    3.000 s2c jsq 30 | 22=96 36={OTHER} 37.4=3 37.5={FOE} 37.6=9
    3.500 s2c jsq 30 | 22=100 36={FOE} 37.5={ME} 37.6=12
    """)
    assert tracker.element == "feu" and tracker.known == {111: "feu"}
    # un sort déjà connu, lancé dans le vide : pas de ligne de dégâts, l'élément est su quand même
    tracker.known[333] = "eau"
    feed(tracker, f"""
    4.000 c2s jsr 7 | 1=333 2=250
    4.100 s2c jsq 40 | 2.2.2=333 2.5=250 22=300 36={ME}
    """)
    assert tracker.element == "eau"


def test_poison_teaches_its_element_without_becoming_the_last_cast():
    tracker = live.Tracker()
    feed(tracker, START, me=ME)
    feed(tracker, f"""
    2.000 c2s jsr 7 | 1=111 2=310
    2.100 s2c jsq 40 | 2.2.2=111 2.5=310 2.6={FOE} 22=300 36={ME}
    2.100 s2c jsq 30 | 22=97 36={ME} 37.4=1 37.5={FOE} 37.6=20
    5.000 s2c jsq 27 | 22=3793 25.1=444 25.5=310 36={ME}
    5.000 s2c jsq 30 | 22=98 36={ME} 37.4=4 37.5={FOE} 37.6=30
    """)
    assert tracker.element == "terre" and tracker.known == {111: "terre", 444: "air"}


def test_my_fighter_is_guessed_from_what_i_ask_for_and_state_ends_with_the_fight():
    tracker = live.Tracker()
    feed(tracker, START)  # personnage inconnu de la capture
    feed(tracker, f"""
    2.000 c2s jsr 7 | 1=111 2=310
    2.100 s2c jsq 40 | 2.2.2=111 2.5=310 2.6={FOE} 22=300 36={ME}
    2.100 s2c jsq 30 | 22=96 36={ME} 37.4=3 37.5={FOE} 37.6=80
    2.100 s2c jsq 24 | 3.2={FOE} 22=103 36={ME}
    """)
    state = tracker.state()
    assert tracker.me == ME and state["element"] == "eau" and not tracker.dirty
    assert [f["kind"] for f in state["fighters"]] == ["moi", "joueur", "monstre"] and state["fighters"][2]["dead"]
    feed(tracker, "    9.000 s2c jwp 50 | 1=3")
    assert tracker.dirty and tracker.state() == {"active": False}
    feed(tracker, "   20.000 s2c jtk 20 | 1.1=2 1.4.2=96 1.4.3.2=400 2=900")  # hors combat : rien à redessiner
    assert not tracker.dirty


def test_state_file_round_trip(tmp_path):
    path = tmp_path / "sub" / "fight.json"
    assert live.read(path) == {"active": False}
    live.write(path, {"active": True, "round": 2, "element": "air", "fighters": []})
    assert live.read(path)["element"] == "air" and not path.with_suffix(".tmp").exists()
    path.write_text("{pas du json", encoding="utf-8")
    assert live.read(path) == {"active": False}
    spells = tmp_path / "spells.json"
    live.write(spells, {"111": "feu", "222": "n'importe quoi"})
    assert live.load_known(spells) == {111: "feu"} and live.load_known(tmp_path / "absent.json") == {}


def test_capture_writes_the_fight_for_the_overlay(tmp_path):
    from dofustool import db
    from dofustool.archive import Archive
    from dofustool.capture.pipeline import Pipeline

    path = tmp_path / "fight.json"
    path.write_text('{"active": true, "element": "feu", "updated": 5}', encoding="utf-8")  # combat resté ouvert à l'arrêt précédent
    pipeline = Pipeline(Archive(":memory:"), db.connect(":memory:"), {}, fight_path=path)
    assert live.read(path)["active"] is False
    feed(pipeline.fight, START, me=ME)
    feed(pipeline.fight, f"""
    2.000 c2s jsr 7 | 1=111 2=310
    2.100 s2c jsq 40 | 2.2.2=111 2.5=310 2.6={FOE} 22=300 36={ME}
    2.100 s2c jsq 30 | 22=98 36={ME} 37.4=4 37.5={FOE} 37.6=20
    """)
    pipeline.tick(50.0)
    state = live.read(path)
    assert (state["active"], state["element"], state["updated"], len(state["fighters"])) == (True, "air", 50.0, 3)
    assert str(OTHER) not in path.read_text(encoding="utf-8")  # l'identifiant d'un autre joueur ne sort pas
    assert live.load_known(tmp_path / live.SPELLS_PATH.name) == {111: "air"}
    # sans chemin, rien n'est suivi ni écrit
    assert Pipeline(Archive(":memory:"), db.connect(":memory:"), {}).fight is None


def test_confusion_of_the_comte_fight():
    tracker = live.Tracker()
    feed(tracker, "    0.100 s2c jow 5 | 1=112206595", me=ME)
    feed(tracker, START)
    # Comtoise : 2 fois un quart de tour contre horaire, soit 180° ; puis Pendultimatum : +90° horaire
    feed(tracker, f"""
    1.200 s2c juh 70 | 1.1.6=114318 1.1.8={ME} 1.1.10=2 1.1.16=3646 1.2=774 1.3={ME}
    """)
    assert tracker.state()["confusion"] == 180 and tracker.state()["map"] == 112206595
    feed(tracker, f"    2.000 s2c juh 70 | 1.1.6=113833 1.1.8={ME} 1.1.10=1 1.1.16=3643 1.2=771 1.3=-5")
    assert tracker.confusion == 270
    feed(tracker, f"    2.100 s2c juh 70 | 1.1.8={OTHER} 1.1.10=1 1.1.16=3643 1.2=771 1.3=-5")  # sur l'allié : pas pour moi
    assert tracker.confusion == 270
    # nouveau tour : la Comtoise remplace (2 huitièmes de tour contre horaire = 90° contre horaire)
    feed(tracker, f"    5.000 s2c juh 70 | 1.1.6=113842 1.1.8={ME} 1.1.10=2 1.1.16=3646 1.2=775 1.3={ME}")
    assert tracker.confusion == 270
    feed(tracker, f"    6.000 s2c juh 70 | 1.1.6=113838 1.1.8={ME} 1.1.10=90 1.1.16=3646 1.2=770 1.3={ME}")
    assert tracker.confusion == 90
    feed(tracker, "    9.000 s2c jwp 50 | 1=3")
    feed(tracker, f"   10.000 s2c juh 70 | 1.1.8={ME} 1.1.10=1 1.1.16=3643 1.2=771 1.3=-5")  # hors combat
    assert tracker.state() == {"active": False}
