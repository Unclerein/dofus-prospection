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
SHIELD_STAT = 96

# Les points de vie de départ sont la somme de deux stats de la fiche, toutes parts confondues
# (base, équipement, bonus) : la vie de base (stat 0, dont l'identifiant n'est donc pas écrit) et la vitalité.
LIFE_STATS = (0, 11)
SHEET_STATS, SUMMON_STATS = "1.3.4.7.4.", "12.2.1.2.4."

# Champ 22 d'une action : ce qu'elle fait.
CAST, DEATH, SUMMON, INVISIBLE = 300, 103, 181, 150
POISON = 3793  # un poison ou un effet posé plus tôt se déclenche ; la ligne de dégâts suit
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


@dataclass
class Event:
    when: float
    kind: str  # start, end, turn, sheet, life, cast, trigger, damage, heal, shield, death, summon, push, invisible, spent
    source: int | None = None
    target: int | None = None
    amount: int | None = None
    element: str = ""
    spell: int | None = None
    cell: int | None = None
    round: int | None = None
    absorbed: int = 0  # part d'un coup prise par le bouclier


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
    # Numéro de case Dofus (0 à 559, voir fight.grid) : placement, invocation et poussées. Les déplacements
    # à pied ne sont pas suivis ici : leur chemin est une suite d'octets, que l'extrait ne montre pas.
    cell: int | None = None
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
    ended: bool = False

    def add(self, item: Event) -> None:
        self.events.append(item)
        if item.kind in ("sheet", "summon") and item.target is not None:
            tally = self.fighters[item.target]
            tally.max_life = tally.start_life = item.amount
            tally.cell = item.cell
            if item.kind == "sheet":
                tally.monster = item.spell
        if item.kind == "turn" and item.round:
            self.rounds = max(self.rounds, item.round)
        elif item.kind == "damage" and item.target is not None:
            self.fighters[item.target].taken += item.amount
            self.fighters[item.target].absorbed += item.absorbed
            if item.source is not None:
                self.fighters[item.source].dealt += item.amount
        elif item.kind == "push" and item.target is not None:
            self.fighters[item.target].cell = item.cell
        elif item.kind == "shield" and item.target is not None:
            self.fighters[item.target].shield = item.amount
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
    sheets: dict[int, Event] = {}  # fiches reçues pendant le placement, avant le début
    my_life: int | None = None
    for when, direction, key, fields in messages:
        item = event(when, direction, key, fields)
        if item is None:
            continue
        if item.kind == "life":
            my_life = item.amount
            continue
        if current is None and item.kind == "sheet" and item.target is not None:
            sheets[item.target] = item
        if item.kind == "start":
            current = Fight()
            out.append(current)
            for sheet in sheets.values():
                current.add(sheet)
                if sheet.target > 0 and my_life is not None and my_life <= (sheet.amount or 0):
                    current.fighters[sheet.target].start_life = my_life
            sheets = {}
            current.events.append(item)
            continue
        if current is not None:
            current.add(item)
            if item.kind == "end":
                current, sheets = None, {}
    return out


def read_extract(text: str) -> list[Fight]:
    """Les combats d'un extrait écrit par `tools.dump_window`."""
    return fights(parsed for parsed in map(parse_line, text.splitlines()) if parsed)
