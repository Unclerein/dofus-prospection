"""Traitement d'une capture live : archivage, décodage des messages connus, écriture en base.

Ne plante jamais sur une donnée inattendue et n'écrit rien de douteux : un message qui
n'a pas exactement la forme attendue est ignoré (il reste dans l'archive brute).
"""
import logging
import sqlite3
from dataclasses import dataclass

from .. import db
from ..archive import Archive
from ..messages import Mapping, avg_prices, market_history
from ..protocol.session import Message, Session
from . import Segment

log = logging.getLogger("dofustool.capture")

# Sans trafic depuis ce délai, la connexion n'est plus considérée comme active.
ACTIVE_WINDOW_S = 30.0


@dataclass(slots=True)
class _ConnWatch:
    first_ts: float
    last_ts: float
    archive_id: int | None
    got_prices: bool = False
    alerted: bool = False


class Pipeline:
    def __init__(
        self,
        archive: Archive,
        market: sqlite3.Connection,
        keymap: dict[str, Mapping],
        avg_prices_timeout_s: float = 60.0,
    ) -> None:
        self.session = Session()
        self.archive = archive
        self.market = market
        self.keymap = keymap
        self.timeout = avg_prices_timeout_s
        self._watch: dict[int, _ConnWatch] = {}
        self._errors: set[str] = set()

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
            self.archive.commit()
            for watch in self._watch.values():
                active = now - watch.last_ts <= ACTIVE_WINDOW_S
                if active and not watch.got_prices and not watch.alerted and now - watch.first_ts > self.timeout:
                    watch.alerted = True
                    text = (
                        f"Flux de jeu actif depuis {now - watch.first_ts:.0f} s sans prix moyens décodés. "
                        "Si tu as bien choisi un personnage, une mise à jour du jeu a sans doute changé les clés : "
                        "voir MAINTENANCE.md."
                    )
                    log.warning("ALERTE : %s", text)
                    db.set_status(self.market, decode_alert=text, decode_alert_ts=now)
            db.set_status(self.market, heartbeat_ts=now)
        except Exception as exc:
            self._log_error_once(exc)

    def _log_error_once(self, exc: Exception) -> None:
        signature = f"{type(exc).__name__}: {exc}"
        if signature not in self._errors:
            self._errors.add(signature)
            log.error("Erreur ignorée (signalée une seule fois) : %s", signature)
