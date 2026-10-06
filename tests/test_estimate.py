"""Prix du moment estimé à partir du glissement horaire des prix moyens."""
import pytest

from dofustool import db
from dofustool.analysis import estimate
from dofustool.analysis.estimate import RELIABLE, ROUGH
from dofustool.analysis.prices import AVG_PRICE, ESTIMATED, HDV, PriceBook

T0 = 20_000 * 86400.0 + 3600  # 01 h UTC : toute la série tient dans le même jour
HOUR = 3600.0


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    c.executemany(
        "INSERT INTO items VALUES (?, ?, 1, 'Ressource diverse', 1, 1, 0, 2)",
        [(1, "Monte"), (2, "Stable"), (3, "Pas cher"), (4, "Hésite"), (5, "Baisse")],
    )
    c.execute("INSERT INTO items VALUES (9, 'Anneau', 1, 'Anneau', 1, 1, 0, 0)")
    yield c
    c.close()


def snapshots(conn, series: dict[int, list[int]], start: float = T0, step: float = HOUR) -> float:
    """Un relevé par heure ; renvoie la date du dernier."""
    count = len(next(iter(series.values())))
    for k in range(count):
        db.save_snapshot(conn, start + k * step, {item_id: values[k] for item_id, values in series.items()})
    return start + (count - 1) * step


SERIES = {
    1: [10_000, 10_010, 10_020, 10_030, 10_040, 10_050, 10_060],  # +10 par heure, toujours dans le même sens
    2: [5_000] * 7,
    3: [20, 20, 20, 21, 21, 21, 21],  # l'arrondi au kama noie le signal
    4: [10_000, 10_010, 10_000, 10_012, 10_004, 10_009, 10_006],  # va et vient
    5: [10_000, 9_990, 9_980, 9_970, 9_960, 9_950, 9_940],
    9: [10_000, 10_010, 10_020, 10_030, 10_040, 10_050, 10_060],
}


def test_estimate_extrapolates_the_hourly_drift(conn):
    last = snapshots(conn, SERIES)
    found = estimate.estimate_prices(conn)
    up = found[1]
    assert up.price == pytest.approx(10_060 + 60 * 720 / 6) and up.confidence == RELIABLE
    assert (up.avg, up.delta, up.hours, up.ts) == (10_060, 60, 6.0, last)
    assert found[5].price == pytest.approx(9_940 - 60 * 720 / 6) and found[5].confidence == RELIABLE
    assert 2 not in found  # prix moyen immobile : pas de vente récente, rien à dire
    assert 3 not in found  # 1 kama d'écart sur un objet à 20 kamas : inexploitable
    assert found[4].confidence == ROUGH  # sens incohérent d'une heure à l'autre


def test_estimate_needs_enough_hours_and_ignores_the_day_change(conn):
    snapshots(conn, {1: [10_000, 10_010, 10_020]})  # 2 h seulement
    assert estimate.estimate_prices(conn) == {}
    conn.execute("DELETE FROM avg_prices")
    conn.execute("DELETE FROM snapshots")
    # Deux relevés de part et d'autre de minuit UTC : un jour ancien est sorti de la fenêtre entre-temps.
    snapshots(conn, {1: [10_000, 10_900]}, start=20_000 * 86400.0 - 2 * HOUR, step=4 * HOUR)
    assert estimate.estimate_prices(conn) == {}


def test_estimate_is_clamped(conn):
    snapshots(conn, {1: [1_000, 1_100, 1_200, 1_300]})
    found = estimate.estimate_prices(conn)[1]
    assert found.price == 5 * 1_300 and found.confidence == ROUGH  # borne : jamais « fiable »


def test_price_book_uses_reliable_estimates_only_when_asked(conn):
    last = snapshots(conn, SERIES)
    now = last + HOUR
    plain = PriceBook(conn, now, 24)
    assert plain.get(1).source == AVG_PRICE and plain.estimate(1).confidence == RELIABLE

    book = PriceBook(conn, now, 24, use_estimates=True)
    ref = book.get(1)
    assert (ref.source, ref.price, ref.ts, ref.spread) == (ESTIMATED, pytest.approx(17_260), last, 0.15)
    assert book.get(4).source == AVG_PRICE  # « indicatif » : affiché, jamais utilisé comme référence
    assert book.get(9).source == AVG_PRICE and book.estimate(9) is None  # pas pour les équipements
    # Une annonce HDV relevée garde la priorité.
    conn.execute("INSERT INTO hdv_listings VALUES (1, 7, 16000, 0, 0, 0, '[]', ?, ?)", (now, now))
    assert PriceBook(conn, now, 24, use_estimates=True).get(1).source == HDV
    # Un relevé trop ancien ne s'extrapole pas.
    old = PriceBook(conn, last + 30 * HOUR, 24, use_estimates=True)
    assert old.estimates == {} and old.get(5).source == AVG_PRICE


def test_check_measures_the_error_against_hdv(conn):
    last = snapshots(conn, SERIES)
    conn.execute("INSERT INTO hdv_listings VALUES (1, 7, 0, 160000, 0, 0, '[]', ?, ?)", (last, last))  # 16 000 l'unité
    conn.execute("INSERT INTO hdv_listings VALUES (9, 8, 16000, 0, 0, 0, '[]', ?, ?)", (last, last))  # équipement : ignoré
    report = estimate.check(conn, estimate.estimate_prices(conn))
    reliable = report[RELIABLE]
    assert reliable["points"] == 1 and reliable["error"] == pytest.approx(17_260 / 16_000 - 1)
    assert reliable["avg_error"] == pytest.approx(1 - 10_060 / 16_000) and reliable["within_20"] == 1.0
    assert reliable["spread"] == 0.15 and not reliable["measured"]  # trop peu de points : fourchette par défaut
    assert report[ROUGH]["points"] == 0 and report[ROUGH]["error"] is None
