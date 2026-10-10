"""Ce que le joueur possède (inventaire et banque), et les crafts faisables avec."""
import sqlite3
from dataclasses import dataclass, field

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


@dataclass(frozen=True, slots=True)
class Packed:
    """Une ressource possédée sous forme de conteneurs fermés (sachets, tonneaux, sacs) : comptée à part
    du vrai stock, puisqu'il faut d'abord les ouvrir."""

    units: int = 0  # ce que donneraient tous les conteneurs possédés
    containers: tuple[tuple[int, int, int], ...] = ()  # (conteneur, unités par conteneur, conteneurs possédés)

    def to_open(self, missing: int) -> list[tuple[int, int, int]]:
        """[(conteneur, à ouvrir, possédés)] pour couvrir `missing` unités, en ouvrant le moins de conteneurs possible."""
        left = {container: owned for container, _, owned in self.containers}
        opened: dict[int, int] = {}
        remaining = max(0, missing)
        for container, per, _ in sorted(self.containers, key=lambda c: -c[1]):  # les gros d'abord, sans dépasser
            count = min(left[container], remaining // per)
            if count:
                opened[container] = count
                left[container] -= count
                remaining -= count * per
        for container, per, _ in sorted(self.containers, key=lambda c: c[1]):  # puis le plus petit qui finit le compte
            if remaining <= 0:
                break
            count = min(left[container], -(-remaining // per))
            if count:
                opened[container] = opened.get(container, 0) + count
                left[container] -= count
                remaining -= count * per
        return [(container, opened[container], owned) for container, _, owned in self.containers if container in opened]


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
        # Ressources possédées en conteneurs fermés : {ressource: Packed}, et ce que contient chaque conteneur.
        self.contents: dict[int, tuple[int, int]] = {
            container: (item_id, quantity) for container, item_id, quantity in conn.execute("SELECT * FROM item_contents")
        }
        packed: dict[int, list[tuple[int, int, int]]] = {}
        for container, (item_id, quantity) in self.contents.items():
            owned = self._owned.get(container)
            if owned is not None and owned.total > 0:
                packed.setdefault(item_id, []).append((container, quantity, owned.total))
        self._packed = {
            item_id: Packed(sum(per * owned for _, per, owned in found), tuple(sorted(found))) for item_id, found in packed.items()
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

    def packed(self, item_id: int) -> Packed:
        """Ce que le joueur possède de cette ressource en conteneurs fermés, à part du vrai stock."""
        return self._packed.get(item_id, Packed())


@dataclass(slots=True)
class StockCraft:
    result: CraftResult
    craftable: int  # nombre d'exemplaires faisables avec le stock seul
    ingredients: list[tuple[int, int, int]]  # (item, quantité par craft, quantité possédée)
    covered: int  # ingrédients déjà en quantité suffisante pour un craft
    missing_cost: float | None  # coût d'achat de ce qui manque pour un craft ; None si un prix manque
    # Ingrédients possédés aussi en conteneurs fermés : {ingrédient: unités}. Comptés dans craftable.
    packed: dict[int, int] = field(default_factory=dict)

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
        # Un sachet fermé compte : il suffit de l'ouvrir. Le vrai stock et les conteneurs restent distingués.
        packed = {item_id: units for item_id, _, _ in lines if (units := stock.packed(item_id).units)}
        if not any(have for _, _, have in lines) and not packed:
            continue
        missing_cost: float | None = 0.0
        for item_id, need, real in lines:
            have = real + packed.get(item_id, 0)
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
                craftable=min((have + packed.get(item_id, 0)) // need for item_id, need, have in lines),
                ingredients=lines,
                covered=sum(1 for item_id, need, have in lines if have + packed.get(item_id, 0) >= need),
                missing_cost=missing_cost,
                packed=packed,
            )
        )
    out.sort(key=lambda c: (-(c.total_margin or 0.0), -(c.covered / len(c.ingredients)), c.result.item.name))
    return out
