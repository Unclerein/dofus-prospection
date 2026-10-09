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
from datetime import date, timedelta
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from .. import ankama, config, db, ganymede
from ..analysis import GRAIN_DAY, GRAIN_HOUR, cours, fmjournal, similar, workshop
from ..analysis.forgemagie import HUNTING_RUNE, MARKERS, WEAPON_TYPES, Filter, base_lines, classify
from ..analysis import jobxp
from ..analysis.prices import PriceRef
from ..analysis.stock import Stock, stock_crafts
from ..app import data, forge
from ..share import client as share_client
from ..fight import grid as fight_grid, harebourg
from ..staticdata import almanax as almanax_source
from ..staticdata import effects as base_effects

log = logging.getLogger("dofustool.web")

# Grandes familles d'objets (items.category_id), pour les filtres de l'interface.
CATEGORIES = {0: "Équipements", 1: "Consommables", 2: "Ressources", 3: "Objets de quête", 4: "Autres", 5: "Cosmétiques"}


# Métier de forgemagie de chaque métier de fabrication (identifiants de la table jobs).
MAGE_OF = {11: 44, 13: 48, 15: 62, 16: 63, 27: 64, 60: 74}

# Le métier « Base » (recettes sans métier) n'a pas de niveau : il n'est pas proposé dans la configuration.
BASE_JOB_ID = 1


def data_stamp(conn: sqlite3.Connection, config_path: Path = config.CONFIG_PATH) -> list:
    """Empreinte des données : change dès qu'une capture écrit du nouveau ou que la config est modifiée."""
    stamp = conn.execute(
        "SELECT (SELECT COUNT(*) FROM snapshots), (SELECT MAX(captured_at) FROM market_history), "
        "(SELECT MAX(captured_at) FROM hdv_listings), (SELECT COUNT(*) FROM hdv_listings), "
        # Nombre de fiches, et date du dernier lot de fiches rafraîchies (qui change les calculs, pas leur nombre).
        "(SELECT COUNT(*) || ':' || COALESCE((SELECT value FROM template_meta WHERE key = 'refreshed_at'), '') FROM item_effects_fetched), "
        "(SELECT value FROM static_meta WHERE key = 'imported_at'), "
        "(SELECT MAX(captured_at) FROM holdings_meta), "
        # Somme des niveaux, pas la date du relevé : un gain d'expérience ne doit pas tout recalculer.
        "(SELECT COUNT(*) FROM characters), (SELECT SUM(level) FROM character_jobs), "
        "(SELECT MAX(captured_at) FROM my_sales_meta), (SELECT COUNT(*) FROM my_sales), (SELECT COUNT(*) FROM trades), "
        "(SELECT group_concat(key || '=' || value, '|') FROM capture_status "
        " WHERE key IN ('started_ts', 'stopped_ts', 'decode_alert'))"
    ).fetchone()
    cfg_mtime = config_path.stat().st_mtime if config_path.exists() else 0.0
    return [*stamp, cfg_mtime]


# Positions, dans l'empreinte, de ce qui ne change pas les calculs lourds (prix, crafts, tendances) :
# le stock (6), mes lots en vente (9, 10), mes ventes et achats (11), l'état de la capture (12).
# Un mouvement d'inventaire arrive toutes les quelques secondes en jeu : il ne doit pas tout faire recalculer.
STOCK_POSITION = 6
LIGHT_POSITIONS = frozenset({STOCK_POSITION, 9, 10, 11, 12})


def core_stamp(stamp: list) -> tuple:
    """L'empreinte réduite à ce dont dépendent les prix, les crafts et les tendances."""
    return tuple(value for position, value in enumerate(stamp) if position not in LIGHT_POSITIONS)


def local_addresses() -> list[dict]:
    """Adresses de ce PC sur ses réseaux, pour indiquer aux amis où joindre le hub."""
    import ipaddress
    import socket

    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = ipaddress.ip_address(info[4][0])
            if address.is_loopback or any(str(address) == f["ip"] for f in found):
                continue
            # 100.64.0.0/10 : plage des réseaux privés de type Tailscale.
            found.append({"ip": str(address), "tailscale": address in ipaddress.ip_network("100.64.0.0/10")})
    except OSError:
        pass
    return sorted(found, key=lambda f: not f["tailscale"])


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


# Intervalle entre deux passages en arrière-plan sur les fiches des équipements (périmées, contredites).
TEMPLATES_EVERY_S = 600.0
# Atelier : au-delà, l'annonce la moins chère d'un équipement est trop vieille pour servir de prix d'achat.
WORKSHOP_LISTING_MAX_AGE_S = 48 * 3600.0
TEMPLATES_BATCH = 100  # fiches rafraîchies avant de faire recalculer les pages


class Api:
    background_refresh = True  # coupé dans les tests : pas de fil en arrière-plan ni d'appel réseau

    """Un objet par serveur. Les calculs lourds sont refaits seulement quand les données changent."""

    def __init__(self, db_path: Path | str = db.MARKET_PATH, config_path: Path = config.CONFIG_PATH) -> None:
        self.db_path = db_path
        self.config_path = config_path
        self._lock = threading.Lock()
        self._stamp: list | None = None
        self._state: dict | None = None
        self._fetching = threading.Lock()
        self.ganymede_dir: Path | None = None  # None : le dossier de données de Ganymède sur ce PC ; remplacé dans les tests
        self._templates_checked = 0.0  # dernier passage en arrière-plan sur les fiches des équipements
        self._ranking_stamp: tuple | None = None
        self._ranking_cache: dict[tuple, dict] = {}
        self._ranking_loaded: dict | None = None  # annonces lues de tous les équipements, pour la même empreinte
        self.fetch_effects = base_effects.fetch  # remplaçable dans les tests
        self.fetch_almanax = almanax_source.fetch

    def ensure_template(self, conn: sqlite3.Connection, item_id: int, refresh: bool = False) -> bool:
        """Récupère sur DofusDB les caractéristiques de base d'un item si elles manquent. Renvoie True si connues.

        refresh : les redemander même si elles sont connues (fiche périmée) ; renvoie alors False en cas d'échec.
        """
        if not refresh and forge.load_template(conn, item_id) is not None:
            return True
        try:
            base_effects.store(conn, item_id, self.fetch_effects(item_id))
            return True
        except Exception as exc:  # DofusDB injoignable : l'interface l'indique, on réessaiera
            log.warning("Caractéristiques de base de l'item %s non récupérées : %s", item_id, exc)
            return False

    def refresh_templates(self, conn: sqlite3.Connection, pause: float = 0.2) -> int:
        """Demande à DofusDB les fiches manquantes, contredites par les annonces ou périmées. Renvoie leur nombre.

        La version du jeu connue de DofusDB est vérifiée de temps en temps : si elle a changé, toutes les
        fiches sont à relire. Une fiche périmée continue de servir tant que la nouvelle n'est pas arrivée.
        """
        now = time.time()
        if base_effects.version_check_due(conn, now):
            try:
                if base_effects.note_version(conn, base_effects.fetch_version(), now):
                    log.info("Version du jeu connue de DofusDB changée : les fiches des équipements sont rafraîchies.")
            except Exception as exc:  # DofusDB injoignable : les fiches manquantes sont quand même tentées
                log.warning("Version de DofusDB non lue : %s", exc)
        missing = set(base_effects.missing_items(conn))
        done = refreshed = 0
        for item_id in base_effects.pending_items(conn, now):
            if not self.ensure_template(conn, item_id, refresh=item_id not in missing):
                break  # inutile d'insister si le service ne répond pas
            done += 1
            refreshed += item_id not in missing
            # Les pages ne sont recalculées qu'une fois par lot de fiches rafraîchies, pas à chacune.
            if refreshed and refreshed % TEMPLATES_BATCH == 0:
                base_effects.mark_refreshed(conn)
            time.sleep(pause)
        if refreshed % TEMPLATES_BATCH:
            base_effects.mark_refreshed(conn)
        return done

    def fetch_missing_templates(self) -> None:
        """En arrière-plan : complète et rafraîchit les fiches des équipements vus à l'HDV. Un seul passage à la fois."""
        if not self._fetching.acquire(blocking=False):
            return
        self._templates_checked = time.time()

        def work() -> None:
            try:
                conn = self.connect()
                try:
                    self.refresh_templates(conn)
                finally:
                    conn.close()
            finally:
                self._fetching.release()

        threading.Thread(target=work, daemon=True).start()

    def connect(self) -> sqlite3.Connection:
        return db.connect(self.db_path)

    def _load(self, conn: sqlite3.Connection) -> dict:
        stamp = data_stamp(conn, self.config_path)
        with self._lock:
            fresh = self._state is not None and self._stamp is not None
            if fresh and stamp != self._stamp and core_stamp(stamp) == core_stamp(self._stamp):
                # Seuls le stock, mes lots ou mon journal ont bougé : les prix et les crafts restent valables.
                if stamp[STOCK_POSITION] != self._stamp[STOCK_POSITION]:
                    stock = Stock(conn)
                    crafts_from_stock = stock_crafts(conn, self._state["ws"].calculator, stock)
                    self._state = {
                        **self._state, "stock": stock, "stock_crafts": crafts_from_stock,
                        "craftable": {c.result.item.id: c.craftable for c in crafts_from_stock if c.craftable},
                    }  # fmt: skip
                self._stamp = stamp
            if self._state is None or stamp != self._stamp:
                cfg = config.load(self.config_path)
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

    def _hidden(self, conn: sqlite3.Connection, state: dict):
        """Renvoie une fonction disant si un item est masqué : ignoré lui-même, ou par son type."""
        items = db.ignored_items(conn)
        types = db.ignored_types(conn)
        meta = state["meta"]
        return lambda item_id: item_id in items or meta.get(item_id, (None, None))[0] in types

    @staticmethod
    def _estimate(ws, item_id: int) -> dict | None:
        guess = ws.prices.estimate(item_id)
        if guess is None:
            return None
        return {
            "price": guess.price,
            "avg": guess.avg,
            "delta": guess.delta,
            "hours": guess.hours,
            "confidence": guess.confidence,
            "spread": ws.prices.estimate_spread(guess.confidence),
            "ts": guess.ts,
        }

    @staticmethod
    def _rolls(c, price) -> dict:
        """Lecture d'un exemplaire, telle que l'infobulle de l'interface l'attend."""
        return {
            "price": price, "label": c.label, "plain": c.plain,
            "quality": round(c.quality * 100) if c.quality is not None else None,
            "transcended": c.transcended, "hunting": c.hunting,
            "values": c.values, "exo": list(c.exo), "over": list(c.over), "missing": list(c.missing),
        }  # fmt: skip

    def _similar(self, conn: sqlite3.Connection, item_id: int, mine, template, ignored, own_price: int | None = None) -> dict | None:
        """Valeur d'un exemplaire d'après les annonces HDV similaires du même modèle, ou None s'il n'y en a aucune."""
        listings, captured_at = similar.load_listings(conn, item_id, template, ignored)
        if own_price is not None:
            listings = similar.without_own(listings, own_price, mine)
        found = similar.estimate(mine, listings, template, captured_at) if listings else None
        if found is None:
            return None
        return {
            "price": found.price, "confidence": found.confidence, "count": found.count, "floor": found.floor,
            "captured_at": found.captured_at, "listing": self._rolls(found.listing, found.price), "listings": len(listings),
        }  # fmt: skip

    # --- pages ---------------------------------------------------------------

    def version(self) -> dict:
        conn = self.connect()
        try:
            # Le journal de forgemagie change à chaque rune : l'interface le recharge au plus une fois par
            # minute, et ces compteurs restent hors de l'empreinte qui déclenche le recalcul des pages.
            journal = conn.execute(
                "SELECT (SELECT COUNT(*) FROM fm_items), (SELECT CAST(MAX(ts) / 60 AS INTEGER) FROM fm_passes), "
                "(SELECT COUNT(*) FROM offline_sales), (SELECT COUNT(*) FROM fm_items WHERE base_cost IS NOT NULL OR sold_at IS NOT NULL), "
                "(SELECT COUNT(*) FROM exchanges), (SELECT COUNT(*) FROM crafts)"
            ).fetchone()
            return {"stamp": [*data_stamp(conn, self.config_path), *journal]}
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
                    "use_estimated_prices": cfg.use_estimated_prices,
                    "equipment_price": cfg.equipment_price,
                    "onboarded": cfg.onboarded,
                    "share": share_client.status(conn, cfg),
                    "estimates": {
                        "count": len(state["ws"].prices.estimates),
                        "reliable": sum(1 for e in state["ws"].prices.estimates.values() if e.confidence == "fiable"),
                        "check": state["ws"].prices.estimate_check,
                    },
                    "cours_check": cours.check(conn),
                    # Quantités possédées, pour le badge des icônes : {item_id: [inventaire, banque]}.
                    "owned": {item_id: [o.inventory, o.bank, o.havre] for item_id, o in state["stock"].items().items()},
                }
            )
        finally:
            conn.close()

    def crafts(self) -> dict:
        conn = self.connect()
        try:
            state = self._load(conn)
            frame = state["crafts"]
            hidden = self._hidden(conn, state)
            rows = [row for row in (records(frame) if not frame.empty else []) if not hidden(row["item_id"])]
            prices = state["ws"].prices
            for row in rows:
                row["icon"] = state["icons"].get(row["item_id"])
                row["craftable"] = state["craftable"].get(row["item_id"], 0)
                row["equipment"] = prices.is_equipment(row["item_id"])
                row["avg"] = prices.avg_price(row["item_id"]) if row["equipment"] else None
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
            hidden = self._hidden(conn, state)
            rows = [
                row
                for row in (records(state["trends"]) if not state["trends"].empty else [])
                if not hidden(row["item_id"])
            ]
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
            hdv_unit = hdv_lot = None
            if hdv is not None:
                if hdv["kind"] == "lots" and not hdv["frame"].empty:
                    best = hdv["frame"].loc[hdv["frame"]["Prix unitaire"].idxmin()]  # à égalité, le plus petit lot
                    hdv_unit, hdv_lot = float(best["Prix unitaire"]), int(best["Lot"].lstrip("x"))
                hdv = {
                    **hdv,
                    "frame": records(hdv["frame"]),
                    # Mes lots en vente : {taille du lot: prix le plus bas}, pour les reconnaître dans les annonces.
                    "mine": dict(conn.execute("SELECT lot, MIN(price) FROM my_sales WHERE item_id = ? GROUP BY lot", (item_id,))),
                }
            rebuilt = detail["rebuilt"]
            if rebuilt is not None and rebuilt.processed_ts > rebuilt.base_at:
                rebuilt = {
                    "processed_ts": rebuilt.processed_ts,
                    "blind": rebuilt.blind,
                    "min_qty": rebuilt.min_qty,
                    "hourly": [[p.end, p.price, p.qty, p.sure, p.hours] for p in rebuilt.points if p.qty],
                    "daily": cours.daily(conn, item_id, rebuilt),
                }
            else:
                rebuilt = None
            relative = None
            if detail["relative"] is not None and detail["relative"][0] is not None:
                found, observed = detail["relative"]
                relative = {
                    "observed": observed,
                    "pace": found.pace if observed else None,
                    "hours": found.hours,
                    "moved": found.moved,
                    "skipped": found.skipped,
                    "points": [[end, hours, share * 100] for end, hours, share in found.points],
                }
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
                    "ignored": item_id in db.ignored_items(conn),
                    "type_ignored": state["meta"].get(item_id, (None, None))[0] in db.ignored_types(conn),
                    "owned": {
                        "inventory": state["stock"].get(item_id).inventory,
                        "bank": state["stock"].get(item_id).bank,
                        "havre": state["stock"].get(item_id).havre,
                        "known": state["stock"].known,
                    },
                    "hdv_unit": hdv_unit,
                    "hdv_lot": hdv_lot,
                    "exchangeable": item.exchangeable,
                    "equipment": ws.prices.is_equipment(item_id),
                    "icon": state["icons"].get(item_id),
                    "ref": {"price": ref.price, "source": ref.source, "ts": ref.ts, "lot": ref.lot, "spread": ref.spread}
                    if ref
                    else None,
                    "estimate": self._estimate(ws, item_id),
                    "unit_cost": {"cost": cost.cost, "mode": cost.mode},
                    "qty_24h": liquidity.qty_24h,
                    "qty_7d": liquidity.qty_7d,
                    "market_seen_at": detail["market_seen_at"],
                    "rebuilt": rebuilt,
                    "relative": relative,
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

    # --- configuration -------------------------------------------------------

    def find_launcher(self, configured: str = "") -> dict:
        """Cherche le launcher Ankama sur ce PC (registre, dossiers habituels, disques). Rien n'est enregistré."""
        found = ankama.find(configured)
        return {"path": str(found) if found else None}

    def config(self) -> dict:
        """Contenu de config.toml, et de quoi le remplir : métiers du jeu, personnages vus par la capture."""
        conn = self.connect()
        try:
            cfg = config.load(self.config_path)
            names = dict(conn.execute("SELECT id, name FROM jobs WHERE id != ? ORDER BY name", (BASE_JOB_ID,)))
            characters = []
            for character_id, name, level in conn.execute("SELECT id, name, level FROM characters ORDER BY name"):
                captured = db.character_jobs(conn, character_id)
                characters.append(
                    {
                        "id": character_id,
                        "name": name,
                        "level": level,
                        "jobs": {names[j]: lvl for j, (lvl, _, _) in captured.items() if j in names},
                        "jobs_at": max((ts for _, _, ts in captured.values()), default=None),
                    }
                )
            return {
                "values": asdict(cfg),
                "jobs": list(names.values()),
                "characters": characters,
                "path": str(self.config_path),
                "addresses": local_addresses(),
                "now": time.time(),
            }
        finally:
            conn.close()

    def set_equipment_price(self, mode: str) -> dict:
        """Change le prix de vente retenu pour les équipements (réglage de config.toml)."""
        import dataclasses

        if mode not in ("both", "base", "any", "avg"):
            raise ValueError("mode inconnu")
        config.save(dataclasses.replace(config.load(self.config_path), equipment_price=mode), self.config_path)
        return {"equipment_price": mode}

    def save_config(self, values: dict) -> dict:
        """Valide les valeurs saisies et réécrit config.toml. ValueError si l'une est invalide."""
        config.save(config.from_values(values), self.config_path)
        return self.config()

    def item_tip(self, item_id: int) -> dict | None:
        """De quoi afficher l'infobulle d'un objet : ses caractéristiques de base et ses prix connus."""
        conn = self.connect()
        try:
            state = self._load(conn)
            ws, names = state["ws"], state["effects"]
            item = ws.items.get(item_id)
            if item is None:
                return None
            equipment = ws.prices.is_equipment(item_id)
            lines, fixed, known = [], [], True
            if equipment:
                known = self.ensure_template(conn, item_id)
                rank = lambda effect_id: (state["priorities"].get(effect_id, 1_000_000), effect_id)  # noqa: E731
                line = lambda effect_id, bounds: {  # noqa: E731
                    "name": names.get(effect_id, f"effet {effect_id}"), "asset": state["assets"].get(effect_id),
                    "min": bounds[0], "max": bounds[1],
                }
                template = forge.load_template(conn, item_id) or {}
                lines = [line(e, template[e]) for e in sorted(template, key=rank)]
                fixed_lines = forge.load_fixed_lines(conn, item_id)
                fixed = [line(e, fixed_lines[e]) for e in sorted(fixed_lines, key=rank)]
            ref = ws.prices.get(item_id) if item.exchangeable else None
            base, cheapest = ws.prices.hdv_price(item_id), ws.prices.hdv_any(item_id)
            return clean(
                {
                    "id": item_id,
                    "name": item.name,
                    "level": item.level,
                    "type": state["meta"].get(item_id, (None, None))[0],
                    "icon": state["icons"].get(item_id),
                    "equipment": equipment,
                    "template_known": known,
                    "lines": lines,
                    "fixed": fixed,
                    "avg": ws.prices.avg_price(item_id),
                    "ref": {"price": ref.price, "source": ref.source, "lot": ref.lot, "ts": ref.ts} if ref else None,
                    "hdv": {"price": base[0], "lot": base[3], "ts": base[2]} if base else None,
                    "hdv_any": {"price": cheapest[0], "ts": cheapest[2]} if cheapest else None,
                    "craft_cost": forge.craft_cost(ws, item_id),
                    "now": ws.now,
                }
            )
        finally:
            conn.close()

    # --- métiers -------------------------------------------------------------

    def jobs(self) -> dict:
        """Métiers qui ont des recettes, avec le niveau de la configuration et l'expérience relevée."""
        conn = self.connect()
        try:
            state = self._load(conn)
            cfg = state["cfg"]
            levels = {name.casefold(): level for name, level in cfg.jobs.items()}
            captured = db.character_jobs(conn, cfg.character_id) if cfg.character_id else {}
            rows = conn.execute(
                "SELECT j.id, j.name, COUNT(*), MAX(r.level) FROM recipes r JOIN jobs j ON j.id = r.job_id "
                "GROUP BY j.id ORDER BY j.name"
            ).fetchall()
            jobs = []
            for job_id, name, count, top in rows:
                level = levels.get(name.casefold())
                seen = captured.get(job_id)
                # L'expérience relevée ne vaut que si elle correspond au niveau enregistré.
                xp = seen[1] if seen is not None and seen[0] == level else None
                jobs.append({"id": job_id, "name": name, "recipes": count, "max_level": top, "level": level, "xp": xp})
            return {"jobs": jobs}
        finally:
            conn.close()

    def job_plan(
        self,
        job_id: int,
        start_xp: int,
        target_level: int,
        bonus_pct: float = 100.0,
        resale: bool = False,
        exclude: frozenset[int] = frozenset(),
    ) -> dict:
        """Chemin le moins coûteux pour monter un métier, et la liste de courses qui va avec."""
        conn = self.connect()
        try:
            state = self._load(conn)
            ws, calc, stock = state["ws"], state["ws"].calculator, state["stock"]
            hidden = self._hidden(conn, state)
            ratios = dict(conn.execute("SELECT result_id, ratio_pct FROM recipe_xp"))
            candidates, results, skipped = [], {}, 0
            for recipe in calc.recipes.values():
                if recipe.job_id != job_id or not recipe.ingredients:
                    continue
                if recipe.result_id in exclude or hidden(recipe.result_id):
                    continue
                result = calc.evaluate(recipe)
                # Coût d'un craft : tout acheter ; à défaut (ingrédient non achetable), le moins cher possible.
                cost = result.direct_cost if result.direct_cost is not None else result.recursive_cost
                if cost is None:
                    skipped += 1
                    continue
                if resale and result.revenue is not None:
                    cost -= result.revenue
                results[recipe.result_id] = (recipe, result)
                candidates.append(jobxp.Candidate(recipe.result_id, recipe.level, ratios.get(recipe.result_id, 100), cost))

            plan = jobxp.cheapest_path(candidates, start_xp, target_level, bonus_pct)
            shopping: dict[int, int] = {}
            steps = []
            for step in plan.steps:
                recipe, result = results[step.item_id]
                for item_id, quantity in recipe.ingredients:
                    shopping[item_id] = shopping.get(item_id, 0) + quantity * step.crafts
                steps.append(
                    {
                        "item_id": step.item_id,
                        "name": result.item.name,
                        "icon": state["icons"].get(step.item_id),
                        "level": recipe.level,
                        "from_level": step.from_level,
                        "to_level": step.to_level,
                        "crafts": step.crafts,
                        "xp": step.xp,
                        "cost": step.cost,
                        "unit_cost": step.cost / step.crafts,
                        "sell": result.sell.price if result.sell else None,
                        "source": result.sell.source if result.sell else None,
                        "lot": result.sell.lot if result.sell else None,
                        "ratio_pct": ratios.get(step.item_id, 100),
                        "ingredients": len(recipe.ingredients),
                    }
                )
            needs = []
            for item_id, quantity in shopping.items():
                item = ws.items.get(item_id)
                ref = ws.prices.get(item_id) if item is not None and item.exchangeable else None
                have = stock.get(item_id).total
                needs.append(
                    {
                        "item_id": item_id,
                        "name": item.name if item else f"#{item_id}",
                        "icon": state["icons"].get(item_id),
                        "quantity": quantity,
                        "have": have,
                        "to_buy": max(0, quantity - have),
                        "price": ref.price if ref else None,
                        "source": ref.source if ref else None,
                        "lot": ref.lot if ref else None,
                        "cost": ref.price * max(0, quantity - have) if ref else None,
                    }
                )
            needs.sort(key=lambda row: -(row["cost"] or 0))
            excluded = [
                {"item_id": i, "name": ws.items[i].name if i in ws.items else f"#{i}", "icon": state["icons"].get(i)}
                for i in sorted(exclude)
            ]
            return clean(
                {
                    "steps": steps,
                    "shopping": needs,
                    "excluded": excluded,
                    "start_xp": plan.start_xp,
                    "start_level": jobxp.xp_to_level(plan.start_xp),
                    "end_xp": plan.end_xp,
                    "end_level": jobxp.xp_to_level(plan.end_xp),
                    "target_level": plan.target_level,
                    "target_xp": jobxp.level_to_xp(plan.target_level),
                    "blocked_at": plan.blocked_at,
                    "cost": plan.cost,
                    "crafts": plan.crafts,
                    "candidates": len(candidates),
                    "unpriced": skipped,
                    "stock_known": stock.known,
                    "resale": resale,
                }
            )
        finally:
            conn.close()

    # --- objets ignorés ------------------------------------------------------

    def ignored(self) -> dict:
        conn = self.connect()
        try:
            state = self._load(conn)
            ws = state["ws"]
            rows = []
            for item_id, added_at in db.ignored_items(conn).items():
                item = ws.items.get(item_id)
                rows.append(
                    {
                        "item_id": item_id,
                        "name": item.name if item else f"#{item_id}",
                        "level": item.level if item else None,
                        "icon": state["icons"].get(item_id),
                        "type": state["meta"].get(item_id, (None, None))[0],
                        "added_at": added_at,
                    }
                )
            rows.sort(key=lambda row: -row["added_at"])
            # Tous les types d'objets, pour en ignorer un en bloc. Seuls comptent les objets échangeables.
            hidden_types = db.ignored_types(conn)
            counts: dict[tuple[str, str], int] = {}
            for item_id, (type_name, category) in state["meta"].items():
                item = ws.items.get(item_id)
                if type_name and item is not None and item.exchangeable:
                    counts[(type_name, category)] = counts.get((type_name, category), 0) + 1
            merged: dict[str, dict] = {}
            for (type_name, category), count in counts.items():
                entry = merged.setdefault(type_name, {"name": type_name, "category": category, "count": 0})
                entry["count"] += count
            types = [
                {**entry, "ignored": entry["name"] in hidden_types, "added_at": hidden_types.get(entry["name"])}
                for entry in sorted(merged.values(), key=lambda e: e["name"])
            ]
            return {"rows": rows, "types": types, "now": time.time()}
        finally:
            conn.close()

    def set_ignored(self, item_id: int, ignored: bool) -> dict:
        conn = self.connect()
        try:
            db.set_ignored(conn, item_id, ignored, time.time())
            return {"item_id": item_id, "ignored": ignored, "count": len(db.ignored_items(conn))}
        finally:
            conn.close()

    def set_type_ignored(self, type_name: str, ignored: bool) -> dict:
        conn = self.connect()
        try:
            known = {row[0] for row in conn.execute("SELECT DISTINCT type_name FROM items WHERE type_name IS NOT NULL")}
            if type_name not in known:
                raise ValueError("type d'objet inconnu")
            db.set_type_ignored(conn, type_name, ignored, time.time())
            return {"type": type_name, "ignored": ignored, "count": len(db.ignored_types(conn))}
        finally:
            conn.close()

    # --- atelier ---------------------------------------------------------------

    def workshop(self) -> dict:
        """Les listes de l'atelier, avec pour chacune ce qu'il faut réunir, ce qui est possédé et ce qui reste à acheter."""
        conn = self.connect()
        try:
            state = self._load(conn)
            ws, stock, icons = state["ws"], state["stock"], state["icons"]
            recipes = ws.calculator.recipes
            now = time.time()

            def reference(item_id: int) -> PriceRef | None:
                """Prix auquel l'objet s'achète. Un équipement se paie au prix de l'annonce la moins chère de l'HDV,
                pas au prix moyen, qui mêle les exemplaires forgemagés : tant que le relevé est assez récent."""
                item = ws.items.get(item_id)
                if item is None or not item.exchangeable:
                    return None
                cheapest = ws.prices.hdv_any(item_id)
                if cheapest is not None and now - cheapest[2] <= WORKSHOP_LISTING_MAX_AGE_S:
                    return PriceRef(*cheapest)
                return ws.prices.get(item_id)

            def price(item_id: int) -> float | None:
                ref = reference(item_id)
                return ref.price if ref is not None else None

            owned = lambda item_id: stock.get(item_id).total  # noqa: E731

            def named(item_id: int) -> dict:
                item = ws.items.get(item_id)
                return {
                    "item_id": item_id, "name": item.name if item else f"#{item_id}", "icon": icons.get(item_id),
                    "type": state["meta"].get(item_id, (None, None))[0],
                }  # fmt: skip

            plans = []
            for list_id, name, created_at in conn.execute("SELECT id, name, created_at FROM workshop_lists ORDER BY created_at DESC, id DESC").fetchall():
                goals = [
                    workshop.Goal(*row)
                    for row in conn.execute("SELECT id, item_id, quantity, mode, note FROM workshop_goals WHERE list_id = ? ORDER BY id", (list_id,))
                ]
                opened = {row[0] for row in conn.execute("SELECT item_id FROM workshop_opened WHERE list_id = ?", (list_id,))}
                plans.append((list_id, name, created_at, workshop.plan(goals, opened, recipes, price, owned)))
            # Besoin de chaque ressource toutes listes confondues, pour signaler celles que plusieurs listes se disputent.
            everywhere: dict[int, list[tuple[int, str, int]]] = {}
            for list_id, name, _, found in plans:
                for item_id, quantity in found.lines.items():
                    everywhere.setdefault(item_id, []).append((list_id, name, quantity))

            lists = []
            for list_id, name, created_at, found in plans:
                lines, total, remaining, unpriced = [], 0.0, 0.0, 0
                for item_id, quantity in found.lines.items():
                    have = stock.get(item_id)
                    unit = price(item_id)
                    ref = reference(item_id) if unit is not None else None
                    to_buy = max(0, quantity - have.total)
                    others = everywhere[item_id]
                    need_all = sum(q for _, _, q in others)
                    recipe = recipes.get(item_id)
                    if unit is None:
                        unpriced += 1
                    else:
                        total += unit * quantity
                        remaining += unit * to_buy
                    lines.append(
                        {
                            **named(item_id), "need": quantity,
                            "owned": {"inventory": have.inventory, "bank": have.bank, "havre": have.havre, "total": have.total},
                            "to_buy": to_buy, "price": unit, "source": ref.source if ref else None, "lot": ref.lot if ref else None,
                            "cost": unit * to_buy if unit is not None else None,
                            "craftable": recipe is not None and bool(recipe.ingredients),
                            # Disputée : d'autres listes en veulent aussi, et le stock ne couvre pas tout.
                            "need_all": need_all, "missing_all": max(0, need_all - have.total),
                            "shared": [{"list": other, "name": label, "need": q} for other, label, q in others] if len(others) > 1 else [],
                        }
                    )  # fmt: skip
                lines.sort(key=lambda row: (-(row["cost"] or 0), row["name"]))
                by_item = {row["item_id"]: row for row in lines}
                counted: set[int] = set()

                def node(item_id: int, share: int, trail: tuple[int, ...]) -> dict:
                    """Une ressource dans l'arbre : sous la recette qui la réclame, avec la part que celle-ci en veut."""
                    if item_id in found.opened and item_id not in trail:
                        wanted, to_make = found.opened[item_id]
                        have = stock.get(item_id)
                        children = [
                            node(child, parts[item_id], (*trail, item_id)) for child, parts in found.sources.items() if parts.get(item_id)
                        ]
                        return {
                            **named(item_id), "kind": "made", "share": share, "need": wanted, "to_make": to_make,
                            "owned": {"inventory": have.inventory, "bank": have.bank, "havre": have.havre, "total": have.total},
                            "children": children, "cost": sum(child["cost"] or 0 for child in children),
                        }  # fmt: skip
                    row = by_item[item_id]
                    first = item_id not in counted
                    counted.add(item_id)
                    return {
                        **row, "kind": "leaf", "share": share,
                        # Réclamée par plusieurs recettes, la ressource n'est comptée qu'à sa première apparition.
                        "repeat": not first, "cost": row["cost"] if first else None, "children": [],
                    }

                # Dans l'ordre des recettes, pas du coût : déplier un ingrédient ne doit pas le déplacer.
                tree = [node(item_id, parts[0], ()) for item_id, parts in found.sources.items() if parts.get(0)]
                lists.append(
                    {
                        "id": list_id, "name": name, "created_at": created_at,
                        "goals": [
                            {
                                **named(g.goal.item_id), "id": g.goal.id, "quantity": g.goal.quantity, "choice": g.goal.mode, "mode": g.mode,
                                "note": g.goal.note, "craftable": g.craftable, "buy_cost": g.buy_cost, "craft_cost": g.craft_cost,
                                "owned": g.owned, "to_make": g.to_make,
                            }
                            for g in found.goals
                        ],
                        "lines": lines, "tree": tree,
                        "opened": [{**named(item_id), "need": need, "to_make": to_make} for item_id, (need, to_make) in found.opened.items()],
                        "cost_total": total, "cost_remaining": remaining, "unpriced": unpriced,
                    }
                )  # fmt: skip
            return clean({"lists": lists, "stock_known": stock.known, "now": time.time()})
        finally:
            conn.close()

    def ganymede_guides(self) -> dict:
        """Guides de l'application Ganymède installée sur ce PC, ceux en cours d'abord. Lecture seule, rien n'en sort."""
        return {
            "guides": [
                {"id": g.id, "name": g.name, "steps": g.steps, "current_step": g.current_step, "started": g.started, "done": g.done}
                for g in ganymede.guides(self.ganymede_dir)
                if g.listed  # sans liste de ressources au début, un guide ne demande rien à réunir
            ]
        }

    def workshop_action(self, payload: dict) -> dict:
        """Modifie les listes de l'atelier. Lève ValueError si la demande est mal formée."""

        def number(name: str, low: int = 1, high: int = 10**9) -> int:
            value = payload.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise ValueError(f"{name} attendu")
            return value

        def text(name: str, limit: int = 80) -> str:
            value = payload.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} attendu")
            return " ".join(value.split())[:limit]

        action = payload.get("action")
        conn = self.connect()
        try:
            now = time.time()
            known = lambda item_id: conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone() is not None  # noqa: E731
            if action == "create":
                return {"list": db.workshop_create(conn, text("name"), [], now)}
            if action == "plan":
                # Liste toute faite : [[objet, quantité], …], chaque objectif à fabriquer (plan de métier).
                goals = payload.get("goals")
                if not isinstance(goals, list) or not 0 < len(goals) <= 200:
                    raise ValueError("goals attendu")
                rows = []
                for entry in goals:
                    if not (isinstance(entry, list) and len(entry) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in entry)):
                        raise ValueError("objectif mal formé")
                    if not known(entry[0]) or not 0 < entry[1] <= 10**6:
                        raise ValueError("objectif inconnu")
                    rows.append((entry[0], entry[1], workshop.CRAFT, ""))
                return {"list": db.workshop_create(conn, text("name"), rows, now)}
            if action == "ganymede":
                # Ce qu'il reste à réunir pour finir un guide Ganymède : les objets échangeables seulement
                # (un objet de quête ne s'achète pas), chacun à acheter ou fabriquer au moins cher.
                guide_id = number("guide")
                found = next((g for g in ganymede.guides(self.ganymede_dir) if g.id == guide_id), None)
                wanted = ganymede.needs(guide_id, self.ganymede_dir, from_start=payload.get("whole") is True)
                if found is None or wanted is None:
                    raise ValueError("guide introuvable")
                tradable = dict(conn.execute("SELECT id, category_id FROM items WHERE exchangeable"))
                # Un équipement (catégorie 0, Dofus compris) demandé par plusieurs quêtes est le même exemplaire gardé sur soi.
                rows = [
                    (item_id, min(wanted.peak.get(item_id, quantity) if tradable[item_id] == 0 else quantity, 10**6), None, "")
                    for item_id, quantity in sorted(wanted.items.items())
                    if item_id in tradable
                ][:200]
                if not rows:
                    raise ValueError("plus rien à réunir pour ce guide")
                name = " ".join(found.name.split())[:80]
                return {"list": db.workshop_create(conn, name, rows, now), "skipped": len(wanted.items) - len(rows)}
            if action == "import":
                # Liste venue d'ailleurs (un stuff Dofusbook) : des identifiants d'objets du jeu, un exemplaire
                # de chaque. Ceux que cette base ne connaît pas sont laissés de côté, et comptés.
                ids = payload.get("items")
                if not isinstance(ids, list) or not 0 < len(ids) <= 60 or not all(isinstance(v, int) and not isinstance(v, bool) for v in ids):
                    raise ValueError("items attendu")
                counts: dict[int, int] = {}
                for item_id in ids:
                    if known(item_id):
                        counts[item_id] = counts.get(item_id, 0) + 1
                if not counts:
                    raise ValueError("aucun objet connu")
                rows = [(item_id, quantity, None, "") for item_id, quantity in counts.items()]
                return {"list": db.workshop_create(conn, text("name"), rows, now), "skipped": len(ids) - sum(counts.values())}
            if action == "almanax":
                days = number("days", 1, 31)
                start = date.today()
                rows, missing = [], 0
                for offset in range(days):
                    day = start + timedelta(days=offset)
                    found = almanax_source.cached(conn, day)
                    if found is None:
                        try:
                            found = self.fetch_almanax(day)
                            if found["name"] or found["items"]:
                                almanax_source.store(conn, day, found)
                        except Exception as exc:  # DofusDB injoignable : ce jour manquera à la liste
                            log.warning("Almanax du %s non récupéré : %s", day.isoformat(), exc)
                            missing += 1
                            continue
                    for item_id, quantity, _ in found["items"]:
                        rows.append((item_id, quantity, None, f"{day.day:02d}/{day.month:02d}"))
                if not rows:
                    raise ValueError("almanax indisponible")
                end = start + timedelta(days=days - 1)
                name = f"Almanax du {start.day:02d}/{start.month:02d} au {end.day:02d}/{end.month:02d}"
                return {"list": db.workshop_create(conn, name, rows, now), "missing_days": missing}
            if action in ("rename", "delete", "add", "open"):
                list_id = number("list")
                if conn.execute("SELECT 1 FROM workshop_lists WHERE id = ?", (list_id,)).fetchone() is None:
                    raise ValueError("liste inconnue")
                with conn:
                    if action == "rename":
                        conn.execute("UPDATE workshop_lists SET name = ? WHERE id = ?", (text("name"), list_id))
                    elif action == "delete":
                        db.workshop_delete(conn, list_id)
                    elif action == "add":
                        item_id = number("item_id")
                        if not known(item_id):
                            raise ValueError("objet inconnu")
                        conn.execute(
                            "INSERT INTO workshop_goals (list_id, item_id, quantity, mode, note) VALUES (?, ?, ?, NULL, '')",
                            (list_id, item_id, number("quantity", 1, 10**6) if "quantity" in payload else 1),
                        )
                    else:
                        item_id = number("item_id")
                        if payload.get("opened"):
                            conn.execute("INSERT OR IGNORE INTO workshop_opened VALUES (?, ?)", (list_id, item_id))
                        else:
                            conn.execute("DELETE FROM workshop_opened WHERE list_id = ? AND item_id = ?", (list_id, item_id))
                return {"list": list_id}
            if action in ("goal", "remove"):
                goal_id = number("goal")
                row = conn.execute("SELECT list_id FROM workshop_goals WHERE id = ?", (goal_id,)).fetchone()
                if row is None:
                    raise ValueError("objectif inconnu")
                with conn:
                    if action == "remove":
                        conn.execute("DELETE FROM workshop_goals WHERE id = ?", (goal_id,))
                    else:
                        if "quantity" in payload:
                            conn.execute("UPDATE workshop_goals SET quantity = ? WHERE id = ?", (number("quantity", 1, 10**6), goal_id))
                        if "mode" in payload:
                            mode = payload["mode"]
                            if mode not in (None, workshop.BUY, workshop.CRAFT):
                                raise ValueError("mode attendu")
                            conn.execute("UPDATE workshop_goals SET mode = ? WHERE id = ?", (mode, goal_id))
                return {"list": row[0]}
            raise ValueError("action inconnue")
        finally:
            conn.close()

    # --- aujourd'hui ---------------------------------------------------------

    def today(self, since: float | None = None) -> dict:
        """Ce qui demande une action, ce qui s'est passé depuis la dernière visite, et quelques pistes."""
        lots = self.sales()
        conn = self.connect()
        try:
            state = self._load(conn)
            ws, cfg, icons = state["ws"], state["cfg"], state["icons"]
            now = time.time()
            since = now - 86400 if since is None else min(max(since, now - 30 * 86400), now)
            hidden = self._hidden(conn, state)

            def named(item_id: int) -> dict:
                item = ws.items.get(item_id)
                return {"item_id": item_id, "name": item.name if item else f"#{item_id}", "icon": icons.get(item_id)}

            undercut = [r for r in lots["rows"] if r["undercut"] > 0]
            expiring = sorted((r for r in lots["rows"] if r["remaining_s"] < 3 * 86400), key=lambda r: r["remaining_s"])
            pick = lambda rows: [{k: r[k] for k in ("item_id", "name", "icon", "lot", "price", "undercut", "remaining_s")} for r in rows[:4]]  # noqa: E731

            sold = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(price), 0), COALESCE(SUM(source_id < 0), 0) FROM trades WHERE kind = 'sale' AND ts >= ?", (since,)
            ).fetchone()
            bought = conn.execute("SELECT COUNT(*), COALESCE(SUM(price), 0) FROM trades WHERE kind = 'purchase' AND ts >= ?", (since,)).fetchone()
            # Le plus gros mouvement de chaque sens, pour donner un visage au total.
            biggest = {
                kind: conn.execute(
                    "SELECT item_id, SUM(price) FROM trades WHERE kind = ? AND ts >= ? GROUP BY item_id ORDER BY 2 DESC LIMIT 1", (kind, since)
                ).fetchone()
                for kind in ("sale", "purchase")
            }
            forged = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(rune_price), 0), COUNT(DISTINCT uid) FROM fm_passes WHERE ts >= ?", (since,)
            ).fetchone()
            last_forged = conn.execute("SELECT item_id FROM fm_passes WHERE ts >= ? ORDER BY ts DESC LIMIT 1", (since,)).fetchone()
            # Les derniers objets travaillés, quelle que soit la date : c'est eux qu'on veut retrouver d'un clic.
            recent_forged = []
            ignored = forge.non_stat_effects(conn)
            for uid, item_id, ts, after in conn.execute("SELECT uid, item_id, last_ts, after FROM fm_items ORDER BY last_ts DESC LIMIT 3").fetchall():
                # Ses jets tels qu'ils sont après forgemagie, pour l'infobulle (si les caractéristiques de base sont connues).
                template = forge.load_template(conn, item_id)
                effects = [(e, v) for e, v in json.loads(after) if v is not None and e not in ignored]
                rolls = self._rolls(classify(effects, template), None) if template is not None else None
                recent_forged.append({**named(item_id), "uid": uid, "ts": ts, "rolls": rolls})

            crafts = sorted(
                (c for c in state["stock_crafts"] if c.craftable > 0 and (c.total_margin or 0) > 0 and not hidden(c.result.item.id)),
                key=lambda c: -c.total_margin,
            )[:4]
            signals = []
            if not state["trends"].empty:
                frame = state["trends"]
                frame = frame[frame["Signal"].notna() & frame["Vendus 7 j"].fillna(0).gt(0)]
                frame = frame.reindex(frame["Écart %"].abs().sort_values(ascending=False).index)
                for row in records(frame.head(12)):
                    if not hidden(row["item_id"]) and len(signals) < 4:
                        signals.append({**named(row["item_id"]), "deviation": row["Écart %"], "price": row["Prix"], "sold_7d": row["Vendus 7 j"]})

            status = data.status(conn, now)
            unpriced = [r for r in lots["rows"] if not r["equipment"] and r["hdv"] is None]
            return clean(
                {
                    "now": now, "since": since,
                    "todo": {
                        "undercut": {"count": len(undercut), "amount": sum(r["price"] for r in undercut), "rows": pick(undercut)},
                        "expiring": {"count": len(expiring), "rows": pick(expiring)},
                        "capture": {
                            "running": status["running"], "alert": bool(status["decode_alert"]),
                            "awaiting": bool(status["running"] and status.get("awaiting_prices_since")),
                            "late": bool(status.get("awaiting_prices_late")),
                        },
                    },
                    "recent": {
                        "sold": {"count": sold[0], "amount": sold[1], "offline": sold[2], "top": named(biggest["sale"][0]) if biggest["sale"] else None},
                        "bought": {"count": bought[0], "amount": bought[1], "top": named(biggest["purchase"][0]) if biggest["purchase"] else None},
                        "forged": {
                            "passes": forged[0], "cost": forged[1], "objects": forged[2], "top": named(last_forged[0]) if last_forged else None,
                            "items": recent_forged,
                        },
                    },
                    "crafts": [{**named(c.result.item.id), "craftable": c.craftable, "margin": c.total_margin} for c in crafts],
                    "signals": signals,
                    "quality": {
                        "unpriced_lots": len(unpriced),
                        "snapshot_age_s": now - status["last_snapshot_ts"] if status["last_snapshot_ts"] else None,
                        "sales_known": bool(lots["markets"]),
                    },
                    "stock_known": state["stock"].known,
                }
            )  # fmt: skip
        finally:
            conn.close()

    def almanax(self, day: str | None = None) -> dict:
        """Bonus et offrande d'un jour (AAAA-MM-JJ, aujourd'hui par défaut), avec le coût de l'offrande et ce qu'on en possède."""
        today = date.today()
        try:
            wanted = date.fromisoformat(day) if day else today
        except ValueError:
            wanted = today
        if abs((wanted - today).days) > 400:
            wanted = today
        conn = self.connect()
        try:
            state = self._load(conn)
            ws, stock = state["ws"], state["stock"]
            head = {
                "day": wanted.isoformat(), "today": today.isoformat(),
                "previous": (wanted - timedelta(days=1)).isoformat(), "next": (wanted + timedelta(days=1)).isoformat(),
            }  # fmt: skip
            found = almanax_source.cached(conn, wanted)
            if found is None:
                try:
                    found = self.fetch_almanax(wanted)
                    if found["name"] or found["items"]:
                        almanax_source.store(conn, wanted, found)
                except Exception as exc:  # DofusDB injoignable : la case l'indique, on réessaiera
                    log.warning("Almanax du %s non récupéré : %s", wanted.isoformat(), exc)
                    return {**head, "available": False}
            items, total = [], 0.0
            for item_id, quantity, name in found["items"]:
                item = ws.items.get(item_id)
                ref = ws.prices.get(item_id)
                owned = stock.get(item_id).total if stock.known else None
                cost = ref.price * quantity if ref else None
                total = None if cost is None or total is None else total + cost
                items.append(
                    {
                        "item_id": item_id, "name": item.name if item else name or f"#{item_id}", "icon": state["icons"].get(item_id),
                        "quantity": quantity, "unit": ref.price if ref else None, "source": ref.source if ref else None,
                        "cost": cost, "owned": owned,
                    }
                )  # fmt: skip
            # plain() de nouveau : les jours déjà gardés en base l'ont été avant que les renvois soient nettoyés.
            desc = almanax_source.plain(found["desc"])
            return clean({**head, "available": True, "name": found["name"], "desc": desc, "items": items, "cost": total})
        finally:
            conn.close()

    # --- mes ventes ----------------------------------------------------------

    def sales(self) -> dict:
        """Lots que le joueur a en vente, comparés au prix le plus bas relevé à l'HDV pour la même taille de lot."""
        conn = self.connect()
        try:
            state = self._load(conn)
            ws, cfg = state["ws"], state["cfg"]
            now = time.time()
            cheapest = {
                row[0]: row[1:]
                for row in conn.execute(
                    "SELECT item_id, MIN(NULLIF(p1, 0)), MIN(NULLIF(p10, 0)), MIN(NULLIF(p100, 0)), MIN(NULLIF(p1000, 0)), "
                    "MAX(captured_at) FROM hdv_current WHERE captured_at >= ? GROUP BY item_id",
                    (now - cfg.last_sale_max_age_hours * 3600,),
                )
            }
            rows = []
            ignored = forge.non_stat_effects(conn)
            templates: dict[int, dict | None] = {}
            for item_id, lot, price, remaining, captured_at, effects in conn.execute(
                "SELECT item_id, lot, price, remaining_s, captured_at, effects FROM my_sales"
            ):
                item = ws.items.get(item_id)
                equipment = ws.prices.is_equipment(item_id)
                # Jets de mon exemplaire, lus comme une annonce de l'HDV (pour l'infobulle au survol),
                # et l'annonce similaire la moins chère du même modèle.
                rolls = close = None
                if equipment and effects:
                    if item_id not in templates:
                        templates[item_id] = forge.load_template(conn, item_id) if self.ensure_template(conn, item_id) else None
                    if templates[item_id] is not None:
                        c = classify([tuple(e) for e in json.loads(effects) if e[0] not in ignored], templates[item_id])
                        rolls = self._rolls(c, price)
                        close = self._similar(conn, item_id, c, templates[item_id], ignored, own_price=price)
                seen = cheapest.get(item_id)
                # Un équipement se compare mal : chaque exemplaire a ses propres jets.
                hdv = seen[(1, 10, 100, 1000).index(lot)] if seen and not equipment else None
                avg = ws.prices.avg_price(item_id)
                rows.append(
                    {
                        "item_id": item_id,
                        "name": item.name if item else f"#{item_id}",
                        "icon": state["icons"].get(item_id),
                        "type": state["meta"].get(item_id, (None, None))[0],
                        "equipment": equipment,
                        "rolls": rolls,
                        "similar": close,
                        "lot": lot,
                        "price": price,
                        "unit": price / lot,
                        "hdv": hdv,
                        "hdv_ts": seen[4] if hdv is not None else None,
                        "undercut": price - hdv if hdv is not None and hdv < price
                        # Une comparaison grossière (exos et overs seulement) ne suffit pas à parler de sous-enchère.
                        else price - close["price"]
                        if close is not None and close["confidence"] != similar.ROUGH and close["price"] < price
                        else 0,
                        "avg": avg * lot if avg else None,
                        "remaining_s": max(0, remaining - (now - captured_at)),
                        "captured_at": captured_at,
                    }
                )
            rows.sort(key=lambda r: (-(r["undercut"] > 0), -r["price"]))
            meta = conn.execute("SELECT COUNT(*), MIN(captured_at), MAX(captured_at) FROM my_sales_meta").fetchone()
            # Journal : ventes conclues et achats annoncés par le jeu, les plus récents d'abord.
            trades = []
            # Une vente porte le numéro du lot, un achat celui de l'objet.
            forged = {uid for row in conn.execute("SELECT uid, lot_uid FROM fm_items") for uid in row if uid is not None}
            for source_id, ts, kind, item_id, quantity, price, ref in conn.execute(
                "SELECT source_id, ts, kind, item_id, quantity, price, ref FROM trades ORDER BY ts DESC, source_id DESC LIMIT 500"
            ):
                item = ws.items.get(item_id)
                avg = ws.prices.avg_price(item_id)
                trades.append(
                    {
                        "ts": ts,
                        "kind": kind,
                        "item_id": item_id,
                        "name": item.name if item else f"#{item_id}",
                        "icon": state["icons"].get(item_id),
                        "quantity": quantity,
                        "price": price,
                        "avg": avg * quantity if avg else None,
                        "offline": source_id < 0,  # vente conclue hors ligne, déduite des lots disparus
                        # Lié à la forgemagie : une rune, ou un exemplaire que j'ai forgemagé (acheté ou vendu).
                        "fm": (state["meta"].get(item_id, ("",))[0] or "").startswith("Rune ") or ref in forged,
                    }
                )
            # Kamas gagnés hors ligne dont les lots n'ont pas (encore) été retrouvés.
            pending = conn.execute(
                "SELECT COALESCE(SUM(delta), 0), MIN(ts), MAX(ts) FROM offline_sales WHERE attributed = 0 AND delta > 0"
            ).fetchone()
            def valued(item_id: int, quantity: int) -> dict:
                item = ws.items.get(item_id)
                ref = ws.prices.get(item_id)
                return {
                    "item_id": item_id, "name": item.name if item else f"#{item_id}", "icon": state["icons"].get(item_id),
                    "quantity": quantity, "value": ref.price * quantity if ref else None,
                }  # fmt: skip

            # Échanges conclus avec d'autres joueurs : ce qui a été donné et reçu, valorisé au prix de référence du jour.
            exchanges = []
            for source_id, ts, given_kamas, received_kamas in conn.execute(
                "SELECT source_id, ts, kamas_given, kamas_received FROM exchanges ORDER BY ts DESC LIMIT 100"
            ).fetchall():
                sides = {0: [], 1: []}
                for received, item_id, quantity in conn.execute(
                    "SELECT received, item_id, SUM(quantity) FROM exchange_items WHERE source_id = ? GROUP BY received, item_id", (source_id,)
                ):
                    sides[received].append(valued(item_id, quantity))
                for side in sides.values():
                    side.sort(key=lambda row: -(row["value"] or 0))
                exchanges.append(
                    {
                        "ts": ts, "kamas_given": given_kamas, "kamas_received": received_kamas,
                        "given": sides[0], "received": sides[1],
                        "value_given": sum(row["value"] or 0 for row in sides[0]),
                        "value_received": sum(row["value"] or 0 for row in sides[1]),
                    }
                )  # fmt: skip
            # Objets fabriqués : valeur et coût de fabrication aux prix du jour.
            crafts = []
            for ts, item_id, quantity in conn.execute("SELECT ts, item_id, quantity FROM crafts ORDER BY ts DESC, source_id DESC LIMIT 300"):
                cost = forge.craft_cost(ws, item_id)
                crafts.append({**valued(item_id, quantity), "ts": ts, "cost": cost * quantity if cost is not None else None})
            totals = {
                f"{kind}_{label}": conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(price), 0) FROM trades WHERE kind = ? AND ts >= ?", (kind, now - days * 86400)
                ).fetchone()
                for kind in ("sale", "purchase")
                for label, days in (("24h", 1), ("7d", 7))
            }
            return clean(
                {
                    "rows": rows, "markets": meta[0], "oldest": meta[1], "latest": meta[2], "tax": cfg.hdv_tax, "now": now,
                    "trades": trades, "totals": totals, "exchanges": exchanges, "crafts": crafts,
                    "offline_pending": {"amount": pending[0], "since": pending[1], "latest": pending[2]},
                    "first_trade": conn.execute("SELECT MIN(ts) FROM trades").fetchone()[0],
                }
            )  # fmt: skip
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
                        "havre": owned.havre,
                        "total": owned.total,
                        "price": ref.price if ref else None,
                        "source": ref.source if ref else None,
                        "lot": ref.lot if ref else None,
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
            hidden = self._hidden(conn, state)
            rows = []
            for c in state["stock_crafts"]:
                r = c.result
                if hidden(r.item.id):
                    continue
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
                        "lot": r.sell.lot if r.sell else None,
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
            # Prix de chaque ingrédient, une seule fois : [prix moyen du jeu, prix HDV, date du relevé HDV, lot HDV].
            prices = {}
            for row in rows:
                for ingredient in row["ingredients"]:
                    if ingredient["id"] not in prices:
                        hdv = ws.prices.hdv_price(ingredient["id"])
                        prices[ingredient["id"]] = [
                            ws.prices.avg_price(ingredient["id"]),
                            hdv[0] if hdv else None,
                            hdv[2] if hdv else None,
                            hdv[3] if hdv else None,
                        ]
            return clean(
                {
                    "rows": rows,
                    "prices": prices,
                    "snapshot_ts": ws.prices.snapshot_ts,
                    "jobs": sorted({row["job"] for row in rows}),
                    "meta": stock.meta,
                    "bank_inferred": stock.bank_inferred,
                    "has_jobs": bool(state["cfg"].jobs),
                    "now": ws.now,
                }
            )
        finally:
            conn.close()

    # --- combat du Comte Harebourg ------------------------------------------

    def harebourg(self, state: dict) -> dict:
        """Aide au combat du Comte pour une position donnée (saisie à la main en attendant la capture).

        state : {"me", "target", "comte" : [col, row] ou null, "allies" : [[col, row]…],
                 "rotation" : 0/90/180/270 (degrés horaires), "round" : numéro du tour}.
        Tout le calcul est fait ici (dofustool.fight) ; l'interface ne fait qu'afficher.
        """
        layout = fight_grid.load("comte")

        def cell(value) -> tuple[int, int] | None:
            if value is None:
                return None
            if not (isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) and -100 < v < 100 for v in value)):
                raise ValueError("case attendue : [colonne, rangée]")
            return value[0], value[1]

        me, target, comte = cell(state.get("me")), cell(state.get("target")), cell(state.get("comte"))
        allies = frozenset(c for c in (cell(v) for v in state.get("allies", [])[:16]) if c is not None)
        rotation = harebourg.Rotation(int(state.get("rotation", 90)))
        round_number = int(state.get("round", 1))
        if round_number < 1:
            raise ValueError("numéro de tour invalide")
        occupied = allies | {c for c in (me, target) if c is not None}

        out: dict = {
            "layout": [line for line in layout.to_text().splitlines()],
            "rotation": rotation.value,
            "rotation_label": rotation.label,
            "bumped": rotation.bumped().value,
            "round": round_number,
            "life_bands": [[floor, r.value, r.label] for floor, r in harebourg.LIFE_BANDS],
            "rotations": [[r.value, r.label] for r in harebourg.Rotation],
            "landing": None,
            "shot": None,
            "swap": None,
            "swap_map": None,
            "mi_temps": [],
        }
        if me is not None:
            # Case d'arrivée pour chaque case visée, pour l'aperçu au survol : [colonne, rangée, échec critique].
            out["landing"] = [
                [list(s.landing) + [s.critical_failure] for s in (harebourg.cast_on(layout, me, (col, row), rotation) for col in range(layout.width))]
                for row in range(layout.height)
            ]
            if target is not None:
                s = harebourg.shot_at(layout, me, target, rotation)
                out["shot"] = {"aim": list(s.aim), "clickable": s.clickable, "critical_failure": s.critical_failure, "melee": s.melee}
        if comte is not None:
            out["mi_temps"] = [list(c) for c in harebourg.mi_temps(layout, comte)]
            plan = harebourg.swap_map(layout, round_number, comte, occupied - {me} if me else occupied)
            out["swap_map"] = [[c[0], c[1], s.verdict.name] for c, s in plan.items()]
            if me is not None and me != comte:
                s = harebourg.swap(layout, round_number, me, comte, occupied - {me})
                out["swap"] = {"mover": s.mover, "destination": list(s.destination), "verdict": s.verdict.name, "label": s.verdict.value}
        return out

    # --- forgemagie ----------------------------------------------------------

    def forge_options(self) -> dict:
        conn = self.connect()
        try:
            state = self._load(conn)
            if base_effects.missing_items(conn) or (self.background_refresh and time.time() - self._templates_checked >= TEMPLATES_EVERY_S):
                self.fetch_missing_templates()
            fetched = {row[0] for row in conn.execute("SELECT item_id FROM item_effects_fetched")}
            rows = conn.execute(
                "SELECT i.id, i.name, i.level, COUNT(*) FROM hdv_current h JOIN items i ON i.id = h.item_id "
                "WHERE i.category_id = 0 GROUP BY i.id ORDER BY i.name"
            ).fetchall()
            hidden = self._hidden(conn, state)
            return {
                "items": [
                    {"id": i, "name": name, "level": level, "count": count, "icon": state["icons"].get(i),
                     "template_known": i in fetched, "type": state["meta"].get(i, (None, None))[0]}
                    for i, name, level, count in rows
                    if not hidden(i)
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

            def listing(entry, mine: bool = False) -> dict:
                price, c, first_seen, last_seen = entry
                return {
                    "mine": mine,
                    "price": price, "label": c.label, "plain": c.plain,
                    "quality": round(c.quality * 100) if c.quality is not None else None,
                    "transcended": c.transcended, "hunting": c.hunting,
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
                    "listings": [
                        listing(entry, mine)
                        for entry, mine in zip(current, forge.mark_own(current, forge.own_listings(conn, item_id, template)))
                    ],
                    "gone": [listing(entry) for entry in everything if latest is not None and entry[3] < latest],
                    "filter": db.load_fm_filter(conn, item_id),
                    # Une rune de chasse ne se pose que sur une arme : le critère n'est proposé que là.
                    "weapon": state["meta"].get(item_id, (None, None))[0] in WEAPON_TYPES,
                    "hunting_icon": state["icons"].get(HUNTING_RUNE),
                }
            )
        finally:
            conn.close()

    def forge_journal(self) -> dict:
        """Un dossier par exemplaire forgemagé : runes passées, coût, et marge une fois l'objet vendu."""
        conn = self.connect()
        try:
            state = self._load(conn)
            ws, names, icons = state["ws"], state["effects"], state["icons"]
            tax = ws.calculator.tax

            def price_of(item_id: int) -> float | None:
                ref = ws.prices.get(item_id)
                return ref.price if ref is not None else None

            fmjournal.freeze_prices(conn, price_of)
            units = fmjournal.purchase_units(conn)
            on_sale = dict(conn.execute("SELECT uid, price FROM my_sales"))
            ignored = forge.non_stat_effects(conn)
            order = state["priorities"]

            def label(item_id: int) -> str:
                item = ws.items.get(item_id)
                return item.name if item else f"#{item_id}"

            dossiers = []
            overall: dict[int, fmjournal.RuneLine] = {}
            for d in fmjournal.load(conn):
                real = fmjournal.real_rune_cost(d, units)
                if d.base_cost is not None:
                    base, base_source = d.base_cost, "manual"
                elif (bought := fmjournal.base_purchase(conn, d)) is not None:
                    base, base_source = bought
                elif (crafted := forge.craft_cost(ws, d.item_id)) is not None:
                    base, base_source = crafted, "craft"
                else:
                    base, base_source = price_of(d.item_id), "market"
                    if base is None:
                        base_source = None
                if d.sold_price is not None:
                    status, sale, sale_ts = "sold", d.sold_price, d.sold_at
                elif d.lot_uid in on_sale:
                    status, sale, sale_ts = "listed", on_sale[d.lot_uid], d.listed_at
                else:
                    reference = ws.prices.get(d.item_id)
                    status, sale, sale_ts = "kept", reference.price if reference else None, None
                # Valeur d'après les annonces similaires : elle remplace le prix du modèle tant que l'objet n'est pas en vente.
                close, special, rolls = None, False, None
                if self.ensure_template(conn, d.item_id):
                    template = forge.load_template(conn, d.item_id)
                    mine = classify([(i, v) for i, v in d.after.items() if i not in ignored], template)
                    special = bool(mine.exo or mine.over)
                    if status != "sold":
                        close = self._similar(conn, d.item_id, mine, template, ignored, own_price=sale if status == "listed" else None)
                        if status == "kept" and close is not None:
                            sale = close["price"]
                    rolls = self._rolls(mine, sale)  # l'exemplaire tel qu'il est, pour l'infobulle
                rune_cost = d.rune_cost
                margin = sale * (1 - tax) - base - rune_cost if sale is not None and base is not None else None
                ids = sorted(set(d.after) | set(d.before or {}), key=lambda i: (order.get(i, 1_000_000), i))
                for line in d.runes.values():
                    total = overall.setdefault(line.rune_id, fmjournal.RuneLine(line.rune_id))
                    for attr in ("count", "sc", "sn", "ec", "cost", "priced"):
                        setattr(total, attr, getattr(total, attr) + getattr(line, attr))
                dossiers.append(
                    {
                        "uid": d.uid, "item_id": d.item_id, "name": label(d.item_id), "icon": icons.get(d.item_id),
                        "type": state["meta"].get(d.item_id, (None, None))[0],
                        "first_ts": d.first_ts, "last_ts": d.last_ts, "passes": d.passes, "sc": d.sc, "sn": d.sn, "ec": d.ec,
                        "duration_s": d.duration_s, "pool": d.pool,
                        "rune_cost": rune_cost, "unpriced": d.unpriced,
                        "real_cost": real[0] if real else None, "real_passes": real[1] if real else 0,
                        "base_cost": base, "base_source": base_source,
                        "status": status, "sale": sale, "sale_ts": sale_ts, "tax": sale * tax if sale is not None else None,
                        # similar : estimation par les annonces proches ; special : exo ou over, mal estimé par le prix du modèle.
                        "similar": close, "special": special, "rolls": rolls,
                        "margin": margin,
                        "runes": [
                            {
                                "id": line.rune_id, "name": label(line.rune_id), "icon": icons.get(line.rune_id),
                                "count": line.count, "sc": line.sc, "sn": line.sn, "ec": line.ec, "cost": line.cost,
                                "unit": line.cost / line.priced if line.priced else None, "bought": units.get(line.rune_id),
                            }
                            for line in sorted(d.runes.values(), key=lambda l: -l.cost)
                        ],
                        "lines": [
                            {
                                "id": i, "name": names.get(i, f"effet {i}"), "asset": state["assets"].get(i),
                                "before": d.before.get(i, 0) if d.before is not None else None, "after": d.after.get(i, 0),
                            }
                            for i in ids
                            if i not in MARKERS
                        ],
                    }
                )  # fmt: skip
            return clean(
                {
                    "now": time.time(), "tax": tax, "dossiers": dossiers,
                    "runes": [
                        {
                            "id": line.rune_id, "name": label(line.rune_id), "icon": icons.get(line.rune_id),
                            "count": line.count, "sc": line.sc, "sn": line.sn, "ec": line.ec, "cost": line.cost,
                            "unit": line.cost / line.priced if line.priced else None, "bought": units.get(line.rune_id),
                        }
                        for line in sorted(overall.values(), key=lambda l: -l.cost)
                    ],
                }
            )  # fmt: skip
        finally:
            conn.close()

    def set_forge_base_cost(self, uid: int, cost: int | None) -> dict:
        conn = self.connect()
        try:
            db.set_fm_base_cost(conn, uid, cost)
            return {"uid": uid, "base_cost": cost}
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
        self,
        criterion: str,
        exo: int | None = None,
        effect: int | None = None,
        amount: int | None = None,
        exo_value: int | None = None,
        exact: bool = False,
        hunting: bool | None = None,
    ) -> dict:
        """criterion : « saved » (critères de chaque objet), « exo » (+ exo_value), ou « over » (effect + amount).

        exact : l'exo doit avoir pile la valeur demandée.
        hunting : avec ou sans rune de chasse (None : peu importe). « Avec » ne garde que les armes.

        Le classement relit toutes les annonces de tous les équipements : il est gardé en mémoire tant que
        ni les données, ni la configuration, ni les critères enregistrés n'ont changé.
        """
        conn = self.connect()
        try:
            # Ni le stock ni mes ventes n'entrent dans le classement : un achat en jeu ne le fait pas recalculer.
            stamp = (
                *core_stamp(data_stamp(conn, self.config_path)),
                conn.execute("SELECT group_concat(item_id || ':' || config, '|') FROM (SELECT * FROM fm_filters ORDER BY item_id)").fetchone()[0],
            )
        finally:
            conn.close()
        if stamp != self._ranking_stamp:
            self._ranking_stamp, self._ranking_cache, self._ranking_loaded = stamp, {}, None
        cache, key = self._ranking_cache, (criterion, exo, effect, amount, exo_value, exact, hunting)
        if key not in cache:
            cache[key] = self._forge_ranking(criterion, exo, effect, amount, exo_value, exact, hunting)
        # Les objets ignorés sont retirés à la sortie : en ignorer un ne fait pas recalculer le classement.
        conn = self.connect()
        try:
            hidden = self._hidden(conn, self._load(conn))
        finally:
            conn.close()
        found = cache[key]
        rows = [row for row in found["rows"] if not hidden(row["item_id"])]
        if hunting is True and criterion != "saved":
            rows = [row for row in rows if row.get("type") in WEAPON_TYPES]
        return {**found, "rows": rows}

    def _forge_ranking(
        self, criterion: str, exo: int | None, effect: int | None, amount: int | None, exo_value: int | None, exact: bool,
        hunting: bool | None = None,
    ) -> dict:  # fmt: skip
        conn = self.connect()
        try:
            state = self._load(conn)
            names = state["effects"]
            mode = {"saved": forge.SAVED, "exo": forge.EXO, "over": forge.OVER}.get(criterion, forge.SAVED)
            over = (effect, max(1, amount or 1)) if effect else None
            # Les annonces de tous les équipements sont lues une fois, puis servent à tous les critères.
            if self._ranking_loaded is None:
                self._ranking_loaded = forge.load_all(conn)
            loaded = self._ranking_loaded
            frame = forge.ranking(conn, state["ws"], mode, exo, over, loaded, exo_value, exact, hunting)
            rows = records(frame) if not frame.empty else []
            # Métier de forgemagie de chaque objet : celui de son métier de fabrication ; pour un objet
            # sans recette, celui qui fabrique d'ordinaire ce type d'objet.
            job_names = dict(conn.execute("SELECT id, name FROM jobs"))
            craft_job = dict(conn.execute("SELECT result_id, job_id FROM recipes"))
            by_type: dict[str, dict[int, int]] = {}
            for type_name, job_id, count in conn.execute(
                "SELECT i.type_name, r.job_id, COUNT(*) FROM recipes r JOIN items i ON i.id = r.result_id "
                "WHERE i.category_id = 0 GROUP BY i.type_name, r.job_id"
            ):
                if job_id in MAGE_OF:
                    by_type.setdefault(type_name, {})[job_id] = count
            usual = {type_name: max(counts, key=counts.get) for type_name, counts in by_type.items()}
            ws = state["ws"]
            for row in rows:
                item_id = row["item_id"]
                row["icon"] = state["icons"].get(item_id)
                row["type"] = state["meta"].get(item_id, (None, None))[0]
                maker = craft_job.get(item_id)
                if maker not in MAGE_OF:
                    maker = usual.get(row["type"])
                row["mage"] = job_names.get(MAGE_OF.get(maker))
                row["Vendus 7 j"] = ws.prices.liquidity(item_id).qty_7d
                row["Vendus 30 j"] = ws.prices.sold_30d(item_id)
                row["Vendus estimés"] = ws.prices.rebuilt_at(item_id) is not None
            mine = state["cfg"].jobs
            return {
                "rows": rows,
                # Mes niveaux dans les métiers de forgemagie (absent : métier non renseigné).
                "mages": {name: mine.get(name) for name in sorted(job_names[j] for j in MAGE_OF.values() if j in job_names)},
                "exos": [
                    {"id": effect_id, "name": names.get(effect_id, f"effet {effect_id}"), "count": count}
                    for effect_id, count in forge.all_exo_counts(conn, loaded).items()
                ],
                "lines": [
                    {"id": effect_id, "name": names.get(effect_id, f"effet {effect_id}"), "count": count}
                    for effect_id, count in forge.base_line_counts(conn, loaded).items()
                ],
                "known": len(forge.equipment_options(conn)),
            }
        finally:
            conn.close()
