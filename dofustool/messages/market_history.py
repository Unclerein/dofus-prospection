"""Cours du marché d'un item : réponse du serveur à l'ouverture de l'onglet en jeu.

Un seul message porte deux séries, quelle que soit la période affichée ensuite :
  - une série horaire sur 24 h (un point par heure où l'item s'est vendu) ;
  - une série journalière sur 30 j, dont le jeu tire aussi sa vue « 7 jours ».
Chaque point donne le prix unitaire, la quantité vendue dans la tranche et la date de la
dernière vente de cette tranche. Le tout dernier point est donc le dernier achat.
"""
from dataclasses import dataclass
from datetime import datetime

from ..protocol.wire import LEN, VARINT, WireError, iter_fields
from . import Mapping

HOUR = 3600
DAY = 86400


@dataclass(frozen=True, slots=True)
class Point:
    sold_at: float  # date de la dernière vente de la tranche
    price: int
    quantity: int

    def bucket(self, size: int) -> int:
        """Début (UTC) de la tranche horaire ou journalière : stable d'une capture à l'autre."""
        return int(self.sold_at) // size * size


@dataclass(frozen=True, slots=True)
class MarketHistory:
    item_id: int
    hourly: tuple[Point, ...]
    daily: tuple[Point, ...]

    @property
    def last_sale(self) -> Point | None:
        points = self.hourly + self.daily
        return max(points, key=lambda p: p.sold_at) if points else None


def _parse_date(raw: bytes) -> float:
    """Date ISO 8601 avec fuseau ; la fraction de seconde peut dépasser 6 chiffres."""
    text = raw.decode("ascii")
    head, dot, rest = text.partition(".")
    if dot:
        digits = len(rest) - len(rest.lstrip("0123456789"))
        text = f"{head}.{rest[:digits][:6]}{rest[digits:]}"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("date sans fuseau")
    return parsed.timestamp()


def parse(body: bytes, mapping: Mapping) -> MarketHistory | None:
    """Renvoie le cours décodé, ou None si le corps n'a pas la forme attendue ou ne contient aucun point."""
    f = mapping.fields
    series: dict[int, list[Point]] = {f["hourly"]: [], f["daily"]: []}
    item_ids: set[int] = set()
    try:
        for number, wire_type, entry in iter_fields(body):
            if number not in series or wire_type != LEN:
                return None
            item_id = date = None
            price = quantity = 0
            for sub_number, sub_type, value in iter_fields(entry):
                if sub_number == f["date"] and sub_type == LEN:
                    date = _parse_date(value)
                elif sub_type != VARINT:
                    return None
                elif sub_number == f["item_id"]:
                    item_id = value
                elif sub_number == f["price"]:
                    price = value
                elif sub_number == f["quantity"]:
                    quantity = value
                else:
                    return None
            if item_id is None or date is None or price <= 0:
                return None
            item_ids.add(item_id)
            series[number].append(Point(date, price, quantity))
    except (WireError, ValueError, UnicodeDecodeError):
        return None
    if len(item_ids) != 1:
        return None
    hourly, daily = (tuple(sorted(series[f[name]], key=lambda p: p.sold_at)) for name in ("hourly", "daily"))
    # Une tranche ne doit apparaître qu'une fois par série.
    if len({p.bucket(HOUR) for p in hourly}) != len(hourly) or len({p.bucket(DAY) for p in daily}) != len(daily):
        return None
    return MarketHistory(item_ids.pop(), hourly, daily)
