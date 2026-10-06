"""Prix du moment estimé à partir du glissement horaire des prix moyens.

Le prix moyen du jeu est la moyenne des ventes des 30 derniers jours, pondérée par les quantités
(vérifié au kama près sur les objets dont on a le cours du marché). Il est recalculé toutes les
heures : chaque heure de ventes y pèse environ 1/720. S'il a bougé de d kamas en h heures, les
ventes récentes se sont faites autour de

    prix moyen + d × 720 / h

Hypothèses, à garder en tête : les ventes sont supposées régulières, et le jour le plus ancien qui
sort chaque jour de la fenêtre fausse la variation. On ne mesure donc qu'à l'intérieur d'une même
journée UTC (le moment exact de ce décalage n'est pas vérifié). `check` confronte en permanence
l'estimation aux annonces HDV relevées : c'est elle qui dit ce que vaut la méthode.
"""
import sqlite3
import statistics
from dataclasses import dataclass

from . import DAY

WINDOW_HOURS = 720.0  # fenêtre du prix moyen : 30 jours
LOOKBACK_HOURS = 48.0  # relevés pris en compte, en remontant depuis le plus récent
MIN_HOURS = 3.0  # en dessous, l'extrapolation est trop instable
CLAMP = (0.2, 5.0)  # une estimation hors de ces multiples du prix moyen est ramenée à la borne

RELIABLE = "fiable"
ROUGH = "indicatif"
# Fourchette affichée tant que le contrôle n'a pas assez de points pour la mesurer.
DEFAULT_SPREAD = {RELIABLE: 0.15, ROUGH: 0.35}
MIN_CHECK_POINTS = 20


@dataclass(frozen=True, slots=True)
class Estimate:
    price: float
    avg: int  # prix moyen du dernier relevé
    delta: int  # variation cumulée du prix moyen sur les heures observées
    hours: float
    confidence: str
    ts: float  # date du dernier relevé


def _confidence(price: float, hours: float, moved: int, same_sign: int, clamped: bool) -> str | None:
    # Le prix moyen est arrondi au kama : une erreur de 1 kama sur la variation en vaut 720/h sur le prix.
    noise = WINDOW_HOURS / hours / max(price, 1.0)
    if hours >= 6 and moved >= 3 and same_sign >= 0.7 * moved and noise <= 0.05 and not clamped:
        return RELIABLE
    if noise <= 0.3:
        return ROUGH
    return None


def estimate_prices(conn: sqlite3.Connection, lookback_hours: float = LOOKBACK_HOURS) -> dict[int, Estimate]:
    """Estimation de chaque objet dont le prix moyen a bougé dans les relevés récents."""
    latest = conn.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
    if latest is None:
        return {}
    snapshots = conn.execute(
        "SELECT id, ts FROM snapshots WHERE ts >= ? ORDER BY ts, id", (latest - lookback_hours * 3600,)
    ).fetchall()
    if len(snapshots) < 2:
        return {}
    prices = [dict(conn.execute("SELECT item_id, price FROM avg_prices WHERE snapshot_id = ?", (sid,))) for sid, _ in snapshots]

    delta: dict[int, int] = {}
    hours: dict[int, float] = {}
    ups: dict[int, int] = {}
    downs: dict[int, int] = {}
    for (_, t0), (_, t1), before, after in zip(snapshots, snapshots[1:], prices, prices[1:]):
        if int(t0 // DAY) != int(t1 // DAY):
            continue  # à cheval sur deux jours UTC : un jour ancien est sorti de la fenêtre entre-temps
        span = (t1 - t0) / 3600
        for item_id, new in after.items():
            old = before.get(item_id)
            if old is None:
                continue
            hours[item_id] = hours.get(item_id, 0.0) + span
            if new != old:
                delta[item_id] = delta.get(item_id, 0) + new - old
                counter = ups if new > old else downs
                counter[item_id] = counter.get(item_id, 0) + 1

    out: dict[int, Estimate] = {}
    current = prices[-1]
    for item_id, total in delta.items():
        avg = current.get(item_id)
        span = hours.get(item_id, 0.0)
        if not avg or total == 0 or span < MIN_HOURS:
            continue
        raw = avg + total * WINDOW_HOURS / span
        price = min(max(raw, CLAMP[0] * avg), CLAMP[1] * avg)
        up, down = ups.get(item_id, 0), downs.get(item_id, 0)
        confidence = _confidence(price, span, up + down, up if total > 0 else down, price != raw)
        if confidence is not None:
            out[item_id] = Estimate(price, avg, total, span, confidence, latest)
    return out


def check(conn: sqlite3.Connection, estimates: dict[int, Estimate], lookback_hours: float = LOOKBACK_HOURS) -> dict[str, dict]:
    """Confronte les estimations aux annonces HDV relevées sur la même période (ressources et consommables).

    Par niveau de confiance : nombre de points, erreur médiane de l'estimation et du prix moyen brut,
    et fourchette (70e centile de l'erreur) à afficher avec le prix estimé.
    """
    latest = conn.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
    errors: dict[str, list[tuple[float, float]]] = {RELIABLE: [], ROUGH: []}
    if latest is not None:
        rows = conn.execute(
            "SELECT h.item_id, MIN(CASE WHEN h.p1 > 0 THEN h.p1 END), MIN(CASE WHEN h.p10 > 0 THEN h.p10 / 10.0 END), "
            " MIN(CASE WHEN h.p100 > 0 THEN h.p100 / 100.0 END), MIN(CASE WHEN h.p1000 > 0 THEN h.p1000 / 1000.0 END) "
            "FROM hdv_current h JOIN items i ON i.id = h.item_id "
            "WHERE i.category_id IN (1, 2) AND h.captured_at >= ? GROUP BY h.item_id",
            (latest - lookback_hours * 3600,),
        )
        for item_id, *units in rows:
            estimate = estimates.get(item_id)
            asked = [u for u in units if u]
            if estimate is not None and asked:
                real = min(asked)
                errors[estimate.confidence].append((abs(estimate.price / real - 1), abs(estimate.avg / real - 1)))
    out = {}
    for level, points in errors.items():
        entry = {"points": len(points), "error": None, "avg_error": None, "within_20": None, "spread": DEFAULT_SPREAD[level], "measured": False}
        if points:
            mine = sorted(e for e, _ in points)
            entry["error"] = statistics.median(mine)
            entry["avg_error"] = statistics.median(a for _, a in points)
            entry["within_20"] = sum(e <= 0.2 for e in mine) / len(mine)
            if len(points) >= MIN_CHECK_POINTS:
                entry["spread"] = min(0.6, max(0.05, mine[int(0.7 * (len(mine) - 1))]))
                entry["measured"] = True
        out[level] = entry
    return out
