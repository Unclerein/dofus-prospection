"""Source hors ligne : relit un fichier .pcap / .pcapng."""
from collections.abc import Iterable, Iterator
from pathlib import Path

from scapy.utils import RawPcapNgReader, RawPcapReader

from . import GAME_PORT, Segment
from .packet import parse_tcp


class PcapSource:
    def __init__(self, path: str | Path, ports: Iterable[int] = (GAME_PORT,)):
        self.path = Path(path)
        self.ports = frozenset(ports)

    def __iter__(self) -> Iterator[Segment]:
        with RawPcapReader(str(self.path)) as reader:
            is_ng = isinstance(reader, RawPcapNgReader)
            for data, meta in reader:
                if is_ng:
                    linktype = meta.linktype
                    ts = ((meta.tshigh << 32) | meta.tslow) / meta.tsresol
                else:
                    linktype = reader.linktype
                    ts = meta.sec + meta.usec / (1e9 if reader.nano else 1e6)
                segment = parse_tcp(data, linktype, ts, self.ports)
                if segment is not None:
                    yield segment
