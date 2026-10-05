"""Écart du prix courant d'un item à sa tendance."""
import sqlite3
from dataclasses import dataclass

from . import DAY, GRAIN_DAY

MARKET_HISTORY = "cours du marché"
SNAPSHOTS = "relevés de prix moyens"
INSUFFICIENT = "données insuffisantes"

UNDER = "sous-coté"
OVER = "sur-coté"


@dataclass(frozen=True, slots=True)
class Trend:
    item_id: int
    basis: str
    current: int | None = None
    mean_7d: float | None = None
    mean_30d: float | None = None
    samples: int = 0  # relevés ou tranches ayant servi au calcul

    @property
    def dev_7d(self) -> float | None:
        return self.current / self.mean_7d - 1 if self.current is not None and self.mean_7d else None

    @property
    def dev_30d(self) -> float | None:
        return self.current / self.mean_30d - 1 if self.current is not None and self.mean_30d else None

    @property
    def deviation(self) -> float | None:
        """Écart retenu pour le signal : sur 30 j s'il existe, sinon sur 7 j."""
        return self.dev_30d if self.dev_30d is not None else self.dev_7d

    def signal(self, threshold: float) -> str | None:
        deviation = self.deviation
        if deviation is None:
            return None
        if deviation <= -threshold:
            return UNDER
        if deviation >= threshold:
            return OVER
        return None


def compute_trends(
    conn: sqlite3.Connection, now: float, min_snapshots: int, last_sale_max_age_hours: float
) -> dict[int, Trend]:
    """Tendance de chaque item ayant au moins un prix.

    Avec un historique du cours du marché et un dernier prix de vente récent, le signal est
    immédiat. Sinon on compare le prix moyen du dernier relevé à la moyenne des relevés
    précédents, à condition d'en avoir assez.
    """
    trends: dict[int, Trend] = {}

    # Moyennes des prix journaliers pondérées par les quantités vendues, comme le « prix moyen »
    # affiché par le jeu (vérifié sur trois objets).
    history: dict[int, tuple[float | None, float | None, int]] = {
        item_id: (mean_7d, mean_30d, count)
        for item_id, mean_7d, mean_30d, count in conn.execute(
            "SELECT item_id, "
            " 1.0 * SUM(CASE WHEN bucket_ts > :week THEN price * MAX(qty_sold, 1) END) "
            "   / SUM(CASE WHEN bucket_ts > :week THEN MAX(qty_sold, 1) END), "
            " 1.0 * SUM(price * MAX(qty_sold, 1)) / SUM(MAX(qty_sold, 1)), COUNT(*) "
            "FROM market_history WHERE period = :grain AND bucket_ts > :month GROUP BY item_id",
            {"grain": GRAIN_DAY, "week": now - 7 * DAY, "month": now - 30 * DAY},
        )
    }
    for item_id, price, _ in conn.execute(
        "SELECT item_id, price, MAX(sold_at) FROM last_sales WHERE sold_at >= ? GROUP BY item_id",
        (now - last_sale_max_age_hours * 3600,),
    ):
        if item_id in history:
            mean_7d, mean_30d, count = history[item_id]
            trends[item_id] = Trend(item_id, MARKET_HISTORY, price, mean_7d, mean_30d, count)

    latest = conn.execute("SELECT id, ts FROM snapshots ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
    if latest is None:
        return trends
    latest_id, latest_ts = latest
    current = dict(conn.execute("SELECT item_id, price FROM avg_prices WHERE snapshot_id = ?", (latest_id,)))
    past: dict[int, tuple[float | None, float | None, int]] = {
        item_id: (mean_7d, mean_30d, count)
        for item_id, mean_7d, mean_30d, count in conn.execute(
            "SELECT p.item_id, AVG(CASE WHEN s.ts >= :week THEN p.price END), AVG(p.price), COUNT(*) "
            "FROM avg_prices p JOIN snapshots s ON s.id = p.snapshot_id "
            "WHERE s.id != :latest AND s.ts >= :month GROUP BY p.item_id",
            {"latest": latest_id, "week": latest_ts - 7 * DAY, "month": latest_ts - 30 * DAY},
        )
    }
    for item_id, price in current.items():
        if item_id in trends:
            continue
        mean_7d, mean_30d, count = past.get(item_id, (None, None, 0))
        if count + 1 < min_snapshots:
            trends[item_id] = Trend(item_id, INSUFFICIENT, price, samples=count + 1)
        else:
            trends[item_id] = Trend(item_id, SNAPSHOTS, price, mean_7d, mean_30d, count + 1)
    return trends
