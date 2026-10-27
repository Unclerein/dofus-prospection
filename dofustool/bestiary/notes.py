"""Notes écrites des donjons (notes/<donjon>-<nom>.toml) et des avis de recherche (notes/avis/<monstre>-<nom>.toml) :
l'essentiel, l'ordre de focus, et ce qu'il faut savoir de chaque monstre.

Rédigées d'après Dofus pour les Noobs, en nos propres mots ; chaque note dit sa source, sa date et si elle a été vérifiée en jeu.
"""
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

NOTES_DIR = Path(__file__).resolve().parent / "notes"
WANTED_DIR = NOTES_DIR / "avis"


@dataclass(frozen=True)
class Spell:
    name: str
    reach: str = ""
    effect: str = ""
    limit: str = ""


@dataclass(frozen=True)
class MonsterNote:
    name: str
    reach: str = ""
    moves: str = ""
    mechanic: str = ""
    spells: tuple[Spell, ...] = ()


@dataclass(frozen=True)
class Note:
    dungeon: int
    name: str
    source: str
    date: str
    status: str
    focus: tuple[int, ...]
    essentials: tuple[tuple[str, str, str], ...]  # (« ok » ou « danger », titre, texte)
    monsters: dict[int, MonsterNote] = field(default_factory=dict)
    wanted: int | None = None  # avis de recherche : le monstre recherché (la note n'a alors pas de donjon)


def parse(raw: dict) -> Note:
    monsters = {}
    for key, entry in raw.get("monstres", {}).items():
        spells = tuple(Spell(s.get("nom", ""), s.get("portee", ""), s.get("effet", ""), s.get("limite", "")) for s in entry.get("sorts", []))
        monsters[int(key)] = MonsterNote(entry.get("nom", ""), entry.get("portee", ""), entry.get("deplacement", ""), entry.get("mecanique", ""), spells)
    essentials = tuple((e.get("type", "ok"), e.get("titre", ""), e.get("texte", "")) for e in raw.get("essentiel", []))
    wanted = int(raw["avis"]) if "avis" in raw else None
    return Note(int(raw.get("donjon", 0)), raw.get("nom", ""), raw.get("source", ""), raw.get("date_source", ""), raw.get("statut", ""),
                tuple(int(m) for m in raw.get("focus", [])), essentials, monsters, wanted)  # fmt: skip


def load(directory: Path = NOTES_DIR) -> dict[int, Note]:
    """Toutes les notes, par donjon. Une note illisible est ignorée : elle ne doit pas empêcher les autres de servir."""
    notes = {}
    for path in sorted(directory.glob("*.toml")):
        try:
            note = parse(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        notes[note.dungeon] = note
    return notes


def load_wanted(directory: Path = WANTED_DIR) -> dict[int, Note]:
    """Les notes d'avis de recherche, par monstre recherché."""
    notes = {}
    for path in sorted(directory.glob("*.toml")):
        try:
            note = parse(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if note.wanted:
            notes[note.wanted] = note
    return notes
