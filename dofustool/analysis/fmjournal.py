"""Journal de forgemagie : un dossier par exemplaire travaillé, reconstitué à partir des passages de rune captés.

Un passage se classe d'après ce que le jeu en dit :

  - succès critique : la rune est passée et aucune autre ligne n'a baissé ;
  - succès neutre   : la rune est passée, d'autres lignes ont baissé ;
  - échec           : la rune n'est pas passée.

Quand l'état de l'objet avant le passage n'a pas été vu (premier passage d'une connexion), la
perte se déduit du puits : un puits qui bouge trahit une ligne perdue ou payée.
"""
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

SC, SN, EC = "sc", "sn", "ec"
# Au-delà de cet écart, deux passages n'appartiennent plus à la même séance : le temps entre eux ne compte pas.
GAP_S = 120.0
# Un achat du même modèle ne sert de prix de l'objet de base que s'il précède de peu la forgemagie.
MODEL_PURCHASE_MAX_AGE_S = 7 * 86400.0


def outcome(passed: bool, lost: bool | None, pool_change: int) -> str:
    if not passed:
        return EC
    if lost is None:
        return SN if pool_change else SC
    return SN if lost else SC


@dataclass(slots=True)
class RuneLine:
    rune_id: int
    count: int = 0
    sc: int = 0
    sn: int = 0
    ec: int = 0
    cost: float = 0.0  # au prix du marché figé pour chaque passage
    priced: int = 0  # passages dont le prix est connu


@dataclass(slots=True)
class Dossier:
    uid: int
    item_id: int
    first_ts: float
    last_ts: float
    before: dict[int, int] | None
    after: dict[int, int]
    base_cost: int | None  # saisi à la main
    listed_price: int | None
    listed_at: float | None
    sold_price: int | None
    sold_at: float | None
    lot_uid: int | None  # lot en vente qui contient l'objet
    passes: int = 0
    sc: int = 0
    sn: int = 0
    ec: int = 0
    duration_s: float = 0.0
    pool: float | None = None  # puits après le dernier passage
    runes: dict[int, RuneLine] = field(default_factory=dict)

    @property
    def rune_cost(self) -> float:
        return sum(line.cost for line in self.runes.values())

    @property
    def unpriced(self) -> int:
        return self.passes - sum(line.priced for line in self.runes.values())


def freeze_prices(conn: sqlite3.Connection, price_of: Callable[[int], float | None]) -> int:
    """Fige le prix unitaire des runes des passages qui n'en ont pas encore. Renvoie le nombre de runes chiffrées.

    Le prix retenu est celui du moment où le journal est calculé pour la première fois après le
    passage : assez proche du prix payé tant que l'interface tourne pendant la séance.
    """
    done = 0
    with conn:
        for (rune_id,) in conn.execute("SELECT DISTINCT rune_id FROM fm_passes WHERE rune_price IS NULL").fetchall():
            price = price_of(rune_id)
            if price is not None:
                conn.execute("UPDATE fm_passes SET rune_price = ? WHERE rune_id = ? AND rune_price IS NULL", (price, rune_id))
                done += 1
    return done


def _effects(encoded: str | None) -> dict[int, int] | None:
    if encoded is None:
        return None
    return {effect_id: value for effect_id, value in json.loads(encoded) if value is not None}


def load(conn: sqlite3.Connection) -> list[Dossier]:
    """Tous les dossiers, le plus récemment travaillé d'abord."""
    dossiers = {
        row[0]: Dossier(row[0], row[1], row[2], row[3], _effects(row[4]), _effects(row[5]) or {}, *row[6:])
        for row in conn.execute(
            "SELECT uid, item_id, first_ts, last_ts, before, after, base_cost, listed_price, listed_at, sold_price, sold_at, lot_uid "
            "FROM fm_items"
        )
    }
    previous: dict[int, float] = {}
    for uid, ts, rune_id, passed, lost, pool, pool_change, price in conn.execute(
        "SELECT uid, ts, rune_id, passed, lost, pool, pool_change, rune_price FROM fm_passes ORDER BY ts, source_id"
    ):
        dossier = dossiers.get(uid)
        if dossier is None:
            continue
        kind = outcome(bool(passed), None if lost is None else bool(lost), pool_change)
        line = dossier.runes.setdefault(rune_id, RuneLine(rune_id))
        line.count += 1
        setattr(line, kind, getattr(line, kind) + 1)
        if price is not None:
            line.cost += price
            line.priced += 1
        dossier.passes += 1
        setattr(dossier, kind, getattr(dossier, kind) + 1)
        if pool is not None:
            dossier.pool = pool
        if uid in previous and ts - previous[uid] <= GAP_S:
            dossier.duration_s += ts - previous[uid]
        previous[uid] = ts
    return sorted(dossiers.values(), key=lambda d: d.last_ts, reverse=True)


def purchase_units(conn: sqlite3.Connection) -> dict[int, float]:
    """Prix unitaire moyen de mes achats de chaque objet, pondéré par les quantités."""
    return {
        item_id: spent / bought
        for item_id, spent, bought in conn.execute(
            "SELECT item_id, SUM(price), SUM(quantity) FROM trades WHERE kind = 'purchase' GROUP BY item_id"
        )
        if bought
    }


def real_rune_cost(dossier: Dossier, units: dict[int, float]) -> tuple[float, int] | None:
    """(coût des runes à mon prix d'achat moyen, passages couverts), ou None si aucune de ces runes n'a été achetée."""
    covered = [line for line in dossier.runes.values() if line.rune_id in units]
    if not covered:
        return None
    return sum(line.count * units[line.rune_id] for line in covered), sum(line.count for line in covered)


def base_purchase(conn: sqlite3.Connection, dossier: Dossier) -> tuple[int, str] | None:
    """(prix, origine) de l'achat de l'objet de base : cet exemplaire précis, sinon un achat récent du même modèle."""
    row = conn.execute(
        "SELECT price FROM trades WHERE kind = 'purchase' AND ref = ? AND item_id = ? AND quantity = 1", (dossier.uid, dossier.item_id)
    ).fetchone()
    if row is not None:
        return row[0], "purchase"
    row = conn.execute(
        "SELECT price FROM trades WHERE kind = 'purchase' AND item_id = ? AND quantity = 1 AND ts BETWEEN ? AND ? "
        "ORDER BY ts DESC LIMIT 1",
        (dossier.item_id, dossier.first_ts - MODEL_PURCHASE_MAX_AGE_S, dossier.first_ts),
    ).fetchone()
    return (row[0], "model") if row is not None else None
