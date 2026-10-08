"""Annonces en vente à l'HDV pour un item : réponse du serveur à l'ouverture de sa fiche d'achat.

Pour une ressource, une seule entrée porte le prix le plus bas de chaque taille de lot
(x1, x10, x100, x1000 ; 0 = aucun lot de cette taille). Pour un équipement, il y a une
entrée par exemplaire, avec ses caractéristiques et son prix.

Seuls les effets purement numériques sont conservés. Certains effets portent du texte
(par exemple le nom du joueur qui a modifié l'objet) : on n'en garde que l'identifiant.
"""
from dataclasses import dataclass

from ..protocol.wire import LEN, VARINT, WireError, iter_fields, read_varint, to_signed
from . import Mapping

LOT_SIZES = (1, 10, 100, 1000)


@dataclass(frozen=True, slots=True)
class Listing:
    uid: int
    prices: tuple[int, int, int, int]  # prix du lot de 1, 10, 100, 1000
    effects: tuple[tuple[int, int | None], ...]  # (id d'effet, valeur ou None pour un effet sans valeur numérique)


@dataclass(frozen=True, slots=True)
class HdvListings:
    item_id: int
    listings: tuple[Listing, ...]


def _prices(wire_type: int, value) -> list[int]:
    if wire_type == VARINT:
        return [value]
    out, pos = [], 0
    while pos < len(value):
        price, pos = read_varint(value, pos)
        out.append(price)
    return out


def parse(body: bytes, mapping: Mapping) -> HdvListings | None:
    """Renvoie les annonces décodées, ou None si la forme est inattendue ou s'il n'y a aucune annonce."""
    f = mapping.fields
    item_id = None
    listings = []
    try:
        for number, wire_type, value in iter_fields(body):
            if number == f["item_id"] and wire_type == VARINT:
                item_id = value
            elif number == f["entries"] and wire_type == LEN:
                uid = entry_item = None
                prices: list[int] = []
                effects = []
                for sub_number, sub_type, sub_value in iter_fields(value):
                    if sub_number == f["effects"] and sub_type == LEN:
                        effect_id = effect_value = None
                        for e_number, e_type, e_value in iter_fields(sub_value):
                            if e_type != VARINT:
                                continue  # texte ou sous-message : jamais conservé
                            if e_number == f["effect_id"]:
                                effect_id = e_value
                            elif e_number == f["effect_value"]:
                                effect_value = to_signed(e_value)
                        if effect_id is None:
                            return None
                        effects.append((effect_id, effect_value))
                    elif sub_number == f["entry_item"] and sub_type == VARINT:
                        entry_item = sub_value
                    elif sub_number == f["uid"] and sub_type == VARINT:
                        uid = sub_value
                    elif sub_number == f["prices"]:
                        if sub_type not in (VARINT, LEN):
                            return None  # prix en entier fixe : forme inconnue
                        prices += _prices(sub_type, sub_value)
                    elif sub_type == LEN:
                        # Sous-message inconnu : sans doute des effets dont le champ a changé de numéro.
                        # Mieux vaut refuser l'annonce que l'enregistrer comme un exemplaire sans effets.
                        return None
                if uid is None or entry_item is None or len(prices) != len(LOT_SIZES) or not any(prices):
                    return None
                listings.append((entry_item, Listing(uid, tuple(prices), tuple(effects))))
            elif wire_type != VARINT:
                return None
    except WireError:
        return None
    if item_id is None or not listings or any(entry_item != item_id for entry_item, _ in listings):
        return None
    return HdvListings(item_id, tuple(listing for _, listing in listings))
