"""Combat suivi en direct : état écrit pour l'affichage. Nombres inventés."""
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
    assert tracker.me == ME and not tracker.dirty
    assert [f["kind"] for f in state["fighters"]] == ["moi", "joueur", "monstre"] and state["fighters"][2]["dead"]
    feed(tracker, "    9.000 s2c jwp 50 | 1=3")
    assert tracker.dirty and tracker.state() == {"active": False}
    feed(tracker, "   20.000 s2c jtk 20 | 1.1=2 1.4.2=96 1.4.3.2=400 2=900")  # hors combat : rien à redessiner
    assert not tracker.dirty


def test_state_file_round_trip(tmp_path):
    path = tmp_path / "sub" / "fight.json"
    assert live.read(path) == {"active": False}
    live.write(path, {"active": True, "round": 2, "fighters": []})
    assert live.read(path)["round"] == 2 and not path.with_suffix(".tmp").exists()
    path.write_text("{pas du json", encoding="utf-8")
    assert live.read(path) == {"active": False}


def test_capture_writes_the_fight_for_the_overlay(tmp_path):
    from dofustool import db
    from dofustool.archive import Archive
    from dofustool.capture.pipeline import Pipeline

    path = tmp_path / "fight.json"
    path.write_text('{"active": true, "round": 3, "updated": 5}', encoding="utf-8")  # combat resté ouvert à l'arrêt précédent
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
    assert (state["active"], state["updated"], len(state["fighters"])) == (True, 50.0, 3)
    assert str(OTHER) not in path.read_text(encoding="utf-8")  # l'identifiant d'un autre joueur ne sort pas
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


def test_my_cell_comes_back_with_a_spell_cast_on_myself():
    tracker = live.Tracker()
    feed(tracker, START, me=ME)
    # un déplacement que la capture n'a pas su lire : la case reste l'ancienne...
    assert next(f["cell"] for f in tracker.state()["fighters"] if f["kind"] == "moi") == 300
    # ... jusqu'au prochain sort lancé sur moi (Comtoise, en début de tour), qui dit où je suis
    feed(tracker, f"    3.000 s2c jsq 69 | 2.2.1=20821 2.2.2=3646 2.3=1 2.5=342 2.6={ME} 22=300 36={ME}")
    assert next(f["cell"] for f in tracker.state()["fighters"] if f["kind"] == "moi") == 342
    feed(tracker, f"    4.000 s2c jsq 69 | 2.2.2=111 2.5=355 2.6={FOE} 22=300 36={ME}")  # sur un ennemi : c'est sa case, pas la mienne
    assert next(f["cell"] for f in tracker.state()["fighters"] if f["kind"] == "moi") == 342


def test_a_path_starting_on_a_multiple_of_8_is_followed():
    from .test_protocol import ld, vi

    tracker = live.Tracker()
    feed(tracker, START, me=ME)
    tracker.feed(5.0, "s2c", journal.MOVE, ld(1, bytes([0x90, 0x03, 0x9E, 0x03])) + vi(2, 1) + vi(4, ME))  # [400, 414]
    assert next(f["cell"] for f in tracker.state()["fighters"] if f["kind"] == "moi") == 414


def test_last_rune_of_the_huppermage():
    tracker = live.Tracker()
    feed(tracker, START, me=ME)
    # rune de feu, puis d'air : l'état « Dernière rune » posé sur moi par le jeu
    feed(tracker, f"    2.000 s2c juh 78 | 1.1.1=701 1.1.6=214000 1.1.8={ME} 1.1.16=13744 1.2=950 1.3={ME}")
    assert tracker.state()["rune"] == "feu"
    feed(tracker, f"    3.000 s2c juh 78 | 1.1.1=704 1.1.6=214003 1.1.8={ME} 1.1.16=13744 1.2=950 1.3={ME}")
    assert tracker.rune == "air"
    feed(tracker, f"    3.500 s2c juh 78 | 1.1.1=702 1.1.8={OTHER} 1.1.16=13744 1.2=950 1.3={OTHER}")  # celle d'un autre joueur
    feed(tracker, f"    3.600 s2c juh 78 | 1.1.1=999 1.1.8={ME} 1.1.16=1 1.2=950 1.3={ME}")  # un autre état
    assert tracker.rune == "air"
    feed(tracker, "    9.000 s2c jwp 50 | 1=3")
    feed(tracker, START.replace("0.300", "10.300").replace("1.000", "11.000").replace("1.100", "11.100"))
    assert tracker.rune is None  # nouveau combat : plus de dernière rune


def test_fight_left_without_its_end_is_closed():
    tracker = live.Tracker()
    feed(tracker, START, me=ME)
    feed(tracker, f"    2.000 s2c juh 78 | 1.1.1=701 1.1.8={ME} 1.1.16=13744 1.2=950 1.3={ME}")
    assert tracker.state()["rune"] == "feu"
    # pas de fin de combat vue, mais le joueur change de carte : plus de combat, plus de rune
    feed(tracker, "    5.000 s2c jow 5 | 1=212601347")
    assert tracker.dirty and tracker.state() == {"active": False} and tracker.rune is None
    # une nouvelle connexion ferme aussi un combat resté ouvert
    feed(tracker, START.replace("0.300", "10.300").replace("1.000", "11.000").replace("1.100", "11.100"))
    assert tracker.state()["active"]
    tracker.close()
    assert tracker.state() == {"active": False}
    tracker.close()  # sans combat : rien à faire
    assert not tracker.dirty


def test_my_clicks_on_cells_are_kept_for_the_overlay_grid():
    from .test_protocol import ld, vi

    tracker = live.Tracker()
    tracker.feed(1.0, "c2s", journal.ORDER, vi(1, 111) + vi(2, 355))  # hors combat : rien
    feed(tracker, START, me=ME)
    tracker.feed(5.0, "c2s", journal.ORDER, vi(1, 111) + vi(2, 355))
    tracker.feed(6.0, "c2s", journal.ORDER, vi(1, 111) + vi(2, 300))  # sur un combattant (moi) : pas pris
    # déplacement demandé : direction × 4096 + case, en varints ; le bout du chemin est la case cliquée
    path = [3 * 4096 + 400, 3 * 4096 + 427]
    raw = b"".join(bytes([v & 0x7F | 0x80, v >> 7 & 0x7F | 0x80, v >> 14]) for v in path)
    tracker.feed(7.0, "c2s", journal.MOVE_REQUEST, ld(1, raw) + vi(2, 112206595))
    assert tracker.state()["clicks"] == [(5.0, 355), (7.0, 427)]


def test_confusion_set_before_i_am_known_counts_once_i_am():
    tracker = live.Tracker()
    feed(tracker, START)  # capture lancée en pleine partie : on ne sait pas encore qui je suis
    feed(tracker, f"    2.000 s2c juh 78 | 1.1.8={ME} 1.1.10=1 1.1.16=3646 1.2=771")
    assert tracker.state()["confusion"] is None
    tracker.me = ME  # mon premier sort me fait reconnaître
    assert tracker.state()["confusion"] == 90
