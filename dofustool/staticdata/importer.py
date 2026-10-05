"""Import des données statiques de dofus.sqlite vers les tables de data/market.sqlite."""
import json
import sqlite3
import time
from pathlib import Path

from .. import db
from . import source

VERSION_FILE = source.DOFUS_SQLITE.with_name("VERSION")

# Bit de m_flags qui marque un item échangeable. Déduit, pas documenté : tous les items
# ayant un prix moyen l'ont (8 767 sur 8 767), aucun item sans ce bit n'a de prix, et il
# concorde avec le champ « exchangeable » de DofusDB sur un échantillon de 12 items.
EXCHANGEABLE_FLAG = 0x4

SCHEMA = db.STATIC_SCHEMA

_ITEMS = """
SELECT i.id, t.value, i.typeId, tt.value, i.level, (i.m_flags & {flag}) != 0, {weapon}
FROM {table} i
LEFT JOIN translations t ON t.id = CAST(i.nameId AS TEXT) AND t.lang = :lang
LEFT JOIN ItemTypeData ty ON ty.id = i.typeId
LEFT JOIN translations tt ON tt.id = CAST(ty.nameId AS TEXT) AND tt.lang = :lang
"""
# translations.id est de type TEXT : sans le CAST, SQLite n'utilise pas sa clé primaire.


def read_version(path: Path = VERSION_FILE) -> str:
    return path.read_text(encoding="utf-8-sig").strip() if path.exists() else "inconnue"


def import_static(
    src_path: Path = source.DOFUS_SQLITE,
    dst: sqlite3.Connection | None = None,
    version: str | None = None,
    lang: str = "fr",
) -> dict[str, int]:
    """Remplace les tables statiques par le contenu de src_path. Renvoie le nombre de lignes par table."""
    src = source.connect(src_path)
    own_dst = dst is None
    dst = dst or db.connect()
    try:
        items = []
        for table, weapon in (("ItemData", 0), ("WeaponData", 1)):
            sql = _ITEMS.format(table=table, weapon=weapon, flag=EXCHANGEABLE_FLAG)
            # Un item sans traduction garde un nom repérable plutôt que de disparaître.
            items += [(i, name or f"#{i}", *rest) for i, name, *rest in src.execute(sql, {"lang": lang})]
        jobs = [
            (i, name or f"#{i}")
            for i, name in src.execute(
                "SELECT j.id, t.value FROM JobData j "
                "LEFT JOIN translations t ON t.id = CAST(j.nameId AS TEXT) AND t.lang = ?",
                (lang,),
            )
        ]
        recipes, ingredients = [], []
        by_recipe: dict[str, list[int]] = {}
        # quantities[i] correspond au i-ème ingrédient dans l'ordre d'insertion de la jonction.
        for recipe_id, item_id in src.execute(
            "SELECT RecipeData_id, target_id FROM RecipeData_ingredientIds_junction ORDER BY rowid"
        ):
            by_recipe.setdefault(recipe_id, []).append(item_id)
        for recipe_id, result_id, job_id, level, quantities in src.execute(
            "SELECT id, resultId, jobId, resultLevel, quantities FROM RecipeData"
        ):
            recipes.append((result_id, job_id, level))
            ingredients += [
                (result_id, item_id, quantity)
                for item_id, quantity in zip(by_recipe.get(recipe_id, []), json.loads(quantities), strict=True)
            ]

        with dst:  # une seule transaction : l'ancien contenu reste en place si l'import échoue
            dst.executescript(SCHEMA)
            for table in ("items", "jobs", "recipes", "recipe_ingredients"):
                dst.execute(f"DELETE FROM {table}")
            dst.executemany("INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?)", items)
            dst.executemany("INSERT INTO jobs VALUES (?, ?)", jobs)
            dst.executemany("INSERT INTO recipes VALUES (?, ?, ?)", recipes)
            dst.executemany("INSERT INTO recipe_ingredients VALUES (?, ?, ?)", ingredients)
            dst.executemany(
                "INSERT OR REPLACE INTO static_meta VALUES (?, ?)",
                [("version", version or read_version()), ("imported_at", str(int(time.time()))), ("lang", lang)],
            )
        return {"items": len(items), "jobs": len(jobs), "recipes": len(recipes), "recipe_ingredients": len(ingredients)}
    finally:
        src.close()
        if own_dst:
            dst.close()
