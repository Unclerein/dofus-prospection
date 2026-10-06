"""Lots que le joueur a mis en vente à l'HDV : liste envoyée à l'ouverture de l'onglet « Vendre ».

Une entrée par lot : l'objet, la taille du lot (1, 10, 100, 1000), son prix, et le temps restant
avant que le lot ne soit retiré de la vente (en secondes). Seuls des nombres sont lus.

Chaque HDV (ressources, équipements…) envoie sa propre liste, précédée de son descripteur. La
liste remplace donc seulement les lots de cet HDV, reconnu à l'empreinte de son descripteur.

Donnée personnelle : elle reste dans la base locale, et aucune fixture réelle n'est commitée.
"""
import zlib
from dataclasses import dataclass

from ..protocol.wire import LEN, VARINT, WireError, iter_fields
from . import Mapping

LOT_SIZES = (1, 10, 100, 1000)
MAX_REMAINING_S = 40 * 86400  # un lot reste en vente 28 jours ; marge pour un changement de règle


@dataclass(frozen=True, slots=True)
class Sale:
    uid: int
    item_id: int
    lot: int
    price: int  # prix du lot entier
    remaining_s: int


@dataclass(frozen=True, slots=True)
class SalesList:
    market: int  # empreinte de l'HDV qui a envoyé la liste (0 si son descripteur est absent)
    sales: tuple[Sale, ...]


def _market(descriptor: bytes) -> int:
    """Empreinte d'un HDV : sa liste de types d'objets acceptés, le plus long champ de son descripteur.

    Les autres champs (compteurs) changent d'une ouverture à l'autre ; cette liste, non.
    """
    longest = b""
    for _, wire_type, value in iter_fields(descriptor):
        if wire_type == LEN and len(value) > len(longest):
            longest = value
    return zlib.crc32(longest) if longest else 0


def parse(body: bytes, mapping: Mapping) -> SalesList | None:
    """Renvoie les lots en vente (éventuellement aucun), ou None si le corps n'a pas la forme attendue."""
    f = mapping.fields
    sales = []
    market = 0
    try:
        for number, wire_type, value in iter_fields(body):
            if number != f["entries"]:
                if wire_type == LEN:
                    market = market or _market(value)
                continue
            if wire_type != LEN:
                return None
            uid = item_id = lot = price = None
            remaining = 0
            for sub_number, sub_type, sub_value in iter_fields(value):
                if sub_number == f["ref"] and sub_type == LEN:
                    for r_number, r_type, r_value in iter_fields(sub_value):
                        if r_type != VARINT:
                            return None
                        if r_number == f["uid"]:
                            uid = r_value
                        elif r_number == f["item_id"]:
                            item_id = r_value
                        elif r_number == f["lot"]:
                            lot = r_value
                elif sub_type != VARINT:
                    return None
                elif sub_number == f["price"]:
                    price = sub_value
                elif sub_number == f["remaining"]:
                    remaining = sub_value
            if not uid or not item_id or lot not in LOT_SIZES or not price or remaining > MAX_REMAINING_S:
                return None
            sales.append(Sale(uid, item_id, lot, price, remaining))
    except WireError:
        return None
    if not sales and not market:
        return None  # ni lot ni descripteur : ce n'est pas ce message
    return SalesList(market, tuple(sales))
