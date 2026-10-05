import itertools
from pathlib import Path

import pytest

from dofustool import db
from dofustool.analysis.forgemagie import Filter, classify, perfect_filter
from dofustool.analysis.prices import AVG_PRICE, HDV, HDV_PLAIN, LAST_SALE, MEDIAN_24H, PriceBook, unit_price, weighted_median
from dofustool.analysis.trends import HDV_ASK, compute_trends
from dofustool.app import data
from dofustool.config import Config
from dofustool.messages import Mapping, hdv_listings, load_keymap
from dofustool.staticdata.effects import missing_items, parse_effects, store

from .test_protocol import ld, varint, vi

FIXTURE = Path(__file__).parent / "fixtures" / "hdv_listings.bin"
MAPPING = Mapping(
    "xxx",
    {"item_id": 1, "entries": 2, "effects": 1, "entry_item": 2, "uid": 5, "prices": 6, "effect_id": 1, "effect_value": 10},
)
NOW = 2_000_000.0
PA, VITA, CHANCE, MODIFIED = 111, 125, 123, 985


def packed(values) -> bytes:
    return b"".join(varint(v) for v in values)


def listing(item: int, uid: int, prices, effects=(), extra: bytes = b"") -> bytes:
    body = b"".join(
        ld(1, vi(1, effect_id) + (vi(10, value) if value is not None else ld(9, b"Nom-De-Joueur")))
        for effect_id, value in effects
    )
    return ld(2, body + vi(2, item) + vi(3, 9) + vi(5, uid) + ld(6, packed(prices)) + extra)


def message(item: int, *listings: bytes) -> bytes:
    return vi(1, item) + b"".join(listings) + vi(3, 9)


# --- parseur ----------------------------------------------------------------

def test_parse_resource_lots():
    hdv = hdv_listings.parse(message(289, listing(289, 1137, [4, 80, 937, 9443])), MAPPING)
    assert hdv.item_id == 289 and len(hdv.listings) == 1
    assert hdv.listings[0].prices == (4, 80, 937, 9443) and hdv.listings[0].effects == ()


def test_parse_equipment_keeps_numbers_and_drops_text():
    body = message(
        2469,
        listing(2469, 1, [59000, 0, 0, 0], [(PA, 1)]),
        listing(2469, 2, [99999, 0, 0, 0], [(VITA, 150), (PA, 1), (MODIFIED, None)]),
    )
    hdv = hdv_listings.parse(body, MAPPING)
    assert [l.prices[0] for l in hdv.listings] == [59000, 99999]
    assert hdv.listings[1].effects == ((VITA, 150), (PA, 1), (MODIFIED, None))  # le nom du joueur n'est pas gardé
    assert b"Nom-De-Joueur" not in repr(hdv).encode()


@pytest.mark.parametrize(
    "body",
    [
        vi(1, 289) + vi(3, 34),  # aucune annonce (fiche refermée)
        message(289, listing(290, 1, [4, 0, 0, 0])),  # annonce d'un autre item
        message(289, listing(289, 1, [4, 80, 937])),  # trois prix au lieu de quatre
        message(289, listing(289, 1, [0, 0, 0, 0])),  # aucun prix
        message(289, ld(2, vi(2, 289) + ld(6, packed([4, 0, 0, 0])))),  # sans identifiant d'annonce
        message(289, listing(289, 1, [4, 0, 0, 0])) + ld(9, b"x"),  # champ de premier niveau inattendu
        message(289, listing(289, 1, [4, 0, 0, 0]))[:-3],  # tronqué
    ],
)
def test_parse_rejects_unexpected_shapes(body):
    assert hdv_listings.parse(body, MAPPING) is None


def test_real_fixture_ble():
    hdv = hdv_listings.parse(FIXTURE.read_bytes(), load_keymap()["hdv_listings"])
    assert hdv.item_id == 289 and len(hdv.listings) == 1
    assert hdv.listings[0].effects == ()
    assert len(hdv.listings[0].prices) == 4 and all(p > 0 for p in hdv.listings[0].prices)


# --- forgemagie -------------------------------------------------------------

def test_classify_exo_over_plain():
    template = {PA: (1, 1), VITA: (201, 250), 116: (-1, -1)}
    base = classify([(PA, 1), (VITA, 230), (116, 1)], template)
    assert base.label == "de base" and base.plain and base.quality == pytest.approx(29 / 49)
    assert classify([(PA, 1), (VITA, 230), (MODIFIED, None)], template).label == "forgemagé"
    perfect = classify([(PA, 1), (VITA, 250)], template)
    assert perfect.perfect and perfect.plain and perfect.label == "jets parfaits"
    exo = classify([(PA, 1), (VITA, 230), (CHANCE, 10)], template)
    assert exo.exo == (CHANCE,) and exo.label == "exo" and not exo.plain
    over = classify([(PA, 1), (VITA, 265)], template)
    assert over.over == (VITA,) and over.label == "over" and over.quality == 1
    both = classify([(PA, 2), (VITA, 210), (CHANCE, 5)], template)
    assert both.label == "exo + over"
    # Ligne de base absente ou à 0 : l'exemplaire est abîmé, pas comparable à un craft.
    broken = classify([(PA, 1)], template)
    assert broken.missing == (VITA,) and broken.label == "ligne manquante" and not broken.plain
    assert classify([(PA, 1), (VITA, 0)], template).missing == (VITA,)
    # Un malus de base n'est ni un over ni une ligne manquante ; une ligne fixe n'a pas de « jet ».
    assert classify([(PA, 1), (VITA, 201), (116, 5)], template).plain
    fixed = classify([(PA, 1)], {PA: (1, 1)})
    assert fixed.quality is None and fixed.label == "de base" and not fixed.perfect


def test_filter_line_by_line_and_exo():
    template = {PA: (1, 1), VITA: (201, 250)}
    low = classify([(PA, 1), (VITA, 205)], template)
    high = classify([(PA, 1), (VITA, 250)], template)
    exo = classify([(PA, 1), (VITA, 240), (CHANCE, 12)], template)
    assert all(Filter({}).matches(x) for x in (low, high, exo))
    assert [Filter({VITA: 240}).matches(x) for x in (low, high, exo)] == [False, True, True]
    assert [Filter({}, exo=0).matches(x) for x in (low, high, exo)] == [True, True, False]
    assert [Filter({}, exo=CHANCE).matches(x) for x in (low, high, exo)] == [False, False, True]
    assert not Filter({}, exo=CHANCE, exo_min=15).matches(exo)
    assert [perfect_filter(template).matches(x) for x in (low, high, exo)] == [False, True, False]
    saved = Filter({VITA: 240}, exo=CHANCE, exo_min=10)
    assert Filter.from_config(saved.to_config()) == saved and Filter.from_config({}) == Filter({})


def test_dofusdb_effects_parsing():
    payload = {"effects": [{"effectId": 111, "from": 1, "to": 0}, {"effectId": 125, "from": 201, "to": 250},
                           {"effectId": 116, "from": -1, "to": 0}]}  # fmt: skip
    assert sorted(parse_effects(payload)) == [(111, 1, 1), (116, -1, -1), (125, 201, 250)]
    assert parse_effects({}) == []


# --- prix de référence ------------------------------------------------------

def test_unit_price_and_weighted_median():
    assert unit_price((4, 80, 937, 9443)) == 4
    assert unit_price((0, 80, 700, 0)) == 7
    assert unit_price((0, 0, 0, 0)) is None
    assert weighted_median([(100, 1), (200, 1), (900, 10)]) == 900
    assert weighted_median([]) is None


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    c.executemany(
        "INSERT INTO items VALUES (?, ?, 1, 'type', 60, 1, 0, ?)",
        [(289, "Blé", 2), (2469, "Gelano", 0), (3000, "Anneau inconnu", 0)],
    )
    c.executemany("INSERT INTO effects VALUES (?, ?)", [(PA, "PA"), (VITA, "Vitalité"), (CHANCE, "Chance")])
    db.save_snapshot(c, NOW - 3600, {289: 10, 2469: 394_000, 3000: 50_000})
    yield c
    c.close()


_clock = itertools.count()


def save(conn, body):
    """Chaque appel simule une nouvelle consultation, un peu plus tard que la précédente."""
    db.save_hdv_listings(conn, hdv_listings.parse(body, MAPPING), NOW - 60 + next(_clock) * 0.01)


def book(conn, now=NOW):
    return PriceBook(conn, now, last_sale_max_age_hours=24)


def test_resource_reference_order(conn):
    assert book(conn).get(289).source == AVG_PRICE
    db.save_last_sale(conn, 289, 9, NOW - 600, NOW)
    assert (book(conn).get(289).price, book(conn).get(289).source) == (9, LAST_SALE)
    save(conn, message(289, listing(289, 1137, [12, 80, 937, 9443])))
    ref = book(conn).get(289)
    assert (ref.price, ref.source) == (8, HDV)  # le lot de 10 revient à 8 l'unité
    assert book(conn, NOW + 48 * 3600).get(289).source == AVG_PRICE  # annonces et vente trop anciennes


def test_trend_from_hdv_ask_for_resources_only(conn):
    # Blé affiché à 4 l'unité pour un prix moyen de 10 : sous-coté de 60 %, sans attendre d'autres relevés.
    save(conn, message(289, listing(289, 1137, [4, 80, 937, 9443])))
    trends = compute_trends(conn, NOW, 5, 24, book(conn))
    ble = trends[289]
    assert ble.basis == HDV_ASK and (ble.current, ble.reference) == (4, 10)
    assert ble.deviation == pytest.approx(-0.6) and ble.signal(0.15) == "sous-coté"
    assert trends[2469].basis == "données insuffisantes"  # sans annonce : relevés, pas encore assez nombreux
    assert compute_trends(conn, NOW, 5, 24)[289].basis == "données insuffisantes"  # sans carnet de prix : inchangé

    # Un équipement ouvert à l'HDV n'est jamais comparé à son prix moyen.
    save(conn, message(2469, listing(2469, 2, [59_000, 0, 0, 0], [(PA, 1)])))
    store(conn, 2469, [(PA, 1, 1)])
    assert compute_trends(conn, NOW, 5, 24, book(conn))[2469].basis == "données insuffisantes"

    # Annonce trop ancienne : elle ne sert plus.
    assert compute_trends(conn, NOW + 48 * 3600, 5, 24, book(conn, NOW + 48 * 3600))[289].basis == "données insuffisantes"

    # Le cours du marché, quand il existe, reste prioritaire sur l'annonce.
    day = int(NOW) // 86400 * 86400
    db.save_market_history(conn, 289, db.GRAIN_DAY, [(day, 9, 500), (day - 86400, 9, 500)], NOW - 30)
    db.save_last_sale(conn, 289, 9, NOW - 600, NOW)
    assert compute_trends(conn, NOW, 5, 24, book(conn))[289].basis == "cours du marché"

    frame, _ = data.trends_frame(conn, Config(min_snapshots_for_trend=5), data.build_workspace(conn, Config(), NOW))
    assert "Prix moyen" in frame.columns


def test_equipment_reference_ignores_exo_and_single_sales(conn):
    hour = int(NOW) // 3600 * 3600
    db.save_market_history(
        conn, 2469, db.GRAIN_HOUR, [(hour, 64_333, 3), (hour - 3600, 292_747, 4), (hour - 7200, 900_000, 2)], NOW - 30
    )
    db.save_last_sale(conn, 2469, 64_333, NOW - 600, NOW)
    # Sans annonce : la médiane pondérée sur 24 h, pas la dernière vente isolée.
    ref = book(conn).get(2469)
    assert (ref.price, ref.source) == (292_747, MEDIAN_24H)

    save(
        conn,
        message(
            2469,
            listing(2469, 1, [40_000, 0, 0, 0], [(VITA, 5), (PA, 1)]),  # exo bradé : ignoré
            listing(2469, 2, [59_000, 0, 0, 0], [(PA, 1)]),
            listing(2469, 3, [75_000, 0, 0, 0], [(PA, 1)]),
            listing(2469, 4, [30_000, 0, 0, 0], [(PA, 2)]),  # over : ignoré
        ),
    )
    assert book(conn).get(2469).source == MEDIAN_24H  # caractéristiques de base encore inconnues
    store(conn, 2469, [(PA, 1, 1)])
    ref = book(conn).get(2469)
    assert (ref.price, ref.source) == (59_000, HDV_PLAIN)

    # Tendance : pour un équipement, c'est le prix médian du jour qui est comparé, pas la dernière vente.
    day = int(NOW) // 86400 * 86400
    db.save_market_history(conn, 2469, db.GRAIN_DAY, [(day, 300_000, 50), (day - 86400, 280_000, 50)], NOW - 30)
    trend = compute_trends(conn, NOW, 5, 24, book(conn))[2469]
    assert trend.current == 292_747 and abs(trend.deviation) < 0.05

    # Une annonce plus chère que les ventes récentes ne sert pas de référence.
    save(conn, message(2469, listing(2469, 7, [800_000, 0, 0, 0], [(PA, 1)])))
    assert (book(conn).get(2469).price, book(conn).get(2469).source) == (292_747, MEDIAN_24H)
    save(conn, message(2469, listing(2469, 2, [59_000, 0, 0, 0], [(PA, 1)])))

    # Que des exos en vente : on retombe sur la médiane.
    save(conn, message(2469, listing(2469, 9, [40_000, 0, 0, 0], [(VITA, 5), (PA, 1)])))
    assert book(conn).get(2469).source == MEDIAN_24H
    # Les annonces disparues restent en historique, hors de la liste courante.
    assert conn.execute("SELECT COUNT(*) FROM hdv_current WHERE item_id = 2469").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM hdv_listings WHERE item_id = 2469").fetchone()[0] == 6


def test_missing_items_and_dashboard_detail(conn):
    save(conn, message(289, listing(289, 1137, [4, 80, 937, 9443])))
    save(
        conn,
        message(
            2469,
            listing(2469, 1, [59_000, 0, 0, 0], [(PA, 1)]),
            listing(2469, 2, [99_999, 0, 0, 0], [(VITA, 150), (PA, 1), (MODIFIED, None)]),
        ),
    )
    assert missing_items(conn) == [2469]  # seul l'équipement vu à l'HDV est à récupérer
    assert missing_items(conn, all_equipment=True) == [2469, 3000]

    ws = data.build_workspace(conn, Config(), NOW)
    lots = data.hdv_detail(conn, ws, 289)
    assert lots["kind"] == "lots" and list(lots["frame"]["Lot"]) == ["x1", "x10", "x100", "x1000"]
    assert list(lots["frame"]["Prix unitaire"]) == [4, 8, 9.37, 9.443]
    unknown = data.hdv_detail(conn, ws, 2469)
    assert not unknown["template_known"] and set(unknown["frame"]["Forgemagie"]) == {"inconnue"}

    store(conn, 2469, [(PA, 1, 1)])
    assert missing_items(conn) == []
    known = data.hdv_detail(conn, ws, 2469)
    assert known["base"] == "PA 1" and known["counts"] == {"de base": 1, "exo": 1}
    exo = known["frame"].iloc[1]
    assert (exo["Prix"], exo["Exo"], exo["Caractéristiques"]) == (99_999, "Vitalité 150", "Vitalité 150, PA 1")
    assert data.hdv_detail(conn, ws, 3000) is None
