"""Cours du marché sans ouvrir l'onglet en jeu, déduit des relevés horaires de prix moyens.

Le prix moyen du jeu vaut exactement somme(prix × quantité) ÷ somme(quantité) des ventes de la
fenêtre de 30 jours (le jour UTC courant et les 29 précédents), arrondi au kama. Il est renvoyé
environ toutes les heures. Chaque relevé dit donc quelque chose de ce qui s'est vendu depuis le
précédent.

Temps 1, sans relevé du cours : entre deux relevés A puis A' du même jour UTC, avec un prix du
moment p connu par ailleurs, la part du volume mensuel vendue dans l'intervalle vaut

    (A' − A) ÷ (p − A')

Le volume lui-même reste inconnu : on n'a qu'un rythme relatif.

Temps 2, après un relevé du cours : sa série journalière donne la somme S et la quantité Q exactes
de la fenêtre. Chaque nouveau prix moyen A' révèle alors la vente de l'intervalle :

    q = (A'·Q − S) ÷ (p0 − A')    puis    p = (A'·(Q + q) − S) ÷ q

où p0 est un prix de référence (annonce HDV fraîche pour une ressource, sinon dernier prix connu).
Les points reconstitués ont leurs propres tables : market_history ne contient que du réel, et elle
est partagée avec le hub.

Limites, à afficher : un objet bon marché et très échangé ne fait pas bouger son prix moyen
arrondi (aucun signal) ; les heures sans relevé se fondent en un seul intervalle ; sans annonce HDV
récente, le partage entre prix et quantité est incertain ; la sortie du jour le plus ancien est
supposée à minuit UTC, ce qui n'a été observé qu'indirectement.
"""
import sqlite3
from dataclasses import dataclass

from . import DAY, GRAIN_DAY, GRAIN_HOUR, HOUR

WINDOW_DAYS = 30
WINDOW_HOURS = 720.0
ROUNDING = 0.5  # le prix moyen est arrondi au kama
SURE_SPREAD = 0.15  # écart toléré entre le prix déduit et p0 pour un point sûr
ABSURD = 5.0  # un prix déduit hors de [p0 ÷ 5, p0 × 5] n'est pas une vente plausible
MIN_GAP = 0.005  # en dessous de 0,5 % d'écart entre p0 et le prix moyen, la quantité déduite n'a plus de sens
MAX_PACE = 15.0  # une vente déduite à plus de 15 fois le rythme moyen du mois est une erreur de p0, pas une vente
MIN_BURST = 10.0  # … sauf petite quantité : dix exemplaires d'un coup restent plausibles pour un objet rare
HDV_FRESH_HOURS = 24.0  # une annonce HDV sert de p0 si elle a été vue à moins de 24 h du relevé
LOOKBACK_HOURS = 48.0  # temps 1 : relevés pris en compte, en remontant depuis le plus récent


# --- temps 1 : cours relatif -------------------------------------------------


@dataclass(frozen=True, slots=True)
class Relative:
    points: tuple[tuple[float, float, float], ...]  # (fin de l'intervalle, durée en heures, part du mois vendue)
    hours: float  # heures couvertes par les intervalles retenus
    share: float  # part du volume mensuel vendue sur ces heures
    moved: int  # intervalles où le prix moyen a bougé
    skipped: int  # intervalles écartés : signe incohérent avec le prix du moment

    @property
    def pace(self) -> float | None:
        """Rythme des ventes rapporté au rythme moyen du mois (1 = 1/720 du volume mensuel par heure)."""
        return self.share / self.hours * WINDOW_HOURS if self.hours else None


def relative(conn: sqlite3.Connection, item_id: int, price: float | None, lookback_hours: float = LOOKBACK_HOURS) -> Relative | None:
    """Part du volume mensuel vendue entre deux relevés de prix moyens, au prix du moment `price`."""
    latest = conn.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
    if latest is None or not price:
        return None
    rows = conn.execute(
        "SELECT s.ts, p.price FROM avg_prices p JOIN snapshots s ON s.id = p.snapshot_id "
        "WHERE p.item_id = ? AND s.ts >= ? ORDER BY s.ts",
        (item_id, latest - lookback_hours * HOUR),
    ).fetchall()
    if len(rows) < 2:
        return None
    points = []
    hours = share = 0.0
    moved = skipped = 0
    for (t0, before), (t1, after) in zip(rows, rows[1:]):
        if int(t0 // DAY) != int(t1 // DAY) or before <= 0 or after <= 0:
            continue  # à cheval sur deux jours UTC : un jour ancien est sorti de la fenêtre entre-temps
        part = 0.0
        if after != before:
            moved += 1
            if price == after or (after - before) * (price - after) < 0:
                skipped += 1
                continue
            part = (after - before) / (price - after)
        span = (t1 - t0) / HOUR
        points.append((t1, span, part))
        hours += span
        share += part
    return Relative(tuple(points), hours, share, moved, skipped)


# --- temps 2 : reconstitution après un relevé --------------------------------


@dataclass(frozen=True, slots=True)
class Point:
    end: float  # date du relevé de prix moyens qui ferme l'intervalle
    hours: float
    total: float  # somme ajoutée à la fenêtre (prix × quantité)
    qty: int  # 0 : resynchronisation, aucune vente comptée
    sure: bool

    @property
    def price(self) -> float | None:
        return self.total / self.qty if self.qty else None


class Window:
    """Ventes de la fenêtre du prix moyen, par jour UTC : {début du jour: [somme, quantité]}."""

    def __init__(self, days: dict[int, list[float]] | None = None) -> None:
        self.days = days or {}

    def totals(self) -> tuple[float, int]:
        return sum(d[0] for d in self.days.values()), int(sum(d[1] for d in self.days.values()))

    def add(self, day: int, total: float, qty: int) -> None:
        entry = self.days.setdefault(day, [0.0, 0])
        entry[0] += total
        entry[1] += qty

    def move_to(self, ts: float) -> None:
        """Retire les jours sortis de la fenêtre à la date ts."""
        first = (int(ts // DAY) - WINDOW_DAYS + 1) * int(DAY)
        for day in [d for d in self.days if d < first]:
            del self.days[day]


def step(window: Window, prev_ts: float, ts: float, avg: int, ref: float | None) -> Point | None:
    """Fait avancer la fenêtre jusqu'au relevé de prix moyen `avg` (date ts).

    Renvoie le point reconstitué, ou None si aucune vente n'est détectée. Un point incohérent
    (qty 0) resynchronise la fenêtre pour que S/Q redonne le prix moyen reçu.
    """
    window.move_to(ts)
    total, qty = window.totals()
    if qty and abs(avg - total / qty) <= ROUNDING:
        return None
    hours = (ts - prev_ts) / HOUR
    # Le jour le plus ancien sort à minuit UTC, en principe : le premier intervalle après minuit est peu sûr.
    after_midnight = int(prev_ts // DAY) != int(ts // DAY)
    gap = avg * qty - total
    if not qty:
        point = Point(ts, hours, float(avg), 1, False)  # fenêtre vide : au moins une vente, au prix moyen
    elif ref is None or abs(ref - avg) < MIN_GAP * avg or gap * (ref - avg) < 0:
        # p0 trop proche du prix moyen : diviser par leur écart ferait exploser la quantité. C'est le cas
        # d'un équipement dont chaque vente se fait à un prix très différent selon ses jets.
        point = Point(ts, hours, gap, 0, False)
    else:
        sold = max(1, round(gap / (ref - avg)))
        price = (avg * (qty + sold) - total) / sold
        usual = qty * max(hours, 1.0) / WINDOW_HOURS  # ce qui se vend d'ordinaire sur cette durée
        if not ref / ABSURD <= price <= ref * ABSURD or sold > max(MIN_BURST, MAX_PACE * usual):
            point = Point(ts, hours, gap, 0, False)
        else:
            # L'arrondi du prix moyen (± 0,5) fait varier la quantité déduite d'autant.
            noise = ROUNDING * (qty + sold) / abs(ref - avg)
            sure = abs(price / ref - 1) <= SURE_SPREAD and noise <= max(1.0, 0.25 * sold) and not after_midnight
            point = Point(ts, hours, price * sold, sold, sure)
    window.add(int(ts // DAY) * int(DAY), point.total, point.qty)
    return point


def sensitivity(window: Window, avg: int, ref: float | None) -> float | None:
    """Plus petite quantité vendue au prix ref qui fait bouger le prix moyen arrondi (None : aucune)."""
    _, qty = window.totals()
    if ref is None or ref == avg:
        return None
    return ROUNDING * qty / abs(ref - avg)


def _asks(conn: sqlite3.Connection) -> dict[int, tuple[float, float]]:
    """{item: (prix unitaire le plus bas, date du relevé)} des ressources et consommables vus à l'HDV."""
    rows = conn.execute(
        "SELECT h.item_id, MIN(CASE WHEN h.p1 > 0 THEN h.p1 END), MIN(CASE WHEN h.p10 > 0 THEN h.p10 / 10.0 END), "
        " MIN(CASE WHEN h.p100 > 0 THEN h.p100 / 100.0 END), MIN(CASE WHEN h.p1000 > 0 THEN h.p1000 / 1000.0 END), "
        " MAX(h.captured_at) "
        "FROM hdv_current h JOIN items i ON i.id = h.item_id WHERE i.category_id != 0 GROUP BY h.item_id"
    )
    out = {}
    for item_id, *units, seen in rows:
        asked = [u for u in units if u]
        if asked:
            out[item_id] = (min(asked), seen)
    return out


def _window(conn: sqlite3.Connection, item_id: int, base_at: float) -> Window:
    """Fenêtre au relevé de départ, plus les points déjà reconstitués."""
    window = Window()
    for day, price, qty in conn.execute(
        "SELECT bucket_ts, price, COALESCE(qty_sold, 0) FROM market_history "
        "WHERE item_id = ? AND period = ? AND captured_at = ?",
        (item_id, GRAIN_DAY, base_at),
    ):
        window.add(day, price * qty, qty)
    for end, total, qty in conn.execute("SELECT end_ts, total, qty FROM cours_points WHERE item_id = ?", (item_id,)):
        window.add(int(end // DAY) * int(DAY), total, qty)
    return window


def _first_price(conn: sqlite3.Connection, item_id: int, base_at: float) -> float | None:
    """Dernier prix de vente du relevé de départ : tranche horaire la plus récente, sinon journalière."""
    for grain in (GRAIN_HOUR, GRAIN_DAY):
        row = conn.execute(
            "SELECT price FROM market_history WHERE item_id = ? AND period = ? AND captured_at = ? "
            "ORDER BY bucket_ts DESC LIMIT 1",
            (item_id, grain, base_at),
        ).fetchone()
        if row is not None:
            return float(row[0])
    return None


def update(conn: sqlite3.Connection, fresh_hours: float = HDV_FRESH_HOURS) -> int:
    """Traite les relevés de prix moyens arrivés depuis le dernier passage. Renvoie le nombre de points ajoutés.

    Un nouveau relevé du cours d'un objet (série journalière plus récente) repart de zéro pour cet objet.
    """
    bases = dict(
        conn.execute("SELECT item_id, MAX(captured_at) FROM market_history WHERE period = ? GROUP BY item_id", (GRAIN_DAY,))
    )
    states = {row[0]: row[1:] for row in conn.execute("SELECT item_id, base_at, processed_ts, last_price FROM cours_state")}
    asks = _asks(conn)
    added = 0
    with conn:
        for item_id in states.keys() - bases.keys():  # cours disparu (purge du hub) : plus de point de départ
            conn.execute("DELETE FROM cours_state WHERE item_id = ?", (item_id,))
            conn.execute("DELETE FROM cours_points WHERE item_id = ?", (item_id,))
        for item_id, base_at in bases.items():
            state = states.get(item_id)
            fresh = state is None or state[0] != base_at
            if fresh:
                conn.execute("DELETE FROM cours_points WHERE item_id = ?", (item_id,))
                state = (base_at, base_at, _first_price(conn, item_id, base_at))
            _, processed, last_price = state
            rows = conn.execute(
                "SELECT s.ts, p.price FROM avg_prices p JOIN snapshots s ON s.id = p.snapshot_id "
                "WHERE p.item_id = ? AND s.ts > ? ORDER BY s.ts",
                (item_id, processed),
            ).fetchall()
            if not rows and not fresh:
                continue
            window = _window(conn, item_id, base_at)
            ask = asks.get(item_id)
            month_qty = min_qty = None
            for ts, avg in rows:
                if avg > 0:
                    ref = ask[0] if ask is not None and abs(ask[1] - ts) <= fresh_hours * HOUR else last_price
                    point = step(window, processed, ts, avg, ref)
                    if point is not None:
                        conn.execute(
                            "INSERT OR REPLACE INTO cours_points VALUES (?, ?, ?, ?, ?, ?)",
                            (item_id, point.end, point.hours, point.total, point.qty, int(point.sure)),
                        )
                        added += 1
                        if point.sure:
                            last_price = point.price
                    month_qty = window.totals()[1]
                    min_qty = sensitivity(window, avg, ref)
                processed = ts
            conn.execute(
                "INSERT INTO cours_state VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (item_id) DO UPDATE SET "
                "base_at = excluded.base_at, processed_ts = excluded.processed_ts, last_price = excluded.last_price, "
                "month_qty = COALESCE(excluded.month_qty, month_qty), min_qty = CASE WHEN excluded.month_qty IS NULL "
                "THEN min_qty ELSE excluded.min_qty END",
                (item_id, base_at, processed, last_price, month_qty, min_qty),
            )
    return added


@dataclass(frozen=True, slots=True)
class Rebuilt:
    base_at: float  # date du relevé de départ
    processed_ts: float  # dernier relevé de prix moyens traité
    month_qty: int | None  # quantité vendue sur 30 j, d'après la reconstitution
    min_qty: float | None  # vente la plus petite qui fait bouger le prix moyen (None : aucune)
    points: tuple[Point, ...]

    @property
    def blind(self) -> bool:
        """Prix moyen trop peu sensible : même une journée de ventes ordinaire peut passer inaperçue."""
        if self.processed_ts <= self.base_at or self.month_qty is None:
            return False  # rien de traité encore : le relevé seul fait foi
        return self.min_qty is None or self.min_qty > max(1.0, self.month_qty / WINDOW_DAYS)


def rebuilt(conn: sqlite3.Connection, item_id: int) -> Rebuilt | None:
    state = conn.execute(
        "SELECT base_at, processed_ts, month_qty, min_qty FROM cours_state WHERE item_id = ?", (item_id,)
    ).fetchone()
    if state is None:
        return None
    points = tuple(
        Point(end, hours, total, qty, bool(sure))
        for end, hours, total, qty, sure in conn.execute(
            "SELECT end_ts, hours, total, qty, sure FROM cours_points WHERE item_id = ? ORDER BY end_ts", (item_id,)
        )
    )
    return Rebuilt(*state, points)


def daily(conn: sqlite3.Connection, item_id: int, state: Rebuilt) -> list[tuple[int, float, int]]:
    """Jours touchés par la reconstitution : (début du jour, prix moyen, quantité), relevé compris pour le jour du relevé."""
    days: dict[int, list[float]] = {}
    for point in state.points:
        if point.qty:
            entry = days.setdefault(int(point.end // DAY) * int(DAY), [0.0, 0])
            entry[0] += point.total
            entry[1] += point.qty
    for day, price, qty in conn.execute(
        "SELECT bucket_ts, price, COALESCE(qty_sold, 0) FROM market_history WHERE item_id = ? AND period = ? AND captured_at = ?",
        (item_id, GRAIN_DAY, state.base_at),
    ):
        if day in days:
            days[day][0] += price * qty
            days[day][1] += qty
    return [(day, total / qty, qty) for day, (total, qty) in sorted(days.items()) if qty]


def liquidity(conn: sqlite3.Connection) -> dict[int, tuple[int, int, float]]:
    """{item: (vendus sur 24 h, vendus sur 7 j, date du dernier relevé traité)}, relevé et reconstitution réunis.

    Seulement pour les objets dont la reconstitution a avancé et dont le prix moyen est assez sensible.
    Mêmes tranches que PriceBook : 24 h débordent sur une 25e tranche horaire, 7 j = le jour et les 6 d'avant.
    """
    out = {}
    for item_id, base_at, processed, month_qty, min_qty in conn.execute(
        "SELECT item_id, base_at, processed_ts, month_qty, min_qty FROM cours_state WHERE processed_ts > base_at"
    ).fetchall():
        if Rebuilt(base_at, processed, month_qty, min_qty, ()).blind:
            continue
        # Plus de resynchronisations que de ventes lues : la reconstitution ne suit pas cet objet
        # (prix de vente trop dispersés). Mieux vaut les quantités du relevé qu'un faux zéro.
        read, lost = conn.execute(
            "SELECT COALESCE(SUM(qty > 0), 0), COALESCE(SUM(qty = 0), 0) FROM cours_points WHERE item_id = ?", (item_id,)
        ).fetchone()
        if lost > read:
            continue
        hour = int(processed // HOUR) * int(HOUR)
        day = int(processed // DAY) * int(DAY)
        qty_24h, qty_7d = conn.execute(
            "SELECT "
            " (SELECT COALESCE(SUM(qty_sold), 0) FROM market_history WHERE item_id = :i AND period = :hour AND bucket_ts >= :h) "
            " + (SELECT COALESCE(SUM(qty), 0) FROM cours_points WHERE item_id = :i AND end_ts > :since), "
            " (SELECT COALESCE(SUM(qty_sold), 0) FROM market_history WHERE item_id = :i AND period = :daily AND bucket_ts >= :d) "
            " + (SELECT COALESCE(SUM(qty), 0) FROM cours_points WHERE item_id = :i AND end_ts >= :d)",
            {
                "i": item_id, "hour": GRAIN_HOUR, "daily": GRAIN_DAY, "h": hour - 24 * int(HOUR),
                "since": processed - DAY, "d": day - 6 * int(DAY),
            },
        ).fetchone()
        out[item_id] = (qty_24h, qty_7d, processed)
    return out
