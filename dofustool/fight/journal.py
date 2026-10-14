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
SHEET, MY_LIFE = "jwj", "kqo"  # fiche d'un combattant à son entrée ; mes points de vie hors combat
STAT = "jtk"  # une stat d'un combattant change
MOVE = "jnz"  # déplacement à pied : le chemin, case par case ; absent pour un ennemi invisible
# Cases de départ des deux équipes : à l'entrée en combat, et avant le combat quand le joueur demande l'aperçu (touche L).
START_CELLS, PREVIEW = "jxk", "jvk"
ORDER = "jsr"  # du client vers le serveur : le joueur lance un sort (1 : sort, 2 : case)
SHIELD_STAT = 96

# Les points de vie de départ sont la somme de deux stats de la fiche, toutes parts confondues
# (base, équipement, bonus) : la vie de base (stat 0, dont l'identifiant n'est donc pas écrit) et la vitalité.
LIFE_STATS = (0, 11)
SHEET_STATS, SUMMON_STATS = "1.3.4.7.4.", "12.2.1.2.4."

# Champ 22 d'une action : ce qu'elle fait.
CAST, DEATH, SUMMON, INVISIBLE = 300, 103, 181, 150
TELEPORT, SWAP = 4, 8  # téléportation sur une case (20.1 case, 20.2 combattant) ; changement de case (4.1 combattant, 4.2 départ, 4.3 arrivée)
POISON = 3793  # un poison ou un effet posé plus tôt se déclenche ; la ligne de dégâts suit
AP_USED, MP_USED = 102, 129
# Champ 37.4 d'une ligne de dégâts ; absent pour le neutre. Le code d'action dit la même chose
# (96 eau, 97 terre, 98 air, 99 feu, 100 neutre, 92 et 93 vols de vie, 80 poussée) mais pas toujours (1133, 2822).
ELEMENTS = {None: "neutre", 1: "terre", 2: "feu", 3: "eau", 4: "air", -1: "poussée"}

Fields = list[tuple[str, str]]


def parse_line(line: str) -> tuple[float, str, str, Fields] | None:
    """(délai, sens, clé, champs) d'une ligne d'extrait ; None pour l'en-tête ou une ligne illisible."""
    head, sep, tail = line.partition("|")  # un message sans champ finit par la barre, sans rien derrière
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


def life(fields: Fields, prefix: str) -> int | None:
    """Points de vie que donne une liste de stats (identifiant en « .2 », valeurs dans les champs qui suivent)."""
    total, stat, found = 0, None, False
    seen: set[str] = set()  # champs de la stat en cours : un champ qui revient ouvre la stat suivante, sans identifiant
    for name, value in fields:
        if not name.startswith(prefix):
            continue
        if name == prefix + "2":
            stat, seen = int(value), set()
            continue
        if name in seen:
            stat, seen = 0, set()
        seen.add(name)
        if stat in LIFE_STATS and not value.startswith("LEN"):
            total, found = total + int(value), True
    return total if found else None


def cells(fields: Fields, path: str) -> list[int]:
    """Liste de cases de ce champ (« [352,366] » dans un extrait)."""
    for name, value in fields:
        if name == path and value.startswith("[") and value.endswith("]"):
            return [int(part) for part in value[1:-1].split(",")]
    return []


@dataclass
class Preview:
    """Aperçu d'un combat avant de le lancer : ce que le jeu montre avec la touche L."""
    map_id: int | None
    fight_map: int | None
    sides: tuple[list[int], list[int]]


def preview(key: str, fields: Fields) -> Preview | None:
    if key != PREVIEW:
        return None
    return Preview(number(fields, "1"), number(fields, "4"), (cells(fields, "2.1"), cells(fields, "2.2")))


@dataclass
class Event:
    when: float
    kind: str  # start, end, turn, sheet, life, sides, move, cast, trigger, damage, heal, shield, death, summon, push, teleport, invisible, spent
    source: int | None = None
    target: int | None = None
    amount: int | None = None  # dégâts, soin, points de vie, points de bouclier, PA ou PM dépensés, selon l'événement
    element: str = ""
    spell: int | None = None  # sort lancé ; pour une fiche ou une invocation, le numéro du monstre
    cell: int | None = None
    round: int | None = None
    absorbed: int = 0  # part d'un coup prise par le bouclier
    path: tuple[list[int], ...] = ()  # chemin d'un déplacement, ou cases de départ des deux équipes


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
    if key == SHEET:
        return Event(when, "sheet", target=number(fields, "1.1"), amount=life(fields, SHEET_STATS),
                     spell=number(fields, "1.3.4.6.3.3"), cell=number(fields, "1.2.3"))
    if key == MY_LIFE:
        return Event(when, "life", amount=number(fields, "3"))
    if key == START_CELLS:
        return Event(when, "sides", path=(cells(fields, "1.1"), cells(fields, "1.2")))
    if key == MOVE:
        path = cells(fields, "1")
        return Event(when, "move", target=number(fields, "4"), cell=path[-1], path=(path,)) if path else None
    if key == STAT:
        if number(fields, "1.4.2") != SHIELD_STAT:
            return None
        return Event(when, "shield", target=number(fields, "2"), amount=number(fields, "1.4.3.2") or 0)
    if key != ACTION:
        return None
    action, source = number(fields, "22"), number(fields, "36")
    if action == CAST:  # y compris les effets passifs qui se relancent seuls à chaque début de tour
        return Event(when, "cast", source, number(fields, "2.6"), spell=number(fields, "2.2.2"), cell=number(fields, "2.5"))
    if action == DEATH:
        return Event(when, "death", source, number(fields, "3.2"))
    if action == SUMMON:
        return Event(when, "summon", number(fields, "12.2.1.2.3"), number(fields, "12.2.1.5.3.1"), amount=life(fields, SUMMON_STATS),
                     spell=number(fields, "12.2.1.1.3.5"), cell=number(fields, "12.2.1.5.3.2.3"))
    if action == INVISIBLE:
        return Event(when, "invisible", source, number(fields, "14.2"), amount=number(fields, "14.1"))
    if action in (AP_USED, MP_USED):
        return Event(when, "spent", source, number(fields, "13.3"), amount=number(fields, "13.2"), element="PA" if action == AP_USED else "PM")
    if action == POISON:
        return Event(when, "trigger", source, spell=number(fields, "25.1"), cell=number(fields, "25.5"))
    if number(fields, "37.6") is not None or number(fields, "37.2") is not None:
        return Event(when, "damage", source, number(fields, "37.5"), amount=number(fields, "37.6") or 0,
                     element=ELEMENTS.get(number(fields, "37.4"), ""), absorbed=number(fields, "37.2") or 0)
    if number(fields, "39.4") is not None:
        return Event(when, "heal", source, number(fields, "39.2"), amount=number(fields, "39.4"))
    if number(fields, "5.3") is not None:
        return Event(when, "push", source, number(fields, "5.1"), cell=number(fields, "5.3"))
    if action == TELEPORT:
        return Event(when, "teleport", source, number(fields, "20.2"), cell=number(fields, "20.1"))
    if action == SWAP:
        return Event(when, "teleport", source, number(fields, "4.1"), cell=number(fields, "4.3"))
    return None


@dataclass
class Tally:
    dealt: int = 0
    taken: int = 0
    healed: int = 0
    absorbed: int = 0
    shield: int = 0  # points de bouclier en ce moment
    dead: bool = False
    summoner: int | None = None
    cell: int | None = None  # numéro de case Dofus (0 à 559, voir fight.grid) ; inconnue pour un ennemi invisible
    monster: int | None = None  # None pour un personnage ou un compagnon
    max_life: int | None = None
    start_life: int | None = None

    @property
    def life(self) -> int | None:
        """Points de vie restants, d'après les coups et les soins vus ; l'érosion n'est pas comptée."""
        if self.start_life is None:
            return None
        return 0 if self.dead else max(0, min(self.max_life, self.start_life - self.taken + self.healed))


@dataclass
class Fight:
    events: list[Event] = field(default_factory=list)
    fighters: dict[int, Tally] = field(default_factory=lambda: defaultdict(Tally))
    rounds: int = 0
    sides: tuple[list[int], ...] = ()  # cases de départ de chaque équipe
    ended: bool = False

    def add(self, item: Event) -> None:
        self.events.append(item)
        kind = item.kind
        if kind == "turn":
            self.rounds = max(self.rounds, item.round or 0)
        elif kind == "sides":
            self.sides = item.path
        elif kind == "end":
            self.ended = True
        if item.target is None or kind not in ("sheet", "summon", "damage", "heal", "shield", "death", "push", "move", "teleport"):
            return
        tally = self.fighters[item.target]
        if kind in ("sheet", "summon"):
            tally.max_life = tally.start_life = item.amount
            tally.cell = item.cell
            if kind == "sheet":
                tally.monster = item.spell
            else:
                tally.summoner = item.source
        elif kind == "damage":
            tally.taken += item.amount
            tally.absorbed += item.absorbed
            if item.source is not None:
                self.fighters[item.source].dealt += item.amount
        elif kind == "heal":
            tally.healed += item.amount
        elif kind == "shield":
            tally.shield = item.amount
        elif kind == "death":
            tally.dead = True
        elif item.cell is not None and item.cell >= 0:  # poussée, déplacement ou téléportation
            tally.cell = item.cell


class Reader:
    """Lit les messages un à un et tient le combat en cours : le même code sert après coup et en direct."""

    def __init__(self) -> None:
        self.current: Fight | None = None
        self.finished: list[Fight] = []
        self._sheets: dict[int, Event] = {}  # fiches reçues pendant le placement, avant le début
        self._my_life: int | None = None
        self._sides: Event | None = None

    def feed(self, when: float, direction: str, key: str, fields: Fields) -> Event | None:
        """L'événement porté par ce message, une fois pris en compte ; None s'il n'en porte pas."""
        item = event(when, direction, key, fields)
        if item is None:
            return None
        if item.kind == "life":
            self._my_life = item.amount
        elif item.kind == "start":
            self.current = Fight()
            for sheet in self._sheets.values():
                self.current.add(sheet)
                if sheet.target > 0 and self._my_life is not None and self._my_life <= (sheet.amount or 0):
                    self.current.fighters[sheet.target].start_life = self._my_life
            if self._sides is not None:
                self.current.add(self._sides)
            self._sheets, self._sides = {}, None
            self.current.events.append(item)
        elif self.current is None:
            if item.kind == "sheet" and item.target is not None:
                self._sheets[item.target] = item
            elif item.kind == "sides":
                self._sides = item
        else:
            self.current.add(item)
            if item.kind == "end":
                self.finished.append(self.current)
                self.current, self._sheets, self._sides = None, {}, None
        return item


def fights(messages) -> list[Fight]:
    """Les combats d'une suite de messages (délai, sens, clé, champs), dans l'ordre ; le dernier peut être inachevé."""
    reader = Reader()
    for when, direction, key, fields in messages:
        reader.feed(when, direction, key, fields)
    return reader.finished + ([reader.current] if reader.current is not None else [])


def read_extract(text: str) -> list[Fight]:
    """Les combats d'un extrait écrit par `tools.dump_window`."""
    return fights(parsed for parsed in map(parse_line, text.splitlines()) if parsed)
