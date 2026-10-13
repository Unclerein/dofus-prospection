"""Journal d'un combat : qui lance quoi, qui perd ou regagne combien, qui meurt, tour par tour.

Lecture seule, à partir de ce que le serveur envoie au client. Le journal ne sait rien de plus que
le joueur : un ennemi invisible n'a pas de case, et on ne lui en invente pas.

L'entrée est la forme à plat de `tools.dump_window` (« 37.6=49 ») : le même code lit un message de
l'archive et une ligne d'extrait. Les clés ci-dessous sont celles du build d'octobre 2026 ; elles
tournent à chaque build et devront passer par la table des clés une fois leur réidentification écrite.
"""
from collections import defaultdict
from dataclasses import dataclass, field

FIGHT_START, FIGHT_END, TURN, ACTION = "jxr", "jwp", "jvj", "jsq"

# Champ 22 d'une action : ce qu'elle fait.
CAST, DEATH, SUMMON, INVISIBLE = 300, 103, 181, 150
AP_USED, MP_USED = 102, 129
# Champ 37.4 d'une ligne de dégâts ; absent pour le neutre. Le code d'action dit la même chose
# (96 eau, 97 terre, 98 air, 99 feu, 100 neutre, 92 et 93 vols de vie, 80 poussée) mais pas toujours (1133, 2822).
ELEMENTS = {None: "neutre", 1: "terre", 2: "feu", 3: "eau", 4: "air", -1: "poussée"}

Fields = list[tuple[str, str]]


def parse_line(line: str) -> tuple[float, str, str, Fields] | None:
    """(délai, sens, clé, champs) d'une ligne d'extrait ; None pour l'en-tête ou une ligne illisible."""
    head, sep, tail = line.partition(" | ")
    parts = head.split()
    if not sep or len(parts) != 4:
        return None
    try:
        when = float(parts[0])
    except ValueError:
        return None
    return when, parts[1], parts[2], [tuple(pair.split("=", 1)) for pair in tail.split() if "=" in pair]


def number(fields: Fields, path: str) -> int | None:
    """Première valeur entière de ce champ."""
    for name, value in fields:
        if name == path:
            try:
                return int(value)
            except ValueError:
                return None
    return None


@dataclass
class Event:
    when: float
    kind: str  # start, end, turn, cast, damage, heal, death, summon, push, invisible, spent
    source: int | None = None
    target: int | None = None
    amount: int | None = None
    element: str = ""
    spell: int | None = None
    cell: int | None = None
    round: int | None = None


def event(when: float, direction: str, key: str, fields: Fields) -> Event | None:
    """L'événement de combat que porte ce message, s'il en porte un."""
    if direction != "s2c":
        return None
    if key == FIGHT_START:
        return Event(when, "start")
    if key == FIGHT_END:
        return Event(when, "end")
    if key == TURN:
        return Event(when, "turn", source=number(fields, "3"), round=number(fields, "7"))
    if key != ACTION:
        return None
    action, source = number(fields, "22"), number(fields, "36")
    if action == CAST:  # y compris les effets passifs qui se relancent seuls à chaque début de tour
        return Event(when, "cast", source, number(fields, "2.6"), spell=number(fields, "2.2.2"), cell=number(fields, "2.5"))
    if action == DEATH:
        return Event(when, "death", source, number(fields, "3.2"))
    if action == SUMMON:
        return Event(when, "summon", number(fields, "12.2.1.2.3"), number(fields, "12.2.1.5.3.1"),
                     spell=number(fields, "12.2.1.1.3.5"), cell=number(fields, "12.2.1.5.3.2.3"))
    if action == INVISIBLE:
        return Event(when, "invisible", source, number(fields, "14.2"), amount=number(fields, "14.1"))
    if action in (AP_USED, MP_USED):
        return Event(when, "spent", source, number(fields, "13.3"), amount=number(fields, "13.2"), element="PA" if action == AP_USED else "PM")
    if number(fields, "37.6") is not None:
        return Event(when, "damage", source, number(fields, "37.5"), amount=number(fields, "37.6"), element=ELEMENTS.get(number(fields, "37.4"), ""))
    if number(fields, "39.4") is not None:
        return Event(when, "heal", source, number(fields, "39.2"), amount=number(fields, "39.4"))
    if number(fields, "5.3") is not None:
        return Event(when, "push", source, number(fields, "5.1"), cell=number(fields, "5.3"))
    return None


@dataclass
class Tally:
    dealt: int = 0
    taken: int = 0
    healed: int = 0
    dead: bool = False
    summoner: int | None = None


@dataclass
class Fight:
    events: list[Event] = field(default_factory=list)
    fighters: dict[int, Tally] = field(default_factory=lambda: defaultdict(Tally))
    rounds: int = 0
    ended: bool = False

    def add(self, item: Event) -> None:
        self.events.append(item)
        if item.kind == "turn" and item.round:
            self.rounds = max(self.rounds, item.round)
        elif item.kind == "damage" and item.target is not None:
            self.fighters[item.target].taken += item.amount
            if item.source is not None:
                self.fighters[item.source].dealt += item.amount
        elif item.kind == "heal" and item.target is not None:
            self.fighters[item.target].healed += item.amount
        elif item.kind == "death" and item.target is not None:
            self.fighters[item.target].dead = True
        elif item.kind == "summon" and item.target is not None:
            self.fighters[item.target].summoner = item.source
        elif item.kind == "end":
            self.ended = True


def fights(messages) -> list[Fight]:
    """Les combats d'une suite de messages (délai, sens, clé, champs), dans l'ordre."""
    out: list[Fight] = []
    current: Fight | None = None
    for when, direction, key, fields in messages:
        item = event(when, direction, key, fields)
        if item is None:
            continue
        if item.kind == "start":
            current = Fight()
            out.append(current)
        if current is not None:
            current.add(item)
            if item.kind == "end":
                current = None
    return out


def read_extract(text: str) -> list[Fight]:
    """Les combats d'un extrait écrit par `tools.dump_window`."""
    return fights(parsed for parsed in map(parse_line, text.splitlines()) if parsed)
