"""Ce que le joueur possède (inventaire et banque), et les crafts faisables avec."""
import sqlite3
from dataclasses import dataclass

from ..db import ALL, BANK, HAVRE, INVENTORY
from .crafts import CraftCalculator, CraftResult


@dataclass(frozen=True, slots=True)
class Owned:
    inventory: int = 0
    bank: int = 0
    havre: int = 0  # havre-sac

    @property
    def total(self) -> int:
        return self.inventory + self.bank + self.havre


class Stock:
    """Quantités possédées par item, hors objets portés.

    Chaque coffre (inventaire, banque, havre-sac) est suivi séparément : par ses listes complètes,
    par la répartition que porte chaque pile d'une liste réunie d'HDV ou d'atelier, puis par les
    mouvements isolés. Avant ce suivi, la banque se déduisait d'une liste « tout confondu » : fusion
    moins inventaire ; ce calcul reste pour les bases qui n'ont que ces anciens relevés.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.meta: dict[str, dict] = {
            container: {"captured_at": ts, "kamas": kamas, "stacks": stacks}
            for container, ts, kamas, stacks in conn.execute("SELECT container, captured_at, kamas, stacks FROM holdings_meta")
        }
        by: dict[str, dict[int, int]] = {INVENTORY: {}, BANK: {}, ALL: {}}
        for container, item_id, quantity in conn.execute(
            "SELECT container, item_id, SUM(quantity) FROM holdings WHERE NOT equipped GROUP BY container, item_id"
        ):
            by.setdefault(container, {})[item_id] = quantity
        inventory, bank, havre = by[INVENTORY], by[BANK], by.get(HAVRE, {})
        # La liste fusionnée est souvent plus récente que la dernière visite à la banque : on s'y fie alors.
        # Dès que les coffres sont suivis pile par pile, l'ancienne liste « tout confondu » ne sert plus.
        by_pile = conn.execute("SELECT 1 FROM piles LIMIT 1").fetchone() is not None
        self.bank_inferred = not by_pile and ALL in self.meta and (
            BANK not in self.meta or self.meta[ALL]["captured_at"] > self.meta[BANK]["captured_at"]
        )
        if self.bank_inferred:
            bank = {i: q - inventory.get(i, 0) for i, q in by[ALL].items() if q - inventory.get(i, 0) > 0}
        self._owned = {
            i: Owned(inventory.get(i, 0), bank.get(i, 0), havre.get(i, 0)) for i in inventory.keys() | bank.keys() | havre.keys()
        }

    @property
    def known(self) -> bool:
        return bool(self.meta)

    @property
    def bank_known(self) -> bool:
        return BANK in self.meta or self.bank_inferred

    def get(self, item_id: int) -> Owned:
        return self._owned.get(item_id, Owned())

    def items(self) -> dict[int, Owned]:
        return self._owned


@dataclass(slots=True)
class StockCraft:
    result: CraftResult
    craftable: int  # nombre d'exemplaires faisables avec le stock seul
    ingredients: list[tuple[int, int, int]]  # (item, quantité par craft, quantité possédée)
    covered: int  # ingrédients déjà en quantité suffisante pour un craft
    missing_cost: float | None  # coût d'achat de ce qui manque pour un craft ; None si un prix manque

    @property
    def total_margin(self) -> float | None:
        margin = self.result.recursive_margin
        return margin * self.craftable if margin is not None else None


def stock_crafts(conn: sqlite3.Connection, calculator: CraftCalculator, stock: Stock) -> list[StockCraft]:
    """Recettes dont le joueur possède au moins un ingrédient.

    Seuls les ingrédients directs sont regardés : posséder de quoi fabriquer un ingrédient ne
    compte pas comme le posséder.
    """
    job_names = dict(conn.execute("SELECT id, name FROM jobs"))
    out = []
    for recipe in calculator.recipes.values():
        if not recipe.ingredients:
            continue
        lines = [(item_id, quantity, stock.get(item_id).total) for item_id, quantity in recipe.ingredients]
        if not any(have for _, _, have in lines):
            continue
        missing_cost: float | None = 0.0
        for item_id, need, have in lines:
            if have >= need:
                continue
            unit = calculator.unit_cost(item_id).cost
            if unit is None:
                missing_cost = None
            elif missing_cost is not None:
                missing_cost += (need - have) * unit
        out.append(
            StockCraft(
                result=calculator.evaluate(recipe, job_names.get(recipe.job_id, f"#{recipe.job_id}")),
                craftable=min(have // need for _, need, have in lines),
                ingredients=lines,
                covered=sum(1 for _, need, have in lines if have >= need),
                missing_cost=missing_cost,
            )
        )
    out.sort(key=lambda c: (-(c.total_margin or 0.0), -(c.covered / len(c.ingredients)), c.result.item.name))
    return out
