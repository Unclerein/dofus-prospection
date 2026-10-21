"""Collecte de forgemagie : où en est le groupe, ce qui rapporte, les missions et les règles connues.

Le but : retrouver par la mesure comment le jeu tire le résultat d'un passage, pour un simulateur de
forgemagie calé sur de vrais passages. Ankama n'a jamais publié la formule ; il faut assez de passages
dans chaque situation (ligne basse ou haute, objet léger ou lourd, avec ou sans puits, over, exo…).

Les passages sont rangés dans des cases. Chaque case a un objectif ; la progression ne compte pas
au-delà, pour que mille passages de plus au même endroit ne fassent pas croire que tout avance.
Un passage rapporte d'autant plus de points que sa case est vide : 10 dans une case vide, 1 dans une
case pleine. Les missions sont personnelles : elles poussent vers ce qui manque, et rapportent un bonus.

Tout se calcule à partir des passages du groupe (les siens, et ceux reçus par le hub).
"""
import json
import math
import os
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..share import CHARACTER
from . import fmdata

PURPOSE = (
    "Ankama n'a jamais publié comment le jeu décide du résultat d'une rune. On rassemble les passages de tout "
    "le groupe pour retrouver les vraies chances par la mesure, puis construire un simulateur de forgemagie "
    "calé sur de vrais passages : savoir à l'avance ce que coûte un over, quand tenter un exo, quelle méthode "
    "est la meilleure après la perte d'un PA."
)

CRIT = 115  # % Critique
CHEAP = 10_000  # au-delà, un objet n'est proposé qu'après les moins chers
LIGHT = 150  # poids max sous lequel un objet est « léger »
TYPES = ("Amulette", "Anneau", "Bottes", "Ceinture", "Chapeau", "Cape", "Bouclier", "Sac à dos")

# --- les cases ------------------------------------------------------------------

LINE_BINS = ((0.2, "0–20 %"), (0.4, "20–40 %"), (0.6, "40–60 %"), (0.8, "60–80 %"), (math.inf, "80–100 %"))
ITEM_BINS = ((0.6, "moins de 60 %"), (0.8, "60–80 %"), (0.95, "80–95 %"), (math.inf, "95 % et plus"))
EXTRA_BINS = ((10, "0–10"), (25, "10–25"), (50, "25–50"), (math.inf, "50–101"))
POOL_LEVELS = ("sans puits", "petit puits", "gros puits")  # 0, moins de 20, 20 et plus

NORMAL_TARGET, EXTRA_TARGET, EDGE_TARGET, LIGHT_TARGET = 600, 700, 2000, 8000
GAP_TARGET = 1000  # zones où la formule reste floue
REACH_LOW, REACH_HIGH = 15, 30  # ligne entre 15 et 30 fois le bonus de la rune : la portée de la rune
LOW_FILL = 0.6  # objet rempli à moins de 60 % de son poids max
HIGH_LEVEL, LOW_LEVEL = 150, 100
RANK_TARGET, LOADED_TARGET = 600, 300
LOADED = 8  # poids over/exo porté ailleurs sur l'objet à partir duquel un premier over compte comme « chargé »
MODELS_TARGET, MODEL_PASSES = 50, 100  # 50 modèles d'objets avec au moins 100 passages chacun


@dataclass
class Block:
    id: str
    title: str
    explain: str
    targets: dict[str, int]  # case → objectif
    counts: Counter = field(default_factory=Counter)

    @property
    def target(self) -> int:
        return sum(self.targets.values())

    @property
    def filled(self) -> int:
        return sum(min(self.counts[cell], goal) for cell, goal in self.targets.items())

    def fill(self) -> float:
        return self.filled / self.target if self.target else 0.0


def blocks() -> list[Block]:
    normal = {f"n|{line}|{item}|{pool}": NORMAL_TARGET for _, line in LINE_BINS for _, item in ITEM_BINS for pool in ("sans", "avec")}
    extra = {f"o|{kind}|{band}|{pool}": EXTRA_TARGET for kind in ("over", "exo") for _, band in EXTRA_BINS for pool in POOL_LEVELS}
    return [
        Block("normal", "Lignes normales", "Runes sous le max de la ligne : la courbe critique, neutre, échec selon le remplissage de la ligne et de l'objet, avec et sans puits.", normal),
        Block("extra", "Over et exo", "Tentatives au-delà du max ou hors des lignes de base, selon le poids over/exo déjà porté par l'objet et le puits.", extra),
        Block("ranks", "Rang des overs", "Premier, deuxième, troisième point au-delà du max d'une même ligne, sur un objet sous 25 de poids over/exo ; "
              "et des premiers overs sur un objet qui porte déjà un over ou un exo ailleurs, pour savoir si c'est le rang ou ce poids qui compte.",
              {"r|1": RANK_TARGET, "r|2": RANK_TARGET, "r|3": RANK_TARGET, "r|1|chargé": LOADED_TARGET}),
        Block("edges", "Seuil du 1 %", "Tentatives sur un objet qui porte déjà 25 à 29, puis 30 ou plus, de poids over/exo : "
              "le seuil où il ne reste qu'environ 1 % de critique est-il à 25 ou à 30 ?", {"s|25": EDGE_TARGET, "s|30": EDGE_TARGET}),
        Block("light", "Objets légers", f"Objets de poids max inférieur à {LIGHT} : la formule tient-elle en bas de l'échelle ?", {"l": LIGHT_TARGET}),
        Block("gaps", "Zones floues de la formule", "Ce qui manque pour finir la formule : lignes entre 15 et 30 fois le bonus de la rune, "
              "objets haut niveau peu remplis, objets de niveau 100 ou moins.", {"z|portée": GAP_TARGET, "z|peu rempli": GAP_TARGET, "z|bas niveau": GAP_TARGET}),
    ]  # fmt: skip


def band(value: float, bins) -> str:
    return next(label for limit, label in bins if value < limit)


def pool_of(row) -> float | None:
    """Puits juste avant le passage ; s'il n'a pas été vu, celui d'après quand il n'a pas bougé."""
    if row.pool_before is not None:
        return row.pool_before
    return row.pool_after if row.pool_change == 0 else None


def cells_of(row) -> list[str]:
    """Les cases où compte un passage de rune. Aucune tant que la fiche de l'objet ou de la rune manque,
    ni pour une ligne négative (malus), étudiée à part."""
    if row.line_kind in ("?", "malus"):
        return []
    out = []
    pool = pool_of(row)
    if row.line_kind in ("over", "exo"):
        if pool is not None:
            level = POOL_LEVELS[0 if pool <= 0 else 1 if pool < 20 else 2]
            out.append(f"o|{row.line_kind}|{band(row.extra_before, EXTRA_BINS)}|{level}")
        if 25 <= row.extra_before < 30:
            out.append("s|25")
        elif 30 <= row.extra_before < 40:
            out.append("s|30")
    elif row.line_max and pool is not None and row.item_weight_max:
        line = band(row.line_before / row.line_max, LINE_BINS)
        item = band(row.item_weight_before / row.item_weight_max, ITEM_BINS)
        out.append(f"n|{line}|{item}|{'avec' if pool > 0 else 'sans'}")
    rank = over_rank(row)
    if rank is not None and rank <= 3:
        out.append(f"r|{rank}")
        if rank == 1 and LOADED <= other_extra(row) < 25:
            out.append("r|1|chargé")
    if row.item_weight_max and row.item_weight_max < LIGHT:
        out.append("l")
    if in_reach_zone(row):
        out.append("z|portée")
    if low_fill(row):
        out.append("z|peu rempli")
    if row.item_level is not None and row.item_level <= LOW_LEVEL and not _extra(row):
        out.append("z|bas niveau")
    return out


def in_reach_zone(row) -> bool:
    """Ligne normale qui vaut déjà 15 à 30 fois le bonus de la rune, et qui reste à 95 % de son max ou moins."""
    return (not _extra(row) and bool(row.line_max) and bool(row.rune_value)
            and REACH_LOW <= row.line_before / row.rune_value < REACH_HIGH and row.line_before + row.rune_value <= 0.95 * row.line_max)


def low_fill(row) -> bool:
    """Objet de niveau 150 ou plus, rempli à moins de 60 % de son poids max (objet neuf ou mal roulé)."""
    return (not _extra(row) and (row.item_level or 0) >= HIGH_LEVEL and bool(row.item_weight_max)
            and row.item_weight_before / row.item_weight_max < LOW_FILL)


def over_rank(row) -> int | None:
    """Rang de l'over tenté sur la ligne visée : 1 si elle est au max, 2 si elle le dépasse déjà d'une rune…
    None si ce n'est pas un over, ou si l'objet porte déjà 25 de poids over/exo (là, c'est la règle du 1 %)."""
    if row.line_max is None or row.line_before < row.line_max or not row.rune_value or row.extra_before >= 25:
        return None
    return (row.line_before - row.line_max) // row.rune_value + 1


def other_extra(row) -> float:
    """Poids over/exo que l'objet porte ailleurs que sur la ligne visée."""
    if not row.line_max or not row.rune_value or row.rune_weight is None:
        return row.extra_before
    return row.extra_before - max(0, row.line_before - row.line_max) * row.rune_weight / row.rune_value


def value(count: int, goal: int) -> float:
    """Points d'un passage dans une case : 10 si elle est vide, 1 quand elle est pleine."""
    return 1 + 9 * max(0.0, 1 - count / goal)


# --- les passages --------------------------------------------------------------


@dataclass
class Tool:
    """Un passage qui n'est pas une rune de forgemagie (orbe, potion…), pour les points."""

    ts: float
    source: str
    kind: str
    item_id: int
    character_id: int | None


def tools(conn: sqlite3.Connection) -> list[Tool]:
    out = [Tool(ts, "moi", kind, item, character) for ts, kind, item, character in conn.execute(
        "SELECT ts, kind, item_id, character_id FROM fm_passes WHERE kind != 'rune' AND after IS NOT NULL")]  # fmt: skip
    out += [Tool(at, "partage", kind, item, None) for at, kind, item in conn.execute("SELECT at, kind, item_id FROM shared_fm_passes WHERE kind != 'rune'")]
    return out


# --- les missions ----------------------------------------------------------------


@dataclass
class Mission:
    id: str
    title: str
    text: str
    target: int
    bonus: int
    rule: str | None  # règle qu'elle aide à trancher
    counts: Callable  # (passage de rune) → clé de compteur, ou None s'il ne compte pas
    items: Callable  # (fiche d'objet) → l'objet convient
    cells: Callable  # (case) → la case est visée par la mission
    hint: str = ""
    parts: tuple[str, ...] = ("",)  # sous-objectifs, chacun à `target` (ex. 4 % et 7 % de critique)


def _extra(row) -> bool:
    return row.line_kind in ("over", "exo")


# Dans l'ordre de priorité : les premières sont celles qui manquent le plus pour finir la formule.
MISSIONS = [
    Mission("portee", "Au bout de la portée", "2 000 passages sur une ligne qui vaut déjà 15 à 30 fois le bonus de la rune, sans dépasser 95 % de son max.",
            2000, 800, "taille-rune", lambda r: "" if in_reach_zone(r) else None, lambda it: it.weight < 400, lambda c: c == "z|portée",
            "Le moins cher : une petite rune (+1) sur une ligne déjà haute mais loin de son max, par exemple 20 en Force sur un objet à 40 de max."),
    Mission("mal-roule", "Objet mal roulé", "1 000 passages sur un objet de niveau 150 ou plus rempli à moins de 60 % de son poids max.",
            1000, 800, "poids-objet", lambda r: "" if low_fill(r) else None, lambda it: it.level >= HIGH_LEVEL, lambda c: c == "z|peu rempli",
            "Un objet tout juste fabriqué avec de mauvais jets fait l'affaire : forger ses lignes basses avant de le remplir."),
    Mission("premiers-overs", "Premiers overs", "100 tentatives d'over ou d'exo sur un objet qui porte moins de 25 de poids over/exo.", 100, 300, "puits-aide",
            lambda r: "" if _extra(r) and r.extra_before < 25 else None, lambda it: it.weight < 400, lambda c: c.startswith("o|") and ("|0–10|" in c or "|10–25|" in c)),
    Mission("exo-sec", "Exo à sec", "100 tentatives d'exo sans puits.", 100, 400, "puits-aide",
            lambda r: "" if r.line_kind == "exo" and pool_of(r) == 0 else None, lambda it: it.weight < 400, lambda c: c.startswith("o|exo|") and c.endswith("sans puits")),
    Mission("au-dela-25", "Au-delà de 25", "150 tentatives d'over ou d'exo sur un objet qui porte déjà 25 à 29 de poids over/exo, et 150 à 30 ou plus.", 150, 500, "seuil-25",
            lambda r: ("25 à 29" if 25 <= r.extra_before < 30 else "30 et plus" if r.extra_before >= 30 else None) if _extra(r) else None,
            lambda it: it.weight < LIGHT, lambda c: c in ("s|25", "s|30") or "|25–50|" in c or "|50–101|" in c,
            "Le moins cher : monter un over avec des runes de poids 1 (Fo, Cha) sur un objet léger, puis tenter entre 25 et 29, puis au-delà de 30.",
            ("25 à 29", "30 et plus")),
    Mission("over-charge", "Premier over, objet chargé", "50 premiers overs (ligne au max) sur un objet qui porte déjà entre 8 et 25 de poids over/exo ailleurs.",
            50, 400, "rang-over", lambda r: "" if over_rank(r) == 1 and LOADED <= other_extra(r) < 25 else None, lambda it: it.weight < 400,
            lambda c: c == "r|1|chargé", "Par exemple : un objet avec un exo ou un over déjà passé, et une autre ligne montée à son max ; on tente l'over sur celle-ci."),
    Mission("bas-niveau", "Petits objets", "300 passages de rune sur des objets de niveau 100 ou moins.", 300, 400, "poids-objet",
            lambda r: "" if r.item_level is not None and r.item_level <= LOW_LEVEL and not _extra(r) else None,
            lambda it: it.level <= LOW_LEVEL, lambda c: c == "z|bas niveau",
            "Ils échouent bien plus souvent que les objets haut niveau : il faut savoir pourquoi."),
    Mission("duel-crit", "Le duel des critiques", "50 tentatives d'over en % critique sur un objet à 4 % max, et 50 sur un objet à 7 % max.", 50, 500, "petit-max",
            lambda r: (f"{r.line_max} %" if r.rune_effect == CRIT and r.line_kind == "over" and r.line_max in (4, 7) else None),
            lambda it: it.crit in (4, 7), lambda c: c.startswith("o|over|"),
            "Paire idéale, presque le même poids total : Amulette de Kubitus (4 % max) et Cape du Boostache (7 % max).", ("4 %", "7 %")),
    Mission("sans-puits", "Sans filet", "300 passages de rune sur un objet sans puits.", 300, 300, "puits-aide",
            lambda r: "" if pool_of(r) == 0 else None, lambda it: it.weight < 400, lambda c: c.endswith("|sans") or c.endswith("|sans puits"),
            "Le plus simple : forger un objet neuf, avant qu'il n'ait généré du puits."),
    Mission("petite-grosse", "Petite contre grosse", "300 runes simples et 300 runes Ra sur des lignes déjà à 80 % de leur max ou plus.",
            300, 400, "taille-rune",
            lambda r: (r.rune_tier if r.rune_tier in ("simple", "Ra") and not _extra(r) and r.line_max and r.line_before >= 0.8 * r.line_max else None),
            lambda it: it.weight < 400, lambda c: c.startswith("n|80–100 %"),
            "Le plus parlant : sur le même objet et la même ligne, alterner une rune simple et sa Ra (Fo et Ra Fo, Ine et Ra Ine…).", ("simple", "Ra")),
    Mission("lignes-basses", "Repartir d'en bas", "300 passages sur une ligne sous 40 % de son max.", 300, 200, "neutre-50",
            lambda r: "" if not _extra(r) and r.line_max and r.line_before < 0.4 * r.line_max else None, lambda it: it.weight < 400,
            lambda c: c.startswith("n|0–20") or c.startswith("n|20–40")),
    Mission("poids-plume", "Poids plume", f"300 passages sur un objet de poids max inférieur à {LIGHT}.", 300, 300, "poids-objet",
            lambda r: "" if r.item_weight_max < LIGHT else None, lambda it: it.weight < LIGHT, lambda c: c == "l"),
    Mission("juste-milieu", "Juste milieu", "300 passages sur un objet de poids max entre 150 et 400.", 300, 300, "poids-objet",
            lambda r: "" if LIGHT <= r.item_weight_max < 400 else None, lambda it: LIGHT <= it.weight < 400, lambda c: c.startswith("n|")),
    Mission("explorateur", "Explorateur", "5 modèles d'objets que personne du groupe n'avait forgés, 50 passages sur chacun.", 5, 500, "poids-objet",
            lambda r: None, lambda it: it.group == 0 and it.weight < 400, lambda c: False),
]  # fmt: skip
EXPLORER_PASSES = 50
PRIORITY = 6  # les 6 premières missions sont marquées prioritaires


# --- les objets proposés -----------------------------------------------------------


@dataclass
class ItemInfo:
    id: int
    name: str
    type: str
    level: int
    weight: float  # poids de toutes ses lignes à leur max de base
    crit: int | None  # max de base du % critique
    price: int | None
    source: str  # « hdv » ou « craft »
    on_sale: bool  # des annonces relevées ces trois derniers jours
    group: int  # passages du groupe sur ce modèle
    rune: tuple[str, int] | None  # rune la moins chère pour l'une de ses lignes : (nom, prix)
    exo_rune: tuple[str, int] | None = None  # rune la moins chère pour une ligne qu'il n'a pas
    crit_rune: tuple[str, int] | None = None  # rune de % critique la moins chère

    def interest(self) -> float:
        return 2.0 if self.group == 0 else 1.5 if self.group < 200 else 1.0

    def order(self) -> tuple:
        # Les moins de 10 000 k d'abord ; puis le prix, allégé pour les modèles peu forgés.
        return (self.price is None, (self.price or 0) >= CHEAP, (self.price or 0) / self.interest())


def catalogue(conn: sqlite3.Connection, density: dict[int, float], group: Counter, now: float) -> list[ItemInfo]:
    snapshot = conn.execute("SELECT id FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()
    price = dict(conn.execute("SELECT item_id, price FROM avg_prices WHERE snapshot_id = ? AND price > 0", (snapshot[0],))) if snapshot else {}
    effects: dict[int, dict[int, tuple[int, int]]] = defaultdict(dict)
    for item, effect, low, high in conn.execute("SELECT item_id, effect_id, min_value, max_value FROM item_effects"):
        effects[item][effect] = (low, high)
    recipe: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for result, ingredient, quantity in conn.execute("SELECT result_id, item_id, quantity FROM recipe_ingredients"):
        recipe[result].append((ingredient, quantity))
    on_sale = {row[0] for row in conn.execute("SELECT DISTINCT item_id FROM hdv_listings WHERE captured_at > ?", (now - 3 * 86400,))}
    runes: dict[int, tuple[str, int]] = {}
    for rune_id, name in conn.execute("SELECT id, name FROM items WHERE type_name = 'Rune de forgemagie'"):
        rune = effects.get(rune_id, {})
        if len(rune) == 1 and price.get(rune_id):
            effect = next(iter(rune))
            if effect not in runes or price[rune_id] < runes[effect][1]:
                runes[effect] = (name, price[rune_id])
    marks = ",".join("?" * len(TYPES))
    out = []
    for item_id, name, type_name, level, weapon in conn.execute(
        f"SELECT id, name, type_name, level, is_weapon FROM items WHERE exchangeable = 1 AND ((is_weapon = 1 AND type_name != 'Outil') OR type_name IN ({marks}))", TYPES
    ):
        lines = {e: high for e, (_, high) in effects.get(item_id, {}).items() if high and high > 0 and density.get(e)}
        if len(lines) < 2:
            continue
        craft = None
        if recipe.get(item_id) and all(ingredient in price for ingredient, _ in recipe[item_id]):
            craft = sum(price[ingredient] * quantity for ingredient, quantity in recipe[item_id])
        hdv = price.get(item_id)
        cost, source = min(((c, s) for c, s in ((hdv, "hdv"), (craft, "craft")) if c), default=(None, ""))
        cheapest = min((runes[e] for e in lines if e in runes), key=lambda r: r[1], default=None)
        exo = min((rune for e, rune in runes.items() if e not in effects.get(item_id, {})), key=lambda r: r[1], default=None)
        out.append(ItemInfo(item_id, name, type_name, level, round(sum(h * density[e] for e, h in lines.items()), 1), lines.get(CRIT),
                            cost, source, item_id in on_sale, group[item_id], cheapest, exo, runes.get(CRIT)))  # fmt: skip
    return out


# --- les règles ----------------------------------------------------------------------

STATUSES = {"certain": "Certain", "probable": "Très probable", "verifier": "À vérifier", "refute": "Réfuté", "incertain": "Incertain, à trancher"}


@dataclass
class Rule:
    id: str
    title: str
    text: str
    source: str
    judge: Callable  # (contexte) → (statut, preuves en une phrase, nombre de cas, confiance 0–1)
    mission: str | None = None


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return 0.0, 1.0
    p = hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - spread), min(1.0, centre + spread)


def gap(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Écart entre deux taux (succès, essais), en écarts-types : au-delà de 2, il n'est pas dû au hasard."""
    (ha, na), (hb, nb) = a, b
    if not na or not nb:
        return 0.0
    p = (ha + hb) / (na + nb)
    spread = math.sqrt(p * (1 - p) * (1 / na + 1 / nb))
    return (ha / na - hb / nb) / spread if spread else 0.0


def pct(x: float) -> str:
    return f"{x * 100:.1f} %".replace(".", ",")


def _no_counterexample(support: int, against: int, needed: int, what: str, base: str = "incertain"):
    confidence = 1 - 0.99**support if not against else 0.0
    if against:
        return "refute", f"{against} contre-exemple{'s' if against > 1 else ''} sur {support + against} {what}.", support + against, 0.0
    if support >= needed:
        return "certain", f"{support} {what}, aucun contre-exemple.", support, confidence
    return base, f"{support} {what} pour l'instant, aucun contre-exemple ; il en faut {needed}.", support, confidence


def judge_pool_accounting(ctx):
    checked = good = 0
    for r in ctx.rows:
        if r.outcome == "sc" or r.pool_before is None or r.rune_weight is None:
            continue
        expected = max(0.0, r.pool_before + r.lost_weight - r.rune_weight)
        checked += 1
        good += abs(r.pool_after - expected) <= 1.0
    rate = good / checked if checked else 0.0
    status = "certain" if checked >= 1000 and rate >= 0.97 else "probable" if checked >= 200 and rate >= 0.9 else "verifier"
    return status, f"Vérifié sur {pct(rate)} de {checked} passages payés.", checked, rate


def judge_tools(ctx):
    n = len([t for t in ctx.tools if t.kind != "gemme"])
    status, text, cases, confidence = _no_counterexample(n, 0, 0, "passages d'outils (transcendance, orbe, potion, éclat, gravure)")
    return status, "Validé par le groupe. " + text, cases, 1.0 if status == "certain" else confidence


LINE_CAP = 101.0


def judge_line_cap(ctx):
    """Over passés et refusés : le poids de la ligne visée après la rune, plus les autres overs et exos de l'objet."""
    passed = []
    for r in ctx.rows:
        if r.line_max and r.line_after > r.line_max and r.rune_weight and r.rune_value:
            density = r.rune_weight / r.rune_value
            other = r.extra_before - max(0, r.line_before - r.line_max) * density
            passed.append(r.line_after * density + other)
    refused = ctx.over_refusals
    near = [w for w in passed if w >= 90]  # seuls les overs proches de la limite disent quelque chose
    against = sum(1 for w in passed if w > LINE_CAP + 0.5) + sum(1 for w in refused if w <= LINE_CAP)
    support = sum(1 for w in near if w <= LINE_CAP + 0.5) + sum(1 for w in refused if w > LINE_CAP)
    text = (f"{len(near)} overs passés près de la limite (ligne à 90 de poids ou plus), dont {sum(1 for w in near if w <= LINE_CAP + 0.5)} à 101 ou moins ; "
            f"{len(refused)} refus du jeu, dont {sum(1 for w in refused if w > LINE_CAP)} au-delà de 101.")
    if against:
        return "refute", text, support + against, 0.0
    if support >= 50:
        return "certain", text, support, 1 - 0.99**support
    if support >= 20:
        return "probable", text, support, 1 - 0.99**support
    return "verifier", text + " Il en faut au moins 20.", support, 1 - 0.99**support


def judge_cap(ctx):
    against = sum(1 for r in ctx.rows if r.extra_after > 101.5)
    support = sum(1 for r in ctx.rows if _extra(r) and r.rune_weight is not None and r.extra_before + r.rune_weight > 101 and r.extra_after <= 101.5)
    status, text, cases, confidence = _no_counterexample(support, against, 0, "tentatives qui auraient dépassé 101 sur nos passages")
    return status, "Validé par le groupe. " + text, cases, 1.0 if status == "certain" else confidence


def judge_beyond_25(ctx):
    """Réussite des tentatives over/exo selon le poids over/exo déjà porté : 20 à 24, 25 à 29, 30 et plus."""
    bands = {"20 à 24": (20, 25), "25 à 29": (25, 30), "30 et plus": (30, 1000)}
    rates = {}
    for label, (low, high) in bands.items():
        tries = [r for r in ctx.rows if _extra(r) and low <= r.extra_before < high]
        rates[label] = (sum(r.line_after > r.line_before for r in tries), len(tries))
    show = lambda x: f"{pct(x[0] / x[1])} ({x[1]})" if x[1] else "— (0)"  # noqa: E731
    text = "Tentatives passées selon le poids over/exo déjà porté : " + ", ".join(f"{label} : {show(rate)}" for label, rate in rates.items()) + "."
    middle, above = rates["25 à 29"], rates["30 et plus"]
    cases = middle[1] + above[1]
    if not cases:
        return "verifier", "Aucune tentative au-delà de 25 de poids over/exo pour l'instant.", 0, 0.0
    if above[1] >= 100 and wilson(*above)[0] > 0.05:
        return "refute", text + " Au-delà de 30, ça passe bien plus d'une fois sur cent.", cases, 0.7
    if above[1] < 300 or wilson(*above)[1] > 0.03:
        return "verifier", text + " Il faut 300 tentatives à 30 ou plus, et 100 entre 25 et 29.", cases, min(1.0, above[1] / 300) * 0.5
    if middle[1] < 100:
        return "verifier", text + " Au-delà de 30, c'est bien autour de 1 % ; reste à savoir entre 25 et 29.", cases, 0.5
    if gap(middle, above) >= 2:
        return "probable", text + " Le seuil est à 30 : entre 25 et 29, ça passe encore normalement.", cases, 0.8
    if middle[0] / middle[1] <= 0.03:
        return "probable", text + " Le seuil est à 25 : entre 25 et 29, c'est déjà autour de 1 %, comme au-delà de 30.", cases, 0.8
    return "verifier", text + " Entre 25 et 29, ni normal ni 1 % : à creuser.", cases, 0.4


def judge_neutral(ctx):
    passed = [r for r in ctx.rows if not _extra(r) and r.outcome in ("sc", "sn") and r.line_max]
    low = [r for r in passed if r.line_before < 0.6 * r.line_max]
    high = [r for r in passed if r.line_before >= 0.8 * r.line_max]
    a, b = (sum(r.outcome == "sn" for r in low), len(low)), (sum(r.outcome == "sn" for r in high), len(high))
    share = lambda x: pct(x[0] / x[1]) if x[1] else "—"  # noqa: E731
    text = f"Neutres parmi les succès : {share(a)} sur une ligne sous 60 % de son max ({a[1]}), {share(b)} au-dessus de 80 % ({b[1]})."
    if min(a[1], b[1]) < 300:
        return "verifier", text + " Il manque des passages sur des lignes basses.", a[1] + b[1], 0.3
    z = gap(a, b)
    if abs(z) < 2:
        return "probable", text, a[1] + b[1], 0.8
    return "refute", text, a[1] + b[1], min(1.0, abs(z) / 4)


def judge_pool_helps(ctx):
    tries = [r for r in ctx.rows if _extra(r) and pool_of(r) is not None and r.rune_weight is not None]
    rich = [r for r in tries if pool_of(r) >= r.rune_weight]
    dry = [r for r in tries if pool_of(r) <= 0]
    a, b = (sum(r.passed for r in rich), len(rich)), (sum(r.passed for r in dry), len(dry))
    text = (f"Avec assez de puits : {a[0]} réussites sur {a[1]} ; sans puits : {b[0]} sur {b[1]}.")
    if min(a[1], b[1]) < 100:
        return "verifier", text + " Il faut au moins 100 tentatives de chaque côté.", a[1] + b[1], 0.2
    z = gap(a, b)
    if z >= 2:
        return "probable", text, a[1] + b[1], min(1.0, z / 4)
    if z <= -2:
        return "refute", text, a[1] + b[1], min(1.0, -z / 4)
    return "verifier", text + " Pas encore d'écart net.", a[1] + b[1], 0.3


def judge_partial_pool(ctx):
    tries = [r for r in ctx.rows if _extra(r) and pool_of(r) is not None and r.rune_weight is not None]
    partial = [r for r in tries if 0 < pool_of(r) < r.rune_weight]
    dry = [r for r in tries if pool_of(r) <= 0]
    a, b = (sum(r.passed for r in partial), len(partial)), (sum(r.passed for r in dry), len(dry))
    share = lambda x: f" ({pct(x[0] / x[1])})" if x[1] else ""  # noqa: E731
    text = f"Avec un puits plus petit que la rune : {a[0]} réussites sur {a[1]}{share(a)} ; sans puits : {b[0]} sur {b[1]}{share(b)}."
    if min(a[1], b[1]) < 100:
        return "verifier", text + " Il faut au moins 100 tentatives de chaque côté.", a[1] + b[1], 0.2
    z = gap(a, b)
    if z >= 2:
        return "probable", text, a[1] + b[1], min(1.0, z / 4)
    if z <= -2:
        return "refute", text, a[1] + b[1], min(1.0, -z / 4)
    return "verifier", text + " Pas encore d'écart net.", a[1] + b[1], 0.3


def judge_over_rank(ctx):
    first = [r for r in ctx.rows if over_rank(r) == 1 and other_extra(r) < 5]
    second = [r for r in ctx.rows if over_rank(r) == 2]
    loaded = [r for r in ctx.rows if over_rank(r) == 1 and LOADED <= other_extra(r) < 25]
    rate = lambda rows: (sum(r.line_after > r.line_before for r in rows), len(rows))  # noqa: E731
    a, b, c = rate(first), rate(second), rate(loaded)
    show = lambda x: f"{pct(x[0] / x[1])} ({x[1]})" if x[1] else "— (0)"  # noqa: E731
    text = f"Passés : 1er over {show(a)}, 2e over {show(b)} ; 1er over sur un objet qui porte déjà un over ou un exo ailleurs : {show(c)}."
    if min(a[1], b[1]) < 100:
        return "verifier", text + " Il faut au moins 100 tentatives de 1er et de 2e over.", a[1] + b[1], 0.2
    drop = gap(a, b)
    if drop < 2:
        return "refute", text + " Pas d'écart net entre le 1er et le 2e over.", a[1] + b[1], 0.5
    if c[1] < 50:
        return "verifier", text + " Le 2e over passe bien moins souvent ; reste à savoir si c'est le rang ou le poids déjà porté par l'objet : il faut 50 premiers overs sur un objet chargé.", a[1] + b[1] + c[1], 0.5
    if gap(c, b) >= 2:
        return "probable", text + " Un 1er over sur un objet chargé passe bien mieux qu'un 2e over : c'est le rang qui compte.", a[1] + b[1] + c[1], min(1.0, gap(c, b) / 4)
    return "refute", text + " Un 1er over sur un objet chargé passe aussi mal qu'un 2e over : c'est le poids déjà porté qui compte, pas le rang.", a[1] + b[1] + c[1], 0.6


TIER_BANDS = ((0.8, 0.95, "arrivée entre 80 et 95 %"), (0.95, 1.05, "arrivée entre 95 et 105 %"))


def judge_rune_size(ctx):
    """Réussite des runes simples, Pa et Ra, à hauteur égale de la ligne après la rune."""
    rates: dict = {}
    for low, high, label in TIER_BANDS:
        for tier in ("simple", "Pa", "Ra"):
            rows = [r for r in ctx.rows if r.rune_tier == tier and not _extra(r) and r.line_max and r.rune_value
                    and low <= (r.line_before + r.rune_value) / r.line_max < high]  # fmt: skip
            rates[(label, tier)] = (sum(r.outcome != "ec" for r in rows), len(rows))
    show = lambda x: f"{pct(x[0] / x[1])} ({x[1]})" if x[1] else "— (0)"  # noqa: E731
    text = " ; ".join(f"{label} : simple {show(rates[(label, 'simple')])}, Pa {show(rates[(label, 'Pa')])}, Ra {show(rates[(label, 'Ra')])}" for _, _, label in TIER_BANDS) + "."
    gaps, cases = [], 0
    for _, _, label in TIER_BANDS:
        small = rates[(label, "simple")]
        for tier in ("Pa", "Ra"):
            big = rates[(label, tier)]
            cases += big[1]
            if min(small[1], big[1]) >= 200:
                gaps.append(gap(small, big))
    if len(gaps) < 4:
        return "verifier", text + " Il faut au moins 200 runes de chaque taille dans chaque tranche.", cases, 0.3
    if all(z >= 2 for z in gaps):
        return "probable", text, cases, min(1.0, min(gaps) / 4)
    if all(abs(z) < 2 for z in gaps):
        return "refute", text + " À hauteur égale de la ligne, la taille de la rune ne change rien : seule compte la hauteur où elle arrive.", cases, 0.7
    return "verifier", text + " Résultats partagés selon la taille : à creuser.", cases, 0.4


def judge_item_weight(ctx):
    normal = [r for r in ctx.rows if not _extra(r) and r.item_weight_max]
    light = [r for r in normal if r.item_weight_before / r.item_weight_max < 0.6]
    heavy = [r for r in normal if r.item_weight_before / r.item_weight_max >= 0.8]
    a, b = (sum(r.outcome == "ec" for r in heavy), len(heavy)), (sum(r.outcome == "ec" for r in light), len(light))
    text = f"Échecs sur un objet rempli à 80 % ou plus : {pct(a[0] / a[1]) if a[1] else '—'} ({a[1]}) ; à moins de 60 % : {pct(b[0] / b[1]) if b[1] else '—'} ({b[1]})."
    if min(a[1], b[1]) < 300:
        return "verifier", text + " Il manque des passages sur des objets peu remplis.", a[1] + b[1], 0.3
    z = gap(a, b)
    return ("probable" if z >= 2 else "refute" if z <= -2 else "verifier"), text, a[1] + b[1], min(1.0, abs(z) / 4)


def judge_exo_first(ctx):
    cases = [r for r in ctx.rows if r.outcome == "sn" and r.pool_before is not None and r.rune_weight is not None
             and r.pool_before >= r.rune_weight and r.has_exo]  # fmt: skip
    exo = sum(r.exo_lost for r in cases)
    if not cases:
        return "verifier", "Aucun cas pour l'instant : un succès neutre sur un objet qui a un exo et assez de puits.", 0, 0.0
    text = f"L'exo a sauté dans {exo} cas sur {len(cases)} ({pct(exo / len(cases))}) où le puits aurait suffi."
    if len(cases) >= 50:
        if exo / len(cases) >= 0.7:
            return "probable", text, len(cases), exo / len(cases)
        if exo / len(cases) <= 0.3:
            return "refute", text, len(cases), 1 - exo / len(cases)
    return "verifier", text + " Il en faut au moins 50.", len(cases), 0.3


def judge_small_max(ctx):
    tries = {4: [0, 0], 7: [0, 0]}
    for r in ctx.rows:
        if r.rune_effect == CRIT and r.line_kind == "over" and r.line_max in tries:
            tries[r.line_max][0] += r.passed
            tries[r.line_max][1] += 1
    (h4, n4), (h7, n7) = tries[4], tries[7]
    text = f"Over réussis sur un objet à 4 % max : {h4} sur {n4} ; à 7 % max : {h7} sur {n7}."
    if min(n4, n7) < 50:
        return "verifier", text + " Il faut au moins 50 tentatives de chaque côté.", n4 + n7, 0.2
    z = gap((h4, n4), (h7, n7))
    return ("probable" if z >= 2 else "refute" if z <= -2 else "verifier"), text, n4 + n7, min(1.0, abs(z) / 4)


def judge_job_level(ctx):
    return "certain", "Validé par le groupe. Nos passages ne disent pas le niveau du métier : on ne peut pas le mesurer nous-mêmes.", 0, 1.0


RULES = [
    Rule("puits-compte", "Le puits se compte au poids près", "Quand une rune passe en payant, le puits varie du poids perdu par les autres lignes, moins le poids de la rune.",
         "Mesuré sur nos passages", judge_pool_accounting),
    Rule("outils-surs", "Les outils ne ratent jamais", "Transcendances, orbes, potions, éclats et gravures passent à tous les coups.", "Communauté, validé par le groupe", judge_tools),
    Rule("ligne-101", "Une ligne ne dépasse pas 101 de poids par l'over",
         "Un over est refusé s'il porterait la ligne au-delà de 101 de poids, en comptant les autres overs et exos de l'objet. "
         "Sur une ligne qui pèse 100 à son max, un over ne passe donc que d'un point de poids.",
         "Mesuré sur nos passages (remplace « pas d'over sur une ligne qui pèse 100 ou plus »)", judge_line_cap),
    Rule("plafond-101", "Plafond de 101 de poids over et exo", "Over et exo réunis, un objet ne peut pas porter plus de 101 de poids.", "Communauté, validé par le groupe", judge_cap),
    Rule("seuil-25", "1 % de critique au-delà de 25… ou de 30 ?", "Quand l'objet porte déjà assez de poids over/exo, une nouvelle tentative ne passe qu'en critique, "
         "environ une fois sur cent. La communauté place le seuil à 25 ; Xilef le place à 30 (jusqu'à 29, les chances restent normales).",
         "Communauté et Xilef, à départager", judge_beyond_25, "au-dela-25"),
    Rule("neutre-50", "La part de neutres ne dépend pas de la ligne", "Sur une ligne normale, la part des succès qui coûtent quelque chose (lignes ou puits) est la même, ligne basse ou presque au max.",
         "Mesuré sur nos passages", judge_neutral, "lignes-basses"),
    Rule("taille-rune", "Les grosses runes passent moins bien", "À hauteur égale de la ligne après la rune, une Pa ou une Ra passe moins souvent qu'une rune simple. "
         "Sinon, c'est seulement que la grosse rune pousse la ligne plus haut.", "Théorie à vérifier", judge_rune_size, "petite-grosse"),
    Rule("rang-over", "Chaque over de plus sur la même ligne est plus dur", "Le 2e point au-delà du max d'une ligne passe bien moins souvent que le 1er, sur un objet sous 25 de poids over/exo.",
         "Théorie à vérifier", judge_over_rank, "over-charge"),
    Rule("puits-aide", "Le puits aide over et exo", "Une tentative d'over ou d'exo passe plus souvent quand l'objet a assez de puits pour la payer.", "Théorie à vérifier", judge_pool_helps, "exo-sec"),
    Rule("puits-partiel", "Même un petit puits aide", "Un puits trop petit pour payer la rune aide quand même over et exo, comparé à pas de puits du tout.",
         "Théorie à vérifier", judge_partial_pool, "exo-sec"),
    Rule("poids-objet", "Plus l'objet est chargé, plus il échoue", "Le remplissage de l'objet (son poids actuel face à son poids max) fait monter les échecs.", "Théorie à vérifier", judge_item_weight, "poids-plume"),
    Rule("exo-avant-puits", "L'exo saute avant le puits", "Sur un succès neutre, un exo est retiré avant que le puits ne paie, même quand le puits suffirait.", "Théorie à vérifier", judge_exo_first),
    Rule("petit-max", "Over plus facile sur une ligne à petit max", "Un over passe plus facilement sur un objet à 4 % de critique max que sur un objet à 7 %.", "Théorie à vérifier", judge_small_max, "duel-crit"),
    Rule("niveau-metier", "Le niveau du métier ne joue plus", "Depuis la refonte, le niveau du forgemage ne change pas les chances.", "Communauté, validé par le groupe", judge_job_level),
]  # fmt: skip


@dataclass
class Context:
    rows: list
    tools: list
    over_refusals: list[float]


def over_refusals(conn: sqlite3.Connection, density: dict[int, float]) -> list[float]:
    """Refus du jeu sur une rune qui aurait mis une ligne au-delà de son max : le poids qu'aurait eu la ligne,
    plus les autres overs et exos de l'objet."""
    effects: dict[int, dict[int, tuple[int, int]]] = defaultdict(dict)
    for item, effect, low, high in conn.execute("SELECT item_id, effect_id, min_value, max_value FROM item_effects"):
        effects[item][effect] = (low, high)
    rows = list(conn.execute("SELECT item_id, rune_id, before FROM fm_refusals WHERE before IS NOT NULL AND rune_id IS NOT NULL"))
    rows += list(conn.execute("SELECT item_id, rune_id, before FROM shared_fm_refusals WHERE before IS NOT NULL AND rune_id IS NOT NULL"))
    out = []
    for item_id, rune_id, raw in rows:
        rune = effects.get(rune_id, {})
        if len(rune) != 1:
            continue
        effect, (bonus, _) = next(iter(rune.items()))
        base = effects.get(item_id, {})
        high = base.get(effect, (None, None))[1]
        before = {e: v for e, v in json.loads(raw) if v is not None}
        value = before.get(effect, 0)
        if not high or value + bonus <= high:
            continue
        other = fmdata.extra_weight(before, base, density) - max(0, value - high) * density.get(effect, 0.0)
        out.append((value + bonus) * density.get(effect, 0.0) + other)
    return out


# --- le calcul d'ensemble ------------------------------------------------------------


def character_name(conn: sqlite3.Connection, character_id: int | None) -> str | None:
    row = conn.execute("SELECT name FROM characters WHERE id = ?", (character_id,)).fetchone() if character_id else None
    return row[0] if row else None


def main_character(conn: sqlite3.Connection) -> str | None:
    """Le personnage qui a le plus forgé, ou à défaut le dernier connecté."""
    row = conn.execute(
        "SELECT character_id FROM fm_passes WHERE character_id IS NOT NULL GROUP BY character_id ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    name = character_name(conn, row[0]) if row else None
    if name is None:
        row = conn.execute("SELECT name FROM characters ORDER BY seen_at DESC LIMIT 1").fetchone()
        name = row[0] if row else None
    return name


def kamas_spent(conn: sqlite3.Connection) -> int:
    """Ce que les runes et outils passés sur ce PC ont coûté, au prix figé du journal ou au prix moyen."""
    snapshot = conn.execute("SELECT id FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()
    price = dict(conn.execute("SELECT item_id, price FROM avg_prices WHERE snapshot_id = ?", (snapshot[0],))) if snapshot else {}
    return round(sum(fixed if fixed is not None else price.get(rune_id, 0)
                     for rune_id, fixed in conn.execute("SELECT rune_id, rune_price FROM fm_passes")))  # fmt: skip


@dataclass
class State:
    blocks: list[Block]
    models: Counter  # passages du groupe par modèle d'objet
    points: float  # mes points, bonus de missions compris
    passes: int  # mes passages (runes et outils)
    missions: list[dict]
    done: dict[str, tuple[float, int | None]]  # mission → (date, personnage)
    rows: list
    tools: list
    density: dict[int, float]


def compute(conn: sqlite3.Connection, density: dict[int, float] | None = None) -> State:
    density = density if density is not None else fmdata.densities()
    rows = fmdata.build(conn, density)
    others = tools(conn)
    events = sorted([(r.ts, 0, r) for r in rows] + [(t.ts, 1, t) for t in others], key=lambda e: (e[0], e[1]))
    parts = blocks()
    goal = {cell: goal for block in parts for cell, goal in block.targets.items()}
    owner = {cell: block for block in parts for cell in block.targets}
    models: Counter = Counter()
    first: dict[int, str] = {}  # modèle → qui l'a forgé en premier (« moi » ou « partage »)
    mine_points = 0.0
    mine_passes = 0
    progress = {m.id: Counter() for m in MISSIONS}
    done: dict[str, tuple[float, int | None]] = {}
    for ts, _, event in events:
        if isinstance(event, Tool):
            cells = []
        else:
            cells = cells_of(event)
        worth = max((value(owner[c].counts[c], goal[c]) for c in cells), default=1.0)
        for c in cells:
            owner[c].counts[c] += 1
        item_id = event.item_id
        first.setdefault(item_id, event.source)
        models[item_id] += 1
        if event.source != "moi":
            continue
        mine_points += worth
        mine_passes += 1
        for mission in MISSIONS:
            if mission.id in done:
                continue
            if mission.id == "explorateur":
                key = None
                if first[item_id] == "moi":
                    progress["explorateur"][item_id] += 1
                    if sum(1 for n in progress["explorateur"].values() if n >= EXPLORER_PASSES) >= mission.target:
                        done[mission.id] = (ts, event.character_id)
                continue
            else:
                key = mission.counts(event) if not isinstance(event, Tool) else None
            if key is None:
                continue
            progress[mission.id][key] += 1
            if all(progress[mission.id][part if mission.parts != ("",) else ""] >= mission.target for part in mission.parts):
                done[mission.id] = (ts, event.character_id)
    missions = []
    for mission in MISSIONS:
        if mission.id == "explorateur":
            count = sum(1 for n in progress["explorateur"].values() if n >= EXPLORER_PASSES)
            parts_view = [{"label": "", "count": count}]
        else:
            parts_view = [{"label": part, "count": progress[mission.id][part]} for part in mission.parts]
        aimed = [c for c in goal if mission.cells(c)]
        worth = sum(value(owner[c].counts[c], goal[c]) for c in aimed) / len(aimed) if aimed else 10.0
        missions.append({"mission": mission, "parts": parts_view, "worth": round(worth, 1)})
    bonus = sum(m.bonus for m in MISSIONS if m.id in done)
    return State(parts, models, round(mine_points + bonus, 1), mine_passes, missions, done, rows, others, density)


FOCUS_PATH = Path(__file__).resolve().parents[2] / "data" / "collecte.json"


def open_missions(state: State) -> list[dict]:
    """Les missions pas encore réussies, celles dont les passages rapportent le plus d'abord (puis le plus gros bonus)."""
    out = []
    rank = {m.id: i for i, m in enumerate(MISSIONS)}
    for entry in sorted(state.missions, key=lambda e: (rank[e["mission"].id] >= PRIORITY, -e["worth"], -e["mission"].bonus)):
        mission = entry["mission"]
        if mission.id in state.done:
            continue
        count = min(min(part["count"] for part in entry["parts"]), mission.target)
        out.append({"id": mission.id, "title": mission.title, "text": mission.text, "worth": entry["worth"], "bonus": mission.bonus,
                    "count": count, "target": mission.target})  # fmt: skip
    return out


def focus(state: State) -> dict | None:
    """La mission du moment : pas encore réussie, celle dont les passages rapportent le plus."""
    missions = open_missions(state)
    return missions[0] if missions else None


def fits(mission_id: str, effects: dict[int, int], base: dict[int, tuple[int, int]], density: dict[int, float], group: int) -> bool | None:
    """La mission va-t-elle avec l'objet posé sur l'atelier ? None : elle ne dépend pas de l'objet (puits, orbes…).

    effects : ses lignes actuelles ; base : ses bornes de base ; group : passages du groupe sur ce modèle."""
    weight = sum(high * density.get(e, 0.0) for e, (_, high) in base.items() if high and high > 0)
    extra = fmdata.extra_weight(effects, base, density)
    at_max = any(high and high > 0 and effects.get(e, 0) >= high for e, (_, high) in base.items())
    if mission_id == "poids-plume":
        return 0 < weight < LIGHT
    if mission_id == "juste-milieu":
        return LIGHT <= weight < 400
    if mission_id == "duel-crit":
        return base.get(CRIT, (None, None))[1] in (4, 7)
    if mission_id == "au-dela-25":
        return extra >= 25
    if mission_id == "over-charge":
        return LOADED <= extra < 25 and at_max
    if mission_id == "lignes-basses":
        return any(high and high > 0 and 0 < effects.get(e, 0) < 0.4 * high for e, (_, high) in base.items())
    if mission_id == "explorateur":
        return group == 0
    if mission_id == "mal-roule":
        return 0 < weight and sum(v * density.get(e, 0.0) for e, v in effects.items() if v > 0) / weight < LOW_FILL
    return None


def pick(missions: list[dict], verdicts: dict[str, bool | None]) -> dict | None:
    """La mission à proposer pour l'objet posé : la plus rentable de celles qui vont avec lui, sinon la plus
    rentable de celles qui ne dépendent pas de l'objet, sinon la mission du moment."""
    for wanted in (True, None):
        found = next((m for m in missions if verdicts.get(m["id"]) is wanted), None)
        if found is not None:
            return found
    return missions[0] if missions else None


def signature(conn: sqlite3.Connection) -> str:
    """Change dès qu'un passage arrive (le sien ou celui d'un autre) ou qu'une fiche d'objet est lue : le calcul est alors à refaire."""
    mine = conn.execute("SELECT COUNT(*), MAX(source_id), COUNT(character_id) FROM fm_passes").fetchone()
    others = conn.execute("SELECT COUNT(*), (SELECT COUNT(*) FROM shared_fm_refusals) FROM shared_fm_passes").fetchone()
    fetched = conn.execute("SELECT COUNT(*) FROM item_effects_fetched").fetchone()[0]
    return f"{mine[0]}:{mine[1]}:{mine[2]}:{others[0]}:{others[1]}:{fetched}"


_cache: dict = {}
_cache_lock = threading.Lock()


def cached(conn: sqlite3.Connection, density: dict[int, float] | None = None) -> State:
    """Le calcul de la collecte, refait seulement quand quelque chose a changé ; la page et la synchro le partagent."""
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    if not path:  # base en mémoire (tests) : rien à partager
        return compute(conn, density)
    key = path + "|" + signature(conn)
    with _cache_lock:
        if _cache.get("key") != key:
            _cache.update(key=key, state=compute(conn, density))
        return _cache["state"]


def remember(conn: sqlite3.Connection, state: State, now: float | None = None, focus_path: Path | None = None) -> list[str]:
    """Inscrit les missions réussies ; renvoie celles qui viennent de l'être. focus_path : où écrire la mission
    du moment pour la fenêtre d'affichage."""
    now = time.time() if now is None else now
    known = {row[0] for row in conn.execute("SELECT mission_id FROM fm_missions_done")}
    fresh = [m for m in state.done if m not in known]
    with conn:
        conn.executemany("INSERT OR IGNORE INTO fm_missions_done VALUES (?, ?, ?)", [(m, *state.done[m]) for m in fresh])
        # Objectif relevé depuis : la mission n'est plus réussie.
        conn.executemany("DELETE FROM fm_missions_done WHERE mission_id = ?", [(m,) for m in known if m not in state.done])
    if focus_path is not None:
        try:
            focus_path.parent.mkdir(parents=True, exist_ok=True)
            partial = focus_path.with_suffix(".tmp")
            missions = open_missions(state)
            payload = {"mission": missions[0] if missions else None, "missions": missions, "updated": now}
            partial.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(partial, focus_path)
        except OSError:
            pass
    return fresh


def rules(conn: sqlite3.Connection, state: State, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    ctx = Context(state.rows, state.tools, over_refusals(conn, state.density))
    previous = {row[0]: row[1:] for row in conn.execute("SELECT rule_id, status, changed_at, was FROM fm_rule_status")}
    out, changes = [], []
    for rule in RULES:
        status, evidence, n, confidence = rule.judge(ctx)
        known = previous.get(rule.id)
        if known is None:  # premier calcul : pas un changement
            changes.append((rule.id, status, now, None))
            was, changed = None, None
        elif known[0] != status:
            changes.append((rule.id, status, now, known[0]))
            was, changed = known[0], now
        else:
            was, changed = known[2], known[1]
        recent = was is not None and changed is not None and now - changed < 7 * 86400
        out.append({"id": rule.id, "title": rule.title, "text": rule.text, "source": rule.source, "status": status, "label": STATUSES[status],
                    "evidence": evidence, "n": n, "confidence": round(max(0.0, min(1.0, confidence)), 3), "mission": rule.mission,
                    "was": STATUSES.get(was) if recent else None, "changed_at": changed if recent else None})  # fmt: skip
    if changes:
        with conn:
            conn.executemany("INSERT OR REPLACE INTO fm_rule_status VALUES (?, ?, ?, ?)", changes)
    return out


def score(conn: sqlite3.Connection, state: State) -> dict:
    """Ce que le hub reçoit pour le classement : points, passages, kamas investis, missions, et le personnage qui forge."""
    name = main_character(conn)
    missions = [[mission, round(at, 1)] for mission, (at, _) in sorted(state.done.items(), key=lambda kv: kv[1][0])]
    return {"character": name if name and CHARACTER.match(name) else None, "points": state.points, "passes": state.passes,
            "kamas": kamas_spent(conn), "missions": missions}  # fmt: skip


def overview(state: State) -> dict:
    """Barres de progression : une par bloc, la diversité des modèles, et l'ensemble."""
    view = []
    for block in state.blocks:
        cells = [{"cell": cell, "count": block.counts[cell], "target": goal} for cell, goal in block.targets.items()]
        view.append({"id": block.id, "title": block.title, "explain": block.explain, "count": sum(block.counts.values()),
                     "filled": block.filled, "target": block.target, "fill": round(block.fill(), 4), "cells": cells})  # fmt: skip
    worked = sum(1 for n in state.models.values() if n >= MODEL_PASSES)
    total = sum(state.models.values())
    top = state.models.most_common(1)
    diversity = {"id": "models", "title": "Diversité des objets", "explain": f"{MODELS_TARGET} modèles d'objets avec au moins {MODEL_PASSES} passages chacun, aucun au-delà de 10 % du total.",
                 "count": worked, "filled": min(worked, MODELS_TARGET), "target": MODELS_TARGET, "fill": round(min(1.0, worked / MODELS_TARGET), 4),
                 "top_share": round(top[0][1] / total, 4) if top and total else 0.0, "top_item": top[0][0] if top else None, "cells": []}  # fmt: skip
    weights = {"models": 5000}
    parts = view + [diversity]
    weight = lambda b: weights.get(b["id"], b["target"])  # noqa: E731
    overall = sum(weight(b) * b["fill"] for b in parts) / sum(weight(b) for b in parts)
    passes = sum(state.models.values())
    return {"blocks": parts, "overall": round(overall, 4), "passes": passes}
