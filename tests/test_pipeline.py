import logging

import pytest

from dofustool import config, db
from dofustool.archive import Archive
from dofustool.capture import Segment
from dofustool.capture.pipeline import Pipeline
from dofustool.messages import Mapping

from datetime import timedelta

from .test_avg_prices import synthetic
from .test_market_history import MAPPING as MARKET_MAPPING
from .test_market_history import T0, entry
from .test_protocol import AUTH_LIKE, EVENT, REQUEST, framed, ld, segments

MAPPING = Mapping("avg", {"entries": 1, "item_id": 3, "price": 5})


def any_frame(key: str, body: bytes) -> bytes:
    return ld(2, ld(3, ld(1, b"type.ankama.com/" + key.encode()) + ld(2, body)))


@pytest.fixture
def pipeline():
    market = db.connect(":memory:")
    p = Pipeline(Archive(":memory:"), market, {"avg_prices": MAPPING}, avg_prices_timeout_s=60.0)
    yield p
    market.close()


def feed(pipeline, s2c: bytes, t: float = 1000.0, port: int = 40001, c2s: bytes = framed(REQUEST)):
    for seg in segments(port, "192.0.2.2", c2s, s2c, t):
        pipeline.handle(seg)


def test_snapshot_saved_archived_and_logged(pipeline, caplog):
    caplog.set_level(logging.INFO, logger="dofustool.capture")
    feed(pipeline, framed(EVENT) + framed(any_frame("avg", synthetic(1200))))
    pipeline.tick(1001.0)

    assert pipeline.archive.count() == 3
    snapshot_id, _ = db.latest_snapshot(pipeline.market)
    assert len(db.snapshot_prices(pipeline.market, snapshot_id)) == 1200
    assert [r.getMessage() for r in caplog.records] == ["Relevé de prix moyens enregistré : 1200 items."]
    status = db.get_status(pipeline.market)
    assert status["decode_alert"] == "" and "heartbeat_ts" in status and "last_avg_prices_ts" in status

    # Reconnexion : même liste, pas de second relevé.
    feed(pipeline, framed(any_frame("avg", synthetic(1200))), t=2000.0, port=40002)
    assert pipeline.market.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    assert "identiques au dernier relevé" in caplog.records[-1].getMessage()


def test_market_history_saved_and_logged(pipeline, caplog):
    caplog.set_level(logging.INFO, logger="dofustool.capture")
    pipeline.keymap["market_history"] = MARKET_MAPPING
    pipeline.market.execute("INSERT INTO items VALUES (289, 'Blé', 1, 'Céréale', 1, 1, 0, 2)")
    body = entry(1, T0, 9, 100) + entry(2, T0, 9, 100) + entry(2, T0 - timedelta(days=1), 8, 900)
    feed(pipeline, framed(any_frame("xxx", body)) + framed(any_frame("xxx", b"\x08\x01")))  # le 2e est rejeté
    assert [r.getMessage() for r in caplog.records] == [
        "Cours du marché enregistré : Blé (1 points horaires, 2 journaliers)."
    ]
    assert pipeline.market.execute("SELECT COUNT(*) FROM market_history").fetchone()[0] == 3
    assert db.latest_last_sale(pipeline.market, 289)[0] == 9
    assert pipeline.archive.count() == 3  # les deux messages restent dans l'archive brute


def test_alert_when_no_avg_prices_after_timeout(pipeline, caplog):
    feed(pipeline, framed(EVENT))
    pipeline.tick(1030.0)
    assert not caplog.records  # encore dans le délai
    # Le flux reste actif (nouveau trafic), toujours sans prix moyens.
    next_seq = 501 + len(framed(EVENT))
    pipeline.handle(Segment(1065.0, "192.0.2.2", 5555, "10.0.0.2", 40001, next_seq, 0, framed(EVENT)))
    pipeline.tick(1070.0)
    pipeline.tick(1071.0)
    alerts = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(alerts) == 1  # une seule fois
    assert db.get_status(pipeline.market)["decode_alert"].startswith("Flux de jeu actif")


def test_no_alert_for_idle_or_auth_only_connection(pipeline, caplog):
    feed(pipeline, framed(AUTH_LIKE), c2s=framed(AUTH_LIKE))  # flux d'authentification seul
    feed(pipeline, framed(EVENT), t=1000.0, port=40002)  # flux de jeu, puis plus rien
    pipeline.tick(1200.0)
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
    assert pipeline.archive.count() == 2  # rien du flux d'authentification


def test_wrong_shape_under_known_key_is_not_saved(pipeline):
    feed(pipeline, framed(any_frame("avg", synthetic(1200) + ld(7, b"x"))))
    assert db.latest_snapshot(pipeline.market) is None
    assert pipeline.archive.count() == 2  # conservé dans l'archive brute


def test_handle_never_raises(pipeline, caplog):
    pipeline.archive.close()  # provoque une erreur d'écriture
    feed(pipeline, framed(EVENT))
    feed(pipeline, framed(EVENT), port=40002)
    pipeline.tick(1001.0)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1  # signalée une seule fois


def test_config_defaults_and_file(tmp_path):
    assert config.load(tmp_path / "absent.toml") == config.Config()
    path = tmp_path / "config.toml"
    path.write_text('[jobs]\nBijoutier = 120\n[market]\nhdv_tax = 0.01\n[capture]\niface = ""\n', encoding="utf-8")
    cfg = config.load(path)
    assert cfg.jobs == {"Bijoutier": 120} and cfg.hdv_tax == 0.01 and cfg.iface is None
    assert config.load().ankama_path.endswith("Ankama Launcher.exe")
