"""La page Donjons de l'interface : la liste des donjons et des avis de recherche, et la fiche de chacun.

Tout part des fiches calculées (données du jeu) et des notes écrites (data/bestiary/notes) ; rien n'est écrit ici, sauf
le statut « vérifié en jeu » d'une note, qui change son fichier (et la renvoie donc au hub à la synchro suivante).
"""
import dataclasses
import json
import re
from collections import Counter
from pathlib import Path

from . import card as cards
from . import notes as written
from .monster import Game
from .notes import Note

VERIFIED = "vérifié en jeu"
PENDING = "à confirmer"


def status(note: Note | None) -> str:
    """« vérifié », « note » ou « calculé » (pas de note : seulement la fiche calculée)."""
    if note is None:
        return "calculé"
    return "vérifié" if note.status == VERIFIED else "note"


def index(game: Game, notes: dict[int, Note], wanted: dict[int, Note], visits: Counter) -> dict:
    """Tous les donjons à boss et tous les avis de recherche, avec ce qu'il faut pour les lister et les chercher."""
    dungeons = []
    for dungeon, level in game.conn.execute("SELECT id, optimalPlayerLevel FROM DungeonData WHERE bosses != '[]'"):
        name, bosses, monsters = game.dungeon(dungeon)
        boss = bosses[0]
        dungeons.append({
            "id": dungeon, "name": name, "level": level, "boss": game.monster_name(boss), "gfx": game.gfx(boss),
            "status": status(notes.get(dungeon)), "visits": visits[dungeon],
            "monsters": [game.monster_name(m) for m in monsters],
        })  # fmt: skip
    hunted = []
    for monster, grades in game.conn.execute("SELECT id, grades FROM MonsterData WHERE isBounty = 1"):
        hunted.append({"id": monster, "name": game.monster_name(monster), "level": json.loads(grades)[-1]["level"],
                       "gfx": game.gfx(monster), "status": status(wanted.get(monster))})  # fmt: skip
    dungeons.sort(key=lambda d: (-d["visits"], -(d["level"] or 0), d["name"]))
    hunted.sort(key=lambda w: (w["status"] == "calculé", w["name"]))
    return {"dungeons": dungeons, "wanted": hunted, "notes": len(notes) + len(wanted)}


def _card(card: cards.Card) -> dict:
    return dataclasses.asdict(card)


def dungeon_page(game: Game, notes: dict[int, Note], dungeon: int) -> dict | None:
    row = game.conn.execute("SELECT optimalPlayerLevel FROM DungeonData WHERE id = ?", (dungeon,)).fetchone()
    if row is None:
        return None
    name, bosses, monsters = game.dungeon(dungeon)
    note = notes.get(dungeon)
    built = [cards.build(game, notes, monster, dungeon) for monster in monsters]
    focus = list(note.focus) if note else []
    # Le boss d'abord, puis l'ordre de focus du guide, puis les autres.
    built.sort(key=lambda c: (not c.boss, focus.index(c.monster) if c.monster in focus else len(focus), c.name))
    boss = next((c for c in built if c.boss), built[0] if built else None)
    return {
        "id": dungeon, "kind": "donjon", "name": name, "level": row[0], "boss": boss.name if boss else "", "gfx": boss.gfx if boss else 0,
        "status": status(note), "note_key": f"donjon-{dungeon}" if note else None,
        "source": note.source.split(" — ", 1)[-1] if note else "", "date": note.date if note else "",
        "essentials": [list(e) for e in note.essentials] if note else [],
        "focus": [{"id": m, "name": game.monster_name(m), "gfx": game.gfx(m)} for m in focus],
        "monsters": [_card(c) for c in built],
    }  # fmt: skip


def wanted_page(game: Game, notes: dict[int, Note], wanted: dict[int, Note], monster: int) -> dict | None:
    row = game.conn.execute("SELECT grades FROM MonsterData WHERE id = ? AND isBounty = 1", (monster,)).fetchone()
    if row is None:
        return None
    note = wanted.get(monster)
    star = cards.build(game, notes, monster, None, wanted)
    others = [cards.build(game, notes, m, None, wanted) for m in (note.monsters if note else {}) if m != monster]
    return {
        "id": monster, "kind": "avis", "name": star.name, "level": json.loads(row[0])[-1]["level"], "boss": star.name, "gfx": star.gfx,
        "status": status(note), "note_key": f"avis-{monster}" if note else None,
        "source": note.source.split(" — ", 1)[-1] if note else "", "date": note.date if note else "",
        "essentials": [list(e) for e in note.essentials] if note else [], "focus": [],
        "monsters": [_card(star)] + [_card(c) for c in others],
    }  # fmt: skip


def set_verified(key: str, verified: bool, directory: Path | None = None) -> bool:
    """Marque une note « vérifié en jeu » (ou de nouveau « à confirmer »). Le fichier change, donc la note repart au hub."""
    if not re.fullmatch(r"(donjon|avis)-\d{1,7}", key):
        return False
    path = (directory or written.NOTES_DIR) / f"{key}.toml"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    line = f'statut = "{VERIFIED if verified else PENDING}"'
    text, count = re.subn(r'(?m)^statut = ".*"$', line, text)
    if not count:
        text = text.replace("\n", f"\n{line}\n", 1)
    path.write_text(text, encoding="utf-8")
    return True
