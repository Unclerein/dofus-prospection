import json

import pytest

from dofustool import db
from dofustool.analysis.jobxp import Candidate, cheapest_path, craft_xp, level_to_xp, xp_to_level

from .test_web import api, app_db  # noqa: F401  (fixtures)


# --- formule ----------------------------------------------------------------

def test_craft_xp_formula():
    assert craft_xp(1, 1) == 20  # recette de son niveau : 20 x niveau
    assert craft_xp(100, 100) == 2000 and craft_xp(200, 200) == 4000
    assert craft_xp(50, 60) == 442  # 1000 / (10^1,1 / 10 + 1) = 442,69, arrondi à l'entier inférieur
    assert craft_xp(50, 150) == 59 and craft_xp(50, 151) == 0  # plus de 100 niveaux d'écart : rien
    assert craft_xp(60, 50) == 0  # recette au-dessus de son niveau : infaisable
    assert craft_xp(100, 100, ratio_pct=50) == 1000 and craft_xp(100, 100, ratio_pct=0) == 0
    assert craft_xp(100, 100, bonus_pct=150) == 3000
    assert [craft_xp(40, level) for level in (40, 50, 80, 120)] == sorted(
        (craft_xp(40, level) for level in (40, 50, 80, 120)), reverse=True
    )  # l'XP baisse quand le métier dépasse la recette


def test_level_and_xp_are_inverse():
    assert level_to_xp(1) == 0 and level_to_xp(2) == 20 and level_to_xp(200) == 398_000
    for level in (1, 2, 17, 100, 199, 200):
        assert xp_to_level(level_to_xp(level)) == level
        if level > 1:
            assert xp_to_level(level_to_xp(level) - 1) == level - 1
    assert xp_to_level(10**9) == 200 and xp_to_level(-5) == 1


# --- chemin -----------------------------------------------------------------

BREAD = Candidate(item_id=1, level=1, ratio_pct=100, cost=10)  # 20 XP au niveau 1 : 0,5 k/XP
CAKE = Candidate(item_id=2, level=5, ratio_pct=100, cost=30)  # 100 XP au niveau 5 : 0,3 k/XP
JEWEL = Candidate(item_id=3, level=5, ratio_pct=100, cost=500)  # même XP que le gâteau, bien plus cher


def test_cheapest_path_switches_recipe_when_a_better_one_unlocks():
    plan = cheapest_path([BREAD, CAKE, JEWEL], start_xp=0, target_level=8)
    assert plan.reached and plan.end_xp >= level_to_xp(8) and xp_to_level(plan.end_xp) == 8
    assert [(s.item_id, s.from_level, s.to_level) for s in plan.steps] == [(1, 1, 5), (2, 5, 8)]
    assert plan.steps[0].crafts * 10 == plan.steps[0].cost and plan.cost == sum(s.cost for s in plan.steps)
    assert plan.crafts == sum(s.crafts for s in plan.steps)
    assert 3 not in {s.item_id for s in plan.steps}  # jamais la recette chère à XP égale


def test_removing_an_item_changes_the_path():
    without_cake = cheapest_path([BREAD, JEWEL], start_xp=0, target_level=8)
    with_cake = cheapest_path([BREAD, CAKE, JEWEL], start_xp=0, target_level=8)
    assert without_cake.reached and without_cake.cost > with_cake.cost
    assert 2 not in {s.item_id for s in without_cake.steps}


def test_path_starts_from_current_xp_and_stops_at_target():
    xp = level_to_xp(6) + 15
    plan = cheapest_path([BREAD, CAKE], start_xp=xp, target_level=7)
    assert plan.start_xp == xp and [(s.item_id, s.from_level, s.to_level) for s in plan.steps] == [(2, 6, 7)]
    assert cheapest_path([BREAD, CAKE], start_xp=level_to_xp(9), target_level=7).steps == []  # déjà au-dessus


def test_path_reports_where_it_gets_stuck():
    # Une seule recette de niveau 1 : elle ne rapporte plus rien au-delà du niveau 101.
    plan = cheapest_path([BREAD], start_xp=0, target_level=150)
    assert not plan.reached and plan.blocked_at == 102
    assert cheapest_path([], start_xp=0, target_level=5).blocked_at == 1


def test_negative_cost_recipes_are_preferred():
    profit = Candidate(item_id=9, level=1, ratio_pct=100, cost=-5)  # revente déduite : le craft rapporte
    plan = cheapest_path([BREAD, profit], start_xp=0, target_level=3)
    assert {s.item_id for s in plan.steps} == {9} and plan.cost < 0
    # Entre deux crafts « gratuits », celui qui donne le plus d'XP, pas celui qui rapporte le plus de kamas.
    jackpot = Candidate(item_id=8, level=1, ratio_pct=10, cost=-1000)  # 2 XP par craft, gros bénéfice
    plan = cheapest_path([profit, jackpot], start_xp=0, target_level=3)
    assert {s.item_id for s in plan.steps} == {9}


def test_zero_xp_ratio_is_never_used():
    plan = cheapest_path([Candidate(1, 1, 0, 1), BREAD], start_xp=0, target_level=3)
    assert plan.reached and all(s.xp > 0 for s in plan.steps)
    assert cheapest_path([Candidate(1, 1, 0, 1)], start_xp=0, target_level=3).blocked_at == 1


# --- serveur ----------------------------------------------------------------

def test_jobs_and_plan_endpoints(api):  # noqa: F811
    jobs = {j["name"]: j for j in api.jobs()["jobs"]}
    assert jobs["Paysan"]["recipes"] == 5 and jobs["Forgeron"]["recipes"] == 3
    paysan = jobs["Paysan"]["id"]

    plan = api.job_plan(paysan, 0, 25)
    json.dumps(plan, allow_nan=False)
    assert plan["start_level"] == 1 and plan["blocked_at"] == 1  # la première recette du métier est niveau 10
    plan = api.job_plan(paysan, level_to_xp(10), 25)
    assert plan["blocked_at"] is None and plan["end_level"] == 25 and plan["crafts"] > 0
    assert plan["steps"][0]["name"] == "Farine" and plan["steps"][0]["from_level"] == 10
    assert plan["cost"] == pytest.approx(sum(s["cost"] for s in plan["steps"]))
    wheat = next(row for row in plan["shopping"] if row["name"] == "Blé")
    assert wheat["quantity"] >= 2 * plan["steps"][0]["crafts"] and wheat["to_buy"] == wheat["quantity"]  # stock inconnu

    used = {s["item_id"] for s in plan["steps"]}
    again = api.job_plan(paysan, level_to_xp(10), 25, exclude=frozenset(used))
    assert not used & {s["item_id"] for s in again["steps"]}
    assert {x["item_id"] for x in again["excluded"]} == used
    assert again["cost"] >= plan["cost"] or again["blocked_at"] is not None

    net = api.job_plan(paysan, level_to_xp(10), 25, resale=True)
    assert net["resale"] and net["cost"] < plan["cost"]  # la revente déduite baisse le coût

    api.set_ignored(next(iter(used)), True)  # un objet ignoré sort aussi du chemin
    assert next(iter(used)) not in {s["item_id"] for s in api.job_plan(paysan, level_to_xp(10), 25)["steps"]}


def test_plan_uses_recipe_xp_ratio(api):  # noqa: F811
    paysan = next(j["id"] for j in api.jobs()["jobs"] if j["name"] == "Paysan")
    base = api.job_plan(paysan, level_to_xp(10), 12)
    conn = api.connect()
    conn.execute("INSERT INTO recipe_xp VALUES (?, 0)", (base["steps"][0]["item_id"],))  # cette recette ne donne plus d'XP
    conn.commit()
    conn.close()
    assert base["steps"][0]["item_id"] not in {s["item_id"] for s in api.job_plan(paysan, level_to_xp(10), 12)["steps"]}
    assert db.connect(api.db_path).execute("SELECT COUNT(*) FROM recipe_xp").fetchone()[0] == 1
