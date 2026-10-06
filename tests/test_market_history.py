from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dofustool import db
from dofustool.analysis.prices import LAST_SALE, PriceBook
from dofustool.analysis.trends import MARKET_HISTORY, compute_trends
from dofustool.messages import Mapping, load_keymap, market_history
from dofustool.messages.market_history import DAY, HOUR

from .test_protocol import ld, vi

FIXTURE = Path(__file__).parent / "fixtures" / "market_history.bin"
# Numéros de champ du build du 5 octobre 2026, celui des fixtures réelles de ce fichier.
MAPPING = Mapping("xxx", {"hourly": 1, "daily": 2, "quantity": 1, "date": 2, "price": 3, "item_id": 4})
T0 = datetime(2026, 10, 5, 14, 6, 26, tzinfo=timezone.utc)
# Heure de la capture dont est tirée la fixture (05/10/2026 16:14 heure de Paris).
CAPTURED = datetime(2026, 10, 5, 14, 14, 15, tzinfo=timezone.utc).timestamp()


def entry(series: int, when: datetime, price: int, qty: int, item: int = 289, fraction: str = ".123456789") -> bytes:
    date = when.strftime("%Y-%m-%dT%H:%M:%S") + fraction + "+00:00"
    return ld(series, vi(1, qty) + ld(2, date.encode()) + vi(3, price) + vi(4, item))


def test_parse_synthetic():
    body = (
        entry(1, T0, 9, 100)
        + entry(1, T0 - timedelta(hours=1), 8, 50)
        + entry(2, T0, 9, 150, fraction=".123456")
        + entry(2, T0 - timedelta(days=1), 7, 900, fraction="")
    )
    history = market_history.parse(body, MAPPING)
    assert history.item_id == 289
    assert [(p.price, p.quantity) for p in history.hourly] == [(8, 50), (9, 100)]  # trié par date
    assert [(p.price, p.quantity) for p in history.daily] == [(7, 900), (9, 150)]
    assert history.last_sale.price == 9 and history.last_sale.sold_at == pytest.approx(T0.timestamp(), abs=1)
    assert history.hourly[1].bucket(HOUR) == T0.replace(minute=0, second=0).timestamp()
    assert history.daily[1].bucket(DAY) == T0.replace(hour=0, minute=0, second=0).timestamp()


@pytest.mark.parametrize(
    "body",
    [
        b"",  # aucun point
        entry(1, T0, 9, 100) + entry(1, T0, 9, 100, item=290),  # deux items
        entry(1, T0, 9, 100) + entry(1, T0 + timedelta(minutes=5), 9, 10),  # même tranche horaire deux fois
        entry(1, T0, 0, 100),  # prix nul
        entry(3, T0, 9, 100),  # série inconnue
        ld(1, vi(1, 5) + ld(2, b"pas une date") + vi(3, 9) + vi(4, 289)),
        ld(1, vi(1, 5) + ld(2, b"2026-10-05T14:06:26") + vi(3, 9) + vi(4, 289)),  # date sans fuseau
        ld(1, vi(1, 5) + vi(3, 9) + vi(4, 289)),  # date absente
        entry(1, T0, 9, 100) + ld(1, vi(1, 5) + ld(2, b"2026-10-05T14:06:26+00:00") + vi(3, 9) + vi(4, 289) + ld(7, b"x")),
        entry(1, T0, 9, 100)[:-2],  # tronqué
    ],
)
def test_parse_rejects_unexpected_shapes(body):
    assert market_history.parse(body, MAPPING) is None


# --- fixture réelle : valeurs lues à l'écran, onglet « Cours du marché » du Blé ----------

@pytest.fixture(scope="module")
def ble():
    return market_history.parse(FIXTURE.read_bytes(), MAPPING)


def test_fixture_matches_24h_screen(ble):
    assert ble.item_id == 289
    assert len(ble.hourly) == 23
    assert sum(p.quantity for p in ble.hourly) == 76_671  # « 76 671 articles vendus »
    hovered = [p for p in ble.hourly if p.quantity == 7200][0]  # point survolé : 05/10 15:46, 9 k, 7 200 objets
    assert hovered.price == 9
    assert datetime.fromtimestamp(hovered.sold_at, timezone(timedelta(hours=2))).strftime("%d/%m %H:%M") == "05/10 15:46"
    assert sorted(p.price for p in ble.hourly)[11] == 9  # prix médian 9
    assert int(sum(p.price for p in ble.hourly) / 23) == 8  # prix moyen 8


def test_fixture_matches_30d_screen(ble):
    assert len(ble.daily) == 30
    assert sum(p.quantity for p in ble.daily) == 2_247_374  # « 2 247 374 articles vendus »
    hovered = [p for p in ble.daily if p.quantity == 54_794][0]  # point survolé : 04/10, 9 k, 54 794 objets vendus
    assert hovered.price == 9
    assert datetime.fromtimestamp(hovered.sold_at, timezone(timedelta(hours=2))).strftime("%d/%m") == "04/10"
    assert int(sum(p.price for p in ble.daily) / 30) == 10  # prix moyen 10
    assert max(p.price for p in ble.daily) == 23 and min(p.price for p in ble.daily) == 5  # sommets de la courbe


def test_fixture_matches_7d_screen(ble):
    # La vue « 7 jours » du jeu reprend les 9 derniers points journaliers.
    last = ble.daily[-9:]
    assert [p.price for p in last] == [10, 11, 11, 9, 9, 9, 9, 9, 9]  # courbe du 28/09 au 05/10
    assert sum(p.quantity for p in last) == 653_488  # « 653 488 articles vendus »


def test_fixture_last_sale_is_last_purchase(ble):
    sale = ble.last_sale
    assert sale.price == 9
    # « Dernier achat : 05/10 - 16:06 »
    assert datetime.fromtimestamp(sale.sold_at, timezone(timedelta(hours=2))).strftime("%d/%m - %H:%M") == "05/10 - 16:06"


def test_save_market_and_analysis(ble):
    conn = db.connect(":memory:")
    db.save_market(conn, ble, CAPTURED)
    db.save_market(conn, ble, CAPTURED + 60)  # reconsultation : aucun doublon
    assert conn.execute("SELECT period, COUNT(*) FROM market_history GROUP BY period ORDER BY period").fetchall() == [
        (db.GRAIN_DAY, 30),
        (db.GRAIN_HOUR, 23),
    ]
    assert conn.execute("SELECT COUNT(*) FROM last_sales").fetchone()[0] == 1
    assert db.latest_last_sale(conn, 289)[0] == 9

    prices = PriceBook(conn, CAPTURED + 3600, last_sale_max_age_hours=24)
    ref = prices.get(289)
    assert (ref.price, ref.source) == (9, LAST_SALE)
    assert prices.liquidity(289).qty_24h == 76_671  # identique au total affiché sur 24 h
    assert prices.liquidity(289).qty_7d == sum(p.quantity for p in ble.daily[-7:])
    assert not prices.liquidity(1).known
    # Une vente trop ancienne ne sert plus de prix de référence.
    assert PriceBook(conn, CAPTURED + 3 * 86400, last_sale_max_age_hours=24).get(289) is None

    trend = compute_trends(conn, CAPTURED + 3600, min_snapshots=5, last_sale_max_age_hours=24)[289]
    assert trend.basis == MARKET_HISTORY and trend.current == 9
    assert trend.mean_30d == pytest.approx(10.2, abs=0.2)  # le jeu affiche « prix moyen : 10 » sur 30 j
    assert trend.mean_7d == pytest.approx(9.3, abs=0.3)
    conn.close()


def test_growing_bucket_is_replaced_not_duplicated():
    conn = db.connect(":memory:")
    first = market_history.parse(entry(1, T0, 9, 100) + entry(2, T0, 9, 100), MAPPING)
    later = market_history.parse(
        entry(1, T0 + timedelta(minutes=20), 10, 350) + entry(2, T0 + timedelta(minutes=20), 10, 350), MAPPING
    )
    db.save_market(conn, first, T0.timestamp() + 60)
    db.save_market(conn, later, T0.timestamp() + 1800)
    assert conn.execute("SELECT period, price, qty_sold FROM market_history ORDER BY period").fetchall() == [
        (db.GRAIN_DAY, 10, 350),
        (db.GRAIN_HOUR, 10, 350),
    ]
    assert conn.execute("SELECT COUNT(*) FROM last_sales").fetchone()[0] == 2  # deux ventes distinctes observées
    assert db.latest_last_sale(conn, 289)[0] == 10
    conn.close()


# --- second objet : Gelano, cher et peu vendu (écran du 05/10/2026 17:31) -----------------

def weighted_median(points):
    total = sum(p.quantity for p in points)
    seen = 0
    for p in sorted(points, key=lambda p: p.price):
        seen += p.quantity
        if seen * 2 >= total:
            return p.price


def test_gelano_fixture_matches_screen():
    gelano = market_history.parse(
        FIXTURE.with_name("market_history_gelano.bin").read_bytes(), MAPPING
    )
    paris = timezone(timedelta(hours=2))
    assert gelano.item_id == 2469
    assert sum(p.quantity for p in gelano.hourly) == 99  # « 99 articles vendus »
    hovered = [p for p in gelano.hourly if p.price == 82_500][0]  # point survolé : 05/10 16:41, 2 objets vendus
    assert hovered.quantity == 2
    assert datetime.fromtimestamp(hovered.sold_at, paris).strftime("%d/%m %H:%M") == "05/10 16:41"
    # Le prix moyen et le prix médian affichés par le jeu sont pondérés par les quantités.
    weighted = sum(p.price * p.quantity for p in gelano.hourly) / 99
    assert int(weighted) == 391_544  # « prix moyen : 391 544 »
    assert weighted_median(gelano.hourly) == 292_747  # « prix médian : 292 747 »
    assert max(p.price for p in gelano.hourly) == 1_073_999  # sommet de l'axe
    sale = gelano.last_sale  # « dernier achat : 05/10 - 17:29 »
    assert datetime.fromtimestamp(sale.sold_at, paris).strftime("%d/%m - %H:%M") == "05/10 - 17:29"
    assert sale.price == 64_333


def test_real_keymap_reads_a_body_built_with_its_field_numbers():
    mapping = load_keymap()["market_history"]
    f = mapping.fields
    point = vi(f["quantity"], 12) + ld(f["date"], T0.isoformat().encode()) + vi(f["price"], 150) + vi(f["item_id"], 289)
    parsed = market_history.parse(ld(f["hourly"], point) + ld(f["daily"], point), mapping)
    assert parsed.item_id == 289 and parsed.hourly[0].price == 150 and parsed.daily[0].quantity == 12
