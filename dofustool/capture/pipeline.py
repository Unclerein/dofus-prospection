"""Traitement d'une capture live : archivage, décodage des messages connus, écriture en base.

Ne plante jamais sur une donnée inattendue et n'écrit rien de douteux : un message qui
n'a pas exactement la forme attendue est ignoré (il reste dans l'archive brute).
"""
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .. import db, identify
from ..archive import Archive
from ..messages import Mapping, avg_prices, characters, exchange, fm, hdv_listings, inventory, load_keymap, market_history, sales, storage, trades
from ..protocol.session import Message, Session
from ..protocol.tcp import C2S, S2C
from . import Segment

log = logging.getLogger("dofustool.capture")

# Sans trafic depuis ce délai, la connexion n'est plus considérée comme active.
ACTIVE_WINDOW_S = 30.0
# Tant qu'un message reste à retrouver (après une mise à jour du jeu), on le cherche à cet intervalle.
IDENTIFY_EVERY_S = 20.0
# Le jeu envoie les prix moyens au choix du personnage, puis une fois par heure à partir de là. Une
# capture lancée alors que le jeu est déjà connecté rate le premier envoi et attend le suivant, jusqu'à
# une heure. Passé le délai de la configuration, la capture cherche si les clés ont changé ; elle
# n'alerte qu'après ce délai-ci, plus d'une heure de jeu sans aucun prix moyen.
ALERT_AFTER_S = 70 * 60.0
# L'annonce des ventes hors ligne arrive avec l'inventaire, dans les secondes qui suivent le choix du personnage.
OFFLINE_LOGIN_WINDOW_S = 120.0
# Une connexion déjà active dans les secondes qui suivent le démarrage de la capture existait avant elle.
LATE_START_S = 10.0


@dataclass(slots=True)
class _ConnWatch:
    first_ts: float
    last_ts: float
    archive_id: int | None
    got_prices: bool = False
    waiting: bool = False  # délai de la configuration dépassé, prix moyens toujours attendus
    late: bool = False  # la capture a démarré alors que cette connexion était déjà ouverte
    coffre: str | None = None  # coffre que le joueur vient d'ouvrir (banque ou havre-sac) : sa liste suit
    offline_announced: bool = False  # l'annonce des ventes hors ligne de cette connexion a déjà été reçue
    trade: exchange.Tracker = field(default_factory=exchange.Tracker)  # échange en cours avec un autre joueur
    visible: set = field(default_factory=lambda: {db.INVENTORY})  # coffres de la dernière liste reçue
    alerted: bool = False
    next_build_check: float = 0.0  # prochaine recherche de nouvelles clés, tant que les prix manquent
    inventory: storage.Storage | None = None  # dernier inventaire de cette connexion
    character_id: int | None = None  # personnage choisi sur cette connexion
    scan: identify.Scan | None = None  # ce que la connexion a échangé, pour retrouver les messages
    fm_rune: int | None = None  # rune posée sur l'atelier de forgemagie, en attente de son résultat
    fm_objects: dict[int, tuple] = field(default_factory=dict)  # derniers effets connus des objets posés sur l'atelier


class Pipeline:
    def __init__(
        self,
        archive: Archive,
        market: sqlite3.Connection,
        keymap: dict[str, Mapping],
        avg_prices_timeout_s: float = 60.0,
        keymap_path: Path | None = None,
        started_at: float | None = None,
    ) -> None:
        # started_at : heure de démarrage de la capture, pour reconnaître une connexion ouverte avant elle.
        self._started_at = started_at
        # Si le chemin est donné, keymap.json est relu dès qu'il change : après une mise à jour du jeu,
        # la correction des clés prend effet sans relancer la capture (ni perdre la connexion en cours).
        self._keymap_path = keymap_path
        self._keymap_mtime = keymap_path.stat().st_mtime if keymap_path is not None and keymap_path.exists() else None
        self.session = Session()
        self.archive = archive
        self.market = market
        self.keymap = keymap
        self.timeout = avg_prices_timeout_s
        self._watch: dict[int, _ConnWatch] = {}
        self._errors: set[str] = set()
        self._next_identify = 0.0
        self._context: identify.Context | None = None
        self._runes: frozenset[int] | None = None
        self._stock_moved: float | None = None  # dernier mouvement d'inventaire pas encore signalé à l'interface
        self.backup_dir = db.MARKET_PATH.parent / "keymap-backups"

    def handle(self, segment: Segment) -> None:
        try:
            for msg in self.session.feed(segment):
                self._on_message(msg)
        except Exception as exc:  # une erreur de décodage ne doit jamais arrêter la capture
            self._log_error_once(exc)

    def _on_message(self, msg: Message) -> None:
        watch = self._watch.get(msg.conn)
        if watch is None:
            conn = self.session.states[msg.conn].connection
            archive_id = self.archive.open_connection(conn.first_ts, conn.key.client_port, conn.key.server, "live")
            watch = self._watch[msg.conn] = _ConnWatch(conn.first_ts, msg.ts, archive_id)
            watch.late = self._started_at is not None and conn.first_ts - self._started_at <= LATE_START_S
            db.set_status(self.market, last_connection_ts=conn.first_ts)
        watch.last_ts = msg.ts
        source_id = self.archive.add(watch.archive_id, msg) if watch.archive_id is not None else None
        self._decode(msg, watch, source_id)

    def _decode(self, msg: Message, watch: _ConnWatch, source_id: int | None = None) -> None:
        mapping = self.keymap.get("market_history")
        if mapping is not None and msg.key == mapping.key:
            history = market_history.parse(msg.body, mapping)
            if history is not None:
                db.save_market(self.market, history, msg.ts)
                row = self.market.execute("SELECT name FROM items WHERE id = ?", (history.item_id,)).fetchone()
                log.info(
                    "Cours du marché enregistré : %s (%d points horaires, %d journaliers).",
                    row[0] if row else f"item {history.item_id}",
                    len(history.hourly),
                    len(history.daily),
                )
            return

        mapping = self.keymap.get("character_select")
        if mapping is not None and msg.key == mapping.key and msg.direction == C2S:
            chosen = characters.parse_selection(msg.body, mapping)
            if chosen is not None:
                watch.character_id = chosen
            return

        mapping = self.keymap.get("character_list")
        if mapping is not None and msg.key == mapping.key and msg.direction == S2C:
            found = characters.parse_list(msg.body, mapping)
            if found is not None:
                db.save_characters(self.market, found, msg.ts)
            return

        mapping = self.keymap.get("job_levels")
        if mapping is not None and msg.key == mapping.key and msg.direction == S2C:
            jobs = characters.parse_jobs(msg.body, mapping)
            if jobs is not None and watch.character_id is not None:
                db.save_job_levels(self.market, watch.character_id, jobs, msg.ts)
                if len(jobs) > 1:  # la liste complète de la connexion, pas un gain d'expérience
                    log.info("Métiers enregistrés : %d.", len(jobs))
            return

        mapping = self.keymap.get("info_text")
        if mapping is not None and msg.key == mapping.key and msg.direction == S2C:
            trade = trades.parse_text(msg.body, mapping)
            # Sans numéro d'archive (connexion déjà importée), rien n'est compté : pas de doublon possible.
            if trade is not None and source_id is not None and db.save_trade(self.market, source_id, trade, msg.ts):
                row = self.market.execute("SELECT name FROM items WHERE id = ?", (trade.item_id,)).fetchone()
                log.info(
                    "%s : %s x%d, %d kamas.",
                    "Vente" if trade.kind == trades.SALE else "Achat",
                    row[0] if row else f"item {trade.item_id}", trade.quantity, trade.price,
                )  # fmt: skip
            return

        mapping = self.keymap.get("my_sale_removed")
        if mapping is not None and msg.key == mapping.key and msg.direction == S2C:
            # Retrait d'un lot, ou première moitié d'un changement de prix : le lot recréé suit aussitôt.
            uid = inventory.parse_single(msg.body, mapping, "uid")
            if uid:
                db.remove_lot(self.market, uid, msg.ts)
            return

        mapping = self.keymap.get("my_sale_update")
        if mapping is not None and msg.key == mapping.key and msg.direction == S2C:
            lot = trades.parse_lot_update(msg.body, mapping, self.keymap.get("hdv_listings"))
            if lot is not None:
                db.save_lot_update(self.market, lot, msg.ts)
            return

        if msg.direction == S2C and self._trade(msg, watch, source_id):
            return

        mapping = self.keymap.get("fm_object")
        if mapping is not None and msg.key == mapping.key and msg.direction == S2C:
            placed = fm.parse_object(msg.body, mapping)
            if placed is not None:
                if placed.item_id in self._rune_ids():
                    watch.fm_rune = placed.item_id
                else:
                    watch.fm_objects[placed.uid] = placed.effects
            return

        mapping = self.keymap.get("fm_result")
        if mapping is not None and msg.key == mapping.key and msg.direction == S2C:
            result = fm.parse_result(msg.body, mapping)
            rune, watch.fm_rune = watch.fm_rune, None
            if result is not None and not result.forged and result.passed and source_id is not None:
                # Ni puits ni rune : une fabrication ordinaire, qui entre dans le journal des crafts.
                if db.save_craft(self.market, source_id, msg.ts, result.object):
                    row = self.market.execute("SELECT name FROM items WHERE id = ?", (result.object.item_id,)).fetchone()
                    log.info("Fabrication : %s x%d.", row[0] if row else f"item {result.object.item_id}", result.object.quantity)
                return
            # Sans rune posée, c'est le résultat d'une autre fabrication, pas un passage de rune.
            if result is None or rune is None or not result.forged or result.object.item_id in self._rune_ids():
                return
            uid = result.object.uid
            known = self.market.execute("SELECT 1 FROM fm_items WHERE uid = ?", (uid,)).fetchone()
            if source_id is not None and db.save_fm_pass(self.market, source_id, msg.ts, result, rune, watch.fm_objects.get(uid)):
                if known is None:
                    row = self.market.execute("SELECT name FROM items WHERE id = ?", (result.object.item_id,)).fetchone()
                    log.info("Forgemagie : premier passage de rune sur %s.", row[0] if row else f"item {result.object.item_id}")
            watch.fm_objects[uid] = result.object.effects
            return

        mapping = self.keymap.get("offline_sales")
        if mapping is not None and msg.key == mapping.key and msg.direction == S2C:
            total = fm.parse_offline_total(msg.body, mapping)
            if total is not None and source_id is not None:
                # Seule la première annonce d'une connexion, reçue avec l'inventaire, parle des ventes hors ligne.
                at_login = not watch.offline_announced and not watch.late and msg.ts - watch.first_ts <= OFFLINE_LOGIN_WINDOW_S
                watch.offline_announced = True
                gained = db.save_offline_total(self.market, source_id, total, msg.ts, at_login)
                if gained:
                    log.info("Ventes hors ligne : %d kamas depuis la dernière annonce.", gained)
            return

        mapping = self.keymap.get("unsold_returned")
        if mapping is not None and msg.key == mapping.key and msg.direction == S2C:
            count = inventory.parse_single(msg.body, mapping, "count")
            for item_id, lot, price in db.return_unsold(self.market, count or 0, msg.ts):
                row = self.market.execute("SELECT name FROM items WHERE id = ?", (item_id,)).fetchone()
                log.info("Lot invendu rentré en banque : %s x%d (mis en vente à %d kamas).", row[0] if row else f"item {item_id}", lot, price)
            return

        mapping = self.keymap.get("my_sales")
        if mapping is not None and msg.key == mapping.key and msg.direction == S2C:
            listing = sales.parse(msg.body, mapping, self.keymap.get("hdv_listings"))
            if listing is not None and db.save_sales(self.market, listing, msg.ts):
                log.info("Lots en vente enregistrés : %d.", len(listing.sales))
            return

        if self._stock_moves(msg, watch):
            return

        mapping = self.keymap.get("bank")
        if mapping is not None and msg.key == mapping.key:
            bank = storage.parse(msg.body, mapping)
            opened, watch.coffre = watch.coffre, None
            if bank is None:
                return
            if mapping.fields.get("uid") and "storage_open" in self.keymap:
                # Même message pour la banque et le havre-sac : seul le type annoncé juste avant les distingue.
                if opened is not None and db.save_piles(self.market, bank, msg.ts, only=opened):
                    log.info("%s : %d piles.", "Banque enregistrée" if opened == db.BANK else "Havre-sac enregistré", len(bank.stacks))
            elif db.save_holdings(self.market, db.BANK, bank, msg.ts):
                log.info("Banque enregistrée : %d piles.", len(bank.stacks))
            return

        mapping = self.keymap.get("inventory")
        if mapping is not None and msg.key == mapping.key:
            listing = storage.parse(msg.body, mapping)
            if listing is not None and mapping.fields.get("uid"):
                # Liste simple (inventaire) ou réunie (HDV, atelier) : chaque pile dit dans quels coffres elle est.
                first = watch.inventory is None
                watch.inventory = listing
                watch.visible = {db.INVENTORY} | {db.PILE_CONTAINERS[part] for s in listing.stacks for part, _ in s.parts}
                if db.save_piles(self.market, listing, msg.ts) and first:
                    log.info("Inventaire enregistré : %d piles.", len(listing.stacks))
            elif listing is not None:
                # Même clé pour l'inventaire et pour la liste fusionnée envoyée à l'ouverture de l'HDV.
                if storage.looks_merged(listing, watch.inventory):
                    db.save_holdings(self.market, db.ALL, listing, msg.ts)
                else:
                    first = watch.inventory is None
                    watch.inventory = listing
                    if db.save_holdings(self.market, db.INVENTORY, listing, msg.ts) and first:
                        log.info("Inventaire enregistré : %d piles.", len(listing.stacks))
            return

        mapping = self.keymap.get("hdv_listings")
        if mapping is not None and msg.key == mapping.key:
            hdv = hdv_listings.parse(msg.body, mapping)
            if hdv is not None:  # une réponse vide (fiche refermée) n'efface pas les annonces connues
                db.save_hdv_listings(self.market, hdv, msg.ts)
                row = self.market.execute("SELECT name FROM items WHERE id = ?", (hdv.item_id,)).fetchone()
                log.info(
                    "Annonces HDV enregistrées : %s (%d).", row[0] if row else f"item {hdv.item_id}", len(hdv.listings)
                )
            return

        mapping = self.keymap.get("avg_prices")
        if mapping is not None and msg.key == mapping.key:
            prices = avg_prices.parse(msg.body, mapping)
            if prices is None:
                return  # même clé, autre forme : laissé au chien de garde
            watch.got_prices = True
            snapshot_id = db.save_snapshot(self.market, msg.ts, prices)
            if snapshot_id is None:
                log.info("Prix moyens reçus (%d items) : identiques au dernier relevé, non réenregistrés.", len(prices))
            else:
                log.info("Relevé de prix moyens enregistré : %d items.", len(prices))
            db.set_status(self.market, last_avg_prices_ts=msg.ts, decode_alert="", awaiting_prices_since="")
            watch.waiting = False
            if watch.alerted:
                watch.alerted = False
                log.info("Alerte levée : les prix moyens ont fini par être décodés.")

    def _trade(self, msg: Message, watch: _ConnWatch, source_id: int | None) -> bool:
        """Échange avec un autre joueur. Renvoie True si le message lui appartenait.

        Les objets posés et la clôture passent par les mêmes messages qu'un atelier : hors d'un échange
        ouvert, ils sont laissés aux autres traitements.
        """
        keymap, trade = self.keymap, watch.trade
        mapping = keymap.get("exchange_started")
        if mapping is not None and msg.key == mapping.key:
            trade.start()
            return True
        if trade.current is None:
            return False
        for name in ("fm_object", "exchange_modified"):
            mapping = keymap.get(name)
            if mapping is not None and msg.key == mapping.key:
                placed = exchange.parse_placed(msg.body, mapping)
                if placed is not None:
                    trade.place(*placed)
                return True
        mapping = keymap.get("exchange_removed")
        if mapping is not None and msg.key == mapping.key:
            removed = exchange.parse_flagged(msg.body, mapping, "uid")
            if removed is not None:
                trade.remove(*removed)
            return True
        mapping = keymap.get("exchange_kamas")
        if mapping is not None and msg.key == mapping.key:
            offered = exchange.parse_flagged(msg.body, mapping, "amount")
            if offered is not None:
                trade.kamas(*offered)
            return True
        mapping = keymap.get("exchange_closed")
        if mapping is not None and msg.key == mapping.key:
            done = trade.close(bool(exchange.parse_closed(msg.body, mapping)))
            if done is not None and source_id is not None and db.save_exchange(self.market, source_id, msg.ts, done):
                log.info(
                    "Échange conclu : %d objets donnés, %d reçus, %d kamas donnés, %d reçus.",
                    len(done.given), len(done.received), done.kamas_given, done.kamas_received,
                )  # fmt: skip
            return True
        return False

    def _stock_moves(self, msg: Message, watch: _ConnWatch) -> bool:
        """Mouvements de l'inventaire entre deux listes complètes. Renvoie True si le message en était un."""
        if msg.direction != S2C:
            return False
        keymap, listing = self.keymap, self.keymap.get("inventory")
        mapping = keymap.get("storage_open")
        if mapping is not None and msg.key == mapping.key:
            kind = inventory.parse_single(msg.body, mapping, "type")
            watch.coffre = db.PILE_CONTAINERS.get(inventory.STORAGE_TYPES.get(kind))
            return True
        if listing is None or not listing.fields.get("uid"):
            return False
        mapping = keymap.get("pile_update")
        if mapping is not None and msg.key == mapping.key:
            update = inventory.parse_pile_update(msg.body, mapping)
            if update is not None and db.update_pile(self.market, update, watch.visible, msg.ts):
                self._stock_moved = msg.ts
            return True
        for name in ("object_added", "object_created", "object_modified"):
            mapping = keymap.get(name)
            if mapping is not None and msg.key == mapping.key:
                obj = inventory.parse_object(msg.body, mapping, listing, storage.BAG_POSITION)
                if obj is not None and db.add_pile(self.market, obj, msg.ts):
                    self._stock_moved = msg.ts
                return True
        mapping = keymap.get("object_removed")
        if mapping is not None and msg.key == mapping.key:
            uid = inventory.parse_single(msg.body, mapping, "uid")
            if uid and db.remove_pile(self.market, uid, msg.ts):
                self._stock_moved = msg.ts
            return True
        mapping = keymap.get("kamas")
        if mapping is not None and msg.key == mapping.key:
            kamas = inventory.parse_single(msg.body, mapping, "kamas")
            if kamas is not None:
                db.set_kamas(self.market, kamas, msg.ts)
                self._stock_moved = msg.ts
            return True
        return False

    def _rune_ids(self) -> frozenset[int]:
        if self._runes is None:
            self._runes = frozenset(row[0] for row in self.market.execute(identify.RUNES_SQL))
        return self._runes

    def tick(self, now: float) -> None:
        """À appeler régulièrement : valide l'archive et surveille le décodage."""
        try:
            self._reload_keymap()
            self.archive.commit()
            for watch in self._watch.values():
                active = now - watch.last_ts <= ACTIVE_WINDOW_S
                if active and not watch.got_prices and not watch.alerted and now - watch.first_ts > self.timeout:
                    # Une mise à jour du jeu a peut-être changé les clés : on cherche régulièrement.
                    if now >= watch.next_build_check:
                        watch.next_build_check = now + IDENTIFY_EVERY_S
                        self._identify(watch, new_build=True)
                        if watch.got_prices:
                            continue
                    if not watch.waiting:
                        watch.waiting = True
                        if watch.late:
                            log.info("Capture lancée après la connexion au jeu : les prix moyens arriveront dans l'heure.")
                        else:
                            log.info("Prix moyens pas encore reçus : ils arrivent au choix du personnage.")
                        db.set_status(self.market, awaiting_prices_since=watch.first_ts, awaiting_prices_late=int(watch.late))
                    if now - watch.first_ts <= ALERT_AFTER_S:
                        continue
                    watch.alerted = True
                    text = (
                        f"Flux de jeu actif depuis {(now - watch.first_ts) / 60:.0f} min sans prix moyens décodés. "
                        "Le jeu les envoie au moins toutes les heures : une mise à jour du jeu a sans doute changé "
                        "les clés, voir MAINTENANCE.md."
                    )
                    log.warning("ALERTE : %s", text)
                    db.set_status(self.market, decode_alert=text, decode_alert_ts=now)
            if now >= self._next_identify:
                self._next_identify = now + IDENTIFY_EVERY_S
                for watch in self._watch.values():
                    if now - watch.last_ts <= ACTIVE_WINDOW_S:
                        self._identify(watch, new_build=False)
            # Les derniers mouvements d'inventaire, restés sous le seuil de signalement, finissent par être signalés.
            if self._stock_moved is not None and now - self._stock_moved >= db.STOCK_SIGNAL_EVERY_S:
                db.flush_stock_signal(self.market, self._stock_moved)
                self._stock_moved = None
            db.set_status(self.market, heartbeat_ts=now)
        except Exception as exc:
            self._log_error_once(exc)

    def _identify(self, watch: _ConnWatch, new_build: bool) -> None:
        """Retrouve par leur structure les messages que keymap.json ne connaît pas (ou plus) et les décode.

        new_build : les prix moyens ne sont pas décodés, donc toutes les clés sont peut-être périmées.
        """
        if self._keymap_path is None or watch.archive_id is None or not self._keymap_path.exists():
            return
        raw = identify.read_keymap(self._keymap_path)
        wanted = identify.NAMES if new_build else tuple(identify.pending(raw))
        if not wanted:
            return
        if watch.scan is None:
            watch.scan = identify.Scan(watch.first_ts)
        watch.scan.feed(self.archive.db, watch.archive_id)
        if self._context is None:
            self._context = identify.Context.load(self.market)
        found, problems = identify.identify(watch.scan, self._context, wanted)
        if new_build:
            prices = found.get("avg_prices")
            current = raw.get("avg_prices", {})
            if prices is None:
                return  # pas de liste de prix dans le flux : le personnage n'est sans doute pas encore choisi
            new_build = current.get("key") != prices.key or current.get("fields") != prices.fields
        changed = identify.changes(raw, found)
        if not changed and not new_build:
            return
        identify.write_keymap(self._keymap_path, found if new_build else changed, new_build, self.backup_dir)
        if new_build:
            log.warning("Mise à jour du jeu détectée : les clés des messages ont changé.")
        for name, item in sorted(changed.items()):
            log.info("Message retrouvé : %s (clé %s)%s.", name, item.key, ", sans les effets d'équipement" if item.partial else "")
        for name, problem in sorted(problems.items()):
            log.warning("Message %s non retrouvé : %s.", name, problem)
        self._reload_keymap()
        self._context = None  # les prix moyens de contrôle vont changer
        self._replay(watch, {item.key for item in changed.values()})

    def _replay(self, watch: _ConnWatch, keys: set[str]) -> None:
        """Décode ce que la connexion avait déjà reçu sous des clés tout juste retrouvées."""
        if not keys:
            return
        marks = ",".join("?" * len(keys))
        rows = self.archive.db.execute(
            f"SELECT id, ts, direction, key, frame_field, correlation, body FROM messages "
            f"WHERE connection_id = ? AND key IN ({marks}) ORDER BY id",
            (watch.archive_id, *sorted(keys)),
        ).fetchall()
        for source_id, ts, direction, key, frame_field, correlation, body in rows:
            self._decode(Message(0, ts, direction, key, body, frame_field, correlation), watch, source_id)

    def _reload_keymap(self) -> None:
        if self._keymap_path is None or not self._keymap_path.exists():
            return
        mtime = self._keymap_path.stat().st_mtime
        if mtime == self._keymap_mtime:
            return
        self._keymap_mtime = mtime
        try:
            self.keymap = load_keymap(self._keymap_path)
        except (ValueError, KeyError, TypeError) as exc:  # fichier en cours d'écriture ou invalide : on garde l'ancien
            log.warning("keymap.json illisible, ancienne table conservée : %s", exc)
            return
        for watch in self._watch.values():
            watch.alerted = False  # laisse l'alerte se lever au prochain relevé décodé
        log.info("keymap.json rechargé : %d messages connus.", len(self.keymap))

    def _log_error_once(self, exc: Exception) -> None:
        signature = f"{type(exc).__name__}: {exc}"
        if signature not in self._errors:
            self._errors.add(signature)
            log.error("Erreur ignorée (signalée une seule fois) : %s", signature)
