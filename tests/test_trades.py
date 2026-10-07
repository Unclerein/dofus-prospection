"""Ventes conclues, achats et lots modifiés : décodage, enregistrement, journal. Données synthétiques uniquement."""
import json
import logging
import time

import pytest

from dofustool import db
from dofustool.archive import Archive
from dofustool.capture.pipeline import Pipeline
from dofustool.db.backfill import backfill
from dofustool.messages import Mapping, sales, trades
from dofustool.messages.sales import Sale, SalesList
from dofustool.messages.trades import PURCHASE, SALE, Trade
from dofustool.protocol.session import Message
from dofustool.protocol.tcp import S2C
from dofustool.web.api import Api

from .test_app import app_db  # noqa: F401  (fixture : une base complète sur disque)
from .test_pipeline import any_frame, feed
from .test_protocol import framed, ld, vi

TEXT = Mapping("txt", {"id": 2, "params": 4})
UPDATE = Mapping("upd", {"price": 1, "ref": 2, "remaining": 3, "item_id": 1, "uid": 3, "lot": 4})


def text(ident: int, *params) -> bytes:
    return vi(2, ident) + b"".join(ld(4, str(p).encode()) for p in params)


def update(uid: int, item: int, lot: int, price: int, effects: bytes = b"") -> bytes:
    return vi(1, price) + ld(2, vi(1, item) + effects + vi(3, uid) + vi(4, lot)) + vi(3, 2_419_200)


def test_parse_sale_and_purchase_texts():
    assert trades.parse_text(text(65, 248_204, 21_209, 21_209, 10), TEXT) == Trade(SALE, 21_209, 10, 248_204)
    assert trades.parse_text(text(252, 11_653, 3_602_459, 100, 174_800), TEXT) == Trade(PURCHASE, 11_653, 100, 174_800)
    assert trades.read_text(text(36, "Un-Nom", 12), TEXT) == (36, [None, 12])  # un paramètre non numérique n'est jamais lu


@pytest.mark.parametrize(
    "body",
    [
        text(89),  # autre texte, sans paramètre
        text(21, 1, 23_726),  # autre texte
        text(65, 248_204, 21_209, 99, 10),  # les deux mentions de l'objet diffèrent
        text(65, 248_204, 21_209, 21_209, 7),  # taille de lot impossible
        text(65, "prix", 21_209, 21_209, 10),  # paramètre non numérique
        text(252, 11_653, 3_602_459, 100),  # il manque un paramètre
        text(65, 0, 21_209, 21_209, 10),
        ld(4, b"65"),  # pas de numéro de texte
        vi(2, 65) + ld(9, b"x"),  # champ inattendu
        b"\xff",
    ],
)
def test_parse_text_ignores_everything_else(body):
    assert trades.parse_text(body, TEXT) is None


def test_parse_lot_update_reads_numbers_only():
    assert trades.parse_lot_update(update(857_144, 22_219, 1, 259_999), UPDATE) == Sale(857_144, 22_219, 1, 259_999, 2_419_200)
    # Un lot d'équipement porte ses effets, dont un nom de joueur : ils ne font ni refuser ni lire le lot.
    worn = update(494_120, 18_018, 1, 6_500_000, effects=ld(2, vi(1, 985) + ld(9, b"Nom-De-Joueur")))
    assert trades.parse_lot_update(worn, UPDATE) == Sale(494_120, 18_018, 1, 6_500_000, 2_419_200)
    assert trades.parse_lot_update(update(1, 22_219, 3, 100), UPDATE) is None  # taille de lot impossible
    assert trades.parse_lot_update(vi(1, 100) + vi(3, 5), UPDATE) is None
    # La liste complète des lots accepte elle aussi un lot d'équipement.
    listing = Mapping("lst", {"entries": 2, "ref": 1, "uid": 1, "item_id": 2, "lot": 3, "price": 2, "remaining": 3})
    entry = ld(2, ld(1, vi(1, 7) + vi(2, 18_018) + ld(9, b"effets") + vi(3, 1)) + vi(2, 6_500_000) + vi(3, 5_000))
    assert sales.parse(entry, listing).sales == (Sale(7, 18_018, 1, 6_500_000, 5_000),)


def test_a_sale_removes_one_matching_lot_and_is_counted_once():
    conn = db.connect(":memory:")
    lots = (Sale(1, 21_209, 10, 248_204, 9_000), Sale(2, 21_209, 10, 248_204, 5_000), Sale(3, 21_209, 10, 250_000, 100), Sale(4, 7_300, 10, 5_973, 800))
    db.save_sales(conn, SalesList(11, lots), 1_000.0)
    sold = Trade(SALE, 21_209, 10, 248_204)
    assert db.save_trade(conn, 501, sold, 2_000.0)
    assert not db.save_trade(conn, 501, sold, 2_000.0)  # même message rejoué : rien de plus
    # Des deux lots identiques, celui qui expire le plus tôt est parti ; le lot à un autre prix reste.
    assert [row[0] for row in conn.execute("SELECT uid FROM my_sales ORDER BY uid")] == [1, 3, 4]
    assert db.save_trade(conn, 502, sold, 2_001.0)
    assert db.save_trade(conn, 503, sold, 2_002.0)  # troisième vente identique : plus aucun lot à retirer, mais elle compte
    assert [row[0] for row in conn.execute("SELECT uid FROM my_sales ORDER BY uid")] == [3, 4]
    # Un achat ne touche pas à mes lots. Une vente antérieure au relevé ne retire rien : le relevé en tient déjà compte.
    assert db.save_trade(conn, 504, Trade(PURCHASE, 7_300, 10, 5_973), 2_003.0)
    assert db.save_trade(conn, 400, Trade(SALE, 7_300, 10, 5_973), 900.0)
    assert [row[0] for row in conn.execute("SELECT uid FROM my_sales ORDER BY uid")] == [3, 4]
    assert conn.execute("SELECT kind, COUNT(*), SUM(price) FROM trades GROUP BY kind ORDER BY kind").fetchall() == [
        ("purchase", 1, 5_973),
        ("sale", 4, 3 * 248_204 + 5_973),
    ]
    conn.close()


def test_lot_update_changes_a_price_or_adds_a_lot():
    conn = db.connect(":memory:")
    db.save_lot_update(conn, Sale(9, 22_219, 1, 259_999, 2_419_200), 500.0)
    assert conn.execute("SELECT COUNT(*) FROM my_sales").fetchone()[0] == 0  # aucun relevé encore : HDV inconnu
    db.save_sales(conn, SalesList(11, (Sale(1, 22_219, 1, 300_000, 9_000),)), 1_000.0)
    db.save_sales(conn, SalesList(22, (Sale(2, 7_300, 10, 5_973, 9_000),)), 1_100.0)
    db.save_lot_update(conn, Sale(1, 22_219, 1, 259_999, 2_419_200), 1_200.0)  # changement de prix : le lot reste dans son HDV
    db.save_lot_update(conn, Sale(5, 22_220, 1, 80_000, 2_419_200), 1_300.0)  # nouveau lot : HDV du dernier relevé
    assert conn.execute("SELECT market, uid, price, remaining_s, captured_at FROM my_sales ORDER BY uid").fetchall() == [
        (11, 1, 259_999, 2_419_200, 1_200.0),
        (22, 2, 5_973, 9_000, 1_100.0),
        (22, 5, 80_000, 2_419_200, 1_300.0),
    ]
    conn.close()


def test_pipeline_logs_trades_and_backfill_does_not_double_them(caplog, monkeypatch):
    market = db.connect(":memory:")
    market.execute("INSERT INTO items VALUES (21209, 'Bandeau', 1, 'Ressource diverse', 1, 1, 0, 2)")
    db.save_sales(market, SalesList(11, (Sale(1, 21_209, 10, 248_204, 9_000),)), 900.0)
    keymap = {"info_text": TEXT, "my_sale_update": UPDATE}
    pipeline = Pipeline(Archive(":memory:"), market, dict(keymap))
    stream = (
        framed(any_frame("txt", text(65, 248_204, 21_209, 21_209, 10)))
        + framed(any_frame("txt", text(252, 11_653, 3_602_459, 100, 174_800)))
        + framed(any_frame("txt", text(21, 1, 23_726)))
        + framed(any_frame("upd", update(857_144, 22_219, 1, 259_999)))
    )
    with caplog.at_level(logging.INFO, logger="dofustool.capture"):
        feed(pipeline, stream)
    assert [r.getMessage() for r in caplog.records] == ["Vente : Bandeau x10, 248204 kamas.", "Achat : item 11653 x100, 174800 kamas."]
    assert market.execute("SELECT kind, item_id, quantity, price FROM trades ORDER BY source_id").fetchall() == [
        ("sale", 21_209, 10, 248_204),
        ("purchase", 11_653, 100, 174_800),
    ]
    assert [row[0] for row in market.execute("SELECT uid FROM my_sales")] == [857_144]  # le lot vendu est parti, le nouveau est là

    # Rejouer l'archive retrouve les mêmes messages par leur numéro : rien n'est compté deux fois.
    pipeline.archive.commit()
    monkeypatch.setattr("dofustool.db.backfill.load_keymap", lambda: {"avg_prices": Mapping("zzz", {"entries": 1, "item_id": 3, "price": 5}), **keymap})
    assert backfill(pipeline.archive.db, market)["ventes et achats ajoutés"] == 0
    fresh = db.connect(":memory:")
    assert backfill(pipeline.archive.db, fresh)["ventes et achats ajoutés"] == 2
    market.close()
    fresh.close()


def test_journal_is_served(app_db):  # noqa: F811
    conn = db.connect(app_db)
    now = time.time()
    db.save_trade(conn, 1, Trade(SALE, 3, 10, 2_500), now - 3_600)
    db.save_trade(conn, 2, Trade(PURCHASE, 1, 100, 900), now - 7_200)
    db.save_trade(conn, 3, Trade(SALE, 1, 1, 12), now - 3 * 86_400)
    conn.close()
    api = Api(app_db)
    first = api.version()["stamp"]
    data = api.sales()
    json.dumps(data, allow_nan=False)
    assert [(r["kind"], r["name"], r["quantity"], r["price"]) for r in data["trades"]] == [
        ("sale", "Pain", 10, 2_500),
        ("purchase", "Blé", 100, 900),
        ("sale", "Blé", 1, 12),
    ]
    assert data["totals"] == {"sale_24h": [1, 2_500], "sale_7d": [2, 2_512], "purchase_24h": [1, 900], "purchase_7d": [1, 900]}
    assert data["rows"] == [] and data["first_trade"] == pytest.approx(now - 3 * 86_400)
    conn = db.connect(app_db)
    db.save_trade(conn, 4, Trade(SALE, 3, 1, 300), now)
    conn.close()
    assert api.version()["stamp"] != first  # une nouvelle vente rafraîchit l'interface


def test_equipment_lots_keep_their_rolls_when_the_effect_fields_are_known():
    effects = Mapping("hdv", {"effect_id": 10, "effect_value": 5})
    rolls = ld(2, vi(5, 371) + vi(10, 125)) + ld(2, vi(10, 985) + ld(9, b"Nom-De-Joueur"))  # vitalité, puis « modifié par »
    lot = trades.parse_lot_update(update(494_120, 18_018, 1, 6_500_000, effects=rolls), UPDATE, effects)
    assert lot.effects == ((125, 371), (985, None))  # le nom n'est jamais lu
    assert trades.parse_lot_update(update(494_120, 18_018, 1, 6_500_000, effects=rolls), UPDATE).effects == ()
    partial = Mapping("hdv", {"effect_id": 0, "effect_value": 0})  # annonces HDV vues sans équipement : champs inconnus
    assert trades.parse_lot_update(update(494_120, 18_018, 1, 6_500_000, effects=rolls), UPDATE, partial).effects == ()
    listing = Mapping("lst", {"entries": 2, "ref": 1, "uid": 1, "item_id": 2, "lot": 3, "price": 2, "remaining": 3})
    entry = ld(2, ld(1, vi(1, 7) + vi(2, 18_018) + ld(9, vi(5, 40) + vi(10, 119)) + vi(3, 1)) + vi(2, 6_500_000) + vi(3, 5_000))
    assert sales.parse(entry, listing, effects).sales[0].effects == ((119, 40),)

    conn = db.connect(":memory:")
    db.save_sales(conn, sales.parse(entry, listing, effects), 1_000.0)
    assert conn.execute("SELECT effects FROM my_sales").fetchone() == ("[[119, 40]]",)
    db.save_sales(conn, sales.parse(entry, listing), 1_100.0)  # relevé relu sans les jets : ils sont conservés
    assert conn.execute("SELECT effects, captured_at FROM my_sales").fetchone() == ("[[119, 40]]", 1_100.0)
    db.save_lot_update(conn, lot, 1_200.0)
    assert conn.execute("SELECT effects FROM my_sales WHERE uid = 494120").fetchone() == ("[[125, 371], [985, null]]",)
    conn.close()
