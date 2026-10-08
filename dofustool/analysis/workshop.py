"""Atelier : des listes d'objectifs (objets à obtenir), et ce qu'il faut réunir pour chacune.

Un objectif s'achète tout fait ou se fabrique. Fabriqué, il se décompose en ses ingrédients, au
premier niveau seulement : un ingrédient lui-même fabricable reste une ligne à acheter, jusqu'à ce
que le joueur l'« ouvre » pour le remplacer par ses propres ingrédients.

Ce que le joueur possède déjà est déduit : d'abord des objets à fabriquer (objectif ou ingrédient
ouvert déjà en stock), puis des lignes à acheter. Chaque liste est calculée face au stock entier ;
quand plusieurs listes veulent la même ressource, le besoin total est donné à côté.
"""
from collections.abc import Callable
from dataclasses import dataclass, field

from .crafts import Recipe

BUY, CRAFT = "buy", "craft"
MAX_DEPTH = 8


@dataclass(frozen=True, slots=True)
class Goal:
    id: int
    item_id: int
    quantity: int
    mode: str | None = None  # BUY, CRAFT, ou None : le moins cher des deux
    note: str = ""


@dataclass(slots=True)
class GoalPlan:
    goal: Goal
    mode: str  # ce qui est retenu
    craftable: bool
    buy_cost: float | None  # tout acheter fait
    craft_cost: float | None  # acheter les ingrédients du premier niveau
    owned: int  # exemplaires déjà possédés
    to_make: int  # à fabriquer, une fois le stock déduit (0 pour un objectif acheté)


@dataclass(slots=True)
class Plan:
    goals: list[GoalPlan] = field(default_factory=list)
    lines: dict[int, int] = field(default_factory=dict)  # objet à réunir -> quantité
    opened: dict[int, tuple[int, int]] = field(default_factory=dict)  # ingrédient ouvert -> (besoin, à fabriquer)


def first_level_cost(recipe: Recipe, price: Callable[[int], float | None]) -> float | None:
    """Coût d'une fabrication en achetant chaque ingrédient ; None si l'un d'eux n'a pas de prix."""
    total = 0.0
    for item_id, quantity in recipe.ingredients:
        unit = price(item_id)
        if unit is None:
            return None
        total += unit * quantity
    return total


def plan(
    goals: list[Goal],
    opened: set[int],
    recipes: dict[int, Recipe],
    price: Callable[[int], float | None],
    owned: Callable[[int], int],
) -> Plan:
    out = Plan()
    used: dict[int, int] = {}  # stock déjà affecté à un objet à fabriquer

    def take(item_id: int, wanted: int) -> int:
        """Prend dans le stock ce qui est disponible pour cet objet ; renvoie ce qu'il reste à produire."""
        free = max(0, owned(item_id) - used.get(item_id, 0))
        taken = min(wanted, free)
        used[item_id] = used.get(item_id, 0) + taken
        return wanted - taken

    def need(item_id: int, quantity: int, path: tuple[int, ...]) -> None:
        recipe = recipes.get(item_id)
        if quantity <= 0:
            return
        if item_id in opened and recipe is not None and recipe.ingredients and item_id not in path and len(path) < MAX_DEPTH:
            to_make = take(item_id, quantity)
            before = out.opened.get(item_id, (0, 0))
            out.opened[item_id] = (before[0] + quantity, before[1] + to_make)
            for ingredient_id, per in recipe.ingredients:
                need(ingredient_id, per * to_make, (*path, item_id))
        else:
            out.lines[item_id] = out.lines.get(item_id, 0) + quantity

    for goal in goals:
        recipe = recipes.get(goal.item_id)
        craftable = recipe is not None and bool(recipe.ingredients)
        unit = price(goal.item_id)
        buy_cost = unit * goal.quantity if unit is not None else None
        craft_unit = first_level_cost(recipe, price) if craftable else None
        craft_cost = craft_unit * goal.quantity if craft_unit is not None else None
        if not craftable:
            mode = BUY
        elif goal.mode in (BUY, CRAFT):
            mode = goal.mode
        elif buy_cost is None or (craft_cost is not None and craft_cost < buy_cost):
            mode = CRAFT
        else:
            mode = BUY
        to_make = 0
        if mode == CRAFT:
            to_make = take(goal.item_id, goal.quantity)
            for ingredient_id, per in recipe.ingredients:
                need(ingredient_id, per * to_make, (goal.item_id,))
        else:
            out.lines[goal.item_id] = out.lines.get(goal.item_id, 0) + goal.quantity
        out.goals.append(GoalPlan(goal, mode, craftable, buy_cost, craft_cost, owned(goal.item_id), to_make))
    return out
