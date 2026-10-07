"""Ventes conclues, achats, et lots créés ou modifiés à l'HDV.

Le jeu annonce une vente ou un achat par un message d'information : un numéro de texte et des
paramètres. Pour ces deux textes, les paramètres sont des nombres écrits en chiffres ; un
paramètre qui n'est pas fait de chiffres n'est jamais lu ni conservé.

  - texte 65  : vente conclue  — prix du lot, objet, objet, taille du lot ;
  - texte 252 : achat          — objet, (identifiant interne), taille du lot, prix du lot.

Les numéros de texte sont des constantes du jeu ; seuls la clé du message et ses numéros de champ
changent d'un build à l'autre.

Quand le joueur met un lot en vente ou change son prix, le serveur renvoie le lot tel qu'il est
désormais : prix, temps restant, et la référence (objet, identifiant du lot, taille).

Donnée personnelle : elle reste dans la base locale, et aucune fixture réelle n'est commitée.
"""
from dataclasses import dataclass

from ..protocol.wire import LEN, VARINT, WireError, iter_fields
from . import Mapping
from .sales import LOT_SIZES, MAX_REMAINING_S, Sale

SOLD_TEXT = 65
BOUGHT_TEXT = 252
SALE, PURCHASE = "sale", "purchase"
MAX_DIGITS = 15


@dataclass(frozen=True, slots=True)
class Trade:
    kind: str  # SALE ou PURCHASE
    item_id: int
    quantity: int  # taille du lot
    price: int  # prix du lot entier


def read_text(body: bytes, mapping: Mapping) -> tuple[int, list[int | None]] | None:
    """(numéro du texte, paramètres) ; un paramètre non numérique vaut None. None si la forme est inattendue."""
    f = mapping.fields
    ident = None
    params: list[int | None] = []
    try:
        for number, wire_type, value in iter_fields(body):
            if number == f["id"] and wire_type == VARINT:
                ident = value
            elif number == f["params"] and wire_type == LEN:
                params.append(int(value) if value.isdigit() and len(value) <= MAX_DIGITS else None)
            elif wire_type != VARINT:
                return None
    except WireError:
        return None
    return (ident, params) if ident is not None else None


def parse_text(body: bytes, mapping: Mapping) -> Trade | None:
    """Vente ou achat annoncé par ce message d'information, ou None si c'est un autre texte."""
    text = read_text(body, mapping)
    if text is None or len(text[1]) != 4 or any(p is None for p in text[1]):
        return None
    ident, params = text
    if ident == SOLD_TEXT:
        price, item_id, again, quantity = params
        if item_id != again:
            return None
        kind = SALE
    elif ident == BOUGHT_TEXT:
        item_id, _, quantity, price = params
        kind = PURCHASE
    else:
        return None
    if item_id <= 0 or quantity not in LOT_SIZES or price <= 0:
        return None
    return Trade(kind, item_id, quantity, price)


def parse_lot_update(body: bytes, mapping: Mapping) -> Sale | None:
    """Lot que le joueur vient de mettre en vente ou de modifier, ou None si la forme est inattendue."""
    f = mapping.fields
    uid = item_id = lot = price = None
    remaining = 0
    try:
        for number, wire_type, value in iter_fields(body):
            if number == f["ref"] and wire_type == LEN:
                for r_number, r_type, r_value in iter_fields(value):
                    if r_type != VARINT:
                        continue  # effets d'un équipement : non lus
                    if r_number == f["uid"]:
                        uid = r_value
                    elif r_number == f["item_id"]:
                        item_id = r_value
                    elif r_number == f["lot"]:
                        lot = r_value
            elif wire_type != VARINT:
                return None
            elif number == f["price"]:
                price = value
            elif number == f["remaining"]:
                remaining = value
    except WireError:
        return None
    if not uid or not item_id or lot not in LOT_SIZES or not price or remaining > MAX_REMAINING_S:
        return None
    return Sale(uid, item_id, lot, price, remaining)
