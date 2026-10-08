"""Contenu de l'inventaire du personnage et de la banque.

Le serveur envoie des listes complètes :
  - l'inventaire à chaque connexion, puis régulièrement ;
  - la banque quand on l'ouvre ;
  - une liste réunie (inventaire, plus la banque et le havre-sac si le joueur les a cochés) à
    l'ouverture d'un HDV ou d'un atelier, sous la même clé que l'inventaire. Chaque pile y porte
    sa répartition : combien dans l'inventaire, en banque, au havre-sac ;
  - le havre-sac, sous la même clé que la banque (un message annonce juste avant le type du coffre).
Seuls des nombres sont lus (objet, quantité, position, identifiant de la pile, répartition) : jamais de texte.

Donnée personnelle : elle reste dans la base locale, et aucune fixture réelle n'est commitée.
"""
from dataclasses import dataclass, field

from ..protocol.wire import LEN, VARINT, WireError, iter_fields
from . import Mapping
from .inventory import read_parts

# Position d'un objet rangé dans le sac ; toute autre valeur est un emplacement d'équipement porté.
BAG_POSITION = 63


@dataclass(frozen=True, slots=True)
class Stack:
    item_id: int
    quantity: int
    equipped: bool
    uid: int = field(default=0, compare=False)  # identifiant de la pile, le même d'un coffre à l'autre
    parts: tuple[tuple[int, int], ...] = field(default=(), compare=False)  # (coffre, quantité), si le serveur détaille


@dataclass(frozen=True, slots=True)
class Storage:
    kamas: int
    stacks: tuple[Stack, ...]

    def quantities(self, include_equipped: bool = False) -> dict[int, int]:
        """Quantité par item, piles additionnées. Les objets portés sont exclus par défaut."""
        out: dict[int, int] = {}
        for stack in self.stacks:
            if include_equipped or not stack.equipped:
                out[stack.item_id] = out.get(stack.item_id, 0) + stack.quantity
        return out

    @property
    def detailed(self) -> bool:
        """Vrai si le serveur a détaillé la répartition des piles : c'est une liste réunie d'HDV ou d'atelier."""
        return any(stack.parts for stack in self.stacks)


def parse(body: bytes, mapping: Mapping) -> Storage | None:
    """Renvoie le contenu décodé, ou None si le corps n'a pas la forme attendue."""
    f = mapping.fields
    kamas = 0
    stacks = []
    try:
        for number, wire_type, value in iter_fields(body):
            if number == f["kamas"] and wire_type == VARINT:
                kamas = value
            elif number == f["entries"] and wire_type == LEN:
                position = 0
                obj = None
                for sub_number, sub_type, sub_value in iter_fields(value):
                    if sub_number == f["position"] and sub_type == VARINT:
                        position = sub_value
                    elif sub_number == f["object"] and sub_type == LEN:
                        obj = sub_value
                if obj is None:
                    return None
                item_id = quantity = uid = 0
                fields = list(iter_fields(obj))
                for o_number, o_type, o_value in fields:
                    if o_type != VARINT:
                        continue  # effets et sous-messages : non lus
                    if o_number == f["item_id"]:
                        item_id = o_value
                    elif o_number == f["quantity"]:
                        quantity = o_value
                    elif o_number == f.get("uid"):
                        uid = o_value
                if item_id <= 0 or quantity <= 0 or quantity > 10**9:
                    return None
                parts = read_parts(fields, f) if f.get("split") else ()
                if parts is None or (parts and sum(q for _, q in parts) != quantity):
                    return None  # répartition incohérente : forme inattendue
                stacks.append(Stack(item_id, quantity, position != BAG_POSITION, uid, parts))
            elif wire_type == LEN and number != f["entries"]:
                continue  # champ texte annexe (non lu)
            elif wire_type != VARINT:
                return None
    except WireError:
        return None
    return Storage(kamas, tuple(stacks))


def looks_merged(candidate: Storage, inventory: Storage | None) -> bool:
    """Vrai si une liste reçue sous la clé de l'inventaire est en fait la fusion inventaire + banque.

    La liste fusionnée contient tous les objets de l'inventaire, en plus grand nombre. La première
    liste d'une connexion est toujours l'inventaire : sans inventaire connu, on ne conclut pas.
    """
    if inventory is None or not inventory.stacks:
        return False
    mine = set(inventory.quantities(include_equipped=True))
    theirs = candidate.quantities(include_equipped=True)
    overlap = len(mine & theirs.keys()) / len(mine)
    return overlap >= 0.95 and len(candidate.stacks) > len(inventory.stacks) * 1.15
