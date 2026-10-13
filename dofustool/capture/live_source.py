"""Source live : écoute passive via Npcap. N'envoie jamais rien sur le réseau."""
import queue
import time
from collections.abc import Iterable, Iterator

from scapy.all import AsyncSniffer, conf

from . import GAME_PORT, Segment
from .packet import LINKTYPE_ETHERNET, parse_tcp


def usable(interfaces) -> list:
    """Cartes réseau par lesquelles le jeu peut passer : celles qui ont une adresse, sauf la boucle locale.

    Une carte débranchée est gardée : elle peut se connecter en cours de session (Wi-Fi, VPN)."""
    return [
        card for card in interfaces
        if getattr(card, "ip", None) and "LOOPBACK" not in str(getattr(card, "flags", "")).upper()
    ]  # fmt: skip


START_CHECK_S = 1.5  # temps laissé à l'écoute pour échouer si une carte ne s'ouvre pas


class LiveSource:
    """Sans carte imposée, toutes les cartes utilisables sont écoutées à la fois : la carte « par défaut » de
    Windows n'est pas toujours celle du jeu (nouveau matériel, VPN, passage du Wi-Fi au câble), et écouter la
    mauvaise ne se voit pas. Un paquet vu sur deux cartes n'est compté qu'une fois : le réassemblage TCP
    écarte les doublons."""

    def __init__(self, ports: Iterable[int] = (GAME_PORT,), iface: str | None = None):
        self.ports = frozenset(ports)
        if iface:
            resolved = [conf.ifaces.dev_from_name(iface)]
        else:
            resolved = usable(conf.ifaces.values()) or [conf.iface]
        self.iface = ", ".join(getattr(card, "name", None) or str(card) for card in resolved)
        self._queue: queue.Queue[Segment | None] = queue.Queue()
        self._several = len(resolved) > 1
        self._sniffer = self._listen(resolved if self._several else resolved[0])

    def _listen(self, cards) -> AsyncSniffer:
        return AsyncSniffer(
            iface=cards,
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
        if not self._several:
            return
        # Une carte qui refuse de s'ouvrir arrête toute l'écoute : on se replie alors sur la carte par défaut.
        time.sleep(START_CHECK_S)
        thread = getattr(self._sniffer, "thread", None)
        if thread is not None and not thread.is_alive():
            self._several = False
            self.iface = getattr(conf.iface, "name", None) or str(conf.iface)
            self._sniffer = self._listen(conf.iface)
            self._sniffer.start()

    def stop(self) -> None:
        if self._sniffer.running:
            self._sniffer.stop()
        self._queue.put(None)

    def poll(self, timeout: float) -> Segment | None:
        """Renvoie le prochain segment, ou None si rien n'arrive dans le délai."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def __iter__(self) -> Iterator[Segment]:
        """Itère jusqu'à l'appel de stop()."""
        if not self._sniffer.running:
            self.start()
        while (segment := self._queue.get()) is not None:
            yield segment
