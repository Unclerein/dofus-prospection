"""Base de travail data/market.sqlite : schéma et accès."""
import hashlib
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

MARKET_PATH = Path(__file__).resolve().parents[2] / "data" / "market.sqlite"

# Valeurs de market_history.period : le grain de la série envoyée par le serveur.
GRAIN_HOUR = "hour"
GRAIN_DAY = "day"

# Valeurs de holdings.container.
INVENTORY = "inventory"
BANK = "bank"
ALL = "all"  # liste fusionnée inventaire + banque, envoyée à l'ouverture de l'HDV

STATIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    type_id      INTEGER NOT NULL,
    type_name    TEXT,
    level        INTEGER NOT NULL,
    exchangeable INTEGER NOT NULL,
    is_weapon    INTEGER NOT NULL,
    category_id  INTEGER
);
-- Identifiant de l'image d'un item (servie par DofusDB, mise en cache dans data/icons).
CREATE TABLE IF NOT EXISTS item_icons (
    item_id INTEGER PRIMARY KEY,
    icon_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS effects (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
-- Ordre d'affichage d'un effet dans une infobulle du jeu, et nom de l'image de sa caractéristique.
-- is_stat = 0 pour ce qui n'est pas une caractéristique forgemageable : lignes de dégâts d'une
-- arme, propriétés comme « Arme de chasse », signatures.
CREATE TABLE IF NOT EXISTS effect_meta (
    effect_id INTEGER PRIMARY KEY,
    priority  INTEGER NOT NULL,
    asset     TEXT,
    is_stat   INTEGER NOT NULL DEFAULT 1
);
-- Caractéristiques de base d'un item (source : DofusDB), pour repérer exos et overs.
CREATE TABLE IF NOT EXISTS item_effects (
    item_id   INTEGER NOT NULL,
    effect_id INTEGER NOT NULL,
    min_value INTEGER NOT NULL,
    max_value INTEGER NOT NULL,
    PRIMARY KEY (item_id, effect_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS item_effects_fetched (
    item_id    INTEGER PRIMARY KEY,
    fetched_at REAL NOT NULL
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
-- Annonces HDV : une ligne par annonce, avec la première et la dernière consultation où elle
-- a été vue (captured_at). Une annonce absente de la dernière consultation de son item a été
-- vendue ou retirée ; elle est gardée comme historique.
CREATE TABLE IF NOT EXISTS hdv_listings (
    item_id     INTEGER NOT NULL,
    uid         INTEGER NOT NULL,
    p1          INTEGER NOT NULL,
    p10         INTEGER NOT NULL,
    p100        INTEGER NOT NULL,
    p1000       INTEGER NOT NULL,
    effects     TEXT NOT NULL,
    captured_at REAL NOT NULL,
    first_seen  REAL,
    PRIMARY KEY (item_id, uid)
) WITHOUT ROWID;
-- Annonces encore en vente lors de la dernière consultation de chaque item.
CREATE VIEW IF NOT EXISTS hdv_current AS
    SELECT h.* FROM hdv_listings h
    JOIN (SELECT item_id, MAX(captured_at) AS seen FROM hdv_listings GROUP BY item_id) l
      ON l.item_id = h.item_id AND h.captured_at = l.seen;
-- Objets que le joueur ne veut plus voir dans les classements (réglé dans l'interface).
CREATE TABLE IF NOT EXISTS ignored_items (
    item_id  INTEGER PRIMARY KEY,
    added_at REAL NOT NULL
);
-- Types d'objets ignorés en bloc (« Aile », « Rune de forgemagie »…).
CREATE TABLE IF NOT EXISTS ignored_types (
    type_name TEXT PRIMARY KEY,
    added_at  REAL NOT NULL
);
-- Filtres de forgemagie réglés dans le dashboard, par item.
CREATE TABLE IF NOT EXISTS fm_filters (
    item_id INTEGER PRIMARY KEY,
    config  TEXT NOT NULL
);
-- Objets possédés par le joueur. Donnée personnelle, comme le reste de data/ : jamais partagée.
-- Chaque liste complète reçue remplace la précédente de son conteneur.
CREATE TABLE IF NOT EXISTS holdings (
    container TEXT NOT NULL,
    item_id   INTEGER NOT NULL,
    equipped  INTEGER NOT NULL,
    quantity  INTEGER NOT NULL,
    PRIMARY KEY (container, item_id, equipped)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS holdings_meta (
    container   TEXT PRIMARY KEY,
    captured_at REAL NOT NULL,
    kamas       INTEGER NOT NULL,
    stacks      INTEGER NOT NULL
);
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(hdv_listings)")}
    if columns and "first_seen" not in columns:
        conn.execute("ALTER TABLE hdv_listings ADD COLUMN first_seen REAL")
        conn.execute("UPDATE hdv_listings SET first_seen = captured_at")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(effect_meta)")}
    if columns and "is_stat" not in columns:  # renseignée au prochain import des données statiques
        conn.execute("ALTER TABLE effect_meta ADD COLUMN is_stat INTEGER NOT NULL DEFAULT 1")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    if columns and "category_id" not in columns:  # renseignée au prochain import des données statiques
        conn.execute("ALTER TABLE items ADD COLUMN category_id INTEGER")
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


def save_hdv_listings(conn: sqlite3.Connection, hdv, captured_at: float) -> None:
    """Enregistre les annonces d'un message décodé (messages.hdv_listings).

    Une annonce déjà connue garde sa date de première vue ; les annonces absentes du message
    restent en base avec leur ancienne date : la vue hdv_current ne les montre plus.
    """
    with conn:
        conn.executemany(
            "INSERT INTO hdv_listings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (item_id, uid) DO UPDATE SET "
            " first_seen = MIN(first_seen, excluded.first_seen), "
            " p1 = CASE WHEN excluded.captured_at >= captured_at THEN excluded.p1 ELSE p1 END, "
            " p10 = CASE WHEN excluded.captured_at >= captured_at THEN excluded.p10 ELSE p10 END, "
            " p100 = CASE WHEN excluded.captured_at >= captured_at THEN excluded.p100 ELSE p100 END, "
            " p1000 = CASE WHEN excluded.captured_at >= captured_at THEN excluded.p1000 ELSE p1000 END, "
            " effects = CASE WHEN excluded.captured_at >= captured_at THEN excluded.effects ELSE effects END, "
            " captured_at = MAX(captured_at, excluded.captured_at)",
            (
                (hdv.item_id, l.uid, *l.prices, json.dumps([list(e) for e in l.effects]), captured_at, captured_at)
                for l in hdv.listings
            ),
        )


def save_holdings(conn: sqlite3.Connection, container: str, storage, captured_at: float) -> bool:
    """Remplace le contenu d'un conteneur par une liste décodée (messages.storage.Storage).

    Renvoie False sans rien écrire si une liste plus récente est déjà enregistrée (rejeu d'archive).
    """
    row = conn.execute("SELECT captured_at FROM holdings_meta WHERE container = ?", (container,)).fetchone()
    if row is not None and row[0] > captured_at:
        return False
    totals: dict[tuple[int, bool], int] = {}
    for stack in storage.stacks:
        totals[(stack.item_id, stack.equipped)] = totals.get((stack.item_id, stack.equipped), 0) + stack.quantity
    with conn:
        conn.execute("DELETE FROM holdings WHERE container = ?", (container,))
        conn.executemany(
            "INSERT INTO holdings VALUES (?, ?, ?, ?)",
            ((container, item_id, int(equipped), quantity) for (item_id, equipped), quantity in totals.items()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO holdings_meta VALUES (?, ?, ?, ?)",
            (container, captured_at, storage.kamas, len(storage.stacks)),
        )
    return True


def set_ignored(conn: sqlite3.Connection, item_id: int, ignored: bool, now: float) -> None:
    with conn:
        if ignored:
            conn.execute("INSERT OR IGNORE INTO ignored_items VALUES (?, ?)", (item_id, now))
        else:
            conn.execute("DELETE FROM ignored_items WHERE item_id = ?", (item_id,))


def ignored_items(conn: sqlite3.Connection) -> dict[int, float]:
    """{item: date d'ajout} des objets ignorés."""
    return dict(conn.execute("SELECT item_id, added_at FROM ignored_items"))


def set_type_ignored(conn: sqlite3.Connection, type_name: str, ignored: bool, now: float) -> None:
    with conn:
        if ignored:
            conn.execute("INSERT OR IGNORE INTO ignored_types VALUES (?, ?)", (type_name, now))
        else:
            conn.execute("DELETE FROM ignored_types WHERE type_name = ?", (type_name,))


def ignored_types(conn: sqlite3.Connection) -> dict[str, float]:
    """{type: date d'ajout} des types d'objets ignorés."""
    return dict(conn.execute("SELECT type_name, added_at FROM ignored_types"))


def load_fm_filter(conn: sqlite3.Connection, item_id: int) -> dict:
    row = conn.execute("SELECT config FROM fm_filters WHERE item_id = ?", (item_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def save_fm_filter(conn: sqlite3.Connection, item_id: int, config: dict) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO fm_filters VALUES (?, ?)", (item_id, json.dumps(config)))


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
