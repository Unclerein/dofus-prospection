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
    conn.executemany("INSERT INTO fm_refusals (source_id, ts, uid, item_id, rune_id, status, code, before) VALUES (?, ?, 7, 10, 20, 0, NULL, '[[118, 101]]')", [(500 + i, 3000.0 + i) for i in range(20)])
    rules = {r["id"]: r for r in collecte.rules(conn, collecte.compute(conn, DENSITY), now=200.0)}
    assert rules["ligne-101"]["status"] == "probable" and rules["ligne-101"]["was"] == "À vérifier"
    # Un refus alors que la ligne serait restée sous 101 la contredirait.
    conn.execute("INSERT INTO fm_refusals (source_id, ts, uid, item_id, rune_id, status, code, before) VALUES (900, 4000.0, 7, 10, 20, 0, NULL, '[[118, 50]]')")
    rules = {r["id"]: r for r in collecte.rules(conn, collecte.compute(conn, DENSITY), now=300.0)}
    assert rules["ligne-101"]["status"] == "refute"


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


def test_the_one_percent_threshold_is_settled_between_25_and_30():
    from types import SimpleNamespace

    def tries(extra, n, passed, weight=3):
        # Over de +1 sur une ligne déjà au-delà de son max : toute la rune compte en poids over.
        return [SimpleNamespace(line_kind="over", extra_before=extra, line_before=10, line_max=8, rune_value=1, rune_weight=weight,
                                line_after=11 if i < passed else 10) for i in range(n)]

    crossing_30 = tries(28, 400, 3)  # 28 + 3 : franchit 30, environ 1 %
    at_30 = collecte.judge_beyond_25(collecte.Context(crossing_30 + tries(23, 150, 50), [], []))
    assert at_30[0] == "probable" and "seuil est à 30" in at_30[1]
    at_25 = collecte.judge_beyond_25(collecte.Context(crossing_30 + tries(23, 150, 1), [], []))
    assert at_25[0] == "probable" and "seuil est à 25" in at_25[1]
    assert collecte.judge_beyond_25(collecte.Context(tries(28, 50, 1), [], []))[0] == "verifier"
    # Déjà au-delà de 30, ou une rune lourde : ni l'un ni l'autre ne franchit le seuil au sens de la rumeur.
    assert collecte.crossing(tries(32, 1, 0)[0]) is None and collecte.crossing(tries(20, 1, 0, weight=30)[0]) is None
    assert collecte.crossing(tries(23, 1, 0)[0]) == 25 and collecte.crossing(tries(28, 1, 0)[0]) == 30


def test_missions_put_the_formula_gaps_first():
    ids = [m.id for m in collecte.MISSIONS]
    assert ids[:2] == ["portee", "mal-roule"] and "orbes" not in ids
    state = collecte.compute(forge([(0.0, 40, 1)]), DENSITY)
    assert collecte.focus(state)["id"] in ids[:collecte.PRIORITY]
    assert {b.id for b in state.blocks} >= {"gaps"} and "orbs" not in {b.id for b in state.blocks}


def test_passes_keep_the_game_version_and_the_rules_of_their_time():
    conn = forge([(0.0, 40, 1)] * 2)
    assert db.tag_game_version(conn) == 0  # version du jeu inconnue : rien n'est deviné
    conn.executemany("INSERT INTO static_meta VALUES (?, ?)", [("version", "v6.0_3.7.11.11"), ("imported_at", "1000.5")])
    conn.commit()
    assert db.game_version(conn) == "3.7.11.11"
    assert db.tag_game_version(conn) == 1  # le passage d'avant l'import des données reste sans version
    assert conn.execute("SELECT ts, game_version FROM fm_passes ORDER BY ts").fetchall() == [(1000.0, None), (1001.0, "3.7.11.11")]
    assert db.record_densities(conn, {118: 1.0}) and not db.record_densities(conn, {118: 2.0})
    # La fiche d'un objet qui change garde l'ancienne dans l'historique.
    from dofustool.staticdata import effects as base_effects

    assert db.snapshot_templates(conn, "3.7.4.4", 5.0) == 2
    base_effects.store(conn, 10, [(118, 30, 55), (125, 100, 200)])
    history = conn.execute("SELECT version, effects FROM item_effects_history WHERE item_id = 10 ORDER BY seen_at").fetchall()
    assert [json.loads(e) for _, e in history] == [[[118, 30, 50], [125, 100, 200]], [[118, 30, 55], [125, 100, 200]]]
    base_effects.store(conn, 10, [(118, 30, 55), (125, 100, 200)])
    assert conn.execute("SELECT COUNT(*) FROM item_effects_history WHERE item_id = 10").fetchone()[0] == 2  # inchangée : rien de plus


def test_item_fill_and_item_weight_are_judged_apart():
    from types import SimpleNamespace

    def tries(n, failed, weight, fill):
        return [SimpleNamespace(line_kind="bas", line_max=100, rune_value=5, line_before=40, line_after=45, item_level=200,
                                item_weight_max=weight, item_weight_before=fill * weight, outcome="ec" if i < failed else "sn")
                for i in range(n)]

    full_fails = tries(400, 200, 700, 0.95) + tries(400, 50, 700, 0.5)
    assert collecte.judge_item_weight(collecte.Context(full_fails, [], []))[0] == "certain"
    for r in full_fails:
        r.rune_tier, r.rune_weight = "simple", 3
    same = tries(8000, 2000, 700, 0.8) + tries(8000, 2000, 300, 0.8)  # même remplissage, poids max différent : rien ne change
    worse = tries(1000, 400, 700, 0.8) + tries(1000, 200, 300, 0.8)
    for r in same + worse:
        r.rune_tier, r.rune_weight = "simple", 3
    assert collecte.judge_heavy_item(collecte.Context(same, [], []))[0] == "refute"
    assert collecte.judge_heavy_item(collecte.Context(worse, [], []))[0] == "probable"
