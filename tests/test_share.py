"""Partage des relevés de marché entre joueurs : enregistrements, hub, synchronisation."""
import json
import threading
import time

import pytest

from dofustool import config, db, identify, share
from dofustool.messages.hdv_listings import HdvListings, Listing
from dofustool.share import client
from dofustool.share.hub import Hub, member_of, serve

NOW = time.time()
PRICES = {1000 + i: 50 + i for i in range(600)}
BUILD = {"key": "isr", "fields": {"entries": 3, "item_id": 1, "price": 2}}


def market_db():
    conn = db.connect(":memory:")
    db.save_hdv_listings(conn, HdvListings(289, (Listing(7, (12, 110, 1050, 0), ()),)), NOW - 60)
    ring = (Listing(8, (900_000, 0, 0, 0), ((125, 449), (111, None))), Listing(9, (950_000, 0, 0, 0), ((125, 430),)))
    db.save_hdv_listings(conn, HdvListings(500, ring), NOW - 50)
    hour = int(NOW) // 3600 * 3600
    db.save_market_history(conn, 289, db.GRAIN_HOUR, [(hour - 3600, 9, 100), (hour, 10, 40)], NOW - 40)
    db.save_market_history(conn, 289, db.GRAIN_DAY, [(hour // 86400 * 86400, 9, 5000)], NOW - 40)
    db.save_last_sale(conn, 289, 10, NOW - 120, NOW - 40)
    db.save_snapshot(conn, NOW - 30, PRICES)
    return conn


def test_collect_then_apply_reproduces_the_market_data():
    source, target = market_db(), db.connect(":memory:")
    records = share.collect(source, 0)
    assert sorted(r["kind"] for r in records) == ["hdv", "hdv", "market", "prices"]
    assert all(share.validate(r) for r in records)
    json.dumps(records)
    for record in records:
        assert share.apply(target, record)
    for query in (
        "SELECT item_id, uid, p1, p10, p100, p1000, effects, captured_at FROM hdv_current ORDER BY uid",
        "SELECT item_id, period, bucket_ts, price, qty_sold, captured_at FROM market_history ORDER BY period, bucket_ts",
        "SELECT item_id, price, sold_at FROM last_sales",
        "SELECT content_hash FROM snapshots",
    ):
        assert target.execute(query).fetchall() == source.execute(query).fetchall() != []
    assert db.snapshot_prices(target, db.latest_snapshot(target)[0]) == PRICES
    # Rien d'ancien n'est renvoyé, et rien de personnel ne l'est jamais.
    assert share.collect(source, NOW + 3600) == []
    assert not any("holdings" in json.dumps(r) or "my_sales" in json.dumps(r) for r in records)

    # Réappliquer est sans effet ; un relevé plus ancien ne remplace pas le plus récent.
    older = {**records[0], "at": NOW - 5000, "data": {"item_id": 289, "listings": [[7, 99, 0, 0, 0, []]]}}
    assert share.apply(target, older) and share.apply(target, records[0])
    assert target.execute("SELECT p1 FROM hdv_current WHERE item_id = 289").fetchall() == [(12,)]


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "stock"},
        {"at": NOW + 86400},
        {"at": "hier"},
        {"key": "290"},
        {"data": {"item_id": 289, "listings": []}},
        {"data": {"item_id": 289, "listings": [[7, -1, 0, 0, 0, []]]}},
        {"data": {"item_id": 289, "listings": [[7, 0, 0, 0, 0, []]]}},
        {"data": {"item_id": 289, "listings": [[7, 12, 0, 0, 0, [["vita", 4]]]]}},
        {"data": {"item_id": 289, "listings": [[7, 12, 0, 0, 0, "x"]]}},
        {"data": {"item_id": 289}},
        {"data": "x"},
    ],
)
def test_validate_rejects_anything_unexpected(change):
    good = {"kind": "hdv", "key": "289", "at": NOW, "data": {"item_id": 289, "listings": [[7, 12, 110, 0, 0, [[125, 40]]]]}}
    assert share.validate(good)
    assert not share.validate({**good, **change})
    assert not share.apply(db.connect(":memory:"), {**good, **change})
    assert not share.validate({"kind": "prices", "key": "h", "at": NOW, "data": {"prices": [[1, 5]]}})  # trop court pour un relevé
    assert not share.validate({"kind": "market", "key": "289", "at": NOW, "data": {"item_id": 289, "hour": [[NOW, -3, 1]], "day": []}})
    assert not share.validate(None) and not share.validate([])


def test_hub_relays_between_members_and_keeps_the_latest():
    hub = Hub(":memory:")
    records = share.collect(market_db(), 0)
    first = hub.sync("alice", 0, records)
    assert (first["accepted"], first["rejected"], first["records"]) == (4, 0, [])  # personne d'autre n'a rien envoyé
    assert hub.sync("alice", first["cursor"], records)["accepted"] == 0  # renvoyé une seconde fois : sans effet

    got = hub.sync("bob", 0, [])
    assert sorted(r["kind"] for r in got["records"]) == ["hdv", "hdv", "market", "prices"] and not got["more"]
    assert [m["pseudo"] for m in got["members"]] == ["alice", "bob"] and got["members"][0]["pushed"] == 4
    assert hub.sync("bob", got["cursor"], [])["records"] == []

    # Bob relève le Blé plus tard : son relevé remplace celui d'Alice, qui le reçoit ; l'inverse est refusé.
    newer = {"kind": "hdv", "key": "289", "at": NOW, "data": {"item_id": 289, "listings": [[7, 11, 100, 0, 0, []]]}}
    stale = {**newer, "at": NOW - 999, "data": {"item_id": 289, "listings": [[7, 99, 0, 0, 0, []]]}}
    assert hub.sync("bob", got["cursor"], [newer, stale, {"kind": "hdv"}]) | {"members": [], "cursor": 0} == {
        "cursor": 0, "records": [], "more": False, "accepted": 1, "rejected": 1, "members": [],
    }  # fmt: skip
    back = hub.sync("alice", first["cursor"], [])["records"]
    assert [r["data"]["listings"][0][1] for r in back] == [11]

    assert hub.purge("bob") == 1 and [m["pseudo"] for m in hub.sync("alice", 0, [])["members"]] == ["alice"]
    hub.close()


def test_hub_forgets_old_records_and_merges_keymaps():
    hub = Hub(":memory:")
    old = {"kind": "hdv", "key": "289", "at": NOW - 4 * 86400, "data": {"item_id": 289, "listings": [[7, 11, 0, 0, 0, []]]}}
    hub.sync("alice", 0, [old])
    assert hub.sync("bob", 0, [])["records"] == []  # trop vieux pour être distribué

    partial = {"key": "jzs", "fields": {"item_id": 3, "effects": 0}, "partial": True}
    full = {"key": "jzs", "fields": {"item_id": 3, "effects": 5}}
    signature = share.keymap_signature(BUILD)
    alice = {"kind": "keymap", "key": signature, "at": NOW - 100, "data": {"entries": {"avg_prices": BUILD, "hdv_listings": partial}}}
    bob = {"kind": "keymap", "key": signature, "at": NOW - 200, "data": {"entries": {"avg_prices": BUILD, "hdv_listings": full, "bank": {"key": "irp", "fields": {"kamas": 1}}}}}
    hub.sync("alice", 0, [alice])
    assert hub.sync("bob", 0, [bob])["accepted"] == 1
    merged = hub.sync("carol", 0, [])["records"][-1]["data"]["entries"]
    assert merged["hdv_listings"] == full and merged["bank"]["key"] == "irp" and merged["avg_prices"] == BUILD
    assert hub.sync("alice", 0, [alice])["accepted"] == 0  # n'apporte plus rien
    hub.close()


def test_a_shared_keymap_only_fills_what_is_missing_on_the_same_build(tmp_path):
    path = tmp_path / "keymap.json"
    mine = {"avg_prices": BUILD, "bank": {"key": "old", "fields": {"kamas": 9}, "stale": True}, "inventory": {"key": "irl", "fields": {"kamas": 4}}}
    path.write_text(json.dumps(mine), encoding="utf-8")
    theirs = {"avg_prices": BUILD, "bank": {"key": "irp", "fields": {"kamas": 1}}, "inventory": {"key": "zzz", "fields": {"kamas": 7}}}
    conn = db.connect(":memory:")

    other_build = {"kind": "keymap", "key": "abc:entries=1", "at": NOW, "data": {"entries": theirs}}
    assert share.apply(conn, other_build, path) and identify.read_keymap(path) == mine  # autre build : ignoré

    record = {"kind": "keymap", "key": share.keymap_signature(BUILD), "at": NOW, "data": {"entries": theirs}}
    assert share.apply(conn, record, path)
    raw = identify.read_keymap(path)
    assert raw["bank"] == {"key": "irp", "fields": {"kamas": 1}}  # retrouvé par un ami
    assert raw["inventory"] == mine["inventory"]  # ce qu'on connaît déjà n'est jamais remplacé

    sent = share.collect(conn, 0, path)
    assert [r["kind"] for r in sent] == ["keymap"] and sent[0]["key"] == share.keymap_signature(BUILD)
    assert share.validate(sent[0]) and "stale" not in json.dumps(sent[0])


# --- bout en bout, par le réseau local ------------------------------------------


@pytest.fixture
def hub_url(tmp_path):
    path = tmp_path / "hub.toml"
    host = config.from_values({**BASE, "share_members": {"alice": "jeton-alice-0123456789", "bob": "jeton-bob-0123456789xx"}})
    config.save(host, path)
    server = serve(0, Hub(":memory:"), path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


BASE = {
    "server_name": "Kourial", "hdv_tax": 0.02, "last_sale_max_age_hours": 24, "min_snapshots_for_trend": 5,
    "min_liquidity": 0, "trend_threshold": 0.15, "avg_prices_timeout_s": 60, "dofus_process": "Dofus",
}  # fmt: skip


def member(url, token, server="Kourial"):
    return config.from_values({**BASE, "server_name": server, "share_hub_url": url, "share_token": token, "share_pseudo": "x"})


def test_two_players_exchange_their_readings(hub_url):
    alice, bob = market_db(), db.connect(":memory:")
    db.save_hdv_listings(bob, HdvListings(3, (Listing(1, (300, 2500, 0, 0), ()),)), NOW - 10)

    assert client.sync_once(alice, member(hub_url, "jeton-alice-0123456789")) == {"sent": 4, "received": 0}
    assert client.sync_once(bob, member(hub_url, "jeton-bob-0123456789xx")) == {"sent": 1, "received": 4}
    assert client.sync_once(alice, member(hub_url, "jeton-alice-0123456789")) == {"sent": 0, "received": 1}
    for conn in (alice, bob):
        assert [row[0] for row in conn.execute("SELECT DISTINCT item_id FROM hdv_current ORDER BY 1")] == [3, 289, 500]
    assert len(db.snapshot_prices(bob, db.latest_snapshot(bob)[0])) == len(PRICES)
    # Rien ne tourne en rond : ce qu'on a reçu n'est pas renvoyé comme une nouveauté.
    assert client.sync_once(bob, member(hub_url, "jeton-bob-0123456789xx")) == {"sent": 0, "received": 0}

    state = client.status(alice, member(hub_url, "jeton-alice-0123456789"))
    assert state["enabled"] and state["sent"] == 4 and state["received"] == 1 and state["last_error"] is None
    assert [m["pseudo"] for m in state["members"]] == ["alice", "bob"]


def test_hub_refuses_unknown_tokens_and_other_servers(hub_url):
    conn = market_db()
    with pytest.raises(ConnectionError, match="jeton inconnu"):
        client.sync_once(conn, member(hub_url, "mauvais-jeton-0123456789"))
    with pytest.raises(ConnectionError, match="Kourial"):
        client.sync_once(conn, member(hub_url, "jeton-alice-0123456789", server="Draconiros"))
    with pytest.raises(ConnectionError, match="injoignable"):
        client.sync_once(conn, member("http://127.0.0.1:9", "jeton-alice-0123456789"))
    assert member_of("jeton-alice-0123456789", {"alice": "jeton-alice-0123456789"}) == "alice"
    assert member_of("", {"alice": ""}) is None and not client.enabled(config.Config())


def test_share_settings_round_trip(tmp_path):
    values = {**BASE, "share_pseudo": "Lou", "share_hub_url": "http://100.64.0.1:8610/", "share_token": "abcdefghijklmnopqrst",
              "share_host": True, "share_port": 8611, "share_members": {"Tom": "0123456789abcdef"}, "onboarded": True}  # fmt: skip
    cfg = config.from_values(values)
    assert cfg.share_hub_url == "http://100.64.0.1:8610" and cfg.share_members == {"Tom": "0123456789abcdef"}
    config.save(cfg, tmp_path / "c.toml")
    assert config.load(tmp_path / "c.toml") == cfg and cfg.onboarded and cfg.share_port == 8611
    for bad in ({"share_hub_url": "ftp://x"}, {"share_token": "a b"}, {"share_members": {"Tom": "court"}}, {"share_members": []}, {"share_port": 80}):
        with pytest.raises(ValueError):
            config.from_values({**values, **bad})
