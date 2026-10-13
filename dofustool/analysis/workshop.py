"""Atelier : des listes d'objectifs (objets à obtenir), et ce qu'il faut réunir pour chacune.

Un objectif s'achète tout fait ou se fabrique. Fabriqué, il se décompose en ses ingrédients, au
premier niveau seulement : un ingrédient lui-même fabricable reste une ligne à acheter, jusqu'à ce
que le joueur l'« ouvre » pour le remplacer par ses propres ingrédients.

Ce que le joueur possède déjà est déduit : d'abord des objets à fabriquer (objectif ou ingrédient
ouvert déjà en stock), puis des lignes à acheter. Chaque liste est calculée face au stock entier ;
quand plusieurs listes veulent la même ressource, le besoin total est donné à côté.

« Le moins cher » compare ce qu'il reste à payer : un ingrédient cher déjà en stock fait pencher
vers la fabrication, y compris dans les sous-recettes ouvertes.
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
    # Montée de métier : nombre de crafts déjà faits. L'objectif se fabrique alors en entier, sans déduire les
    # exemplaires possédés (ils n'ont donné aucune XP) ; seuls ses ingrédients sont pris dans le stock.
    done: int | None = None


@dataclass(slots=True)
class GoalPlan:
    goal: Goal
    mode: str  # ce qui est retenu
    craftable: bool
    buy_cost: float | None  # tout acheter fait, exemplaires déjà possédés déduits
    craft_cost: float | None  # acheter les ingrédients du premier niveau qui manquent encore
    owned: int  # exemplaires déjà possédés
    to_make: int  # à fabriquer, une fois le stock déduit (0 pour un objectif acheté)


@dataclass(slots=True)
class Plan:
    goals: list[GoalPlan] = field(default_factory=list)
    lines: dict[int, int] = field(default_factory=dict)  # objet à réunir -> quantité
    opened: dict[int, tuple[int, int]] = field(default_factory=dict)  # ingrédient ouvert -> (besoin, à fabriquer)
    # D'où vient le besoin de chaque objet (ligne ou ingrédient ouvert) : {ingrédient ouvert qui le demande: quantité},
    # la clé 0 désignant les objectifs eux-mêmes. Sert à ranger chaque ressource sous la recette qui la réclame.
    sources: dict[int, dict[int, int]] = field(default_factory=dict)


def craft_cost_left(
    item_id: int,
    count: int,
    recipes: dict[int, Recipe],
    opened: set[int],
    price: Callable[[int], float | None],
    free: Callable[[int], int] = lambda _: 0,
) -> float | None:
    """Ce qu'il reste à payer pour fabriquer count fois item_id (free : stock encore disponible).

    Un ingrédient déjà possédé ne coûte rien. Un ingrédient ouvert se chiffre à son tour par sa
    recette ; les autres au prix d'achat. Le stock est réparti au fil du calcul : une ressource
    partagée par deux sous-recettes n'est comptée qu'une fois comme déjà là. None si un objet à
    acheter n'a pas de prix.
    """
    taken: dict[int, int] = {}

    def cost(target: int, wanted: int, path: tuple[int, ...]) -> float | None:
        have = min(wanted, max(0, free(target) - taken.get(target, 0)))
        taken[target] = taken.get(target, 0) + have
        wanted -= have
        if wanted == 0:
            return 0.0
        recipe = recipes.get(target)
        if target in opened and recipe is not None and recipe.ingredients and target not in path and len(path) < MAX_DEPTH:
            return ingredients(recipe, wanted, (*path, target))
        unit = price(target)
        return unit * wanted if unit is not None else None

    def ingredients(recipe: Recipe, made: int, path: tuple[int, ...]) -> float | None:
        total = 0.0
        for ingredient_id, per in recipe.ingredients:
            part = cost(ingredient_id, per * made, path)
            if part is None:
                return None
            total += part
        return total

    return ingredients(recipes[item_id], count, (item_id,))


def plan(
    goals: list[Goal],
    opened: set[int],
    recipes: dict[int, Recipe],
    price: Callable[[int], float | None],
    owned: Callable[[int], int],
) -> Plan:
    out = Plan()
    used: dict[int, int] = {}  # stock déjà affecté à un objet à fabriquer

    def free(item_id: int) -> int:
        return max(0, owned(item_id) - used.get(item_id, 0))

    def take(item_id: int, wanted: int) -> int:
        """Prend dans le stock ce qui est disponible pour cet objet ; renvoie ce qu'il reste à produire."""
        taken = min(wanted, free(item_id))
        used[item_id] = used.get(item_id, 0) + taken
        return wanted - taken

    def need(item_id: int, quantity: int, path: tuple[int, ...], parent: int = 0) -> None:
        recipe = recipes.get(item_id)
        if quantity <= 0:
            return
        wanted = out.sources.setdefault(item_id, {})
        wanted[parent] = wanted.get(parent, 0) + quantity
        if item_id in opened and recipe is not None and recipe.ingredients and item_id not in path and len(path) < MAX_DEPTH:
            to_make = take(item_id, quantity)
            before = out.opened.get(item_id, (0, 0))
            out.opened[item_id] = (before[0] + quantity, before[1] + to_make)
            for ingredient_id, per in recipe.ingredients:
                need(ingredient_id, per * to_make, (*path, item_id), item_id)
        else:
            out.lines[item_id] = out.lines.get(item_id, 0) + quantity

    for goal in goals:
        recipe = recipes.get(goal.item_id)
        craftable = recipe is not None and bool(recipe.ingredients)
        # Les deux coûts comparent ce qu'il reste à payer : un objet ou un ingrédient déjà possédé ne coûte rien.
        left = max(0, goal.quantity - free(goal.item_id))
        unit = price(goal.item_id)
        buy_cost = unit * left if unit is not None else (0.0 if left == 0 else None)
        if goal.done is not None and craftable:
            left = max(0, goal.quantity - goal.done)
        craft_cost = craft_cost_left(goal.item_id, left, recipes, opened, price, free) if craftable else None
        if goal.done is not None and craftable:
            for ingredient_id, per in recipe.ingredients:
                need(ingredient_id, per * left, (goal.item_id,))
            out.goals.append(GoalPlan(goal, CRAFT, craftable, buy_cost, craft_cost, owned(goal.item_id), left))
            continue
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
            wanted = out.sources.setdefault(goal.item_id, {})
            wanted[0] = wanted.get(0, 0) + goal.quantity
        out.goals.append(GoalPlan(goal, mode, craftable, buy_cost, craft_cost, owned(goal.item_id), to_make))
    return out
