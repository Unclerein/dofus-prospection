"""Échange entre deux joueurs : ce que chacun pose, les kamas proposés, et l'issue.

Le serveur annonce l'ouverture de l'échange, puis chaque objet posé ou retiré et chaque somme
proposée, en disant de quel côté il vient, et enfin la clôture : conclu ou annulé. Les objets
posés passent par le même message que ceux d'un atelier.

Seuls des nombres sont lus : ni le nom ni l'identifiant de l'autre joueur ne sont conservés.

Donnée personnelle : elle reste dans la base locale, et aucune fixture réelle n'est commitée.
"""
from dataclasses import dataclass, field

from ..protocol.wire import LEN, VARINT, WireError, iter_fields
from . import Mapping

MAX_KAMAS = 10**12


@dataclass(slots=True)
class Exchange:
    """Échange en cours. given / received : {identifiant de pile: (objet, quantité)}."""

    kamas_given: int = 0
    kamas_received: int = 0
    given: dict[int, tuple[int, int]] = field(default_factory=dict)
    received: dict[int, tuple[int, int]] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.kamas_given or self.kamas_received or self.given or self.received)


def parse_placed(body: bytes, mapping: Mapping) -> tuple[bool, list[tuple[int, int, int]]] | None:
    """(posé par l'autre joueur ?, [(identifiant de pile, objet, quantité)]) ; plusieurs objets peuvent arriver d'un coup."""
    f = mapping.fields
    remote = False
    objects = []
    try:
        for number, wire_type, value in iter_fields(body):
            if number == f.get("remote") and wire_type == VARINT:
                remote = bool(value)
            elif number == f["wrap"] and wire_type == LEN:
                for w_number, w_type, w_value in iter_fields(value):
                    if w_number != f["object"] or w_type != LEN:
                        continue
                    values = {n: v for n, t, v in iter_fields(w_value) if t == VARINT}
                    uid, item_id, quantity = values.get(f["uid"]), values.get(f["item_id"]), values.get(f["quantity"], 1)
                    if not uid or not item_id or not 0 < quantity <= 10**9:
                        return None
                    objects.append((uid, item_id, quantity))
            elif wire_type == LEN:
                return None
    except WireError:
        return None
    return (remote, objects) if objects else None


def parse_flagged(body: bytes, mapping: Mapping, name: str) -> tuple[int, bool] | None:
    """(valeur, côté de l'autre joueur ?) d'un message fait d'entiers : somme proposée, ou pile retirée."""
    value, remote = 0, False
    try:
        for number, wire_type, raw in iter_fields(body):
            if wire_type != VARINT:
                return None
            if number == mapping.fields[name]:
                value = raw
            elif number == mapping.fields["remote"]:
                remote = bool(raw)
    except WireError:
        return None
    return value, remote


def parse_closed(body: bytes, mapping: Mapping) -> bool | None:
    """True si l'échange est conclu, False s'il est annulé, None si la forme est inattendue."""
    success = False
    try:
        for number, wire_type, raw in iter_fields(body):
            if wire_type != VARINT:
                return None
            if number == mapping.fields["success"]:
                success = bool(raw)
    except WireError:
        return None
    return success


class Tracker:
    """Suit un échange du début à la fin, sur une connexion."""

    def __init__(self) -> None:
        self.current: Exchange | None = None

    def start(self) -> None:
        self.current = Exchange()

    def place(self, remote: bool, objects: list[tuple[int, int, int]]) -> None:
        if self.current is not None:
            side = self.current.received if remote else self.current.given
            for uid, item_id, quantity in objects:
                side[uid] = (item_id, quantity)

    def remove(self, uid: int, remote: bool) -> None:
        if self.current is not None:
            (self.current.received if remote else self.current.given).pop(uid, None)

    def kamas(self, amount: int, remote: bool) -> None:
        if self.current is not None and 0 <= amount <= MAX_KAMAS:
            if remote:
                self.current.kamas_received = amount
            else:
                self.current.kamas_given = amount

    def close(self, success: bool) -> Exchange | None:
        """L'échange conclu, ou None s'il est annulé, vide, ou si rien n'était ouvert."""
        done, self.current = self.current, None
        return done if success and done is not None and not done.empty else None
