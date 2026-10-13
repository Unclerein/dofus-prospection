"""XP des métiers de forgemagie et chemin le moins coûteux. Données synthétiques ; la formule vient de la capture."""
import pytest

from .test_app import app_db  # noqa: F401  (fixture : une base complète sur disque)

from dofustool.analysis import jobxp, magexp
from dofustool.analysis.magexp import Rune, Support

FO, SA, SO = 118, 124, 178  # densités du jeu : 1, 3, 10


def test_xp_formula_matches_what_the_game_gave():
    # Métier 200, objet 200 : 20 x le poids. Valeurs relevées dans l'archive.
    assert [magexp.mage_xp(w, 200, 200) for w in (1, 2, 3, 5, 10, 30, 100)] == [20, 40, 60, 100, 200, 600, 2000]
    # Objet sous le niveau du métier : l'XP fond vite.
    assert magexp.mage_xp(5, 198, 200) == 81 and magexp.mage_xp(10, 198, 200) == 163 and magexp.mage_xp(6, 198, 200) == 97
    assert magexp.mage_xp(5, 196, 200) == 67 and magexp.mage_xp(15, 196, 200) == 201 and magexp.mage_xp(9, 196, 200) == 120
    assert magexp.mage_xp(5, 194, 200) == 56 and magexp.mage_xp(5, 186, 200) == 32 and magexp.mage_xp(10, 186, 200) == 65
    assert magexp.mage_xp(9, 114, 200) == 7 and magexp.mage_xp(3, 114, 200) == 2 and magexp.mage_xp(10, 110, 200) == 7
    assert magexp.mage_xp(1, 110, 200) == 1  # minimum de 1 : le calcul donne 0, le jeu a donné 1
    # C'est l'XP d'un craft multipliée par poids / 200.
    assert magexp.mage_xp(200, 150, 180) == jobxp.craft_xp(150, 180)
    # Un mage ne travaille pas un objet au-dessus de son niveau.
    assert magexp.mage_xp(10, 25, 24) == 0 and magexp.mage_xp(10, 24, 24) == 24 and magexp.mage_xp(0, 24, 24) == 0


RUNES = [Rune(1, FO, 1, 40.0), Rune(2, FO, 3, 120.0), Rune(3, SA, 3, 240.0), Rune(4, SO, 10, 500.0), Rune(5, SA, 9, 720.0)]


def test_best_pair_takes_the_two_cheapest_lines():
    ring = Support(100, 200, frozenset({FO, SA, SO}), 50_000.0)
    rate, (first, second), gained = magexp.best_pair(ring, RUNES, 200)
    # Par point d'XP : So 2,5 ; Fo 2 (la Pa, aussi chère que la petite mais trois fois moins de passages) ; Sa 4.
    assert (first.item_id, second.item_id) == (2, 4)
    assert gained == pytest.approx(20 + 20) and rate == pytest.approx((40 + 50) / 40)
    assert magexp.best_pair(Support(101, 200, frozenset({FO}), 1.0), RUNES, 200) is None  # une seule ligne : rien à alterner
    assert magexp.best_pair(ring, RUNES, 199) is None  # hors de portée du métier


def test_cheapest_path_changes_support_only_when_it_pays():
    supports = [
        Support(100, 1, frozenset({FO, SA}), 500.0),
        Support(101, 50, frozenset({FO, SA}), 2_000.0),
        Support(102, 51, frozenset({FO, SA}), 5_000_000.0),  # un niveau de plus, hors de prix : jamais rentable
        Support(103, 100, frozenset({FO}), 10.0),  # une seule ligne
    ]
    plan = magexp.cheapest_path(supports, RUNES, 0, 60, pass_rate=0.5)
    assert plan.blocked_at is None and plan.end_xp == jobxp.level_to_xp(60) and plan.pass_rate == 0.5
    assert [(s.item_id, s.from_level, s.to_level, s.bought) for s in plan.steps] == [(100, 1, 50, True), (101, 50, 60, True)]
    assert set(plan.steps[0].runes) <= {1, 2, 3, 5} and plan.passes == sum(sum(s.runes.values()) for s in plan.steps)
    # Deux fois moins de réussite : deux fois plus de runes, à l'arrondi près.
    easy = magexp.cheapest_path(supports, RUNES, 0, 60, pass_rate=1.0)
    assert plan.rune_cost == pytest.approx(2 * easy.rune_cost, rel=0.05)
    # Aucun objet à la portée du métier : le plan s'arrête et le dit.
    blocked = magexp.cheapest_path([Support(101, 50, frozenset({FO, SA}), 1.0)], RUNES, 0, 60)
    assert blocked.blocked_at == 1 and blocked.steps == []
    # Déjà au niveau visé : rien à faire.
    assert magexp.cheapest_path(supports, RUNES, jobxp.level_to_xp(60), 60).steps == []


def test_mage_plan_is_served(app_db, tmp_path):  # noqa: F811
    """La page Métiers propose les métiers de forgemagie : objet support, runes à alterner, liste de courses."""
    import sqlite3
    import time

    from dofustool import db
    from dofustool.web.api import Api

    now = time.time()
    conn = db.connect(app_db)
    conn.executemany("INSERT OR IGNORE INTO jobs VALUES (?, ?)", [(16, "Bijoutier"), (63, "Joaillomage")])
    conn.execute("INSERT INTO recipes VALUES (500, 16, 60)")  # l'Anneau test (niveau 60) : Vitalité 201 à 250, PA 1
    conn.execute("INSERT INTO recipe_ingredients VALUES (500, 1, 1)")
    conn.executemany(
        "INSERT INTO items VALUES (?, ?, 1, 'Rune de forgemagie', 1, 1, 0, 2)",
        [(9001, "Rune Vi"), (9002, "Rune Pa Vi"), (9003, "Rune Ga Pa"), (9004, "Rune Fo")],
    )
    conn.executemany("INSERT INTO item_effects VALUES (?, ?, ?, ?)", [(9001, 125, 5, 5), (9002, 125, 15, 15), (9003, 111, 1, 1), (9004, 118, 1, 1)])
    conn.executemany("INSERT INTO item_effects_fetched VALUES (?, ?)", [(i, now) for i in (9001, 9002, 9003, 9004)])
    db.save_snapshot(conn, now, {9001: 100, 9002: 999, 9003: 20_000, 9004: 40})
    conn.close()
    static = tmp_path / "dofus.sqlite"
    source = sqlite3.connect(static)
    source.execute("CREATE TABLE EffectData (id INTEGER, effectPowerRate REAL)")
    source.executemany("INSERT INTO EffectData VALUES (?, ?)", [(125, 0.2), (111, 100.0), (118, 1.0)])
    source.commit()
    source.close()

    api = Api(app_db)
    api.static_path = static
    mages = [job for job in api.jobs()["jobs"] if job["mage"]]
    assert [job["name"] for job in mages] == ["Joaillomage"] and mages[0]["recipes"] == 0
    plan = api.job_plan(63, jobxp.level_to_xp(60), 62, pass_rate=0.5)
    assert plan["mage"] and plan["pass_rate"] == 0.5 and plan["blocked_at"] is None and plan["end_level"] == 62
    (step,) = plan["steps"]
    assert (step["item_id"], step["level"], step["from_level"], step["to_level"], step["bought"]) == (500, 60, 60, 62, True)
    # Deux lignes sur l'anneau : Vitalité (la Pa, chiffrée par fusion à 300 et non 999) et PA. La Rune Fo n'y a pas de ligne.
    assert {rune["item_id"] for rune in step["runes"]} == {9002, 9003}
    assert {rune["item_id"]: rune["price"] for rune in step["runes"]} == {9002: 300, 9003: 20_000}
    assert plan["supports_cost"] == step["support_price"] and plan["cost"] == pytest.approx(step["cost"] + step["support_price"])
    assert {row["item_id"] for row in plan["shopping"]} == {500, 9002, 9003}
    assert api.job_plan(63, 0, 10)["blocked_at"] == 1  # aucun objet de niveau 1 à 10 : le plan le dit
    assert api.job_plan(63, jobxp.level_to_xp(60), 62, exclude=frozenset({500}))["blocked_at"] == 60
