"""Allègement de l'archive brute : seuls les vieux messages non décodés partent. Données synthétiques uniquement."""
from dofustool import db
from dofustool.archive import KEEP_DAYS, Archive
from dofustool.protocol.session import Message
from dofustool.protocol.tcp import C2S, S2C

DAY = 86400.0
NOW = 100 * DAY


def test_prune_keeps_recent_connections_and_decoded_messages():
    archive = Archive(":memory:")
    old = archive.open_connection(NOW - 20 * DAY, 40000, "192.0.2.1", "live")
    recent = archive.open_connection(NOW - 2 * DAY, 40001, "192.0.2.1", "live")
    for conn, start in ((old, NOW - 20 * DAY), (recent, NOW - 2 * DAY)):
        archive.add(conn, Message(0, start + 1, S2C, "isr", b"prix", 2, None))  # décodé : gardé
        archive.add(conn, Message(0, start + 2, S2C, "zzz", b"deplacement", 2, None))
        archive.add(conn, Message(0, start + 3, C2S, "yyy", b"chat", 1, 4))
    assert archive.prune(NOW, {"isr", "kao"}) == 2
    rows = archive.db.execute("SELECT connection_id, key FROM messages ORDER BY id").fetchall()
    assert rows == [(old, "isr"), (recent, "isr"), (recent, "zzz"), (recent, "yyy")]
    assert archive.prune(NOW, {"isr", "kao"}) == 0  # rien de plus au passage suivant
    # Une session ouverte il y a longtemps mais encore dans le délai n'est pas touchée.
    assert archive.prune(NOW, {"isr"}, keep_days=KEEP_DAYS) == 0 and archive.count() == 4
    assert archive.prune(NOW, {"isr"}, keep_days=1) == 2 and archive.count() == 2
    archive.close()


def test_old_price_snapshots_are_thinned_to_one_per_day():
    conn = db.connect(":memory:")
    ids = {}
    # Trois relevés il y a 40 jours, deux il y a 39 jours, deux il y a 10 jours ; chacun a un contenu différent.
    for label, age_days, hour in (("a", 40, 1), ("b", 40, 9), ("c", 40, 20), ("d", 39, 3), ("e", 39, 4), ("f", 10, 5), ("g", 10, 6)):
        ts = (100 - age_days) * DAY + hour * 3600
        ids[label] = db.save_snapshot(conn, ts, {1: 100 + len(ids), 2: 50})
    assert db.thin_snapshots(conn, NOW) == 3
    kept = [row[0] for row in conn.execute("SELECT id FROM snapshots ORDER BY ts")]
    assert kept == [ids["c"], ids["e"], ids["f"], ids["g"]]  # le dernier de chaque vieux jour, tout ce qui est récent
    assert conn.execute("SELECT COUNT(*) FROM avg_prices").fetchone()[0] == 2 * len(kept)
    assert db.thin_snapshots(conn, NOW) == 0
    conn.close()
