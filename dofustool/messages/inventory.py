"""Mouvements de l'inventaire, un par un : ce que le serveur envoie entre deux listes complètes.

  - pile modifiée  : nouvelle quantité d'une pile, avec sa répartition par coffre quand le joueur
                     voit aussi sa banque ou son havre-sac (atelier, HDV) ;
  - objet ajouté   : un objet reçu, acheté, fabriqué ou ramassé, tel qu'il entre dans l'inventaire ;
  - objet retiré   : l'identifiant d'une pile qui quitte l'inventaire ;
  - kamas          : le nouveau total de kamas du personnage ;
  - coffre ouvert  : le type du coffre dont la liste suit (banque ou havre-sac).

Une pile garde le même identifiant d'un coffre à l'autre. Seuls des nombres sont lus.

Donnée personnelle : elle reste dans la base locale, et aucune fixture réelle n'est commitée.
"""
from dataclasses import dataclass

from ..protocol.wire import LEN, VARINT, WireError, iter_fields
from . import Mapping

# Coffres, tels que le jeu les numérote dans la répartition d'une pile.
INVENTORY, BANK, HAVRE = 1, 2, 3
CONTAINERS = (INVENTORY, BANK, HAVRE)
# Types de coffre annoncés à l'ouverture.
STORAGE_TYPES = {15: BANK, 18: HAVRE}
MAX_QUANTITY = 10**9


@dataclass(frozen=True, slots=True)
class PileUpdate:
    uid: int
    quantity: int  # total de la pile dans les coffres que le joueur a sous les yeux ; toujours juste
    # (coffre, quantité) ; vide si le serveur ne détaille pas. Après un achat, la part de l'inventaire
    # est encore celle d'avant : seul le total tient compte du lot acheté.
    parts: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class NewObject:
    uid: int
    item_id: int
    quantity: int
    equipped: bool
    parts: tuple[tuple[int, int], ...]


def read_parts(fields, f: dict[str, int]) -> tuple[tuple[int, int], ...] | None:
    """Répartition (coffre, quantité) portée par un objet ou une pile ; None si une part est incohérente."""
    parts = []
    for number, wire_type, value in fields:
        if number != f.get("split") or wire_type != LEN:
            continue
        container = quantity = None
        for p_number, p_type, p_value in iter_fields(value):
            if p_type != VARINT:
                return None
            if p_number == f["split_container"]:
                container = p_value
            elif p_number == f["split_quantity"]:
                quantity = p_value
        if container not in CONTAINERS or quantity is None or not 0 <= quantity <= MAX_QUANTITY:
            return None
        parts.append((container, quantity))
    return tuple(parts)


def parse_pile_update(body: bytes, mapping: Mapping) -> PileUpdate | None:
    f = mapping.fields
    uid = None
    quantity = 0  # une pile vidée arrive sans quantité
    try:
        top = list(iter_fields(body))
        parts = read_parts(top, {"split": f["parts"], "split_container": f["part_container"], "split_quantity": f["part_quantity"]})
        for number, wire_type, value in top:
            if number == f["pile"] and wire_type == LEN:
                for p_number, p_type, p_value in iter_fields(value):
                    if p_type != VARINT:
                        return None
                    if p_number == f["uid"]:
                        uid = p_value
                    elif p_number == f["quantity"]:
                        quantity = p_value
            elif number != f["parts"] and wire_type != VARINT:
                return None
    except WireError:
        return None
    if parts is None or not uid or not 0 <= quantity <= MAX_QUANTITY:
        return None
    return PileUpdate(uid, quantity, parts)


def parse_object(body: bytes, mapping: Mapping, storage: Mapping, bag_position: int = 63) -> NewObject | None:
    """Objet ajouté à l'inventaire. storage : message de l'inventaire, qui donne les champs d'un objet."""
    f, o = mapping.fields, storage.fields
    if not o.get("uid"):
        return None
    found = None
    try:
        for number, wire_type, value in iter_fields(body):
            if number != f["wrap"] or wire_type != LEN:
                if wire_type == LEN:
                    return None
                continue
            position, obj = bag_position, None
            for w_number, w_type, w_value in iter_fields(value):
                if w_number == f["object"] and w_type == LEN:
                    obj = list(iter_fields(w_value))
                elif w_number == o["position"] and w_type == VARINT:
                    position = w_value
            if obj is None or found is not None:
                return None
            values = {n: v for n, t, v in obj if t == VARINT}
            parts = read_parts(obj, o) if o.get("split") else ()
            uid, item_id, quantity = values.get(o["uid"]), values.get(o["item_id"]), values.get(o["quantity"], 1)
            if parts is None or not uid or not item_id or not 0 < quantity <= MAX_QUANTITY:
                return None
            found = NewObject(uid, item_id, quantity, position != bag_position, parts)
    except WireError:
        return None
    return found


def parse_single(body: bytes, mapping: Mapping, name: str) -> int | None:
    """Message fait d'un seul entier utile (identifiant retiré, total de kamas, type de coffre) ; vide = 0."""
    value = 0
    try:
        for number, wire_type, raw in iter_fields(body):
            if wire_type != VARINT:
                return None
            if number == mapping.fields[name]:
                value = raw
    except WireError:
        return None
    return value
