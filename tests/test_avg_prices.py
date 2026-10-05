from pathlib import Path

import pytest

from dofustool.messages import Mapping, avg_prices, load_keymap
from dofustool.protocol.schema import Schema, render
from dofustool.staticdata import source

from .test_protocol import ld, vi

FIXTURE = Path(__file__).parent / "fixtures" / "avg_prices.bin"
MAPPING = Mapping("xxx", {"entries": 1, "item_id": 3, "price": 5})


def synthetic(n: int) -> bytes:
    return b"".join(ld(1, vi(3, 100 + i) + vi(5, 10 * (i + 1))) for i in range(n))


def test_parse_synthetic():
    body = synthetic(avg_prices.MIN_ENTRIES) + ld(1, vi(3, 9))  # dernière entrée sans prix
    prices = avg_prices.parse(body, MAPPING)
    assert len(prices) == avg_prices.MIN_ENTRIES
    assert prices[100] == 10 and 9 not in prices


@pytest.mark.parametrize(
    "body",
    [
        synthetic(10),  # trop court pour être le catalogue
        synthetic(avg_prices.MIN_ENTRIES) + ld(2, b""),  # champ de premier niveau inattendu
        synthetic(avg_prices.MIN_ENTRIES) + ld(1, vi(3, 1) + ld(4, b"x")),  # entrée avec une chaîne
        synthetic(avg_prices.MIN_ENTRIES) + ld(1, vi(5, 1)),  # entrée sans identifiant
        synthetic(avg_prices.MIN_ENTRIES)[:-1],  # tronqué
    ],
)
def test_parse_rejects_unexpected_shapes(body):
    assert avg_prices.parse(body, MAPPING) is None


def test_fixture_parses_with_current_keymap():
    prices = avg_prices.parse(FIXTURE.read_bytes(), load_keymap()["avg_prices"])
    assert len(prices) == 8767
    assert prices[289] == 10  # Blé
    assert prices[2469] == 394524  # Gelano
    assert all(p > 0 for p in prices.values())


@pytest.mark.skipif(not source.DOFUS_SQLITE.exists(), reason="data/static/dofus.sqlite absent")
def test_fixture_ids_are_known_items():
    prices = avg_prices.parse(FIXTURE.read_bytes(), load_keymap()["avg_prices"])
    conn = source.connect()
    known = {r[0] for r in conn.execute("SELECT id FROM ItemData UNION ALL SELECT id FROM WeaponData")}
    conn.close()
    assert len(prices.keys() & known) / len(prices) >= 0.9


# --- schéma -----------------------------------------------------------------

PROTO = """
syntax = "proto3";

message abc {
  repeated ent aaaa = 1;
  string bbbb = 2;
  kind cccc = 3;
  map<int32, string> dddd = 4;
  repeated int32 eeee = 5;
  message ent {
    int32 ffff = 3;
    optional int64 gggg = 5;
  }
}

message kind {
}
"""


def test_schema_describe_hides_strings_and_keeps_numbers():
    schema = Schema.parse(PROTO)
    body = (
        ld(1, vi(3, 7) + vi(5, (1 << 64) - 2))
        + ld(2, b"secret")
        + vi(3, 4)
        + ld(4, vi(1, 9) + ld(2, "caché".encode()))
        + ld(5, b"\x01\x02\x03")
    )
    described, mismatches = schema.describe("abc", body)
    assert mismatches == 0
    assert described == {1: [{3: 7, 5: -2}], 2: "<string 6 o>", 3: 4, 4: [{1: 9, 2: "<string 6 o>"}], 5: [1, 2, 3]}
    assert "secret" not in render(described)


def test_schema_describe_flags_mismatches_without_decoding():
    schema = Schema.parse(PROTO)
    described, mismatches = schema.describe("abc", ld(9, b"texte") + vi(2, 5))
    assert mismatches == 2
    assert "texte" not in render(described)
    assert schema.describe("zzz", b"abc") == ("<pas de schéma, 3 o>", 1)
