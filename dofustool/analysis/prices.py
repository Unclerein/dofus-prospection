"""Prix de référence d'un item et liquidité."""
import sqlite3
from dataclasses import dataclass

from . import PERIOD_7D, PERIOD_24H

LAST_SALE = "dernier prix de vente"
AVG_PRICE = "prix moyen"


@dataclass(frozen=True, slots=True)
class PriceRef:
    price: int
    source: str
    ts: float

    def age_hours(self, now: float) -> float:
        return max(0.0, now - self.ts) / 3600


@dataclass(frozen=True, slots=True)
class Liquidity:
    qty_24h: int | None = None
    qty_7d: int | None = None

    @property
    def known(self) -> bool:
        return self.qty_24h is not None or self.qty_7d is not None


class PriceBook:
    """Prix de référence : le dernier prix de vente s'il est assez récent, sinon le prix moyen du dernier relevé."""

    def __init__(self, conn: sqlite3.Connection, now: float, last_sale_max_age_hours: float) -> None:
        self.now = now
        self.snapshot_ts: float | None = None
        self._avg: dict[int, int] = {}
        latest = conn.execute("SELECT id, ts FROM snapshots ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
        if latest is not None:
            self.snapshot_ts = latest[1]
            self._avg = dict(conn.execute("SELECT item_id, price FROM avg_prices WHERE snapshot_id = ?", (latest[0],)))
        self._last_sales: dict[int, tuple[int, float]] = {
            item_id: (price, ts)
            for item_id, price, ts in conn.execute(
                "SELECT item_id, price, MAX(captured_at) FROM last_sales WHERE captured_at >= ? GROUP BY item_id",
                (now - last_sale_max_age_hours * 3600,),
            )
        }
        self._liquidity: dict[int, dict[str, int]] = {}
        for item_id, period, qty in conn.execute(
            "SELECT item_id, period, SUM(qty_sold) FROM market_history "
            "WHERE period IN (?, ?) AND qty_sold IS NOT NULL GROUP BY item_id, period",
            (PERIOD_24H, PERIOD_7D),
        ):
            self._liquidity.setdefault(item_id, {})[period] = qty

    def get(self, item_id: int) -> PriceRef | None:
        sale = self._last_sales.get(item_id)
        if sale is not None:
            return PriceRef(sale[0], LAST_SALE, sale[1])
        avg = self._avg.get(item_id)
        if avg is not None and self.snapshot_ts is not None:
            return PriceRef(avg, AVG_PRICE, self.snapshot_ts)
        return None

    def liquidity(self, item_id: int) -> Liquidity:
        sold = self._liquidity.get(item_id, {})
        return Liquidity(sold.get(PERIOD_24H), sold.get(PERIOD_7D))
