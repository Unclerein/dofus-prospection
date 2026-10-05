"""Extraction du segment TCP d'une trame brute, sans passer par la dissection scapy (lente)."""
import ipaddress
import struct

from . import Segment

LINKTYPE_NULL = 0
LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101

_ETH_IPV4 = 0x0800
_ETH_IPV6 = 0x86DD
_ETH_VLAN = (0x8100, 0x88A8)
_TCP = 6
# En-têtes d'extension IPv6 de longueur (n + 1) * 8 octets.
_IPV6_EXT = (0, 43, 60)


def parse_tcp(data: bytes, linktype: int, ts: float, ports: frozenset[int]) -> Segment | None:
    """Renvoie le segment si la trame est du TCP dont un des ports est dans `ports`."""
    try:
        if linktype == LINKTYPE_ETHERNET:
            off = 12
            (ethertype,) = struct.unpack_from("!H", data, off)
            while ethertype in _ETH_VLAN:
                off += 4
                (ethertype,) = struct.unpack_from("!H", data, off)
            off += 2
            if ethertype not in (_ETH_IPV4, _ETH_IPV6):
                return None
        elif linktype == LINKTYPE_NULL:
            off = 4
        elif linktype == LINKTYPE_RAW:
            off = 0
        else:
            return None

        version = data[off] >> 4
        if version == 4:
            ihl = (data[off] & 0x0F) * 4
            total_len, _, frag, _, proto = struct.unpack_from("!HHHBB", data, off + 2)
            if proto != _TCP or frag & 0x3FFF:  # fragments IP non gérés
                return None
            src, dst = data[off + 12 : off + 16], data[off + 16 : off + 20]
            tcp_off = off + ihl
            end = off + total_len
        elif version == 6:
            payload_len, proto = struct.unpack_from("!HB", data, off + 4)
            src, dst = data[off + 8 : off + 24], data[off + 24 : off + 40]
            end = off + 40 + payload_len
            tcp_off = off + 40
            while proto in _IPV6_EXT:
                proto, ext_len = data[tcp_off], data[tcp_off + 1]
                tcp_off += (ext_len + 1) * 8
            if proto != _TCP:
                return None
        else:
            return None

        sport, dport, seq, _, offs_flags = struct.unpack_from("!HHIIH", data, tcp_off)
        if sport not in ports and dport not in ports:
            return None
        data_off = tcp_off + (offs_flags >> 12) * 4
        # `end` écarte le bourrage Ethernet ; min() tolère une trame tronquée par le snaplen.
        payload = bytes(data[data_off : min(end, len(data))])
        return Segment(
            ts=ts,
            src=str(ipaddress.ip_address(bytes(src))),
            sport=sport,
            dst=str(ipaddress.ip_address(bytes(dst))),
            dport=dport,
            seq=seq,
            flags=offs_flags & 0x1FF,
            payload=payload,
        )
    except (struct.error, IndexError, ValueError):
        return None
