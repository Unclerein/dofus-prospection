"""Accès en lecture seule au dofus.sqlite de ledouxm/dofus-sqlite."""
import json
import sqlite3
from pathlib import Path

DOFUS_SQLITE = Path(__file__).resolve().parents[2] / "data" / "static" / "dofus.sqlite"

# Les armes ne sont pas dans ItemData : un item est une ligne de l'une ou l'autre table.
_ITEMS = (
    "SELECT id, nameId, typeId, level FROM ItemData "
    "UNION ALL SELECT id, nameId, typeId, level FROM WeaponData"
)


def connect(path: Path = DOFUS_SQLITE) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def find_item_ids(conn: sqlite3.Connection, name: str, lang: str = "fr") -> list[int]:
    rows = conn.execute(
        f"SELECT i.id FROM ({_ITEMS}) i "
        "JOIN translations t ON t.id = i.nameId AND t.lang = ? WHERE t.value = ? ORDER BY i.id",
        (lang, name),
    )
    return [r[0] for r in rows]


def recipe_of(conn: sqlite3.Connection, item_id: int, lang: str = "fr") -> dict | None:
    """Recette qui produit item_id, ou None si l'item n'est pas craftable."""
    row = conn.execute(
        "SELECT r.id, r.quantities, r.resultLevel, r.jobId, t.value FROM RecipeData r "
        "JOIN JobData j ON j.id = r.jobId "
        "LEFT JOIN translations t ON t.id = j.nameId AND t.lang = ? WHERE r.resultId = ?",
        (lang, item_id),
    ).fetchone()
    if row is None:
        return None
    recipe_id, quantities, level, job_id, job_name = row
    # quantities[i] correspond au i-ème ingrédient dans l'ordre d'insertion de la jonction.
    ingredients = conn.execute(
        f"SELECT j.target_id, t.value FROM RecipeData_ingredientIds_junction j "
        f"LEFT JOIN ({_ITEMS}) i ON i.id = j.target_id "
        "LEFT JOIN translations t ON t.id = i.nameId AND t.lang = ? "
        "WHERE j.RecipeData_id = ? ORDER BY j.rowid",
        (lang, recipe_id),
    ).fetchall()
    return {
        "result_id": item_id,
        "job_id": job_id,
        "job": job_name,
        "level": level,
        "ingredients": [
            {"item_id": iid, "name": name, "quantity": qty}
            for (iid, name), qty in zip(ingredients, json.loads(quantities), strict=True)
        ],
    }
