"""Marges de craft, directes et récursives."""
import sqlite3
from dataclasses import dataclass, field

from .prices import Liquidity, PriceBook, PriceRef

BUY = "achat"
CRAFT = "craft"


@dataclass(frozen=True, slots=True)
class Item:
    id: int
    name: str
    level: int
    exchangeable: bool


@dataclass(frozen=True, slots=True)
class Recipe:
    result_id: int
    job_id: int
    level: int
    ingredients: tuple[tuple[int, int], ...]  # (item, quantité)


@dataclass(frozen=True, slots=True)
class UnitCost:
    """Coût le plus bas pour obtenir une unité d'un item. cost est None si aucun moyen n'a de prix."""

    cost: float | None
    mode: str | None = None
    missing: frozenset[int] = frozenset()  # items sans prix qui empêchent le calcul


@dataclass(slots=True)
class CraftResult:
    recipe: Recipe
    item: Item
    job: str
    sell: PriceRef | None
    revenue: float | None  # prix de vente net de taxe
    direct_cost: float | None  # tous les ingrédients achetés
    recursive_cost: float | None  # min(achat, craft) à chaque niveau
    liquidity: Liquidity
    low_liquidity: bool
    missing_prices: list[int] = field(default_factory=list)
    non_exchangeable_ingredients: list[int] = field(default_factory=list)
    crafted_ingredients: list[int] = field(default_factory=list)  # sous-crafts retenus par le calcul récursif
    result_not_exchangeable: bool = False
    own_job: bool | None = None  # réalisable avec tes métiers ; None si aucun métier n'est configuré
    weighted_margin: float | None = None

    @property
    def margin(self) -> float | None:
        if self.revenue is None or self.direct_cost is None:
            return None
        return self.revenue - self.direct_cost

    @property
    def recursive_margin(self) -> float | None:
        if self.revenue is None or self.recursive_cost is None:
            return None
        return self.revenue - self.recursive_cost

    @property
    def margin_pct(self) -> float | None:
        """Marge récursive rapportée au capital engagé."""
        margin = self.recursive_margin
        if margin is None or not self.recursive_cost:
            return None
        return margin / self.recursive_cost

    @property
    def flags(self) -> list[str]:
        out = []
        if self.result_not_exchangeable:
            out.append("résultat non échangeable")
        if self.missing_prices:
            out.append(f"{len(self.missing_prices)} prix manquant(s)")
        if self.non_exchangeable_ingredients:
            out.append("ingrédient non échangeable")
        if not self.liquidity.known:
            out.append("liquidité inconnue")
        elif self.low_liquidity:
            out.append("peu échangé")
        return out


def load_items(conn: sqlite3.Connection) -> dict[int, Item]:
    return {
        i: Item(i, name, level, bool(exchangeable))
        for i, name, level, exchangeable in conn.execute("SELECT id, name, level, exchangeable FROM items")
    }


def load_recipes(conn: sqlite3.Connection) -> dict[int, Recipe]:
    ingredients: dict[int, list[tuple[int, int]]] = {}
    for result_id, item_id, quantity in conn.execute("SELECT result_id, item_id, quantity FROM recipe_ingredients"):
        ingredients.setdefault(result_id, []).append((item_id, quantity))
    return {
        result_id: Recipe(result_id, job_id, level, tuple(ingredients.get(result_id, ())))
        for result_id, job_id, level in conn.execute("SELECT result_id, job_id, level FROM recipes")
    }


def resolve_jobs(conn: sqlite3.Connection, jobs: dict[str, int]) -> tuple[dict[int, int], list[str]]:
    """Convertit {nom de métier: niveau} en {id: niveau}. Renvoie aussi les noms inconnus."""
    by_name = {name.casefold(): job_id for job_id, name in conn.execute("SELECT id, name FROM jobs")}
    resolved, unknown = {}, []
    for name, level in jobs.items():
        job_id = by_name.get(name.casefold())
        if job_id is None:
            unknown.append(name)
        else:
            resolved[job_id] = level
    return resolved, unknown


class CraftCalculator:
    def __init__(
        self,
        items: dict[int, Item],
        recipes: dict[int, Recipe],
        prices: PriceBook,
        hdv_tax: float,
        job_levels: dict[int, int] | None = None,
        min_liquidity: int = 0,
    ) -> None:
        """job_levels : {id de métier: niveau} du joueur, ou None s'il n'en a pas configuré.

        Les métiers ne restreignent jamais le calcul : un craft peut être confié à un autre
        joueur. Ils servent seulement à marquer et filtrer ce que le joueur peut faire lui-même.
        """
        self.items = items
        self.recipes = recipes
        self.prices = prices
        self.tax = hdv_tax
        self.job_levels = job_levels
        self.min_liquidity = min_liquidity
        self._memo: dict[int, UnitCost] = {}

    def own_job(self, recipe: Recipe) -> bool | None:
        if self.job_levels is None:
            return None
        return self.job_levels.get(recipe.job_id, 0) >= recipe.level

    def _buy_price(self, item_id: int) -> int | None:
        item = self.items.get(item_id)
        if item is None or not item.exchangeable:
            return None
        ref = self.prices.get(item_id)
        return ref.price if ref is not None else None

    def unit_cost(self, item_id: int) -> UnitCost:
        return self._unit_cost(item_id, set())[0]

    def _unit_cost(self, item_id: int, stack: set[int]) -> tuple[UnitCost, bool]:
        """Renvoie (coût, dépend d'un cycle). Un résultat obtenu en coupant un cycle n'est pas mémorisé."""
        if item_id in self._memo:
            return self._memo[item_id], False
        buy = self._buy_price(item_id)
        recipe = self.recipes.get(item_id)
        craft: float | None = None
        missing: set[int] = set()
        tainted = False
        if recipe is not None and recipe.ingredients:
            if item_id in stack:
                tainted = True  # garde anti-cycle : dans cette branche, l'item ne peut qu'être acheté
            else:
                stack.add(item_id)
                craft = 0.0
                for ingredient_id, quantity in recipe.ingredients:
                    sub, sub_tainted = self._unit_cost(ingredient_id, stack)
                    tainted = tainted or sub_tainted
                    if sub.cost is None:
                        missing |= sub.missing or {ingredient_id}
                        craft = None
                    elif craft is not None:
                        craft += quantity * sub.cost
                stack.discard(item_id)
        if buy is not None and (craft is None or buy <= craft):
            result = UnitCost(float(buy), BUY)
        elif craft is not None:
            result = UnitCost(craft, CRAFT)
        else:
            result = UnitCost(None, None, frozenset(missing or {item_id}))
        if not tainted:
            self._memo[item_id] = result
        return result, tainted

    def evaluate(self, recipe: Recipe, job_name: str = "") -> CraftResult:
        item = self.items[recipe.result_id]
        sell = self.prices.get(recipe.result_id) if item.exchangeable else None
        liquidity = self.prices.liquidity(recipe.result_id)
        low = liquidity.qty_7d is not None and liquidity.qty_7d < self.min_liquidity
        result = CraftResult(
            recipe=recipe,
            item=item,
            job=job_name,
            sell=sell,
            revenue=sell.price * (1 - self.tax) if sell is not None else None,
            direct_cost=0.0,
            recursive_cost=0.0,
            liquidity=liquidity,
            low_liquidity=low,
            result_not_exchangeable=not item.exchangeable,
            own_job=self.own_job(recipe),
        )
        if item.exchangeable and sell is None:
            result.missing_prices.append(item.id)
        for ingredient_id, quantity in recipe.ingredients:
            ingredient = self.items.get(ingredient_id)
            if ingredient is not None and not ingredient.exchangeable:
                result.non_exchangeable_ingredients.append(ingredient_id)
            buy = self._buy_price(ingredient_id)
            if buy is None:
                result.direct_cost = None
            elif result.direct_cost is not None:
                result.direct_cost += quantity * buy
            unit = self.unit_cost(ingredient_id)
            if unit.cost is None:
                result.recursive_cost = None
                result.missing_prices += sorted(unit.missing - set(result.missing_prices))
            else:
                if unit.mode == CRAFT:
                    result.crafted_ingredients.append(ingredient_id)
                if result.recursive_cost is not None:
                    result.recursive_cost += quantity * unit.cost
        margin = result.recursive_margin
        if margin is not None:
            # Un objet sous le seuil de liquidité voit sa marge réduite au prorata ; liquidité inconnue = pas de pénalité.
            factor = 1.0
            if low and self.min_liquidity > 0:
                factor = (liquidity.qty_7d or 0) / self.min_liquidity
            result.weighted_margin = margin * factor
        return result


def rank_crafts(conn: sqlite3.Connection, calculator: CraftCalculator) -> list[CraftResult]:
    """Évalue toutes les recettes. Les recettes incalculables sont gardées, en fin de classement."""
    job_names = dict(conn.execute("SELECT id, name FROM jobs"))
    results = [
        calculator.evaluate(recipe, job_names.get(recipe.job_id, f"#{recipe.job_id}"))
        for recipe in calculator.recipes.values()
    ]
    results.sort(key=lambda r: (r.weighted_margin is None, -(r.weighted_margin or 0.0), r.item.name))
    return results
