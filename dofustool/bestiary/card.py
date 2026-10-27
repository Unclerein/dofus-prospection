"""Ce qu'affiche la fiche d'un monstre en jeu : la note écrite de son donjon quand il y en a une, sinon la fiche calculée."""
from dataclasses import dataclass, field

from .monster import Game
from .notes import Note, Spell

MECHANICS_SHOWN = 2  # fiche calculée : les deux mécaniques les plus importantes suffisent


@dataclass
class Card:
    monster: int
    name: str
    gfx: int  # image du monstre (DofusDB)
    boss: bool
    subtitle: str  # « Temple de Koutoulou · niv. 212 »
    stats: str  # « 6 600 PV · 12 PA · 5 PM »
    weakest: str
    focus_rank: int | None  # sa place dans l'ordre de focus du guide
    rows: list[tuple[str, str]]  # (« PORTÉE », texte)
    essentials: tuple[tuple[str, str, str], ...] = ()
    focus: list[tuple[int, str, int]] = field(default_factory=list)  # (monstre, nom, image)
    spells: tuple[Spell, ...] = ()
    footer: str = ""


def thousands(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def pick_note(notes: dict[int, Note], monster: int, dungeon: int | None, bosses: dict[int, list[int]]) -> Note | None:
    """La note qui parle de ce monstre : celle du donjon où l'on se bat, sinon celle dont il est le boss, sinon la première."""
    if dungeon in notes and monster in notes[dungeon].monsters:
        return notes[dungeon]
    talking = [note for note in notes.values() if monster in note.monsters]
    return next((note for note in talking if monster in bosses.get(note.dungeon, [])), talking[0] if talking else None)


def build(game: Game, notes: dict[int, Note], monster: int, dungeon: int | None = None) -> Card:
    sheet = game.sheet(monster)
    bosses = {d: game.dungeon(d)[1] for d in notes}
    note = pick_note(notes, monster, dungeon, bosses)
    written = note.monsters.get(monster) if note else None
    where = note.name if note else (game.dungeon(dungeon)[0] if dungeon else "")
    computed = (sheet.reach or "", " · ".join(sheet.moves), " · ".join(sheet.mechanics[:MECHANICS_SHOWN]))
    mine = (written.reach, written.moves, written.mechanic) if written else ("", "", "")
    # Ce que la note dit l'emporte ; là où elle ne dit rien, la fiche calculée.
    rows = list(zip(("PORTÉE", "BOUGE", "SPÉCIAL"), (own or auto for own, auto in zip(mine, computed))))
    boss = bool(note) and monster in bosses.get(note.dungeon, [])
    focus = list(note.focus) if note else []
    card = Card(
        monster, sheet.name, game.gfx(monster), boss, " · ".join(part for part in (where, f"niv. {sheet.level}") if part),
        f"{thousands(sheet.life)} PV · {sheet.ap} PA · {sheet.mp} PM", sheet.weakest,
        focus.index(monster) + 1 if monster in focus else None, [(label, text) for label, text in rows if text],
    )  # fmt: skip
    if written:
        card.spells = written.spells
    if note:
        if boss:
            card.essentials = note.essentials
            card.focus = [(m, note.monsters[m].name if m in note.monsters else game.monster_name(m), game.gfx(m)) for m in focus]
        day = "/".join(reversed(note.date.split("-"))) if note.date else ""
        card.footer = f"D'après Dofus pour les Noobs{' · ' + day if day else ''}"
    else:
        card.footer = "Calculé depuis les données du jeu"
    return card
