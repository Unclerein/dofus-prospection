"""Personnages, niveaux de métier, et écriture de config.toml. Données synthétiques uniquement."""
import json
import urllib.error
import urllib.request

import pytest

from dofustool import config, db
from dofustool.archive import Archive
from dofustool.capture.pipeline import Pipeline
from dofustool.messages import Mapping, characters, load_keymap
from dofustool.messages.characters import Character, JobLevel
from dofustool.web.api import Api

from .test_app import app_db  # noqa: F401  (fixture : une base complète sur disque)
from .test_pipeline import any_frame, feed
from .test_protocol import any_, framed, ld, vi
from .test_web import get, server  # noqa: F401

LIST = Mapping("lst", {"entries": 1, "info": 2, "id": 3, "level": 4, "name": 5})
SELECT = Mapping("sel", {"id": 1})
JOBS = Mapping("job", {"entries": 2, "job_id": 1, "xp": 2, "level": 5})
KEYMAP = {"character_list": LIST, "character_select": SELECT, "job_levels": JOBS}


def character(character_id: int, name: str, level: int) -> bytes:
    info = ld(1, ld(3, vi(4, 3)) + vi(6, 4)) + vi(4, level) + ld(5, name.encode())
    return ld(1, ld(2, info) + vi(3, character_id))


def job(job_id: int, level: int, xp: int = 0) -> bytes:
    return ld(2, vi(1, job_id) + (vi(2, xp) if xp else b"") + vi(3, 999) + vi(5, level))


def test_parse_list_selection_and_jobs():
    body = character(52_000_000_001, "Alpha", 234) + character(7, "Bêta", 12)
    assert characters.parse_list(body, LIST) == (Character(52_000_000_001, "Alpha", 234), Character(7, "Bêta", 12))
    assert characters.parse_selection(vi(1, 52_000_000_001), SELECT) == 52_000_000_001
    assert characters.parse_jobs(job(28, 103, 106_454) + job(48, 1), JOBS) == (JobLevel(28, 103, 106_454), JobLevel(48, 1, 0))


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"\xff",
        ld(1, vi(3, 7)),  # pas de nom
        ld(1, ld(2, vi(4, 10) + ld(5, b"\xff\xfe")) + vi(3, 7)),  # nom illisible
        ld(1, ld(2, vi(4, 10) + ld(5, b"a\nb")) + vi(3, 7)),  # caractère de contrôle
        ld(1, ld(2, vi(4, 10) + ld(5, b"x" * 65)) + vi(3, 7)),  # nom trop long
    ],
)
def test_parse_list_rejects_unexpected_shapes(body):
    assert characters.parse_list(body, LIST) is None


def test_parse_rejects_unexpected_selection_and_jobs():
    assert characters.parse_selection(vi(1, 7) + vi(2, 1), SELECT) is None
    assert characters.parse_selection(ld(1, b"abc"), SELECT) is None
    assert characters.parse_jobs(b"", JOBS) is None
    assert characters.parse_jobs(job(28, 201), JOBS) is None  # niveau impossible
    assert characters.parse_jobs(ld(2, vi(1, 28) + ld(5, b"x")), JOBS) is None


def test_real_keymap_has_character_mappings():
    keymap = load_keymap()
    assert set(KEYMAP) <= keymap.keys()
    assert keymap["job_levels"].fields.keys() == JOBS.fields.keys()


def test_pipeline_stores_characters_and_jobs(caplog):
    market = db.connect(":memory:")
    pipeline = Pipeline(Archive(":memory:"), market, dict(KEYMAP))
    select = framed(ld(1, ld(1, any_("sel", vi(1, 7))) + vi(2, 3)))
    s2c = (
        framed(any_frame("lst", character(7, "Alpha", 200) + character(8, "Bêta", 50)))
        + framed(any_frame("job", job(28, 103, 106_454) + job(24, 40, 15_773)))
        + framed(any_frame("job", job(24, 41, 16_500)))  # gain d'expérience : un seul métier
    )
    with caplog.at_level("INFO", logger="dofustool.capture"):
        feed(pipeline, s2c, c2s=select)
    assert market.execute("SELECT id, name, level FROM characters ORDER BY id").fetchall() == [(7, "Alpha", 200), (8, "Bêta", 50)]
    assert {j: v[:2] for j, v in db.character_jobs(market, 7).items()} == {28: (103, 106_454), 24: (41, 16_500)}
    assert db.character_jobs(market, 8) == {}
    assert [r.getMessage() for r in caplog.records] == ["Métiers enregistrés : 2."]

    # Un relevé plus ancien (rejeu d'archive) ne remplace pas le plus récent.
    db.save_job_levels(market, 7, [JobLevel(24, 12, 1_500)], 1.0)
    assert db.character_jobs(market, 7)[24][0] == 41


def test_jobs_without_a_chosen_character_are_not_stored():
    market = db.connect(":memory:")
    pipeline = Pipeline(Archive(":memory:"), market, dict(KEYMAP))
    feed(pipeline, framed(any_frame("job", job(28, 103, 106_454))))
    assert market.execute("SELECT COUNT(*) FROM character_jobs").fetchone()[0] == 0


# --- config.toml ---------------------------------------------------------------

VALUES = {
    "server_name": "Kourial",
    "character_id": 7,
    "jobs": {"Paysan": 103, "Façonneur": 10},
    "hdv_tax": 0.02,
    "last_sale_max_age_hours": 36,
    "min_snapshots_for_trend": 5,
    "min_liquidity": 100,
    "trend_threshold": 0.2,
    "iface": "",
    "avg_prices_timeout_s": 60,
    "ankama_path": 'C:\\Program Files\\Ankama\\Ankama "Launcher".exe',
    "dofus_process": "Dofus",
    "start_dashboard": True,
}


def test_config_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    cfg = config.from_values(VALUES)
    config.save(cfg, path)
    assert config.load(path) == cfg
    assert cfg.character_id == 7 and cfg.jobs == {"Paysan": 103, "Façonneur": 10} and cfg.iface is None
    assert cfg.ankama_path.endswith('"Launcher".exe') and cfg.last_sale_max_age_hours == 36.0
    assert not list(tmp_path.glob("*.tmp"))
    config.save(config.Config(), path)  # les valeurs par défaut se relisent aussi
    assert config.load(path) == config.Config()


@pytest.mark.parametrize(
    "change",
    [
        {"hdv_tax": 3},
        {"hdv_tax": "0.02"},
        {"hdv_tax": True},
        {"min_liquidity": 1.5},
        {"min_snapshots_for_trend": 0},
        {"trend_threshold": None},
        {"jobs": {"Paysan": 201}},
        {"jobs": {"Paysan": 0}},
        {"jobs": {"a\nb": 10}},
        {"jobs": []},
        {"character_id": -1},
        {"character_id": "7"},
        {"server_name": 'x"\n[launcher]'},
        {"ankama_path": "x" * 300},
    ],
)
def test_config_rejects_invalid_values(change):
    with pytest.raises(ValueError):
        config.from_values({**VALUES, **change})


@pytest.fixture
def api(app_db, tmp_path):  # noqa: F811
    conn = db.connect(app_db)
    db.save_characters(conn, [Character(7, "Alpha", 200), Character(8, "Bêta", 50)], 100.0)
    job_ids = {name: job_id for job_id, name in conn.execute("SELECT id, name FROM jobs")}
    db.save_job_levels(conn, 7, [JobLevel(job_ids["Paysan"], 103, 106_454)], 200.0)
    conn.close()
    return Api(app_db, tmp_path / "config.toml")


def test_api_config_lists_characters_and_saves(api):
    data = api.config()
    json.dumps(data, allow_nan=False)
    assert data["values"]["character_id"] is None and "Paysan" in data["jobs"] and "Base" not in data["jobs"]
    alpha, beta = data["characters"]
    assert (alpha["name"], alpha["level"], alpha["jobs"], alpha["jobs_at"]) == ("Alpha", 200, {"Paysan": 103}, 200.0)
    assert beta["jobs"] == {} and beta["jobs_at"] is None

    assert api.status()["has_jobs"] is False
    first = api.version()["stamp"]
    saved = api.save_config({**VALUES, "jobs": alpha["jobs"], "ankama_path": ""})
    assert saved["values"]["character_id"] == 7 and saved["values"]["jobs"] == {"Paysan": 103}
    assert api.version()["stamp"] != first and api.status()["has_jobs"] is True
    paysan = next(j for j in api.jobs()["jobs"] if j["name"] == "Paysan")
    assert paysan["level"] == 103 and paysan["xp"] == 106_454  # l'expérience relevée suit le niveau enregistré

    api.save_config({**VALUES, "jobs": {"Paysan": 150}, "ankama_path": ""})
    paysan = next(j for j in api.jobs()["jobs"] if j["name"] == "Paysan")
    assert paysan["level"] == 150 and paysan["xp"] is None  # niveau saisi à la main : pas d'expérience relevée
    with pytest.raises(ValueError):
        api.save_config({**VALUES, "hdv_tax": 9})
    assert config.load(api.config_path).jobs == {"Paysan": 150}  # un refus ne touche pas au fichier


def post(url: str, body: bytes, headers: dict):
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def test_http_config(server, api):  # noqa: F811
    json_type = {"Content-Type": "application/json"}
    assert json.loads(get(server + "/api/config")[2])["values"]["hdv_tax"] == 0.02
    body = json.dumps({**VALUES, "ankama_path": ""}).encode()
    assert post(server + "/api/config", body, json_type)["values"]["min_liquidity"] == 100
    assert config.load(api.config_path).min_liquidity == 100

    with pytest.raises(urllib.error.HTTPError) as error:
        post(server + "/api/config", json.dumps({**VALUES, "hdv_tax": 9}).encode(), json_type)
    assert error.value.code == 400 and "Taxe HDV" in json.loads(error.value.read())["error"]
    # Une page d'un autre site ne peut pas modifier la configuration.
    for headers in ({"Content-Type": "text/plain"}, {**json_type, "Origin": "https://exemple.invalid"}, {**json_type, "Host": "exemple.invalid"}):
        with pytest.raises(urllib.error.HTTPError) as error:
            post(server + "/api/config", body.replace(b"100", b"777"), headers)
        assert error.value.code == 403
    assert config.load(api.config_path).min_liquidity == 100
