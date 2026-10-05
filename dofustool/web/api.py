"""Données servies à l'interface web, sous forme de dictionnaires sérialisables en JSON.

Aucun calcul métier ici : tout vient de dofustool.analysis et des fonctions déjà testées de
dofustool.app (data, forge). Ce module ne fait que mettre en forme et mettre en cache.
"""
import json
import logging
import math
import sqlite3
import threading
import time
from pathlib import Path

import pandas as pd

from .. import config, db
from ..analysis import GRAIN_DAY, GRAIN_HOUR
from ..analysis.forgemagie import Filter, base_lines
from ..analysis.stock import Stock, stock_crafts
from ..app import data, forge
from ..staticdata import effects as base_effects

log = logging.getLogger("dofustool.web")

# Grandes familles d'objets (items.category_id), pour les filtres de l'interface.
CATEGORIES = {0: "Équipements", 1: "Consommables", 2: "Ressources", 3: "Objets de quête", 4: "Autres", 5: "Cosmétiques"}


def data_stamp(conn: sqlite3.Connection) -> list:
    """Empreinte des données : change dès qu'une capture écrit du nouveau ou que la config est modifiée."""
    stamp = conn.execute(
        "SELECT (SELECT COUNT(*) FROM snapshots), (SELECT MAX(captured_at) FROM market_history), "
        "(SELECT MAX(captured_at) FROM hdv_listings), (SELECT COUNT(*) FROM hdv_listings), "
        "(SELECT COUNT(*) FROM item_effects_fetched), (SELECT value FROM static_meta WHERE key = 'imported_at'), "
        "(SELECT MAX(captured_at) FROM holdings_meta), "
        "(SELECT group_concat(key || '=' || value, '|') FROM capture_status "
        " WHERE key IN ('started_ts', 'stopped_ts', 'decode_alert'))"
    ).fetchone()
    cfg_mtime = config.CONFIG_PATH.stat().st_mtime if config.CONFIG_PATH.exists() else 0.0
    return [*stamp, cfg_mtime]


def records(frame: pd.DataFrame) -> list[dict]:
    """DataFrame -> liste de dictionnaires JSON (NaN devient null)."""
    return json.loads(frame.to_json(orient="records", force_ascii=False))


def clean(value):
    """Rend une valeur sérialisable : NaN et infinis deviennent null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


class Api:
    """Un objet par serveur. Les calculs lourds sont refaits seulement quand les données changent."""

    def __init__(self, db_path: Path | str = db.MARKET_PATH) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._stamp: list | None = None
        self._state: dict | None = None
        self._fetching = threading.Lock()
        self.fetch_effects = base_effects.fetch  # remplaçable dans les tests

    def ensure_template(self, conn: sqlite3.Connection, item_id: int) -> bool:
        """Récupère sur DofusDB les caractéristiques de base d'un item si elles manquent. Renvoie True si connues."""
        if forge.load_template(conn, item_id) is not None:
            return True
        try:
            base_effects.store(conn, item_id, self.fetch_effects(item_id))
            return True
        except Exception as exc:  # DofusDB injoignable : l'interface l'indique, on réessaiera
            log.warning("Caractéristiques de base de l'item %s non récupérées : %s", item_id, exc)
            return False

    def fetch_missing_templates(self) -> None:
        """En arrière-plan : complète les équipements vus à l'HDV. Un seul passage à la fois."""
        if not self._fetching.acquire(blocking=False):
            return

        def work() -> None:
            try:
                conn = self.connect()
                try:
                    for item_id in base_effects.missing_items(conn):
                        if not self.ensure_template(conn, item_id):
                            break  # inutile d'insister si le service ne répond pas
                        time.sleep(0.2)
                finally:
                    conn.close()
            finally:
                self._fetching.release()

        threading.Thread(target=work, daemon=True).start()

    def connect(self) -> sqlite3.Connection:
        return db.connect(self.db_path)

    def _load(self, conn: sqlite3.Connection) -> dict:
        stamp = data_stamp(conn)
        with self._lock:
            if self._state is None or stamp != self._stamp:
                cfg = config.load()
                ws = data.build_workspace(conn, cfg, time.time())
                trends, insufficient = data.trends_frame(conn, cfg, ws)
                stock = Stock(conn)
                crafts_from_stock = stock_crafts(conn, ws.calculator, stock)
                self._state = {
                    "stock": stock,
                    "stock_crafts": crafts_from_stock,
                    "craftable": {c.result.item.id: c.craftable for c in crafts_from_stock if c.craftable},
                    "cfg": cfg,
                    "ws": ws,
                    "crafts": data.crafts_frame(conn, ws),
                    "trends": trends,
                    "insufficient": insufficient,
                    "icons": dict(conn.execute("SELECT item_id, icon_id FROM item_icons")),
                    "meta": {
                        item_id: (type_name, CATEGORIES.get(category_id, "Autres"))
                        for item_id, type_name, category_id in conn.execute("SELECT id, type_name, category_id FROM items")
                    },
                    "effects": forge.effect_names(conn),
                    "assets": forge.effect_assets(conn),
                    "priorities": forge.effect_priorities(conn),
                    "recipe_uses": dict(conn.execute("SELECT item_id, COUNT(*) FROM recipe_ingredients GROUP BY item_id")),
                }
                self._stamp = stamp
            return self._state

    # --- pages ---------------------------------------------------------------

    def version(self) -> dict:
        conn = self.connect()
        try:
            return {"stamp": data_stamp(conn)}
        finally:
            conn.close()

    def status(self) -> dict:
        conn = self.connect()
        try:
            state = self._load(conn)
            cfg = state["cfg"]
            return clean(
                {
                    **data.status(conn, time.time()),
                    "now": time.time(),
                    "server_name": cfg.server_name,
                    "hdv_tax": cfg.hdv_tax,
                    "has_jobs": bool(cfg.jobs),
                    "unknown_jobs": state["ws"].unknown_jobs,
                    "trend_threshold": cfg.trend_threshold,
                    "min_snapshots_for_trend": cfg.min_snapshots_for_trend,
                    "min_liquidity": cfg.min_liquidity,
                }
            )
        finally:
            conn.close()

    def crafts(self) -> dict:
        conn = self.connect()
        try:
            state = self._load(conn)
            frame = state["crafts"]
            rows = records(frame) if not frame.empty else []
            for row in rows:
                row["icon"] = state["icons"].get(row["item_id"])
                row["craftable"] = state["craftable"].get(row["item_id"], 0)
            return {
                "rows": rows,
                "jobs": sorted(frame["Métier"].unique()) if not frame.empty else [],
                "stock_known": state["stock"].known,
            }
        finally:
            conn.close()

    def trends(self) -> dict:
        conn = self.connect()
        try:
            state = self._load(conn)
            rows = records(state["trends"]) if not state["trends"].empty else []
            for row in rows:
                row["icon"] = state["icons"].get(row["item_id"])
                row["type"], row["category"] = state["meta"].get(row["item_id"], (None, "Autres"))
                row["recipes"] = state["recipe_uses"].get(row["item_id"], 0)  # nombre de recettes qui l'utilisent
            return {"rows": rows, "insufficient": state["insufficient"]}
        finally:
            conn.close()

    def items(self) -> dict:
        """Objets consultables, pour la recherche."""
        conn = self.connect()
        try:
            state = self._load(conn)
            rows = conn.execute(
                "SELECT id, name, level FROM items WHERE id IN ("
                " SELECT item_id FROM avg_prices UNION SELECT result_id FROM recipes"
                " UNION SELECT item_id FROM recipe_ingredients UNION SELECT item_id FROM last_sales) ORDER BY name"
            ).fetchall()
            return {"items": [[i, name, level, state["icons"].get(i)] for i, name, level in rows]}
        finally:
            conn.close()

    def item(self, item_id: int) -> dict | None:
        conn = self.connect()
        try:
            state = self._load(conn)
            ws = state["ws"]
            if item_id not in ws.items:
                return None
            detail = data.item_detail(conn, ws, item_id)
            item, ref, liquidity, cost = detail["item"], detail["ref"], detail["liquidity"], detail["unit_cost"]

            def series(period: str) -> list:
                return conn.execute(
                    "SELECT bucket_ts, price, qty_sold FROM market_history WHERE item_id = ? AND period = ? "
                    "ORDER BY bucket_ts",
                    (item_id, period),
                ).fetchall()

            craft = None
            if detail["craft"] is not None:
                result = detail["craft"]["result"]
                ingredients = records(detail["craft"]["ingredients"])
                for row in ingredients:
                    row["icon"] = state["icons"].get(row["item_id"])
                    row["have"] = state["stock"].get(row["item_id"]).total
                craft = {
                    "craftable": state["craftable"].get(item_id, 0),
                    "job": result.job,
                    "level": result.recipe.level,
                    "own_job": result.own_job,
                    "revenue": result.revenue,
                    "cost": result.recursive_cost,
                    "margin": result.recursive_margin,
                    "margin_pct": result.margin_pct,
                    "flags": result.flags,
                    "ingredients": ingredients,
                }
            used_in = records(detail["used_in"])
            for row in used_in:
                row["icon"] = state["icons"].get(row["item_id"])
            hdv = detail["hdv"]
            hdv_unit = None
            if hdv is not None:
                if hdv["kind"] == "lots" and not hdv["frame"].empty:
                    hdv_unit = float(hdv["frame"]["Prix unitaire"].min())
                hdv = {**hdv, "frame": records(hdv["frame"])}
            avg = conn.execute(
                "SELECT p.price, s.ts FROM avg_prices p JOIN snapshots s ON s.id = p.snapshot_id "
                "WHERE p.item_id = ? ORDER BY s.ts DESC LIMIT 1",
                (item_id,),
            ).fetchone()
            return clean(
                {
                    "id": item_id,
                    "name": item.name,
                    "level": item.level,
                    "type": detail["type"],
                    "category": state["meta"].get(item_id, (None, "Autres"))[1],
                    "avg_price": {"price": avg[0], "ts": avg[1]} if avg else None,
                    "owned": {
                        "inventory": state["stock"].get(item_id).inventory,
                        "bank": state["stock"].get(item_id).bank,
                        "known": state["stock"].known,
                    },
                    "hdv_unit": hdv_unit,
                    "exchangeable": item.exchangeable,
                    "equipment": ws.prices.is_equipment(item_id),
                    "icon": state["icons"].get(item_id),
                    "ref": {"price": ref.price, "source": ref.source, "ts": ref.ts} if ref else None,
                    "unit_cost": {"cost": cost.cost, "mode": cost.mode},
                    "qty_24h": liquidity.qty_24h,
                    "qty_7d": liquidity.qty_7d,
                    "market_seen_at": detail["market_seen_at"],
                    "hourly": series(GRAIN_HOUR),
                    "daily": series(GRAIN_DAY),
                    "snapshots": conn.execute(
                        "SELECT s.ts, p.price FROM avg_prices p JOIN snapshots s ON s.id = p.snapshot_id "
                        "WHERE p.item_id = ? ORDER BY s.ts",
                        (item_id,),
                    ).fetchall(),
                    "hdv": hdv,
                    "craft": craft,
                    "used_in": used_in,
                    "now": ws.now,
                }
            )
        finally:
            conn.close()

    # --- stock ---------------------------------------------------------------

    def stock(self) -> dict:
        """Objets possédés (inventaire et banque), valorisés au prix de référence."""
        conn = self.connect()
        try:
            state = self._load(conn)
            ws, stock = state["ws"], state["stock"]
            rows = []
            for item_id, owned in stock.items().items():
                item = ws.items.get(item_id)
                ref = ws.prices.get(item_id) if item is not None and item.exchangeable else None
                type_name, category = state["meta"].get(item_id, (None, "Autres"))
                rows.append(
                    {
                        "item_id": item_id,
                        "name": item.name if item else f"#{item_id}",
                        "level": item.level if item else None,
                        "icon": state["icons"].get(item_id),
                        "type": type_name,
                        "category": category,
                        "inventory": owned.inventory,
                        "bank": owned.bank,
                        "total": owned.total,
                        "price": ref.price if ref else None,
                        "source": ref.source if ref else None,
                        "value": ref.price * owned.total if ref else None,
                        "recipes": state["recipe_uses"].get(item_id, 0),
                    }
                )
            rows.sort(key=lambda r: -(r["value"] or 0))
            return clean({"rows": rows, "meta": stock.meta, "bank_inferred": stock.bank_inferred, "now": ws.now})
        finally:
            conn.close()

    def stock_crafts(self) -> dict:
        """Recettes dont au moins un ingrédient est possédé, avec ce qui est disponible et ce qui manque."""
        conn = self.connect()
        try:
            state = self._load(conn)
            ws = state["ws"]
            rows = []
            for c in state["stock_crafts"]:
                r = c.result
                rows.append(
                    {
                        "item_id": r.item.id,
                        "name": r.item.name,
                        "icon": state["icons"].get(r.item.id),
                        "job": r.job,
                        "level": r.recipe.level,
                        "own_job": r.own_job,
                        "sell": r.sell.price if r.sell else None,
                        "source": r.sell.source if r.sell else None,
                        "margin": r.recursive_margin,
                        "craftable": c.craftable,
                        "total_margin": c.total_margin,
                        "covered": c.covered,
                        "lines": len(c.ingredients),
                        "missing_cost": c.missing_cost,
                        "flags": r.flags,
                        "ingredients": [
                            {
                                "id": item_id,
                                "name": ws.items[item_id].name if item_id in ws.items else f"#{item_id}",
                                "icon": state["icons"].get(item_id),
                                "need": need,
                                "have": have,
                            }
                            for item_id, need, have in c.ingredients
                        ],
                    }
                )
            stock = state["stock"]
            return clean(
                {
                    "rows": rows,
                    "jobs": sorted({row["job"] for row in rows}),
                    "meta": stock.meta,
                    "bank_inferred": stock.bank_inferred,
                    "has_jobs": bool(state["cfg"].jobs),
                    "now": ws.now,
                }
            )
        finally:
            conn.close()

    # --- forgemagie ----------------------------------------------------------

    def forge_options(self) -> dict:
        conn = self.connect()
        try:
            state = self._load(conn)
            if base_effects.missing_items(conn):
                self.fetch_missing_templates()
            fetched = {row[0] for row in conn.execute("SELECT item_id FROM item_effects_fetched")}
            rows = conn.execute(
                "SELECT i.id, i.name, i.level, COUNT(*) FROM hdv_current h JOIN items i ON i.id = h.item_id "
                "WHERE i.category_id = 0 GROUP BY i.id ORDER BY i.name"
            ).fetchall()
            return {
                "items": [
                    {"id": i, "name": name, "level": level, "count": count, "icon": state["icons"].get(i),
                     "template_known": i in fetched, "type": state["meta"].get(i, (None, None))[0]}
                    for i, name, level, count in rows
                ]
            }  # fmt: skip
        finally:
            conn.close()

    def forge_item(self, item_id: int) -> dict | None:
        """Tout ce qu'il faut pour filtrer les annonces d'un équipement côté navigateur."""
        conn = self.connect()
        try:
            state = self._load(conn)
            ws, names = state["ws"], state["effects"]
            if item_id not in ws.items:
                return None
            item = ws.items[item_id]
            head = {"id": item_id, "name": item.name, "level": item.level, "icon": state["icons"].get(item_id)}
            if ws.prices.is_equipment(item_id):
                self.ensure_template(conn, item_id)  # premier affichage d'un équipement tout juste vu à l'HDV
            template = forge.load_template(conn, item_id)
            if template is None:
                return {**head, "template_known": False}

            def listing(entry) -> dict:
                price, c, first_seen, last_seen = entry
                return {
                    "price": price, "label": c.label, "plain": c.plain,
                    "quality": round(c.quality * 100) if c.quality is not None else None,
                    "transcended": c.transcended,
                    "values": c.values, "exo": list(c.exo), "over": list(c.over), "missing": list(c.missing),
                    "first_seen": first_seen, "last_seen": last_seen,
                }  # fmt: skip

            current = forge.read_listings(conn, item_id, template)
            latest = max((entry[3] for entry in current), default=None)
            everything = forge.read_listings(conn, item_id, template, current=False)
            fixed = forge.load_fixed_lines(conn, item_id)
            used = {i for entry in everything for i in entry[1].values} | set(template) | set(fixed)
            return clean(
                {
                    **head,
                    "template_known": True,
                    "captured_at": latest,
                    "now": ws.now,
                    "tax": ws.calculator.tax,
                    "craft_cost": forge.craft_cost(ws, item_id),
                    "lines": [
                        {"id": effect_id, "name": names.get(effect_id, f"effet {effect_id}"), "min": low, "max": high}
                        for effect_id, (low, high) in base_lines(template).items()
                    ],
                    # Lignes de base non forgemageables (dégâts d'une arme…), affichées telles quelles.
                    "fixed": [
                        {"id": effect_id, "name": names.get(effect_id, f"effet {effect_id}"), "min": low, "max": high}
                        for effect_id, (low, high) in fixed.items()
                    ],
                    "assets": {i: state["assets"][i] for i in used if i in state["assets"]},
                    "order": sorted(used, key=lambda i: (state["priorities"].get(i, 1_000_000), i)),
                    "exos": [
                        {"id": effect_id, "name": names.get(effect_id, f"effet {effect_id}"), "count": count}
                        for effect_id, count in forge.exo_counts(current).items()
                    ],
                    "names": {i: names.get(i, f"effet {i}") for i in used},
                    "template": {effect_id: [low, high] for effect_id, (low, high) in template.items()},
                    "listings": [listing(entry) for entry in current],
                    "gone": [listing(entry) for entry in everything if latest is not None and entry[3] < latest],
                    "filter": db.load_fm_filter(conn, item_id),
                }
            )
        finally:
            conn.close()

    def save_forge_filter(self, item_id: int, config_dict: dict) -> dict:
        conn = self.connect()
        try:
            flt = Filter.from_config(config_dict)  # valide et normalise ce qui vient du navigateur
            db.save_fm_filter(conn, item_id, flt.to_config())
            return {"saved": flt.to_config()}
        finally:
            conn.close()

    def forge_ranking(
        self, criterion: str, exo: int | None = None, effect: int | None = None, amount: int | None = None
    ) -> dict:
        """criterion : « saved » (critères de chaque objet), « exo », ou « over » (effect + amount)."""
        conn = self.connect()
        try:
            state = self._load(conn)
            names = state["effects"]
            mode = {"saved": forge.SAVED, "exo": forge.EXO, "over": forge.OVER}.get(criterion, forge.SAVED)
            over = (effect, max(1, amount or 1)) if effect else None
            frame = forge.ranking(conn, state["ws"], mode, exo, over)
            rows = records(frame) if not frame.empty else []
            for row in rows:
                row["icon"] = state["icons"].get(row["item_id"])
                row["type"] = state["meta"].get(row["item_id"], (None, None))[0]
            return {
                "rows": rows,
                "exos": [
                    {"id": effect_id, "name": names.get(effect_id, f"effet {effect_id}"), "count": count}
                    for effect_id, count in forge.all_exo_counts(conn).items()
                ],
                "lines": [
                    {"id": effect_id, "name": names.get(effect_id, f"effet {effect_id}"), "count": count}
                    for effect_id, count in forge.base_line_counts(conn).items()
                ],
                "known": len(forge.equipment_options(conn)),
            }
        finally:
            conn.close()
