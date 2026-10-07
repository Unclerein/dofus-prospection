"""Allègement de l'archive brute : seuls les vieux messages non décodés partent. Données synthétiques uniquement."""
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
