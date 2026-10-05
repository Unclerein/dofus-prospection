import sqlite3

import pytest

from dofustool.staticdata import importer, source

EXCH = importer.EXCHANGEABLE_FLAG


@pytest.fixture
def mini_source(tmp_path):
    """Un dofus.sqlite minimal, avec le même schéma que l'original pour les tables lues."""
    path = tmp_path / "dofus.sqlite"
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE translations (id TEXT, value TEXT, lang TEXT, PRIMARY KEY (id, lang));
        CREATE TABLE ItemData (id INTEGER PRIMARY KEY, m_flags INTEGER, nameId INTEGER, typeId INTEGER, level INTEGER);
        CREATE TABLE WeaponData (id INTEGER PRIMARY KEY, m_flags INTEGER, nameId INTEGER, typeId INTEGER, level INTEGER);
        CREATE TABLE ItemTypeData (id INTEGER PRIMARY KEY, nameId INTEGER);
        CREATE TABLE JobData (id INTEGER PRIMARY KEY, nameId INTEGER);
        CREATE TABLE RecipeData (id TEXT PRIMARY KEY, resultId INTEGER, resultLevel INTEGER, quantities TEXT, jobId INTEGER);
        CREATE TABLE RecipeData_ingredientIds_junction (RecipeData_id INTEGER, target_id INTEGER,
            PRIMARY KEY (RecipeData_id, target_id));
        """
    )
    c.executemany(
        "INSERT INTO translations VALUES (?, ?, ?)",
        [("1", "Blé", "fr"), ("1", "Wheat", "en"), ("2", "Farine", "fr"), ("3", "Épée", "fr"),
         ("4", "Fer", "fr"), ("10", "Céréale", "fr"), ("20", "Paysan", "fr"), ("21", "Forgeron", "fr")],
    )  # fmt: skip
    c.executemany(
        "INSERT INTO ItemData VALUES (?, ?, ?, ?, ?)",
        [(100, 1024 | EXCH, 1, 50, 1), (101, 1024 | EXCH, 2, 50, 10), (102, 1024, 4, 50, 20), (103, EXCH, 999, 51, 5)],
    )
    c.execute("INSERT INTO WeaponData VALUES (200, ?, 3, 60, 30)", (1024 | EXCH,))
    c.execute("INSERT INTO ItemTypeData VALUES (50, 10)")
    c.executemany("INSERT INTO JobData VALUES (?, ?)", [(28, 20), (11, 21)])
    c.executemany(
        "INSERT INTO RecipeData VALUES (?, ?, ?, ?, ?)",
        [("r-farine", 101, 10, "[2]", 28), ("r-epee", 200, 30, "[5,3]", 11)],
    )
    # Ordre d'insertion volontairement différent de l'ordre des identifiants.
    c.executemany(
        "INSERT INTO RecipeData_ingredientIds_junction VALUES (?, ?)",
        [("r-epee", 102), ("r-farine", 100), ("r-epee", 101)],
    )
    c.commit()
    c.close()
    return path


def test_import_static(mini_source):
    dst = sqlite3.connect(":memory:")
    counts = importer.import_static(mini_source, dst, version="v-test")
    assert counts == {"items": 5, "jobs": 2, "recipes": 2, "recipe_ingredients": 3}

    items = {r[0]: r[1:] for r in dst.execute("SELECT * FROM items")}
    assert items[100] == ("Blé", 50, "Céréale", 1, 1, 0)
    assert items[102][4] == 0  # Fer : non échangeable dans ce jeu d'essai
    assert items[103][:3] == ("#103", 51, None)  # ni traduction ni type connu
    assert items[200] == ("Épée", 60, None, 30, 1, 1)

    assert dst.execute("SELECT job_id, level FROM recipes WHERE result_id = 200").fetchone() == (11, 30)
    assert dst.execute(
        "SELECT item_id, quantity FROM recipe_ingredients WHERE result_id = 200 ORDER BY item_id"
    ).fetchall() == [(101, 3), (102, 5)]
    assert dict(dst.execute("SELECT key, value FROM static_meta"))["version"] == "v-test"


def test_reimport_replaces_content(mini_source):
    dst = sqlite3.connect(":memory:")
    importer.import_static(mini_source, dst, version="v1")
    src = sqlite3.connect(mini_source)
    src.execute("DELETE FROM RecipeData WHERE id = 'r-epee'")
    src.execute("DELETE FROM RecipeData_ingredientIds_junction WHERE RecipeData_id = 'r-epee'")
    src.commit()
    src.close()
    counts = importer.import_static(mini_source, dst, version="v2")
    assert counts["recipes"] == 1 and counts["recipe_ingredients"] == 1
    assert dst.execute("SELECT COUNT(*) FROM recipe_ingredients").fetchone()[0] == 1
    assert dict(dst.execute("SELECT key, value FROM static_meta"))["version"] == "v2"


def test_failed_import_keeps_previous_content(mini_source):
    dst = sqlite3.connect(":memory:")
    importer.import_static(mini_source, dst, version="v1")
    src = sqlite3.connect(mini_source)
    src.execute("UPDATE RecipeData SET quantities = '[1]' WHERE id = 'r-epee'")  # incohérent : 2 ingrédients
    src.commit()
    src.close()
    with pytest.raises(ValueError):
        importer.import_static(mini_source, dst, version="v2")
    assert dst.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 2
    assert dict(dst.execute("SELECT key, value FROM static_meta"))["version"] == "v1"


@pytest.mark.skipif(not source.DOFUS_SQLITE.exists(), reason="data/static/dofus.sqlite absent")
def test_import_real_data():
    dst = sqlite3.connect(":memory:")
    counts = importer.import_static(dst=dst, version="test")
    assert counts["items"] > 20000 and counts["recipes"] > 4000
    assert dst.execute("SELECT name, level, exchangeable FROM items WHERE id = 2469").fetchone() == ("Gelano", 60, 1)
    assert dst.execute(
        "SELECT j.name, r.level FROM recipes r JOIN jobs j ON j.id = r.job_id WHERE r.result_id = 2469"
    ).fetchone() == ("Bijoutier", 60)
    gelano = dict(
        dst.execute(
            "SELECT i.name, ri.quantity FROM recipe_ingredients ri JOIN items i ON i.id = ri.item_id "
            "WHERE ri.result_id = 2469"
        )
    )
    assert gelano["Gelée Bleuet"] == 50 and gelano["Gelée Citron"] == 20 and len(gelano) == 8
    # Toute recette et tout ingrédient renvoie à un item connu.
    assert dst.execute("SELECT COUNT(*) FROM recipes WHERE result_id NOT IN (SELECT id FROM items)").fetchone()[0] == 0
    assert (
        dst.execute("SELECT COUNT(*) FROM recipe_ingredients WHERE item_id NOT IN (SELECT id FROM items)").fetchone()[0]
        == 0
    )
