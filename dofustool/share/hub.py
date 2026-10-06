"""Hub de partage : reçoit les relevés de chaque joueur et les redistribue aux autres.

Un seul point d'entrée, POST /sync, protégé par un jeton par joueur. Le hub ne garde que le dernier
enregistrement de chaque objet (data/hub.sqlite) et ne connaît rien de personnel.

Usage : lancé par « python -m dofustool.web » quand host_hub est activé dans la configuration.
"""
import gzip
import hmac
import json
import logging
import sqlite3
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import config, db
from . import validate

log = logging.getLogger("dofustool.share")

HUB_PATH = db.MARKET_PATH.parent / "hub.sqlite"
MAX_BODY = 16 * 1024 * 1024
MAX_RECORDS = 2000
PULL_BYTES = 3 * 1024 * 1024
# Au-delà, un enregistrement n'est plus distribué : chaque joueur garde son propre historique.
KEEP_S = {"hdv": 3 * 86400.0, "prices": 3 * 86400.0, "market": 60 * 86400.0, "keymap": 60 * 86400.0}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    kind    TEXT NOT NULL,
    key     TEXT NOT NULL,
    at      REAL NOT NULL,
    author  TEXT NOT NULL,
    payload BLOB NOT NULL,
    UNIQUE (kind, key)
);
CREATE TABLE IF NOT EXISTS members (
    pseudo    TEXT PRIMARY KEY,
    last_seen REAL NOT NULL,
    pushed    INTEGER NOT NULL DEFAULT 0
);
"""


class Hub:
    def __init__(self, path: Path | str = HUB_PATH) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._db.close()

    def sync(self, pseudo: str, cursor: int, records: list, now: float | None = None) -> dict:
        """Enregistre ce qu'un joueur envoie, puis lui rend ce que les autres ont envoyé depuis `cursor`."""
        now = time.time() if now is None else now
        accepted = rejected = 0
        with self._lock, self._db:
            for record in records[:MAX_RECORDS]:
                if not validate(record, now):
                    rejected += 1
                    continue
                if self._store(pseudo, record):
                    accepted += 1
            self._db.execute(
                "INSERT INTO members VALUES (?, ?, ?) ON CONFLICT (pseudo) DO UPDATE SET "
                "last_seen = excluded.last_seen, pushed = pushed + excluded.pushed",
                (pseudo, now, accepted),
            )
            for kind, keep in KEEP_S.items():
                self._db.execute("DELETE FROM records WHERE kind = ? AND at < ?", (kind, now - keep))

            out, size, last = [], 0, cursor
            more = False
            for seq, author, payload in self._db.execute(
                "SELECT seq, author, payload FROM records WHERE seq > ? ORDER BY seq", (cursor,)
            ):
                if size > PULL_BYTES:
                    more = True
                    break
                last = seq
                if author != pseudo:
                    out.append(json.loads(payload))
                    size += len(payload)
            members = [
                {"pseudo": name, "last_seen": seen, "pushed": pushed}
                for name, seen, pushed in self._db.execute("SELECT pseudo, last_seen, pushed FROM members ORDER BY pseudo")
            ]
        return {"cursor": last, "records": out, "more": more, "accepted": accepted, "rejected": rejected, "members": members}

    def _store(self, pseudo: str, record: dict) -> bool:
        """Garde l'enregistrement s'il est plus récent que celui déjà connu pour cet objet."""
        kind, key = record["kind"], record["key"]
        row = self._db.execute("SELECT at, payload FROM records WHERE kind = ? AND key = ?", (kind, key)).fetchone()
        if kind == "keymap" and row is not None:
            # Table des clés d'un même build : on réunit ce que chacun a retrouvé, le complet l'emporte sur le partiel.
            known = json.loads(row[1])["data"]["entries"]
            merged = dict(known)
            for name, entry in record["data"]["entries"].items():
                if name not in merged or (merged[name].get("partial") and not entry.get("partial")):
                    merged[name] = entry
            if merged == known:
                return False
            record = {**record, "at": max(row[0], record["at"]), "data": {"entries": merged}}
        elif row is not None and row[0] >= record["at"]:
            return False
        self._db.execute("DELETE FROM records WHERE kind = ? AND key = ?", (kind, key))
        self._db.execute(
            "INSERT INTO records (kind, key, at, author, payload) VALUES (?, ?, ?, ?, ?)",
            (kind, key, record["at"], pseudo, json.dumps(record, separators=(",", ":"))),
        )
        return True

    def purge(self, pseudo: str) -> int:
        """Retire du hub tout ce qu'un joueur a envoyé (ce que les autres ont déjà reçu reste chez eux)."""
        with self._lock, self._db:
            count = self._db.execute("DELETE FROM records WHERE author = ?", (pseudo,)).rowcount
            self._db.execute("DELETE FROM members WHERE pseudo = ?", (pseudo,))
        return count


def member_of(token: str, members: dict[str, str]) -> str | None:
    """Pseudo du joueur à qui appartient ce jeton, comparé en temps constant."""
    found = None
    for pseudo, expected in members.items():
        if expected and hmac.compare_digest(token.encode(), expected.encode()):
            found = pseudo
    return found


def make_handler(hub: Hub, config_path: Path = config.CONFIG_PATH) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "prospection-hub"

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass

        def _reply(self, status: int, payload: dict) -> None:
            body = gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._reply(HTTPStatus.NOT_FOUND, {"error": "introuvable"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if self.path != "/sync" or not 0 < length <= MAX_BODY:
                    return self._reply(HTTPStatus.BAD_REQUEST, {"error": "requête invalide"})
                raw = self.rfile.read(length)
                cfg = config.load(config_path)  # relu à chaque requête : un jeton retiré cesse aussitôt de marcher
                token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
                pseudo = member_of(token, cfg.share_members)
                if pseudo is None:
                    return self._reply(HTTPStatus.UNAUTHORIZED, {"error": "jeton inconnu"})
                if self.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                request = json.loads(raw)
                server = str(request.get("server") or "")
                if cfg.server_name and server and server.casefold() != cfg.server_name.casefold():
                    return self._reply(HTTPStatus.CONFLICT, {"error": f"ce hub partage le serveur {cfg.server_name}, pas {server}"})
                cursor, records = request.get("cursor", 0), request.get("records", [])
                if not isinstance(cursor, int) or not isinstance(records, list):
                    return self._reply(HTTPStatus.BAD_REQUEST, {"error": "requête invalide"})
                reply = hub.sync(pseudo, cursor, records)
                if reply["accepted"] or reply["records"]:
                    log.info("%s : %d relevés reçus, %d envoyés.", pseudo, reply["accepted"], len(reply["records"]))
                self._reply(HTTPStatus.OK, reply)
            except (ValueError, OSError, EOFError) as exc:
                self._reply(HTTPStatus.BAD_REQUEST, {"error": f"requête invalide : {type(exc).__name__}"})
            except Exception:
                log.exception("Erreur du hub")
                self._reply(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "erreur interne"})

    return Handler


def serve(
    port: int, hub: Hub | None = None, config_path: Path = config.CONFIG_PATH, bind: str = "0.0.0.0"
) -> ThreadingHTTPServer:
    """Sur un PC, le hub écoute sur toutes les interfaces : c'est le seul service de l'outil joignable
    depuis un autre PC. Sur un serveur, on le lie à 127.0.0.1 et un frontal HTTPS (Caddy) le protège."""
    return ThreadingHTTPServer((bind, port), make_handler(hub or Hub(), config_path))


def _edit_members(change) -> config.Config:
    """Applique `change` à la liste des joueurs autorisés et réécrit la configuration."""
    import dataclasses

    cfg = config.load()
    members = dict(cfg.share_members)
    change(members)
    cfg = dataclasses.replace(cfg, share_members=members)
    config.save(cfg)
    return cfg


def main() -> int:
    import argparse
    import dataclasses
    import secrets

    parser = argparse.ArgumentParser(description="Hub de partage de Prospection.")
    parser.add_argument("--bind", default="0.0.0.0", help="adresse d'écoute (127.0.0.1 derrière un frontal HTTPS)")
    parser.add_argument("--port", type=int, help="port d'écoute (défaut : celui de la configuration, 8610)")
    parser.add_argument("--add", metavar="PSEUDO", help="autoriser un joueur : crée et affiche son jeton")
    parser.add_argument("--remove", metavar="PSEUDO", help="retirer un joueur : son jeton cesse de fonctionner")
    parser.add_argument("--list", action="store_true", help="lister les joueurs autorisés et leur dernier passage")
    parser.add_argument("--server", metavar="NOM", help="nom du serveur de jeu partagé (les autres sont refusés)")
    parser.add_argument("--purge", metavar="PSEUDO", help="retirer du hub tout ce que ce joueur a envoyé")
    args = parser.parse_args()

    if args.server is not None:
        config.save(dataclasses.replace(config.load(), server_name=args.server.strip()))
        print(f"Serveur de jeu partagé : {args.server.strip() or '(tous)'}")
        return 0
    if args.add:
        pseudo = args.add.strip()
        if not pseudo or len(pseudo) > 30 or not pseudo.isprintable() or '"' in pseudo:
            print("Pseudo invalide (30 caractères au plus, sans guillemet).")
            return 1
        if pseudo in config.load().share_members:
            print(f"{pseudo} est déjà autorisé. Pour lui donner un nouveau jeton : --remove puis --add.")
            return 1
        token = secrets.token_urlsafe(18)
        _edit_members(lambda members: members.update({pseudo: token}))
        print(f"Joueur ajouté : {pseudo}")
        print(f"Son jeton (à lui envoyer en privé, il ne sera plus affiché) : {token}")
        return 0
    if args.remove:
        if args.remove not in config.load().share_members:
            print(f"{args.remove} n'est pas dans la liste.")
            return 1
        _edit_members(lambda members: members.pop(args.remove))
        print(f"{args.remove} est retiré : son jeton ne fonctionne plus.")
        return 0
    if args.list:
        cfg = config.load()
        seen = {name: (when, pushed) for name, when, pushed in Hub()._db.execute("SELECT pseudo, last_seen, pushed FROM members")}
        print(f"Serveur de jeu : {cfg.server_name or '(tous)'} — {len(cfg.share_members)} joueur(s) autorisé(s)")
        for pseudo in sorted(cfg.share_members):
            if pseudo in seen:
                when = time.strftime("%d/%m %H:%M", time.localtime(seen[pseudo][0]))
                print(f"  {pseudo:20} vu le {when}, {seen[pseudo][1]} relevés envoyés")
            else:
                print(f"  {pseudo:20} jamais vu")
        return 0
    if args.purge:
        print(f"{Hub().purge(args.purge)} enregistrement(s) retiré(s) du hub.")
        return 0

    cfg = config.load()
    port = args.port or cfg.share_port
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    server = serve(port, bind=args.bind)
    log.info("Hub de partage en écoute sur %s:%d, %d joueur(s) autorisé(s).", args.bind, port, len(cfg.share_members))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
