"""XP de forgemagie mesurée dans l'archive : archive synthétique dont l'XP suit une formule cachée."""
from dofustool.archive import Archive
from dofustool.messages import Mapping
from dofustool.protocol.session import Message
from dofustool.protocol.tcp import S2C
from dofustool.tools import fm_xp

from .test_characters import JOBS, job
from .test_identify import BUILDS, fm_object_body, fm_result_body
from .test_protocol import ld

PLACED = Mapping("obj", BUILDS[0]["fm_object"])
RESULT = Mapping("res", BUILDS[0]["fm_result"])
MAPS = {"obj": ("fm_object", PLACED), "res": ("fm_result", RESULT), "job": ("job_levels", JOBS)}
RUNE_FO, RUNE_PA_FO, RUNE_VI = 1519, 1545, 1523
FORCE, VITA = 118, 125
DENSITY = {FORCE: 1.0, VITA: 0.2}
RUNES = {RUNE_FO: (FORCE, 1), RUNE_PA_FO: (FORCE, 3), RUNE_VI: (VITA, 5)}
JOB = 44  # forgemage
RING, AMULET = 2000, 2001
LEVELS = {RING: 50, AMULET: 150}


def hidden(weight: float, level: int, result: str) -> int:
    """La formule que l'outil doit retrouver (inventée pour le test)."""
    return round(weight * level / 10 * (0.5 if result == fm_xp.SN else 1))


def session():
    archive = Archive(":memory:")
    conn = archive.open_connection(1000.0, 40001, "s", "test")
    ts, xp = 1000.0, 5_000
    add = lambda key, body: archive.add(conn, Message(0, ts, S2C, key, body, 3, None))  # noqa: E731
    add("job", job(JOB, 60, xp))  # liste complète à la connexion
    stats = {RING: {FORCE: 20, VITA: 100}, AMULET: {FORCE: 30, VITA: 200}}
    plan = [(RING, RUNE_FO, "SC"), (RING, RUNE_PA_FO, "SC"), (AMULET, RUNE_VI, "SC"), (AMULET, RUNE_FO, "SN"),
            (RING, RUNE_VI, "EC"), (AMULET, RUNE_PA_FO, "SC"), (RING, RUNE_VI, "SC"), (AMULET, RUNE_VI, "SC")] * 2  # fmt: skip
    for uid, (item, rune, result) in enumerate(plan):
        ts += 5
        add("obj", fm_object_body(PLACED.fields, item=item, uid=10 + item, effects=tuple(stats[item].items())))
        add("obj", fm_object_body(PLACED.fields, item=rune, uid=99, effects=((RUNES[rune][0], RUNES[rune][1]),)))
        effect, value = RUNES[rune]
        after = dict(stats[item])
        if result != "EC":
            after[effect] += value
        if result != "SC":
            other = VITA if effect == FORCE else FORCE
            after[other] -= 1
        stats[item] = after
        ts += 0.5
        add("res", fm_result_body(RESULT.fields, passed=result != "EC", pool=0, change=0, item=item, uid=10 + item, effects=tuple(after.items())))
        if result != "EC":
            xp += hidden(DENSITY[effect] * value, LEVELS[item], result)
            ts += 0.2
            add("job", job(JOB, 60, xp))
    return archive.db


def test_passes_are_measured_and_the_formula_found():
    passes = fm_xp.replay(session(), MAPS, set(RUNES))
    assert len(passes) == 16
    runes = fm_xp.weigh(passes, DENSITY)
    assert runes == {RUNE_FO: (FORCE, 1), RUNE_PA_FO: (FORCE, 3), RUNE_VI: (VITA, 5)}
    first = passes[0]
    assert (first.result, first.weight, first.xp, first.job_id, first.job_level) == ("SC", 1.0, 5, JOB, 60)
    assert [p.result for p in passes[:8]] == ["SC", "SC", "SC", "SN", "EC", "SC", "SC", "SC"]
    assert passes[4].xp is None  # échec : pas d'XP
    assert passes[3].xp == hidden(1.0, 150, "SN")
    report = "\n".join(fm_xp.analyse(passes, LEVELS))
    # La loi de puissance retrouve poids^1 × niveau^1 et la moitié pour un succès neutre.
    line = next(l for l in report.splitlines() if "ajusté en logarithmes" in l)
    assert "poids^1.0" in line and "niveau^1.0" in line and "× 0.5" in line, line


def test_keymaps_include_backups(tmp_path, monkeypatch):
    import json

    backups = tmp_path / "keymap-backups"
    backups.mkdir()
    old = {"fm_result": {"key": "ancien", "fields": BUILDS[1]["fm_result"]}, "avg_prices": {"key": "x", "fields": {"entries": 1}}}
    (backups / "keymap-20261001-120000.json").write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.setattr(fm_xp, "RUNTIME_KEYMAP_PATH", tmp_path / "absent.json")
    maps = fm_xp.keymaps(tmp_path)
    assert maps["ancien"][0] == "fm_result" and any(name == "job_levels" for name, _ in maps.values())
