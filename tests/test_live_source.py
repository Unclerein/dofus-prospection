"""Choix des cartes réseau écoutées par la capture, sans ouvrir aucune carte."""
from types import SimpleNamespace

from dofustool.capture.live_source import usable


def card(name, ip="10.0.0.2", flags="UP+RUNNING+OK"):
    return SimpleNamespace(name=name, ip=ip, flags=flags)


def test_every_card_with_an_address_is_listened_to_except_loopback():
    cards = [
        card("Ethernet"),
        card("Wi-Fi 3", flags="UP+RUNNING+WIRELESS+DISCONNECTED"),  # débranchée maintenant, peut-être pas tout à l'heure
        card("NordLynx", flags="UP+RUNNING+DISCONNECTED"),
        card("Loopback Pseudo-Interface 1", ip="127.0.0.1", flags="LOOPBACK+UP+RUNNING+OK"),
        card("WAN Miniport (IP)", ip=None),
        card("Sans adresse", ip=""),
    ]
    assert [c.name for c in usable(cards)] == ["Ethernet", "Wi-Fi 3", "NordLynx"]
    assert usable([]) == []
