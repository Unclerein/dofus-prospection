"""Cours du marché déduit des prix moyens : historique de ventes synthétique, prix moyens arrondis qui
s'en déduisent, puis reconstitution comparée aux ventes réelles."""
import math

import pytest

from dofustool import db
from dofustool.analysis import DAY, GRAIN_DAY, GRAIN_HOUR, HOUR, cours
from dofustool.analysis.prices import PriceBook

D = 20_000 * int(DAY)  # minuit UTC du jour du relevé
ITEM, CHEAP, FILLER = 1, 2, 99


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    c.executemany(
        "INSERT INTO items VALUES (?, ?, 1, 'Ressource diverse', 1, 1, 0, 2)",
        [(ITEM, "Écaille"), (CHEAP, "Blé"), (FILLER, "Remplissage")],
    )
    yield c
    c.close()


def history(price: int = 400_000, qty: int = 3) -> list[tuple[float, int, int]]:
    """30 jours de ventes, une par jour à midi : le relevé les donne au kama près."""
    return [(D - d * DAY + 12 * HOUR, price + 100 * d, qty + d % 2) for d in range(29, -1, -1)]


def average(sales, t: float) -> int:
    """Prix moyen du jeu à la date t : fenêtre du jour UTC et des 29 précédents, arrondi au kama."""
    day = int(t // DAY)
    kept = [(p, q) for ts, p, q in sales if ts <= t and day - 29 <= int(ts // DAY) <= day]
    return math.floor(sum(p * q for p, q in kept) / sum(q for _, q in kept) + 0.5)


def capture(conn, sales, at: float, item: int = ITEM) -> None:
    """Relevé du cours à la date at : série journalière (et horaire) exacte."""
    days: dict[int, list[int]] = {}
    for ts, p, q in sales:
        if ts <= at and int(ts // DAY) > int(at // DAY) - 30:
            entry = days.setdefault(int(ts // DAY) * int(DAY), [0, 0])
            entry[0] += p * q
            entry[1] += q
    db.save_market_history(conn, item, GRAIN_DAY, [(d, s // q, q) for d, (s, q) in days.items()], at)
    hours = [(int(ts // HOUR) * int(HOUR), p, q) for ts, p, q in sales if at - DAY < ts <= at]
    db.save_market_history(conn, item, GRAIN_HOUR, hours, at)


def snapshots(conn, sales_by_item: dict, times) -> None:
    """Un relevé de prix moyens à chaque date ; un objet de remplissage change à chaque fois (pas de dédoublonnage)."""
    for k, t in enumerate(times):
        prices = {item: average(sales, t) for item, sales in sales_by_item.items()}
        db.save_snapshot(conn, t, {**prices, FILLER: k + 1})


def hdv(conn, item: int, unit: int, seen: float) -> None:
    conn.execute("INSERT OR REPLACE INTO hdv_listings VALUES (?, 1, ?, 0, 0, 0, '[]', ?, ?)", (item, unit, seen, seen))
    conn.commit()


def sold(points):
    return [(p.qty, round(p.price)) for p in points if p.qty]


def test_single_sale_is_found(conn):
    sales = history()
    capture(conn, sales, D + 13 * HOUR)
    hdv(conn, ITEM, 410_000, D + 13 * HOUR)
    sales.append((D + 14.5 * HOUR, 410_000, 3))
    snapshots(conn, {ITEM: sales}, [D + 14 * HOUR, D + 15 * HOUR, D + 16 * HOUR])
    assert cours.update(conn) == 1
    found = cours.rebuilt(conn, ITEM)
    (point,) = found.points
    assert point.qty == 3 and point.price == pytest.approx(410_000, abs=50) and point.sure
    assert point.end == D + 15 * HOUR and point.hours == 1
    assert found.month_qty == sum(q for _, _, q in sales) and not found.blind
    assert cours.update(conn) == 0  # incrémental : rien de nouveau, rien d'ajouté
    assert len(cours.rebuilt(conn, ITEM).points) == 1


def test_several_hours(conn):
    sales = history()
    capture(conn, sales, D + 1 * HOUR)
    hdv(conn, ITEM, 405_000, D + 1 * HOUR)
    # Les ventes se font à l'annonce la moins chère, à quelques kamas près.
    new = [(D + 2.5 * HOUR, 405_000, 2), (D + 4.2 * HOUR, 405_100, 5), (D + 6.7 * HOUR, 404_950, 1), (D + 7.1 * HOUR, 405_000, 4)]
    sales += new
    snapshots(conn, {ITEM: sales}, [D + h * HOUR for h in range(2, 10)])
    cours.update(conn)
    points = cours.rebuilt(conn, ITEM).points
    assert [p.end for p in points] == [D + 3 * HOUR, D + 5 * HOUR, D + 7 * HOUR, D + 8 * HOUR]
    assert [p.qty for p in points] == [2, 5, 1, 4]
    for point, (_, price, _) in zip(points, new):
        assert point.price == pytest.approx(price, rel=0.002)
    assert all(p.sure for p in points)


def test_midnight_drops_the_oldest_day(conn):
    sales = history()
    capture(conn, sales, D + 21 * HOUR)
    hdv(conn, ITEM, 410_000, D + 21 * HOUR)
    # Rien ne se vend autour de minuit : la sortie du jour le plus ancien ne doit pas passer pour une vente.
    sales += [(D + DAY + 1.5 * HOUR, 409_000, 2), (D + DAY + 2.5 * HOUR, 411_000, 3)]
    snapshots(conn, {ITEM: sales}, [D + h * HOUR for h in (22, 23, 24.5, 25, 26, 27)])
    cours.update(conn)
    points = cours.rebuilt(conn, ITEM).points
    assert [(p.end, p.qty) for p in points] == [(D + 26 * HOUR, 2), (D + 27 * HOUR, 3)]
    assert all(p.sure for p in points)

    # Une vente dans l'intervalle qui passe minuit est retrouvée, mais marquée peu sûre.
    conn2 = db.connect(":memory:")
    conn2.execute("INSERT INTO items VALUES (?, 'Écaille', 1, 'Ressource diverse', 1, 1, 0, 2)", (ITEM,))
    sales = history()
    capture(conn2, sales, D + 22 * HOUR)
    hdv(conn2, ITEM, 410_000, D + 22 * HOUR)
    sales.append((D + 23.5 * HOUR, 410_000, 4))
    snapshots(conn2, {ITEM: sales}, [D + 22.5 * HOUR, D + 24.5 * HOUR])
    cours.update(conn2)
    (point,) = cours.rebuilt(conn2, ITEM).points
    assert point.qty == 4 and point.price == pytest.approx(410_000, rel=0.002) and not point.sure


def test_incoherent_sign_resyncs(conn):
    sales = history()
    capture(conn, sales, D + 1 * HOUR)
    hdv(conn, ITEM, 390_000, D + 1 * HOUR)  # annonce sous le prix moyen…
    sales.append((D + 2.5 * HOUR, 440_000, 3))  # … mais une vente bien au-dessus
    sales.append((D + 4.5 * HOUR, 390_000, 6))
    snapshots(conn, {ITEM: sales}, [D + h * HOUR for h in (2, 3, 4, 5)])
    cours.update(conn)
    first, second = cours.rebuilt(conn, ITEM).points
    assert first.qty == 0 and not first.sure  # resynchronisation, aucune vente comptée
    # Après resynchronisation, S/Q redonne le prix moyen reçu : la vente suivante est lue normalement.
    assert second.qty == 6 and second.price == pytest.approx(390_000, rel=0.002) and second.sure


def test_gap_merges_hours(conn):
    sales = history()
    capture(conn, sales, D + 1 * HOUR)
    hdv(conn, ITEM, 408_000, D + 1 * HOUR)
    sales += [(D + 3.5 * HOUR, 408_000, 2), (D + 5.5 * HOUR, 408_000, 3)]
    snapshots(conn, {ITEM: sales}, [D + 2 * HOUR, D + 8 * HOUR])  # cinq heures sans relevé
    cours.update(conn)
    (point,) = cours.rebuilt(conn, ITEM).points
    assert (point.qty, point.hours) == (5, 6) and point.price == pytest.approx(408_000, rel=0.002)


def test_new_capture_resets(conn):
    sales = history()
    capture(conn, sales, D + 1 * HOUR)
    hdv(conn, ITEM, 410_000, D + 1 * HOUR)
    sales.append((D + 2.5 * HOUR, 410_000, 3))
    snapshots(conn, {ITEM: sales}, [D + 2 * HOUR, D + 3 * HOUR])
    cours.update(conn)
    assert len(cours.rebuilt(conn, ITEM).points) == 1
    capture(conn, sales, D + 3.5 * HOUR)  # nouveau relevé réel : il fait foi
    cours.update(conn)
    found = cours.rebuilt(conn, ITEM)
    assert found.base_at == D + 3.5 * HOUR and found.points == () and found.processed_ts == found.base_at
    sales.append((D + 4.5 * HOUR, 409_000, 2))
    snapshots(conn, {ITEM: sales}, [D + 5 * HOUR])
    cours.update(conn)
    assert sold(cours.rebuilt(conn, ITEM).points) == [(2, pytest.approx(409_000, rel=0.002))]


def test_cheap_item_gives_no_signal(conn):
    sales = [(D - d * DAY + 12 * HOUR, 10, 3_000) for d in range(29, -1, -1)]
    capture(conn, sales, D + 13 * HOUR, item=CHEAP)
    hdv(conn, CHEAP, 11, D + 13 * HOUR)
    sales += [(D + 14.5 * HOUR, 11, 200), (D + 15.5 * HOUR, 11, 300)]
    snapshots(conn, {CHEAP: sales}, [D + h * HOUR for h in (14, 15, 16)])
    cours.update(conn)
    found = cours.rebuilt(conn, CHEAP)
    assert found.points == ()  # le prix moyen arrondi n'a pas bougé
    assert found.min_qty > 40_000 and found.blind
    assert CHEAP not in cours.liquidity(conn)  # pas de quantités reconstituées trompeuses


def test_liquidity_extends_the_capture(conn):
    sales = history()
    capture(conn, sales, D + 13 * HOUR)
    hdv(conn, ITEM, 410_000, D + 13 * HOUR)
    sales += [(D + 14.5 * HOUR, 410_000, 3), (D + DAY + 2.5 * HOUR, 410_000, 2)]
    snapshots(conn, {ITEM: sales}, [D + h * HOUR for h in (14, 15, 16, 26, 27)])
    before = PriceBook(conn, D + 13 * HOUR, 24).liquidity(ITEM)
    cours.update(conn)
    book = PriceBook(conn, D + 27 * HOUR, 24)
    after = book.liquidity(ITEM)
    # 24 h (et une tranche) avant le dernier prix moyen, D+27h : la vente de midi du relevé, puis les deux reconstituées.
    assert after.qty_24h == 3 + 3 + 2
    # 7 j glissent d'un jour : le jour D-6 (3 vendus) sort, les 5 reconstitués entrent.
    assert after.qty_7d == before.qty_7d - 3 + 5 and book.rebuilt_at(ITEM) == D + 27 * HOUR
    days = cours.daily(conn, ITEM, cours.rebuilt(conn, ITEM))
    assert [(d, q) for d, _, q in days] == [(D, 3 + 3), (D + int(DAY), 2)]  # jour du relevé : relevé + reconstitué


def test_relative_share_without_capture(conn):
    sales = history()
    start = sum(q for _, _, q in sales)
    sales += [(D + 14.5 * HOUR, 410_000, 3), (D + 16.5 * HOUR, 405_000, 6)]
    snapshots(conn, {ITEM: sales}, [D + h * HOUR for h in (14, 15, 16, 17, 18)])
    found = cours.relative(conn, ITEM, 410_000)
    assert [t for t, _, _ in found.points] == [D + h * HOUR for h in (15, 16, 17, 18)]
    shares = [s for _, _, s in found.points]
    assert shares[0] == pytest.approx(3 / start, rel=0.05) and shares[1] == 0 and shares[3] == 0
    assert shares[2] > 0  # vente à 405 000 lue au prix 410 000 : sous-estimée, mais vue
    assert found.hours == 4 and found.moved == 2 and found.skipped == 0
    assert found.pace == pytest.approx(found.share / 4 * 720)

    # Prix du moment sous le prix moyen alors qu'il monte : intervalle écarté.
    assert cours.relative(conn, ITEM, 390_000).skipped == 2
    # Intervalles à cheval sur minuit UTC : ignorés.
    snapshots(conn, {ITEM: sales}, [D + DAY + 1 * HOUR])
    assert len(cours.relative(conn, ITEM, 410_000).points) == 4
    assert cours.relative(conn, ITEM, None) is None


def test_item_page(tmp_path):
    import time

    from dofustool.web.api import Api

    path = tmp_path / "market.sqlite"
    conn = db.connect(path)
    conn.executemany(
        "INSERT INTO items VALUES (?, ?, 1, 'Ressource diverse', 1, 1, 0, 2)",
        [(ITEM, "Écaille"), (CHEAP, "Sans relevé"), (FILLER, "Remplissage")],
    )
    day = int(time.time() // DAY - 1) * DAY  # hier, minuit UTC : toute la série est passée
    shift = day - D
    sales = [(ts + shift, p, q) for ts, p, q in history()]
    other = [(ts + shift, p // 2, q) for ts, p, q in history()]
    capture(conn, sales, day + 1 * HOUR)
    hdv(conn, ITEM, 410_000, day + 1 * HOUR)
    hdv(conn, CHEAP, 205_000, time.time() - 60)  # annonce fraîche : le rythme est calculable
    sales.append((day + 2.5 * HOUR, 410_000, 3))
    other.append((day + 2.5 * HOUR, 205_000, 2))
    snapshots(conn, {ITEM: sales, CHEAP: other}, [day + h * HOUR for h in (2, 3, 4)])
    conn.close()

    api = Api(path, tmp_path / "config.toml")
    page = api.item(ITEM)
    assert page["relative"] is None and not page["rebuilt"]["blind"]
    assert [(q, sure) for _, _, q, sure, _ in page["rebuilt"]["hourly"]] == [(3, True)]
    assert page["rebuilt"]["daily"] == [[day, pytest.approx(410_000, rel=0.002), 3]]  # relevé fait à 1 h, avant la vente de midi
    assert page["qty_7d"] == PriceBook(db.connect(path), time.time(), 24).liquidity(ITEM).qty_7d

    page = api.item(CHEAP)
    assert page["rebuilt"] is None and page["relative"]["observed"]
    month = sum(q for ts, _, q in other if ts <= day + 2 * HOUR)  # volume de la fenêtre au début de l'intervalle
    # L'arrondi au kama d'un prix moyen qui bouge de 80 kamas pèse quelques pour cent.
    assert [p[2] for p in page["relative"]["points"]] == [pytest.approx(100 * 2 / month, rel=0.05), 0]
    assert page["relative"]["pace"] > 0


def test_reference_too_close_or_absurd_quantity_is_not_a_sale(conn):
    """Un équipement vendu à des prix très dispersés : le dernier prix connu colle au prix moyen, la division explose."""
    window = cours.Window({D: [340 * 12_000_000.0, 340]})
    # p0 à 0,2 % du prix moyen reçu : aucune quantité déduite, simple resynchronisation.
    point = cours.step(window, D + 1 * HOUR, D + 2 * HOUR, 12_050_000, 12_074_000)
    assert point.qty == 0 and not point.sure
    assert window.totals()[1] == 340 and window.totals()[0] / 340 == pytest.approx(12_050_000)
    # p0 à 1,5 % : la quantité déduite (plus de cent pour un objet qui s'en vend onze par jour) est refusée.
    point = cours.step(window, D + 2 * HOUR, D + 3 * HOUR, 12_150_000, 12_335_000)
    assert point.qty == 0 and not point.sure and window.totals()[1] == 340
    # Une vraie vente, à bonne distance du prix moyen, reste lue.
    point = cours.step(window, D + 3 * HOUR, D + 4 * HOUR, 12_185_000, 16_200_000)
    assert point.qty == 3 and point.price == pytest.approx(16_150_000, rel=0.01)


def test_a_new_capture_checks_what_was_deduced(conn):
    """Le cours est relevé à nouveau : ce qui avait été déduit depuis le relevé précédent est comparé au réel."""
    sales = history()
    capture(conn, sales, D + 1 * HOUR)
    hdv(conn, ITEM, 410_000, D + 1 * HOUR)
    assert cours.check(conn)["points"] == 0
    sales.append((D + 2.5 * HOUR, 410_000, 3))
    snapshots(conn, {ITEM: sales}, [D + 2 * HOUR, D + 3 * HOUR])
    cours.update(conn)
    sales.append((D + 3.2 * HOUR, 410_000, 2))  # vendu après le dernier prix moyen traité : pas encore déduit
    capture(conn, sales, D + 3.5 * HOUR)
    cours.update(conn)
    assert conn.execute("SELECT base_at, checked_at, real_qty, deduced_qty, shown FROM cours_checks").fetchall() == [
        (D + 1 * HOUR, D + 3.5 * HOUR, 5, 3, 1)
    ]
    found = cours.check(conn)
    assert (found["points"], found["sold"], found["quiet"], found["measured"]) == (1, 1, 0, False)
    assert found["exact"] == 0 and found["double"] == 1 and found["missed"] == 0 and found["ratio"] == pytest.approx(0.6)
    # Lendemain : le jour le plus ancien (4 ventes) est sorti de la fenêtre. Seule la vente de midi (3) est nouvelle.
    snapshots(conn, {ITEM: sales}, [D + DAY + 1 * HOUR])
    cours.update(conn)
    capture(conn, sales, D + DAY + 2 * HOUR)
    cours.update(conn)
    assert conn.execute("SELECT real_qty FROM cours_checks WHERE checked_at = ?", (D + DAY + 2 * HOUR,)).fetchone() == (3,)
    # Un intervalle presque jamais suivi (aucun prix moyen traité) est noté mais écarté de la mesure.
    capture(conn, sales, D + DAY + 9 * HOUR)
    cours.update(conn)
    assert conn.execute("SELECT COUNT(*) FROM cours_checks").fetchone() == (3,)
    assert cours.check(conn)["set_aside"] >= 1
