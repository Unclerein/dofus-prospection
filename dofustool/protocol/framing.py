"""Découpage d'un flux en frames préfixées par leur longueur (varint, hors en-tête)."""
from .wire import WireError, read_varint

# Garde-fou : une longueur annoncée au-delà signale un flux désynchronisé, pas une vraie frame.
MAX_FRAME = 16 * 1024 * 1024


class FramingError(ValueError):
    pass


class Deframer:
    def __init__(self) -> None:
        self._buf = bytearray()
        self.frames = 0
        self.consumed = 0

    def push(self, data: bytes) -> list[bytes]:
        self._buf += data
        frames = []
        pos = 0
        while pos < len(self._buf):
            try:
                size, body = read_varint(self._buf, pos)
            except WireError:
                if len(self._buf) - pos >= 10:
                    raise FramingError("en-tête de longueur invalide") from None
                break  # en-tête incomplet : attendre la suite
            if size > MAX_FRAME:
                raise FramingError(f"longueur de frame invraisemblable : {size}")
            if body + size > len(self._buf):
                break
            frames.append(bytes(self._buf[body : body + size]))
            pos = body + size
        del self._buf[:pos]
        self.frames += len(frames)
        self.consumed += pos
        return frames

    @property
    def leftover(self) -> int:
        """Octets en attente qui ne forment pas (encore) une frame complète."""
        return len(self._buf)
