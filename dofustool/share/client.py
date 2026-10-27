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
from . import FM_SHARE_VERSION, apply, collect, collect_fm, collect_notes, fm_key, forget_fm_sent, get_state, mark_fm_sent, set_state, validate

log = logging.getLogger("dofustool.share")

SYNC_EVERY_S = 60.0
CHUNK = 300
# Monte quand le client apprend à lire de nouveaux enregistrements (2 : passages et scores de forgemagie ;
# 3 : passages avec version du jeu et rang, qu'une version d'avant refusait).
PULL_VERSION = 3
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
        set_state(conn, hub=identity, cursor=0, push_mark=0, own_restored=0)
        forget_fm_sent(conn)
    if state.get("pull_version") != str(PULL_VERSION):
        # Version qui sait lire des enregistrements de plus : ceux que l'ancienne a reçus sans les comprendre
        # (et donc ignorés) sont toujours sur le hub ; on relit tout depuis le début, une fois.
        state["cursor"] = "0"
        set_state(conn, cursor=0, pull_version=PULL_VERSION)
    if state.get("fm_share_version") != str(FM_SHARE_VERSION):
        # Les passages déjà partagés repartent une fois, avec ce qu'ils n'avaient pas (version, rang) ;
        # le hub remplace l'ancien exemplaire de chacun au lieu de le doubler.
        forget_fm_sent(conn)
        set_state(conn, fm_share_version=FM_SHARE_VERSION)
    # Une fois par PC (et après une réinstallation, qui repart de zéro) : tout est relu depuis le début, avec sa
    # propre forgemagie, que le hub ne rend sinon jamais à son auteur ; ce PC en reprend ce qu'il n'a plus.
    restore = state.get("own_restored") != "1"
    if restore:
        state["cursor"] = "0"
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
    written = collect_notes(float(state.get("notes_mark", 0)))
    if written:
        chunks.append((written, [("notes", started)]))
    score = score_record(conn, state, cfg)
    if score is not None:
        chunks.append(([score[0]], [("score", score[1])]))
    chunks = chunks or [([], [])]
    sent = received = 0
    members: list = []
    own: list | None = None  # ma forgemagie rendue par le hub ; None : hub d'avant cette reprise
    more = True
    while chunks or more:
        chunk, marks = chunks.pop(0) if chunks else ([], [])
        request = {"server": cfg.server_name, "cursor": cursor, "records": chunk}
        if restore:
            request["mine"] = True
        reply = post(cfg.share_hub_url, cfg.share_token, request)
        if restore and isinstance(reply.get("mine"), list):
            own = (own or []) + reply["mine"]
        sent += int(reply.get("accepted", 0))
        if marks and marks[0][0] == "score":
            if int(reply.get("rejected", 0)) == 0:
                set_state(conn, fm_score_sig=marks[0][1])
        elif marks and marks[0][0] == "notes":
            if int(reply.get("rejected", 0)) == 0:  # hub d'avant les notes : elles repartiront après sa mise à jour
                set_state(conn, notes_mark=marks[0][1])
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
    if own is not None:
        received += restore_own(conn, own)
        set_state(conn, own_restored=1)
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


def restore_own(conn: sqlite3.Connection, records: list) -> int:
    """Reprend, de ma forgemagie gardée par le hub, ce que ce PC n'a plus (réinstallé) ; renvoie le nombre repris.

    Les passages repris rejoignent ceux des autres : ils comptent pour la collecte et les formules. Mon score
    d'avant, lui, est gardé à part et s'ajoute à celui des passages de ce PC, pour ne pas l'écraser au prochain envoi."""
    local = {fm_key("fm", uid, ts, rune) for uid, ts, rune in conn.execute("SELECT uid, ts, rune_id FROM fm_passes")}
    local |= {fm_key("refus", uid, ts, rune) for uid, ts, rune in conn.execute("SELECT uid, ts, rune_id FROM fm_refusals")}
    restored, score = 0, None
    for record in records:
        if not isinstance(record, dict) or not validate(record):
            continue
        if record["kind"] == "fm_score":
            score = record if score is None or record["at"] > score["at"] else score
        elif record["key"] not in local and apply(conn, record):
            restored += 1
    first = conn.execute("SELECT MIN(ts) FROM fm_passes").fetchone()[0]
    # Score envoyé avant le premier passage de ce PC : il ne compte que les passages perdus, on peut l'ajouter.
    if restored and score is not None and (first is None or score["at"] <= first):
        data = score["data"]
        carry = {"points": data["points"], "passes": data["passes"], "kamas": data["kamas"], "missions": data["missions"]}
        set_state(conn, fm_score_carry=json.dumps(carry))
    conn.commit()
    return restored


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
