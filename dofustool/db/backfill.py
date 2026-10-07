"""Rejoue l'archive brute dans data/market.sqlite avec les parseurs et le keymap courants.

Sans effet sur ce qui est déjà enregistré : les relevés sont dédoublonnés par contenu.

Usage : python -m dofustool.db.backfill
"""
import sqlite3
import sys

from .. import db
from ..archive import ARCHIVE_PATH
from ..messages import avg_prices, characters, hdv_listings, market_history, sales, storage, trades
from ..messages import load_runtime_keymap as load_keymap


def backfill(archive: sqlite3.Connection, market: sqlite3.Connection) -> dict[str, int]:
    keymap = load_keymap()
    counts = {"lus": 0, "relevés ajoutés": 0, "rejetés": 0}
    mapping = keymap["avg_prices"]
    rows = archive.execute("SELECT ts, body FROM messages WHERE key = ? ORDER BY ts", (mapping.key,))
    for ts, body in rows:
        counts["lus"] += 1
        prices = avg_prices.parse(body, mapping)
        if prices is None:
            counts["rejetés"] += 1
        elif db.save_snapshot(market, ts, prices) is not None:
            counts["relevés ajoutés"] += 1
    mapping = keymap.get("market_history")
    if mapping is not None:
        counts.update({"cours lus": 0, "cours enregistrés": 0, "cours rejetés": 0})
        for ts, body in archive.execute("SELECT ts, body FROM messages WHERE key = ? ORDER BY ts", (mapping.key,)):
            counts["cours lus"] += 1
            history = market_history.parse(body, mapping)
            if history is None:
                counts["cours rejetés"] += 1
            else:
                db.save_market(market, history, ts)
                counts["cours enregistrés"] += 1
    mapping = keymap.get("hdv_listings")
    if mapping is not None:
        counts.update({"listes HDV lues": 0, "listes HDV enregistrées": 0})
        # Dans l'ordre chronologique : la liste la plus récente d'un item remplace les précédentes.
        for ts, body in archive.execute("SELECT ts, body FROM messages WHERE key = ? ORDER BY ts", (mapping.key,)):
            counts["listes HDV lues"] += 1
            hdv = hdv_listings.parse(body, mapping)
            if hdv is not None:
                db.save_hdv_listings(market, hdv, ts)
                counts["listes HDV enregistrées"] += 1
    bank, inventory = keymap.get("bank"), keymap.get("inventory")
    if bank is not None:
        counts["banques lues"] = 0
        for ts, body in archive.execute("SELECT ts, body FROM messages WHERE key = ? ORDER BY ts", (bank.key,)):
            parsed = storage.parse(body, bank)
            if parsed is not None:
                db.save_holdings(market, db.BANK, parsed, ts)
                counts["banques lues"] += 1
    if inventory is not None:
        counts.update({"inventaires lus": 0, "listes fusionnées lues": 0})
        current: dict[int, storage.Storage] = {}  # dernier inventaire de chaque connexion
        rows = archive.execute(
            "SELECT connection_id, ts, body FROM messages WHERE key = ? ORDER BY connection_id, id", (inventory.key,)
        )
        for connection_id, ts, body in rows:
            parsed = storage.parse(body, inventory)
            if parsed is None:
                continue
            if storage.looks_merged(parsed, current.get(connection_id)):
                db.save_holdings(market, db.ALL, parsed, ts)
                counts["listes fusionnées lues"] += 1
            else:
                current[connection_id] = parsed
                db.save_holdings(market, db.INVENTORY, parsed, ts)
                counts["inventaires lus"] += 1
    mine = keymap.get("my_sales")
    if mine is not None:
        counts["listes de ventes lues"] = 0
        for ts, body in archive.execute(
            "SELECT ts, body FROM messages WHERE key = ? AND direction = 's2c' ORDER BY ts", (mine.key,)
        ):
            parsed = sales.parse(body, mine)
            if parsed is not None:
                db.save_sales(market, parsed, ts)
                counts["listes de ventes lues"] += 1
    text = keymap.get("info_text")
    if text is not None:
        counts["ventes et achats ajoutés"] = 0
        for source_id, ts, body in archive.execute(
            "SELECT id, ts, body FROM messages WHERE key = ? AND direction = 's2c' ORDER BY id", (text.key,)
        ):
            trade = trades.parse_text(body, text)
            if trade is not None and db.save_trade(market, source_id, trade, ts):
                counts["ventes et achats ajoutés"] += 1
    listing, select, jobs = (keymap.get(k) for k in ("character_list", "character_select", "job_levels"))
    if listing is not None:
        for ts, body in archive.execute(
            "SELECT ts, body FROM messages WHERE key = ? AND direction = 's2c' ORDER BY ts", (listing.key,)
        ):
            found = characters.parse_list(body, listing)
            if found is not None:
                db.save_characters(market, found, ts)
    if select is not None and jobs is not None:
        counts["relevés de métiers lus"] = 0
        chosen: dict[int, int] = {}  # personnage choisi sur chaque connexion
        rows = archive.execute(
            "SELECT connection_id, ts, direction, key, body FROM messages WHERE key IN (?, ?) ORDER BY connection_id, id",
            (select.key, jobs.key),
        )
        for connection_id, ts, direction, key, body in rows:
            if key == select.key and direction == "c2s":
                character_id = characters.parse_selection(body, select)
                if character_id is not None:
                    chosen[connection_id] = character_id
            elif key == jobs.key and direction == "s2c" and connection_id in chosen:
                levels = characters.parse_jobs(body, jobs)
                if levels is not None:
                    db.save_job_levels(market, chosen[connection_id], levels, ts)
                    counts["relevés de métiers lus"] += 1
    return counts


def main() -> int:
    if not ARCHIVE_PATH.exists():
        print("data/archive.sqlite absent : rien à rejouer.")
        return 1
    archive = sqlite3.connect(f"file:{ARCHIVE_PATH.as_posix()}?mode=ro", uri=True)
    market = db.connect()
    counts = backfill(archive, market)
    print("Rejeu de l'archive : " + ", ".join(f"{n} {label}" for label, n in counts.items()))
    archive.close()
    market.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
