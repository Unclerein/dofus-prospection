import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from dofustool import db
from dofustool.web.api import Api, clean, data_stamp
from dofustool.web.server import fetch_icon, make_handler

from .test_app import app_db  # noqa: F401  (fixture : une base complète sur disque)


@pytest.fixture
def api(app_db, tmp_path):  # noqa: F811
    conn = db.connect(app_db)
    conn.executemany("INSERT INTO item_icons VALUES (?, ?)", [(3, 9001), (500, 9002)])
    conn.commit()
    conn.close()
    return Api(app_db, tmp_path / "config.toml")  # fichier absent : valeurs par défaut


def test_clean_makes_values_json_safe():
    assert clean({1: float("nan"), "a": [1.5, float("inf")], "b": (2, None)}) == {"1": None, "a": [1.5, None], "b": [2, None]}
    json.dumps(clean({"x": float("nan")}), allow_nan=False)


def test_status_and_version(api):
    status = api.status()
    json.dumps(status, allow_nan=False)
    assert status["snapshots"] == 5 and status["decode_alert"] == "test d'alerte" and status["hdv_tax"] == 0.02
    first = api.version()["stamp"]
    conn = api.connect()
    db.save_snapshot(conn, 1.0, {1: 123456})
    conn.close()
    assert api.version()["stamp"] != first  # une nouvelle donnée change l'empreinte


def test_crafts_trends_items(api):
    crafts = api.crafts()
    json.dumps(crafts, allow_nan=False)
    assert len(crafts["rows"]) == 8 and crafts["jobs"] == ["Forgeron", "Paysan"]
    pain = next(r for r in crafts["rows"] if r["Objet"] == "Pain")
    assert pain["icon"] == 9001 and pain["Marge"] is not None and pain["Source du prix"]
    assert any(r["Marge"] is None for r in crafts["rows"])  # les recettes incalculables sont transmises

    trends = api.trends()
    json.dumps(trends, allow_nan=False)
    assert trends["rows"] and {"Objet", "Écart %", "Signal", "type", "category"} <= trends["rows"][0].keys()
    assert {row["category"] for row in trends["rows"]} == {"Ressources"}
    uses = {row["Objet"]: row["recipes"] for row in trends["rows"]}
    assert uses["Blé"] == 5 and uses["Pain"] == 0  # le Blé entre dans cinq recettes, le Pain dans aucune

    items = api.items()["items"]
    assert [3, "Pain (niv. 1)"[:4], 1, 9001] == [items[[i[0] for i in items].index(3)][0], "Pain", 1, 9001]


def test_item_detail(api):
    pain = api.item(3)
    json.dumps(pain, allow_nan=False)
    assert pain["name"] == "Pain" and pain["icon"] == 9001 and not pain["equipment"]
    assert pain["ref"]["price"] == 180 and len(pain["daily"]) == 12 and len(pain["hourly"]) == 20
    assert len(pain["snapshots"]) == 5 and pain["qty_24h"] == 60
    assert pain["craft"]["job"] == "Paysan" and len(pain["craft"]["ingredients"]) == 2
    assert pain["category"] == "Ressources" and pain["avg_price"]["price"] == 400 and pain["hdv_unit"] is None
    conn = api.connect()
    conn.execute("INSERT INTO hdv_listings VALUES (3, 1, 300, 2500, 0, 0, '[]', 9e9, 9e9)")
    conn.commit()
    conn.close()
    pain = api.item(3)
    assert (pain["hdv_unit"], pain["hdv_lot"]) == (250, 10)  # le lot de 10 revient à 250 l'unité, sous le prix moyen de 400
    assert (pain["ref"]["price"], pain["ref"]["lot"]) == (250, 10)
    assert pain["craft"]["ingredients"][0]["Mode"] in ("achat", "craft")
    ble = api.item(1)
    assert ble["craft"] is None and len(ble["used_in"]) == 5
    assert api.item(999_999) is None


def test_ignored_items_disappear_from_rankings(api):
    assert api.ignored()["rows"] == []
    names = lambda rows, key: {row[key] for row in rows}  # noqa: E731
    assert "Pain" in names(api.crafts()["rows"], "Objet")
    trend_item = next(row for row in api.trends()["rows"] if row["item_id"] != 3)

    assert api.set_ignored(3, True) == {"item_id": 3, "ignored": True, "count": 1}  # le Pain
    api.set_ignored(trend_item["item_id"], True)
    api.set_ignored(3, True)  # deux fois : sans effet
    assert "Pain" not in names(api.crafts()["rows"], "Objet") and len(api.crafts()["rows"]) >= 6
    assert trend_item["item_id"] not in {row["item_id"] for row in api.trends()["rows"]}
    assert api.item(3)["ignored"] is True and api.item(1)["ignored"] is (trend_item["item_id"] == 1)

    listed = api.ignored()["rows"]
    json.dumps(listed, allow_nan=False)
    assert {row["item_id"] for row in listed} == {3, trend_item["item_id"]}
    assert next(row for row in listed if row["item_id"] == 3)["name"] == "Pain"

    assert api.set_ignored(3, False)["count"] == 1
    assert "Pain" in names(api.crafts()["rows"], "Objet") and api.item(3)["ignored"] is False


def test_ignoring_a_whole_type(api):
    types = {t["name"]: t for t in api.ignored()["types"]}
    assert types["type"]["count"] >= 8 and types["Anneau"]["count"] == 1 and not types["type"]["ignored"]
    before = len(api.crafts()["rows"])
    assert before > 0 and api.trends()["rows"]

    assert api.set_type_ignored("type", True) == {"type": "type", "ignored": True, "count": 1}
    assert api.crafts()["rows"] == [] and api.trends()["rows"] == []  # toutes les recettes de la fixture sont de ce type
    detail = api.item(3)
    assert detail["type_ignored"] is True and detail["ignored"] is False
    assert next(t for t in api.ignored()["types"] if t["name"] == "type")["ignored"] is True

    api.set_type_ignored("type", False)
    assert len(api.crafts()["rows"]) == before and api.item(3)["type_ignored"] is False
    with pytest.raises(ValueError):
        api.set_type_ignored("Type inventé", True)


def test_ignored_recipe_leaves_stock_crafts(api):
    from dofustool.messages.storage import Stack, Storage

    conn = api.connect()
    db.save_holdings(conn, db.INVENTORY, Storage(0, (Stack(1, 9, False), Stack(2, 7, False), Stack(4, 1, False))), 100.0)
    conn.close()
    assert {"Pain", "Farine"} <= {row["name"] for row in api.stock_crafts()["rows"]}
    api.set_ignored(3, True)
    remaining = {row["name"] for row in api.stock_crafts()["rows"]}
    assert "Pain" not in remaining and "Farine" in remaining


def test_stock_endpoints(api):
    empty = api.stock_crafts()
    assert empty["rows"] == [] and empty["meta"] == {}
    assert api.item(3)["owned"] == {"inventory": 0, "bank": 0, "known": False}

    from dofustool.messages.storage import Stack, Storage

    conn = api.connect()
    db.save_holdings(conn, db.INVENTORY, Storage(1500, (Stack(1, 9, False), Stack(2, 7, False), Stack(500, 1, True))), 100.0)
    db.save_holdings(conn, db.BANK, Storage(900, (Stack(4, 1, False), Stack(1, 100, False))), 110.0)
    conn.close()

    stock = api.stock()
    json.dumps(stock, allow_nan=False)
    ble = next(r for r in stock["rows"] if r["name"] == "Blé")
    assert (ble["inventory"], ble["bank"], ble["total"], ble["recipes"]) == (9, 100, 109, 5)
    assert ble["value"] == ble["price"] * 109 and ble["category"] == "Ressources"
    assert not any(r["item_id"] == 500 for r in stock["rows"])  # l'anneau porté n'est pas compté
    owned = json.loads(json.dumps(api.status()))["owned"]  # badge des icônes : {item_id: [inventaire, banque]}
    assert owned["1"] == [9, 100] and owned["4"] == [0, 1] and "500" not in owned
    assert stock["meta"]["inventory"]["kamas"] == 1500 and stock["meta"]["bank"]["kamas"] == 900

    crafts = api.stock_crafts()
    json.dumps(crafts, allow_nan=False)
    pain = next(r for r in crafts["rows"] if r["name"] == "Pain")
    assert pain["craftable"] == 1 and (pain["covered"], pain["lines"]) == (2, 2)
    assert [(i["name"], i["have"], i["need"]) for i in pain["ingredients"]] == [("Farine", 7, 3), ("Eau", 1, 1)]
    farine_id = pain["ingredients"][0]["id"]
    avg, hdv, hdv_ts, hdv_lot = crafts["prices"][str(farine_id)]
    assert avg is not None and hdv is None and hdv_ts is None and hdv_lot is None  # prix moyen connu, fiche HDV jamais ouverte
    conn = api.connect()
    conn.execute("INSERT INTO hdv_listings VALUES (?, 1, 60, 500, 0, 0, '[]', 9e9, 9e9)", (farine_id,))
    conn.commit()
    conn.close()
    assert api.stock_crafts()["prices"][str(farine_id)][1:] == [50, 9e9, 10]  # meilleur prix unitaire : le lot de 10
    farine = next(r for r in crafts["rows"] if r["name"] == "Farine")
    assert farine["craftable"] == 54 and farine["total_margin"] == pytest.approx(54 * farine["margin"])

    # Le reste de l'interface en tient compte.
    assert next(r for r in api.crafts()["rows"] if r["Objet"] == "Pain")["craftable"] == 1
    detail = api.item(3)
    assert detail["craft"]["craftable"] == 1 and [row["have"] for row in detail["craft"]["ingredients"]] == [7, 1]
    assert api.item(1)["owned"] == {"inventory": 9, "bank": 100, "known": True}


def test_forge_item_and_filter(api):
    options = api.forge_options()["items"]
    assert [(o["id"], o["count"], o["template_known"], o["icon"]) for o in options] == [(500, 4, True, 9002)]
    assert options[0]["type"] == "Anneau"

    item = api.forge_item(500)
    json.dumps(item, allow_nan=False)
    assert item["template_known"] and item["tax"] == 0.02
    assert [(line["name"], line["min"], line["max"]) for line in item["lines"]] == [("PA", 1, 1), ("Vitalité", 201, 250)]
    assert [l["price"] for l in item["listings"]] == [30_000, 50_000, 90_000, 400_000]
    assert [l["label"] for l in item["listings"]] == ["ligne manquante", "de base", "jets parfaits", "exo"]
    assert item["listings"][3]["exo"] == [123] and item["listings"][3]["values"]["123"] == 15
    assert item["exos"] == [{"id": 123, "name": "Chance", "count": 1}]
    assert [l["price"] for l in item["gone"]] == [70_000]  # vue hier, absente aujourd'hui
    assert item["filter"] == {} and item["names"]["125"] == "Vitalité"
    assert item["assets"] == {} and item["order"] == [111, 123, 125]  # sans priorité connue : par identifiant
    conn = api.connect()
    conn.executemany(
        "INSERT INTO effect_meta (effect_id, priority, asset) VALUES (?, ?, ?)",
        [(125, 5000, "tx_vitality"), (123, 5700, "tx_chance"), (111, 7000, None)],
    )
    conn.commit()
    conn.close()
    api._stamp = None  # force le rechargement des métadonnées
    ordered = api.forge_item(500)
    assert [line["name"] for line in ordered["lines"]] == ["Vitalité", "PA"] and ordered["order"] == [125, 123, 111]
    assert ordered["assets"] == {"125": "tx_vitality", "123": "tx_chance"}
    assert ordered["fixed"] == [] and ordered["listings"][0]["transcended"] is False
    assert item["template"] == {"111": [1, 1], "125": [201, 250]}

    saved = api.save_forge_filter(500, {"minimums": {"125": "240"}, "exo": 123, "exo_min": 10, "inconnu": 1})
    assert saved == {"saved": {"minimums": {"125": 240}, "exo": 123, "exo_min": 10}}
    assert api.forge_item(500)["filter"] == saved["saved"]
    assert api.forge_item(999_999) is None


def test_base_effects_are_fetched_on_demand(api):
    conn = api.connect()
    conn.execute("INSERT INTO items VALUES (501, 'Cape neuve', 1, 'Cape', 100, 1, 0, 0)")
    conn.execute("INSERT INTO hdv_listings VALUES (501, 1, 80000, 0, 0, 0, '[[125, 120], [123, 9]]', 5.0, 5.0)")
    conn.commit()
    conn.close()

    def offline(item_id):
        raise OSError("hors ligne")

    api.fetch_effects = offline
    assert api.forge_item(501)["template_known"] is False  # échec : rien n'est enregistré, on pourra réessayer
    calls = []
    api.fetch_effects = lambda item_id: calls.append(item_id) or [(125, 101, 150)]
    item = api.forge_item(501)
    assert item["template_known"] and item["lines"] == [{"id": 125, "name": "Vitalité", "min": 101, "max": 150}]
    assert item["listings"][0]["label"] == "exo"
    api.forge_item(501)
    assert calls == [501]  # récupérées une seule fois, puis lues en base


def test_forge_ranking(api):
    exo = api.forge_ranking("exo", 123)
    json.dumps(exo, allow_nan=False)
    row = exo["rows"][0]
    assert row["Objet"] == "Anneau test" and row["icon"] == 9002 and row["type"] == "Anneau"
    assert row["Moins cher de base"] == 50_000 and row["Moins cher selon critère"] == 400_000
    assert row["Prime sur la base"] == 350_000 and row["Attention"] == "le moins cher a perdu : Vitalité"
    assert exo["exos"] == [{"id": 123, "name": "Chance", "count": 1}] and exo["known"] == 1
    assert api.forge_ranking("saved")["rows"][0]["Moins cher selon critère"] is None
    assert exo["lines"] == [{"id": 111, "name": "PA", "count": 1}, {"id": 125, "name": "Vitalité", "count": 1}]
    # Over : au moins N au-dessus du jet parfait. Ici aucune annonce ne dépasse 250 de Vitalité.
    over = api.forge_ranking("over", effect=125, amount=5)["rows"]
    assert len(over) == 1 and over[0]["Correspondent"] == 0 and over[0]["Moins cher selon critère"] is None
    assert api.forge_ranking("over", effect=123, amount=1)["rows"] == []  # Chance n'est pas une ligne de base


# --- serveur HTTP ------------------------------------------------------------

@pytest.fixture
def server(api, tmp_path):
    icons = tmp_path / "icons"
    icons.mkdir()
    (icons / "9001.png").write_bytes(b"\x89PNG fictif")
    (icons / "effects").mkdir()
    (icons / "effects" / "tx_vitality.png").write_bytes(b"\x89PNG vita")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api, icons))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


JSON = {"Content-Type": "application/json"}


def get(url: str):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.headers.get("Content-Type"), response.read()


def test_http_routes(server):
    status, content_type, body = get(server + "/api/status")
    assert status == 200 and content_type.startswith("application/json") and json.loads(body)["snapshots"] == 5
    assert json.loads(get(server + "/api/item/3")[2])["name"] == "Pain"
    assert json.loads(get(server + "/api/stock")[2])["rows"] == []
    assert "rows" in json.loads(get(server + "/api/stock/crafts")[2])
    assert json.loads(get(server + "/api/forge/ranking?criterion=exo&exo=123")[2])["rows"]
    assert json.loads(get(server + "/api/forge/ranking?criterion=over&effect=125&amount=2")[2])["rows"]
    status, content_type, body = get(server + "/")
    assert status == 200 and content_type.startswith("text/html") and b"Prospection" in body
    assert get(server + "/app.js")[1].startswith(("text/javascript", "application/javascript"))
    assert get(server + "/icons/9001.png")[2] == b"\x89PNG fictif"
    assert get(server + "/icons/effects/tx_vitality.png")[2] == b"\x89PNG vita"

    for path in ("/api/item/999999", "/api/inconnu", "/../config.toml", "/icons/abc.png", "/icons/effects/..%2Fx.png"):
        with pytest.raises(urllib.error.HTTPError) as error:
            get(server + path)
        assert error.value.code == 404


def test_http_save_filter(server, api):
    def post(body: bytes):
        request = urllib.request.Request(server + "/api/forge/item/500/filter", data=body, method="POST", headers=JSON)
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    assert post(b'{"minimums": {"125": 230}}') == {"saved": {"minimums": {"125": 230}, "exo": None, "exo_min": 1}}
    assert api.forge_item(500)["filter"]["minimums"] == {"125": 230}
    def post_ignore(body: bytes):
        request = urllib.request.Request(server + "/api/ignore", data=body, method="POST", headers=JSON)
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    assert post_ignore(b'{"item_id": 3, "ignored": true}')["count"] == 1
    assert [row["item_id"] for row in json.loads(get(server + "/api/ignored")[2])["rows"]] == [3]
    assert post_ignore(b'{"item_id": 3, "ignored": false}')["count"] == 0
    with pytest.raises(urllib.error.HTTPError) as error:
        post_ignore(b'{"item_id": "3"}')
    assert error.value.code == 400

    def post_type(body: bytes):
        request = urllib.request.Request(server + "/api/ignore-type", data=body, method="POST", headers=JSON)
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    assert post_type(b'{"type": "Anneau"}') == {"type": "Anneau", "ignored": True, "count": 1}
    assert post_type(b'{"type": "Anneau", "ignored": false}')["count"] == 0
    for bad in (b'{"type": 12}', b'{"type": "Inconnu"}'):
        with pytest.raises(urllib.error.HTTPError) as error:
            post_type(bad)
        assert error.value.code == 400

    for bad in (b"pas du json", b"[1, 2]", b'{"minimums": {"125": "abc"}}'):
        with pytest.raises(urllib.error.HTTPError) as error:
            post(bad)
        assert error.value.code == 400


def test_icon_cache(tmp_path, monkeypatch):
    (tmp_path / "7.png").write_bytes(b"\x89PNG en cache")
    assert fetch_icon(7, tmp_path) == b"\x89PNG en cache"  # aucun accès réseau si l'image est en cache

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"\x89PNG telecharge"

    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: calls.append(request.full_url) or Response())
    assert fetch_icon(8, tmp_path) == b"\x89PNG telecharge" and (tmp_path / "8.png").exists()
    assert fetch_icon(8, tmp_path) == b"\x89PNG telecharge" and len(calls) == 1  # téléchargée une seule fois

    def fail(request, timeout):
        raise urllib.error.URLError("hors ligne")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    assert fetch_icon(9, tmp_path) is None and not (tmp_path / "9.png").exists()


def test_harebourg_helper(api):
    empty = api.harebourg({})
    assert len(empty["layout"]) == 22 and empty["landing"] is None and empty["swap_map"] is None
    me, comte = [6, 13], [8, 13]
    out = api.harebourg({"me": me, "target": [6, 12], "comte": comte, "rotation": 90, "round": 2})
    json.dumps(out, allow_nan=False)
    assert out["shot"]["aim"] == [5, 13] and out["shot"]["clickable"]  # 90° horaire : on vise un quart de tour avant
    assert out["landing"][12][6] == [7, 13, False]
    assert out["swap"]["mover"] == "comte" and out["swap"]["destination"] == [4, 13] and out["swap"]["verdict"] == "SAFE"
    assert [8, 13] in out["mi_temps"] and out["bumped"] == 180
    with pytest.raises(ValueError):
        api.harebourg({"me": [1, "a"]})
    with pytest.raises(ValueError):
        api.harebourg({"rotation": 45})
