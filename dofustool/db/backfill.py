"""Rejoue l'archive brute dans data/market.sqlite avec les parseurs et le keymap courants.

Sans effet sur ce qui est déjà enregistré : les relevés sont dédoublonnés par contenu.

Usage : python -m dofustool.db.backfill
"""
import sqlite3
import sys

from .. import db
from ..archive import ARCHIVE_PATH
from ..messages import avg_prices, hdv_listings, load_keymap, market_history


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
