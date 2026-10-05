import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from dofustool import db
from dofustool.analysis import DAY, GRAIN_DAY, GRAIN_HOUR
from dofustool.app import data
from dofustool.config import Config

from .test_analysis import AVG, HOUR, ITEMS, NOW, RECIPES, conn  # noqa: F401  (fixture)

CFG = Config(jobs={"Paysan": 20}, min_snapshots_for_trend=2, trend_threshold=0.15)
APP = Path(__file__).resolve().parents[1] / "dofustool" / "app" / "main.py"


def test_crafts_frame_and_filters(conn):  # noqa: F811
    ws = data.build_workspace(conn, CFG, NOW)
    df = data.crafts_frame(conn, ws)
    assert len(df) == len(RECIPES)
    pain = df[df["Objet"] == "Pain"].iloc[0]
    assert pain["Coût"] == 65 and pain["Marge"] == pytest.approx(131) and pain["Marge %"] == pytest.approx(131 / 65 * 100)
    assert pain["Mon métier"] is True or pain["Mon métier"] == True  # noqa: E712
    assert pain["Source du prix"] == "prix moyen" and pain["Âge du prix (h)"] == 1.0

    assert len(data.filter_crafts(df)) == 5  # 3 recettes incalculables masquées par défaut
    assert len(data.filter_crafts(df, only_computable=False)) == len(RECIPES)
    assert set(data.filter_crafts(df, jobs=["Forgeron"])["Objet"]) == {"Cycle A", "Cycle B"}
    assert set(data.filter_crafts(df, level_range=(15, 45))["Objet"]) == {"Pain", "Brioche"}
    assert set(data.filter_crafts(df, only_own_jobs=True)["Objet"]) == {"Farine", "Pain"}
    assert "Cycle B" not in set(data.filter_crafts(df, max_capital=100)["Objet"])
    # Liquidité : connue et insuffisante = masqué ; inconnue = conservé.
    db.save_market_history(conn, 3, GRAIN_DAY, [(int(NOW) // 86400 * 86400, 200, 5)], NOW)
    df = data.crafts_frame(conn, data.build_workspace(conn, CFG, NOW))
    filtered = set(data.filter_crafts(df, min_liquidity=10)["Objet"])
    assert "Pain" not in filtered and "Farine" in filtered


def test_trends_frame(conn):  # noqa: F811
    ws = data.build_workspace(conn, CFG, NOW)
    frame, insufficient = data.trends_frame(conn, Config(min_snapshots_for_trend=5), ws)
    assert frame.empty and insufficient == len(AVG)
    db.save_snapshot(conn, NOW, {**AVG, 1: 20})  # le Blé double
    frame, insufficient = data.trends_frame(conn, CFG, data.build_workspace(conn, CFG, NOW))
    ble = frame[frame["Objet"] == "Blé"].iloc[0]
    assert ble["Écart %"] == pytest.approx(100) and ble["Signal"] == "sur-coté"
    assert pd.isna(frame[frame["Objet"] == "Farine"].iloc[0]["Signal"])


def test_item_detail(conn):  # noqa: F811
    db.save_last_sale(conn, 3, 180, NOW - HOUR, NOW)
    db.save_market_history(conn, 3, GRAIN_HOUR, [(int(NOW) // 3600 * 3600, 180, 12)], NOW)
    ws = data.build_workspace(conn, CFG, NOW)
    pain = data.item_detail(conn, ws, 3)
    assert pain["ref"].price == 180 and len(pain["snapshots"]) == 1 and len(pain["last_sales"]) == 1
    assert list(pain["history"]) == ["Par heure (24 h)"] and pain["liquidity"].qty_24h == 12
    assert pain["market_seen_at"] == NOW
    ingredients = pain["craft"]["ingredients"].set_index("Ingrédient")
    assert ingredients.loc["Farine", "Mode"] == "craft" and ingredients.loc["Farine", "Sous-total"] == 60
    assert ingredients.loc["Eau", "Mode"] == "achat"
    assert pain["used_in"].empty

    ble = data.item_detail(conn, ws, 1)
    assert ble["craft"] is None
    assert set(ble["used_in"]["Objet"]) == {"Farine", "Levure liée", "Gâteau", "Cycle B", "Trophée lié"}
    sans_prix = data.item_detail(conn, ws, 8)
    assert sans_prix["ref"] is None and sans_prix["unit_cost"].cost is None
    assert 3 in data.item_options(conn) and 7 not in data.item_options(conn)  # Relique : ni prix ni recette


def test_status(conn):  # noqa: F811
    s = data.status(conn, NOW)
    assert not s["running"] and s["snapshots"] == 1 and s["priced_items"] == len(AVG) and s["decode_alert"] == ""
    db.set_status(conn, started_ts=NOW - 60, heartbeat_ts=NOW - 2, stopped_ts="", decode_alert="clés changées")
    s = data.status(conn, NOW)
    assert s["running"] and s["decode_alert"] == "clés changées"
    assert not data.status(conn, NOW + 600)["running"]  # plus de signe de vie


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    path = tmp_path / "market.sqlite"
    c = db.connect(path)
    c.executemany("INSERT INTO items VALUES (?, ?, 1, 'type', 1, ?, 0, 2)", [(i, n, e) for i, (n, e) in ITEMS.items()])
    c.executemany("INSERT INTO jobs VALUES (?, ?)", [(28, "Paysan"), (11, "Forgeron")])
    for result, (job, level, ingredients) in RECIPES.items():
        c.execute("INSERT INTO recipes VALUES (?, ?, ?)", (result, job, level))
        c.executemany("INSERT INTO recipe_ingredients VALUES (?, ?, ?)", [(result, i, q) for i, q in ingredients])
    import time

    now = time.time()
    for age, factor in [(20, 1.0), (10, 1.0), (5, 1.0), (3, 1.0), (0, 2.0)]:
        db.save_snapshot(c, now - age * DAY - 1, {k: int(v * factor) + age for k, v in AVG.items()})
    db.save_last_sale(c, 3, 180, now - HOUR, now)
    day = int(now) // 86400 * 86400
    db.save_market_history(c, 3, GRAIN_DAY, [(day - d * 86400, 150 + d, 10 + d) for d in range(12)], now)
    db.save_market_history(c, 3, GRAIN_HOUR, [(int(now) // 3600 * 3600 - h * 3600, 170 + h, 3) for h in range(20)], now)
    db.set_status(c, decode_alert="test d'alerte", decode_alert_ts=now)
    # Un équipement avec ses annonces et ses caractéristiques de base, pour la page Forgemagie.
    c.execute("INSERT INTO items VALUES (500, 'Anneau test', 1, 'Anneau', 60, 1, 0, 0)")
    c.executemany("INSERT INTO effects VALUES (?, ?)", [(111, "PA"), (125, "Vitalité"), (123, "Chance")])
    c.executemany("INSERT INTO item_effects VALUES (500, ?, ?, ?)", [(111, 1, 1), (125, 201, 250)])
    c.execute("INSERT INTO item_effects_fetched VALUES (500, ?)", (now,))
    c.executemany(
        "INSERT INTO hdv_listings VALUES (500, ?, ?, 0, 0, 0, ?, ?, ?)",
        [
            (1, 50_000, "[[111, 1], [125, 210]]", now, now),
            (2, 90_000, "[[111, 1], [125, 250]]", now, now),
            (3, 400_000, "[[111, 1], [125, 240], [123, 15]]", now, now),
            (4, 30_000, "[[111, 1]]", now, now),
            (5, 70_000, "[[111, 1], [125, 230]]", now - 86400, now - 86400),
        ],
    )
    c.commit()
    c.close()
    monkeypatch.setenv("DOFUSTOOL_DB", str(path))
    return path


@pytest.mark.parametrize("page", ["Crafts", "Forgemagie", "Tendances", "Fiche objet", "État"])
def test_app_pages_render_without_error(app_db, page):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=60)
    at.session_state["page"] = page
    at.session_state["item_id"] = 3
    at.run()
    assert not at.exception, at.exception
    assert at.title[0].value == page
    if page != "État":
        assert any("Alerte de décodage" in e.value for e in at.error)
