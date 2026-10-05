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
def api(app_db):  # noqa: F811
    conn = db.connect(app_db)
    conn.executemany("INSERT INTO item_icons VALUES (?, ?)", [(3, 9001), (500, 9002)])
    conn.commit()
    conn.close()
    return Api(app_db)


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
    assert trends["rows"] and {"Objet", "Écart %", "Signal"} <= trends["rows"][0].keys()

    items = api.items()["items"]
    assert [3, "Pain (niv. 1)"[:4], 1, 9001] == [items[[i[0] for i in items].index(3)][0], "Pain", 1, 9001]


def test_item_detail(api):
    pain = api.item(3)
    json.dumps(pain, allow_nan=False)
    assert pain["name"] == "Pain" and pain["icon"] == 9001 and not pain["equipment"]
    assert pain["ref"]["price"] == 180 and len(pain["daily"]) == 12 and len(pain["hourly"]) == 20
    assert len(pain["snapshots"]) == 5 and pain["qty_24h"] == 60
    assert pain["craft"]["job"] == "Paysan" and len(pain["craft"]["ingredients"]) == 2
    assert pain["craft"]["ingredients"][0]["Mode"] in ("achat", "craft")
    ble = api.item(1)
    assert ble["craft"] is None and len(ble["used_in"]) == 5
    assert api.item(999_999) is None


def test_forge_item_and_filter(api):
    options = api.forge_options()["items"]
    assert [(o["id"], o["count"], o["template_known"], o["icon"]) for o in options] == [(500, 4, True, 9002)]

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

    saved = api.save_forge_filter(500, {"minimums": {"125": "240"}, "exo": 123, "exo_min": 10, "inconnu": 1})
    assert saved == {"saved": {"minimums": {"125": 240}, "exo": 123, "exo_min": 10}}
    assert api.forge_item(500)["filter"] == saved["saved"]
    assert api.forge_item(999_999) is None


def test_forge_ranking(api):
    exo = api.forge_ranking("exo", 123)
    json.dumps(exo, allow_nan=False)
    row = exo["rows"][0]
    assert row["Objet"] == "Anneau test" and row["icon"] == 9002
    assert row["Moins cher de base"] == 50_000 and row["Moins cher selon critère"] == 400_000
    assert row["Prime sur la base"] == 350_000 and row["Attention"] == "le moins cher a perdu : Vitalité"
    assert exo["exos"] == [{"id": 123, "name": "Chance", "count": 1}] and exo["known"] == 1
    assert api.forge_ranking("perfect", None)["rows"][0]["Moins cher selon critère"] == 90_000
    assert api.forge_ranking("saved", None)["rows"][0]["Moins cher selon critère"] is None


# --- serveur HTTP ------------------------------------------------------------

@pytest.fixture
def server(api, tmp_path):
    icons = tmp_path / "icons"
    icons.mkdir()
    (icons / "9001.png").write_bytes(b"\x89PNG fictif")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api, icons))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(url: str):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.headers.get("Content-Type"), response.read()


def test_http_routes(server):
    status, content_type, body = get(server + "/api/status")
    assert status == 200 and content_type.startswith("application/json") and json.loads(body)["snapshots"] == 5
    assert json.loads(get(server + "/api/item/3")[2])["name"] == "Pain"
    assert json.loads(get(server + "/api/forge/ranking?criterion=exo&exo=123")[2])["rows"]
    status, content_type, body = get(server + "/")
    assert status == 200 and content_type.startswith("text/html") and b"dofustool" in body
    assert get(server + "/app.js")[1].startswith(("text/javascript", "application/javascript"))
    assert get(server + "/icons/9001.png")[2] == b"\x89PNG fictif"

    for path in ("/api/item/999999", "/api/inconnu", "/../config.toml", "/icons/abc.png"):
        with pytest.raises(urllib.error.HTTPError) as error:
            get(server + path)
        assert error.value.code == 404


def test_http_save_filter(server, api):
    def post(body: bytes):
        request = urllib.request.Request(server + "/api/forge/item/500/filter", data=body, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    assert post(b'{"minimums": {"125": 230}}') == {"saved": {"minimums": {"125": 230}, "exo": None, "exo_min": 1}}
    assert api.forge_item(500)["filter"]["minimums"] == {"125": 230}
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
