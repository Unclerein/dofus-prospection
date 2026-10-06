"""Synchronisation de ce PC avec le hub de partage : envoie ses relevés, récupère ceux des autres."""
import gzip
import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import config, db
from . import apply, collect, get_state, set_state

log = logging.getLogger("dofustool.share")

SYNC_EVERY_S = 60.0
CHUNK = 300
TIMEOUT_S = 30


def _post(url: str, token: str, payload: dict) -> dict:
    body = gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    request = urllib.request.Request(
        url.rstrip("/") + "/sync",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            message = json.loads(gzip.decompress(raw))["error"]
        except Exception:
            message = f"erreur {error.code}"
        raise ConnectionError(message) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ConnectionError(f"hub injoignable ({getattr(error, 'reason', error)})") from None
    return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)


def enabled(cfg: config.Config) -> bool:
    return bool(cfg.share_hub_url and cfg.share_token)


def sync_once(conn: sqlite3.Connection, cfg: config.Config, keymap_path: Path | None = None, post=_post) -> dict:
    """Un aller-retour complet avec le hub. Renvoie {sent, received} ; lève ConnectionError si le hub refuse ou ne répond pas."""
    state = get_state(conn)
    if state.get("hub") != cfg.share_hub_url:  # autre hub : on repart de zéro
        state = {}
        set_state(conn, hub=cfg.share_hub_url, cursor=0, push_mark=0)
    cursor = int(state.get("cursor", 0))
    started = time.time()
    records = collect(conn, float(state.get("push_mark", 0)), keymap_path)
    sent = received = 0
    members: list = []
    chunks = [records[i : i + CHUNK] for i in range(0, len(records), CHUNK)] or [[]]
    more = True
    while chunks or more:
        chunk = chunks.pop(0) if chunks else []
        reply = post(cfg.share_hub_url, cfg.share_token, {"server": cfg.server_name, "cursor": cursor, "records": chunk})
        sent += int(reply.get("accepted", 0))
        for record in reply.get("records", []):
            received += bool(apply(conn, record, keymap_path))
        cursor = int(reply.get("cursor", cursor))
        members = reply.get("members", members)
        more = bool(reply.get("more"))
        set_state(conn, cursor=cursor)
    set_state(
        conn,
        push_mark=started,
        last_ok=time.time(),
        last_error="",
        members=json.dumps(members),
        sent=int(state.get("sent", 0)) + sent,
        received=int(state.get("received", 0)) + received,
    )
    return {"sent": sent, "received": received}


def status(conn: sqlite3.Connection, cfg: config.Config) -> dict:
    state = get_state(conn)
    return {
        "enabled": enabled(cfg),
        "hosting": cfg.share_host,
        "pseudo": cfg.share_pseudo,
        "hub_url": cfg.share_hub_url,
        "last_ok": float(state["last_ok"]) if state.get("last_ok") else None,
        "last_error": state.get("last_error") or None,
        "sent": int(state.get("sent", 0)),
        "received": int(state.get("received", 0)),
        "members": json.loads(state["members"]) if state.get("members") else [],
    }


class Syncer(threading.Thread):
    """Synchronise en arrière-plan tant que l'interface tourne. Ne fait jamais tomber le processus."""

    def __init__(self, db_path=db.MARKET_PATH, config_path: Path = config.CONFIG_PATH, keymap_path: Path | None = None) -> None:
        super().__init__(daemon=True)
        self.db_path, self.config_path, self.keymap_path = db_path, config_path, keymap_path
        self.wake = threading.Event()

    def run(self) -> None:
        while True:
            try:
                cfg = config.load(self.config_path)
                if enabled(cfg):
                    conn = db.connect(self.db_path)
                    try:
                        result = sync_once(conn, cfg, self.keymap_path)
                        if result["sent"] or result["received"]:
                            log.info("Partage : %d envoyés, %d reçus.", result["sent"], result["received"])
                    except ConnectionError as error:
                        set_state(conn, last_error=str(error))
                    finally:
                        conn.close()
            except Exception:
                log.exception("Erreur de synchronisation")
            self.wake.wait(SYNC_EVERY_S)
            self.wake.clear()
