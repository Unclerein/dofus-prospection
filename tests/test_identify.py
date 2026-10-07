"""Identification automatique des messages après une mise à jour du jeu. Données synthétiques uniquement."""
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from dofustool import db, identify
from dofustool.analysis.jobxp import level_to_xp
from dofustool.archive import Archive
from dofustool.capture import Segment
from dofustool.capture.pipeline import Pipeline
from dofustool.messages import Mapping, avg_prices, load_keymap, sales
from dofustool.messages.sales import Sale, SalesList
from dofustool.protocol.session import Message
from dofustool.protocol.tcp import C2S, S2C
from dofustool.web.api import Api

from .test_app import app_db  # noqa: F401  (fixture : une base complète sur disque)
from .test_pipeline import any_frame, feed
from .test_protocol import REQUEST, any_, framed, ld, varint, vi

ITEMS = set(range(1000, 4000))
JOBS = {2, 11, 13, 15, 16, 24, 26, 27, 28, 36, 41, 44, 48, 60, 62, 63, 64, 65, 74, 79}
EFFECTS = {111, 112, 115, 119, 123, 124, 125, 138, 174, 176}
CTX = identify.Context(ITEMS, JOBS, EFFECTS, {i: 500 for i in ITEMS})
N = avg_prices.MIN_ENTRIES + 100
T0 = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
CHOSEN = 52_000_000_001

# Deux jeux de numéros de champ, comme deux builds successifs.
BUILDS = [
    {
        "avg_prices": {"entries": 1, "item_id": 3, "price": 5},
        "inventory": {"kamas": 1, "entries": 2, "position": 3, "object": 5, "quantity": 2, "item_id": 5},
        "job_levels": {"entries": 2, "job_id": 1, "xp": 2, "level": 5},
        "character_list": {"entries": 1, "info": 2, "id": 3, "level": 4, "name": 5},
        "market_history": {"hourly": 1, "daily": 2, "quantity": 1, "date": 2, "price": 3, "item_id": 4},
        "hdv_listings": {"item_id": 1, "entries": 2, "effects": 1, "entry_item": 2, "uid": 5, "prices": 6, "effect_id": 1, "effect_value": 10},
        "my_sales": {"entries": 2, "ref": 1, "uid": 1, "item_id": 2, "lot": 3, "price": 2, "remaining": 3},
        "info_text": {"id": 2, "params": 4},
        "my_sale_update": {"price": 1, "ref": 2, "remaining": 3, "item_id": 1, "uid": 3, "lot": 4},
    },
    {
        "avg_prices": {"entries": 3, "item_id": 1, "price": 2},
        "inventory": {"kamas": 4, "entries": 2, "position": 4, "object": 1, "quantity": 3, "item_id": 4},
        "job_levels": {"entries": 1, "job_id": 5, "xp": 2, "level": 1},
        "character_list": {"entries": 2, "info": 4, "id": 3, "level": 3, "name": 6},
        "market_history": {"hourly": 1, "daily": 3, "quantity": 4, "date": 3, "price": 1, "item_id": 2},
        "hdv_listings": {"item_id": 3, "entries": 1, "effects": 5, "entry_item": 1, "uid": 3, "prices": 2, "effect_id": 10, "effect_value": 5},
        "my_sales": {"entries": 4, "ref": 3, "uid": 2, "item_id": 3, "lot": 1, "price": 5, "remaining": 1},
        "info_text": {"id": 1, "params": 3},
        "my_sale_update": {"price": 2, "ref": 1, "remaining": 4, "item_id": 2, "uid": 1, "lot": 3},
    },
]


def free(fields: dict, count: int = 1) -> list[int]:
    """Numéros de champ inutilisés par ce message, pour ses champs annexes."""
    return [n for n in range(11, 30) if n not in fields.values()][:count]


def avg_body(f) -> bytes:
    return b"".join(ld(f["entries"], vi(f["item_id"], 1000 + i) + vi(f["price"], 495 + i % 7)) for i in range(N))


def storage_body(f, stacks=40, kamas=1500, equipped=3) -> bytes:
    uid_field, effects_field = free(f, 2)
    out = vi(f["kamas"], kamas)
    for i in range(stacks):
        obj = vi(uid_field, 2_000_000 + i * 17) + vi(f["quantity"], 1 + i % 5) + vi(f["item_id"], 1000 + i * 3)
        obj += ld(effects_field, vi(1, 125) + vi(2, 40))
        out += ld(f["entries"], vi(f["position"], 63 if i >= equipped else i) + ld(f["object"], obj))
    return out


def jobs_body(f) -> bytes:
    floor_field, next_field = free(f, 2)
    out = b""
    for i, job_id in enumerate(sorted(JOBS)):
        level = [1, 3, 40, 103, 200][i % 5]
        xp = level_to_xp(level) + (0 if level == 1 else 37)
        entry = vi(f["job_id"], job_id) + (vi(f["xp"], xp) if xp else b"") + vi(f["level"], level)
        entry += (vi(floor_field, level_to_xp(level)) if level > 1 else b"") + vi(next_field, level_to_xp(min(level + 1, 200)))
        out += ld(f["entries"], entry)
    return out


def characters_body(f, ids=(CHOSEN, 21_000_000_002, 599_982_435)) -> bytes:
    (look_field,) = free(f)
    out = b""
    for i, character_id in enumerate(ids):
        info = ld(look_field, vi(1, 3) + ld(2, vi(4, 7))) + vi(f["level"], 200 + i) + ld(f["name"], f"Perso-{i}".encode())
        out += ld(f["entries"], ld(f["info"], info) + vi(f["id"], character_id))
    return out


def market_body(f, item_id=1500, price=500) -> bytes:
    def point(when, p, q):
        return vi(f["quantity"], q) + ld(f["date"], when.isoformat().encode()) + vi(f["price"], p) + vi(f["item_id"], item_id)

    hourly = b"".join(ld(f["hourly"], point(T0 - timedelta(hours=k), price + k, 3 + k)) for k in range(6))
    daily = b"".join(ld(f["daily"], point(T0 - timedelta(days=k), price - 5 + k % 11, 40 + k)) for k in range(30))
    return hourly + daily


def hdv_body(f, item_id=1500, equipment=False) -> bytes:
    (type_field,) = free(f)
    out = vi(f["item_id"], item_id) + vi(type_field, 104)
    for k in range(3 if equipment else 1):
        prices = (900_000 + k, 0, 0, 0) if equipment else (12, 110, 1050, 0)
        entry = vi(f["entry_item"], item_id) + ld(f["prices"], b"".join(varint(p) for p in prices)) + vi(f["uid"], 59_000 + k)
        entry += vi(type_field, 104)
        if equipment:
            for effect_id, value in ((125, 449 - k), (119, 39), (112, 5)):
                entry += ld(f["effects"], vi(f["effect_id"], effect_id) + vi(f["effect_value"], value))
            entry += ld(f["effects"], vi(f["effect_id"], 111) + ld(free(f, 2)[1], vi(1, 53) + vi(3, 45)))  # ligne sans valeur simple
        out += ld(f["entries"], entry)
    return out


def sales_body(f, lots=12, market_types=(262, 266, 15, 152)) -> bytes:
    descriptor = next(n for n in range(6, 30) if n != f["entries"])
    out = ld(descriptor, vi(2, 200) + ld(4, b"".join(varint(t) for t in market_types)) + ld(8, b"".join(varint(s) for s in (1, 10, 100, 1000))))
    for k in range(lots):
        lot = (1, 10, 100)[k % 3]
        ref = vi(f["uid"], 6_000_000 + k) + vi(f["item_id"], 1000 + k) + vi(f["lot"], lot)
        out += ld(f["entries"], ld(f["ref"], ref) + vi(f["price"], 480 * lot + k) + vi(f["remaining"], 2_000_000 - k * 1000))
    return out


def text_body(f, ident: int, *params) -> bytes:
    """Message d'information : un numéro de texte et ses paramètres, écrits en chiffres ou non."""
    return vi(f["id"], ident) + b"".join(ld(f["params"], str(p).encode()) for p in params)


def sale_update_body(f, uid=6_100_000, item=1500, lot=10, price=4_900, equipment=False) -> bytes:
    ref = vi(f["item_id"], item) + vi(f["uid"], uid) + vi(f["lot"], lot)
    if equipment:  # un lot d'équipement porte aussi ses effets, jamais lus
        ref += ld(max(f["item_id"], f["uid"], f["lot"]) + 3, vi(1, 125) + ld(2, b"Nom-De-Joueur"))
    return vi(f["price"], price) + ld(f["ref"], ref) + vi(f["remaining"], 2_419_200)


BODIES = {
    "avg_prices": avg_body, "inventory": storage_body, "job_levels": jobs_body, "character_list": characters_body,
    "market_history": market_body, "my_sales": sales_body,
}  # fmt: skip


@pytest.mark.parametrize("build", BUILDS)
def test_each_matcher_finds_the_field_numbers_of_the_build(build):
    assert identify.match_avg_prices(avg_body(build["avg_prices"]), CTX) == build["avg_prices"]
    assert identify.match_storage(storage_body(build["inventory"]), CTX) == build["inventory"]
    assert identify.match_job_levels(jobs_body(build["job_levels"]), CTX) == build["job_levels"]
    assert identify.match_character_list(characters_body(build["character_list"]), CHOSEN) == build["character_list"]
    assert identify.match_market_history(market_body(build["market_history"]), CTX) == build["market_history"]
    assert identify.match_my_sales(sales_body(build["my_sales"]), CTX) == build["my_sales"]

    f = build["hdv_listings"]
    assert identify.match_hdv_listings(hdv_body(f, equipment=True), CTX) == (f, False)
    partial = {**f, "effects": 0, "effect_id": 0, "effect_value": 0}
    assert identify.match_hdv_listings(hdv_body(f), CTX) == (partial, True)  # une ressource ne dit rien des effets


def test_matchers_refuse_what_only_looks_alike():
    build = BUILDS[0]
    assert identify.match_character_list(characters_body(build["character_list"]), 123_456_789) is None  # personnage absent
    assert identify.match_storage(storage_body(build["inventory"]), identify.Context(set(), JOBS, EFFECTS, {})) is None
    wrong_price = identify.Context(ITEMS, JOBS, EFFECTS, {i: 10**9 for i in ITEMS})
    assert identify.match_market_history(market_body(build["market_history"]), wrong_price) is None  # ne redonne pas le prix moyen
    assert identify.match_my_sales(sales_body(build["my_sales"]), wrong_price) is None
    for matcher in (identify.match_avg_prices, identify.match_storage, identify.match_job_levels, identify.match_market_history, identify.match_my_sales):
        for name, body in BODIES.items():
            if not matcher.__name__.endswith(name.replace("inventory", "storage")):
                assert matcher(body(build[name]), CTX) is None, (matcher.__name__, name)
        assert matcher(b"", CTX) is None and matcher(b"\xff\xff", CTX) is None


def archive_with(build, keys, bank_after=600.0, with_equipment=True) -> tuple[Archive, int]:
    """Une connexion complète : chaque message sous une clé du « nouveau build »."""
    archive = Archive(":memory:")
    conn = archive.open_connection(1000.0, 40000, "192.0.2.1", "test")

    def add(ts, direction, name, body):
        archive.add(conn, Message(0, ts, direction, keys[name], body, 2, None))

    add(1001.0, S2C, "character_list", characters_body(build["character_list"]))
    add(1002.0, C2S, "character_select", vi(1, CHOSEN))
    add(1003.0, S2C, "inventory", storage_body(build["inventory"]))
    add(1003.5, S2C, "job_levels", jobs_body(build["job_levels"]))
    add(1004.0, S2C, "avg_prices", avg_body(build["avg_prices"]))
    add(1000.0 + bank_after, S2C, "bank", storage_body(build["inventory"], stacks=90, equipped=0))
    add(1700.0, S2C, "market_history", market_body(build["market_history"]))
    add(1800.0, S2C, "hdv_listings", hdv_body(build["hdv_listings"]))
    if with_equipment:
        add(1810.0, S2C, "hdv_listings", hdv_body(build["hdv_listings"], equipment=True))
    add(1900.0, S2C, "my_sales", sales_body(build["my_sales"]))
    add(1905.0, S2C, "my_sale_update", sale_update_body(build["my_sale_update"]))
    add(1906.0, S2C, "my_sale_update", sale_update_body(build["my_sale_update"], uid=6_100_001, equipment=True))
    text = build["info_text"]
    for at, body in enumerate((
        text_body(text, 89), text_body(text, 65, 4_800, 1500, 1500, 10), text_body(text, 252, 1501, 3_602_459, 100, 47_000),
        text_body(text, 36, "Un-Nom"), text_body(text, 193, 2026, 10, 7, 18, 56), text_body(text, 21, 1, 23_726),
    )):
        add(1910.0 + at, S2C, "info_text", body)
    # Du bruit : d'autres requêtes d'un seul entier, et des messages sans rapport.
    archive.add(conn, Message(0, 1950.0, C2S, "zz1", vi(1, 207_619_076), 1, 4))
    archive.add(conn, Message(0, 1951.0, S2C, "zz2", vi(1, 5) + ld(2, b"bonjour"), 2, None))
    return archive, conn


KEYS = {name: f"k{index:02d}" for index, name in enumerate(identify.NAMES)}


@pytest.mark.parametrize("build", BUILDS)
def test_identify_a_whole_connection(build):
    archive, conn = archive_with(build, KEYS)
    scan = identify.Scan(1000.0)
    scan.feed(archive.db, conn)
    found, problems = identify.identify(scan, CTX)
    assert problems == {} and set(found) == set(identify.NAMES)
    assert {name: item.key for name, item in found.items()} == KEYS
    assert found["inventory"].fields == found["bank"].fields == build["inventory"]
    assert found["character_select"].fields == {"id": 1}
    assert found["hdv_listings"].fields == build["hdv_listings"] and not found["hdv_listings"].partial


def test_identify_is_incremental_and_reports_ambiguity():
    build = BUILDS[1]
    archive, conn = archive_with(build, KEYS, with_equipment=False)
    scan = identify.Scan(1000.0)
    scan.feed(archive.db, conn)
    found, _ = identify.identify(scan, CTX, ("hdv_listings", "bank"))
    assert set(found) == {"hdv_listings", "bank"} and found["hdv_listings"].partial

    # Un équipement arrive ensuite : la même clé est relue, cette fois avec ses effets.
    archive.add(conn, Message(0, 2000.0, S2C, KEYS["hdv_listings"], hdv_body(build["hdv_listings"], equipment=True), 2, None))
    # Et un second message qui a la forme d'une banque : impossible de trancher.
    archive.add(conn, Message(0, 2001.0, S2C, "dup", storage_body(build["inventory"], stacks=50, equipped=0), 2, None))
    scan.feed(archive.db, conn)
    found, problems = identify.identify(scan, CTX, ("hdv_listings", "bank"))
    assert found["hdv_listings"].fields == build["hdv_listings"] and not found["hdv_listings"].partial
    assert "bank" not in found and "dup" in problems["bank"]


def write_keymap(path, build, keys, stale=()):
    raw = {}
    for name in identify.NAMES:
        source = "inventory" if name == "bank" else name
        fields = {"id": 1} if name == "character_select" else build[source]
        raw[name] = {"key": keys[name], "fields": fields, **({"stale": True} if name in stale else {})}
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_keymap_pending_changes_and_stale_entries(tmp_path):
    path = tmp_path / "keymap.json"
    old_keys = {name: f"o{index:02d}" for index, name in enumerate(identify.NAMES)}
    write_keymap(path, BUILDS[0], old_keys)
    raw = identify.read_keymap(path)
    assert identify.pending(raw) == set()
    del raw["my_sales"]
    raw["bank"]["stale"] = True
    raw["hdv_listings"]["partial"] = True
    assert identify.pending(raw) == {"my_sales", "bank", "hdv_listings"}

    prices = identify.Found("avg_prices", "new", BUILDS[1]["avg_prices"])
    same = identify.Found("inventory", old_keys["inventory"], BUILDS[0]["inventory"])
    partial = identify.Found("market_history", old_keys["market_history"], BUILDS[0]["market_history"], partial=True)
    assert set(identify.changes(identify.read_keymap(path), {"avg_prices": prices, "inventory": same, "market_history": partial})) == {"avg_prices"}

    identify.write_keymap(path, {"avg_prices": prices, "inventory": same}, new_build=True, backup_dir=tmp_path / "backups")
    assert len(list((tmp_path / "backups").glob("keymap-*.json"))) == 1
    raw = identify.read_keymap(path)
    assert raw["avg_prices"] == {"key": "new", "fields": BUILDS[1]["avg_prices"]}
    assert "stale" not in raw["inventory"] and raw["bank"]["stale"] and raw["job_levels"]["stale"]
    assert set(load_keymap(path)) == {"avg_prices", "inventory"}  # une clé périmée n'est plus utilisée
    assert identify.pending(raw) == set(identify.NAMES) - {"avg_prices", "inventory"}


def test_pipeline_recovers_alone_from_a_game_update(tmp_path, caplog):
    """Les clés et les numéros de champ changent : la capture les retrouve, sans alerte ni relance."""
    old_keys = {name: f"o{index:02d}" for index, name in enumerate(identify.NAMES)}
    path = tmp_path / "keymap.json"
    write_keymap(path, BUILDS[0], old_keys)
    build = BUILDS[1]

    market = db.connect(":memory:")
    market.executemany("INSERT INTO items VALUES (?, ?, 1, 'Ressource diverse', 1, 1, 0, 2)", [(i, f"item {i}") for i in sorted(ITEMS)])
    market.executemany("INSERT INTO jobs VALUES (?, ?)", [(j, f"métier {j}") for j in JOBS])
    pipeline = Pipeline(Archive(":memory:"), market, load_keymap(path), avg_prices_timeout_s=60.0, keymap_path=path)
    pipeline.backup_dir = tmp_path / "backups"

    select = framed(ld(1, ld(1, any_(KEYS["character_select"], vi(1, CHOSEN))) + vi(2, 3)))
    listing = framed(any_frame(KEYS["character_list"], characters_body(build["character_list"])))
    login = (
        framed(any_frame(KEYS["inventory"], storage_body(build["inventory"])))
        + framed(any_frame(KEYS["job_levels"], jobs_body(build["job_levels"])))
        + framed(any_frame(KEYS["avg_prices"], avg_body(build["avg_prices"])))
    )
    caplog.set_level(logging.INFO, logger="dofustool.capture")
    # Comme en jeu : la liste des personnages, puis le choix du client, puis le reste.
    feed(pipeline, listing)
    pipeline.handle(Segment(1000.5, "10.0.0.2", 40001, "192.0.2.2", 5555, 101 + len(framed(REQUEST)), 0, select))
    pipeline.handle(Segment(1000.6, "192.0.2.2", 5555, "10.0.0.2", 40001, 501 + len(listing), 0, login))
    pipeline.tick(1001.0)
    assert db.latest_snapshot(market) is None  # anciennes clés : rien n'est décodé, tout est archivé

    # Le flux reste actif ; au bout du délai, la capture cherche avant d'alerter.
    seq = 501 + len(listing) + len(login)
    ping = framed(any_frame("zzz", vi(1, 1)))
    pipeline.handle(Segment(1065.0, "192.0.2.2", 5555, "10.0.0.2", 40001, seq, 0, ping))
    pipeline.tick(1070.0)
    messages = [r.getMessage() for r in caplog.records]
    assert "Mise à jour du jeu détectée : les clés des messages ont changé." in messages
    assert not any("ALERTE" in m for m in messages) and db.get_status(market).get("decode_alert", "") == ""
    assert len(db.snapshot_prices(market, db.latest_snapshot(market)[0])) == N
    assert market.execute("SELECT stacks FROM holdings_meta WHERE container = ?", (db.INVENTORY,)).fetchone() == (40,)
    assert len(db.character_jobs(market, CHOSEN)) == len(JOBS)
    raw = identify.read_keymap(path)
    assert raw["avg_prices"]["key"] == KEYS["avg_prices"] and raw["job_levels"]["fields"] == build["job_levels"]
    assert identify.pending(raw) == {"bank", "market_history", "hdv_listings", "my_sales", "info_text", "my_sale_update"}  # pas encore vus sur ce build

    # Plus tard le joueur ouvre l'onglet Vendre : le message est retrouvé et décodé à la vérification suivante.
    seq += len(ping)
    selling = framed(any_frame(KEYS["my_sales"], sales_body(build["my_sales"])))
    pipeline.handle(Segment(1200.0, "192.0.2.2", 5555, "10.0.0.2", 40001, seq, 0, selling))
    pipeline.tick(1201.0)
    assert market.execute("SELECT COUNT(*), MIN(lot), MAX(lot) FROM my_sales").fetchone() == (12, 1, 100)
    assert "my_sales" not in identify.pending(identify.read_keymap(path))
    assert "Message retrouvé : my_sales (clé k08)." in [r.getMessage() for r in caplog.records]
    market.close()


def test_pipeline_keeps_alerting_when_nothing_can_be_identified(tmp_path, caplog):
    path = tmp_path / "keymap.json"
    write_keymap(path, BUILDS[0], KEYS)
    market = db.connect(":memory:")
    pipeline = Pipeline(Archive(":memory:"), market, load_keymap(path), avg_prices_timeout_s=60.0, keymap_path=path)
    pipeline.backup_dir = tmp_path / "backups"
    ping = framed(any_frame("zzz", vi(1, 1)))
    feed(pipeline, ping)
    pipeline.handle(Segment(1065.0, "192.0.2.2", 5555, "10.0.0.2", 40001, 501 + len(ping), 0, ping))
    pipeline.tick(1070.0)
    assert [r.levelno for r in caplog.records].count(logging.WARNING) == 1  # écran de choix du personnage, ou vraie panne
    assert identify.read_keymap(path) == json.loads(path.read_text(encoding="utf-8")) and not (tmp_path / "backups").exists()
    market.close()


# --- mes ventes ----------------------------------------------------------------

SALES = Mapping("sal", BUILDS[0]["my_sales"])


def test_sales_parse_and_market_fingerprint():
    listing = sales.parse(sales_body(SALES.fields, lots=3), SALES)
    assert listing.sales == (
        Sale(6_000_000, 1000, 1, 480, 2_000_000),
        Sale(6_000_001, 1001, 10, 4801, 1_999_000),
        Sale(6_000_002, 1002, 100, 48002, 1_998_000),
    )
    other = sales.parse(sales_body(SALES.fields, lots=1, market_types=(2, 3, 4)), SALES)
    assert other.market != listing.market and listing.market == sales.parse(sales_body(SALES.fields, lots=8), SALES).market
    empty = sales.parse(sales_body(SALES.fields, lots=0), SALES)
    assert empty == SalesList(listing.market, ())  # onglet Vendre ouvert sans aucun lot : la liste est vide, pas inconnue
    assert sales.parse(b"", SALES) is None and sales.parse(vi(1, 5), SALES) is None
    bad_lot = ld(2, ld(1, vi(1, 7) + vi(2, 1000) + vi(3, 7)) + vi(2, 100) + vi(3, 5))
    assert sales.parse(bad_lot, SALES) is None


def test_sales_are_stored_per_market_and_served(app_db):  # noqa: F811
    conn = db.connect(app_db)
    now = __import__("time").time()
    first = SalesList(11, (Sale(1, 1, 100, 900, 86_400), Sale(2, 3, 1, 250, 20 * 86_400), Sale(3, 500, 1, 5_000_000, 86_400), Sale(4, 1, 1, 12, 5000)))
    assert db.save_sales(conn, first, now - 60)
    assert db.save_sales(conn, SalesList(22, (Sale(9, 2, 1, 77, 5000),)), now - 30)  # un autre HDV : aucun objet en commun
    assert not db.save_sales(conn, SalesList(11, ()), now - 3600)  # relevé plus ancien : ignoré
    assert conn.execute("SELECT COUNT(*) FROM my_sales").fetchone()[0] == 5
    # Le Blé (item 1) se vend 8 les 100 à l'HDV : mon lot à 900 est sous-enchéri. Le Pain n'est pas relevé.
    conn.execute("INSERT INTO hdv_listings VALUES (1, 77, 12, 0, 800, 0, '[]', ?, ?)", (now, now))
    conn.commit()
    conn.close()

    data = Api(app_db).sales()
    json.dumps(data, allow_nan=False)
    assert data["markets"] == 2 and len(data["rows"]) == 5
    wheat = data["rows"][0]  # les lots sous-enchéris viennent en premier
    assert (wheat["name"], wheat["lot"], wheat["hdv"], wheat["undercut"], wheat["unit"]) == ("Blé", 100, 800, 100, 9.0)
    by_uid_price = {row["price"]: row for row in data["rows"]}
    assert by_uid_price[12]["hdv"] == 12 and by_uid_price[12]["undercut"] == 0  # au prix le plus bas
    assert by_uid_price[250]["hdv"] is None and by_uid_price[5_000_000]["equipment"] and by_uid_price[5_000_000]["hdv"] is None
    assert 86_300 < wheat["remaining_s"] <= 86_400 - 59

    conn = db.connect(app_db)
    assert db.save_sales(conn, SalesList(11, ()), now)  # tout vendu ou retiré dans cet HDV
    conn.close()
    assert [row["price"] for row in Api(app_db).sales()["rows"]] == [77]


def test_a_relisted_market_replaces_its_old_fingerprint():
    conn = db.connect(":memory:")
    db.save_sales(conn, SalesList(11, (Sale(1, 1000, 1, 50, 9000), Sale(2, 1001, 10, 500, 9000))), 100.0)
    db.save_sales(conn, SalesList(22, (Sale(9, 1002, 1, 70, 9000),)), 110.0)
    # Après une mise à jour du jeu, le même HDV arrive sous une autre empreinte et ses lots sont renumérotés.
    db.save_sales(conn, SalesList(33, (Sale(70, 1001, 10, 500, 8000), Sale(71, 1003, 1, 80, 9000))), 200.0)
    assert conn.execute("SELECT market, uid FROM my_sales ORDER BY market, uid").fetchall() == [(22, 9), (33, 70), (33, 71)]
    assert [row[0] for row in conn.execute("SELECT market FROM my_sales_meta ORDER BY market")] == [22, 33]
    conn.close()
