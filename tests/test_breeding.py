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


def herd_of(st, herd=None, first=1) -> list[bytes]:
    return [mount(n, st, **kw) for n, kw in enumerate(HERD if herd is None else herd, first)]


def stable_body(herd=None, st=None, extra=3) -> bytes:
    """L'étable ; extra : numéro du petit bloc envoyé à côté des montures."""
    st = st or STABLE.fields
    return ld(st["wrap"], b"".join(ld(st["mounts"], m) for m in herd_of(st, herd)) + ld(extra, vi(1, 1) + vi(2, 10)))


def content(f, st, active=(0, 4), objects=(24470, 30000, 9210, 20000, 39980, 0), herd=HERD[:2], first=1) -> bytes:
    body = ld(f["active"], bytes(active)) if active else b""
    for index, points in enumerate(objects):
        body += ld(f["objects"], (vi(f["object_value"], points) if points else b"") + (vi(f["object_index"], index) if index else b""))
    return body + b"".join(ld(f["mounts"], m) for m in herd_of(st, herd, first))


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
    assert c.execute("SELECT place, COUNT(*) FROM mounts GROUP BY place").fetchall() == [(db.STABLE, 4), (1, 1)]  # la DD n° 3 est sortie de l'étable
    assert c.execute("SELECT objects, active FROM paddocks").fetchone() == ("[24470, 30000, 9210, 20000, 39980, 0]", "[0, 4]")
    db.save_stable(c, breeding.parse_stable(stable_body(HERD[:1]), STABLE), NOW + 10)  # les autres ont quitté l'étable
    assert c.execute("SELECT COUNT(*) FROM mounts WHERE place = ?", (db.STABLE,)).fetchone() == (1,)


def test_paddock_opened_in_person_is_recognized():
    # Ouvert sur place, l'enclos arrive sans son numéro : on le reconnaît parmi ceux vus à distance.
    f = PADDOCK.fields
    here = lambda **kw: ld(f["wrap"], ld(f["content"], content(f, STABLE.fields, **kw)))  # noqa: E731
    assert breeding.parse_paddock(here(), PADDOCK, STABLE).paddock_id is None
    c = db.connect(":memory:")
    assert sim.which_paddock(c, (0,) * 6, [], NOW) == (None, False)  # aucun enclos connu
    db.save_paddock(c, 1, breeding.parse_paddock(paddock_body(1, herd=HERD[:1]), PADDOCK, STABLE), NOW)
    db.save_paddock(c, 2, breeding.parse_paddock(paddock_body(2, herd=HERD[1:2], first=2, active=()), PADDOCK, STABLE), NOW)
    seen = breeding.parse_paddock(here(herd=HERD[1:2], first=2), PADDOCK, STABLE)
    assert sim.paddock_here(c, seen, NOW + 60, map_id=77) == 2  # ses montures y sont : sûr, sa carte est retenue
    seen = breeding.parse_paddock(here(herd=HERD[3:4], first=9, active=()), PADDOCK, STABLE)
    assert sim.which_paddock(c, seen.objects, [m.uid for m in seen.mounts], NOW + 3600) == (2, False)  # montures changées : ses objets, arrêtés
    assert sim.which_paddock(c, (0,) * 6, [], NOW, map_id=77) == (2, True)  # sur sa carte, plus besoin du reste
    assert sim.paddock_here(c, breeding.parse_paddock(paddock_body(1), PADDOCK, STABLE), NOW, map_id=77) == 1  # à distance : son numéro



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


# --- simulation de l'enclos ----------------------------------------------------

from dofustool.analysis import breeding as sim  # noqa: E402
from dofustool.overlay import Breeding, breeding_text  # noqa: E402


def herd(*serenities, love=0, maturity=0, stamina=20000):
    return [[s, love, maturity, stamina] for s in serenities]


def test_a_tick_moves_every_mount_and_spends_only_what_is_used():
    p = sim.Paddock(1, 0.0, [24470, 30000, 9210, 20000, 39980, 0], {sim.SLAPPER, sim.LOVER}, herd(4520, 4520, love=520))
    for _ in range(452):
        sim.step(p)
    assert p.mounts[0][sim.SERENITY] == 4520 - 4520 and p.mounts[0][sim.LOVE] == 520 + 4520  # sérénité du début du tick
    sim.step(p)  # sérénité à 0 : plus d'amour, et la dragofesse ne dépense plus rien
    assert p.mounts[0][sim.LOVE] == 5040 and p.objects[sim.LOVER] == 39980 - 4520
    assert p.objects[sim.SLAPPER] == 24470 - 4530
    capped = sim.Paddock(1, 0.0, [0, 100, 0, 0, 0, 0], {sim.PATTER}, herd(4995))
    sim.step(capped)
    sim.step(capped)
    assert capped.mounts[0][sim.SERENITY] == 5000 and capped.objects[sim.PATTER] == 90  # plafond : le second tick ne coûte rien
    assert sim.rate(80_000) == 10 and sim.rate(80_001) == 20 and sim.rate(200_000) == 40 and sim.rate(0) == 0


def test_target_zone_follows_the_gauges_left_to_fill():
    assert sim.target_zone(herd(0, love=5000, maturity=0)) == (1, 2000)
    assert sim.target_zone(herd(0, love=20000, maturity=0, stamina=0)) == (-2000, -1)
    assert sim.target_zone(herd(0, love=20000, maturity=20000)) is None
    # Amour et endurance ne montent jamais ensemble : l'enclos suit le côté où sont ses DD.
    assert sim.target_zone(herd(-3000, -1600, -5000, love=0, maturity=0, stamina=1300)) == (-2000, -1)
    assert sim.target_zone(herd(800, 4000, love=0, maturity=0, stamina=0)) == (1, 2000)
    assert sim.target_zone(herd(-200, love=0, maturity=20000, stamina=0)) == (-5000, -1)


def test_advice_waits_for_a_five_minute_window():
    spread = sim.Paddock(1, 1000.0, [50_000, 50_000, 0, 0, 0, 0], {sim.PATTER}, herd(0, 2500, love=5000))
    advice = sim.advise(spread)
    # Caresseur : la plus haute bute à 5 000 ; on bascule dès que la plus basse laisse 5 minutes dans la zone (3 310).
    assert advice.kind == "switch" and advice.then == sim.SLAPPER and advice.at == 1000.0 + 331 * sim.TICK_S
    after = sim.project(spread, advice.at)
    after.toggles = [(advice.at, sim.PATTER, False), (advice.at, sim.SLAPPER, True)]
    stop = sim.advise(after)
    assert stop.kind == "stop" and stop.title == "Coupe le baffeur"
    assert stop.at == advice.at + 300 * sim.TICK_S and stop.until - stop.at >= sim.MARGIN_S
    rising = sim.Paddock(1, 0.0, [0, 50_000, 0, 0, 0, 0], {sim.PATTER}, herd(-4940, -4940, love=5040))
    direct = sim.advise(rising)
    assert direct.kind == "stop" and direct.at == 495 * sim.TICK_S and direct.until == direct.at + 199 * sim.TICK_S
    assert sim.advise(sim.Paddock(1, 0.0, [0] * 6, set(), herd(0, love=5000))) is None  # ni baffeur ni caresseur


def test_clicks_after_the_last_view_are_replayed():
    p = sim.Paddock(1, 0.0, [0, 50_000, 0, 0, 0, 0], set(), herd(-100, love=5000), toggles=[(100.0, sim.PATTER, True)])
    advice = sim.advise(p)
    assert advice.kind == "stop" and advice.at == 110.0 + 10 * sim.TICK_S


def test_toggle_message_and_paddock_events_in_the_base():
    assert breeding.parse_toggle(b"", KEYMAP["paddock_start"]) == 0 and breeding.parse_toggle(vi(1, 4), KEYMAP["paddock_start"]) == 4
    assert breeding.parse_toggle(vi(2, 4), KEYMAP["paddock_start"]) is None and breeding.parse_toggle(vi(1, 9), KEYMAP["paddock_start"]) is None
    c = db.connect(":memory:")
    db.save_paddock(c, 1, breeding.parse_paddock(paddock_body(active=(1,), herd=[dict(serenity=-100, love=5000, stamina=20000)]), PADDOCK, STABLE), NOW)
    db.save_toggle(c, 1, NOW + 5, sim.PATTER, False)
    [p] = sim.load(c)
    assert p.active == {1} and p.toggles == [(NOW + 5, sim.PATTER, False)] and p.mounts == [[-100, 5000, 0, 20000]]
    summary = sim.summary(c, NOW + 60)
    assert summary["stable"]["count"] == 1 and summary["paddocks"][0]["hint"] == "Lance le caresseur."


def test_overlay_announces_each_gesture_once():
    c = db.connect(":memory:")
    db.save_paddock(c, 1, breeding.parse_paddock(paddock_body(active=(1,), herd=[dict(serenity=-4940, love=5040, stamina=20000)]), PADDOCK, STABLE), NOW)
    board = Breeding(c)
    assert board.due(NOW) == []
    due = board.due(NOW + 495 * sim.TICK_S)
    assert [a.title for a in due] == ["Coupe le caresseur"] and breeding_text(due[0]) == "1 DD entre 1 et 2 000"
    assert board.due(NOW + 500 * sim.TICK_S) == []  # déjà annoncé
    assert board.due(NOW + 900 * sim.TICK_S) == []  # trop tard : les DD sont sorties de la zone


def test_a_banner_goes_away_once_the_gesture_is_done():
    from dofustool.overlay import Board

    c = db.connect(":memory:")
    db.save_paddock(c, 1, breeding.parse_paddock(paddock_body(active=(1,), herd=[dict(serenity=-4940, love=5040, stamina=20000)]), PADDOCK, STABLE), NOW)
    watch, board = Breeding(c), Board()
    at = NOW + 495 * sim.TICK_S
    [advice] = watch.due(at)
    gesture = (advice.paddock_id, advice.kind, advice.running, advice.then)
    board.note("ÉLEVAGE · ENCLOS 1", advice.title, "", at, shown_s=300, kind="élevage", gesture=gesture)
    assert watch.done(at + 20) == []  # rien n'a bougé
    db.save_toggle(c, 1, at + 30, sim.PATTER, False)  # le caresseur est coupé
    assert watch.done(at + 35) == [gesture] and watch.done(at + 40) == []
    board.dismiss(gesture, at + 35)
    board.hold(at + 36)  # le jeu passe derrière : le bandeau effacé ne revient pas
    board.expire(at + 37)
    assert board.banners == []


def test_gauges_full_and_objects_empty_are_foreseen():
    p = sim.Paddock(1, 0.0, [0, 0, 0, 50, 5000, 0], {sim.TROUGH, sim.LOVER}, herd(1000, 1000, love=19_000, maturity=0))
    empty, full = sim.forecast(p)
    assert full == {sim.LOVER: 100 * sim.TICK_S}  # l'amour plein en 100 ticks ; la dragofesse s'arrête alors de dépenser
    assert empty == {sim.TROUGH: 5 * sim.TICK_S}  # l'abreuvoir n'avait que 50 points
    kinds = [(g.kind, g.title) for g in sim.gestures(p)]
    assert kinds == [("empty", "L’abreuvoir est vide"), ("full", "Coupe la dragofesse")]
    idle = sim.Paddock(1, 0.0, [0, 0, 0, 0, 5000, 0], {sim.LOVER}, herd(-500, love=0))
    assert sim.forecast(idle) == ({}, {})  # sérénité négative : l'amour ne monte pas, rien n'arrive


def test_overlay_announces_a_full_gauge_once_until_it_is_dealt_with():
    c = db.connect(":memory:")
    db.save_paddock(c, 1, breeding.parse_paddock(paddock_body(active=(4,), objects=(0, 0, 0, 0, 5000, 0),
                                                              herd=[dict(serenity=1000, love=19_000, stamina=20000)]), PADDOCK, STABLE), NOW)  # fmt: skip
    watch = Breeding(c)
    [full] = watch.due(NOW + 100 * sim.TICK_S)
    assert full.kind == "full" and breeding_text(full) == "Amour plein"
    assert watch.due(NOW + 3 * 3600) == []  # une seule fois tant qu'on n'y a pas touché
    db.save_toggle(c, 1, NOW + 3 * 3600 + 1, sim.LOVER, False)
    assert watch.done(NOW + 3 * 3600 + 5) == [(1, "full", sim.LOVER, None)]
    assert watch.due(NOW + 3 * 3600 + 6) == []  # coupée : le bandeau ne revient pas
    db.save_toggle(c, 1, NOW + 3 * 3600 + 60, sim.LOVER, True)  # relancée alors que l'amour est plein : on prévient encore
    assert [a.kind for a in watch.due(NOW + 3 * 3600 + 70)] == ["full"]


def test_mating_preview_and_birth_are_recorded():
    import struct

    from dofustool.tools import mating_export

    pair = ld(3, uid(1)) + ld(4, uid(4))
    assert breeding.parse_pair(pair, KEYMAP["mating_preview_request"]) == (uid(1).decode(), uid(4).decode())
    assert breeding.parse_pair(ld(3, uid(1)), KEYMAP["mating_preview_request"]) is None

    def outcome(color, chance, target=False):
        return ld(5, (vi(2, 1) if target else b"") + vi(4, color) + varint(5 << 3 | 5) + struct.pack("<f", chance))

    body = ld(2, vi(2, 1) + outcome(46, 38.1, True) + outcome(10, 30.96) + outcome(18, 30.94) + ld(7, vi(1, 8700) + vi(2, 60)) + vi(8, 4))
    preview = breeding.parse_preview(body, KEYMAP["mating_preview"])
    assert preview.outcomes == ((46, 38.1, True), (10, 30.96, False), (18, 30.94, False))
    assert preview.extra == {"2": 1, "7.1": 8700, "7.2": 60, "8": 4}
    assert breeding.parse_preview(ld(2, outcome(46, 38.1)), KEYMAP["mating_preview"]) is None  # ne fait pas 100 %

    c = db.connect(":memory:")
    db.save_stable(c, breeding.parse_stable(stable_body(), STABLE), NOW)
    assert db.save_mating_preview(c, NOW + 1, (uid(1).decode(), uid(4).decode()), preview)
    assert not db.save_mating_preview(c, NOW + 2, (uid(1).decode(), uid(99).decode()), preview)  # monture inconnue
    f = KEYMAP["mating_birth"].fields
    birth = ld(f["wrap"], ld(f["parents"], mount(1, sterile=True, love=20000, maturity=20000, stamina=20000, level=53, xp=40000))
               + ld(f["parents"], mount(4, sterile=True, love=20000, maturity=20000, stamina=20000, level=53, xp=40000))
               + vi(3, 90) + ld(f["baby"], mount(9, male=True, color=10, parents=(18, 10), serenity=-3252)))
    parents, baby = breeding.parse_birth(birth, KEYMAP["mating_birth"], STABLE)
    db.save_mating_birth(c, NOW + 3, parents, baby)
    assert c.execute("SELECT COUNT(*) FROM mounts WHERE sterile = 1").fetchone() == (2,)  # les parents sont devenus stériles
    assert c.execute("SELECT color, parents FROM mounts WHERE uid = ?", (uid(9).decode(),)).fetchone() == (10, "[18, 10]")
    text = mating_export.lines(c)
    assert len(text) == 2 and "46 (cible) 38.10 %" in text[0] and "bébé couleur 10 mâle" in text[1] and uid(1).decode() not in "".join(text)


def test_a_negative_herd_of_babies_is_brought_to_minus_2000():
    # Bébés : tout à remplir, sérénité négative, baffeur et foudroyeur en marche (l'enclos du 24/10).
    p = sim.Paddock(1, 0.0, [75600, 79980, 78730, 60000, 65040, 80000], {sim.SLAPPER, sim.BLASTER},
                    herd(-5000, -4615, -2582, -1648, love=0, maturity=0, stamina=1300))
    first = sim.gestures(p)[0]
    # Le baffeur regroupe les DD contre −5 000, puis le caresseur les remonte toutes entre −2 000 et −1.
    assert first.kind == "switch" and first.title == "Passe au caresseur" and first.zone == (-2000, -1)


def test_an_empty_object_cut_is_not_announced_again():
    c = db.connect(":memory:")
    db.save_paddock(c, 1, breeding.parse_paddock(paddock_body(active=(3,), objects=(0, 0, 0, 50, 0, 0),
                                                              herd=[dict(serenity=1000, maturity=0, stamina=20000)]), PADDOCK, STABLE), NOW)  # fmt: skip
    watch = Breeding(c)
    [empty] = watch.due(NOW + 10 * sim.TICK_S)
    assert empty.kind == "empty" and empty.running == sim.TROUGH
    db.save_toggle(c, 1, NOW + 200, sim.TROUGH, False)
    assert watch.done(NOW + 205) == [(1, "empty", sim.TROUGH, None)]
    assert watch.due(NOW + 206) == []
