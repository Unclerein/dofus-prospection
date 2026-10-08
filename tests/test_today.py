"""Page Aujourd'hui et almanax. Données synthétiques uniquement, aucun appel réseau."""
import json
import time
from datetime import date, timedelta

from dofustool import db
from dofustool.messages.sales import Sale, SalesList
from dofustool.messages.trades import PURCHASE, SALE, Trade
from dofustool.staticdata import almanax
from dofustool.web.api import Api

from .test_app import app_db  # noqa: F401  (fixture : une base complète sur disque)


def test_almanax_payload_keeps_names_numbers_and_plain_text():
    payload = {
        "name": {"fr": "Gibier abondant", "en": "Plenty of game"},
        "desc": {"fr": "Les quantités de viande des <b>Chasseurs</b> sont augmentées de 100 %."},
        "itemIds": [3], "quantities": [5],
        "items": [{"id": 3, "name": {"fr": "Pain"}, "iconId": 12}],
    }  # fmt: skip
    assert almanax.parse(payload) == {
        "name": "Gibier abondant",
        "desc": "Les quantités de viande des Chasseurs sont augmentées de 100 %.",
        "items": [[3, 5, "Pain"]],
    }
    assert almanax.parse({}) == {"name": "", "desc": "", "items": []}


def test_almanax_is_fetched_once_per_day_and_priced(app_db):  # noqa: F811
    api = Api(app_db)
    asked = []

    def fetch(day):
        asked.append(day)
        return {"name": "Gibier abondant", "desc": "Viande doublée.", "items": [[3, 5, "Pain"]]}

    api.fetch_almanax = fetch
    today = date.today()
    first = api.almanax()
    json.dumps(first, allow_nan=False)
    assert (first["day"], first["available"], first["name"]) == (today.isoformat(), True, "Gibier abondant")
    assert first["previous"] == (today - timedelta(days=1)).isoformat() and first["next"] == (today + timedelta(days=1)).isoformat()
    (offering,) = first["items"]
    assert (offering["name"], offering["quantity"]) == ("Pain", 5) and offering["cost"] == offering["unit"] * 5 == first["cost"]

    def broken(day):
        raise OSError("hors ligne")

    api.fetch_almanax = broken
    assert api.almanax(today.isoformat())["name"] == "Gibier abondant" and asked == [today]  # relu dans la base
    tomorrow = api.almanax(first["next"])
    assert tomorrow == {"day": first["next"], "today": today.isoformat(), "previous": today.isoformat(),
                        "next": (today + timedelta(days=2)).isoformat(), "available": False}  # fmt: skip
    assert api.almanax("n'importe quoi")["day"] == today.isoformat() and api.almanax("1999-01-01")["day"] == today.isoformat()


def test_today_gathers_what_needs_attention_and_what_happened(app_db):  # noqa: F811
    conn = db.connect(app_db)
    now = time.time()
    # Deux lots de Blé : l'un sous-enchéri (l'HDV le vend 8 les 100), l'autre proche de l'expiration.
    db.save_sales(conn, SalesList(11, (Sale(1, 1, 100, 900, 20 * 86_400), Sale(2, 3, 1, 250, 3_600))), now - 60)
    conn.execute("INSERT INTO hdv_listings VALUES (1, 77, 12, 0, 800, 0, '[]', ?, ?)", (now, now))
    conn.commit()
    db.save_trade(conn, 1, Trade(SALE, 3, 10, 2_500), now - 3_600)
    db.save_trade(conn, 2, Trade(PURCHASE, 1, 100, 900), now - 7_200)
    db.save_trade(conn, 3, Trade(SALE, 1, 1, 12), now - 5 * 86_400)  # avant la dernière visite
    conn.close()

    data = Api(app_db).today(now - 86_400)
    json.dumps(data, allow_nan=False)
    todo = data["todo"]
    assert todo["undercut"]["count"] == 1 and todo["undercut"]["amount"] == 900 and todo["undercut"]["rows"][0]["name"] == "Blé"
    assert todo["expiring"]["count"] == 1 and todo["expiring"]["rows"][0]["name"] == "Pain"
    assert todo["capture"]["running"] is False
    recent = data["recent"]
    assert (recent["sold"]["count"], recent["sold"]["amount"], recent["sold"]["offline"], recent["sold"]["top"]["name"]) == (1, 2_500, 0, "Pain")
    assert (recent["bought"]["count"], recent["bought"]["amount"], recent["bought"]["top"]["name"]) == (1, 900, "Blé")
    assert recent["forged"] == {"passes": 0, "cost": 0, "objects": 0, "top": None}
    assert data["quality"]["unpriced_lots"] == 1 and data["quality"]["sales_known"]
    # Sans date de dernière visite : les dernières 24 heures. Une date trop ancienne est ramenée à 30 jours.
    assert Api(app_db).today()["recent"]["sold"]["count"] == 1
    assert Api(app_db).today(0)["recent"]["sold"]["count"] == 2
