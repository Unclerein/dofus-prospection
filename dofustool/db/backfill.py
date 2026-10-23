"""Rejoue l'archive brute dans data/market.sqlite avec les parseurs et le keymap courants.

Sans effet sur ce qui est déjà enregistré : les relevés sont dédoublonnés par contenu.

Usage : python -m dofustool.db.backfill
"""
import sqlite3
import sys
import time

from .. import db
from ..archive import ARCHIVE_PATH
from ..messages import avg_prices, breeding, characters, fm, hdv_listings, market_history, sales, storage, trades
from ..messages import exchange
from ..messages import inventory as moves
from ..messages import load_runtime_keymap as load_keymap

OFFLINE_LOGIN_WINDOW_S = 120.0  # comme capture.pipeline : l'annonce des ventes hors ligne arrive au début de la connexion


FM_BACKFILL_VERSION = 2  # 1 : état d'avant et d'après ; 2 : le personnage de chaque passage


def backfill_fm(archive: sqlite3.Connection, market: sqlite3.Connection, keymap=None) -> dict[str, int]:
    """Rejoue les passages de forgemagie de l'archive : ajoute ceux qui manquent, complète les autres
    (état d'avant et d'après, puits d'avant, type de ce qui a été posé, personnage), et relève les refus."""
    keymap = keymap or load_keymap()
    placed, outcome, select = keymap.get("fm_object"), keymap.get("fm_result"), keymap.get("character_select")
    if placed is None or outcome is None:
        return {}
    counts = {"passages de rune ajoutés": 0, "fabrications ajoutées": 0, "passages refusés relevés": 0}
    tools = {row[0]: fm.TOOL_TYPES[row[1]] for row in market.execute(fm.TOOLS_SQL)}
    rune: dict[int, int] = {}  # rune (ou potion, orbe…) posée sur l'atelier, par connexion
    item: dict[int, tuple[int, int]] = {}  # dernier objet posé, par connexion
    objects: dict[tuple[int, int], tuple] = {}  # derniers effets connus d'un objet, par connexion
    pools: dict[tuple[int, int], float] = {}  # puits d'un objet après son dernier passage, par connexion
    chosen: dict[int, int] = {}  # personnage choisi, par connexion
    rows = archive.execute(
        "SELECT id, connection_id, ts, key, body, direction FROM messages "
        "WHERE (key IN (?, ?) AND direction = 's2c') OR (key = ? AND direction = 'c2s') ORDER BY connection_id, id",
        (placed.key, outcome.key, select.key if select else ""),
    )
    for source_id, connection_id, ts, key, body, direction in rows:
        if direction == "c2s":
            character = characters.parse_selection(body, select)
            if character is not None:
                chosen[connection_id] = character
            continue
        if key == placed.key:
            obj = fm.parse_object(body, placed)
            if obj is not None and obj.item_id in tools:
                rune[connection_id] = obj.item_id
            elif obj is not None:
                objects[(connection_id, obj.uid)] = obj.effects
                item[connection_id] = (obj.uid, obj.item_id)
            continue
        result = fm.parse_result(body, outcome)
        used = rune.pop(connection_id, None)
        if result is None:
            refusal = fm.parse_refusal(body, outcome)
            if refusal is not None:
                at = item.get(connection_id)
                before = objects.get((connection_id, at[0])) if at else None
                counts["passages refusés relevés"] += db.save_fm_refusal(market, source_id, ts, *refusal, at, used, before)
            continue
        if not result.forged and result.passed:
            counts["fabrications ajoutées"] += db.save_craft(market, source_id, ts, result.object)
            continue
        if used is None or result.object.item_id in tools:
            continue
        slot = (connection_id, result.object.uid)
        if db.save_fm_pass(market, source_id, ts, result, used, objects.get(slot), tools[used], pools.get(slot), chosen.get(connection_id)):
            counts["passages de rune ajoutés"] += 1
        objects[slot] = result.object.effects
        pools[slot] = result.pool
    with market:  # fait une fois pour toutes, même si des passages n'ont pas pu être complétés (archive allégée)
        market.execute("DELETE FROM fm_backfill")
        market.execute("INSERT INTO fm_backfill VALUES (?)", (FM_BACKFILL_VERSION,))
    return counts


def fm_backfill_due(market: sqlite3.Connection) -> bool:
    """Des passages enregistrés avant une version qui garde plus de choses (état d'avant et d'après,
    personnage) : le rattrapage est à faire.

    Une seule fois par version : un passage dont les messages ne sont plus dans l'archive ne sera jamais
    complété, et le rattrapage ne doit pas se relancer à chaque démarrage pour lui."""
    done = market.execute("SELECT MAX(version) FROM fm_backfill").fetchone()[0] or 0
    if done < FM_BACKFILL_VERSION:
        return market.execute("SELECT 1 FROM fm_passes LIMIT 1").fetchone() is not None
    return False


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
    counts.update(_breeding(archive, market, keymap))
    bank, listing = keymap.get("bank"), keymap.get("inventory")
    opening = keymap.get("storage_open")
    if listing is not None and listing.fields.get("uid") and bank is not None and opening is not None:
        # Listes complètes dans l'ordre : chaque pile dit dans quels coffres elle est, et le type d'un coffre
        # ouvert seul est annoncé juste avant sa liste. Les mouvements isolés ne sont pas rejoués : la
        # dernière liste complète de chaque coffre suffit.
        counts.update({"listes d'inventaire lues": 0, "coffres lus": 0})
        opened: dict[int, str | None] = {}
        rows = archive.execute(
            "SELECT connection_id, ts, key, body FROM messages WHERE key IN (?, ?, ?) AND direction = 's2c' ORDER BY id",
            (listing.key, bank.key, opening.key),
        )
        for connection_id, ts, key, body in rows:
            if key == opening.key:
                kind = moves.parse_single(body, opening, "type")
                opened[connection_id] = db.PILE_CONTAINERS.get(moves.STORAGE_TYPES.get(kind))
            elif key == bank.key:
                parsed, container = storage.parse(body, bank), opened.pop(connection_id, None)
                if parsed is not None and container is not None:
                    db.save_piles(market, parsed, ts, only=container)
                    counts["coffres lus"] += 1
            else:
                parsed = storage.parse(body, listing)
                if parsed is not None:
                    db.save_piles(market, parsed, ts)
                    counts["listes d'inventaire lues"] += 1
        bank = inventory_legacy = None
    else:
        inventory_legacy = listing
    inventory = inventory_legacy
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
            parsed = sales.parse(body, mine, keymap.get("hdv_listings"))
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
    counts.update(backfill_fm(archive, market, keymap))
    names = ("exchange_started", "fm_object", "exchange_modified", "exchange_removed", "exchange_kamas", "exchange_closed")
    if all(name in keymap for name in names):
        counts["échanges ajoutés"] = 0
        by_key = {keymap[name].key: name for name in names}
        trackers: dict[int, exchange.Tracker] = {}
        marks = ",".join("?" * len(by_key))
        rows = archive.execute(
            f"SELECT id, connection_id, ts, key, body FROM messages WHERE key IN ({marks}) AND direction = 's2c' ORDER BY id",
            sorted(by_key),
        )
        for source_id, connection_id, ts, key, body in rows:
            name, tracker = by_key[key], trackers.setdefault(connection_id, exchange.Tracker())
            if name == "exchange_started":
                tracker.start()
            elif tracker.current is None:
                continue
            elif name in ("fm_object", "exchange_modified"):
                found = exchange.parse_placed(body, keymap[name])
                if found is not None:
                    tracker.place(*found)
            elif name == "exchange_removed":
                found = exchange.parse_flagged(body, keymap[name], "uid")
                if found is not None:
                    tracker.remove(*found)
            elif name == "exchange_kamas":
                found = exchange.parse_flagged(body, keymap[name], "amount")
                if found is not None:
                    tracker.kamas(*found)
            else:
                done = tracker.close(bool(exchange.parse_closed(body, keymap[name])))
                if done is not None:
                    counts["échanges ajoutés"] += db.save_exchange(market, source_id, ts, done)
    offline = keymap.get("offline_sales")
    if offline is not None and mine is not None:
        counts["ventes hors ligne retrouvées"] = _offline_sales(archive, market, keymap)
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


def _breeding(archive: sqlite3.Connection, market: sqlite3.Connection, keymap) -> dict[str, int]:
    """L'étable et les enclos, dans l'ordre : la dernière vue de chaque monture et de chaque enclos l'emporte.
    Une mise à jour d'enclos ne dit pas lequel c'est : celui ouvert en dernier sur la même connexion."""
    stable, paddock, update = keymap.get("stable"), keymap.get("paddock"), keymap.get("paddock_update")
    if stable is None:
        return {}
    counts = {"étables lues": 0, "enclos lus": 0}
    keys = [m.key for m in (stable, paddock, update) if m is not None]
    opened: dict[int, int] = {}
    rows = archive.execute(
        f"SELECT connection_id, ts, key, body FROM messages WHERE key IN ({','.join('?' * len(keys))}) ORDER BY ts", keys
    )
    for connection_id, ts, key, body in rows:
        if key == stable.key and (mounts := breeding.parse_stable(body, stable)) is not None:
            db.save_stable(market, mounts, ts)
            counts["étables lues"] += 1
        elif paddock is not None and key == paddock.key and (seen := breeding.parse_paddock(body, paddock, stable)) is not None:
            opened[connection_id] = seen.paddock_id
            db.save_paddock(market, seen.paddock_id, seen, ts)
            counts["enclos lus"] += 1
        elif update is not None and key == update.key and connection_id in opened:
            if (seen := breeding.parse_paddock_update(body, update, stable)) is not None:
                db.save_paddock(market, opened[connection_id], seen, ts)
    return counts


def _offline_sales(archive: sqlite3.Connection, market: sqlite3.Connection, keymap) -> int:
    """Retrouve les ventes conclues hors ligne en rejouant, dans l'ordre, annonces du jeu, relevés et ventes en ligne.

    Le rejeu se fait dans une base vide en mémoire : la vraie base ne garde que le dernier relevé de
    chaque HDV, alors qu'il faut ici comparer chaque relevé au précédent. Seul le résultat est recopié.
    """
    names = ("offline_sales", "my_sales", "my_sale_update", "my_sale_removed", "info_text")
    by_key = {keymap[name].key: name for name in names if name in keymap}
    marks = ",".join("?" * len(by_key))
    scratch = db.connect(":memory:")
    rows = archive.execute(
        f"SELECT m.id, m.ts, m.key, m.body, m.connection_id, c.started_at FROM messages m JOIN connections c ON c.id = m.connection_id "
        f"WHERE m.key IN ({marks}) AND m.direction = 's2c' ORDER BY m.id",
        sorted(by_key),
    )
    announced: set[int] = set()  # connexions dont l'annonce de connexion a déjà été lue
    updates = []  # lots mis en vente ou modifiés un par un, dans l'ordre
    for source_id, ts, key, body, connection_id, started_at in rows:
        name = by_key[key]
        mapping = keymap[name]
        if name == "offline_sales":
            total = fm.parse_offline_total(body, mapping)
            if total is not None:
                # Comme la capture : seule la première annonce d'une connexion, à son début, parle des ventes hors ligne.
                at_login = connection_id not in announced and ts - started_at <= OFFLINE_LOGIN_WINDOW_S
                announced.add(connection_id)
                db.save_offline_total(scratch, source_id, total, ts, at_login)
        elif name == "my_sales":
            parsed = sales.parse(body, mapping)
            if parsed is not None:
                db.save_sales(scratch, parsed, ts)
        elif name == "my_sale_update":
            lot = trades.parse_lot_update(body, mapping, keymap.get("hdv_listings"))
            if lot is not None:
                db.save_lot_update(scratch, lot, ts)
                updates.append((lot, ts))
        elif name == "my_sale_removed":
            uid = moves.parse_single(body, mapping, "uid")
            if uid:
                db.remove_lot(scratch, uid, ts)
                db.remove_lot(market, uid, ts)  # un lot retiré depuis le dernier relevé quitte aussi la vraie base
        else:
            trade = trades.parse_text(body, mapping)
            if trade is not None:
                db.save_trade(scratch, source_id, trade, ts)
    # Un lot mis en vente depuis le dernier relevé de l'onglet Vendre n'est dans aucune liste : il est remis,
    # s'il est toujours en vente à la fin du rejeu (ni vendu, ni retiré, ni remplacé depuis).
    last = market.execute("SELECT MAX(captured_at) FROM my_sales_meta").fetchone()[0]
    for lot, ts in updates:
        if last is not None and ts > last and scratch.execute("SELECT 1 FROM my_sales WHERE uid = ? AND captured_at = ?", (lot.uid, ts)).fetchone():
            db.save_lot_update(market, lot, ts)
    added = 0
    with market:
        # Le rejeu fait foi : une annonce mal lue par une version précédente (kamas déposés en banque pris pour des
        # ventes hors ligne) est corrigée, pas seulement complétée.
        for row in scratch.execute("SELECT * FROM offline_sales"):
            market.execute(
                "INSERT INTO offline_sales VALUES (?, ?, ?, ?, ?) ON CONFLICT (source_id) DO UPDATE SET "
                "total = excluded.total, delta = excluded.delta, attributed = excluded.attributed",
                row,
            )
        # Une vente déjà retrouvée reste retrouvée, et les lots disparus encore sans explication sont repris.
        market.execute(
            "UPDATE offline_sales SET attributed = 1 WHERE attributed = 0 AND EXISTS ("
            " SELECT 1 FROM trades WHERE source_id BETWEEN -(offline_sales.source_id * 100 + 99) AND -(offline_sales.source_id * 100))"
        )
        market.execute("DELETE FROM gone_lots")
        for row in scratch.execute("SELECT * FROM gone_lots"):
            market.execute("INSERT OR REPLACE INTO gone_lots VALUES (?, ?, ?, ?, ?)", row)
        for row in scratch.execute("SELECT * FROM trades WHERE source_id < 0"):
            added += market.execute("INSERT OR IGNORE INTO trades VALUES (?, ?, ?, ?, ?, ?, ?)", row).rowcount
            market.execute(
                "UPDATE fm_items SET sold_price = ?, sold_at = ? WHERE lot_uid = ? AND sold_at IS NULL", (row[5], row[1], row[6])
            )
    db.link_fm_lots(market)
    scratch.close()
    return added


def main() -> int:
    if not ARCHIVE_PATH.exists():
        print("data/archive.sqlite absent : rien à rejouer.")
        return 1
    archive = sqlite3.connect(f"file:{ARCHIVE_PATH.as_posix()}?mode=ro", uri=True)
    market = db.connect()
    counts = backfill(archive, market)
    # Le rejeu vient de réenregistrer d'anciens relevés de prix moyens : ne garder qu'un par jour, comme la capture.
    db.thin_snapshots(market, time.time())
    print("Rejeu de l'archive : " + ", ".join(f"{n} {label}" for label, n in counts.items()))
    archive.close()
    market.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
