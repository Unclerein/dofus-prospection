"""Lecteur protobuf minimal, sans schéma."""
from collections.abc import Iterator

VARINT = 0
I64 = 1
LEN = 2
I32 = 5


class WireError(ValueError):
    pass


def read_varint(buf: bytes | memoryview, pos: int) -> tuple[int, int]:
    """Renvoie (valeur, nouvelle position). Lève WireError si le varint est tronqué ou trop long."""
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise WireError("varint tronqué")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift >= 70:
            raise WireError("varint trop long")


def to_signed(value: int) -> int:
    """Interprète un varint 64 bits comme un entier signé (int32/int64 négatifs)."""
    return value - (1 << 64) if value >= 1 << 63 else value


def iter_fields(buf: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Itère (numéro de champ, type de fil, valeur). Lève WireError si buf n'est pas du protobuf valide."""
    pos, end = 0, len(buf)
    while pos < end:
        tag, pos = read_varint(buf, pos)
        number, wire_type = tag >> 3, tag & 7
        if number == 0:
            raise WireError("numéro de champ nul")
        if wire_type == VARINT:
            value, pos = read_varint(buf, pos)
        elif wire_type == LEN:
            length, pos = read_varint(buf, pos)
            if pos + length > end:
                raise WireError("champ LEN tronqué")
            value = buf[pos : pos + length]
            pos += length
        elif wire_type in (I64, I32):
            size = 8 if wire_type == I64 else 4
            if pos + size > end:
                raise WireError("champ fixe tronqué")
            value = int.from_bytes(buf[pos : pos + size], "little")
            pos += size
        else:
            raise WireError(f"type de fil {wire_type} non géré")
        yield number, wire_type, value
