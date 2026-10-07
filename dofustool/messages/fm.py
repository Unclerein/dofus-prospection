"""Atelier de forgemagie : objet posé, résultat d'un passage de rune, et kamas des ventes hors ligne.

Un passage de rune se lit en deux messages du serveur :

  - l'objet posé sur l'atelier (la rune, à chaque passage ; l'équipement, quand on le pose) ;
  - le résultat : rune passée ou non, puits (le reliquat) et son sens de variation, et
    l'équipement tel qu'il est après le passage.

Le troisième message arrive à la connexion : le total des kamas gagnés par les ventes conclues
hors ligne, versés en banque. Le jeu le remet à zéro quand on retire des kamas de la banque.

Seuls des nombres sont lus. Un effet qui porte du texte (le nom du joueur qui a modifié
l'objet) n'est conservé que par son identifiant.

Donnée personnelle : elle reste dans la base locale, et aucune fixture réelle n'est commitée.
"""
import struct
from dataclasses import dataclass

from ..protocol.wire import I32, LEN, VARINT, WireError, iter_fields, to_signed
from . import Mapping

PASSED = 2  # état du résultat : la rune est passée (1 : elle a échoué)
POOL_SAME, POOL_UP, POOL_DOWN = 0, 1, 2
MAX_POOL = 10_000.0


@dataclass(frozen=True, slots=True)
class Object:
    uid: int
    item_id: int
    quantity: int
    effects: tuple[tuple[int, int | None], ...]  # (id d'effet, valeur ou None pour un effet sans valeur numérique)


@dataclass(frozen=True, slots=True)
class Result:
    passed: bool
    pool: float  # puits après le passage
    pool_change: int  # POOL_SAME, POOL_UP ou POOL_DOWN
    object: Object


def _object(body: bytes, f: dict[str, int]) -> Object | None:
    uid = item_id = None
    quantity = 1
    effects = []
    for number, wire_type, value in iter_fields(body):
        if number == f["effects"] and wire_type == LEN:
            effect_id = effect_value = None
            for e_number, e_type, e_value in iter_fields(value):
                if e_type != VARINT:
                    continue  # texte ou sous-message : jamais conservé
                if e_number == f["effect_id"]:
                    effect_id = e_value
                elif e_number == f["effect_value"]:
                    effect_value = to_signed(e_value)
            if effect_id is None:
                return None
            effects.append((effect_id, effect_value))
        elif wire_type != VARINT:
            return None
        elif number == f["uid"]:
            uid = value
        elif number == f["item_id"]:
            item_id = value
        elif number == f["quantity"]:
            quantity = value
    if not uid or not item_id or quantity <= 0:
        return None
    return Object(uid, item_id, quantity, tuple(effects))


def parse_object(body: bytes, mapping: Mapping) -> Object | None:
    """Objet posé sur l'atelier, ou None si la forme est inattendue."""
    f = mapping.fields
    found = None
    try:
        for number, wire_type, value in iter_fields(body):
            if wire_type != LEN:
                continue  # champs annexes (plusieurs runes posées d'un coup)
            if number != f["wrap"] or found is not None:
                return None
            for w_number, w_type, w_value in iter_fields(value):
                if w_number == f["object"] and w_type == LEN:
                    found = _object(w_value, f)
                elif w_type != VARINT:
                    return None
            if found is None:
                return None
    except WireError:
        return None
    return found


def parse_result(body: bytes, mapping: Mapping) -> Result | None:
    """Résultat d'un passage de rune, ou None si la forme est inattendue."""
    f = mapping.fields
    status = found = None
    pool = 0.0  # un puits vide n'est pas transmis
    change = POOL_SAME
    try:
        for number, wire_type, value in iter_fields(body):
            if number == f["status"] and wire_type == VARINT:
                status = value
            elif number == f["result"] and wire_type == LEN:
                for r_number, r_type, r_value in iter_fields(value):
                    if r_number == f["pool"] and r_type == I32:
                        pool = struct.unpack("<f", r_value.to_bytes(4, "little"))[0]
                    elif r_number == f["pool_change"] and r_type == VARINT:
                        change = r_value
                    elif r_number == f["object"] and r_type == LEN:
                        found = _object(r_value, f)
                    else:
                        return None
            else:
                return None
    except WireError:
        return None
    if status not in (1, PASSED) or found is None or change not in (POOL_SAME, POOL_UP, POOL_DOWN):
        return None
    if not 0 <= pool <= MAX_POOL:  # exclut aussi NaN
        return None
    return Result(status == PASSED, pool, change, found)


def parse_offline_total(body: bytes, mapping: Mapping) -> int | None:
    """Kamas des ventes hors ligne en attente (0 : message vide), ou None si la forme est inattendue."""
    total = 0
    try:
        for number, wire_type, value in iter_fields(body):
            if number != mapping.fields["total"] or wire_type != VARINT:
                return None
            total = value
    except WireError:
        return None
    return total
