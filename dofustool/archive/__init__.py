"""Archive brute de tous les messages du flux de jeu (data/archive.sqlite).

Donnée privée : contient le chat et le handshake de session. Reste en local, jamais commitée.

Elle sert à retrouver les messages après une mise à jour du jeu et à rejouer ce qu'une nouvelle
version sait décoder. Au-delà de KEEP_DAYS jours, seuls les messages que l'outil décode sont
gardés : le reste (déplacements, combats, chat) ne sert plus et pèse environ 30 Mo par jour de jeu.
Au-delà de KEEP_ALL_DAYS jours, plus rien n'est gardé : ce qui compte est déjà extrait dans la base.

Les messages sont rangés compressés (zlib) dès qu'ils y gagnent, précédés de PACKED : aucun message du
jeu ne commence par un octet nul (il n'y a pas de champ 0). Toute connexion ouverte par ce module
(Archive, connect) les rend tels qu'ils ont été reçus.
"""
import sqlite3
import zlib
from pathlib import Path

from ..protocol.session import Message

ARCHIVE_PATH = Path(__file__).resolve().parents[2] / "data" / "archive.sqlite"
KEEP_DAYS = 14
KEEP_ALL_DAYS = 30
PACKED = b"\x00z"  # début d'un message compressé
PACK_FROM = 64  # en dessous, la compression ne gagne rien


def pack(body: bytes) -> bytes:
    """Le message compressé, s'il y gagne ; sinon tel quel."""
    if len(body) < PACK_FROM:
        return body
    packed = PACKED + zlib.compress(body, 6)
    return packed if len(packed) < len(body) else body


def unpack(body):
    """Le message tel qu'il a été reçu."""
    if isinstance(body, (bytes, memoryview)) and bytes(body[:2]) == PACKED:
        return zlib.decompress(bytes(body)[2:])
    return body


def _rows(cursor: sqlite3.Cursor, row: tuple) -> tuple:
    return tuple(unpack(value) if isinstance(value, bytes) and value[:2] == PACKED else value for value in row)


def connect(path: Path | str = ARCHIVE_PATH) -> sqlite3.Connection:
    """L'archive ouverte en lecture seule, messages décompressés à la volée."""
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = _rows
    return conn

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
        self._db.row_factory = _rows

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
            (connection_id, msg.ts, msg.direction, msg.key, msg.size, msg.frame_field, msg.correlation, pack(msg.body)),
        )
        return cur.lastrowid

    def add_label(self, ts: float, text: str) -> None:
        """Étiquette saisie par l'utilisateur au moment d'une action en jeu (identify.py)."""
        self._db.execute("INSERT INTO labels (ts, text) VALUES (?, ?)", (ts, text))

    def latest_body(self, key: str) -> bytes | None:
        row = self._db.execute("SELECT body FROM messages WHERE key = ? ORDER BY ts DESC LIMIT 1", (key,)).fetchone()
        return row[0] if row else None

    def prune(self, now: float, keep_keys: set[str], keep_days: float = KEEP_DAYS, keep_all_days: float = KEEP_ALL_DAYS) -> int:
        """Supprime les messages non décodés des connexions ouvertes il y a plus de keep_days jours, et tous ceux de plus
        de keep_all_days jours. Renvoie leur nombre.

        keep_keys : clés des messages que l'outil sait décoder, gardés jusqu'à keep_all_days jours.
        L'espace libéré est réutilisé par les messages suivants : le fichier cesse de grossir (tools.slim le rend au disque).
        """
        marks = ",".join("?" * len(keep_keys))
        cur = self._db.execute(
            f"DELETE FROM messages WHERE key NOT IN ({marks}) "
            "AND connection_id IN (SELECT id FROM connections WHERE started_at < ?)",
            (*sorted(keep_keys), now - keep_days * 86400),
        )
        removed = cur.rowcount
        removed += self._db.execute("DELETE FROM messages WHERE ts < ?", (now - keep_all_days * 86400,)).rowcount
        self._db.execute("DELETE FROM connections WHERE id NOT IN (SELECT DISTINCT connection_id FROM messages) AND started_at < ?",
                         (now - keep_all_days * 86400,))  # fmt: skip
        self._db.commit()
        return removed

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
