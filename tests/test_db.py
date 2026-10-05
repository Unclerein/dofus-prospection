import sqlite3

import pytest

from dofustool import db
from dofustool.archive import Archive
from dofustool.db.backfill import backfill
from dofustool.messages import avg_prices, load_keymap
from dofustool.protocol.session import Message
from dofustool.protocol.tcp import S2C

from .test_avg_prices import FIXTURE


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_connect_creates_all_tables(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"snapshots", "avg_prices", "last_sales", "market_history", "items", "recipes", "static_meta"} <= tables


def test_snapshot_dedup_is_by_content_not_time(conn):
    first = db.save_snapshot(conn, 100.0, {1: 10, 2: 20})
    assert first is not None
    assert db.save_snapshot(conn, 99999.0, {2: 20, 1: 10}) is None  # même contenu, bien plus tard
    second = db.save_snapshot(conn, 101.0, {1: 10, 2: 21})  # un prix change, une seconde après
    assert second is not None and second != first
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM avg_prices").fetchone()[0] == 4
    assert db.latest_snapshot(conn) == (second, 101.0)
    assert db.snapshot_prices(conn, first) == {1: 10, 2: 20}


def test_empty_snapshot_is_not_saved(conn):
    assert db.save_snapshot(conn, 1.0, {}) is None
    assert db.latest_snapshot(conn) is None


def test_last_sales_keep_each_observation(conn):
    db.save_last_sale(conn, 7, 100, 10.0)
    db.save_last_sale(conn, 7, 120, 20.0)
    db.save_last_sale(conn, 7, 120, 20.0)
    assert conn.execute("SELECT COUNT(*) FROM last_sales").fetchone()[0] == 2
    assert db.latest_last_sale(conn, 7) == (120, 20.0)
    assert db.latest_last_sale(conn, 8) is None


def test_market_history_latest_capture_wins(conn):
    db.save_market_history(conn, 7, "7d", [(1000, 50, 3), (2000, 60, None)], captured_at=10.0)
    db.save_market_history(conn, 7, "7d", [(2000, 65, 9), (3000, 70, 1)], captured_at=20.0)
    db.save_market_history(conn, 7, "7d", [(1000, 1, 1)], captured_at=5.0)  # relevé plus ancien : ignoré
    db.save_market_history(conn, 7, "30d", [(1000, 55, 30)], captured_at=20.0)  # autre période : distinct
    rows = conn.execute(
        "SELECT period, bucket_ts, price, qty_sold, captured_at FROM market_history ORDER BY period, bucket_ts"
    ).fetchall()
    assert rows == [
        ("30d", 1000, 55, 30, 20.0),
        ("7d", 1000, 50, 3, 10.0),
        ("7d", 2000, 65, 9, 20.0),
        ("7d", 3000, 70, 1, 20.0),
    ]


def test_backfill_from_archive_is_idempotent(conn):
    mapping = load_keymap()["avg_prices"]
    body = FIXTURE.read_bytes()
    archive = Archive(":memory:")
    conn_id = archive.open_connection(1.0, 40000, "192.0.2.1", "test")
    for ts in (10.0, 500.0):  # deux connexions, même liste renvoyée par le serveur
        archive.add(conn_id, Message(0, ts, S2C, mapping.key, body, 2, None))
    archive.add(conn_id, Message(0, 600.0, S2C, mapping.key, b"\x08\x01", 2, None))  # même clé, autre forme
    archive.commit()

    assert backfill(archive._db, conn) == {"lus": 3, "relevés ajoutés": 1, "rejetés": 1}
    assert backfill(archive._db, conn) == {"lus": 3, "relevés ajoutés": 0, "rejetés": 1}
    snapshot_id, ts = db.latest_snapshot(conn)
    assert ts == 10.0
    assert db.snapshot_prices(conn, snapshot_id) == avg_prices.parse(body, mapping)
