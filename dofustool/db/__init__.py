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
HAVRE = "havre"  # havre-sac
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
-- Coefficient d'XP de métier d'un objet fabriqué, en pourcentage (100 = normal, 0 = aucune XP).
-- Vient des données du jeu : coefficient de l'objet, sinon celui de son type.
CREATE TABLE IF NOT EXISTS recipe_xp (
    result_id INTEGER PRIMARY KEY,
    ratio_pct INTEGER NOT NULL
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
-- Cours reconstitué à partir des prix moyens (analysis.cours), après un relevé réel du cours.
-- Jamais mélangé à market_history, qui ne contient que du réel et part au hub.
CREATE TABLE IF NOT EXISTS cours_state (
    item_id      INTEGER PRIMARY KEY,
    base_at      REAL NOT NULL,  -- captured_at du relevé de départ
    processed_ts REAL NOT NULL,  -- dernier relevé de prix moyens traité
    last_price   REAL,           -- dernier prix de vente connu, sert de référence sans annonce HDV
    month_qty    INTEGER,        -- quantité vendue sur 30 j, d'après la reconstitution
    min_qty      REAL            -- plus petite vente visible dans le prix moyen arrondi
);
-- Un point par intervalle entre deux relevés de prix moyens où une vente a été détectée.
-- qty = 0 : resynchronisation (signe incohérent ou prix absurde), total est alors l'écart rattrapé.
CREATE TABLE IF NOT EXISTS cours_points (
    item_id INTEGER NOT NULL,
    end_ts  REAL NOT NULL,
    hours   REAL NOT NULL,
    total   REAL NOT NULL,
    qty     INTEGER NOT NULL,
    sure    INTEGER NOT NULL,
    PRIMARY KEY (item_id, end_ts)
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
-- Les mêmes possessions, pile par pile : une pile garde son identifiant d'un coffre à l'autre.
-- Tenue à jour par les listes complètes et par chaque mouvement d'inventaire ; holdings en est le total par objet.
CREATE TABLE IF NOT EXISTS piles (
    uid       INTEGER NOT NULL,
    container TEXT NOT NULL,     -- 'inventory', 'bank' ou 'havre'
    item_id   INTEGER NOT NULL,
    quantity  INTEGER NOT NULL,
    equipped  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (uid, container)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS piles_item ON piles (item_id, container);
CREATE TABLE IF NOT EXISTS holdings_meta (
    container   TEXT PRIMARY KEY,
    captured_at REAL NOT NULL,
    kamas       INTEGER NOT NULL,
    stacks      INTEGER NOT NULL
);
-- Personnages du compte et leurs niveaux de métier. Donnée personnelle, comme holdings.
CREATE TABLE IF NOT EXISTS characters (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    level   INTEGER NOT NULL,
    seen_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS character_jobs (
    character_id INTEGER NOT NULL,
    job_id       INTEGER NOT NULL,
    level        INTEGER NOT NULL,
    xp           INTEGER NOT NULL,
    captured_at  REAL NOT NULL,
    PRIMARY KEY (character_id, job_id)
) WITHOUT ROWID;
-- Lots mis en vente par le joueur, par HDV (empreinte de son descripteur). Donnée personnelle.
CREATE TABLE IF NOT EXISTS my_sales (
    market      INTEGER NOT NULL,
    uid         INTEGER NOT NULL,
    item_id     INTEGER NOT NULL,
    lot         INTEGER NOT NULL,
    price       INTEGER NOT NULL,
    remaining_s INTEGER NOT NULL,
    captured_at REAL NOT NULL,
    effects     TEXT,  -- jets d'un lot d'équipement (JSON), s'ils ont été lus
    PRIMARY KEY (market, uid)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS my_sales_meta (
    market      INTEGER PRIMARY KEY,
    captured_at REAL NOT NULL,
    lots        INTEGER NOT NULL
);
-- Ventes conclues et achats du joueur, annoncés par le jeu. Donnée personnelle, jamais partagée.
-- source_id : numéro du message dans l'archive brute, pour qu'un rejeu ne compte rien deux fois.
CREATE TABLE IF NOT EXISTS trades (
    source_id INTEGER PRIMARY KEY,
    ts        REAL NOT NULL,
    kind      TEXT NOT NULL,     -- 'sale' ou 'purchase'
    item_id   INTEGER NOT NULL,
    quantity  INTEGER NOT NULL,  -- taille du lot
    price     INTEGER NOT NULL,  -- prix du lot entier
    ref       INTEGER            -- achat : numéro annoncé par le jeu ; vente : identifiant du lot parti, s'il est connu
);
CREATE INDEX IF NOT EXISTS trades_ts ON trades (ts);
-- Kamas des ventes conclues hors ligne, tels que le jeu les annonce à la connexion (total en attente en banque).
-- delta : ce qui s'est ajouté depuis l'annonce précédente ; attributed : 1 quand les lots vendus ont été retrouvés.
CREATE TABLE IF NOT EXISTS offline_sales (
    source_id  INTEGER PRIMARY KEY,
    ts         REAL NOT NULL,
    total      INTEGER NOT NULL,
    delta      INTEGER NOT NULL,
    attributed INTEGER NOT NULL DEFAULT 0
);
-- Passages de rune en forgemagie. Donnée personnelle, jamais partagée.
CREATE TABLE IF NOT EXISTS fm_passes (
    source_id   INTEGER PRIMARY KEY,
    ts          REAL NOT NULL,
    uid         INTEGER NOT NULL,  -- identifiant de l'exemplaire travaillé
    item_id     INTEGER NOT NULL,
    rune_id     INTEGER NOT NULL,
    passed      INTEGER NOT NULL,  -- 1 : la rune est passée
    lost        INTEGER,           -- 1 : d'autres lignes ont baissé ; NULL : état d'avant inconnu
    pool        REAL,              -- puits après le passage
    pool_change INTEGER NOT NULL,  -- 0 inchangé, 1 en hausse, 2 en baisse
    rune_price  REAL               -- prix unitaire de la rune, figé à la première consultation du journal
);
CREATE INDEX IF NOT EXISTS fm_passes_uid ON fm_passes (uid, ts);
-- Un dossier par exemplaire forgemagé : ses jets avant et après, et ce qu'il est devenu.
CREATE TABLE IF NOT EXISTS fm_items (
    uid          INTEGER PRIMARY KEY,
    item_id      INTEGER NOT NULL,
    first_ts     REAL NOT NULL,
    last_ts      REAL NOT NULL,
    before       TEXT,     -- effets avant le premier passage (JSON), s'ils ont été vus
    after        TEXT NOT NULL,
    base_cost    INTEGER,  -- prix de l'objet de base saisi à la main
    listed_price INTEGER,
    listed_at    REAL,
    sold_price   INTEGER,
    sold_at      REAL
);
-- Échanges conclus avec d'autres joueurs, et objets fabriqués. Donnée personnelle, jamais partagée.
-- L'autre joueur n'est pas enregistré : ni son nom, ni son identifiant.
CREATE TABLE IF NOT EXISTS exchanges (
    source_id      INTEGER PRIMARY KEY,  -- numéro du message de clôture dans l'archive brute
    ts             REAL NOT NULL,
    kamas_given    INTEGER NOT NULL,
    kamas_received INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS exchange_items (
    source_id INTEGER NOT NULL REFERENCES exchanges(source_id),
    received  INTEGER NOT NULL,  -- 1 : reçu ; 0 : donné
    uid       INTEGER NOT NULL,
    item_id   INTEGER NOT NULL,
    quantity  INTEGER NOT NULL,
    PRIMARY KEY (source_id, received, uid)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS crafts (
    source_id INTEGER PRIMARY KEY,
    ts        REAL NOT NULL,
    item_id   INTEGER NOT NULL,
    quantity  INTEGER NOT NULL,
    uid       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS crafts_ts ON crafts (ts);
-- Almanax d'un jour (bonus et offrande), tel que DofusDB le donne : gardé pour ne l'interroger qu'une fois.
CREATE TABLE IF NOT EXISTS almanax (
    day        TEXT PRIMARY KEY,  -- AAAA-MM-JJ
    payload    TEXT NOT NULL,
    fetched_at REAL NOT NULL
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(my_sales)")}
    if columns and "effects" not in columns:  # renseignés au prochain relevé de l'onglet Vendre
        conn.execute("ALTER TABLE my_sales ADD COLUMN effects TEXT")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    if columns and "ref" not in columns:  # renseignée pour les anciens achats au prochain rejeu de l'archive
        conn.execute("ALTER TABLE trades ADD COLUMN ref INTEGER")
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


# Les relevés de prix moyens sont gardés en entier pendant ce nombre de jours (les calculs regardent
# 30 jours en arrière au plus) ; au-delà, un seul par jour suffit à tracer l'évolution d'un prix.
SNAPSHOT_FULL_DAYS = 35


def thin_snapshots(conn: sqlite3.Connection, now: float, keep_days: float = SNAPSHOT_FULL_DAYS) -> int:
    """Au-delà de keep_days jours, ne garde que le dernier relevé de prix moyens de chaque jour (UTC, comme le jeu).

    Renvoie le nombre de relevés supprimés. Un relevé pèse environ 0,3 Mo, et il en arrive une dizaine par jour de jeu.
    """
    last_of_day: dict[int, int] = {}
    old = conn.execute("SELECT id, ts FROM snapshots WHERE ts < ? ORDER BY ts, id", (now - keep_days * 86400,)).fetchall()
    for snapshot_id, ts in old:
        last_of_day[int(ts // 86400)] = snapshot_id
    extra = [(snapshot_id,) for snapshot_id, _ in old if snapshot_id not in last_of_day.values()]
    if extra:
        with conn:
            conn.executemany("DELETE FROM avg_prices WHERE snapshot_id = ?", extra)
            conn.executemany("DELETE FROM snapshots WHERE id = ?", extra)
    return len(extra)


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


# Coffres du jeu (messages.inventory) vers les conteneurs de la base.
PILE_CONTAINERS = {1: INVENTORY, 2: BANK, 3: HAVRE}
# Un mouvement isolé ne signale un changement à l'interface qu'à cet intervalle : une séance de
# forgemagie consomme une rune par seconde, et chaque signal fait recalculer toutes les pages.
STOCK_SIGNAL_EVERY_S = 15.0


def _refresh_holdings(conn: sqlite3.Connection, container: str, item_ids=None) -> None:
    """Recalcule le total par objet d'un conteneur à partir de ses piles (tout le conteneur, ou seulement ces objets)."""
    if item_ids is None:
        conn.execute("DELETE FROM holdings WHERE container = ?", (container,))
        conn.execute(
            "INSERT INTO holdings SELECT container, item_id, equipped, SUM(quantity) FROM piles "
            "WHERE container = ? AND quantity > 0 GROUP BY item_id, equipped",
            (container,),
        )
        return
    for item_id in item_ids:
        conn.execute("DELETE FROM holdings WHERE container = ? AND item_id = ?", (container, item_id))
        conn.execute(
            "INSERT INTO holdings SELECT container, item_id, equipped, SUM(quantity) FROM piles "
            "WHERE container = ? AND item_id = ? AND quantity > 0 GROUP BY equipped",
            (container, item_id),
        )


def _stamp_holdings(conn: sqlite3.Connection, container: str, ts: float, kamas: int | None = None) -> None:
    row = conn.execute("SELECT kamas FROM holdings_meta WHERE container = ?", (container,)).fetchone()
    stacks = conn.execute("SELECT COUNT(*) FROM piles WHERE container = ? AND quantity > 0", (container,)).fetchone()[0]
    conn.execute(
        "INSERT OR REPLACE INTO holdings_meta VALUES (?, ?, ?, ?)",
        (container, ts, kamas if kamas is not None else (row[0] if row else 0), stacks),
    )


def save_piles(conn: sqlite3.Connection, storage, captured_at: float, only: str | None = None) -> set[str]:
    """Enregistre une liste complète pile par pile (messages.storage.Storage dont les piles ont un identifiant).

    only : conteneur d'un coffre ouvert seul (banque ou havre-sac). Sinon c'est une liste de l'inventaire :
    simple, ou réunie quand le serveur détaille la répartition des piles ; seuls les coffres qu'elle
    cite sont remplacés, les autres gardent leur dernier contenu connu.
    Renvoie les conteneurs mis à jour (vide si un relevé plus récent existe déjà).
    """
    rows: dict[str, list[tuple]] = {}
    if only is not None:
        rows[only] = [(s.uid, only, s.item_id, s.quantity, 0) for s in storage.stacks]
    else:
        rows[INVENTORY] = []
        for s in storage.stacks:
            if not s.parts:
                rows[INVENTORY].append((s.uid, INVENTORY, s.item_id, s.quantity, int(s.equipped)))
            for part, quantity in s.parts:
                rows.setdefault(PILE_CONTAINERS[part], []).append((s.uid, PILE_CONTAINERS[part], s.item_id, quantity, 0))
    known = dict(conn.execute("SELECT container, captured_at FROM holdings_meta"))
    fresh = {container for container in rows if known.get(container, 0) <= captured_at}
    if not fresh:
        return set()
    with conn:
        # L'ancienne liste réunie « tout confondu » n'a plus cours dès que les coffres sont connus séparément.
        conn.execute("DELETE FROM holdings WHERE container = ?", (ALL,))
        conn.execute("DELETE FROM holdings_meta WHERE container = ?", (ALL,))
        for container in fresh:
            conn.execute("DELETE FROM piles WHERE container = ?", (container,))
            conn.executemany("INSERT OR REPLACE INTO piles VALUES (?, ?, ?, ?, ?)", [r for r in rows[container] if r[3] > 0])
            _refresh_holdings(conn, container)
            # Les kamas d'une liste de l'inventaire sont ceux du personnage ; ceux d'un coffre ouvert seul,
            # banque ou havre-sac, sont ceux de la banque.
            _stamp_holdings(conn, container, captured_at, storage.kamas if container == INVENTORY else 0 if container == HAVRE else None)
        if only is not None:
            conn.execute("UPDATE holdings_meta SET kamas = ? WHERE container = ?", (storage.kamas, BANK))
    return fresh


def _signal(conn: sqlite3.Connection, container: str, ts: float) -> None:
    """Date le conteneur pour que l'interface se rafraîchisse, sans le faire à chaque mouvement."""
    row = conn.execute("SELECT captured_at FROM holdings_meta WHERE container = ?", (container,)).fetchone()
    if row is None or ts - row[0] >= STOCK_SIGNAL_EVERY_S:
        _stamp_holdings(conn, container, ts)


def flush_stock_signal(conn: sqlite3.Connection, ts: float) -> None:
    """À appeler régulièrement : signale à l'interface les derniers mouvements restés sous le seuil."""
    with conn:
        for (container,) in conn.execute("SELECT container FROM holdings_meta WHERE container IN (?, ?, ?)", (INVENTORY, BANK, HAVRE)).fetchall():
            _stamp_holdings(conn, container, ts)


def _outdated(conn: sqlite3.Connection, ts: float) -> bool:
    """Vrai si un relevé de l'inventaire plus récent que ce mouvement est déjà enregistré (rejeu d'archive)."""
    row = conn.execute("SELECT captured_at FROM holdings_meta WHERE container = ?", (INVENTORY,)).fetchone()
    return row is None or row[0] > ts


def update_pile(conn: sqlite3.Connection, update, visible: set[str], ts: float) -> bool:
    """Applique une pile modifiée (messages.inventory.PileUpdate). Renvoie False si la pile est inconnue.

    visible : coffres que le joueur a sous les yeux (ceux de la dernière liste réunie). Une part absente
    pour un coffre visible veut dire qu'il n'y en a plus ; pour un coffre non visible, on ne sait rien.
    """
    known = dict(conn.execute("SELECT container, quantity FROM piles WHERE uid = ?", (update.uid,)))
    item = conn.execute("SELECT item_id FROM piles WHERE uid = ? LIMIT 1", (update.uid,)).fetchone()
    if item is None or _outdated(conn, ts):
        return False
    seen = visible | {INVENTORY}
    if update.parts or update.quantity == 0:
        # Le total fait foi. Les parts de la banque et du havre-sac sont justes ; celle de l'inventaire est
        # ce qui reste, car un lot acheté s'ajoute au total avant que le détail ne suive.
        wanted = {container: 0 for container in seen}
        wanted.update({PILE_CONTAINERS[part]: quantity for part, quantity in update.parts if part != 1})
        wanted[INVENTORY] = max(0, update.quantity - sum(q for c, q in wanted.items() if c != INVENTORY))
    else:
        held = [container for container in seen if known.get(container, 0) > 0]
        if len(held) == 1:
            wanted = {held[0]: update.quantity}  # la pile n'est que dans ce coffre
        else:
            wanted = {INVENTORY: max(0, update.quantity - sum(known.get(c, 0) for c in seen if c != INVENTORY))}
    with conn:
        for container, quantity in wanted.items():
            if known.get(container, 0) == quantity:
                continue
            if quantity > 0:
                conn.execute(
                    "INSERT INTO piles VALUES (?, ?, ?, ?, 0) ON CONFLICT (uid, container) DO UPDATE SET quantity = excluded.quantity",
                    (update.uid, container, item[0], quantity),
                )
            else:
                conn.execute("DELETE FROM piles WHERE uid = ? AND container = ?", (update.uid, container))
            _refresh_holdings(conn, container, [item[0]])
            _signal(conn, container, ts)
    return True


def add_pile(conn: sqlite3.Connection, obj, ts: float) -> bool:
    """Objet entré dans l'inventaire, ou modifié (messages.inventory.NewObject). Renvoie True si le stock a changé.

    Un objet modifié arrive entier à chaque fois (un équipement à chaque rune passée) : sans changement
    de quantité, rien n'est écrit.
    """
    parts = [(PILE_CONTAINERS[part], quantity) for part, quantity in obj.parts] or [(INVENTORY, obj.quantity)]
    if _outdated(conn, ts):
        return False
    known = dict(conn.execute("SELECT container, quantity FROM piles WHERE uid = ?", (obj.uid,)))
    parts = [(container, quantity) for container, quantity in parts if quantity > 0 and known.get(container) != quantity]
    if not parts:
        return False
    with conn:
        for container, quantity in parts:
            conn.execute(
                "INSERT INTO piles VALUES (?, ?, ?, ?, ?) ON CONFLICT (uid, container) DO UPDATE SET "
                "quantity = excluded.quantity, item_id = excluded.item_id, equipped = excluded.equipped",
                (obj.uid, container, obj.item_id, quantity, int(obj.equipped)),
            )
            _refresh_holdings(conn, container, [obj.item_id])
            _signal(conn, container, ts)
    return True


def remove_pile(conn: sqlite3.Connection, uid: int, ts: float) -> bool:
    """Pile sortie de l'inventaire. Renvoie False si elle n'y était pas connue."""
    row = conn.execute("SELECT item_id FROM piles WHERE uid = ? AND container = ?", (uid, INVENTORY)).fetchone()
    if row is None or _outdated(conn, ts):
        return False
    with conn:
        conn.execute("DELETE FROM piles WHERE uid = ? AND container = ?", (uid, INVENTORY))
        _refresh_holdings(conn, INVENTORY, [row[0]])
        _signal(conn, INVENTORY, ts)
    return True


def set_kamas(conn: sqlite3.Connection, kamas: int, ts: float) -> None:
    """Nouveau total de kamas du personnage."""
    if _outdated(conn, ts):
        return
    with conn:
        conn.execute("UPDATE holdings_meta SET kamas = ? WHERE container = ?", (kamas, INVENTORY))
        _signal(conn, INVENTORY, ts)


# Un lot est rendu à l'expiration de sa mise en vente ; l'heure calculée d'après le dernier relevé peut
# dériver un peu, et un lot expiré hors ligne n'est annoncé qu'à la connexion suivante.
UNSOLD_TOLERANCE_S = 3600.0


def return_unsold(conn: sqlite3.Connection, count: int, ts: float) -> list[tuple[int, int, int]]:
    """Lots invendus que le jeu vient de rentrer en banque : il n'en donne que le nombre.

    Ce sont ceux dont la mise en vente arrive à expiration. Ils quittent mes lots en vente et rejoignent
    la banque. Renvoie [(objet, taille du lot, prix)] ; moins que count si l'heure d'expiration ne désigne
    pas assez de lots (relevé de l'onglet Vendre trop ancien).
    """
    rows = conn.execute(
        "SELECT market, uid, item_id, lot, price FROM my_sales WHERE remaining_s - (? - captured_at) <= ? "
        "ORDER BY remaining_s - (? - captured_at) LIMIT ?",
        (ts, UNSOLD_TOLERANCE_S, ts, max(0, min(count, 200))),
    ).fetchall()
    bank_known = conn.execute("SELECT 1 FROM holdings_meta WHERE container = ?", (BANK,)).fetchone() is not None
    with conn:
        for market, uid, item_id, lot, price in rows:
            conn.execute("DELETE FROM my_sales WHERE market = ? AND uid = ?", (market, uid))
            if bank_known:
                conn.execute(
                    "INSERT INTO piles VALUES (?, ?, ?, ?, 0) ON CONFLICT (uid, container) DO UPDATE SET quantity = quantity + excluded.quantity",
                    (uid, BANK, item_id, lot),
                )
                _refresh_holdings(conn, BANK, [item_id])
        if rows and bank_known:
            _stamp_holdings(conn, BANK, ts)
    return [(item_id, lot, price) for _, _, item_id, lot, price in rows]


def save_exchange(conn: sqlite3.Connection, source_id: int, ts: float, exchange) -> bool:
    """Enregistre un échange conclu (messages.exchange.Exchange). Renvoie False s'il était déjà connu."""
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO exchanges VALUES (?, ?, ?, ?)", (source_id, ts, exchange.kamas_given, exchange.kamas_received)
        )
        if not cur.rowcount:
            return False
        conn.executemany(
            "INSERT OR REPLACE INTO exchange_items VALUES (?, ?, ?, ?, ?)",
            [
                (source_id, received, uid, item_id, quantity)
                for received, side in ((0, exchange.given), (1, exchange.received))
                for uid, (item_id, quantity) in side.items()
            ],
        )
    return True


def save_craft(conn: sqlite3.Connection, source_id: int, ts: float, obj) -> bool:
    """Enregistre un objet fabriqué (messages.fm.Object). Renvoie False s'il était déjà connu."""
    with conn:
        cur = conn.execute("INSERT OR IGNORE INTO crafts VALUES (?, ?, ?, ?, ?)", (source_id, ts, obj.item_id, obj.quantity, obj.uid))
    return bool(cur.rowcount)


def _rolls(sale) -> str | None:
    return json.dumps([list(e) for e in sale.effects]) if sale.effects else None


def save_sales(conn: sqlite3.Connection, listing, captured_at: float) -> bool:
    """Remplace les lots en vente d'un HDV par une liste décodée (messages.sales.SalesList).

    Renvoie False sans rien écrire si une liste plus récente de cet HDV est déjà enregistrée.
    """
    row = conn.execute("SELECT captured_at FROM my_sales_meta WHERE market = ?", (listing.market,)).fetchone()
    if row is not None and row[0] > captured_at:
        return False
    with conn:
        # L'empreinte d'un HDV peut changer avec une mise à jour du jeu (et les lots y sont renumérotés).
        # Un objet ne se vend que dans un seul HDV : une liste enregistrée sous une autre empreinte
        # qui contient l'un de ces objets est donc l'ancien relevé du même HDV.
        ids = sorted({s.item_id for s in listing.sales})
        marks = ",".join("?" * len(ids))
        same = [
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT market FROM my_sales WHERE market != ? AND item_id IN ({marks})", (listing.market, *ids)
            )
        ]
        listed = {s.uid for s in listing.sales}
        gone = [
            row
            for market in (listing.market, *same)
            for row in conn.execute("SELECT uid, item_id, lot, price FROM my_sales WHERE market = ?", (market,))
            if row[0] not in listed
        ]
        # Un relevé rejoué sans les jets (clés des annonces HDV pas encore retrouvées) ne les efface pas.
        kept = dict(conn.execute("SELECT uid, effects FROM my_sales WHERE market = ? AND effects IS NOT NULL", (listing.market,)))
        _attribute_offline(conn, gone, row[0] if row is not None else captured_at, captured_at)
        for market in same:
            conn.execute("DELETE FROM my_sales WHERE market = ?", (market,))
            conn.execute("DELETE FROM my_sales_meta WHERE market = ?", (market,))
        conn.execute("DELETE FROM my_sales WHERE market = ?", (listing.market,))
        conn.executemany(
            "UPDATE fm_items SET listed_price = ?, listed_at = COALESCE(listed_at, ?) WHERE uid = ? AND sold_at IS NULL",
            ((s.price, captured_at, s.uid) for s in listing.sales),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO my_sales VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ((listing.market, s.uid, s.item_id, s.lot, s.price, s.remaining_s, captured_at, _rolls(s)) for s in listing.sales),
        )
        conn.execute("INSERT OR REPLACE INTO my_sales_meta VALUES (?, ?, ?)", (listing.market, captured_at, len(listing.sales)))
        conn.executemany(
            "UPDATE my_sales SET effects = ? WHERE market = ? AND uid = ? AND effects IS NULL",
            ((effects, listing.market, uid) for uid, effects in kept.items()),
        )
    return True


def save_trade(conn: sqlite3.Connection, source_id: int, trade, ts: float) -> bool:
    """Enregistre une vente ou un achat (messages.trades.Trade). Renvoie False s'il était déjà connu.

    Une vente retire aussi le lot correspondant de la liste des lots en vente, sans attendre le
    prochain relevé de l'onglet Vendre.
    """
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO trades VALUES (?, ?, ?, ?, ?, ?, ?)",
            (source_id, ts, trade.kind, trade.item_id, trade.quantity, trade.price, trade.ref or None),
        )
        if not cur.rowcount:
            # Achat enregistré avant que son numéro ne soit lu : le rejeu de l'archive le complète.
            if trade.ref:
                conn.execute("UPDATE trades SET ref = ? WHERE source_id = ? AND ref IS NULL", (trade.ref, source_id))
            return False
        if trade.kind == "sale":
            # Parmi mes lots identiques relevés avant cette vente, un seul part : le plus proche de l'expiration.
            lot = conn.execute(
                "SELECT market, uid FROM my_sales WHERE item_id = ? AND lot = ? AND price = ? AND captured_at <= ? "
                "ORDER BY remaining_s - (? - captured_at) LIMIT 1",
                (trade.item_id, trade.quantity, trade.price, ts, ts),
            ).fetchone()
            if lot is not None:
                conn.execute("DELETE FROM my_sales WHERE market = ? AND uid = ?", lot)
                conn.execute("UPDATE trades SET ref = ? WHERE source_id = ?", (lot[1], source_id))
                _mark_fm_sold(conn, lot[1], trade.price, ts)
    return True


def _mark_fm_sold(conn: sqlite3.Connection, uid: int, price: int, ts: float) -> None:
    conn.execute("UPDATE fm_items SET sold_price = ?, sold_at = ? WHERE uid = ? AND sold_at IS NULL", (price, ts, uid))


# Au-delà, trop de combinaisons de lots donnent la même somme pour en retenir une (et une mise à jour
# du jeu renumérote tous les lots d'un coup : ils semblent alors tous partis).
MAX_OFFLINE_LOTS = 16


def _subset(lots: list[tuple], amount: int) -> list[tuple] | None:
    """Les lots (uid, objet, taille, prix) dont les prix font exactement ce montant, si une seule réponse existe.

    Deux lots identiques sont interchangeables : ils ne font pas deux réponses.
    """
    if not lots or len(lots) > MAX_OFFLINE_LOTS:
        return None
    found: dict[tuple, list[tuple]] = {}
    for mask in range(1, 1 << len(lots)):
        picked = [lot for bit, lot in enumerate(lots) if mask >> bit & 1]
        if sum(lot[3] for lot in picked) == amount:
            found.setdefault(tuple(sorted(lot[1:] for lot in picked)), picked)
            if len(found) > 1:
                return None
    return next(iter(found.values()), None)


def _attribute_offline(conn: sqlite3.Connection, gone: list[tuple], since: float, ts: float) -> None:
    """Rapproche les lots disparus d'un relevé de mes ventes des kamas gagnés hors ligne.

    N'inscrit une vente que si les prix des lots font le montant au kama près, d'une seule façon :
    un lot retiré à la main ou expiré ne doit pas passer pour une vente.
    """
    # Une vente conclue entre deux relevés a été annoncée à une connexion située entre les deux.
    pending = conn.execute(
        "SELECT source_id, ts, delta FROM offline_sales WHERE attributed = 0 AND delta > 0 AND ts > ? AND ts <= ? ORDER BY ts",
        (since, ts),
    ).fetchall()
    if not pending or not gone:
        return
    # D'abord annonce par annonce, pour dater chaque vente de la bonne connexion ; sinon le tout d'un bloc.
    plan, left = [], list(gone)
    for source_id, at, delta in pending:
        picked = _subset(left, delta)
        if picked is not None:
            plan.append(([source_id], at, picked))
            left = [lot for lot in left if lot not in picked]
    if len(plan) < len(pending):
        done = {source_id for sources, _, _ in plan for source_id in sources}
        rest = [row for row in pending if row[0] not in done]
        picked = _subset(left, sum(delta for _, _, delta in rest))
        if picked is not None:
            plan.append(([row[0] for row in rest], rest[-1][1], picked))
    for sources, at, picked in plan:
        source_id = sources[-1]
        for rank, (uid, item_id, lot, price) in enumerate(picked):
            # Numéro négatif : une vente déduite, qu'aucun message du jeu n'annonce.
            conn.execute(
                "INSERT OR IGNORE INTO trades VALUES (?, ?, 'sale', ?, ?, ?, ?)",
                (-(source_id * 100 + rank), at, item_id, lot, price, uid),
            )
            _mark_fm_sold(conn, uid, price, at)
        marks = ",".join("?" * len(sources))
        conn.execute(f"UPDATE offline_sales SET attributed = 1 WHERE source_id IN ({marks})", sources)


def save_offline_total(conn: sqlite3.Connection, source_id: int, total: int, ts: float) -> int:
    """Enregistre le total des ventes hors ligne annoncé par le jeu. Renvoie ce qui s'y est ajouté (0 si déjà connu).

    Le total ne fait que monter jusqu'à ce que le joueur retire des kamas de sa banque : il repart alors de zéro.
    """
    previous = conn.execute(
        "SELECT total FROM offline_sales WHERE ts <= ? AND source_id != ? ORDER BY ts DESC, source_id DESC LIMIT 1", (ts, source_id)
    ).fetchone()
    if previous is None:
        delta = 0  # première annonce connue : on ne sait pas ce qui s'y est ajouté, elle sert de point de départ
    else:
        delta = total - previous[0] if total >= previous[0] else total
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO offline_sales VALUES (?, ?, ?, ?, ?)", (source_id, ts, total, delta, int(delta == 0))
        )
    return delta if cur.rowcount else 0


def save_fm_pass(conn: sqlite3.Connection, source_id: int, ts: float, result, rune_id: int, before) -> bool:
    """Enregistre un passage de rune (messages.fm.Result). before : effets de l'objet juste avant, s'ils sont connus.

    Renvoie False s'il était déjà connu.
    """
    obj = result.object
    after = {effect_id: value for effect_id, value in obj.effects if value is not None}
    lost = None
    if before is not None:
        lost = int(any(value > after.get(effect_id, 0) for effect_id, value in before if value is not None and value > 0))
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO fm_passes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (source_id, ts, obj.uid, obj.item_id, rune_id, int(result.passed), lost, result.pool, result.pool_change),
        )
        if not cur.rowcount:
            return False
        conn.execute(
            "INSERT INTO fm_items (uid, item_id, first_ts, last_ts, before, after) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (uid) DO UPDATE SET "
            " before = CASE WHEN excluded.first_ts < first_ts THEN excluded.before ELSE before END, "
            " after = CASE WHEN excluded.last_ts >= last_ts THEN excluded.after ELSE after END, "
            " first_ts = MIN(first_ts, excluded.first_ts), last_ts = MAX(last_ts, excluded.last_ts)",
            (
                obj.uid, obj.item_id, ts, ts,
                json.dumps([list(e) for e in before]) if before is not None else None,
                json.dumps([list(e) for e in obj.effects]),
            ),
        )  # fmt: skip
    return True


def set_fm_base_cost(conn: sqlite3.Connection, uid: int, cost: int | None) -> None:
    with conn:
        conn.execute("UPDATE fm_items SET base_cost = ? WHERE uid = ?", (cost, uid))


def save_lot_update(conn: sqlite3.Connection, sale, ts: float) -> None:
    """Lot créé ou modifié (messages.sales.Sale) : il garde son HDV s'il est connu, sinon celui du dernier relevé."""
    row = conn.execute("SELECT market FROM my_sales WHERE uid = ?", (sale.uid,)).fetchone()
    if row is None:
        row = conn.execute("SELECT market FROM my_sales_meta ORDER BY captured_at DESC LIMIT 1").fetchone()
    if row is None:
        return  # aucun relevé de l'onglet Vendre encore : on ne sait pas à quel HDV rattacher ce lot
    with conn:
        conn.execute("DELETE FROM my_sales WHERE uid = ?", (sale.uid,))
        conn.execute(
            "INSERT INTO my_sales VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (row[0], sale.uid, sale.item_id, sale.lot, sale.price, sale.remaining_s, ts, _rolls(sale)),
        )
        conn.execute(
            "UPDATE fm_items SET listed_price = ?, listed_at = COALESCE(listed_at, ?) WHERE uid = ? AND sold_at IS NULL",
            (sale.price, ts, sale.uid),
        )


def save_characters(conn: sqlite3.Connection, characters, seen_at: float) -> None:
    """Enregistre les personnages du compte (messages.characters.Character) ; le relevé le plus récent gagne."""
    with conn:
        conn.executemany(
            "INSERT INTO characters VALUES (?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET "
            "name = excluded.name, level = excluded.level, seen_at = excluded.seen_at "
            "WHERE excluded.seen_at >= characters.seen_at",
            ((c.id, c.name, c.level, seen_at) for c in characters),
        )


def save_job_levels(conn: sqlite3.Connection, character_id: int, jobs, captured_at: float) -> None:
    """Enregistre des niveaux de métier (messages.characters.JobLevel) ; le relevé le plus récent gagne."""
    with conn:
        conn.executemany(
            "INSERT INTO character_jobs VALUES (?, ?, ?, ?, ?) ON CONFLICT (character_id, job_id) DO UPDATE SET "
            "level = excluded.level, xp = excluded.xp, captured_at = excluded.captured_at "
            "WHERE excluded.captured_at >= character_jobs.captured_at",
            ((character_id, j.job_id, j.level, j.xp, captured_at) for j in jobs),
        )


def character_jobs(conn: sqlite3.Connection, character_id: int) -> dict[int, tuple[int, int, float]]:
    """{id du métier: (niveau, expérience, date du relevé)} d'un personnage."""
    rows = conn.execute(
        "SELECT job_id, level, xp, captured_at FROM character_jobs WHERE character_id = ?", (character_id,)
    )
    return {job_id: (level, xp, captured_at) for job_id, level, xp, captured_at in rows}


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
