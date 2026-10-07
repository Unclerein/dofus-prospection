import importlib

import pytest


@pytest.mark.parametrize("module", ["scapy.all", "google.protobuf", "blackboxprotobuf", "pandas"])
def test_dependency_imports(module):
    importlib.import_module(module)


def test_blackboxprotobuf_decodes_without_schema():
    import blackboxprotobuf

    # champ 1 varint = 150, champ 2 varint = 7
    message, _ = blackboxprotobuf.decode_message(b"\x08\x96\x01\x10\x07")
    assert message == {"1": 150, "2": 7}


def test_scapy_finds_npcap():
    from scapy.all import conf

    assert conf.use_pcap, "Npcap introuvable par scapy (mode « WinPcap API-compatible » ?)"
