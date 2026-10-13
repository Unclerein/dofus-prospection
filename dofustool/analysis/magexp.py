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
# Une seule ligne travaillée : rien à alterner, seuls ses propres échecs la font redescendre, donc elle reste
# plus souvent collée à son maximum que ne le suppose line_rate. Hypothèse, non mesurée : un quart de réussite
# en moins. Un objet à deux lignes peut aussi être travaillé ainsi, quand sa seconde ligne coûte trop cher.
SINGLE_LINE = 0.75
# Chance de passer selon la taille de la rune et le poids déjà présent sur la ligne (valeur x densité),
# mesurée sur l'archive, ligne sous son maximum. Une rune simple passe très bien sur une ligne peu chargée
# et presque plus au-delà de 100 de poids ; une Pa tient mieux ; une Ra reste stable. C'est ce qui fait
# préférer les Ra sur les grosses lignes. (poids de la ligne jusqu'à, chance de passer)
SIMPLE, PA, RA = 0, 1, 2
LINE_RATES = {
    SIMPLE: ((30, 0.90), (45, 0.82), (60, 0.69), (80, 0.65), (100, 0.48), (math.inf, 0.17)),
    PA: ((45, 0.82), (100, 0.70), (math.inf, 0.65)),
    RA: ((80, 0.71), (math.inf, 0.78)),
}
# Une rune qui ne tient pas sous le maximum de sa ligne : 31 % (234 passages). Une Ra (+10) sur une ligne dont
# le maximum est 10 ne tient que si la ligne est à zéro, d'où l'avantage des Pa sur les petites lignes.
OVER_RATE = 0.31
# Deux façons de choisir. Au moins cher : seuls les kamas comptent. Moins de runes : chaque rune passée coûte
# en plus CLICK_COST kamas de temps (à 1,5 s le passage, 100 kamas valent le temps à 240 000 kamas de l'heure) ;
# runes lourdes et objets de plus haut niveau deviennent alors préférables dès qu'ils épargnent des passages.
CHEAPEST = 0.0
CLICK_COST = 100.0
SAMPLES = 24


def line_rate(tier: int, rune_weight: float, line_weight: float) -> float:
    """Chance moyenne qu'une rune passe sur une ligne dont le maximum pèse line_weight.

    Hypothèse : en montant un métier, la ligne se promène entre zéro et son maximum. À chaque position,
    la rune tient sous le maximum (chance selon sa taille et le poids déjà là) ou non (OVER_RATE).
    """
    if line_weight <= 0:
        return OVER_RATE
    total = 0.0
    for index in range(SAMPLES):
        at = line_weight * (index + 0.5) / SAMPLES
        if at + rune_weight > line_weight:
            total += OVER_RATE
        else:
            total += next(rate for limit, rate in LINE_RATES[tier] if at <= limit)
    return total / SAMPLES


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
    tier: int = SIMPLE  # SIMPLE, PA ou RA


@dataclass(frozen=True, slots=True)
class Support:
    """Un équipement sur lequel passer des runes."""

    item_id: int
    level: int
    # Caractéristiques de base positives, avec le poids de leur maximum (valeur x densité) : les lignes où une
    # rune passe sans être un exo. Un simple ensemble d'identifiants est accepté (maximum inconnu : toutes les
    # runes y passent alors aussi bien).
    lines: dict[int, float] | frozenset[int]

    def line_max(self, effect_id: int) -> float | None:
        return self.lines.get(effect_id) if isinstance(self.lines, dict) else None
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


def best_pair(
    support: Support, runes: list[Rune], job_level: int, click: float = CHEAPEST
) -> tuple[float, tuple[Rune, ...], float] | None:
    """(coût par point d'XP si la réussite était ordinaire, les runes à passer, XP par unité de poids tentée).

    Pour chaque ligne de l'objet, la rune la moins chère au point d'XP espéré : sa chance de passer dépend
    de sa taille et du poids de la ligne (line_rate), rapportée à la réussite ordinaire PASS_RATE. `click`
    s'ajoute au prix de chaque rune (voir CLICK_COST) ; à coût égal, la plus lourde. Puis on alterne les
    deux lignes les moins chères, ou on ne travaille que la meilleure si c'est moins cher malgré SINGLE_LINE.
    None si aucune rune chiffrée ne va sur l'objet.
    """
    by_line: dict[int, tuple[float, float, Rune, float]] = {}
    for rune in runes:
        if rune.effect_id not in support.lines:
            continue
        gained = mage_xp(rune.weight, support.level, job_level)
        if gained <= 0:
            continue
        line_max = support.line_max(rune.effect_id)
        if line_max:
            gained = gained * line_rate(rune.tier, rune.weight, line_max) / PASS_RATE
        key = ((rune.price + click) / gained, -rune.weight, rune, gained)
        if rune.effect_id not in by_line or key[:2] < by_line[rune.effect_id][:2]:
            by_line[rune.effect_id] = key
    if not by_line:
        return None
    ranked = sorted(by_line.values(), key=lambda entry: entry[:2])
    rate, _, first, xp_a = ranked[0]
    alone = (rate / SINGLE_LINE, (first,), xp_a / first.weight * SINGLE_LINE)
    if len(ranked) == 1:
        return alone
    _, _, second, xp_b = ranked[1]
    # Autant de poids tenté sur chaque ligne : pour 1 de poids sur chacune, voici le coût et l'XP.
    cost = (first.price + click) / first.weight + (second.price + click) / second.weight
    gained = xp_a / first.weight + xp_b / second.weight
    return alone if alone[0] < cost / gained else (cost / gained, (first, second), gained)


def cheapest_path(
    supports: list[Support], runes: list[Rune], start_xp: int, target_level: int, pass_rate: float = PASS_RATE,
    click: float = CHEAPEST,
) -> Plan:  # fmt: skip
    """Chemin le moins coûteux sur tout le trajet, prix des objets supports compris.

    Niveau par niveau, chaque objet a un coût au point d'XP (ses meilleures runes ; `click` y ajoute le
    temps de chaque passage, sans entrer dans les kamas affichés). Le meilleur
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
            pair = best_pair(support, fitting[support.item_id], level, click)
            if pair is not None:
                rate, chosen, gained = pair
                found[support.item_id] = (rate / pass_rate * needed[level], chosen, gained * pass_rate)
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
