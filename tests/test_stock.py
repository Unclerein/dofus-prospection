"""Inventaire, banque et crafts faisables. Données entièrement synthétiques : aucun contenu réel de compte."""
import logging

import pytest

from dofustool import db
from dofustool.analysis.crafts import CraftCalculator, load_items, load_recipes
from dofustool.analysis.prices import PriceBook
from dofustool.analysis.stock import Stock, stock_crafts
from dofustool.archive import Archive
from dofustool.capture.pipeline import Pipeline
from dofustool.messages import Mapping, load_keymap, storage
from dofustool.messages.storage import Stack, Storage

from .test_analysis import AVG, NOW, conn  # noqa: F401  (fixture : Blé, Farine, Pain, Eau…)
from .test_pipeline import any_frame, feed
from .test_protocol import framed, ld, vi

INV = Mapping("inv", {"kamas": 1, "entries": 2, "position": 3, "object": 5, "quantity": 2, "item_id": 5})
BANK = Mapping("bnk", {"entries": 1, "kamas": 3, "position": 3, "object": 5, "quantity": 2, "item_id": 5})


def entry(field: int, item: int, qty: int, position: int = 63, uid: int = 7, text: bool = False) -> bytes:
    obj = vi(1, uid) + vi(2, qty) + ld(3, vi(1, 125) + vi(10, 40)) + vi(5, item)
    return ld(field, vi(3, position) + (ld(4, b"Nom donne par le joueur") if text else b"") + ld(5, obj))


def inventory(*stacks, kamas: int = 1500) -> bytes:
    return vi(1, kamas) + b"".join(entry(2, *s) for s in stacks)


def bank(*stacks, kamas: int = 900) -> bytes:
    return b"".join(entry(1, *s) for s in stacks) + ld(2, b"texte annexe") + (vi(3, kamas) if kamas else b"")


# --- parseur ----------------------------------------------------------------

def test_parse_inventory_and_bank():
    inv = storage.parse(inventory((1, 30), (1, 5), (4, 2), (3, 1, 6)), INV)
    assert inv.kamas == 1500 and len(inv.stacks) == 4
    assert inv.stacks[3] == Stack(3, 1, True)  # position 6 : objet porté
    assert inv.quantities() == {1: 35, 4: 2}  # piles additionnées, objets portés exclus
    assert inv.quantities(include_equipped=True) == {1: 35, 4: 2, 3: 1}

    b = storage.parse(bank((1, 100), (2, 8)), BANK)
    assert b.kamas == 900 and b.quantities() == {1: 100, 2: 8}
    assert storage.parse(bank((1, 100), kamas=0), BANK).kamas == 0
    assert storage.parse(vi(1, 10), INV) == Storage(10, ())  # sac vide


def test_parse_never_keeps_text():
    parsed = storage.parse(vi(1, 5) + entry(2, 1, 3, text=True), INV)
    assert parsed == Storage(5, (Stack(1, 3, False),))
    assert b"Nom" not in repr(parsed).encode()


@pytest.mark.parametrize(
    "body",
    [
        vi(1, 5) + ld(2, vi(3, 63)),  # entrée sans objet
        vi(1, 5) + ld(2, vi(3, 63) + ld(5, vi(1, 7) + vi(2, 3))),  # objet sans identifiant d'item
        vi(1, 5) + ld(2, vi(3, 63) + ld(5, vi(1, 7) + vi(5, 12))),  # sans quantité
        inventory((1, 30))[:-2],  # tronqué
        b"\x0d\x00\x00",  # champ fixe inattendu
    ],
)
def test_parse_rejects_unexpected_shapes(body):
    assert storage.parse(body, INV) is None


def test_looks_merged():
    inv = storage.parse(inventory((1, 30), (4, 2), (3, 1)), INV)
    merged = storage.parse(inventory((1, 130), (4, 2), (3, 1), (2, 8), (6, 4)), INV)
    assert storage.looks_merged(merged, inv)
    assert not storage.looks_merged(merged, None)  # première liste d'une connexion : c'est l'inventaire
    assert not storage.looks_merged(inv, inv)
    bigger = storage.parse(inventory((1, 30), (4, 2), (3, 1), (9, 1)), INV)  # un objet ramassé
    assert storage.looks_merged(bigger, inv)  # 4 piles contre 3 : au-delà du seuil, mais…
    many = storage.parse(inventory(*[(i, 1) for i in range(100, 140)]), INV)
    one_more = storage.parse(inventory(*[(i, 1) for i in range(100, 141)]), INV)
    assert not storage.looks_merged(one_more, many)  # …sur un vrai sac, un objet de plus ne suffit pas
    assert not storage.looks_merged(storage.parse(inventory((50, 1), (51, 1), (52, 1), (53, 1)), INV), inv)  # autre contenu


def test_real_keymap_has_storage_mappings():
    keymap = load_keymap()
    assert storage.parse(inventory((1, 30)), keymap["inventory"]).quantities() == {1: 30}
    assert storage.parse(bank((1, 100)), keymap["bank"]).kamas == 900


# --- base et stock ----------------------------------------------------------

def save(conn, container, body, mapping, ts):  # noqa: F811
    return db.save_holdings(conn, container, storage.parse(body, mapping), ts)


def test_holdings_replace_and_ignore_older(conn):  # noqa: F811
    assert save(conn, db.INVENTORY, inventory((1, 30), (1, 5), (3, 1, 6)), INV, 100.0)
    assert conn.execute("SELECT item_id, equipped, quantity FROM holdings ORDER BY item_id").fetchall() == [(1, 0, 35), (3, 1, 1)]
    assert save(conn, db.INVENTORY, inventory((4, 2)), INV, 200.0)  # la liste suivante remplace tout
    assert conn.execute("SELECT item_id, quantity FROM holdings").fetchall() == [(4, 2)]
    assert not save(conn, db.INVENTORY, inventory((1, 999)), INV, 150.0)  # plus ancienne : ignorée (rejeu)
    assert conn.execute("SELECT captured_at, kamas, stacks FROM holdings_meta").fetchall() == [(200.0, 1500, 1)]


def test_stock_combines_inventory_and_bank(conn):  # noqa: F811
    assert not Stock(conn).known
    save(conn, db.INVENTORY, inventory((1, 30), (4, 2), (3, 1, 6)), INV, 100.0)
    stock = Stock(conn)
    assert stock.known and not stock.bank_known and stock.get(1).total == 30 and stock.get(3).total == 0  # porté : exclu

    save(conn, db.BANK, bank((1, 100), (2, 8)), BANK, 110.0)
    stock = Stock(conn)
    assert (stock.get(1).inventory, stock.get(1).bank, stock.get(1).total) == (30, 100, 130)
    assert stock.get(2).total == 8 and stock.get(99).total == 0 and not stock.bank_inferred

    # Une liste fusionnée plus récente que la dernière visite à la banque fait foi : banque = fusion - inventaire.
    save(conn, db.ALL, inventory((1, 90), (4, 2), (2, 8), (6, 4)), INV, 120.0)
    stock = Stock(conn)
    assert stock.bank_inferred and (stock.get(1).inventory, stock.get(1).bank) == (30, 60)
    assert stock.get(6).bank == 4 and stock.get(4).bank == 0
    save(conn, db.BANK, bank((1, 100)), BANK, 130.0)  # banque rouverte ensuite : elle redevient la source
    assert not Stock(conn).bank_inferred and Stock(conn).get(1).bank == 100


def test_stock_crafts(conn):  # noqa: F811
    # Recettes de la fixture : Farine = 2 Blé ; Pain = 3 Farine + 1 Eau ; Brioche = 1 Farine + 2 Levure liée.
    save(conn, db.INVENTORY, inventory((1, 9), (2, 7)), INV, 100.0)  # 9 Blé, 7 Farine
    save(conn, db.BANK, bank((4, 1)), BANK, 100.0)  # 1 Eau
    calc = CraftCalculator(load_items(conn), load_recipes(conn), PriceBook(conn, NOW, 24), 0.02)
    by_name = {c.result.item.name: c for c in stock_crafts(conn, calc, Stock(conn))}

    farine = by_name["Farine"]
    assert farine.craftable == 4 and farine.ingredients == [(1, 2, 9)] and farine.covered == 1 and farine.missing_cost == 0
    assert farine.total_margin == pytest.approx(4 * farine.result.recursive_margin)

    pain = by_name["Pain"]  # 7 Farine / 3 = 2, mais une seule Eau
    assert pain.craftable == 1 and pain.ingredients == [(2, 3, 7), (4, 1, 1)] and pain.covered == 2

    brioche = by_name["Brioche"]  # la Farine suffit, la Levure manque (2 unités à 10, coût de craft)
    assert brioche.craftable == 0 and brioche.covered == 1 and brioche.missing_cost == pytest.approx(20)
    assert "Cycle A" not in by_name  # aucun ingrédient possédé : la recette n'est pas listée
    gateau = by_name["Gâteau"]  # il manque « Sans prix » : coût du manquant incalculable
    assert gateau.craftable == 0 and gateau.missing_cost is None
    assert list(by_name)[0] in ("Farine", "Pain")  # les crafts faisables les plus rentables d'abord


# --- capture ----------------------------------------------------------------

def test_pipeline_stores_inventory_bank_and_merged(caplog):
    caplog.set_level(logging.INFO, logger="dofustool.capture")
    market = db.connect(":memory:")
    pipeline = Pipeline(Archive(":memory:"), market, {"inventory": INV, "bank": BANK})
    inv = inventory(*[(i, 2) for i in range(100, 120)])
    merged = inventory(*[(i, 5) for i in range(100, 140)])
    feed(pipeline, framed(any_frame("inv", inv)) + framed(any_frame("bnk", bank((500, 9)))) + framed(any_frame("inv", merged))
         + framed(any_frame("inv", inv)) + framed(any_frame("inv", b"\x0d\x00\x00")))
    meta = {c: stacks for c, stacks in market.execute("SELECT container, stacks FROM holdings_meta")}
    assert meta == {db.INVENTORY: 20, db.BANK: 1, db.ALL: 40}
    assert [r.getMessage() for r in caplog.records] == ["Inventaire enregistré : 20 piles.", "Banque enregistrée : 1 piles."]
    assert Stock(market).get(100).inventory == 2
    assert pipeline.archive.count() == 6  # tout reste dans l'archive brute
    market.close()
