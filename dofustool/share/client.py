"""Synchronisation de ce PC avec le hub de partage : envoie ses relevés, récupère ceux des autres."""
import gzip
import hashlib
import json
import logging
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import config, db
from . import apply, collect, collect_fm, forget_fm_sent, get_state, mark_fm_sent, set_state, validate

log = logging.getLogger("dofustool.share")

SYNC_EVERY_S = 60.0
CHUNK = 300
# Monte quand le client apprend à lire un nouveau type d'enregistrement (2 : passages et scores de forgemagie).
PULL_VERSION = 2
TIMEOUT_S = 30


def _tls_context() -> ssl.SSLContext:
    """Vérifie le certificat du hub comme le fait Windows (et donc un navigateur).

    Le magasin de certificats que Python lit seul peut ne pas connaître une autorité récente :
    un certificat parfaitement valide serait alors refusé.
    """
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def _post(url: str, token: str, payload: dict) -> dict:
    body = gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    request = urllib.request.Request(
        url.rstrip("/") + "/sync",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    try:
        context = _tls_context() if url.startswith("https://") else None
        with urllib.request.urlopen(request, timeout=TIMEOUT_S, context=context) as response:
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
    # Autre hub, ou autre jeton (donc autre joueur aux yeux du hub) : on repart de zéro et tout est renvoyé.
    identity = cfg.share_hub_url + "#" + hashlib.sha256(cfg.share_token.encode()).hexdigest()[:12]
    if state.get("hub") != identity:
        state = {}
        set_state(conn, hub=identity, cursor=0, push_mark=0)
        forget_fm_sent(conn)
    if state.get("pull_version") != str(PULL_VERSION):
        # Version qui sait lire des enregistrements de plus : ceux que l'ancienne a reçus sans les comprendre
        # (et donc ignorés) sont toujours sur le hub ; on relit tout depuis le début, une fois.
        state["cursor"] = "0"
        set_state(conn, cursor=0, pull_version=PULL_VERSION)
    cursor = int(state.get("cursor", 0))
    started = time.time()
    records = collect(conn, float(state.get("push_mark", 0)), keymap_path)
    chunks: list[tuple[list, list]] = [(records[i : i + CHUNK], []) for i in range(0, len(records), CHUNK)]
    # Passages de forgemagie : à part, marqués envoyés seulement quand le hub les a pris.
    passes, broken = [], []
    for record, kind, source_id in collect_fm(conn):
        (passes if validate(record) else broken).append((record, (kind, source_id)))
    mark_fm_sent(conn, [mark for _, mark in broken])  # jamais valables : inutile de les reproposer
    for i in range(0, len(passes), CHUNK):
        part = passes[i : i + CHUNK]
        chunks.append(([record for record, _ in part], [mark for _, mark in part]))
    score = score_record(conn, state, cfg)
    if score is not None:
        chunks.append(([score[0]], [("score", score[1])]))
    chunks = chunks or [([], [])]
    sent = received = 0
    members: list = []
    more = True
    while chunks or more:
        chunk, marks = chunks.pop(0) if chunks else ([], [])
        reply = post(cfg.share_hub_url, cfg.share_token, {"server": cfg.server_name, "cursor": cursor, "records": chunk})
        sent += int(reply.get("accepted", 0))
        if marks and marks[0][0] == "score":
            if int(reply.get("rejected", 0)) == 0:
                set_state(conn, fm_score_sig=marks[0][1])
        elif marks:
            if int(reply.get("rejected", 0)) == 0:
                mark_fm_sent(conn, marks)
            else:  # hub d'avant le partage de la forgemagie : on réessaiera après sa mise à jour
                chunks = [(c, m) for c, m in chunks if not m]
        for record in reply.get("records", []):
            received += bool(apply(conn, record, keymap_path))
        conn.commit()
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


def score_record(conn: sqlite3.Connection, state: dict, cfg: config.Config) -> tuple[dict, str] | None:
    """Le score de collecte de ce PC, à envoyer s'il a changé ; les missions réussies sont inscrites au passage."""
    from ..analysis import collecte

    signature = collecte.signature(conn)
    if state.get("fm_score_sig") == signature or conn.execute("SELECT 1 FROM fm_passes LIMIT 1").fetchone() is None:
        return None
    try:
        result = collecte.cached(conn)
        collecte.remember(conn, result, focus_path=collecte.FOCUS_PATH)
        data = collecte.score(conn, result)
    except (sqlite3.Error, OSError, ValueError) as error:
        log.warning("Score de collecte non calculé : %s", error)
        return None
    record = {"kind": "fm_score", "key": cfg.share_pseudo or "moi", "at": time.time(), "data": data}
    return (record, signature) if validate(record) else None


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
