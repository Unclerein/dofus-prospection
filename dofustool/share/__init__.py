"""Partage des relevés de marché entre quelques joueurs du même serveur.

Ce qui circule : les annonces HDV, les cours du marché, les relevés de prix moyens et la table des
clés du build. Rien de personnel : ni l'archive brute (elle contient le chat), ni l'inventaire, la
banque, les ventes, les personnages ou la configuration.

Un « enregistrement » est un dictionnaire JSON {kind, key, at, data} :
  - hdv     : les annonces en vente d'un objet au moment `at` ;
  - market  : le cours du marché d'un objet relevé à `at` ;
  - prices  : un relevé de prix moyens (clé = empreinte de son contenu) ;
  - keymap  : la table des clés d'un build (clé = signature du message des prix moyens).
Tout enregistrement reçu est validé (types, bornes) avant d'être appliqué, avec les mêmes règles de
fusion que la capture : le relevé le plus récent gagne.
"""
import json
import sqlite3
import time
from pathlib import Path

from .. import db, identify
from ..db import GRAIN_DAY, GRAIN_HOUR
from ..messages.hdv_listings import HdvListings, Listing

KINDS = ("hdv", "market", "prices", "keymap")
MAX_PRICE = 10**12
MAX_ITEM_ID = 10**7
FUTURE_S = 900.0  # tolérance sur l'horloge d'un autre PC
MAX_AGE_S = 400 * 86400.0
# Chevauchement à l'envoi : un relevé peut être écrit en base un peu après sa date de capture.
OVERLAP_S = 120.0


def _is_int(value, low: int = 0, high: int = MAX_PRICE) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _is_time(value, now: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and now - MAX_AGE_S <= value <= now + FUTURE_S


def validate(record, now: float | None = None) -> bool:
    """Vrai si l'enregistrement a exactement la forme attendue. Rien de douteux n'est accepté."""
    now = time.time() if now is None else now
    if not isinstance(record, dict) or record.get("kind") not in KINDS:
        return False
    key, at, data = record.get("key"), record.get("at"), record.get("data")
    if not isinstance(key, str) or not 0 < len(key) <= 200 or not _is_time(at, now) or not isinstance(data, dict):
        return False
    kind = record["kind"]
    try:
        if kind == "hdv":
            listings = data["listings"]
            if key != str(data["item_id"]) or not _is_int(data["item_id"], 1, MAX_ITEM_ID):
                return False
            if not isinstance(listings, list) or not 0 < len(listings) <= 2000:
                return False
            for uid, p1, p10, p100, p1000, effects in listings:
                prices = (p1, p10, p100, p1000)
                if not _is_int(uid, 1, 2**63) or not all(_is_int(p) for p in prices) or not any(prices):
                    return False
                if not isinstance(effects, list) or len(effects) > 100:
                    return False
                for effect_id, value in effects:
                    if not _is_int(effect_id, 0, 10**6) or not (value is None or _is_int(value, -(10**9), 10**9)):
                        return False
            return True
        if kind == "market":
            if key != str(data["item_id"]) or not _is_int(data["item_id"], 1, MAX_ITEM_ID):
                return False
            for grain in ("hour", "day"):
                points = data[grain]
                if not isinstance(points, list) or len(points) > 2000:
                    return False
                for bucket, price, quantity in points:
                    if not _is_time(bucket, now) or not _is_int(price) or not (quantity is None or _is_int(quantity)):
                        return False
            last = data.get("last")
            return last is None or (_is_int(last[0]) and _is_time(last[1], now) and len(last) == 2)
        if kind == "prices":
            prices = data["prices"]
            if not isinstance(prices, list) or not 500 <= len(prices) <= 100_000:
                return False
            return all(_is_int(item_id, 1, MAX_ITEM_ID) and _is_int(price, 1) for item_id, price in prices)
        # keymap
        entries = data["entries"]
        if not isinstance(entries, dict) or not 0 < len(entries) <= 50:
            return False
        for name, entry in entries.items():
            # Un nom inconnu n'est pas une erreur : c'est un message qu'une version plus récente sait décoder.
            # Il est transmis tel quel, et ignoré à l'arrivée par ceux qui ne le connaissent pas.
            if not isinstance(name, str) or not 0 < len(name) <= 40 or not name.replace("_", "").isalnum():
                return False
            if not isinstance(entry, dict) or not isinstance(entry.get("key"), str) or not 0 < len(entry["key"]) <= 40:
                return False
            fields = entry.get("fields")
            if not isinstance(fields, dict) or not all(isinstance(k, str) and _is_int(v, 0, 10**6) for k, v in fields.items()):
                return False
        return "avg_prices" in entries
    except (KeyError, TypeError, ValueError):
        return False


# --- ce que ce PC envoie -------------------------------------------------------


def keymap_signature(entry: dict) -> str:
    """Identifie un build : la clé et les champs de son message de prix moyens."""
    return entry["key"] + ":" + ",".join(f"{k}={v}" for k, v in sorted(entry["fields"].items()))


def collect(conn: sqlite3.Connection, since: float, keymap_path: Path | None = None) -> list[dict]:
    """Enregistrements des relevés écrits en base depuis `since`."""
    since -= OVERLAP_S
    records = []
    for (item_id,) in conn.execute("SELECT item_id FROM hdv_listings GROUP BY item_id HAVING MAX(captured_at) > ?", (since,)).fetchall():
        rows = conn.execute(
            "SELECT uid, p1, p10, p100, p1000, effects, captured_at FROM hdv_current WHERE item_id = ?", (item_id,)
        ).fetchall()
        if rows:
            listings = [[uid, p1, p10, p100, p1000, json.loads(effects)] for uid, p1, p10, p100, p1000, effects, _ in rows]
            records.append({"kind": "hdv", "key": str(item_id), "at": rows[0][6], "data": {"item_id": item_id, "listings": listings}})
    for item_id, at in conn.execute(
        "SELECT item_id, MAX(captured_at) FROM market_history GROUP BY item_id HAVING MAX(captured_at) > ?", (since,)
    ).fetchall():
        data = {"item_id": item_id, "hour": [], "day": [], "last": None}
        for period, bucket, price, quantity in conn.execute(
            "SELECT period, bucket_ts, price, qty_sold FROM market_history WHERE item_id = ? AND captured_at = ?", (item_id, at)
        ):
            data["hour" if period == GRAIN_HOUR else "day"].append([bucket, price, quantity])
        last = conn.execute("SELECT price, sold_at FROM last_sales WHERE item_id = ?", (item_id,)).fetchone()
        if last is not None:
            data["last"] = [last[0], last[1]]
        records.append({"kind": "market", "key": str(item_id), "at": at, "data": data})
    for snapshot_id, ts, digest in conn.execute("SELECT id, ts, content_hash FROM snapshots WHERE ts > ?", (since,)).fetchall():
        prices = conn.execute("SELECT item_id, price FROM avg_prices WHERE snapshot_id = ? AND price > 0", (snapshot_id,)).fetchall()
        records.append({"kind": "prices", "key": digest, "at": ts, "data": {"prices": [list(row) for row in prices]}})
    if keymap_path is not None and keymap_path.exists() and keymap_path.stat().st_mtime > since:
        raw = identify.read_keymap(keymap_path)
        entries = {
            name: {"key": e["key"], "fields": e["fields"], **({"partial": True} if e.get("partial") else {})}
            for name, e in raw.items()
            if name in identify.NAMES and not e.get("stale")
        }
        if "avg_prices" in entries:
            records.append({
                "kind": "keymap", "key": keymap_signature(entries["avg_prices"]),
                "at": keymap_path.stat().st_mtime, "data": {"entries": entries},
            })  # fmt: skip
    return records


# --- ce que ce PC reçoit -------------------------------------------------------


def apply(conn: sqlite3.Connection, record: dict, keymap_path: Path | None = None) -> bool:
    """Fusionne un enregistrement reçu dans la base locale. Renvoie False s'il est refusé."""
    if not validate(record):
        return False
    kind, at, data = record["kind"], record["at"], record["data"]
    if kind == "hdv":
        listings = tuple(
            Listing(uid, (p1, p10, p100, p1000), tuple((e, v) for e, v in effects))
            for uid, p1, p10, p100, p1000, effects in data["listings"]
        )
        db.save_hdv_listings(conn, HdvListings(data["item_id"], listings), at)
    elif kind == "market":
        for grain, name in ((GRAIN_HOUR, "hour"), (GRAIN_DAY, "day")):
            db.save_market_history(conn, data["item_id"], grain, [tuple(p) for p in data[name]], at)
        if data.get("last"):
            current = conn.execute("SELECT sold_at FROM last_sales WHERE item_id = ?", (data["item_id"],)).fetchone()
            if current is None or current[0] < data["last"][1]:
                db.save_last_sale(conn, data["item_id"], data["last"][0], data["last"][1], at)
    elif kind == "prices":
        db.save_snapshot(conn, at, {item_id: price for item_id, price in data["prices"]})
    elif keymap_path is not None and keymap_path.exists():
        # Table des clés d'un autre joueur : seulement si c'est le même build que le nôtre, et seulement
        # pour les messages qu'on n'a pas encore retrouvés soi-même.
        raw = identify.read_keymap(keymap_path)
        mine = raw.get("avg_prices")
        if not mine or mine.get("stale") or keymap_signature(mine) != record["key"]:
            return True
        found = {
            name: identify.Found(name, e["key"], e["fields"], bool(e.get("partial")))
            for name, e in data["entries"].items()
            if name in identify.pending(raw)
        }
        changed = identify.changes(raw, found)
        if changed:
            identify.write_keymap(keymap_path, changed)
    return True


# --- état de la synchronisation (table share_state) ---------------------------

_STATE = "CREATE TABLE IF NOT EXISTS share_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"


def get_state(conn: sqlite3.Connection) -> dict[str, str]:
    conn.execute(_STATE)
    return dict(conn.execute("SELECT key, value FROM share_state"))


def set_state(conn: sqlite3.Connection, **values) -> None:
    conn.execute(_STATE)
    with conn:
        conn.executemany("INSERT OR REPLACE INTO share_state VALUES (?, ?)", [(k, str(v)) for k, v in values.items()])
