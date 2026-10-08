"""Stock suivi pile par pile et coffre par coffre : listes réunies, coffres ouverts seuls, mouvements isolés.
Données synthétiques uniquement."""
import pytest

from dofustool import db
from dofustool.analysis.stock import Stock
from dofustool.archive import Archive
from dofustool.capture.pipeline import Pipeline
from dofustool.db.backfill import backfill
from dofustool.messages import Mapping, inventory, storage
from dofustool.messages.inventory import BANK, HAVRE, INVENTORY, NewObject, PileUpdate
from dofustool.messages.sales import Sale, SalesList

from .test_pipeline import any_frame, feed
from .test_protocol import framed, ld, vi

LIST = Mapping("inv", {"kamas": 4, "entries": 2, "position": 4, "object": 1, "quantity": 3, "item_id": 4,
                       "uid": 5, "split": 1, "split_quantity": 1, "split_container": 4})  # fmt: skip
COFFRE = Mapping("cof", {"entries": 2, "kamas": 1, "position": 4, "object": 1, "quantity": 3, "item_id": 4, "uid": 5})
OPEN = Mapping("opn", {"type": 1})
UPDATE = Mapping("upd", {"parts": 2, "part_quantity": 1, "part_container": 4, "pile": 3, "quantity": 1, "uid": 3})
ADDED = Mapping("add", {"wrap": 2, "object": 1})
MODIFIED = Mapping("mod", {"wrap": 1, "object": 1})
REMOVED = Mapping("rem", {"uid": 1})
KAMAS = Mapping("kam", {"kamas": 2})
KEYMAP = {"inventory": LIST, "bank": COFFRE, "storage_open": OPEN, "pile_update": UPDATE, "object_added": ADDED,
          "object_modified": MODIFIED, "object_removed": REMOVED, "kamas": KAMAS}  # fmt: skip
WHEAT, FLOUR, RUNE, RING = 289, 290, 1557, 8000


def obj(uid, item, quantity, parts=()):
    split = b"".join(ld(1, vi(1, q) + vi(4, c)) for c, q in parts)
    return split + vi(3, quantity) + vi(4, item) + vi(5, uid) + ld(7, vi(5, 3) + vi(10, 125))


def listing(entries, kamas=1_500, mapping=LIST):
    """entries : (uid, objet, quantité, parts, position)."""
    f = mapping.fields
    return vi(f["kamas"], kamas) + b"".join(ld(f["entries"], ld(1, obj(u, i, q, parts)) + vi(4, pos)) for u, i, q, parts, pos in entries)


def pile(uid, total, parts=()):
    return b"".join(ld(2, vi(1, q) + vi(4, c)) for c, q in parts) + ld(3, (vi(1, total) if total else b"") + vi(3, uid))


def totals(conn):
    return {(c, i): q for c, i, q in conn.execute("SELECT container, item_id, SUM(quantity) FROM holdings WHERE NOT equipped GROUP BY 1, 2")}


# Blé : 40 en inventaire et 60 en banque sous le même identifiant ; farine en banque ; runes au havre-sac et en banque.
MERGED = [
    (11, WHEAT, 100, ((INVENTORY, 40), (BANK, 60)), 63),
    (12, FLOUR, 7, ((BANK, 7),), 63),
    (13, RUNE, 115, ((HAVRE, 24), (BANK, 91)), 63),
    (14, RING, 1, (), 3),  # anneau porté : pas de répartition
]


def test_a_merged_list_says_where_every_pile_is():
    parsed = storage.parse(listing(MERGED), LIST)
    assert parsed.detailed and [(s.uid, s.parts) for s in parsed.stacks][0] == (11, ((INVENTORY, 40), (BANK, 60)))
    assert not storage.parse(listing([(11, WHEAT, 40, (), 63)]), LIST).detailed
    # Une répartition qui ne fait pas le total trahit une forme inattendue.
    assert storage.parse(listing([(11, WHEAT, 100, ((INVENTORY, 40), (BANK, 50)), 63)]), LIST) is None

    conn = db.connect(":memory:")
    assert db.save_piles(conn, parsed, 1_000.0) == {db.INVENTORY, db.BANK, db.HAVRE}
    assert totals(conn) == {
        (db.INVENTORY, WHEAT): 40, (db.BANK, WHEAT): 60, (db.BANK, FLOUR): 7, (db.BANK, RUNE): 91, (db.HAVRE, RUNE): 24,
    }  # fmt: skip
    stock = Stock(conn)
    assert (stock.get(RUNE).bank, stock.get(RUNE).havre, stock.get(RUNE).total) == (91, 24, 115)
    assert stock.get(RING).total == 0 and not stock.bank_inferred  # objet porté : exclu
    # Havre-sac décoché : la liste ne le cite plus, son dernier contenu connu est conservé.
    without = [(11, WHEAT, 100, ((INVENTORY, 40), (BANK, 60)), 63), (13, RUNE, 91, ((BANK, 91),), 63)]
    assert db.save_piles(conn, storage.parse(listing(without), LIST), 1_100.0) == {db.INVENTORY, db.BANK}
    assert totals(conn)[(db.HAVRE, RUNE)] == 24 and (db.BANK, FLOUR) not in totals(conn)
    # Une liste plus ancienne que le dernier relevé ne change rien.
    assert db.save_piles(conn, parsed, 900.0) == set()
    conn.close()


def test_moves_between_two_lists():
    conn = db.connect(":memory:")
    db.save_piles(conn, storage.parse(listing(MERGED), LIST), 1_000.0)
    seen = {db.INVENTORY, db.BANK, db.HAVRE}
    # Un craft consomme 10 blés de la banque.
    assert db.update_pile(conn, PileUpdate(11, 90, ((BANK, 50), (INVENTORY, 40))), seen, 1_010.0)
    assert (totals(conn)[(db.INVENTORY, WHEAT)], totals(conn)[(db.BANK, WHEAT)]) == (40, 50)
    # Un achat de 100 : le total suit tout de suite, le détail donne encore l'inventaire d'avant.
    assert db.update_pile(conn, PileUpdate(11, 190, ((BANK, 50), (INVENTORY, 40))), seen, 1_011.0)
    assert (totals(conn)[(db.INVENTORY, WHEAT)], totals(conn)[(db.BANK, WHEAT)]) == (140, 50)
    # La dernière farine de la banque part : la pile arrive vide, sans détail.
    assert db.update_pile(conn, PileUpdate(12, 0, ()), seen, 1_012.0)
    assert (db.BANK, FLOUR) not in totals(conn)
    # Banque et havre-sac hors de vue : un total sans détail ne concerne que l'inventaire.
    assert db.update_pile(conn, PileUpdate(11, 135, ()), {db.INVENTORY}, 1_013.0)
    assert (totals(conn)[(db.INVENTORY, WHEAT)], totals(conn)[(db.BANK, WHEAT)]) == (135, 50)
    assert not db.update_pile(conn, PileUpdate(999, 5, ()), seen, 1_014.0)  # pile inconnue : rien à dire de son objet

    assert db.add_pile(conn, NewObject(20, FLOUR, 3, False, ()), 1_020.0)
    assert totals(conn)[(db.INVENTORY, FLOUR)] == 3
    assert not db.add_pile(conn, NewObject(20, FLOUR, 3, False, ()), 1_021.0)  # objet « modifié » sans changement
    assert db.remove_pile(conn, 20, 1_022.0) and (db.INVENTORY, FLOUR) not in totals(conn)
    assert not db.remove_pile(conn, 20, 1_023.0)
    db.set_kamas(conn, 777, 1_024.0)
    assert Stock(conn).meta[db.INVENTORY]["kamas"] == 777
    # Rejeu d'un vieux mouvement : ignoré.
    assert not db.update_pile(conn, PileUpdate(11, 1, ()), seen, 500.0) and totals(conn)[(db.INVENTORY, WHEAT)] == 135
    conn.close()


def test_the_interface_is_not_signalled_at_every_move():
    conn = db.connect(":memory:")
    db.save_piles(conn, storage.parse(listing(MERGED), LIST), 1_000.0)
    stamp = lambda: conn.execute("SELECT captured_at FROM holdings_meta WHERE container = ?", (db.INVENTORY,)).fetchone()[0]  # noqa: E731
    for step in range(1, 10):  # une rune par seconde
        db.update_pile(conn, PileUpdate(11, 100 - step, ((BANK, 60), (INVENTORY, 40 - step))), {db.INVENTORY, db.BANK}, 1_000.0 + step)
    assert stamp() == 1_000.0 and totals(conn)[(db.INVENTORY, WHEAT)] == 31  # le stock est juste, le signal attend
    db.update_pile(conn, PileUpdate(11, 80, ((BANK, 60), (INVENTORY, 20))), {db.INVENTORY, db.BANK}, 1_016.0)
    assert stamp() == 1_016.0
    db.flush_stock_signal(conn, 1_020.0)
    assert stamp() == 1_020.0
    conn.close()


@pytest.mark.parametrize(
    "body, expected",
    [
        (pile(11, 90, ((BANK, 50), (INVENTORY, 40))), PileUpdate(11, 90, ((BANK, 50), (INVENTORY, 40)))),
        (pile(11, 0), PileUpdate(11, 0, ())),
        (pile(11, 5, ((9, 5),)), None),  # coffre inconnu
        (ld(3, vi(1, 5)), None),  # pas d'identifiant
        (ld(3, vi(1, 5) + vi(3, 11)) + ld(8, b"x"), None),  # sous-message inconnu
        (b"\xff", None),
    ],
)
def test_parse_pile_update(body, expected):
    assert inventory.parse_pile_update(body, UPDATE) == expected


def test_parse_object_and_single_values():
    added = ld(2, ld(1, obj(30, RUNE, 100)) + vi(4, 63))
    assert inventory.parse_object(added, ADDED, LIST) == NewObject(30, RUNE, 100, False, ())
    worn = ld(1, ld(1, obj(31, RING, 1, ((INVENTORY, 1),))) + vi(4, 3))
    assert inventory.parse_object(worn, MODIFIED, LIST) == NewObject(31, RING, 1, True, ((INVENTORY, 1),))
    assert inventory.parse_object(added, ADDED, Mapping("old", {"position": 4, "item_id": 4, "quantity": 3})) is None  # sans identifiant
    assert inventory.parse_object(ld(2, vi(4, 63)), ADDED, LIST) is None
    assert inventory.parse_single(vi(1, 30), REMOVED, "uid") == 30 and inventory.parse_single(b"", KAMAS, "kamas") == 0
    assert inventory.parse_single(ld(1, b"x"), REMOVED, "uid") is None


def test_pipeline_follows_the_stock_live_and_tells_bank_from_havre(monkeypatch):
    market = db.connect(":memory:")
    pipeline = Pipeline(Archive(":memory:"), market, dict(KEYMAP))
    havre = listing([(90, RUNE, 24, (), 63)], kamas=900, mapping=COFFRE)
    bank = listing([(91, WHEAT, 60, (), 63), (92, FLOUR, 7, (), 63)], kamas=900, mapping=COFFRE)
    stream = (
        framed(any_frame("inv", listing([(11, WHEAT, 40, (), 63), (14, RING, 1, (), 3)])))  # connexion : inventaire seul
        + framed(any_frame("opn", vi(1, 18))) + framed(any_frame("cof", havre))  # havre-sac ouvert
        + framed(any_frame("opn", vi(1, 15))) + framed(any_frame("cof", bank))  # banque ouverte
        + framed(any_frame("cof", havre))  # coffre sans annonce de son type : ignoré plutôt que mal rangé
        + framed(any_frame("upd", pile(11, 140)))  # achat de 100 blés
        + framed(any_frame("add", ld(2, ld(1, obj(30, RUNE, 10)) + vi(4, 63))))
        + framed(any_frame("mod", ld(1, ld(1, obj(30, RUNE, 9)) + vi(4, 63))))
        + framed(any_frame("rem", vi(1, 14)))
        + framed(any_frame("kam", vi(2, 4_242)))
    )  # fmt: skip
    feed(pipeline, stream)
    assert totals(market) == {
        (db.INVENTORY, WHEAT): 140, (db.INVENTORY, RUNE): 9, (db.BANK, WHEAT): 60, (db.BANK, FLOUR): 7, (db.HAVRE, RUNE): 24,
    }  # fmt: skip
    meta = Stock(market).meta
    assert meta[db.INVENTORY]["kamas"] == 4_242 and meta[db.BANK]["kamas"] == 900 and meta[db.HAVRE]["kamas"] == 0
    assert market.execute("SELECT COUNT(*) FROM piles WHERE equipped").fetchone()[0] == 0  # l'anneau porté a été retiré

    # Le rejeu de l'archive retrouve les mêmes coffres à partir des seules listes complètes.
    pipeline.archive.commit()
    monkeypatch.setattr("dofustool.db.backfill.load_keymap", lambda: {"avg_prices": Mapping("zzz", {"entries": 1, "item_id": 3, "price": 5}), **KEYMAP})
    fresh = db.connect(":memory:")
    counts = backfill(pipeline.archive.db, fresh)
    assert counts["listes d'inventaire lues"] == 1 and counts["coffres lus"] == 2
    assert totals(fresh) == {(db.INVENTORY, WHEAT): 40, (db.BANK, WHEAT): 60, (db.BANK, FLOUR): 7, (db.HAVRE, RUNE): 24}
    market.close()
    fresh.close()


def test_old_readings_without_pile_identifiers_still_work():
    """Table des clés d'avant le suivi par pile : l'ancien calcul (banque déduite d'une liste tout confondu) reste."""
    old = Mapping("inv", {"kamas": 4, "entries": 2, "position": 4, "object": 1, "quantity": 3, "item_id": 4})
    market = db.connect(":memory:")
    pipeline = Pipeline(Archive(":memory:"), market, {"inventory": old})
    feed(pipeline, framed(any_frame("inv", listing([(11, WHEAT, 40, (), 63)]))) + framed(any_frame("upd", pile(11, 5))))
    assert totals(market) == {(db.INVENTORY, WHEAT): 40} and market.execute("SELECT COUNT(*) FROM piles").fetchone()[0] == 0
    market.close()


def test_unsold_lots_go_back_to_the_bank(caplog):
    """Le jeu annonce seulement combien de lots invendus il rentre en banque : ce sont ceux qui arrivent à expiration."""
    market = db.connect(":memory:")
    market.execute("INSERT INTO items VALUES (?, 'Blé', 1, 'Céréale', 1, 1, 0, 2)", (WHEAT,))
    db.save_piles(market, storage.parse(listing(MERGED), LIST), 300.0)
    # Relevé de l'onglet Vendre à 300 s : le blé expire 600 s plus tard, la farine dans plus d'une heure.
    lots = (Sale(51, WHEAT, 100, 900, 600), Sale(52, FLOUR, 10, 80, 5_000), Sale(53, RUNE, 1, 2_000, 2_000_000))
    db.save_sales(market, SalesList(11, lots), 300.0)
    pipeline = Pipeline(Archive(":memory:"), market, {"unsold_returned": Mapping("uns", {"count": 1})})
    with caplog.at_level("INFO", logger="dofustool.capture"):
        feed(pipeline, framed(any_frame("uns", vi(1, 2))))  # annoncé vers 1 000 s : deux lots, un seul a expiré
    assert [r.getMessage() for r in caplog.records] == ["Lot invendu rentré en banque : Blé x100 (mis en vente à 900 kamas)."]
    assert [row[0] for row in market.execute("SELECT uid FROM my_sales ORDER BY uid")] == [52, 53]
    assert totals(market)[(db.BANK, WHEAT)] == 160  # les 60 déjà en banque, plus le lot de 100
    assert db.return_unsold(market, 5, 1_000.0) == []  # plus aucun lot proche de l'expiration
    market.close()
