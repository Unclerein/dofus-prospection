"""Expérience de métier : XP d'un craft, et chemin le moins coûteux d'un niveau à un autre.

Règles reprises du simulateur « XP Métier » de DofusDB (dofusdb.fr/fr/tools/jobs-xp), que le
joueur a confirmées. Le calcul réel est fait par le serveur : ceci en est une reproduction.

  base = 20 x niveau de la recette / ((niveau du métier - niveau de la recette)^1,1 / 10 + 1)
  XP   = partie entière de (base x coefficient de l'objet), puis x bonus d'XP

Plus de 100 niveaux d'écart : le craft ne rapporte plus rien. Le coefficient de l'objet (ou de
son type) vient des données du jeu ; 100 % par défaut.
"""
import math
from dataclasses import dataclass, field

MAX_LEVEL = 200
# Au-delà de cet écart entre le niveau du métier et celui de la recette, l'XP est nulle.
MAX_LEVEL_GAP = 100


def craft_xp(recipe_level: int, job_level: int, ratio_pct: int = 100, bonus_pct: float = 100.0) -> int:
    """XP d'un craft pour un métier de niveau job_level. 0 si la recette est hors de portée ou trop facile."""
    if job_level < recipe_level or job_level - MAX_LEVEL_GAP > recipe_level:
        return 0
    base = 20 * recipe_level / ((job_level - recipe_level) ** 1.1 / 10 + 1)
    return math.floor(math.floor(base * ratio_pct / 100) * bonus_pct / 100)


def level_to_xp(level: int) -> int:
    """XP totale nécessaire pour atteindre un niveau de métier."""
    return level * (level - 1) * 10


def xp_to_level(xp: float) -> int:
    return max(1, min(MAX_LEVEL, math.floor((math.sqrt(1 + 0.4 * max(0.0, xp)) + 1) / 2)))


@dataclass(frozen=True, slots=True)
class Candidate:
    """Une recette utilisable pour monter le métier."""

    item_id: int
    level: int
    ratio_pct: int
    cost: float  # coût d'un craft retenu pour le calcul (ingrédients, moins la revente si demandé)


@dataclass(slots=True)
class Step:
    item_id: int
    from_level: int
    to_level: int  # niveau atteint à la fin de l'étape
    crafts: int = 0
    xp: int = 0
    cost: float = 0.0


@dataclass(slots=True)
class Plan:
    steps: list[Step] = field(default_factory=list)
    start_xp: int = 0
    end_xp: int = 0
    target_level: int = 1
    blocked_at: int | None = None  # niveau où plus aucune recette chiffrable ne rapporte d'XP

    @property
    def cost(self) -> float:
        return sum(step.cost for step in self.steps)

    @property
    def crafts(self) -> int:
        return sum(step.crafts for step in self.steps)

    @property
    def reached(self) -> bool:
        return self.blocked_at is None


def cheapest_path(candidates: list[Candidate], start_xp: int, target_level: int, bonus_pct: float = 100.0) -> Plan:
    """Chemin le moins coûteux, niveau par niveau.

    À chaque niveau, on prend la recette au meilleur coût par point d'XP et on en fabrique
    juste assez pour passer au niveau suivant ; l'XP en trop est reportée. L'XP d'une recette
    ne changeant qu'avec le niveau du métier, ce choix niveau par niveau est le bon à
    l'arrondi d'un craft près.
    """
    target_level = max(1, min(MAX_LEVEL, target_level))
    xp = max(0, int(start_xp))
    plan = Plan(start_xp=xp, end_xp=xp, target_level=target_level)
    goal = level_to_xp(target_level)
    while xp < goal:
        level = xp_to_level(xp)
        best: tuple[float, int, Candidate] | None = None
        for candidate in candidates:
            gained = craft_xp(candidate.level, level, candidate.ratio_pct, bonus_pct)
            if gained <= 0:
                continue
            # Un craft qui rapporte plus qu'il ne coûte (revente déduite) compte comme gratuit : le but est
            # de monter le métier, pas de multiplier les crafts les plus lucratifs.
            rate = max(0.0, candidate.cost) / gained
            # À coût par XP égal, la recette qui rapporte le plus d'XP : moins de crafts à faire.
            if best is None or (rate, -gained) < (best[0], -best[1]):
                best = (rate, gained, candidate)
        if best is None:
            plan.blocked_at = level
            break
        _, gained, candidate = best
        needed = min(goal, level_to_xp(level + 1)) - xp
        crafts = max(1, math.ceil(needed / gained))
        xp += crafts * gained
        reached = xp_to_level(xp)
        if plan.steps and plan.steps[-1].item_id == candidate.item_id:
            step = plan.steps[-1]
        else:
            step = Step(candidate.item_id, level, reached)
            plan.steps.append(step)
        step.to_level = reached
        step.crafts += crafts
        step.xp += crafts * gained
        step.cost += crafts * candidate.cost
    plan.end_xp = xp
    return plan
