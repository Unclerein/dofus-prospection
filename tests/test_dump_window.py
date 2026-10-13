"""Extrait d'une tranche horaire : structure et nombres seulement, jamais de texte."""
import sqlite3
import struct

from dofustool.archive import Archive
from dofustool.tools import dump_window

from .test_protocol import ld, vi

SECRET = "Bonjour à toute la guilde"


def test_describe_shows_numbers_and_hides_text():
    body = vi(1, 57) + ld(2, vi(1, 3) + vi(3, 250)) + ld(4, SECRET.encode()) + bytes([0x2D]) + struct.pack("<f", 12.5)
    fields = dump_window.describe(body)
    assert fields == ["1=57", "2.1=3", "2.3=250", f"4=LEN{len(SECRET.encode())}", "5=f12.5"]
    assert SECRET not in " ".join(fields) and "Bonjour" not in " ".join(fields)
    assert dump_window.describe(b"\\xff\\xff\\xff") is None  # pas un message lisible
    assert dump_window.looks_like_text(b"Rune de chasse") and not dump_window.looks_like_text(vi(1, 300) + vi(2, 5))


def test_dump_keeps_the_window_and_drops_the_handshake(tmp_path):
    path = tmp_path / "archive.sqlite"
    archive = Archive(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO connections (id, started_at, client_port, server, source) VALUES (1, 1000.0, 5, 'x', 'test')")
    rows = [
        (1005.0, "s2c", "aaa", vi(1, 1)),  # début de connexion : écarté
        (1100.0, "c2s", "bbb", vi(2, 42)),
        (1101.5, "s2c", "ccc", ld(1, SECRET.encode()) + vi(3, 7)),
        (1300.0, "s2c", "ddd", vi(1, 9)),  # hors de la tranche
    ]
    conn.executemany(
        "INSERT INTO messages (connection_id, ts, direction, key, size, frame_field, correlation, body) VALUES (1, ?, ?, ?, ?, 1, NULL, ?)",
        [(ts, direction, key, len(body), body) for ts, direction, key, body in rows],
    )
    conn.commit()
    lines, skipped = dump_window.dump(conn, 1000.0, 1200.0)
    conn.close()
    archive.close() if hasattr(archive, "close") else None
    assert skipped == 1
    assert lines == ["  100.000 c2s bbb 2 | 2=42", f"  101.500 s2c ccc {len(SECRET.encode()) + 4} | 1=LEN{len(SECRET.encode())} 3=7"]
    assert SECRET not in "\n".join(lines)


def test_cell_lists_are_shown_and_nothing_else_is():
    cells = bytes([0xE0, 0x02, 0xEE, 0x02, 0x7C])  # 352, 366, 124 en liste compacte
    assert dump_window.small_numbers(cells) == [352, 366, 124]
    assert dump_window.describe(ld(1, cells) + vi(4, 7)) == ["1=[352,366,124]", "4=7"]
    for text in ("12:30", "é ", "ab", SECRET):  # du texte, même court ou sans lettres : jamais en nombres
        assert dump_window.small_numbers(text.encode()) is None
    assert dump_window.small_numbers(bytes([0xFF, 0xFF, 0x03])) is None  # nombre trop grand
    assert dump_window.small_numbers(bytes([0xE0, 0x02, 0xEE])) is None  # liste coupée
    assert dump_window.small_numbers(bytes(range(200, 232))) is None  # octets quelconques
