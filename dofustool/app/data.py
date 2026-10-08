"""Préparation des données du dashboard, sans dépendance à Streamlit (testable)."""
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from ..analysis import GRAIN_DAY, GRAIN_HOUR, cours
from ..analysis.forgemagie import MARKERS, classify
from ..analysis.crafts import CraftCalculator, CraftResult, Item, load_items, load_recipes, rank_crafts, resolve_jobs
from ..analysis.prices import AVG_PRICE, ESTIMATED, PriceBook, PriceRef
from ..analysis.trends import INSUFFICIENT, compute_trends
from ..config import Config

# Séries du cours du marché, avec le titre affiché.
SERIES = ((GRAIN_HOUR, "Par heure (24 h)"), (GRAIN_DAY, "Par jour"))
HEARTBEAT_STALE_S = 15.0


@dataclass(slots=True)
class Workspace:
    """Tout ce qu'une page a besoin de recalculer quand les données changent."""

    items: dict[int, Item]
    prices: PriceBook
    calculator: CraftCalculator
    job_names: dict[int, str]
    unknown_jobs: list[str]
    now: float


def build_workspace(conn: sqlite3.Connection, cfg: Config, now: float) -> Workspace:
    items = load_items(conn)
    cours.update(conn)  # incrémental : seuls les prix moyens arrivés depuis le dernier passage
    prices = PriceBook(conn, now, cfg.last_sale_max_age_hours, cfg.use_estimated_prices, cfg.equipment_price)
    job_levels, unknown = resolve_jobs(conn, cfg.jobs)
    calculator = CraftCalculator(items, load_recipes(conn), prices, cfg.hdv_tax, job_levels or None, cfg.min_liquidity)
    return Workspace(items, prices, calculator, dict(conn.execute("SELECT id, name FROM jobs")), unknown, now)


def _craft_row(r: CraftResult, now: float, sold_30d: int | None = None) -> dict:
    notes = list(r.flags)
    if r.crafted_ingredients:
        notes.append(f"{len(r.crafted_ingredients)} sous-craft(s)")
    return {
        "item_id": r.item.id,
        "Objet": r.item.name,
        "Métier": r.job,
        "Niveau": r.recipe.level,
        "Prix de vente": r.sell.price if r.sell else None,
        "Coût": r.recursive_cost,
        "Marge": r.recursive_margin,
        "Marge %": r.margin_pct * 100 if r.margin_pct is not None else None,
        "Marge sans sous-craft": r.margin,
        "Vendus 7 j": r.liquidity.qty_7d,
        "Vendus 24 h": r.liquidity.qty_24h,
        "Vendus 30 j": sold_30d,
        "Source du prix": r.sell.source if r.sell else None,
        "Lot du prix": r.sell.lot if r.sell else None,
        "Âge du prix (h)": round(r.sell.age_hours(now), 1) if r.sell else None,
        "Mon métier": r.own_job,
        "Remarques": ", ".join(notes),
        "Marge pondérée": r.weighted_margin,
    }


def crafts_frame(conn: sqlite3.Connection, ws: Workspace) -> pd.DataFrame:
    """Une ligne par recette, déjà classée par marge pondérée par la liquidité."""
    return pd.DataFrame([_craft_row(r, ws.now, ws.prices.sold_30d(r.item.id)) for r in rank_crafts(conn, ws.calculator)])


def filter_crafts(
    df: pd.DataFrame,
    jobs: list[str] | None = None,
    level_range: tuple[int, int] = (1, 200),
    max_capital: float | None = None,
    min_liquidity: int = 0,
    only_own_jobs: bool = False,
    only_computable: bool = True,
) -> pd.DataFrame:
    if df.empty:
        return df
    mask = df["Niveau"].between(*level_range)
    if jobs:
        mask &= df["Métier"].isin(jobs)
    if only_own_jobs:
        mask &= df["Mon métier"].eq(True)
    if only_computable:
        mask &= df["Marge"].notna()
    if max_capital is not None:
        mask &= df["Coût"].isna() | (df["Coût"] <= max_capital)
    if min_liquidity > 0:
        # Une liquidité inconnue n'est pas une liquidité nulle : ces lignes restent visibles.
        mask &= df["Vendus 7 j"].isna() | (df["Vendus 7 j"] >= min_liquidity)
    return df[mask]


def trends_frame(conn: sqlite3.Connection, cfg: Config, ws: Workspace) -> tuple[pd.DataFrame, int]:
    """Renvoie (objets ayant une tendance calculable, nombre d'objets en « données insuffisantes »)."""
    trends = compute_trends(conn, ws.now, cfg.min_snapshots_for_trend, cfg.last_sale_max_age_hours, ws.prices)
    rows = []
    insufficient = 0
    for trend in trends.values():
        if trend.basis == INSUFFICIENT or trend.deviation is None:
            insufficient += 1
            continue
        item = ws.items.get(trend.item_id)
        rows.append(
            {
                "item_id": trend.item_id,
                "Objet": item.name if item else f"#{trend.item_id}",
                "Niveau": item.level if item else None,
                "Prix": trend.current,
                "Moyenne 7 j": trend.mean_7d,
                "Moyenne 30 j": trend.mean_30d,
                "Prix moyen": trend.reference,
                "Écart %": trend.deviation * 100,
                "Signal": trend.signal(cfg.trend_threshold),
                "Base": trend.basis,
                "Vendus 7 j": ws.prices.liquidity(trend.item_id).qty_7d,
            }
        )
    columns = [
        "item_id", "Objet", "Niveau", "Prix", "Moyenne 7 j", "Moyenne 30 j", "Prix moyen", "Écart %", "Signal", "Base",
        "Vendus 7 j",
    ]  # fmt: skip
    return pd.DataFrame(rows, columns=columns), insufficient


def item_options(conn: sqlite3.Connection) -> dict[int, str]:
    """Objets utiles à consulter : ceux qui ont un prix, une recette, ou servent d'ingrédient."""
    rows = conn.execute(
        "SELECT id, name, level FROM items WHERE id IN ("
        " SELECT item_id FROM avg_prices UNION SELECT result_id FROM recipes"
        " UNION SELECT item_id FROM recipe_ingredients UNION SELECT item_id FROM last_sales) ORDER BY name"
    )
    return {item_id: f"{name} (niv. {level})" for item_id, name, level in rows}


def _local_dates(seconds: pd.Series) -> pd.Series:
    """Horodatages Unix -> dates à l'heure locale de la machine, comme les affiche le jeu."""
    local = datetime.now().astimezone().tzinfo
    return pd.to_datetime(seconds, unit="s", utc=True).dt.tz_convert(local).dt.tz_localize(None)


def moment_price(prices: PriceBook, item_id: int) -> tuple[float | None, bool]:
    """(prix du moment, observé ou non) pour le cours relatif : annonce HDV, sinon prix relevé, sinon prix estimé.

    Le prix estimé vient du glissement des mêmes prix moyens : avec lui, le rythme total vaut 1 par
    construction. Seule la répartition des ventes dans le temps a alors un sens.
    """
    ask = prices.hdv_ask(item_id)
    if ask is not None:
        return ask[0], True
    ref = prices.get(item_id)
    if ref is not None and ref.source not in (AVG_PRICE, ESTIMATED):
        return ref.price, True
    guess = prices.estimate(item_id)
    return (guess.price, False) if guess is not None else (None, False)


def _ref_row(ref: PriceRef | None, now: float) -> tuple[int | None, str | None, float | None, int | None]:
    return (ref.price, ref.source, round(ref.age_hours(now), 1), ref.lot) if ref else (None, None, None, None)


def item_detail(conn: sqlite3.Connection, ws: Workspace, item_id: int) -> dict:
    item = ws.items[item_id]
    type_name = conn.execute("SELECT type_name FROM items WHERE id = ?", (item_id,)).fetchone()[0]
    detail: dict = {
        "item": item,
        "type": type_name,
        "ref": ws.prices.get(item_id),
        "liquidity": ws.prices.liquidity(item_id),
        "unit_cost": ws.calculator.unit_cost(item_id),
    }
    detail["snapshots"] = pd.DataFrame(
        conn.execute(
            "SELECT s.ts, p.price FROM avg_prices p JOIN snapshots s ON s.id = p.snapshot_id "
            "WHERE p.item_id = ? ORDER BY s.ts",
            (item_id,),
        ).fetchall(),
        columns=["ts", "Prix moyen"],
    )
    detail["snapshots"]["Date"] = _local_dates(detail["snapshots"].pop("ts"))
    detail["last_sales"] = pd.DataFrame(
        conn.execute(
            "SELECT sold_at, price FROM last_sales WHERE item_id = ? ORDER BY sold_at", (item_id,)
        ).fetchall(),
        columns=["ts", "Dernier prix de vente"],
    )
    detail["last_sales"]["Date"] = _local_dates(detail["last_sales"].pop("ts"))
    history = {}
    for period, title in SERIES:
        frame = pd.DataFrame(
            conn.execute(
                "SELECT bucket_ts, price, qty_sold FROM market_history WHERE item_id = ? AND period = ? ORDER BY bucket_ts",
                (item_id, period),
            ).fetchall(),
            columns=["ts", "Prix", "Quantité vendue"],
        )
        if not frame.empty:
            frame["Date"] = _local_dates(frame.pop("ts"))
            history[title] = frame
    detail["history"] = history
    detail["market_seen_at"] = ws.prices.market_seen_at(item_id)
    # Cours reconstitué après un relevé, sinon cours relatif tiré des seuls prix moyens.
    detail["rebuilt"] = cours.rebuilt(conn, item_id)
    detail["relative"] = None
    if detail["market_seen_at"] is None:
        price, observed = moment_price(ws.prices, item_id)
        detail["relative"] = (cours.relative(conn, item_id, price), observed)

    detail["hdv"] = hdv_detail(conn, ws, item_id)

    recipe = ws.calculator.recipes.get(item_id)
    detail["craft"] = None
    if recipe is not None:
        result = ws.calculator.evaluate(recipe, ws.job_names.get(recipe.job_id, ""))
        rows = []
        for ingredient_id, quantity in recipe.ingredients:
            ingredient = ws.items.get(ingredient_id)
            price, source, age, lot = _ref_row(ws.prices.get(ingredient_id), ws.now)
            unit = ws.calculator.unit_cost(ingredient_id)
            rows.append(
                {
                    "item_id": ingredient_id,
                    "Ingrédient": ingredient.name if ingredient else f"#{ingredient_id}",
                    "Quantité": quantity,
                    "Prix unitaire": price,
                    "Source": source,
                    "Lot": lot,
                    "Âge (h)": age,
                    "Coût retenu": unit.cost,
                    "Mode": unit.mode if unit.mode else "prix manquant",
                    "Sous-total": unit.cost * quantity if unit.cost is not None else None,
                    "Échangeable": bool(ingredient and ingredient.exchangeable),
                }
            )
        detail["craft"] = {"result": result, "ingredients": pd.DataFrame(rows)}

    used_in = []
    for result_id, quantity in conn.execute(
        "SELECT result_id, quantity FROM recipe_ingredients WHERE item_id = ?", (item_id,)
    ):
        r = ws.calculator.evaluate(ws.calculator.recipes[result_id], ws.job_names.get(ws.calculator.recipes[result_id].job_id, ""))
        used_in.append(
            {
                "item_id": result_id,
                "Objet": r.item.name,
                "Métier": r.job,
                "Niveau": r.recipe.level,
                "Quantité utilisée": quantity,
                "Prix de vente": r.sell.price if r.sell else None,
                "Marge": r.recursive_margin,
            }
        )
    detail["used_in"] = pd.DataFrame(
        used_in, columns=["item_id", "Objet", "Métier", "Niveau", "Quantité utilisée", "Prix de vente", "Marge"]
    ).sort_values("Marge", ascending=False, na_position="last")
    return detail


def hdv_detail(conn: sqlite3.Connection, ws: Workspace, item_id: int) -> dict | None:
    """Dernières annonces HDV connues de l'item : lots pour une ressource, exemplaires pour un équipement."""
    rows = conn.execute(
        "SELECT p1, p10, p100, p1000, effects, captured_at FROM hdv_current WHERE item_id = ? ORDER BY p1", (item_id,)
    ).fetchall()
    if not rows:
        return None
    captured_at = max(row[5] for row in rows)
    if not ws.prices.is_equipment(item_id):
        lots = []
        for index, size in enumerate((1, 10, 100, 1000)):
            prices = [row[index] for row in rows if row[index] > 0]
            if prices:
                lots.append({"Lot": f"x{size}", "Prix du lot": min(prices), "Prix unitaire": min(prices) / size})
        return {"kind": "lots", "captured_at": captured_at, "frame": pd.DataFrame(lots)}

    fetched = conn.execute("SELECT 1 FROM item_effects_fetched WHERE item_id = ?", (item_id,)).fetchone() is not None
    template = {
        effect_id: (low, high)
        for effect_id, low, high in conn.execute(
            "SELECT effect_id, min_value, max_value FROM item_effects WHERE item_id = ?", (item_id,)
        )
    }
    names = dict(conn.execute("SELECT id, name FROM effects"))
    listings = []
    for p1, _, _, _, raw, _ in rows:
        effects = [tuple(e) for e in json.loads(raw)]
        verdict = classify(effects, template) if fetched else None

        def describe(ids: tuple[int, ...]) -> str:
            values = dict(effects)
            return ", ".join(f"{names.get(i, f'effet {i}')} {values[i]}" for i in ids)

        listings.append(
            {
                "Prix": p1,
                "Forgemagie": verdict.label if verdict else "inconnue",
                "Exo": describe(verdict.exo) if verdict else "",
                "Over": describe(verdict.over) if verdict else "",
                "Caractéristiques": ", ".join(
                    f"{names.get(i, f'effet {i}')} {v}" for i, v in effects if i not in MARKERS and v is not None
                ),
            }
        )
    frame = pd.DataFrame(listings)
    return {
        "kind": "listings",
        "captured_at": captured_at,
        "frame": frame,
        "template_known": fetched,
        "base": ", ".join(
            f"{names.get(i, f'effet {i}')} {low}" + (f" à {high}" if high != low else "")
            for i, (low, high) in template.items()
        ),
        "counts": frame["Forgemagie"].value_counts().to_dict(),
    }


def status(conn: sqlite3.Connection, now: float) -> dict:
    raw = dict(conn.execute("SELECT key, value FROM capture_status"))
    meta = dict(conn.execute("SELECT key, value FROM static_meta"))

    def ts(key: str) -> float | None:
        try:
            return float(raw[key])
        except (KeyError, ValueError):
            return None

    latest = conn.execute("SELECT ts FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()
    heartbeat = ts("heartbeat_ts")
    stopped = ts("stopped_ts")
    running = heartbeat is not None and now - heartbeat <= HEARTBEAT_STALE_S and stopped is None
    return {
        "running": running,
        "started_ts": ts("started_ts"),
        "stopped_ts": stopped,
        "heartbeat_ts": heartbeat,
        "last_connection_ts": ts("last_connection_ts"),
        "last_avg_prices_ts": ts("last_avg_prices_ts"),
        "decode_alert": raw.get("decode_alert", ""),
        "decode_alert_ts": ts("decode_alert_ts"),
        "awaiting_prices_since": ts("awaiting_prices_since"),
        "last_snapshot_ts": latest[0] if latest else None,
        "snapshots": conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
        "priced_items": conn.execute(
            "SELECT COUNT(*) FROM avg_prices WHERE snapshot_id = (SELECT id FROM snapshots ORDER BY ts DESC LIMIT 1)"
        ).fetchone()[0],
        "last_sales": conn.execute("SELECT COUNT(DISTINCT item_id) FROM last_sales").fetchone()[0],
        "history_items": conn.execute("SELECT COUNT(DISTINCT item_id) FROM market_history").fetchone()[0],
        "hdv_items": conn.execute("SELECT COUNT(DISTINCT item_id) FROM hdv_listings").fetchone()[0],
        "static_version": meta.get("version"),
        "static_imported_at": float(meta["imported_at"]) if "imported_at" in meta else None,
        "items": conn.execute("SELECT COUNT(*) FROM items").fetchone()[0],
        "recipes": conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0],
    }
