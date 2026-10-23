from dofustool import db, identify
from dofustool.messages import breeding, load_keymap

from .test_protocol import ld, varint, vi

KEYMAP = load_keymap()
STABLE, PADDOCK, UPDATE = KEYMAP["stable"], KEYMAP["paddock"], KEYMAP["paddock_update"]
NOW = 2_000_000.0


def uid(n: int) -> bytes:
    return f"{n:08x}-0000-4000-8000-000000000000".encode()


def signed(n: int) -> bytes:
    return varint(n & 0xFFFFFFFFFFFFFFFF)


def mount(n: int, st=None, *, male=False, color=10, parents=(), serenity=0, love=0, maturity=0, stamina=0, level=1, xp=0, sterile=False) -> bytes:
    """Une monture telle que le jeu l'envoie : une jauge à 0 n'envoie pas sa valeur, l'amour n'envoie pas son type."""
    f = st or STABLE.fields
    data = ld(1, vi(5, 159) + vi(10, 125))  # un bonus : ignoré
    if male:
        data += vi(f["male"], 1)
    if parents:
        data += ld(f["parents"], b"".join(vi(i + 1, c) for i, c in enumerate(parents)))
    data += varint(f["serenity"] << 3) + signed(serenity)
    for kind, value in ((2, stamina), (1, maturity), (0, love)):
        data += ld(f["gauges"], (vi(f["gauge_type"], kind) if kind else b"") + (vi(f["gauge_value"], value) if value else b""))
    data += vi(f["level"], level) + (vi(f["sterile"], 1) if sterile else b"") + vi(f["color"], color) + (vi(f["xp"], xp) if xp else b"")
    return ld(f["uid"], uid(n)) + ld(f["data"], data)


HERD = [
    dict(male=True, color=38, parents=(10, 20), serenity=-120, love=20000, maturity=20000, stamina=20000, level=53, xp=40000),
    dict(color=18, serenity=-4788),  # bébé : niveau 1, jauges vides
    dict(male=True, color=10, serenity=4855, love=30),
    dict(color=20, serenity=650, love=20000, maturity=20000, stamina=20000, level=53, xp=40000, sterile=True),
    dict(color=46, parents=(18, 10), serenity=-865, love=20000, maturity=20000, stamina=20000, level=57, xp=47030),
]


def herd_of(st, herd=None) -> list[bytes]:
    return [mount(n, st, **kw) for n, kw in enumerate(HERD if herd is None else herd, 1)]


def stable_body(herd=None, st=None, extra=3) -> bytes:
    """L'étable ; extra : numéro du petit bloc envoyé à côté des montures."""
    st = st or STABLE.fields
    return ld(st["wrap"], b"".join(ld(st["mounts"], m) for m in herd_of(st, herd)) + ld(extra, vi(1, 1) + vi(2, 10)))


def content(f, st, active=(0, 4), objects=(24470, 30000, 9210, 20000, 39980, 0), herd=HERD[:2]) -> bytes:
    body = ld(f["active"], bytes(active)) if active else b""
    for index, points in enumerate(objects):
        body += ld(f["objects"], (vi(f["object_value"], points) if points else b"") + (vi(f["object_index"], index) if index else b""))
    return body + b"".join(ld(f["mounts"], m) for m in herd_of(st, herd))


def paddock_body(paddock_id=1, f=None, st=None, **kw) -> bytes:
    f = f or PADDOCK.fields
    return ld(f["wrap"], vi(f["paddock_id"], paddock_id) + ld(f["content"], content(f, st or STABLE.fields, **kw)))


def update_body(f=None, st=None, **kw) -> bytes:
    f = f or UPDATE.fields
    return ld(f["wrap"], content(f, st or STABLE.fields, **kw))


def test_stable_reads_each_mount():
    herd = breeding.parse_stable(stable_body(), STABLE)
    assert len(herd) == 5
    adult, baby, young, sterile, crossed = herd
    assert adult.male and adult.color == 38 and adult.parents == (10, 20) and adult.serenity == -120 and adult.fecund
    assert (adult.love, adult.maturity, adult.stamina, adult.level, adult.xp) == (20000, 20000, 20000, 53, 40000)
    assert not baby.male and baby.level == 1 and baby.xp == 0 and baby.serenity == -4788 and not baby.fecund
    assert young.love == 30 and young.maturity == 0 and young.stamina == 0
    assert sterile.sterile and not sterile.fecund
    assert crossed.parents == (18, 10)


def test_paddock_and_its_updates():
    paddock = breeding.parse_paddock(paddock_body(), PADDOCK, STABLE)
    assert paddock.paddock_id == 1 and paddock.active == (0, 4) and paddock.objects == (24470, 30000, 9210, 20000, 39980, 0)
    assert len(paddock.mounts) == 2
    idle = breeding.parse_paddock(paddock_body(active=()), PADDOCK, STABLE)
    assert idle.active == ()  # rien en marche : le champ est absent
    update = breeding.parse_paddock_update(update_body(active=(1,)), UPDATE, STABLE)
    assert update.paddock_id is None and update.active == (1,)
    assert breeding.parse_paddock(paddock_body(), PADDOCK, None) is None  # sans la table de l'étable, rien n'est lu
    assert breeding.parse_paddock(update_body(), PADDOCK, STABLE) is None and breeding.parse_stable(paddock_body(), STABLE) is None


def test_mounts_are_saved_by_place():
    c = db.connect(":memory:")
    db.save_stable(c, breeding.parse_stable(stable_body(), STABLE), NOW)
    paddock = breeding.parse_paddock(paddock_body(herd=HERD[2:3]), PADDOCK, STABLE)
    db.save_paddock(c, 1, paddock, NOW + 5)
    assert c.execute("SELECT place, COUNT(*) FROM mounts GROUP BY place").fetchall() == [(0, 4), (1, 1)]  # la DD n° 3 est sortie de l'étable
    assert c.execute("SELECT objects, active FROM paddocks").fetchone() == ("[24470, 30000, 9210, 20000, 39980, 0]", "[0, 4]")
    db.save_stable(c, breeding.parse_stable(stable_body(HERD[:1]), STABLE), NOW + 10)  # les autres ont quitté l'étable
    assert c.execute("SELECT COUNT(*) FROM mounts WHERE place = 0").fetchone() == (1,)


def test_breeding_messages_are_found_again_after_a_game_update():
    ctx = identify.Context(set(), set(), set(), {}, rides={10, 18, 20, 38, 46})
    found = identify.match_stable(stable_body(), ctx)
    assert found == (STABLE.fields, False)
    assert identify.match_paddock(paddock_body(), STABLE) == (PADDOCK.fields, False)
    assert identify.match_paddock_update(update_body(), STABLE) == (UPDATE.fields, False)
    idle = identify.match_paddock(paddock_body(active=()), STABLE)
    assert idle[1] and idle[0]["active"] == 0  # rien en marche : champ inconnu, à retrouver
    no_sterile = identify.match_stable(stable_body(HERD[:3] + HERD[4:]), ctx)
    assert no_sterile[1] and no_sterile[0]["sterile"] == 0
    assert identify.match_stable(paddock_body(), ctx) is None and identify.match_paddock(stable_body(), STABLE) is None
