"""Expérience des métiers de forgemagie, et chemin le moins coûteux d'un niveau à un autre.

Formule mesurée sur l'archive du joueur (3 381 passages sur 3 382 prédits au point près, métier 200) :

  XP = partie entière de (poids de la rune x niveau de l'objet / ((niveau du métier - niveau de l'objet)^1,1 + 10))

avec un minimum de 1. C'est la formule de l'XP d'un craft multipliée par poids / 200. Le poids d'une
rune est la densité de sa caractéristique (données du jeu) multipliée par ce qu'elle ajoute. L'XP
n'est donnée que si la rune passe, et un mage ne peut pas travailler un objet d'un niveau supérieur
à celui de son métier.

Ce qui n'est pas calculé mais mesuré, et donc réglable : la part des runes qui passent. Sur une ligne
sous son maximum, 77 % passent (quel que soit leur poids) ; au maximum, un quart seulement. Une rune
qui passe en cassant des jets, comme une rune qui échoue, fait perdre ailleurs environ son propre
poids : l'objet redescend tout seul. On monte donc un métier en alternant deux runes sur deux lignes
d'un même objet, sans rune réservée à « casser ». Autant de poids passe sur chaque ligne ; le coût du
point d'XP est celui des deux runes, pondéré ainsi.

Non vérifié : un métier sous le niveau 200 (aucun passage dans l'archive), et la tenue des 77 % quand
on martèle les deux mêmes lignes pendant des centaines de runes.
"""
import math
from dataclasses import dataclass, field

from .jobxp import MAX_LEVEL, level_to_xp, xp_to_level

PASS_RATE = 0.77  # part des runes qui passent, ligne sous son maximum (mesurée sur 4 342 passages)
# Objet à une seule ligne utilisable : rien à alterner, la ligne bute sur son maximum (un quart de réussite
# mesuré là) jusqu'à ce qu'un échec la fasse redescendre. Hypothèse : moitié moins de runes qui passent.
SINGLE_LINE = 0.5


def mage_xp(weight: float, item_level: int, job_level: int) -> int:
    """XP d'une rune qui passe. 0 si l'objet est hors de portée du métier."""
    if item_level > job_level or item_level <= 0 or weight <= 0:
        return 0
    return max(1, math.floor(weight * item_level / ((job_level - item_level) ** 1.1 + 10) + 1e-9))


@dataclass(frozen=True, slots=True)
class Rune:
    item_id: int
    effect_id: int  # caractéristique qu'elle ajoute
    weight: float  # densité x valeur
    price: float


@dataclass(frozen=True, slots=True)
class Support:
    """Un équipement sur lequel passer des runes."""

    item_id: int
    level: int
    lines: frozenset[int]  # caractéristiques de base positives : les lignes où une rune passe sans être un exo
    price: float


@dataclass(slots=True)
class Step:
    item_id: int  # objet support
    from_level: int
    to_level: int
    runes: dict[int, int] = field(default_factory=dict)  # rune -> nombre à passer, échecs compris
    single: bool = False  # objet à une seule ligne : réussite comptée à moitié
    xp: int = 0
    cost: float = 0.0  # runes seulement
    bought: bool = False  # l'objet support est acheté à cette étape


@dataclass(slots=True)
class Plan:
    steps: list[Step] = field(default_factory=list)
    start_xp: int = 0
    end_xp: int = 0
    target_level: int = 1
    blocked_at: int | None = None  # niveau où aucun objet support connu ne convient
    pass_rate: float = PASS_RATE

    @property
    def rune_cost(self) -> float:
        return sum(step.cost for step in self.steps)

    @property
    def passes(self) -> int:
        return sum(sum(step.runes.values()) for step in self.steps)


def best_pair(support: Support, runes: list[Rune], job_level: int) -> tuple[float, tuple[Rune, ...], float] | None:
    """(kamas par point d'XP si tout passait, les runes à alterner, XP par unité de poids passée sur chaque ligne).

    Pour chaque ligne de l'objet, la rune la moins chère au point d'XP (à coût égal, la plus lourde :
    moins de passages). Puis les deux lignes les moins chères. Une seule rune si l'objet n'a qu'une ligne
    où en passer une (voir SINGLE_LINE). None s'il n'en a aucune.
    """
    by_line: dict[int, tuple[float, float, Rune, int]] = {}
    for rune in runes:
        if rune.effect_id not in support.lines:
            continue
        gained = mage_xp(rune.weight, support.level, job_level)
        if gained <= 0:
            continue
        key = (rune.price / gained, -rune.weight, rune, gained)
        if rune.effect_id not in by_line or key[:2] < by_line[rune.effect_id][:2]:
            by_line[rune.effect_id] = key
    if not by_line:
        return None
    if len(by_line) == 1:
        (_, _, only, gained), = by_line.values()
        return only.price / gained, (only,), gained / only.weight
    (_, _, first, xp_a), (_, _, second, xp_b) = sorted(by_line.values(), key=lambda entry: entry[:2])[:2]
    # Autant de poids passe sur chaque ligne : pour 1 de poids sur chacune, voici le coût et l'XP.
    cost = first.price / first.weight + second.price / second.weight
    gained = xp_a / first.weight + xp_b / second.weight
    return cost / gained, (first, second), gained


def cheapest_path(
    supports: list[Support], runes: list[Rune], start_xp: int, target_level: int, pass_rate: float = PASS_RATE
) -> Plan:
    """Chemin le moins coûteux sur tout le trajet, prix des objets supports compris.

    Niveau par niveau, chaque objet a un coût au point d'XP (ses deux meilleures runes). Le meilleur
    enchaînement d'objets se calcule de proche en proche : à chaque niveau, garder l'objet en main ou en
    acheter un autre. On ne change donc d'objet que si le gain rembourse son prix d'ici la fin. Racheter
    un objet déjà utilisé plus tôt est compté comme un nouvel achat : cela n'arrive presque jamais.
    Les nombres de runes comptent les échecs (pass_rate) ; l'XP est une espérance, pas un tirage.
    """
    pass_rate = min(1.0, max(0.05, pass_rate))
    target_level = max(1, min(MAX_LEVEL, target_level))
    xp = max(0, int(start_xp))
    plan = Plan(start_xp=xp, end_xp=xp, target_level=target_level, pass_rate=pass_rate)
    goal = level_to_xp(target_level)
    if xp >= goal:
        return plan
    first = xp_to_level(xp)
    levels = list(range(first, target_level))
    needed = {level: min(goal, level_to_xp(level + 1)) - max(xp, level_to_xp(level)) for level in levels}
    # Les runes utiles à chaque objet, une fois pour toutes.
    fitting = {s.item_id: [rune for rune in runes if rune.effect_id in s.lines] for s in supports}
    by_id = {s.item_id: s for s in supports}

    # Coût de chaque niveau avec chaque objet à sa portée : (kamas, les deux runes, XP par unité de poids).
    options: dict[int, dict[int, tuple[float, tuple[Rune, ...], float]]] = {}
    for level in levels:
        found = {}
        for support in supports:
            if support.level > level or not fitting[support.item_id]:
                continue
            pair = best_pair(support, fitting[support.item_id], level)
            if pair is not None:
                rate, chosen, gained = pair
                passing = pass_rate * (SINGLE_LINE if len(chosen) == 1 else 1.0)
                found[support.item_id] = (rate / passing * needed[level], chosen, gained * passing)
        options[level] = found

    # De proche en proche : coût minimal pour finir chaque niveau avec tel objet en main.
    INF = float("inf")
    best: dict[int, float] = {}
    came: dict[int, dict[int, int | None]] = {}
    reached = first
    for level in levels:
        if not options[level]:
            break
        cheapest_before = min(best.values(), default=0.0)
        holder = min(best, key=best.get) if best else None
        current: dict[int, float] = {}
        came[level] = {}
        for item_id, (cost, _, _) in options[level].items():
            stay = best.get(item_id, INF)
            switch = cheapest_before + by_id[item_id].price
            # À coût égal, on garde l'objet en main.
            if stay <= switch:
                current[item_id], came[level][item_id] = stay + cost, item_id
            else:
                current[item_id], came[level][item_id] = switch + cost, holder
        best = current
        reached = level + 1
    if reached == first:
        plan.blocked_at = first
        return plan
    if reached < target_level:
        plan.blocked_at = reached

    # Remonter le chemin, puis l'écrire étape par étape.
    chosen: dict[int, int] = {}
    item_id = min(best, key=lambda i: (best[i], -by_id[i].level))
    for level in range(reached - 1, first - 1, -1):
        chosen[level] = item_id
        item_id = came[level][item_id] if came[level][item_id] is not None else item_id
    owned: set[int] = set()
    for level in range(first, reached):
        support = by_id[chosen[level]]
        _, pair, gained = options[level][support.item_id]
        weight = needed[level] / gained  # poids à tenter sur chaque ligne, échecs compris (gained en tient compte)
        if plan.steps and plan.steps[-1].item_id == support.item_id:
            step = plan.steps[-1]
        else:
            step = Step(support.item_id, level, level, bought=support.item_id not in owned)
            plan.steps.append(step)
        owned.add(support.item_id)
        step.single = step.single or len(pair) == 1
        for rune in pair:
            count = max(1, math.ceil(weight / rune.weight))
            step.runes[rune.item_id] = step.runes.get(rune.item_id, 0) + count
            step.cost += count * rune.price
        xp += needed[level]
        step.xp += needed[level]
        step.to_level = xp_to_level(xp)
    plan.end_xp = xp
    return plan
