"""Prix de référence d'un item et liquidité."""
import sqlite3
from dataclasses import dataclass

from . import DAY, GRAIN_DAY, GRAIN_HOUR, HOUR

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
    """Prix de référence : le dernier prix de vente s'il est assez récent, sinon le prix moyen du dernier relevé.

    La fraîcheur se juge sur la date de la vente elle-même, pas sur celle de la consultation :
    une vente vieille de trois semaines n'est pas un prix courant, même vue aujourd'hui.
    """

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
                "SELECT item_id, price, MAX(sold_at) FROM last_sales WHERE sold_at >= ? GROUP BY item_id",
                (now - last_sale_max_age_hours * 3600,),
            )
        }
        # Volumes sur les 24 h et les 7 j qui précèdent la dernière consultation de l'item en jeu.
        self._liquidity: dict[int, dict[str, int]] = {}
        for item_id, period, qty in conn.execute(
            "SELECT h.item_id, h.period, SUM(h.qty_sold) FROM market_history h "
            "JOIN (SELECT item_id, MAX(captured_at) AS seen FROM market_history GROUP BY item_id) l "
            "  ON l.item_id = h.item_id "
            "WHERE h.qty_sold IS NOT NULL AND ("
            # Les tranches sont alignées sur l'heure et le jour UTC : la fenêtre glissante de 24 h
            # du serveur déborde sur une 25e tranche horaire ; 7 j = aujourd'hui et les 6 jours d'avant.
            "  (h.period = :hour AND h.bucket_ts >= (CAST(l.seen / :h AS INTEGER) - 24) * :h) OR "
            "  (h.period = :daily AND h.bucket_ts >= (CAST(l.seen / :day AS INTEGER) - 6) * :day)) "
            "GROUP BY h.item_id, h.period",
            {"hour": GRAIN_HOUR, "daily": GRAIN_DAY, "day": int(DAY), "h": int(HOUR)},
        ):
            self._liquidity.setdefault(item_id, {})[period] = qty
        self._seen = dict(conn.execute("SELECT item_id, MAX(captured_at) FROM market_history GROUP BY item_id"))

    def get(self, item_id: int) -> PriceRef | None:
        sale = self._last_sales.get(item_id)
        if sale is not None:
            return PriceRef(sale[0], LAST_SALE, sale[1])
        avg = self._avg.get(item_id)
        if avg is not None and self.snapshot_ts is not None:
            return PriceRef(avg, AVG_PRICE, self.snapshot_ts)
        return None

    def liquidity(self, item_id: int) -> Liquidity:
        if item_id not in self._seen:
            return Liquidity()
        # Cours consulté mais aucune vente dans la fenêtre : la quantité est 0, pas inconnue.
        sold = self._liquidity.get(item_id, {})
        return Liquidity(sold.get(GRAIN_HOUR, 0), sold.get(GRAIN_DAY, 0))

    def market_seen_at(self, item_id: int) -> float | None:
        """Date de la dernière consultation du cours du marché de l'item."""
        return self._seen.get(item_id)
