"""Atelier : listes d'objectifs, décomposition en ingrédients, stock déduit, ressources disputées.
Données synthétiques uniquement, aucun appel réseau."""
import json

import pytest

from dofustool import db
from dofustool.analysis import workshop
from dofustool.analysis.crafts import Recipe
from dofustool.analysis.workshop import BUY, CRAFT, Goal
from dofustool.web.api import Api

from .test_app import app_db  # noqa: F401  (fixture : une base complète sur disque)

# Épée = 2 lames + 5 bois ; lame = 3 fers. Le bois et le fer ne se fabriquent pas.
SWORD, BLADE, WOOD, IRON = 10, 11, 12, 13
RECIPES = {SWORD: Recipe(SWORD, 1, 1, ((BLADE, 2), (WOOD, 5))), BLADE: Recipe(BLADE, 1, 1, ((IRON, 3),))}
PRICES = {SWORD: 1_000.0, BLADE: 200.0, WOOD: 10.0, IRON: 50.0}


def plan(goals, opened=(), stock=None, prices=PRICES):
    stock = stock or {}
    return workshop.plan(goals, set(opened), RECIPES, prices.get, lambda item_id: stock.get(item_id, 0))


def test_a_goal_is_bought_or_made_whichever_is_cheaper():
    found = plan([Goal(1, SWORD, 3)])
    (goal,) = found.goals
    # Fabriquer : 2 lames à 200 + 5 bois à 10 = 450, moins cher que 1 000.
    assert (goal.mode, goal.buy_cost, goal.craft_cost, goal.to_make) == (CRAFT, 3_000.0, 1_350.0, 3)
    assert found.lines == {BLADE: 6, WOOD: 15} and found.opened == {}
    # Imposé, ou sans prix d'achat, ou sans recette.
    assert plan([Goal(1, SWORD, 3, BUY)]).lines == {SWORD: 3}
    assert plan([Goal(1, SWORD, 1)], prices={**PRICES, SWORD: None}).goals[0].mode == CRAFT
    assert plan([Goal(1, SWORD, 1)], prices={**PRICES, SWORD: 400.0}).goals[0].mode == BUY
    assert plan([Goal(1, WOOD, 7, CRAFT)]).lines == {WOOD: 7}  # pas de recette : forcément acheté


def test_an_opened_ingredient_is_replaced_by_its_own_ingredients():
    found = plan([Goal(1, SWORD, 3, CRAFT)], opened={BLADE})
    assert found.lines == {IRON: 18, WOOD: 15} and found.opened == {BLADE: (6, 6)}
    # Ouvrir ce qui ne se fabrique pas ne change rien.
    assert plan([Goal(1, SWORD, 3, CRAFT)], opened={WOOD}).lines == {BLADE: 6, WOOD: 15}


def test_stock_is_deducted_from_what_is_made_before_what_is_bought():
    # Une épée déjà faite, et quatre lames en stock : il reste 2 épées, donc 4 lames, toutes déjà là.
    found = plan([Goal(1, SWORD, 3, CRAFT)], opened={BLADE}, stock={SWORD: 1, BLADE: 4})
    assert found.goals[0].to_make == 2 and found.opened == {BLADE: (4, 0)} and found.lines == {WOOD: 10}
    # Deux objectifs pour le même objet ne comptent pas deux fois l'exemplaire en stock.
    found = plan([Goal(1, SWORD, 1, CRAFT), Goal(2, SWORD, 1, CRAFT)], stock={SWORD: 1})
    assert [g.to_make for g in found.goals] == [0, 1] and found.lines == {BLADE: 2, WOOD: 5}


def test_workshop_lists_are_stored_and_served(app_db):  # noqa: F811
    api = Api(app_db)
    assert api.workshop()["lists"] == []
    first = api.workshop_action({"action": "create", "name": "  Mon   stuff "})["list"]
    # Dans la base de test : Pain (3) = 3 Farine (2) + 1 Eau (4) ; Farine = 2 Blé (1).
    api.workshop_action({"action": "add", "list": first, "item_id": 3, "quantity": 4})
    data = api.workshop()
    json.dumps(data, allow_nan=False)
    (shown,) = data["lists"]
    (goal,) = shown["goals"]
    assert (shown["name"], goal["name"], goal["quantity"], goal["craftable"]) == ("Mon stuff", "Pain", 4, True)
    api.workshop_action({"action": "goal", "goal": goal["id"], "mode": "craft", "quantity": 6})
    (shown,) = api.workshop()["lists"]
    assert shown["goals"][0]["mode"] == "craft" and shown["goals"][0]["quantity"] == 6
    by_name = {line["name"]: line for line in shown["lines"]}
    assert (by_name["Farine"]["need"], by_name["Eau"]["need"], by_name["Farine"]["craftable"], by_name["Eau"]["craftable"]) == (18, 6, True, False)
    assert by_name["Farine"]["shared"] == [] and by_name["Farine"]["need_all"] == 18
    assert shown["cost_total"] >= shown["cost_remaining"] > 0

    # La farine « ouverte » est remplacée par son blé.
    api.workshop_action({"action": "open", "list": first, "item_id": 2, "opened": True})
    (shown,) = api.workshop()["lists"]
    assert {line["name"]: line["need"] for line in shown["lines"]} == {"Blé": 36, "Eau": 6}
    assert [(o["name"], o["need"], o["to_make"]) for o in shown["opened"]] == [("Farine", 18, 18)]
    # L'arbre garde la farine, à fabriquer, avec son blé rangé dessous ; l'eau reste au premier niveau.
    flour, water = shown["tree"]
    assert (flour["name"], flour["kind"], flour["to_make"], [(c["name"], c["share"], c["kind"]) for c in flour["children"]]) == (
        "Farine", "made", 18, [("Blé", 36, "leaf")],
    )  # fmt: skip
    assert (water["name"], water["kind"], water["share"], water["children"]) == ("Eau", "leaf", 6, [])
    assert flour["cost"] == flour["children"][0]["cost"] and not flour["children"][0]["repeat"]
    api.workshop_action({"action": "open", "list": first, "item_id": 2, "opened": False})

    # Une seconde liste veut la même farine : les deux lignes le signalent, avec le besoin total.
    second = api.workshop_action({"action": "plan", "name": "Plan Boulanger", "goals": [[3, 10]]})["list"]
    lists = {entry["id"]: entry for entry in api.workshop()["lists"]}
    again = next(line for line in lists[first]["lines"] if line["name"] == "Farine")
    other = next(line for line in lists[second]["lines"] if line["name"] == "Farine")
    assert (again["need"], other["need"], again["need_all"], other["need_all"]) == (18, 30, 48, 48)
    assert {entry["list"] for entry in again["shared"]} == {first, second} and again["missing_all"] == 48

    api.workshop_action({"action": "rename", "list": first, "name": "Stuff air"})
    api.workshop_action({"action": "remove", "goal": goal["id"]})
    lists = {entry["id"]: entry for entry in api.workshop()["lists"]}
    assert lists[first]["name"] == "Stuff air" and lists[first]["goals"] == [] and lists[first]["lines"] == []
    api.workshop_action({"action": "delete", "list": first})
    assert [entry["id"] for entry in api.workshop()["lists"]] == [second]


def test_almanax_offerings_become_a_list(app_db):  # noqa: F811
    api = Api(app_db)
    api.fetch_almanax = lambda day: {"name": "Bonus", "desc": "", "items": [[1, 5, "Blé"]]}
    made = api.workshop_action({"action": "almanax", "days": 3})
    (shown,) = api.workshop()["lists"]
    assert made == {"list": shown["id"], "missing_days": 0} and shown["name"].startswith("Almanax du ")
    assert [goal["quantity"] for goal in shown["goals"]] == [5, 5, 5] and len({goal["note"] for goal in shown["goals"]}) == 3
    (line,) = shown["lines"]
    assert (line["name"], line["need"]) == ("Blé", 15)  # les offrandes des trois jours, additionnées

    def broken(day):
        raise OSError("hors ligne")

    fresh = Api(app_db)
    fresh.fetch_almanax = broken
    assert fresh.workshop_action({"action": "almanax", "days": 3})["missing_days"] == 0  # déjà en base
    with pytest.raises(ValueError):
        conn = db.connect(app_db)
        conn.execute("DELETE FROM almanax")
        conn.commit()
        conn.close()
        fresh.workshop_action({"action": "almanax", "days": 2})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"action": "create", "name": "   "},
        {"action": "add", "list": 999, "item_id": 3},
        {"action": "plan", "name": "x", "goals": [[3, 0]]},
        {"action": "plan", "name": "x", "goals": [[424242, 1]]},
        {"action": "goal", "goal": 1, "mode": "voler"},
        {"action": "almanax", "days": 400},
        {"action": "détruire"},
    ],
)
def test_malformed_requests_are_refused(app_db, payload):  # noqa: F811
    api = Api(app_db)
    if payload.get("action") == "goal":
        first = api.workshop_action({"action": "create", "name": "L"})["list"]
        api.workshop_action({"action": "add", "list": first, "item_id": 3})
    with pytest.raises(ValueError):
        api.workshop_action(payload)


def test_a_list_is_imported_from_item_ids(app_db):  # noqa: F811
    """Un stuff venu d'ailleurs : des identifiants d'objets, un exemplaire de chacun ; les inconnus sont laissés de côté."""
    api = Api(app_db)
    done = api.workshop_action({"action": "import", "name": " eau  pvm ", "items": [3, 2, 3, 999_999]})
    assert done["skipped"] == 1
    (imported,) = [entry for entry in api.workshop()["lists"] if entry["id"] == done["list"]]
    assert imported["name"] == "eau pvm"
    assert sorted((goal["item_id"], goal["quantity"]) for goal in imported["goals"]) == [(2, 1), (3, 2)]
    for bad in ([], [999_999], ["3"], [True], list(range(1, 80))):
        with pytest.raises(ValueError):
            api.workshop_action({"action": "import", "name": "x", "items": bad})


def test_equipment_is_priced_at_its_cheapest_recent_listing(app_db):  # noqa: F811
    """Dans l'atelier, un équipement s'achète au prix de l'annonce la moins chère de l'HDV, tant que le relevé est récent."""
    api = Api(app_db)
    done = api.workshop_action({"action": "import", "name": "stuff", "items": [500]})
    conn = api.connect()
    prices = api._load(conn)["ws"].prices
    cheapest, usual = prices.hdv_any(500)[0], prices.get(500)
    (goal,) = [entry for entry in api.workshop()["lists"] if entry["id"] == done["list"]][0]["goals"]
    assert goal["buy_cost"] == cheapest
    # Relevé de trois jours : trop vieux, le prix de référence habituel reprend la main.
    conn.execute("UPDATE hdv_listings SET captured_at = captured_at - 3 * 86400, first_seen = first_seen - 3 * 86400 WHERE item_id = 500")
    conn.commit()
    conn.close()
    api = Api(app_db)
    conn = api.connect()
    usual = api._load(conn)["ws"].prices.get(500)
    conn.close()
    (goal,) = [entry for entry in api.workshop()["lists"] if entry["id"] == done["list"]][0]["goals"]
    assert goal["buy_cost"] == (usual.price if usual is not None else None)


def test_workshop_opens_containers_before_buying(app_db):  # noqa: F811
    """Dans l'atelier, ce qui manque se prend d'abord dans les sachets possédés : combien ouvrir, puis combien acheter."""
    from dofustool.messages.storage import Stack, Storage
    from dofustool.staticdata import contents

    conn = db.connect(app_db)
    conn.execute("INSERT INTO items VALUES (900, 'Sachet de Blé', 1, 'Conteneur', 1, 1, 0, 2)")
    contents.store(conn, [(900, 1, 10)])
    db.save_holdings(conn, db.INVENTORY, Storage(0, (Stack(1, 4, False), Stack(900, 3, False))), 100.0)  # 4 Blé, 3 sachets
    conn.close()
    api = Api(app_db)
    done = api.workshop_action({"action": "import", "name": "blé", "items": [1] * 20})
    (found,) = [entry for entry in api.workshop()["lists"] if entry["id"] == done["list"]]
    (line,) = [row for row in found["lines"] if row["item_id"] == 1]
    assert (line["need"], line["owned"]["total"], line["packed"], line["to_buy"]) == (20, 4, 30, 0)
    assert [(o["name"], o["count"], o["owned"]) for o in line["open"]] == [("Sachet de Blé", 2, 3)]
    stock_rows = {row["item_id"]: row for row in api.stock()["rows"]}
    assert stock_rows[900]["contains"] == {"item_id": 1, "name": line["name"], "per": 10, "units": 30}
    assert stock_rows[1]["packed"] == 30 and stock_rows[1]["total"] == 4
