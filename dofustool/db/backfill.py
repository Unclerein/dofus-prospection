"""Rejoue l'archive brute dans data/market.sqlite avec les parseurs et le keymap courants.

Sans effet sur ce qui est déjà enregistré : les relevés sont dédoublonnés par contenu.

Usage : python -m dofustool.db.backfill
"""
import sqlite3
import sys

from .. import db
from ..archive import ARCHIVE_PATH
from ..messages import avg_prices, load_keymap


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
    return counts


def main() -> int:
    if not ARCHIVE_PATH.exists():
        print("data/archive.sqlite absent : rien à rejouer.")
        return 1
    archive = sqlite3.connect(f"file:{ARCHIVE_PATH.as_posix()}?mode=ro", uri=True)
    market = db.connect()
    counts = backfill(archive, market)
    print("Prix moyens : " + ", ".join(f"{n} {label}" for label, n in counts.items()))
    archive.close()
    market.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
