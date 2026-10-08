"""Échanges entre joueurs et fabrications : décodage, suivi, journal. Données synthétiques uniquement."""
import json
import logging
import time

from dofustool import db
from dofustool.archive import Archive
from dofustool.capture.pipeline import Pipeline
from dofustool.db.backfill import backfill
from dofustool.messages import Mapping, exchange, fm
from dofustool.messages.fm import Object
from dofustool.web.api import Api

from .test_app import app_db  # noqa: F401  (fixture : une base complète sur disque)
from .test_fm import PLACED, RESULT
from .test_identify import fm_object_body, fm_result_body
from .test_pipeline import any_frame, feed
from .test_protocol import framed, ld, vi

PLACE = Mapping("obj", {**PLACED.fields, "remote": 1})
KEYMAP = {
    "exchange_started": Mapping("beg", {}),
    "fm_object": PLACE,
    "fm_result": RESULT,
    "exchange_modified": Mapping("chg", PLACED.fields),
    "exchange_removed": Mapping("del", {"remote": 2, "uid": 3}),
    "exchange_kamas": Mapping("kam", {"amount": 1, "remote": 3}),
    "exchange_closed": Mapping("end", {"success": 2}),
}
RING, RUNE, WHEAT = 1500, 3907, 289


def placed(objects, remote=False):
    """Objets posés dans l'échange : (identifiant, objet, quantité)."""
    f = PLACE.fields
    body = vi(f["remote"], 1) if remote else b""
    for uid, item, quantity in objects:
        obj = vi(f["quantity"], quantity) + vi(f["item_id"], item) + vi(f["uid"], uid)
        body += ld(f["wrap"], ld(f["object"], obj) + vi(9, 63))
    return body


def test_parse_what_each_side_puts_down():
    assert exchange.parse_placed(placed([(7, RING, 1)]), PLACE) == (False, [(7, RING, 1)])
    assert exchange.parse_placed(placed([(8, RUNE, 219), (9, WHEAT, 40)], remote=True), PLACE) == (True, [(8, RUNE, 219), (9, WHEAT, 40)])
    assert exchange.parse_placed(b"", PLACE) is None and exchange.parse_placed(ld(8, b"x"), PLACE) is None
    assert exchange.parse_flagged(vi(1, 1_500_000) + vi(3, 1), KEYMAP["exchange_kamas"], "amount") == (1_500_000, True)
    assert exchange.parse_flagged(b"", KEYMAP["exchange_kamas"], "amount") == (0, False)  # somme retirée
    assert exchange.parse_flagged(vi(2, 1) + vi(3, 8), KEYMAP["exchange_removed"], "uid") == (8, True)
    assert exchange.parse_closed(vi(1, 11) + vi(2, 1), KEYMAP["exchange_closed"]) is True
    assert exchange.parse_closed(vi(1, 11), KEYMAP["exchange_closed"]) is False


def test_tracker_keeps_the_final_offer_only():
    tracker = exchange.Tracker()
    tracker.place(False, [(1, RING, 1)])  # rien n'est ouvert : ignoré
    assert tracker.close(True) is None
    tracker.start()
    tracker.kamas(4_000_000, False)
    tracker.kamas(0, False)  # somme retirée avant de valider
    tracker.place(False, [(1, RING, 1)])
    tracker.place(True, [(8, RUNE, 219), (9, WHEAT, 40)])
    tracker.remove(9, True)
    tracker.kamas(200_000, True)
    done = tracker.close(True)
    assert (done.kamas_given, done.kamas_received, done.given, done.received) == (0, 200_000, {1: (RING, 1)}, {8: (RUNE, 219)})
    tracker.start()
    tracker.kamas(5, True)
    assert tracker.close(False) is None  # annulé
    tracker.start()
    assert tracker.close(True) is None  # conclu sans rien : pas noté


def test_pipeline_logs_exchanges_and_crafts_without_mixing_them_with_forging(caplog, monkeypatch):
    market = db.connect(":memory:")
    market.execute("INSERT INTO items VALUES (?, 'Anneau', 1, 'Anneau', 1, 1, 0, 0)", (RING,))
    market.execute("INSERT INTO items VALUES (?, 'Rune Vi', 1, 'Rune de forgemagie', 1, 1, 0, 2)", (RUNE,))
    pipeline = Pipeline(Archive(":memory:"), market, dict(KEYMAP))
    f = RESULT.fields
    crafted = vi(f["quantity"], 1) + vi(f["item_id"], RING) + vi(f["uid"], 555)
    craft = vi(f["status"], 2) + ld(f["result"], ld(f["object"], crafted))  # ni puits ni variation : une fabrication
    stream = (
        framed(any_frame("beg", vi(1, 5_675)))
        + framed(any_frame("obj", placed([(8, RUNE, 219)], remote=True)))  # une rune posée par l'autre : pas un passage
        + framed(any_frame("kam", vi(1, 1_500_000) + vi(3, 1)))
        + framed(any_frame("obj", placed([(1, RING, 1)])))
        + framed(any_frame("end", vi(1, 11) + vi(2, 1)))
        + framed(any_frame("end", vi(1, 11)))  # fermeture d'un atelier, hors échange : ignorée
        + framed(any_frame("res", craft))
        + framed(any_frame("obj", fm_object_body(PLACED.fields)))  # puis un vrai passage de rune
        + framed(any_frame("res", fm_result_body(RESULT.fields, pool=2.0, change=1)))
    )
    with caplog.at_level(logging.INFO, logger="dofustool.capture"):
        feed(pipeline, stream)
    assert [r.getMessage() for r in caplog.records] == [
        "Échange conclu : 1 objets donnés, 1 reçus, 0 kamas donnés, 1500000 reçus.",
        "Fabrication : Anneau x1.",
        "Forgemagie : premier passage de rune sur Anneau.",
    ]
    assert market.execute("SELECT kamas_given, kamas_received FROM exchanges").fetchall() == [(0, 1_500_000)]
    assert market.execute("SELECT received, item_id, quantity FROM exchange_items ORDER BY received").fetchall() == [(0, RING, 1), (1, RUNE, 219)]
    assert market.execute("SELECT item_id, quantity, uid FROM crafts").fetchall() == [(RING, 1, 555)]
    assert market.execute("SELECT COUNT(*) FROM fm_passes").fetchone()[0] == 1

    pipeline.archive.commit()
    monkeypatch.setattr("dofustool.db.backfill.load_keymap", lambda: {"avg_prices": Mapping("zzz", {"entries": 1, "item_id": 3, "price": 5}), **KEYMAP})
    counts = backfill(pipeline.archive.db, market)
    assert (counts["échanges ajoutés"], counts["fabrications ajoutées"], counts["passages de rune ajoutés"]) == (0, 0, 0)
    fresh = db.connect(":memory:")
    fresh.execute("INSERT INTO items VALUES (?, 'Rune Vi', 1, 'Rune de forgemagie', 1, 1, 0, 2)", (RUNE,))
    counts = backfill(pipeline.archive.db, fresh)
    assert (counts["échanges ajoutés"], counts["fabrications ajoutées"], counts["passages de rune ajoutés"]) == (1, 1, 1)
    market.close()
    fresh.close()


def test_a_forged_result_is_not_a_craft():
    assert fm.parse_result(fm_result_body(RESULT.fields, pool=0, change=0), RESULT).forged  # puits vide, variation nulle : forgemagie
    f = RESULT.fields
    body = vi(f["status"], 2) + ld(f["result"], ld(f["object"], vi(f["quantity"], 3) + vi(f["item_id"], WHEAT) + vi(f["uid"], 9)))
    plain = fm.parse_result(body, RESULT)
    assert not plain.forged and plain.object == Object(9, WHEAT, 3, ())


def test_exchanges_and_crafts_are_served(app_db):  # noqa: F811
    conn = db.connect(app_db)
    now = time.time()
    done = exchange.Exchange(kamas_given=100, kamas_received=0, given={1: (3, 10)}, received={2: (1, 50), 3: (1, 50)})
    assert db.save_exchange(conn, 10, now - 60, done) and not db.save_exchange(conn, 10, now - 60, done)
    assert db.save_craft(conn, 11, now - 30, Object(9, 3, 4, ())) and not db.save_craft(conn, 11, now - 30, Object(9, 3, 4, ()))
    conn.close()
    api = Api(app_db)
    data = api.sales()
    json.dumps(data, allow_nan=False)
    (x,) = data["exchanges"]
    assert (x["kamas_given"], [(r["name"], r["quantity"]) for r in x["given"]], [(r["name"], r["quantity"]) for r in x["received"]]) == (
        100, [("Pain", 10)], [("Blé", 100)],
    )  # fmt: skip
    assert x["value_received"] > 0 and x["value_given"] > 0
    (c,) = data["crafts"]
    assert (c["name"], c["quantity"]) == ("Pain", 4) and c["cost"] is not None and c["value"] is not None
