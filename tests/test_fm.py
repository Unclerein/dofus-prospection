"""Journal de forgemagie et ventes hors ligne : décodage, enregistrement, dossiers. Données synthétiques uniquement."""
import json
import logging
import time

import pytest

from dofustool import db
from dofustool.analysis import fmjournal
from dofustool.archive import Archive
from dofustool.capture.pipeline import Pipeline
from dofustool.db.backfill import backfill
from dofustool.messages import Mapping, fm
from dofustool.messages.fm import POOL_DOWN, POOL_SAME, POOL_UP, Object, Result
from dofustool.messages.sales import Sale, SalesList
from dofustool.messages.trades import PURCHASE, SALE, Trade
from dofustool.web.api import Api

from .test_app import app_db  # noqa: F401  (fixture : une base complète sur disque)
from .test_identify import BUILDS, fm_object_body, fm_result_body
from .test_pipeline import any_frame, feed
from .test_protocol import framed, ld, vi

PLACED = Mapping("obj", BUILDS[0]["fm_object"])
RESULT = Mapping("res", BUILDS[0]["fm_result"])
OFFLINE = Mapping("off", BUILDS[0]["offline_sales"])
RUNE, OTHER_RUNE, BOOTS = 3907, 3908, 1500
UID = 3_400_001
START = ((125, 366), (119, 40), (112, 5))


def test_parse_object_and_result_read_numbers_only():
    rune = fm.parse_object(fm_object_body(PLACED.fields), PLACED)
    assert rune == Object(2_500_001, RUNE, 1, ((115, 1),))
    # Plusieurs runes posées d'un coup : un champ annexe de plus, qui ne fait pas refuser l'objet.
    assert fm.parse_object(vi(9, 3) + fm_object_body(PLACED.fields), PLACED) == rune
    result = fm.parse_result(fm_result_body(RESULT.fields, passed=False, pool=4.5, change=POOL_UP), RESULT)
    assert result == Result(False, 4.5, POOL_UP, Object(UID, BOOTS, 1, ((125, 366), (119, 40), (112, 5), (115, 3))))
    assert fm.parse_result(fm_result_body(RESULT.fields, pool=0, change=POOL_SAME), RESULT).pool == 0.0  # puits vide : non transmis
    # Un effet qui porte du texte (« modifié par ») n'est gardé que par son identifiant.
    f = RESULT.fields
    signed = vi(f["quantity"], 1) + vi(f["item_id"], BOOTS) + vi(f["uid"], UID) + ld(f["effects"], vi(f["effect_id"], 985) + ld(9, b"Nom-De-Joueur"))
    body = vi(f["status"], 2) + ld(f["result"], vi(f["pool_change"], 0) + ld(f["object"], signed))
    assert fm.parse_result(body, RESULT).object.effects == ((985, None),)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"\xff",
        vi(1, 3) + ld(2, vi(3, 0)),  # état inconnu, pas d'objet
        vi(1, 2) + ld(2, vi(3, 7) + ld(4, vi(3, 1) + vi(4, BOOTS) + vi(5, UID))),  # sens de variation impossible
        vi(1, 2) + ld(2, vi(3, 0) + ld(4, vi(3, 100) + vi(4, 3_950))),  # fabrication de runes : pas d'identifiant d'exemplaire
        vi(1, 2) + ld(2, vi(3, 0) + ld(4, vi(4, BOOTS) + vi(5, UID)) + ld(8, b"x")),  # sous-message inconnu
    ],
)
def test_parse_result_refuses_other_shapes(body):
    assert fm.parse_result(body, RESULT) is None


def test_parse_offline_total():
    assert fm.parse_offline_total(vi(1, 6_400), OFFLINE) == 6_400
    assert fm.parse_offline_total(b"", OFFLINE) == 0  # remis à zéro
    assert fm.parse_offline_total(vi(2, 5), OFFLINE) is None and fm.parse_offline_total(ld(1, b"x"), OFFLINE) is None


def result(passed, effects, pool=0.0, change=POOL_SAME, uid=UID):
    return Result(passed, pool, change, Object(uid, BOOTS, 1, tuple(effects)))


def test_passes_build_a_dossier():
    conn = db.connect(":memory:")
    step1 = ((125, 366), (119, 43), (112, 5))  # la rune passe, rien ne baisse
    step2 = ((125, 350), (119, 46), (112, 5))  # elle passe, la vitalité baisse
    assert db.save_fm_pass(conn, 10, 1000.0, result(True, step1), RUNE, START)
    assert db.save_fm_pass(conn, 11, 1001.0, result(True, step2, 4.0, POOL_UP), RUNE, step1)
    assert db.save_fm_pass(conn, 12, 1002.0, result(False, step2, 4.0), OTHER_RUNE, step2)  # échec sans perte
    assert db.save_fm_pass(conn, 13, 5000.0, result(True, step2, 1.0, POOL_DOWN), OTHER_RUNE, None)  # autre séance, état d'avant non vu
    assert not db.save_fm_pass(conn, 13, 5000.0, result(True, step2), OTHER_RUNE, None)  # même message rejoué
    assert db.save_fm_pass(conn, 5, 900.0, result(False, START), RUNE, START)  # rejeu d'un passage plus ancien
    (dossier,) = fmjournal.load(conn)
    assert (dossier.passes, dossier.sc, dossier.sn, dossier.ec) == (5, 1, 2, 2)
    assert dossier.before == dict(START) and dossier.after == dict(step2) and dossier.pool == 1.0
    assert dossier.duration_s == pytest.approx(102.0)  # 900 -> 1002 ; la séance suivante ne s'ajoute pas
    assert {r: (l.count, l.sc, l.sn, l.ec) for r, l in dossier.runes.items()} == {RUNE: (3, 1, 1, 1), OTHER_RUNE: (2, 0, 1, 1)}
    assert dossier.rune_cost == 0 and dossier.unpriced == 5

    # Le prix se fige une fois : un passage déjà chiffré garde son prix quand le marché bouge.
    assert fmjournal.freeze_prices(conn, {RUNE: 1_000.0}.get) == 1
    assert fmjournal.freeze_prices(conn, {RUNE: 9_999.0, OTHER_RUNE: 200.0}.get) == 1
    (dossier,) = fmjournal.load(conn)
    assert dossier.rune_cost == 3 * 1_000 + 2 * 200 and dossier.unpriced == 0
    conn.close()


def test_base_cost_and_real_rune_cost_come_from_my_purchases():
    conn = db.connect(":memory:")
    db.save_fm_pass(conn, 10, 100_000.0, result(True, START), RUNE, START)
    db.save_fm_pass(conn, 11, 100_001.0, result(True, START), OTHER_RUNE, START)
    (dossier,) = fmjournal.load(conn)
    assert fmjournal.base_purchase(conn, dossier) is None and fmjournal.real_rune_cost(dossier, {}) is None
    db.save_trade(conn, 1, Trade(PURCHASE, RUNE, 100, 150_000, 77), 90_000.0)
    db.save_trade(conn, 2, Trade(PURCHASE, RUNE, 10, 26_000, 78), 91_000.0)
    units = fmjournal.purchase_units(conn)
    assert units == {RUNE: pytest.approx(1_600.0)}
    assert fmjournal.real_rune_cost(dossier, units) == (pytest.approx(1_600.0), 1)  # l'autre rune n'a pas été achetée
    # Un achat récent du même modèle, puis l'achat de cet exemplaire précis, qui prime.
    db.save_trade(conn, 3, Trade(PURCHASE, BOOTS, 1, 400_000, 999), 99_000.0)
    assert fmjournal.base_purchase(conn, dossier) == (400_000, "model")
    db.save_trade(conn, 4, Trade(PURCHASE, BOOTS, 1, 350_000, UID), 95_000.0)
    assert fmjournal.base_purchase(conn, dossier) == (350_000, "purchase")
    conn.close()


def test_a_forged_object_is_followed_until_its_sale():
    conn = db.connect(":memory:")
    db.save_fm_pass(conn, 10, 1000.0, result(True, START), RUNE, START)
    db.save_sales(conn, SalesList(11, (Sale(1, 7_300, 10, 5_973, 9_000),)), 1100.0)
    db.save_lot_update(conn, Sale(UID, BOOTS, 1, 2_500_000, 2_419_200), 1200.0)  # mis en vente
    db.save_lot_update(conn, Sale(UID, BOOTS, 1, 2_300_000, 2_419_200), 1300.0)  # prix baissé
    assert conn.execute("SELECT listed_price, listed_at, sold_at FROM fm_items").fetchone() == (2_300_000, 1200.0, None)
    assert db.save_trade(conn, 50, Trade(SALE, BOOTS, 1, 2_300_000), 1400.0)
    assert conn.execute("SELECT sold_price, sold_at FROM fm_items").fetchone() == (2_300_000, 1400.0)
    assert conn.execute("SELECT ref FROM trades WHERE source_id = 50").fetchone() == (UID,)
    conn.close()


def lots(conn):
    return [row[0] for row in conn.execute("SELECT uid FROM my_sales ORDER BY uid")]


def test_offline_sales_are_matched_to_the_lots_that_left():
    conn = db.connect(":memory:")
    assert db.save_offline_total(conn, 100, 150_000, 1000.0) == 0  # première annonce : point de départ
    listing = (Sale(1, 1_601, 1, 1_900, 9_000), Sale(2, 1_601, 1, 1_900, 9_000), Sale(3, 1_602, 1, 2_600, 9_000), Sale(4, 7_300, 10, 5_973, 9_000))
    db.save_sales(conn, SalesList(11, listing), 1100.0)
    db.save_fm_pass(conn, 10, 1150.0, result(True, START, uid=3), RUNE, START)  # le lot 3 est un objet forgemagé
    assert db.save_offline_total(conn, 101, 150_000, 2000.0) == 0  # rien de neuf
    assert db.save_offline_total(conn, 102, 0, 2500.0) == 0  # kamas retirés de la banque
    assert db.save_offline_total(conn, 103, 3_800, 3000.0) == 3_800
    assert db.save_offline_total(conn, 103, 3_800, 3000.0) == 0  # même message rejoué
    assert db.save_offline_total(conn, 104, 6_400, 4000.0) == 2_600
    db.save_sales(conn, SalesList(11, (listing[3],)), 4100.0)
    sold = conn.execute("SELECT ts, item_id, quantity, price, ref FROM trades WHERE source_id < 0 ORDER BY ts, ref").fetchall()
    # Chaque vente est datée de la connexion qui l'a annoncée.
    assert sold == [(3000.0, 1_601, 1, 1_900, 1), (3000.0, 1_601, 1, 1_900, 2), (4000.0, 1_602, 1, 2_600, 3)]
    assert conn.execute("SELECT COUNT(*) FROM offline_sales WHERE attributed = 0").fetchone()[0] == 0
    assert conn.execute("SELECT sold_price, sold_at FROM fm_items WHERE uid = 3").fetchone() == (2_600, 4000.0)
    conn.close()


def test_offline_sales_stay_unexplained_when_the_lots_do_not_add_up():
    conn = db.connect(":memory:")
    db.save_offline_total(conn, 100, 0, 1000.0)
    listing = (Sale(1, 1_601, 1, 2_000, 9_000), Sale(2, 1_602, 1, 3_000, 9_000), Sale(3, 7_300, 1, 5_000, 9_000), Sale(4, 7_301, 1, 900, 9_000))
    db.save_sales(conn, SalesList(11, listing), 1100.0)
    db.save_offline_total(conn, 101, 5_000, 2000.0)
    # Trois lots partis : 2 000 + 3 000 font 5 000, le lot à 5 000 aussi. Deux réponses : aucune n'est retenue.
    db.save_sales(conn, SalesList(11, (listing[3],)), 2100.0)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    assert conn.execute("SELECT attributed FROM offline_sales WHERE source_id = 101").fetchone() == (0,)
    # Un lot retiré à la main sans vente annoncée ne devient jamais une vente.
    db.save_sales(conn, SalesList(11, ()), 2200.0)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    # Une vente en ligne retire son lot avant le relevé : elle n'est pas comptée une seconde fois.
    db.save_sales(conn, SalesList(22, (Sale(9, 500, 1, 700, 9_000), Sale(10, 501, 1, 700, 9_000))), 3000.0)
    db.save_trade(conn, 60, Trade(SALE, 500, 1, 700), 3100.0)
    db.save_offline_total(conn, 105, 5_700, 3200.0)
    db.save_sales(conn, SalesList(22, ()), 3300.0)
    assert conn.execute("SELECT item_id FROM trades WHERE source_id < 0").fetchall() == [(501,)]
    conn.close()


def test_pipeline_records_passes_and_backfill_does_not_double_them(caplog, monkeypatch):
    market = db.connect(":memory:")
    market.execute("INSERT INTO items VALUES (?, 'Geta', 1, 'Bottes', 1, 1, 0, 0)", (BOOTS,))
    market.execute("INSERT INTO items VALUES (?, 'Rune Vi', 1, 'Rune de forgemagie', 1, 1, 0, 2)", (RUNE,))
    keymap = {"fm_object": PLACED, "fm_result": RESULT, "offline_sales": OFFLINE}
    pipeline = Pipeline(Archive(":memory:"), market, dict(keymap))
    after = ((125, 350), (119, 43), (112, 5))
    stream = (
        framed(any_frame("off", vi(1, 4_000)))
        + framed(any_frame("obj", fm_object_body(PLACED.fields, item=BOOTS, uid=UID, effects=START)))  # l'équipement posé
        + framed(any_frame("res", fm_result_body(RESULT.fields, effects=START)))  # résultat sans rune posée : autre fabrication
        + framed(any_frame("obj", fm_object_body(PLACED.fields)))
        + framed(any_frame("res", fm_result_body(RESULT.fields, pool=3.0, change=POOL_UP, effects=after)))
        + framed(any_frame("obj", fm_object_body(PLACED.fields)))
        + framed(any_frame("res", fm_result_body(RESULT.fields, passed=False, pool=3.0, change=POOL_SAME, effects=after)))
        + framed(any_frame("off", vi(1, 4_500)))
    )
    with caplog.at_level(logging.INFO, logger="dofustool.capture"):
        feed(pipeline, stream)
    assert [r.getMessage() for r in caplog.records] == [
        "Forgemagie : premier passage de rune sur Geta.",
        "Ventes hors ligne : 500 kamas depuis la dernière annonce.",
    ]
    assert market.execute("SELECT rune_id, passed, lost, pool, pool_change FROM fm_passes ORDER BY source_id").fetchall() == [
        (RUNE, 1, 1, 3.0, POOL_UP),
        (RUNE, 0, 0, 3.0, POOL_SAME),
    ]
    (dossier,) = fmjournal.load(market)
    assert (dossier.uid, dossier.before, dossier.after) == (UID, dict(START), dict(after))

    pipeline.archive.commit()
    monkeypatch.setattr("dofustool.db.backfill.load_keymap", lambda: {"avg_prices": Mapping("zzz", {"entries": 1, "item_id": 3, "price": 5}), **keymap})
    assert backfill(pipeline.archive.db, market)["passages de rune ajoutés"] == 0
    fresh = db.connect(":memory:")
    fresh.execute("INSERT INTO items VALUES (?, 'Rune Vi', 1, 'Rune de forgemagie', 1, 1, 0, 2)", (RUNE,))
    assert backfill(pipeline.archive.db, fresh)["passages de rune ajoutés"] == 2
    market.close()
    fresh.close()


def test_journal_is_served(app_db):  # noqa: F811
    conn = db.connect(app_db)
    now = time.time()
    # Dans la base de test : l'objet 500 est un équipement, l'objet 1 (Blé) joue la rune.
    forged = Result(True, 2.0, POOL_UP, Object(UID, 500, 1, ((125, 380), (119, 40))))
    db.save_fm_pass(conn, 10, now - 600, forged, 1, ((125, 366), (119, 43)))
    db.save_fm_pass(conn, 11, now - 590, Result(False, 2.0, POOL_SAME, forged.object), 1, forged.object.effects)
    db.save_trade(conn, 1, Trade(PURCHASE, 1, 100, 900, 42), now - 7_200)
    conn.close()
    api = Api(app_db)
    stamp = api.version()["stamp"]
    data = api.forge_journal()
    json.dumps(data, allow_nan=False)
    (d,) = data["dossiers"]
    assert (d["uid"], d["passes"], d["sc"], d["sn"], d["ec"], d["status"]) == (UID, 2, 0, 1, 1, "kept")
    assert d["real_cost"] == pytest.approx(18.0) and d["real_passes"] == 2
    assert [(l["before"], l["after"]) for l in d["lines"]] and {(l["id"], l["before"], l["after"]) for l in d["lines"]} == {(125, 366, 380), (119, 43, 40)}
    assert d["runes"][0]["count"] == 2 and data["runes"][0]["count"] == 2 and d["runes"][0]["bought"] == pytest.approx(9.0)
    priced = d["rune_cost"]
    assert d["unpriced"] == 0 and priced > 0

    assert api.set_forge_base_cost(UID, 1_000_000) == {"uid": UID, "base_cost": 1_000_000}
    conn = db.connect(app_db)
    db.save_sales(conn, SalesList(11, (Sale(UID, 500, 1, 3_000_000, 86_400),)), now - 60)
    conn.close()
    assert api.version()["stamp"] != stamp
    (d,) = api.forge_journal()["dossiers"]
    assert (d["status"], d["sale"], d["base_cost"], d["base_source"]) == ("listed", 3_000_000, 1_000_000, "manual")
    assert d["margin"] == pytest.approx(3_000_000 * (1 - data["tax"]) - 1_000_000 - priced)
    conn = db.connect(app_db)
    db.save_trade(conn, 2, Trade(SALE, 500, 1, 3_000_000), now)
    conn.close()
    (d,) = api.forge_journal()["dossiers"]
    assert d["status"] == "sold" and d["sale_ts"] == pytest.approx(now)
    sales = api.sales()
    assert sales["offline_pending"]["amount"] == 0 and sales["trades"][0]["offline"] is False
