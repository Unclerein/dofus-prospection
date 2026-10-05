from dofustool.archive import Archive
from dofustool.messages import Mapping, avg_prices, load_keymap
from dofustool.protocol.session import Message
from dofustool.protocol.tcp import C2S, S2C
from dofustool.tools.reidentify import find_candidates, match_avg_prices

from .test_avg_prices import FIXTURE
from .test_protocol import ld, vi

N = avg_prices.MIN_ENTRIES + 200


def rotated(entries_field: int, id_field: int, price_field: int, with_price_gap: bool = True) -> bytes:
    """Liste de prix avec d'autres numéros de champ, comme après une mise à jour du jeu."""
    out = []
    for i in range(N):
        entry = vi(id_field, 1000 + i)
        if not (with_price_gap and i % 50 == 0):  # quelques items sans prix
            entry += vi(price_field, 10 + i % 7)
        out.append(ld(entries_field, entry))
    return b"".join(out)


KNOWN = set(range(1000, 1000 + N))


def test_match_finds_rotated_field_numbers():
    body = rotated(4, 9, 2)
    candidate = match_avg_prices("zzz", body, KNOWN)
    assert candidate.fields == {"entries": 4, "item_id": 9, "price": 2}
    assert candidate.entries == N and candidate.known_ratio == 1.0 and candidate.confident
    # Le parseur accepte le corps avec le mapping proposé.
    prices = avg_prices.parse(body, Mapping(candidate.key, candidate.fields))
    assert len(prices) == N - N // 50 and prices[1001] == 11


def test_match_distinguishes_ids_from_prices_when_both_are_unique():
    body = b"".join(ld(1, vi(2, 500_000 + i) + vi(7, 1000 + i)) for i in range(N))  # prix tous différents
    assert match_avg_prices("zzz", body, KNOWN).fields == {"entries": 1, "item_id": 7, "price": 2}


def test_match_rejects_other_shapes():
    assert match_avg_prices("a", rotated(1, 3, 5)[:500], KNOWN) is None  # trop peu d'entrées
    assert match_avg_prices("a", rotated(1, 3, 5) + ld(1, ld(4, b"x")), KNOWN) is None  # une chaîne dans une entrée
    assert match_avg_prices("a", rotated(1, 3, 5) + ld(2, vi(3, 1)), KNOWN) is None  # deux champs de premier niveau
    three = b"".join(ld(1, vi(1, i) + vi(2, 5) + vi(3, 6)) for i in range(N))
    assert match_avg_prices("a", three, KNOWN) is None  # trois entiers par entrée
    assert match_avg_prices("a", b"\xff\xff", KNOWN) is None
    unknown = match_avg_prices("a", rotated(1, 3, 5), {1, 2, 3})
    assert unknown.known_ratio == 0 and not unknown.confident
    assert match_avg_prices("a", rotated(1, 3, 5), set()).known_ratio is None


def test_real_fixture_is_identified_with_current_field_numbers():
    mapping = load_keymap()["avg_prices"]
    candidate = match_avg_prices(mapping.key, FIXTURE.read_bytes(), set())
    assert candidate.fields == mapping.fields and candidate.entries == 8924


def test_find_candidates_in_archive():
    archive = Archive(":memory:")
    conn_id = archive.open_connection(1.0, 40000, "192.0.2.1", "test")
    body = rotated(4, 9, 2)
    archive.add(conn_id, Message(0, 100.0, S2C, "new", body, 2, None))
    archive.add(conn_id, Message(0, 101.0, S2C, "big", b"\x0a\x03abc" * 2000, 2, None))  # gros, autre forme
    archive.add(conn_id, Message(0, 102.0, C2S, "up", body, 1, 1))  # mauvais sens
    archive.add(conn_id, Message(0, 50.0, S2C, "old", rotated(1, 3, 5), 2, None))  # avant la mise à jour
    archive.commit()
    assert {c.key for c in find_candidates(archive._db, KNOWN)} == {"new", "old"}
    assert [c.key for c in find_candidates(archive._db, KNOWN, since=90.0)] == ["new"]
