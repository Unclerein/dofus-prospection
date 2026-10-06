"""Personnages du compte et niveaux de métier du personnage connecté.

Trois messages :
  - la liste des personnages, envoyée avant le choix (identifiant, nom, niveau) ;
  - le choix du personnage, envoyé par le client (identifiant seul) ;
  - les métiers du personnage : la liste complète à la connexion, puis un métier à la fois
    à chaque gain d'expérience.

Le nom du personnage est le seul texte lu : il sert à le reconnaître dans l'onglet Config.
Donnée personnelle : elle reste dans la base locale, et aucune fixture réelle n'est commitée.
"""
from dataclasses import dataclass

from ..protocol.wire import LEN, VARINT, WireError, iter_fields
from . import Mapping

MAX_JOB_LEVEL = 200
MAX_NAME_BYTES = 64


@dataclass(frozen=True, slots=True)
class Character:
    id: int
    name: str
    level: int


@dataclass(frozen=True, slots=True)
class JobLevel:
    job_id: int
    level: int
    xp: int


def parse_list(body: bytes, mapping: Mapping) -> tuple[Character, ...] | None:
    """Renvoie les personnages du compte, ou None si le corps n'a pas la forme attendue."""
    f = mapping.fields
    characters = []
    try:
        for number, wire_type, value in iter_fields(body):
            if number != f["entries"] or wire_type != LEN:
                continue
            character_id = level = 0
            name = None
            for sub_number, sub_type, sub_value in iter_fields(value):
                if sub_number == f["id"] and sub_type == VARINT:
                    character_id = sub_value
                elif sub_number == f["info"] and sub_type == LEN:
                    for i_number, i_type, i_value in iter_fields(sub_value):
                        if i_number == f["level"] and i_type == VARINT:
                            level = i_value
                        elif i_number == f["name"] and i_type == LEN:
                            name = i_value
            if character_id <= 0 or not name or len(name) > MAX_NAME_BYTES or not 0 < level < 10_000:
                return None
            text = name.decode("utf-8")
            if not text.isprintable():
                return None
            characters.append(Character(character_id, text, level))
    except (WireError, UnicodeDecodeError):
        return None
    return tuple(characters) if characters else None


def parse_selection(body: bytes, mapping: Mapping) -> int | None:
    """Identifiant du personnage choisi, ou None si le corps n'a pas la forme attendue."""
    try:
        fields = list(iter_fields(body))
    except WireError:
        return None
    if len(fields) != 1:
        return None
    number, wire_type, value = fields[0]
    return value if number == mapping.fields["id"] and wire_type == VARINT and value > 0 else None


def parse_jobs(body: bytes, mapping: Mapping) -> tuple[JobLevel, ...] | None:
    """Renvoie les métiers du message (tous, ou celui qui vient de gagner de l'expérience)."""
    f = mapping.fields
    jobs = []
    try:
        for number, wire_type, value in iter_fields(body):
            if number != f["entries"] or wire_type != LEN:
                continue
            job_id = level = xp = 0
            for sub_number, sub_type, sub_value in iter_fields(value):
                if sub_type != VARINT:
                    return None
                if sub_number == f["job_id"]:
                    job_id = sub_value
                elif sub_number == f["level"]:
                    level = sub_value
                elif sub_number == f["xp"]:
                    xp = sub_value  # absent à 0 d'expérience
            if job_id <= 0 or not 1 <= level <= MAX_JOB_LEVEL or xp > 10**12:
                return None
            jobs.append(JobLevel(job_id, level, xp))
    except WireError:
        return None
    return tuple(jobs) if jobs else None
