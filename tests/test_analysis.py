import pytest

from dofustool import db
from dofustool.analysis import DAY, GRAIN_DAY, GRAIN_HOUR
from dofustool.analysis.crafts import BUY, CRAFT, CraftCalculator, load_items, load_recipes, rank_crafts, resolve_jobs
from dofustool.analysis.prices import AVG_PRICE, LAST_SALE, PriceBook
from dofustool.analysis.trends import INSUFFICIENT, MARKET_HISTORY, OVER, SNAPSHOTS, UNDER, compute_trends

NOW = 1_000_000.0
HOUR = 3600.0

# id: (nom, échangeable)
ITEMS = {
    1: ("Blé", 1), 2: ("Farine", 1), 3: ("Pain", 1), 4: ("Eau", 1), 5: ("Levure liée", 0),
    6: ("Brioche", 1), 7: ("Relique", 0), 8: ("Sans prix", 1), 9: ("Gâteau", 1),
    10: ("Cycle A", 1), 11: ("Cycle B", 1), 12: ("Trophée lié", 0),
}  # fmt: skip
# résultat: (métier, niveau, [(ingrédient, quantité)])
RECIPES = {
    2: (28, 10, [(1, 2)]),  # Farine = 2 Blé
    3: (28, 20, [(2, 3), (4, 1)]),  # Pain = 3 Farine + 1 Eau
    5: (28, 30, [(1, 1)]),  # Levure liée (non échangeable) = 1 Blé
    6: (28, 40, [(2, 1), (5, 2)]),  # Brioche = 1 Farine + 2 Levure liée
    9: (28, 50, [(8, 1), (1, 1)]),  # Gâteau = 1 Sans prix + 1 Blé
    10: (11, 60, [(11, 1)]),  # cycle : A = 1 B
    11: (11, 60, [(10, 1), (1, 1)]),  # B = 1 A + 1 Blé
    12: (11, 70, [(1, 5)]),  # Trophée lié (non échangeable) = 5 Blé
}
AVG = {1: 10, 2: 50, 3: 200, 4: 5, 6: 300, 9: 1000, 10: 100, 11: 500}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    c.executemany(
        "INSERT INTO items VALUES (?, ?, 1, 'type', 1, ?, 0, 2)", [(i, name, exch) for i, (name, exch) in ITEMS.items()]
    )
    c.executemany("INSERT INTO jobs VALUES (?, ?)", [(28, "Paysan"), (11, "Forgeron")])
    for result, (job, level, ingredients) in RECIPES.items():
        c.execute("INSERT INTO recipes VALUES (?, ?, ?)", (result, job, level))
        c.executemany("INSERT INTO recipe_ingredients VALUES (?, ?, ?)", [(result, i, q) for i, q in ingredients])
    db.save_snapshot(c, NOW - HOUR, AVG)
    yield c
    c.close()


def calculator(conn, job_levels=None, min_liquidity=0, tax=0.02):
    prices = PriceBook(conn, NOW, last_sale_max_age_hours=24)
    return CraftCalculator(load_items(conn), load_recipes(conn), prices, tax, job_levels, min_liquidity)


# --- prix de référence -------------------------------------------------------

def test_reference_price_prefers_fresh_last_sale(conn):
    db.save_last_sale(conn, 3, 180, NOW - 2 * HOUR, NOW)  # vente récente
    db.save_last_sale(conn, 2, 999, NOW - 30 * HOUR, NOW)  # vente trop ancienne, même vue à l'instant
    prices = PriceBook(conn, NOW, last_sale_max_age_hours=24)
    pain = prices.get(3)
    assert (pain.price, pain.source) == (180, LAST_SALE) and pain.age_hours(NOW) == pytest.approx(2)
    farine = prices.get(2)
    assert (farine.price, farine.source) == (50, AVG_PRICE) and farine.age_hours(NOW) == pytest.approx(1)
    assert prices.get(8) is None


def test_reference_price_uses_latest_snapshot(conn):
    db.save_snapshot(conn, NOW - 10, {**AVG, 1: 12})
    assert PriceBook(conn, NOW, 24).get(1).price == 12


# --- marges ------------------------------------------------------------------

def test_direct_and_recursive_margin(conn):
    pain = calculator(conn).evaluate(load_recipes(conn)[3])
    assert pain.revenue == pytest.approx(200 * 0.98)
    assert pain.direct_cost == 3 * 50 + 5  # farine achetée
    assert pain.recursive_cost == 3 * 20 + 5  # farine craftée (2 blé = 20 < 50)
    assert pain.margin == pytest.approx(196 - 155)
    assert pain.recursive_margin == pytest.approx(196 - 65)
    assert pain.margin_pct == pytest.approx(131 / 65)
    assert pain.crafted_ingredients == [2]
    assert pain.flags == ["liquidité inconnue"]


def test_unit_cost_picks_cheapest_mode(conn):
    calc = calculator(conn)
    assert (calc.unit_cost(2).cost, calc.unit_cost(2).mode) == (20, CRAFT)
    assert (calc.unit_cost(1).cost, calc.unit_cost(1).mode) == (10, BUY)
    conn.execute("UPDATE avg_prices SET price = 15 WHERE item_id = 2")
    assert calculator(conn).unit_cost(2).mode == BUY  # achat moins cher que le craft


def test_non_exchangeable_ingredient_is_flagged_and_crafted(conn):
    brioche = calculator(conn).evaluate(load_recipes(conn)[6])
    assert brioche.non_exchangeable_ingredients == [5]
    assert brioche.direct_cost is None and brioche.margin is None  # on ne peut pas l'acheter
    assert brioche.recursive_cost == 20 + 2 * 10  # mais on peut la crafter
    assert "ingrédient non échangeable" in brioche.flags


def test_missing_price_is_reported_not_hidden(conn):
    gateau = calculator(conn).evaluate(load_recipes(conn)[9])
    assert gateau.missing_prices == [8]
    assert gateau.recursive_cost is None and gateau.recursive_margin is None and gateau.weighted_margin is None
    assert "1 prix manquant(s)" in gateau.flags


def test_non_exchangeable_result_has_no_revenue(conn):
    trophee = calculator(conn).evaluate(load_recipes(conn)[12])
    assert trophee.result_not_exchangeable and trophee.revenue is None and trophee.recursive_margin is None
    assert trophee.recursive_cost == 50


def test_cycle_guard(conn):
    calc = calculator(conn)
    # A s'achète 100 ; le crafter demanderait B (500 à l'achat, ou A + blé = 110 en craft).
    assert (calc.unit_cost(10).cost, calc.unit_cost(10).mode) == (100, BUY)
    assert (calc.unit_cost(11).cost, calc.unit_cost(11).mode) == (110, CRAFT)
    # Le résultat ne dépend pas de l'ordre d'évaluation.
    calc = calculator(conn)
    assert calc.unit_cost(11).cost == 110 and calc.unit_cost(10).cost == 100


def test_jobs_mark_but_never_restrict(conn):
    job_levels, unknown = resolve_jobs(conn, {"paysan": 20, "Inconnu": 5})
    assert job_levels == {28: 20} and unknown == ["Inconnu"]
    results = {r.item.name: r for r in rank_crafts(conn, calculator(conn, job_levels))}
    assert len(results) == len(RECIPES)  # rien n'est écarté
    assert results["Pain"].own_job is True
    assert results["Brioche"].own_job is False  # niveau 40 requis
    assert results["Cycle B"].own_job is False  # autre métier
    assert results["Pain"].job == "Paysan"
    # Sans métier configuré : pas d'indication, et le même calcul.
    plain = {r.item.name: r for r in rank_crafts(conn, calculator(conn))}
    assert plain["Pain"].own_job is None
    assert plain["Brioche"].recursive_cost == results["Brioche"].recursive_cost


def test_ranking_puts_incomputable_last(conn):
    names = [r.item.name for r in rank_crafts(conn, calculator(conn))]
    assert names[0] == "Cycle B"  # 490 - 110
    assert set(names[-3:]) == {"Gâteau", "Levure liée", "Trophée lié"}  # prix manquant ou résultat invendable


# --- liquidité ---------------------------------------------------------------

def test_liquidity_weights_ranking(conn):
    hour, day = int(NOW) // 3600 * 3600, int(NOW) // 86400 * 86400
    db.save_market_history(conn, 3, GRAIN_HOUR, [(hour, 200, 4), (hour - 30 * 3600, 200, 99)], NOW)  # 2e : hors 24 h
    db.save_market_history(
        conn, 3, GRAIN_DAY, [(day, 200, 10), (day - 6 * 86400, 210, 15), (day - 7 * 86400, 210, 500)], NOW
    )  # le dernier point est hors des 7 jours
    calc = calculator(conn, min_liquidity=100)
    pain = calc.evaluate(load_recipes(conn)[3])
    assert (pain.liquidity.qty_24h, pain.liquidity.qty_7d) == (4, 25)
    assert pain.low_liquidity and "peu échangé" in pain.flags
    assert pain.weighted_margin == pytest.approx(pain.recursive_margin * 0.25)
    brioche = calc.evaluate(load_recipes(conn)[6])  # liquidité inconnue : pas de pénalité, mais signalée
    assert brioche.weighted_margin == brioche.recursive_margin and "liquidité inconnue" in brioche.flags
    assert not calculator(conn, min_liquidity=20).evaluate(load_recipes(conn)[3]).low_liquidity


# --- tendances ---------------------------------------------------------------

def test_trend_insufficient_snapshots(conn):
    trends = compute_trends(conn, NOW, min_snapshots=3, last_sale_max_age_hours=24)
    assert trends[1].basis == INSUFFICIENT and trends[1].samples == 1
    assert trends[1].deviation is None and trends[1].signal(0.15) is None


def test_trend_from_snapshots(conn):
    c = db.connect(":memory:")
    for age_days, price in [(40, 500), (20, 100), (5, 120), (2, 80)]:
        db.save_snapshot(c, NOW - age_days * DAY, {1: price, 2: 50 + age_days})
    db.save_snapshot(c, NOW, {1: 60, 2: 50})
    trend = compute_trends(c, NOW, min_snapshots=4, last_sale_max_age_hours=24)[1]
    assert trend.basis == SNAPSHOTS and trend.samples == 4  # le relevé de 40 jours est hors fenêtre
    assert trend.mean_7d == pytest.approx(100) and trend.mean_30d == pytest.approx(100)
    assert trend.deviation == pytest.approx(-0.4) and trend.signal(0.15) == UNDER
    assert compute_trends(c, NOW, min_snapshots=5, last_sale_max_age_hours=24)[1].basis == INSUFFICIENT


def test_trend_from_market_history_is_immediate(conn):
    day = int(NOW) // 86400 * 86400
    # Moyennes pondérées par les quantités : 7 j = (100*30 + 140*10) / 40 = 110 ; 30 j = 100.
    points = [(day, 100, 30), (day - 2 * 86400, 140, 10), (day - 10 * 86400, 90, 20), (day - 20 * 86400, 90, 20)]
    db.save_market_history(conn, 3, GRAIN_DAY, points + [(day - 40 * 86400, 5000, 5)], NOW)  # le dernier : hors 30 j
    db.save_last_sale(conn, 3, 150, NOW - HOUR, NOW)
    db.save_market_history(conn, 2, GRAIN_DAY, [(day, 100, 5)], NOW)  # historique sans dernier prix de vente
    trends = compute_trends(conn, NOW, min_snapshots=5, last_sale_max_age_hours=24)
    pain = trends[3]
    assert pain.basis == MARKET_HISTORY and pain.current == 150
    assert pain.dev_7d == pytest.approx(150 / 110 - 1) and pain.dev_30d == pytest.approx(0.5)  # moyennes 110 et 100
    assert pain.samples == 4
    assert pain.signal(0.15) == OVER
    assert trends[2].basis == INSUFFICIENT  # retombe sur les relevés
