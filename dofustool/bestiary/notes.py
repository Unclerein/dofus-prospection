"""Notes écrites des donjons (donjon-<id>.toml) et des avis de recherche (avis-<monstre>.toml) : l'essentiel, l'ordre
de focus, et ce qu'il faut savoir de chaque monstre.

Rédigées d'après Dofus pour les Noobs, en nos propres mots ; chaque note dit sa source, sa date et si elle a été vérifiée
en jeu. Elles restent hors du dépôt (data/bestiary/notes) et passent d'un joueur à l'autre par le hub de partage.
"""
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

NOTES_DIR = Path(__file__).resolve().parents[2] / "data" / "bestiary" / "notes"


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


def key_of(note: Note) -> str | None:
    """Nom d'une note, partout le même (fichier, hub) : « donjon-98 », « avis-460 »."""
    if note.wanted:
        return f"avis-{note.wanted}"
    return f"donjon-{note.dungeon}" if note.dungeon > 0 else None


def read(text: str) -> Note | None:
    try:
        return parse(tomllib.loads(text))
    except (ValueError, KeyError, TypeError):
        return None


def _all(directory: Path) -> list[Note]:
    """Toutes les notes lisibles du dossier. Une note illisible est ignorée : elle ne doit pas empêcher les autres de servir."""
    out = []
    for path in sorted(directory.glob("*.toml")):
        try:
            note = read(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if note is not None:
            out.append(note)
    return out


def load(directory: Path = NOTES_DIR) -> dict[int, Note]:
    """Les notes de donjon, par donjon."""
    return {note.dungeon: note for note in _all(directory) if not note.wanted and note.dungeon > 0}


def load_wanted(directory: Path = NOTES_DIR) -> dict[int, Note]:
    """Les notes d'avis de recherche, par monstre recherché."""
    return {note.wanted: note for note in _all(directory) if note.wanted}
