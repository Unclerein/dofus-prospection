"""Le Bestiaire de l'interface : la liste des donjons, des monstres et des avis de recherche, et la fiche de chacun.

Tout part des fiches calculées (données du jeu) et des notes écrites (data/bestiary/notes) ; rien n'est écrit ici.
"""
import dataclasses
import json
from collections import Counter

from . import card as cards
from .monster import Game
from .notes import Note


def status(note: Note | None) -> str:
    """« note » (une note écrite d'après le guide) ou « calculé » (seulement la fiche calculée)."""
    return "note" if note is not None else "calculé"


def index(game: Game, notes: dict[int, Note], wanted: dict[int, Note], visits: Counter) -> dict:
    """Tous les donjons à boss, tous les monstres nommés et tous les avis de recherche, pour les lister et les chercher."""
    dungeons, home = [], {}
    for dungeon, level in game.conn.execute("SELECT id, optimalPlayerLevel FROM DungeonData WHERE bosses != '[]'"):
        name, bosses, monsters = game.dungeon(dungeon)
        boss = bosses[0]
        for monster in monsters:
            home.setdefault(monster, name)
        dungeons.append({
            "id": dungeon, "name": name, "level": level, "boss": game.monster_name(boss), "gfx": game.gfx(boss),
            "status": status(notes.get(dungeon)), "visits": visits[dungeon],
            "monsters": [game.monster_name(m) for m in monsters],
        })  # fmt: skip
    written = {m for note in notes.values() for m in note.monsters} | set(wanted)
    monsters, hunted = [], []
    rows = game.conn.execute(
        "SELECT m.id, t.value, m.gfxId, m.grades, m.isBounty FROM MonsterData m "
        "JOIN translations t ON t.id = m.nameId AND t.lang = 'fr' WHERE t.value != ''"
    ).fetchall()
    for monster, name, gfx, grades, bounty in rows:
        level = json.loads(grades)[-1]["level"] if grades and grades != "[]" else 0
        entry = {"id": monster, "name": name, "level": level, "gfx": gfx, "where": home.get(monster, ""),
                 "status": "note" if monster in written else "calculé"}  # fmt: skip
        monsters.append(entry)
        if bounty:
            hunted.append({**entry, "status": status(wanted.get(monster))})
    dungeons.sort(key=lambda d: (-d["visits"], -(d["level"] or 0), d["name"]))
    monsters.sort(key=lambda m: (m["where"] == "", m["name"]))  # ceux des donjons d'abord
    hunted.sort(key=lambda w: (w["status"] == "calculé", w["name"]))
    return {"dungeons": dungeons, "monsters": monsters, "wanted": hunted, "notes": len(notes) + len(wanted)}


def _card(card: cards.Card) -> dict:
    return dataclasses.asdict(card)


def _note_fields(note: Note | None) -> dict:
    return {"source": note.source.split(" — ", 1)[-1] if note else "", "date": note.date if note else "",
            "essentials": [list(e) for e in note.essentials] if note else []}  # fmt: skip


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
        "status": status(note), **_note_fields(note),
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
        "status": status(note), **_note_fields(note), "focus": [],
        "monsters": [_card(star)] + [_card(c) for c in others],
    }  # fmt: skip


def monster_page(game: Game, notes: dict[int, Note], wanted: dict[int, Note], monster: int) -> dict | None:
    """La fiche d'un seul monstre, et les donjons où on le croise."""
    row = game.conn.execute("SELECT grades FROM MonsterData WHERE id = ?", (monster,)).fetchone()
    if row is None:
        return None
    star = cards.build(game, notes, monster, None, wanted)
    found = [d for (d,) in game.conn.execute("SELECT DISTINCT dungeonId FROM SubAreaData WHERE dungeonId > 0 AND monsters LIKE ?", (f"%{monster}%",))
             if monster in game.dungeon(d)[2]]  # fmt: skip
    return {
        "id": monster, "kind": "monstre", "name": star.name, "level": json.loads(row[0])[-1]["level"] if row[0] != "[]" else 0,
        "boss": star.name, "gfx": star.gfx, "status": "note" if star.footer.startswith("D'après") else "calculé",
        "source": "", "date": "", "essentials": [], "focus": [],
        "dungeons": [{"id": d, "name": game.dungeon(d)[0]} for d in found],
        "monsters": [_card(star)],
    }  # fmt: skip
