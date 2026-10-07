"""Prix de référence d'un item et liquidité."""
import json
import sqlite3
from dataclasses import dataclass

from . import DAY, GRAIN_DAY, GRAIN_HOUR, HOUR
from . import cours
from . import estimate as estimation
from .forgemagie import MARKERS, classify

HDV = "HDV, annonce la moins chère"
HDV_PLAIN = "HDV, moins cher sans exo ni over"
HDV_ANY = "HDV, moins cher exo et over compris"
LAST_SALE = "dernier prix de vente"
MEDIAN_24H = "prix médian sur 24 h"
ESTIMATED = "prix estimé"
AVG_PRICE = "prix moyen"

EQUIPMENT = 0  # items.category_id
# Prix de vente retenu pour un équipement :
#   both : annonce « jet de base » si elle existe, sinon prix moyen ;
#   base : annonce « jet de base » seulement (ni exo, ni over, ni ligne perdue) ;
#   any  : annonce la moins chère, exo, over et transcendance compris, deux lignes perdues au plus ;
#   avg  : prix moyen du jeu seulement.
EQUIP_BOTH, EQUIP_BASE, EQUIP_ANY, EQUIP_AVG = "both", "base", "any", "avg"
EQUIP_MODES = (EQUIP_BOTH, EQUIP_BASE, EQUIP_ANY, EQUIP_AVG)
LOT_SIZES = (1, 10, 100, 1000)


@dataclass(frozen=True, slots=True)
class PriceRef:
    price: float  # prix unitaire
    source: str
    ts: float
    lot: int | None = None  # taille du lot HDV d'où vient le prix unitaire (1, 10, 100, 1000)
    spread: float | None = None  # prix estimé : fourchette relative (0.15 = ± 15 %)

    def age_hours(self, now: float) -> float:
        return max(0.0, now - self.ts) / 3600


@dataclass(frozen=True, slots=True)
class Liquidity:
    qty_24h: int | None = None
    qty_7d: int | None = None

    @property
    def known(self) -> bool:
        return self.qty_24h is not None or self.qty_7d is not None


def weighted_median(points: list[tuple[float, int]]) -> float | None:
    """Médiane des prix pondérée par les quantités, comme le « prix médian » du jeu."""
    total = sum(max(qty, 1) for _, qty in points)
    seen = 0
    for price, qty in sorted(points):
        seen += max(qty, 1)
        if seen * 2 >= total:
            return price
    return None


def unit_price(prices: tuple[int, ...]) -> tuple[float, int] | None:
    """(meilleur prix unitaire, taille du lot) parmi les lots x1, x10, x100, x1000 (0 = pas de lot de cette taille).

    À prix unitaire égal, le plus petit lot l'emporte : c'est le plus facile à acheter.
    """
    candidates = [(price / size, size) for price, size in zip(prices, LOT_SIZES) if price > 0]
    return min(candidates) if candidates else None


class PriceBook:
    """Prix de référence d'un item, par ordre de préférence, tant que l'information est assez récente.

    Ressources et consommables : annonce HDV la moins chère (sauf si le cours du marché montre une
    vente plus récente que ce relevé : elle prime), puis dernier prix de vente, puis prix
    estimé (s'il est jugé fiable et que `use_estimates` est vrai), puis prix moyen.
    Équipements : annonce HDV la moins chère « jet de base » (ni exo, ni over, ni ligne perdue), puis
    prix médian sur 24 h, puis prix moyen — ou un seul de ces prix, selon `equipment_price`.
    Le prix d'un équipement dépend de ses jets et de sa forgemagie : une vente isolée ou une
    annonce exotique ne dit rien du prix d'un exemplaire tout juste fabriqué.

    La fraîcheur d'une vente se juge sur la date de la vente, pas sur celle de la consultation.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        now: float,
        last_sale_max_age_hours: float,
        use_estimates: bool = False,
        equipment_price: str = EQUIP_BOTH,
    ) -> None:
        self.now = now
        self.equipment_price = equipment_price if equipment_price in EQUIP_MODES else EQUIP_BOTH
        oldest = now - last_sale_max_age_hours * 3600
        self.use_estimates = use_estimates
        # Estimations de tous les objets, pour l'affichage ; un relevé trop ancien ne s'extrapole pas.
        self.estimates = estimation.estimate_prices(conn)
        self.estimate_check = estimation.check(conn, self.estimates)
        if any(e.ts < oldest for e in self.estimates.values()):
            self.estimates = {}
        self.snapshot_ts: float | None = None
        self._avg: dict[int, int] = {}
        latest = conn.execute("SELECT id, ts FROM snapshots ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
        if latest is not None:
            self.snapshot_ts = latest[1]
            self._avg = dict(conn.execute("SELECT item_id, price FROM avg_prices WHERE snapshot_id = ?", (latest[0],)))
        self._equipment = {row[0] for row in conn.execute("SELECT id FROM items WHERE category_id = ?", (EQUIPMENT,))}
        self._seen = dict(conn.execute("SELECT item_id, MAX(captured_at) FROM market_history GROUP BY item_id"))

        self._last_sales: dict[int, tuple[int, float]] = {
            item_id: (price, ts)
            for item_id, price, ts in conn.execute(
                "SELECT item_id, price, MAX(sold_at) FROM last_sales WHERE sold_at >= ? GROUP BY item_id", (oldest,)
            )
        }

        # Tranches alignées sur l'heure et le jour UTC : la fenêtre glissante de 24 h du serveur
        # déborde sur une 25e tranche horaire ; 7 j = aujourd'hui et les 6 jours d'avant.
        self._liquidity: dict[int, dict[str, int]] = {}
        hourly: dict[int, list[tuple[float, int]]] = {}
        for item_id, period, price, qty in conn.execute(
            "SELECT h.item_id, h.period, h.price, COALESCE(h.qty_sold, 0) FROM market_history h "
            "JOIN (SELECT item_id, MAX(captured_at) AS seen FROM market_history GROUP BY item_id) l "
            "  ON l.item_id = h.item_id "
            "WHERE (h.period = :hour AND h.bucket_ts >= (CAST(l.seen / :h AS INTEGER) - 24) * :h) OR "
            "      (h.period = :daily AND h.bucket_ts >= (CAST(l.seen / :day AS INTEGER) - 6) * :day)",
            {"hour": GRAIN_HOUR, "daily": GRAIN_DAY, "day": int(DAY), "h": int(HOUR)},
        ):
            sold = self._liquidity.setdefault(item_id, {})
            sold[period] = sold.get(period, 0) + qty
            if period == GRAIN_HOUR:
                hourly.setdefault(item_id, []).append((price, qty))
        # Cours reconstitué depuis le relevé (analysis.cours) : il prolonge les quantités vendues jusqu'au dernier prix moyen.
        self._rebuilt = cours.liquidity(conn)
        self._median: dict[int, tuple[float, float]] = {
            item_id: (weighted_median(points), self._seen[item_id])
            for item_id, points in hourly.items()
            if item_id in self._equipment and self._seen[item_id] >= oldest
        }

        templates: dict[int, dict[int, tuple[int, int]]] = {
            row[0]: {} for row in conn.execute("SELECT item_id FROM item_effects_fetched")
        }
        # Les lignes qui ne se forgemagent pas (dégâts d'arme, propriétés) ne comptent ni comme exo ni comme ligne perdue.
        non_stats = {row[0] for row in conn.execute("SELECT effect_id FROM effect_meta WHERE is_stat = 0")} - MARKERS
        for item_id, effect_id, low, high in conn.execute("SELECT * FROM item_effects"):
            if effect_id not in non_stats:
                templates.setdefault(item_id, {})[effect_id] = (low, high)
        self._hdv: dict[int, tuple[float, str, float, int]] = {}
        self._hdv_any: dict[int, tuple[float, str, float, int]] = {}  # équipements : toutes forgemagies
        for item_id, p1, p10, p100, p1000, effects, captured_at in conn.execute(
            "SELECT item_id, p1, p10, p100, p1000, effects, captured_at FROM hdv_current WHERE captured_at >= ?",
            (oldest,),
        ):
            best = unit_price((p1, p10, p100, p1000))
            if best is None:
                continue
            price, lot = best
            if item_id in self._equipment:
                template = templates.get(item_id)
                listed = [tuple(e) for e in json.loads(effects) if e[0] not in non_stats]
                if template is None:
                    continue
                kind = classify(listed, template)
                if kind.sellable and (item_id not in self._hdv_any or price < self._hdv_any[item_id][0]):
                    self._hdv_any[item_id] = (price, HDV_ANY, captured_at, lot)
                if not kind.plain:
                    continue
                source = HDV_PLAIN
            else:
                source = HDV
            if item_id not in self._hdv or price < self._hdv[item_id][0]:
                self._hdv[item_id] = (price, source, captured_at, lot)

    def is_equipment(self, item_id: int) -> bool:
        return item_id in self._equipment

    def get(self, item_id: int) -> PriceRef | None:
        hdv = self._hdv.get(item_id)
        if item_id in self._equipment:
            if self.equipment_price == EQUIP_ANY:
                cheapest = self._hdv_any.get(item_id)
                return PriceRef(*cheapest) if cheapest is not None else None
            if self.equipment_price != EQUIP_AVG:
                median = self._median.get(item_id)
                if median is not None and median[0] is not None:
                    # Une annonce n'est pas une vente : si les ventes récentes sont plus basses, elles priment.
                    if hdv is None or median[0] < hdv[0]:
                        return PriceRef(median[0], MEDIAN_24H, median[1])
                if hdv is not None:
                    return PriceRef(*hdv)
            if self.equipment_price == EQUIP_BASE:
                return None  # pas d'exemplaire de base relevé : pas de prix, plutôt qu'un prix moyen
        elif hdv is not None:
            sale = self._last_sales.get(item_id)
            # Une vente plus récente que le relevé de l'HDV dit mieux le prix du moment que l'annonce.
            if sale is None or sale[1] <= hdv[2]:
                return PriceRef(*hdv)
        if item_id not in self._equipment:
            sale = self._last_sales.get(item_id)
            if sale is not None:
                return PriceRef(sale[0], LAST_SALE, sale[1])
            guess = self.estimates.get(item_id)
            if self.use_estimates and guess is not None and guess.confidence == estimation.RELIABLE:
                return PriceRef(guess.price, ESTIMATED, guess.ts, None, self.estimate_spread(guess.confidence))
        avg = self._avg.get(item_id)
        if avg is not None and self.snapshot_ts is not None:
            return PriceRef(avg, AVG_PRICE, self.snapshot_ts)
        return None

    def estimate(self, item_id: int) -> estimation.Estimate | None:
        """Prix du moment estimé d'une ressource ou d'un consommable, quel que soit son niveau de confiance."""
        return None if item_id in self._equipment else self.estimates.get(item_id)

    def estimate_spread(self, confidence: str) -> float:
        """Fourchette relative à afficher pour ce niveau de confiance (mesurée si assez de points de contrôle)."""
        return self.estimate_check[confidence]["spread"]

    def hdv_ask(self, item_id: int) -> tuple[float, float] | None:
        """(meilleur prix unitaire demandé à l'HDV, date du relevé) d'une ressource ou d'un consommable."""
        hdv = self._hdv.get(item_id)
        return (hdv[0], hdv[2]) if hdv is not None and hdv[1] == HDV else None

    def hdv_price(self, item_id: int) -> tuple[float, str, float, int] | None:
        """(prix unitaire, nature, date du relevé, taille du lot) de l'annonce HDV retenue pour l'item, quel que soit son type."""
        return self._hdv.get(item_id)

    def hdv_any(self, item_id: int) -> tuple[float, str, float, int] | None:
        """Annonce la moins chère d'un équipement, toutes forgemagies confondues (deux lignes perdues au plus)."""
        return self._hdv_any.get(item_id)

    def avg_price(self, item_id: int) -> int | None:
        """Prix moyen du jeu dans le dernier relevé."""
        return self._avg.get(item_id)

    def median_24h(self, item_id: int) -> float | None:
        """Prix médian pondéré des ventes des dernières 24 h d'un équipement, s'il est assez récent."""
        median = self._median.get(item_id)
        return median[0] if median else None

    def liquidity(self, item_id: int) -> Liquidity:
        rebuilt = self._rebuilt.get(item_id)
        if rebuilt is not None:
            return Liquidity(rebuilt[0], rebuilt[1])
        if item_id not in self._seen:
            return Liquidity()
        # Cours consulté mais aucune vente dans la fenêtre : la quantité est 0, pas inconnue.
        sold = self._liquidity.get(item_id, {})
        return Liquidity(sold.get(GRAIN_HOUR, 0), sold.get(GRAIN_DAY, 0))

    def rebuilt_at(self, item_id: int) -> float | None:
        """Date du dernier prix moyen pris en compte dans les quantités vendues, si elles sont reconstituées."""
        rebuilt = self._rebuilt.get(item_id)
        return rebuilt[2] if rebuilt is not None else None

    def market_seen_at(self, item_id: int) -> float | None:
        """Date de la dernière consultation du cours du marché de l'item."""
        return self._seen.get(item_id)
