"""Traitement d'une capture live : archivage, décodage des messages connus, écriture en base.

Ne plante jamais sur une donnée inattendue et n'écrit rien de douteux : un message qui
n'a pas exactement la forme attendue est ignoré (il reste dans l'archive brute).
"""
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .. import db, identify
from ..archive import Archive
from ..messages import Mapping, avg_prices, characters, hdv_listings, load_keymap, market_history, sales, storage
from ..protocol.session import Message, Session
from ..protocol.tcp import C2S, S2C
from . import Segment

log = logging.getLogger("dofustool.capture")

# Sans trafic depuis ce délai, la connexion n'est plus considérée comme active.
ACTIVE_WINDOW_S = 30.0
# Tant qu'un message reste à retrouver (après une mise à jour du jeu), on le cherche à cet intervalle.
IDENTIFY_EVERY_S = 20.0


@dataclass(slots=True)
class _ConnWatch:
    first_ts: float
    last_ts: float
    archive_id: int | None
    got_prices: bool = False
    alerted: bool = False
    inventory: storage.Storage | None = None  # dernier inventaire de cette connexion
    character_id: int | None = None  # personnage choisi sur cette connexion
    scan: identify.Scan | None = None  # ce que la connexion a échangé, pour retrouver les messages


class Pipeline:
    def __init__(
        self,
        archive: Archive,
        market: sqlite3.Connection,
        keymap: dict[str, Mapping],
        avg_prices_timeout_s: float = 60.0,
        keymap_path: Path | None = None,
    ) -> None:
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
            db.set_status(self.market, last_connection_ts=conn.first_ts)
        watch.last_ts = msg.ts
        if watch.archive_id is not None:
            self.archive.add(watch.archive_id, msg)
        self._decode(msg, watch)

    def _decode(self, msg: Message, watch: _ConnWatch) -> None:
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

        mapping = self.keymap.get("my_sales")
        if mapping is not None and msg.key == mapping.key and msg.direction == S2C:
            listing = sales.parse(msg.body, mapping)
            if listing is not None and db.save_sales(self.market, listing, msg.ts):
                log.info("Lots en vente enregistrés : %d.", len(listing.sales))
            return

        mapping = self.keymap.get("bank")
        if mapping is not None and msg.key == mapping.key:
            bank = storage.parse(msg.body, mapping)
            if bank is not None and db.save_holdings(self.market, db.BANK, bank, msg.ts):
                log.info("Banque enregistrée : %d piles.", len(bank.stacks))
            return

        mapping = self.keymap.get("inventory")
        if mapping is not None and msg.key == mapping.key:
            listing = storage.parse(msg.body, mapping)
            if listing is not None:
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
            db.set_status(self.market, last_avg_prices_ts=msg.ts, decode_alert="")
            if watch.alerted:
                watch.alerted = False
                log.info("Alerte levée : les prix moyens ont fini par être décodés.")

    def tick(self, now: float) -> None:
        """À appeler régulièrement : valide l'archive et surveille le décodage."""
        try:
            self._reload_keymap()
            self.archive.commit()
            for watch in self._watch.values():
                active = now - watch.last_ts <= ACTIVE_WINDOW_S
                if active and not watch.got_prices and not watch.alerted and now - watch.first_ts > self.timeout:
                    # Avant d'alerter : une mise à jour du jeu a peut-être seulement changé les clés.
                    self._identify(watch, new_build=True)
                    if watch.got_prices:
                        continue
                    watch.alerted = True
                    text = (
                        f"Flux de jeu actif depuis {now - watch.first_ts:.0f} s sans prix moyens décodés. "
                        "Si tu as bien choisi un personnage, une mise à jour du jeu a sans doute changé les clés : "
                        "voir MAINTENANCE.md."
                    )
                    log.warning("ALERTE : %s", text)
                    db.set_status(self.market, decode_alert=text, decode_alert_ts=now)
            if now >= self._next_identify:
                self._next_identify = now + IDENTIFY_EVERY_S
                for watch in self._watch.values():
                    if now - watch.last_ts <= ACTIVE_WINDOW_S:
                        self._identify(watch, new_build=False)
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
            f"SELECT ts, direction, key, frame_field, correlation, body FROM messages "
            f"WHERE connection_id = ? AND key IN ({marks}) ORDER BY id",
            (watch.archive_id, *sorted(keys)),
        ).fetchall()
        for ts, direction, key, frame_field, correlation, body in rows:
            self._decode(Message(0, ts, direction, key, body, frame_field, correlation), watch)

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
