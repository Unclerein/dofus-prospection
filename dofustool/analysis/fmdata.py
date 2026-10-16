"""Jeu de données de forgemagie : chaque passage de rune capté, avec tout ce qui peut peser sur son résultat.

But : retrouver par la mesure comment le jeu tire le résultat d'un passage (succès critique, neutre,
échec) et ce qu'il retire, pour un simulateur calé sur de vrais passages. Ankama n'a jamais publié
la formule ; le calcul se fait sur le serveur, la capture n'en voit que les résultats.

Pour chaque passage du journal (table fm_passes), on relit dans l'archive l'objet après le passage et
l'objet juste avant (passage précédent du même exemplaire dans la même connexion, ou objet posé sur
l'atelier), puis on y joint la rune, la ligne visée, ses bornes de base et les poids.

Le fichier écrit (data/fm_dataset.csv) reste sur ce PC : il décrit les objets du joueur.

Usage : python -m dofustool.analysis.fmdata [--out data/fm_dataset.csv]
"""
import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import db
from ..archive import ARCHIVE_PATH
from ..messages import fm, load_keymap, runtime_keymap_path

STATIC_PATH = db.MARKET_PATH.parent / "static" / "dofus.sqlite"
OUT_PATH = db.MARKET_PATH.parent / "fm_dataset.csv"
SC, SN, EC = "sc", "sn", "ec"


@dataclass
class Pass:
    ts: float
    connection: int
    uid: int
    item_id: int
    item_level: int | None
    item_type: str
    rune_id: int
    rune_effect: int | None  # caractéristique de la rune
    rune_value: int | None  # bonus de la rune
    rune_weight: float | None  # poids de la rune : bonus × densité
    line_before: int  # valeur de la ligne visée avant le passage (0 si absente)
    line_after: int
    line_min: int | None  # bornes de base de la ligne ; None : la ligne n'existe pas de base (exo)
    line_max: int | None
    line_kind: str  # « exo », « over » (au-delà du max), « max », « haut » (80 à 100 % du max), « bas »
    line_weight_before: float  # poids de la ligne visée avant le passage
    item_weight_before: float  # poids de toutes les lignes positives avant le passage
    item_weight_max: float  # poids des lignes de base à leur jet max
    pool_before: float | None  # puits avant (connu à partir du deuxième passage de la séance)
    pool_after: float
    pool_change: int
    passed: int
    outcome: str  # sc, sn, ec
    lost_weight: float  # poids retiré aux autres lignes
    lost_lines: str  # JSON {caractéristique: points retirés}
    before_known: int  # l'état d'avant vient d'un message vu (1) ou manque (0)


def densities(path: Path = STATIC_PATH) -> dict[int, float]:
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {row[0]: float(row[1]) for row in source.execute("SELECT id, effectPowerRate FROM EffectData") if row[1]}
    finally:
        source.close()


def line_kind(value: int, low: int | None, high: int | None) -> str:
    if high is None:
        return "exo"
    if value > high:
        return "over"
    if value == high:
        return "max"
    return "haut" if high > 0 and value >= 0.8 * high else "bas"


def weight_of(effects: dict[int, int], density: dict[int, float]) -> float:
    return sum(value * density.get(effect, 0.0) for effect, value in effects.items() if value > 0)


def build(market: sqlite3.Connection, archive: sqlite3.Connection, density: dict[int, float]) -> list[Pass]:
    keymap = load_keymap(runtime_keymap_path())
    result_map, object_map = keymap["fm_result"], keymap["fm_object"]
    items = {row[0]: (row[1], row[2] or "") for row in market.execute("SELECT id, level, type_name FROM items")}
    templates: dict[int, dict[int, tuple[int, int]]] = defaultdict(dict)
    for item_id, effect, low, high in market.execute("SELECT item_id, effect_id, min_value, max_value FROM item_effects"):
        templates[item_id][effect] = (low, high)
    passes = market.execute("SELECT source_id, ts, uid, item_id, rune_id, pool_change FROM fm_passes ORDER BY ts, source_id").fetchall()

    # Objets posés sur l'atelier, par connexion : l'état d'avant au premier passage d'une séance.
    placed: dict[int, list[tuple[float, fm.Object]]] = defaultdict(list)
    for connection, ts, body in archive.execute("SELECT connection_id, ts, body FROM messages WHERE key = ? AND direction = 's2c' ORDER BY id", (object_map.key,)):
        obj = fm.parse_object(bytes(body), object_map)
        if obj is not None and obj.effects:
            placed[connection].append((ts, obj))

    last_after: dict[tuple[int, int], tuple[dict[int, int], float]] = {}
    out: list[Pass] = []
    for source_id, ts, uid, item_id, rune_id, pool_change in passes:
        row = archive.execute("SELECT connection_id, body FROM messages WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            continue
        connection, body = row
        result = fm.parse_result(bytes(body), result_map)
        if result is None or result.object.uid != uid:
            continue
        after = {e: v for e, v in result.object.effects if v is not None}
        previous = last_after.get((connection, uid))
        pool_before = None
        if previous is not None:
            before, pool_before = previous
            known = 1
        else:
            seen = [o for t, o in placed.get(connection, []) if t <= ts and o.uid == uid]
            before = {e: v for e, v in seen[-1].effects if v is not None} if seen else None
            known = int(before is not None)
        last_after[(connection, uid)] = (after, result.pool)
        if before is None:
            continue
        rune = templates.get(rune_id, {})
        rune_effect, (rune_value, _) = next(iter(rune.items())) if len(rune) == 1 else (None, (None, None))
        base = templates.get(item_id, {})
        low, high = base.get(rune_effect, (None, None)) if rune_effect is not None else (None, None)
        line_before, line_after = before.get(rune_effect, 0), after.get(rune_effect, 0)
        lost = {e: v - after.get(e, 0) for e, v in before.items() if e != rune_effect and v > 0 and after.get(e, 0) < v}
        lost_weight = sum(points * density.get(e, 0.0) for e, points in lost.items())
        passed = int(result.passed)
        outcome = EC if not passed else (SN if lost or (known == 0 and pool_change) else SC)
        level, type_name = items.get(item_id, (None, ""))
        out.append(Pass(
            ts=ts, connection=connection, uid=uid, item_id=item_id, item_level=level, item_type=type_name, rune_id=rune_id,
            rune_effect=rune_effect, rune_value=rune_value,
            rune_weight=round(rune_value * density.get(rune_effect, 0.0), 3) if rune_effect is not None and rune_value is not None else None,
            line_before=line_before, line_after=line_after, line_min=low, line_max=high, line_kind=line_kind(line_before, low, high),
            line_weight_before=round(line_before * density.get(rune_effect, 0.0), 3) if rune_effect is not None else 0.0,
            item_weight_before=round(weight_of(before, density), 3),
            item_weight_max=round(sum(h * density.get(e, 0.0) for e, (_, h) in base.items() if h > 0), 3),
            pool_before=pool_before, pool_after=result.pool, pool_change=result.pool_change, passed=passed, outcome=outcome,
            lost_weight=round(lost_weight, 3), lost_lines=json.dumps(lost, separators=(",", ":")), before_known=known,
        ))  # fmt: skip
    return out


def write(rows: list[Pass], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Pass.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()
    market = sqlite3.connect(f"file:{db.MARKET_PATH.as_posix()}?mode=ro", uri=True)
    archive = sqlite3.connect(f"file:{ARCHIVE_PATH.as_posix()}?mode=ro", uri=True)
    rows = build(market, archive, densities())
    write(rows, args.out)
    print(f"{len(rows)} passages écrits dans {args.out}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
