import sqlite3

from dofustool.archive import PACKED, Archive, connect, pack, unpack
from dofustool.protocol.session import Message
from dofustool.tools import slim

NOW = 2_000_000_000.0
BIG = bytes(range(10)) * 50  # se compresse bien


def message(ts, body, key="abc"):
    return Message(0, ts, "s2c", key, body, 2, None)


def test_messages_are_stored_packed_and_read_back_unchanged(tmp_path):
    archive = Archive(tmp_path / "archive.sqlite")
    conn = archive.open_connection(NOW, 4000, "192.0.2.1", "test")
    archive.add(conn, message(NOW, BIG))
    archive.add(conn, message(NOW, b"\x08\x01"))  # trop petit : gardé tel quel
    archive.commit()
    raw = sqlite3.connect(tmp_path / "archive.sqlite").execute("SELECT body, size FROM messages ORDER BY id").fetchall()
    assert raw[0][0].startswith(PACKED) and len(raw[0][0]) < len(BIG) and raw[0][1] == len(BIG)
    assert raw[1][0] == b"\x08\x01"
    assert [row[0] for row in archive.db.execute("SELECT body FROM messages ORDER BY id")] == [BIG, b"\x08\x01"]
    archive.close()
    assert [row[0] for row in connect(tmp_path / "archive.sqlite").execute("SELECT body FROM messages ORDER BY id")] == [BIG, b"\x08\x01"]
    assert unpack(pack(BIG)) == BIG and pack(b"\x08\x01") == b"\x08\x01"


def test_prune_drops_everything_older_than_a_month(tmp_path):
    archive = Archive(tmp_path / "archive.sqlite")
    old = archive.open_connection(NOW - 40 * 86400, 4000, "192.0.2.1", "test")
    archive.add(old, message(NOW - 40 * 86400, BIG, key="kept"))
    recent = archive.open_connection(NOW - 5 * 86400, 4001, "192.0.2.1", "test")
    archive.add(recent, message(NOW - 5 * 86400, BIG, key="kept"))
    assert archive.prune(NOW, {"kept"}) == 1
    assert archive.count() == 1


def test_slim_packs_old_archives_and_drops_other_languages(tmp_path):
    path = tmp_path / "archive.sqlite"
    Archive(path).close()
    raw = sqlite3.connect(path)
    raw.execute("INSERT INTO connections (started_at, client_port, server, source) VALUES (?, 1, 'x', 'test')", (NOW,))
    raw.executemany("INSERT INTO messages (connection_id, ts, direction, key, size, frame_field, body) VALUES (1, ?, 's2c', 'k', ?, 2, ?)",
                    [(NOW, len(BIG), BIG), (NOW - 40 * 86400, len(BIG), BIG)])  # fmt: skip
    raw.commit()
    raw.close()
    note = slim.slim_archive(path, now=NOW)
    assert "1 messages de plus de 30 jours supprimés, 1 compressés" in note
    assert [row[0] for row in connect(path).execute("SELECT body FROM messages")] == [BIG]
    assert slim.slim_archive(path, now=NOW) is None  # rien de plus à faire

    static = tmp_path / "dofus.sqlite"
    s = sqlite3.connect(static)
    s.execute("CREATE TABLE translations (id TEXT, value TEXT, lang TEXT)")
    s.executemany("INSERT INTO translations VALUES (?, ?, ?)", [("1", "Rousse", "fr"), ("1", "Ginger", "en"), ("1", "Rote", "de")])
    s.commit()
    s.close()
    assert slim.slim_static(static).startswith("Données du jeu")
    assert sqlite3.connect(static).execute("SELECT lang FROM translations").fetchall() == [("fr",)]
    assert slim.slim_static(static) is None
