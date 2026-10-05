"""Base de travail data/market.sqlite : schéma et accès."""
import hashlib
import sqlite3
from collections.abc import Iterable
from pathlib import Path

MARKET_PATH = Path(__file__).resolve().parents[2] / "data" / "market.sqlite"

# Valeurs de market_history.period : le grain de la série envoyée par le serveur.
GRAIN_HOUR = "hour"
GRAIN_DAY = "day"

STATIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    type_id      INTEGER NOT NULL,
    type_name    TEXT,
    level        INTEGER NOT NULL,
    exchangeable INTEGER NOT NULL,
    is_weapon    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipes (
    result_id INTEGER PRIMARY KEY,
    job_id    INTEGER NOT NULL,
    level     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS recipe_ingredients (
    result_id INTEGER NOT NULL,
    item_id   INTEGER NOT NULL,
    quantity  INTEGER NOT NULL,
    PRIMARY KEY (result_id, item_id)
);
CREATE INDEX IF NOT EXISTS recipe_ingredients_item ON recipe_ingredients (item_id);
CREATE TABLE IF NOT EXISTS static_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

MARKET_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY,
    ts           REAL NOT NULL,
    content_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS avg_prices (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    item_id     INTEGER NOT NULL,
    price       INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, item_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS avg_prices_item ON avg_prices (item_id, snapshot_id);
CREATE TABLE IF NOT EXISTS last_sales (
    item_id     INTEGER NOT NULL,
    price       INTEGER NOT NULL,
    sold_at     REAL NOT NULL,
    captured_at REAL NOT NULL,
    PRIMARY KEY (item_id, sold_at)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS market_history (
    item_id     INTEGER NOT NULL,
    period      TEXT NOT NULL,
    bucket_ts   INTEGER NOT NULL,
    price       INTEGER NOT NULL,
    qty_sold    INTEGER,
    captured_at REAL NOT NULL,
    PRIMARY KEY (item_id, period, bucket_ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS capture_status (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(path: Path | str = MARKET_PATH) -> sqlite3.Connection:
    """Ouvre la base et crée les tables manquantes."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    # Avant la phase 2b, last_sales n'avait pas la date de vente ; la table était encore vide.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(last_sales)")}
    if columns and "sold_at" not in columns:
        conn.execute("DROP TABLE last_sales")
    conn.executescript(STATIC_SCHEMA + MARKET_SCHEMA)
    return conn


def content_hash(prices: dict[int, int]) -> str:
    digest = hashlib.sha256()
    for item_id in sorted(prices):
        digest.update(f"{item_id}:{prices[item_id]}\n".encode())
    return digest.hexdigest()


def save_snapshot(conn: sqlite3.Connection, ts: float, prices: dict[int, int]) -> int | None:
    """Enregistre un relevé de prix moyens. Renvoie None s'il est vide ou déjà connu.

    Le dédoublonnage porte sur le contenu : le serveur renvoie la même liste à chaque
    connexion tant qu'il ne l'a pas recalculée.
    """
    if not prices:
        return None
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO snapshots (ts, content_hash) VALUES (?, ?)", (ts, content_hash(prices))
        )
        if not cur.rowcount:
            return None
        snapshot_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO avg_prices (snapshot_id, item_id, price) VALUES (?, ?, ?)",
            ((snapshot_id, item_id, price) for item_id, price in prices.items()),
        )
    return snapshot_id


def save_last_sale(conn: sqlite3.Connection, item_id: int, price: int, sold_at: float, captured_at: float) -> None:
    """Enregistre une vente observée. sold_at est la date de la vente, captured_at celle de l'observation."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO last_sales (item_id, price, sold_at, captured_at) VALUES (?, ?, ?, ?)",
            (item_id, price, sold_at, captured_at),
        )


def save_market_history(
    conn: sqlite3.Connection,
    item_id: int,
    period: str,
    points: Iterable[tuple[int, int, int | None]],
    captured_at: float,
) -> None:
    """Enregistre des points (bucket_ts, prix, quantité vendue) d'une série (period = grain, heure ou jour).

    Pour une même tranche, le relevé le plus récent gagne : la tranche en cours grossit d'une capture à l'autre.
    """
    with conn:
        conn.executemany(
            "INSERT INTO market_history (item_id, period, bucket_ts, price, qty_sold, captured_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (item_id, period, bucket_ts) DO UPDATE SET "
            "price = excluded.price, qty_sold = excluded.qty_sold, captured_at = excluded.captured_at "
            "WHERE excluded.captured_at >= market_history.captured_at",
            ((item_id, period, bucket_ts, price, qty, captured_at) for bucket_ts, price, qty in points),
        )


def set_status(conn: sqlite3.Connection, **values: object) -> None:
    """État de la capture, lu par le dashboard (dernier démarrage, alerte de décodage…)."""
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO capture_status (key, value) VALUES (?, ?)",
            ((key, str(value)) for key, value in values.items()),
        )


def get_status(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT key, value FROM capture_status"))


def latest_snapshot(conn: sqlite3.Connection) -> tuple[int, float] | None:
    """Renvoie (id, ts) du relevé le plus récent."""
    return conn.execute("SELECT id, ts FROM snapshots ORDER BY ts DESC, id DESC LIMIT 1").fetchone()


def snapshot_prices(conn: sqlite3.Connection, snapshot_id: int) -> dict[int, int]:
    return dict(conn.execute("SELECT item_id, price FROM avg_prices WHERE snapshot_id = ?", (snapshot_id,)))


def latest_last_sale(conn: sqlite3.Connection, item_id: int) -> tuple[int, float] | None:
    """Renvoie (prix, date de la vente) de la dernière vente connue de l'item."""
    return conn.execute(
        "SELECT price, sold_at FROM last_sales WHERE item_id = ? ORDER BY sold_at DESC LIMIT 1", (item_id,)
    ).fetchone()


def save_market(conn: sqlite3.Connection, history, captured_at: float) -> None:
    """Enregistre un cours du marché décodé (messages.market_history.MarketHistory)."""
    for grain, points, size in ((GRAIN_HOUR, history.hourly, 3600), (GRAIN_DAY, history.daily, 86400)):
        save_market_history(
            conn, history.item_id, grain, [(p.bucket(size), p.price, p.quantity) for p in points], captured_at
        )
    last = history.last_sale
    if last is not None:
        save_last_sale(conn, history.item_id, last.price, last.sold_at, captured_at)
