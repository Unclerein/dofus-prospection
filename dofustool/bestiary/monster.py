"""Fiche calculée d'un monstre, depuis les données du jeu : jusqu'où il frappe, comment il bouge, ce qu'il a de spécial.

Lecture seule de dofus.sqlite. Seul ce que les effets disent sans ambiguïté est retenu ; la note écrite du
donjon complète le reste.
"""
import json
import re
import sqlite3
from dataclasses import dataclass, field
from functools import cached_property

from ..staticdata import source
from . import effects as fx

ELEMENTS = (("reductionNeutral", "Neutre"), ("reductionEarth", "Terre"), ("reductionFire", "Feu"),
            ("reductionWater", "Eau"), ("reductionAir", "Air"))  # fmt: skip
SUBSPELL_DEPTH = 3
MECHANICS_SHOWN = 4
MAX_SHOWN_VALUE = 9999
TELLING = 4  # mots : un nom de sous-sort aussi long décrit sa mécanique (« Tue les cibles dans le glyphe »)


@dataclass
class Sheet:
    id: int
    name: str
    level: int
    life: int
    ap: int
    mp: int
    weakest: str  # élément de plus faible résistance
    reach: str | None  # « Tape à 7 PO ; 10 sans ligne de vue », None s'il ne frappe pas
    moves: list[str] = field(default_factory=list)
    mechanics: list[str] = field(default_factory=list)
    passive: str = ""  # texte du jeu sur sa mécanique, quand il y en a un


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if abs(n) > 1 else ''}"


class Game:
    """Les tables du jeu utiles aux fiches, chargées une fois."""

    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self.conn = conn or source.connect()

    def text(self, text_id) -> str:
        row = self.conn.execute("SELECT value FROM translations WHERE id = ? AND lang = 'fr'", (str(text_id),)).fetchone()
        return row[0] if row else ""

    @cached_property
    def levels(self) -> dict[int, tuple]:
        """Niveau le plus haut de chaque sort : (identifiant du niveau, PO min, PO max, drapeaux, relance, effets)."""
        best: dict[int, tuple] = {}
        for level_id, spell, grade, low, high, flags, interval, raw in self.conn.execute(
            "SELECT id, spellId, grade, minRange, range, m_flags, minCastInterval, effects FROM SpellLevelData"
        ):
            if spell not in best or grade > best[spell][0]:
                best[spell] = (grade, level_id, low, high, flags or 0, interval or 0, raw)
        return {spell: (level_id, low, high, flags, interval, json.loads(raw or "[]")) for spell, (_, level_id, low, high, flags, interval, raw) in best.items()}

    @cached_property
    def templates(self) -> dict[int, str]:
        """Texte de chaque effet tel que le jeu l'affiche (« #1{{~1~2 à }}#2 dommages Feu »)."""
        return {action: self.text(text_id) for action, text_id in self.conn.execute("SELECT id, descriptionId FROM EffectData")}

    def effect_text(self, effect: dict) -> str:
        """Le texte de l'effet comme dans l'infobulle du jeu ; vide si ses valeurs ne sont pas de simples nombres."""
        low, high, value = effect.get("diceNum") or 0, effect.get("diceSide") or 0, effect.get("value") or 0
        if max(abs(low), abs(high)) > MAX_SHOWN_VALUE:
            return ""  # valeur codée (formule, identifiant) : rien de lisible à en tirer
        text = re.sub(r"\{\{~1~2(.*?)\}\}", r"\1" if high else "", self.templates.get(effect["actionId"], ""))
        text = text.replace("{{~ps}}", "s" if max(low, high) > 1 else "")
        text = text.replace("#1", str(low)).replace("#2", str(high) if high else "").replace("#3", str(value))
        return re.sub(r"\{\{.*?\}\}|<[^>]*>", "", text).strip()

    @cached_property
    def level_spell(self) -> dict[int, int]:
        return dict(self.conn.execute("SELECT id, spellId FROM SpellLevelData"))

    def spell_name(self, spell: int) -> str:
        row = self.conn.execute("SELECT nameId FROM SpellData WHERE id = ?", (spell,)).fetchone()
        return self.text(row[0]) if row else ""

    def spell_text(self, spell: int) -> str:
        row = self.conn.execute("SELECT descriptionId FROM SpellData WHERE id = ?", (spell,)).fetchone()
        return self.text(row[0]).strip() if row else ""

    def state(self, state: int) -> tuple[str, bool, bool, bool]:
        """Nom d'un état, et s'il rend invulnérable (tout, en mêlée, à distance)."""
        row = self.conn.execute("SELECT nameId, invulnerable, invulnerableMelee, invulnerableRange FROM SpellStateData WHERE id = ?", (state,)).fetchone()
        return (self.text(row[0]), bool(row[1]), bool(row[2]), bool(row[3])) if row else ("", False, False, False)

    def monster_name(self, monster: int) -> str:
        row = self.conn.execute("SELECT nameId FROM MonsterData WHERE id = ?", (monster,)).fetchone()
        return self.text(row[0]) if row else f"Monstre {monster}"

    def gfx(self, monster: int) -> int:
        """Numéro de l'image du monstre (DofusDB : img/monsters/<numéro>.png)."""
        row = self.conn.execute("SELECT gfxId FROM MonsterData WHERE id = ?", (monster,)).fetchone()
        return row[0] if row else 0

    def dungeon_of_map(self, map_id: int | None) -> int | None:
        if map_id is None:
            return None
        # Par la sous-zone de la carte : la liste des cartes d'un donjon (DungeonData) oublie des salles, dont celle du boss.
        row = self.conn.execute(
            "SELECT a.dungeonId FROM SubAreaData_mapIds_junction s JOIN SubAreaData a ON a.id = s.SubAreaData_id WHERE s.target_id = ? AND a.dungeonId > 0",
            (map_id,),
        ).fetchone()
        return row[0] if row else None

    def dungeon(self, dungeon: int) -> tuple[str, list[int], list[int]]:
        """Nom d'un donjon, ses boss, et tous ses monstres (boss compris), dans l'ordre du jeu."""
        name_id, bosses = self.conn.execute("SELECT nameId, bosses FROM DungeonData WHERE id = ?", (dungeon,)).fetchone()
        monsters: list[int] = []
        for (raw,) in self.conn.execute("SELECT monsters FROM SubAreaData WHERE dungeonId = ? ORDER BY id", (dungeon,)):
            monsters += [m for m in json.loads(raw or "[]") if m not in monsters]
        return self.text(name_id), json.loads(bosses or "[]"), monsters

    def dungeon_sheets(self, dungeon: int) -> tuple[str, list[int], list[Sheet], list[str]]:
        """Nom, boss, fiches de tous ses monstres, et le spécial commun à tous ses monstres (dit une fois, pas sur chaque fiche)."""
        name, bosses, monsters = self.dungeon(dungeon)
        sheets = [self.sheet(monster) for monster in monsters]
        others = [sheet for sheet in sheets if sheet.id not in bosses]
        common = [line for line in others[0].mechanics if all(line in sheet.mechanics for sheet in others)] if len(others) >= 2 else []
        for sheet in others:
            sheet.mechanics = [line for line in sheet.mechanics if line not in common]
        return name, bosses, sheets, common

    # --- lecture des sorts -------------------------------------------------------------------------

    def tree(self, spell: int, depth: int = 0, seen: frozenset = frozenset()):
        """Les effets d'un sort et de ses sous-sorts : (sort d'origine de l'effet, effet, déclencheur hérité)."""
        level = self.levels.get(spell)
        if level is None or spell in seen or depth > SUBSPELL_DEPTH:
            return
        for effect in level[5]:
            yield spell, effect
            if effect["actionId"] in fx.SUBSPELL and effect.get("diceNum"):
                yield from self.tree(effect["diceNum"], depth + 1, seen | {spell})

    def deals_damage(self, spell: int) -> bool:
        return any(effect["actionId"] in fx.DAMAGE for _, effect in self.tree(spell))

    # --- la fiche ----------------------------------------------------------------------------------

    def sheet(self, monster: int) -> Sheet:
        raw_grades, raw_spells = self.conn.execute("SELECT grades, spells FROM MonsterData WHERE id = ?", (monster,)).fetchone()
        grade = json.loads(raw_grades)[-1]
        spells = [s for s in json.loads(raw_spells or "[]") if s in self.levels]
        passive = self.level_spell.get(grade.get("startingSpellId") or 0)
        weakest = min(ELEMENTS, key=lambda element: grade.get(element[0], 0))[1]
        sheet = Sheet(monster, self.monster_name(monster), grade["level"], grade["lifePoints"], grade["actionPoints"], grade["movementPoints"],
                      weakest, self._reach(spells, grade.get("rangeBonus", 0)))  # fmt: skip
        sheet.moves = self._moves(spells)
        sheet.mechanics = self._mechanics(spells, passive)
        sheet.passive = self.spell_text(passive) if passive else ""
        return sheet

    def _reach(self, spells: list[int], bonus: int) -> str | None:
        """Portée max de ses sorts qui font des dommages, avec et sans ligne de vue."""
        sight = free = -1
        line = False
        for spell in spells:
            if not self.deals_damage(spell):
                continue
            _, _, high, flags, _, _ = self.levels[spell]
            reach = high + bonus if high > 1 else high
            if flags & fx.LINE_OF_SIGHT:
                if reach > sight:
                    sight, line = reach, bool(flags & fx.IN_LINE)
            else:
                free = max(free, reach)
        best = max(sight, free)
        if best < 0:
            return None
        if best <= 1:
            return "Corps à corps"
        if best >= fx.WHOLE_MAP:
            return "Frappe partout sur la carte" + ("" if free >= fx.WHOLE_MAP else ", en ligne de vue")
        if free >= sight:
            return f"Tape à {plural(free, 'case')}, sans ligne de vue"
        text = f"Tape à {plural(sight, 'case')}" + (" (en ligne)" if line else "")
        return text + (f" ; {free} sans ligne de vue" if free > 1 else "")

    def _moves(self, spells: list[int]) -> list[str]:
        """Comment il se déplace, et comment il déplace sa cible : la valeur la plus forte de chaque sorte."""
        found: dict[str, int] = {}

        def keep(kind: str, value: int) -> None:
            found[kind] = max(found.get(kind, 0), value)

        for spell in spells:
            _, _, high, _, _, _ = self.levels[spell]
            for _, effect in self.tree(spell):
                if (effect.get("triggers") or "I") != "I":
                    continue
                action, value, mask = effect["actionId"], effect.get("diceNum") or 0, effect.get("targetMask") or ""
                if action in fx.TELEPORT:
                    keep("téléporte", high)
                elif action in fx.SWAP:
                    keep("échange", high)
                elif action == fx.ADVANCE:
                    keep("avance", value)
                elif action == fx.RETREAT:
                    keep("recule", value)
                elif action in fx.GAIN_MP and (high == 0 or "C" in mask):
                    keep("pm", value)
                elif action in fx.PULL:
                    keep("attire", value)
                elif action in fx.PUSH:
                    keep("repousse", value)
                elif action in fx.SYMMETRY:
                    keep("symétrie", 1)
                elif action in fx.SEND_BACK:
                    keep("renvoie", 1)
        words = {
            "téléporte": lambda n: f"Se téléporte ({plural(n, 'case')})", "échange": lambda n: f"Échange de place ({plural(n, 'case')})",
            "avance": lambda n: "Fonce jusqu'à sa cible" if n >= fx.WHOLE_MAP else f"Avance de {plural(n, 'case')}", "recule": lambda n: f"Recule de {plural(n, 'case')}",
            "pm": lambda n: f"+{n} PM", "attire": lambda n: f"Attire de {plural(n, 'case')}", "repousse": lambda n: f"Repousse de {plural(n, 'case')}",
            "symétrie": lambda n: "Téléporte sa cible en symétrie", "renvoie": lambda n: "Renvoie sa cible à une case précédente",
        }  # fmt: skip
        return [words[kind](value) for kind, value in found.items() if value]

    def describe(self, spell: int) -> str:
        """Ce que fait un sous-sort, en quelques mots : son nom s'il l'explique, sinon ses effets."""
        name = self.spell_name(spell)
        if len(name.split()) >= TELLING:
            return name
        parts = []
        for effect in self.levels.get(spell, (0, 0, 0, 0, 0, []))[5]:
            action, mask = effect["actionId"], effect.get("targetMask") or ""
            if action in fx.RETURN_SHARE:
                parts.append(f"renvoie {effect.get('diceNum')} % des dommages" + (" (aussi à ses alliés)" if "a" in mask.split(",") else ""))
            elif action == fx.ADD_STATE:
                state = self.state(effect.get("value") or 0)[0]
                parts.append(f"état {state}" if state and "_" not in state else "")  # « Marqueur_… » : état interne
            elif action == fx.KILL:
                parts.append("tue la cible")
            elif action in fx.SUBSPELL and effect.get("diceNum") and len(self.spell_name(effect["diceNum"]).split()) >= TELLING:
                parts.append(self.spell_name(effect["diceNum"]))
            elif action not in fx.SUBSPELL and action not in fx.SILENT:
                parts.append(self.effect_text(effect))
        parts = [p for p in dict.fromkeys(parts) if p][:2]
        return " ; ".join(parts) or name

    def _mechanics(self, spells: list[int], passive: int | None) -> list[str]:
        """Le spécial, du plus important au moins : invulnérabilité, invisibilité, renvoi, réactions, le reste."""
        ranked: list[tuple[int, str]] = []

        def add(rank: int, line: str) -> None:
            if line and all(line != known for _, known in ranked):
                ranked.append((rank, line))

        protected: dict[int, str] = {}  # état d'invulnérabilité posé → comment
        removals: dict[int, list[str]] = {}
        for origin, is_passive in [(passive, True)] * bool(passive) + [(s, False) for s in spells]:
            interval = self.levels[origin][4] if origin in self.levels else 0
            for spell, effect in self.tree(origin):
                action, trigger = effect["actionId"], effect.get("triggers") or "I"
                if action == fx.INVISIBLE:
                    duration = effect.get("duration") or 0
                    text = "Devient invisible" + (f" {plural(duration, 'tour')}" if duration > 0 else "")
                    add(1, text + (f", tous les {interval} tours" if interval > 1 else ""))
                elif action == fx.ADD_STATE:
                    _, total, melee, distance = self.state(effect.get("value") or 0)
                    if total or melee or distance:
                        what = "Invulnérable" + (" au corps à corps" if melee and not total else " à distance" if distance and not total else "")
                        protected[effect["value"]] = what + ("" if is_passive else f" ({self.spell_name(origin)})")
                elif action in (fx.REMOVE_STATE, fx.DISABLE_STATE) and trigger != "I":
                    for code in trigger.split("|"):
                        if code in fx.TRIGGERS:
                            removals.setdefault(effect.get("value") or 0, []).append(fx.TRIGGERS[code])
                elif action in fx.REFLECT:
                    add(2, f"Renvoie {effect.get('diceNum') or effect.get('value')} dommages")
                elif action in fx.SUBSPELL and effect.get("diceNum") and trigger in fx.TRIGGERS:
                    add(3, f"{fx.TRIGGERS[trigger].capitalize()} : {self.describe(effect['diceNum'])}")
                elif action in fx.SUBSPELL and effect.get("diceNum") and len(self.spell_name(effect["diceNum"]).split()) >= TELLING:
                    add(4, self.spell_name(effect["diceNum"]))
                elif action in fx.SUMMON and effect.get("diceNum"):
                    add(5, f"Invoque {self.monster_name(effect['diceNum'])}")
        for state, what in protected.items():
            how = removals.get(state)
            add(0, what + (f" ; saute {' ou '.join(dict.fromkeys(how))}" if how else ""))
        return [line for _, line in sorted(ranked, key=lambda item: item[0])][:MECHANICS_SHOWN]
