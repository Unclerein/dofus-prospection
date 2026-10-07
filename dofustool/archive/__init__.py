"""Archive brute de tous les messages du flux de jeu (data/archive.sqlite).

Donnée privée : contient le chat et le handshake de session. Reste en local, jamais commitée.

Elle sert à retrouver les messages après une mise à jour du jeu et à rejouer ce qu'une nouvelle
version sait décoder. Au-delà de KEEP_DAYS jours, seuls les messages que l'outil décode sont
gardés : le reste (déplacements, combats, chat) ne sert plus et pèse environ 30 Mo par jour de jeu.
"""
import sqlite3
from pathlib import Path

from ..protocol.session import Message

ARCHIVE_PATH = Path(__file__).resolve().parents[2] / "data" / "archive.sqlite"
KEEP_DAYS = 14

_SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    id          INTEGER PRIMARY KEY,
    started_at  REAL NOT NULL,
    client_port INTEGER NOT NULL,
    server      TEXT NOT NULL,
    source      TEXT NOT NULL,
    UNIQUE (started_at, client_port)
);
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY,
    connection_id INTEGER NOT NULL REFERENCES connections(id),
    ts            REAL NOT NULL,
    direction     TEXT NOT NULL,
    key           TEXT NOT NULL,
    size          INTEGER NOT NULL,
    frame_field   INTEGER NOT NULL,
    correlation   INTEGER,
    body          BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS labels (
    ts   REAL NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_key ON messages (key, ts);
CREATE INDEX IF NOT EXISTS messages_conn ON messages (connection_id, id);
"""


class Archive:
    def __init__(self, path: Path | str = ARCHIVE_PATH) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(_SCHEMA)

    def open_connection(self, started_at: float, client_port: int, server: str, source: str) -> int | None:
        """Enregistre une connexion de jeu. Renvoie None si elle est déjà archivée (réimport)."""
        cur = self._db.execute(
            "INSERT OR IGNORE INTO connections (started_at, client_port, server, source) VALUES (?, ?, ?, ?)",
            (started_at, client_port, server, source),
        )
        return cur.lastrowid if cur.rowcount else None

    def add(self, connection_id: int, msg: Message) -> int:
        """Archive le message et renvoie son numéro."""
        cur = self._db.execute(
            "INSERT INTO messages (connection_id, ts, direction, key, size, frame_field, correlation, body) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (connection_id, msg.ts, msg.direction, msg.key, msg.size, msg.frame_field, msg.correlation, msg.body),
        )
        return cur.lastrowid

    def add_label(self, ts: float, text: str) -> None:
        """Étiquette saisie par l'utilisateur au moment d'une action en jeu (identify.py)."""
        self._db.execute("INSERT INTO labels (ts, text) VALUES (?, ?)", (ts, text))

    def latest_body(self, key: str) -> bytes | None:
        row = self._db.execute("SELECT body FROM messages WHERE key = ? ORDER BY ts DESC LIMIT 1", (key,)).fetchone()
        return row[0] if row else None

    def prune(self, now: float, keep_keys: set[str], keep_days: float = KEEP_DAYS) -> int:
        """Supprime les messages non décodés des connexions ouvertes il y a plus de keep_days jours. Renvoie leur nombre.

        keep_keys : clés des messages que l'outil sait décoder, conservés sans limite de durée.
        L'espace libéré est réutilisé par les messages suivants : le fichier cesse de grossir.
        """
        marks = ",".join("?" * len(keep_keys))
        cur = self._db.execute(
            f"DELETE FROM messages WHERE key NOT IN ({marks}) "
            "AND connection_id IN (SELECT id FROM connections WHERE started_at < ?)",
            (*sorted(keep_keys), now - keep_days * 86400),
        )
        self._db.commit()
        return cur.rowcount

    @property
    def db(self) -> sqlite3.Connection:
        """Connexion SQLite, pour relire ce qui vient d'être archivé (y compris avant validation)."""
        return self._db

    def commit(self) -> None:
        self._db.commit()

    def close(self) -> None:
        self._db.commit()
        self._db.close()

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
