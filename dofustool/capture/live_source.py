"""Source live : écoute passive via Npcap. N'envoie jamais rien sur le réseau."""
import queue
from collections.abc import Iterable, Iterator

from scapy.all import AsyncSniffer, conf

from . import GAME_PORT, Segment
from .packet import LINKTYPE_ETHERNET, parse_tcp


class LiveSource:
    def __init__(self, ports: Iterable[int] = (GAME_PORT,), iface: str | None = None):
        self.ports = frozenset(ports)
        self.iface = iface or conf.iface
        self._queue: queue.Queue[Segment | None] = queue.Queue()
        self._sniffer = AsyncSniffer(
            iface=self.iface,
            filter=" or ".join(f"tcp port {p}" for p in sorted(self.ports)),
            store=False,
            prn=self._on_packet,
        )

    def _on_packet(self, pkt) -> None:
        segment = parse_tcp(bytes(pkt), LINKTYPE_ETHERNET, float(pkt.time), self.ports)
        if segment is not None:
            self._queue.put(segment)

    def start(self) -> None:
        self._sniffer.start()

    def stop(self) -> None:
        if self._sniffer.running:
            self._sniffer.stop()
        self._queue.put(None)

    def __iter__(self) -> Iterator[Segment]:
        """Itère jusqu'à l'appel de stop()."""
        if not self._sniffer.running:
            self.start()
        while (segment := self._queue.get()) is not None:
            yield segment
