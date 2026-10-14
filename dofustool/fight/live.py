"""Le combat en cours, lu pendant qu'il se joue, pour l'afficher par-dessus le jeu.

La capture donne chaque message à un `Tracker`, qui tient l'état du combat et l'écrit dans un petit
fichier local (data/fight.json) que la fenêtre d'affichage relit. Rien n'est envoyé nulle part, et
le fichier ne nomme personne : les autres joueurs y sont de simples « joueur », sans identifiant.

On n'y met que ce que le serveur a dit au client : un ennemi invisible n'y a pas de case.
"""
import json
import os
from pathlib import Path

from ..tools.dump_window import describe
from . import journal

FIGHT_PATH = Path(__file__).resolve().parents[2] / "data" / "fight.json"

MAP_CHANGE = "jow"  # le joueur arrive sur une carte ; à l'entrée en combat, celle du combat
EFFECT = "juh"  # un effet est posé sur un combattant (1.2 : code, 1.1.8 : cible, 1.1.10 : valeur, 1.1.16 : sort)
WATCHED = frozenset({
    journal.FIGHT_START, journal.FIGHT_END, journal.TURN, journal.ACTION, journal.SHEET, journal.MY_LIFE,
    journal.STAT, journal.MOVE, journal.START_CELLS, journal.ORDER, journal.PLACEMENT, MAP_CHANGE, EFFECT,
})  # fmt: skip
# Confusion (combat du Comte Harebourg) : code d'effet -> degrés par unité, horaire positif.
# 770 à 772 : horaire en degrés, en quarts de tour, en huitièmes de tour ; 773 à 775 : de même, contre horaire.
CONFUSION = {770: 1, 771: 90, 772: 45, 773: -1, 774: -90, 775: -45}
COMTOISE = 3646  # sort de début de tour qui fixe la confusion ; les autres (Pendultimatum) s'y ajoutent
ORDER_WINDOW_S = 5.0  # un sort annoncé par le serveur plus tard que ça après ma demande n'est pas le mien


def flat(body: bytes) -> journal.Fields:
    """Les champs d'un message sous la forme que lit le journal (« 37.6 », « 49 »)."""
    return [tuple(pair.split("=", 1)) for pair in describe(body) or [] if "=" in pair]


class Tracker:
    def __init__(self) -> None:
        self.reader = journal.Reader()
        self.me: int | None = None  # mon combattant
        self.dirty = False  # l'état a changé depuis le dernier `state`
        self._ordered: tuple[int, float] | None = None  # sort que je viens de demander, et quand
        self.map_id: int | None = None  # carte où se trouve le joueur, donc celle du combat en cours
        self.confusion: int | None = None  # ma confusion en degrés horaires (0, 90, 180, 270), si j'en ai une

    def feed(self, when: float, direction: str, key: str, body: bytes, me: int | None = None) -> bool:
        """Prend un message de la capture ; vrai s'il concernait le combat."""
        if key not in WATCHED:
            return False
        self.feed_fields(when, direction, key, flat(body), me)
        return True

    def feed_fields(self, when: float, direction: str, key: str, fields: journal.Fields, me: int | None = None) -> None:
        if me is not None:
            self.me = me
        if direction == "c2s":
            if key == journal.ORDER and (spell := journal.number(fields, "1")) is not None:
                self._ordered = (spell, when)
            return
        if key == MAP_CHANGE:
            self.map_id = journal.number(fields, "1")
            return
        if key == EFFECT:
            self._effect(fields)
            return
        fighting = self.reader.current is not None
        item = self.reader.feed(when, direction, key, fields)
        if item is None:
            return
        if item.kind == "start":
            self.confusion = None
        elif item.kind == "cast":
            self._cast(item, when)
        if fighting or self.reader.current is not None:
            self.dirty = True

    def _cast(self, item: journal.Event, when: float) -> None:
        """À défaut de connaître mon personnage : c'est lui qui lance le sort que je viens de demander."""
        ordered = self._ordered
        if ordered is None or item.spell != ordered[0] or when - ordered[1] > ORDER_WINDOW_S:
            return
        self._ordered = None
        if self.me is None:
            self.me = item.source

    def _effect(self, fields: journal.Fields) -> None:
        """Une confusion posée sur moi : celle de début de tour remplace, les suivantes s'ajoutent."""
        unit = CONFUSION.get(journal.number(fields, "1.2"))
        if unit is None or self.reader.current is None or journal.number(fields, "1.1.8") != self.me:
            return
        degrees = unit * (journal.number(fields, "1.1.10") or 0)
        base = 0 if journal.number(fields, "1.1.16") == COMTOISE or self.confusion is None else self.confusion
        self.confusion = (base + degrees) % 360
        self.dirty = True

    def state(self) -> dict:
        """Ce que la fenêtre d'affichage a besoin de savoir ; remet `dirty` à zéro."""
        self.dirty = False
        fight = self.reader.current
        if fight is None:
            return {"active": False}
        fighters = []
        for fighter_id, tally in fight.fighters.items():
            kind = "moi" if fighter_id == self.me else "invocation" if tally.summoner is not None else "monstre" if fighter_id < 0 else "joueur"
            fighters.append({
                "kind": kind, "monster": tally.monster, "life": tally.life, "max_life": tally.max_life,
                "shield": tally.shield, "dead": tally.dead, "cell": tally.cell,
                "mine": tally.summoner is not None and tally.summoner == self.me,
            })  # fmt: skip
        return {
            "active": True, "round": fight.rounds, "fighters": fighters,
            "map": self.map_id, "confusion": self.confusion,
        }  # fmt: skip


def write(path: Path, payload: dict) -> None:
    """Écrit d'un coup : la fenêtre d'affichage ne lit jamais un fichier à moitié écrit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".tmp")
    partial.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(partial, path)


def read(path: Path = FIGHT_PATH) -> dict:
    """L'état écrit par la capture ; « pas de combat » si le fichier manque ou se lit mal."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"active": False}
    except (OSError, ValueError):
        return {"active": False}
