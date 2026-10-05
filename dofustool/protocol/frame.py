"""Désenveloppement d'une frame de jeu : Frame -> google.protobuf.Any -> (clé, corps).

Les numéros de champ de l'enveloppe changent d'un build à l'autre. On ne les code donc
pas en dur : on cherche l'Any (champ 1 = type_url « type.ankama.com/<clé> », champ 2 =
corps) dans les premiers niveaux de la frame. Observé sur le build de la capture de test :

    client -> serveur   1{ 1: Any, 2: id de corrélation }
    serveur -> client   2{ 3: Any }                          (événement)
                        3{ 1: Any, 2: id de corrélation }    (réponse)
"""
from dataclasses import dataclass

from .wire import LEN, VARINT, WireError, iter_fields, to_signed

TYPE_URL_PREFIX = b"type.ankama.com/"
_MAX_DEPTH = 3


@dataclass(frozen=True, slots=True)
class Envelope:
    key: str
    body: bytes
    frame_field: int  # champ de premier niveau : distingue requête / réponse / événement
    correlation: int | None


def _as_any(buf: bytes) -> tuple[str, bytes] | None:
    url = None
    body = b""
    try:
        for number, wire_type, value in iter_fields(buf):
            if wire_type != LEN or number not in (1, 2):
                return None
            if number == 1:
                url = value
            else:
                body = value
    except WireError:
        return None
    if url is None or not url.startswith(TYPE_URL_PREFIX):
        return None
    try:
        return url[len(TYPE_URL_PREFIX) :].decode("ascii"), bytes(body)
    except UnicodeDecodeError:
        return None


def _find_any(buf: bytes, depth: int) -> tuple[str, bytes, int | None] | None:
    try:
        fields = list(iter_fields(buf))
    except WireError:
        return None
    for number, wire_type, value in fields:
        if wire_type != LEN or not value:
            continue
        found = _as_any(value)
        if found is not None:
            varints = [v for _, wt, v in fields if wt == VARINT]
            correlation = to_signed(varints[0]) if len(varints) == 1 else None
            return found[0], found[1], correlation
        if depth < _MAX_DEPTH:
            deeper = _find_any(value, depth + 1)
            if deeper is not None:
                return deeper
    return None


def unwrap(frame: bytes) -> Envelope | None:
    """Renvoie l'enveloppe décodée, ou None si la frame n'est pas un message de jeu."""
    try:
        top = list(iter_fields(frame))
    except WireError:
        return None
    if len(top) != 1 or top[0][1] != LEN:
        return None
    frame_field, _, inner = top[0]
    found = _find_any(inner, 1)
    if found is None:
        return None
    key, body, correlation = found
    return Envelope(key=key, body=body, frame_field=frame_field, correlation=correlation)
