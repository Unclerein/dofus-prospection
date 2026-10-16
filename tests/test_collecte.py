"""Collecte de forgemagie : cases, points, missions, règles, et ce que la fenêtre d'affichage annonce."""
import json

from dofustool import db
from dofustool.analysis import collecte
from dofustool.overlay import Forge

DENSITY = {118: 1.0, 125: 0.2}


def forge(passes, start=1000.0, character=None):
    """Une base avec un objet (Force 30–50, Vitalité 100–200) et une rune de Force +1 ; passes : (puits avant, force avant, passé)."""
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO items VALUES (10, 'Geta', 200, 'Bottes', 1, 1, 0, 0)")
    conn.execute("INSERT INTO items VALUES (20, 'Rune Fo', 1, 'Rune de forgemagie', 1, 1, 0, 2)")
    conn.executemany("INSERT INTO item_effects VALUES (?, ?, ?, ?)", [(10, 118, 30, 50), (10, 125, 100, 200), (20, 118, 1, 1)])
    for i, (pool, strength, passed) in enumerate(passes):
        before, after = [[118, strength], [125, 150]], [[118, strength + passed], [125, 150]]
        conn.execute(
            "INSERT INTO fm_passes (source_id, ts, uid, item_id, rune_id, passed, lost, pool, pool_change, kind, pool_before, before, after, character_id) "
            "VALUES (?, ?, 7, 10, 20, ?, 0, ?, 0, 'rune', ?, ?, ?, ?)",
            (i, start + i, passed, pool, pool, json.dumps(before), json.dumps(after), character),
        )
    conn.commit()
    return conn


def test_a_pass_is_worth_more_where_the_group_has_little():
    assert collecte.value(0, 600) == 10 and collecte.value(600, 600) == 1 and collecte.value(900, 600) == 1
    state = collecte.compute(forge([(0.0, 40, 1)] * 3), DENSITY)
    # Trois passages dans la même case presque vide : un peu moins de 10 points chacun.
    assert 29.9 <= state.points <= 30 and state.passes == 3
    cell = state.blocks[0]
    assert cell.id == "normal" and sum(cell.counts.values()) == 3 and next(iter(cell.counts)).endswith("|sans")


def test_a_mission_is_won_on_the_pass_that_completes_it_and_pays_its_bonus(tmp_path):
    conn = forge([(0.0, 40, 1)] * 300, character=5)
    conn.execute("INSERT INTO characters VALUES (5, 'Forgeronne', 200, 1.0)")
    state = collecte.compute(conn, DENSITY)
    assert state.done["sans-puits"] == (1299.0, 5)
    sans = next(m for m in collecte.MISSIONS if m.id == "sans-puits")
    assert state.points > sans.bonus + 300  # 300 passages, au moins 1 point chacun, et le bonus
    focus = tmp_path / "collecte.json"
    assert collecte.remember(conn, state, now=2000.0, focus_path=focus) == list(state.done)
    assert collecte.remember(conn, state, now=2001.0) == []  # déjà inscrite
    shown = json.loads(focus.read_text(encoding="utf-8"))["mission"]
    assert shown["id"] not in state.done and shown["worth"] == 10.0
    score = collecte.score(conn, state)
    assert score["character"] == "Forgeronne" and score["missions"] == [[m, round(at, 1)] for m, (at, _) in sorted(state.done.items(), key=lambda kv: kv[1][0])]


def test_rules_follow_the_evidence():
    conn = forge([(0.0, 40, 1)] * 5)
    state = collecte.compute(conn, DENSITY)
    rules = {r["id"]: r for r in collecte.rules(conn, state, now=100.0)}
    # Validées par le groupe : certaines tant qu'aucun passage ne les contredit.
    assert {rules[k]["status"] for k in ("outils-surs", "plafond-101", "niveau-metier")} == {"certain"}
    assert rules["seuil-25"]["status"] == "verifier" and rules["ligne-101"]["status"] == "verifier"
    assert rules["ligne-101"]["was"] is None  # premier calcul : pas une nouveauté
    # 20 refus du jeu sur un over qui aurait mis la ligne au-delà de 101 de poids : la règle devient très probable.
    conn.executemany("INSERT INTO fm_refusals VALUES (?, ?, 7, 10, 20, 0, NULL, '[[118, 101]]')", [(500 + i, 3000.0 + i) for i in range(20)])
    rules = {r["id"]: r for r in collecte.rules(conn, collecte.compute(conn, DENSITY), now=200.0)}
    assert rules["ligne-101"]["status"] == "probable" and rules["ligne-101"]["was"] == "À vérifier"
    # Un refus alors que la ligne serait restée sous 101 la contredirait.
    conn.execute("INSERT INTO fm_refusals VALUES (900, 4000.0, 7, 10, 20, 0, NULL, '[[118, 50]]')")
    rules = {r["id"]: r for r in collecte.rules(conn, collecte.compute(conn, DENSITY), now=300.0)}
    assert rules["ligne-101"]["status"] == "refute"
    # Les orbes comptent dans leur case.
    conn.executemany(
        "INSERT INTO fm_passes (source_id, ts, uid, item_id, rune_id, passed, lost, pool, pool_change, kind, before, after) "
        "VALUES (?, ?, 7, 10, 20, 1, 0, 0, 0, 'orbe', '[]', '[]')",
        [(100 + i, 2000.0 + i) for i in range(40)],
    )
    state = collecte.compute(conn, DENSITY)
    assert next(b for b in state.blocks if b.id == "orbs").counts["orbe"] == 40


def test_the_overlay_recalls_the_mission_once_per_workshop_and_announces_fresh_successes(tmp_path):
    conn = forge([])
    conn.execute("INSERT INTO characters VALUES (5, 'Forgeronne', 200, 1.0)")
    conn.execute("INSERT INTO fm_missions_done VALUES ('orbes', 10.0, 5)")  # d'avant le lancement : jamais annoncée
    conn.commit()
    placed, focus = tmp_path / "forge.json", tmp_path / "collecte.json"
    focus.write_text(json.dumps({"mission": {"id": "orbes", "title": "Le hasard des orbes", "text": "", "worth": 10.0, "bonus": 200, "count": 0, "target": 30}}), encoding="utf-8")
    board = Forge(conn, placed, focus, {"orbes": ("Le hasard des orbes", 200), "duel-crit": ("Le duel des critiques", 500)}, 1000.0)
    assert board.mission(1000.0) is None and board.successes(1000.0, "Lou") == []

    placed.write_text(json.dumps({"placed": 1005.0, "item_id": 10}), encoding="utf-8")
    assert board.mission(1010.0)["title"] == "Le hasard des orbes"
    placed.write_text(json.dumps({"placed": 1100.0, "item_id": 10}), encoding="utf-8")
    assert board.mission(1101.0) is None  # même séance d'atelier

    conn.execute("INSERT INTO fm_missions_done VALUES ('duel-crit', 1150.0, 5)")
    conn.execute("INSERT INTO shared_fm_scores VALUES ('xilef', 1160.0, 'Hupper', 10.0, 3, 0, ?)", (json.dumps([["orbes", 1155.0]]),))
    conn.commit()
    assert board.successes(1200.0, "Lou") == [("Forgeronne", "Le duel des critiques", 500), ("Hupper", "Le hasard des orbes", 200)]
    assert board.successes(1201.0, "Lou") == []


def test_overs_are_ranked_on_their_line():
    rows = collecte.compute(forge([(5.0, 49, 1), (5.0, 50, 1), (5.0, 51, 0), (5.0, 52, 1)]), DENSITY).rows
    assert [collecte.over_rank(r) for r in rows] == [None, 1, 2, 3]  # ligne max 50, rune +1
    assert "r|1" in collecte.cells_of(rows[1]) and "r|2" in collecte.cells_of(rows[2])
    assert collecte.other_extra(rows[2]) == 0  # le point déjà au-delà du max est sur la ligne visée


def test_the_workshop_banner_picks_the_mission_that_suits_the_placed_item(tmp_path):
    conn = forge([])  # la Geta : Force 30–50, Vitalité 100–200, poids max 90 : un objet léger
    placed, focus = tmp_path / "forge.json", tmp_path / "collecte.json"
    missions = [{"id": i, "title": i, "text": "", "worth": w, "bonus": 100, "count": 0, "target": 10}
                for i, w in (("au-dela-25", 10.0), ("orbes", 9.9), ("poids-plume", 9.0), ("duel-crit", 8.0))]
    focus.write_text(json.dumps({"mission": missions[0], "missions": missions}), encoding="utf-8")
    board = Forge(conn, placed, focus, {}, 1000.0, DENSITY)
    # Objet léger, sans over : « Au-delà de 25 » ne va pas avec lui, « Poids plume » si.
    placed.write_text(json.dumps({"placed": 1001.0, "item_id": 10, "effects": [[118, 45], [125, 150]]}), encoding="utf-8")
    assert board.mission(1002.0)["id"] == "poids-plume"
    # Objet inconnu de la base : la plus rentable de celles qui ne dépendent pas de l'objet.
    placed.write_text(json.dumps({"placed": 1200.0, "item_id": 99, "effects": []}), encoding="utf-8")
    assert board.mission(1201.0)["id"] == "orbes"
    assert collecte.pick(missions, {}) == missions[0]  # sans rien savoir de l'objet : la mission du moment


def test_a_pass_whose_sheets_are_missing_waits_instead_of_counting_as_an_exo():
    from dofustool.staticdata import effects as base_effects

    conn = forge([(0.0, 40, 1)])
    conn.execute("DELETE FROM item_effects WHERE item_id = 20")  # la fiche de la rune n'est pas encore arrivée
    state = collecte.compute(conn, DENSITY)
    assert state.rows[0].line_kind == "?" and collecte.cells_of(state.rows[0]) == []
    assert sum(sum(b.counts.values()) for b in state.blocks) == 0
    # L'objet et la rune du passage sont à demander à DofusDB, comme les objets vus à l'HDV.
    assert base_effects.forged_items(conn) == [10, 20]
    conn.execute("INSERT INTO item_effects_fetched VALUES (10, 0)")
    assert base_effects.forged_items(conn) == [20]


def test_rune_size_is_read_from_its_name():
    from dofustool.analysis import fmdata

    assert [fmdata.rune_tier(n) for n in ("Rune Fo", "Rune Pa Fo", "Rune Ra Fo", "Rune Pa Ré Pme", "Rune Ré Per Air", "Rune Pa")] == [
        "simple", "Pa", "Ra", "Pa", "simple", "simple"]
