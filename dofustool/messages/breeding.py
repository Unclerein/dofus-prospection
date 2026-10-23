"""Élevage : l'étable, un enclos et ses mises à jour, envoyés quand le joueur ouvre l'interface (touche D).

  - stable         : toutes les montures de l'étable ;
  - paddock        : un enclos à son ouverture (numéro, objets d'élevage, objets actifs, montures) ;
  - paddock_update : le même enclos, renvoyé toutes les 10 s tant que l'interface reste ouverte.

Une monture : un identifiant (texte de 36 caractères, celui du jeu), son sexe, sa couleur (identifiant
RidesData), les couleurs de ses parents, sa sérénité (−5 000 à 5 000), ses trois jauges (0 à 20 000),
son niveau, son expérience, et si elle est stérile. Ses bonus (effets) ne sont pas lus.

Les fiches d'une monture ont les mêmes numéros de champ dans l'étable et dans l'enclos : ils sont rangés
dans l'entrée « stable » de la table des clés, que les deux autres messages réutilisent.

Donnée personnelle : elle reste dans la base locale.
"""
import re
from dataclasses import dataclass

from ..protocol.wire import LEN, VARINT, WireError, iter_fields, read_varint, to_signed
from . import Mapping

LOVE, MATURITY, STAMINA = 0, 1, 2  # types des trois jauges d'une monture ; une jauge à 0 n'envoie pas sa valeur,
# et l'amour (type 0) n'envoie pas non plus son type : vérifié sur l'amour monté de 520 à 5 040 en une nuit de dragofesse
GAUGE_MAX = 20_000
SERENITY_LIMIT = 5_000
OBJECTS = 6  # objets d'élevage d'un enclos : 0 baffeur, 1 caresseur, 2 foudroyeur, 3 abreuvoir, 4 dragofesse, 5 mangeoire
OBJECT_MAX = 200_000
UID = re.compile(rb"^[0-9a-f-]{36}$")


@dataclass(frozen=True, slots=True)
class Mount:
    uid: str
    male: bool
    color: int
    parents: tuple[int, ...]
    serenity: int
    maturity: int
    love: int
    stamina: int
    level: int
    xp: int
    sterile: bool

    @property
    def fecund(self) -> bool:
        """Peut s'accoupler : pas stérile, et ses trois jauges au max."""
        return not self.sterile and min(self.maturity, self.love, self.stamina) >= GAUGE_MAX


@dataclass(frozen=True, slots=True)
class Paddock:
    paddock_id: int | None  # absent d'une mise à jour : c'est l'enclos ouvert
    active: tuple[int, ...]  # objets d'élevage en marche
    objects: tuple[int, ...]  # points de chacun des 6 objets
    mounts: tuple[Mount, ...]


def _packed(value: bytes) -> list[int] | None:
    out, pos = [], 0
    try:
        while pos < len(value):
            number, pos = read_varint(value, pos)
            out.append(number)
    except WireError:
        return None
    return out


def parse_mount(entry: bytes, stable: Mapping) -> Mount | None:
    """Une monture : (identifiant, fiche). None si la forme ou une valeur est inattendue."""
    f = stable.fields
    uid = data = None
    try:
        for number, wire_type, value in iter_fields(entry):
            if number == f["uid"] and wire_type == LEN and UID.match(value):
                uid = value.decode("ascii")
            elif number == f["data"] and wire_type == LEN:
                data = value
            else:
                return None
        if uid is None or data is None:
            return None
        values: dict[int, int] = {}
        parents: list[int] = []
        gauges = {LOVE: 0, MATURITY: 0, STAMINA: 0}
        for number, wire_type, value in iter_fields(data):
            if wire_type == VARINT:
                values[number] = value
            elif number == f["parents"] and wire_type == LEN:
                parents = [v for _, t, v in iter_fields(value) if t == VARINT]
            elif number == f["gauges"] and wire_type == LEN:
                gauge = {n: v for n, t, v in iter_fields(value) if t == VARINT}
                kind = gauge.get(f["gauge_type"], 0)
                if kind not in gauges:
                    return None
                gauges[kind] = gauge.get(f["gauge_value"], 0)
    except WireError:
        return None
    serenity = to_signed(values.get(f["serenity"], 0))
    level = values.get(f["level"], 0)
    color = values.get(f["color"], 0)
    if not -SERENITY_LIMIT <= serenity <= SERENITY_LIMIT or not 1 <= level <= 200 or color <= 0:
        return None
    if any(not 0 <= v <= GAUGE_MAX for v in gauges.values()) or values.get(f["male"], 0) > 1 or values.get(f["sterile"], 0) > 1:
        return None
    return Mount(
        uid, values.get(f["male"], 0) == 1, color, tuple(parents), serenity,
        gauges[MATURITY], gauges[LOVE], gauges[STAMINA], level, values.get(f["xp"], 0), values.get(f["sterile"], 0) == 1,
    )  # fmt: skip


def parse_stable(body: bytes, mapping: Mapping) -> tuple[Mount, ...] | None:
    f = mapping.fields
    try:
        top = list(iter_fields(body))
        if len(top) != 1 or top[0][0] != f["wrap"] or top[0][1] != LEN:
            return None
        mounts = []
        for number, wire_type, value in iter_fields(top[0][2]):
            if number == f["mounts"] and wire_type == LEN:
                mount = parse_mount(value, mapping)
                if mount is None:
                    return None
                mounts.append(mount)
            elif wire_type != LEN:  # à côté des montures, seulement un petit bloc non lu (places de l'étable ?)
                return None
    except WireError:
        return None
    return tuple(mounts)


def _content(content: bytes, f: dict[str, int], stable: Mapping) -> tuple[tuple[int, ...], tuple[int, ...], tuple[Mount, ...]] | None:
    active: list[int] = []
    objects = [0] * OBJECTS
    mounts = []
    for number, wire_type, value in iter_fields(content):
        if number == f["active"] and wire_type == LEN:
            listed = _packed(value)
            if listed is None or any(i >= OBJECTS for i in listed):
                return None
            active += listed
        elif number == f["objects"] and wire_type == LEN:
            obj = {n: v for n, t, v in iter_fields(value) if t == VARINT}
            index, points = obj.get(f["object_index"], 0), obj.get(f["object_value"], 0)
            if index >= OBJECTS or points > OBJECT_MAX:
                return None
            objects[index] = points
        elif number == f["mounts"] and wire_type == LEN:
            mount = parse_mount(value, stable)
            if mount is None:
                return None
            mounts.append(mount)
        else:
            return None
    return tuple(active), tuple(objects), tuple(mounts)


def parse_paddock(body: bytes, mapping: Mapping, stable: Mapping | None) -> Paddock | None:
    """Un enclos à son ouverture ; stable : la table de l'étable, pour lire les montures."""
    if stable is None:
        return None
    f = mapping.fields
    try:
        top = list(iter_fields(body))
        if len(top) != 1 or top[0][0] != f["wrap"] or top[0][1] != LEN:
            return None
        paddock_id = content = None
        for number, wire_type, value in iter_fields(top[0][2]):
            if number == f["paddock_id"] and wire_type == VARINT:
                paddock_id = value
            elif number == f["content"] and wire_type == LEN:
                content = value
            else:
                return None
        parsed = _content(content, f, stable) if content is not None else None
    except WireError:
        return None
    if paddock_id is None or parsed is None:
        return None
    return Paddock(paddock_id, *parsed)


def parse_paddock_update(body: bytes, mapping: Mapping, stable: Mapping | None) -> Paddock | None:
    """L'enclos ouvert, renvoyé à chaque tick : même contenu, sans son numéro."""
    if stable is None:
        return None
    f = mapping.fields
    try:
        top = list(iter_fields(body))
        if len(top) != 1 or top[0][0] != f["wrap"] or top[0][1] != LEN:
            return None
        parsed = _content(top[0][2], f, stable)
    except WireError:
        return None
    return Paddock(None, *parsed) if parsed is not None else None
