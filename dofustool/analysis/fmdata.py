"""Jeu de données de forgemagie : chaque passage de rune capté, avec tout ce qui peut peser sur son résultat.

But : retrouver par la mesure comment le jeu tire le résultat d'un passage (succès critique, neutre,
échec) et ce qu'il retire, pour un simulateur calé sur de vrais passages. Ankama n'a jamais publié
la formule ; le calcul se fait sur le serveur, la capture n'en voit que les résultats.

Chaque passage du journal (table fm_passes) garde l'objet juste avant et juste après, et le puits
d'avant. On y joint la rune, la ligne visée, ses bornes de base et les poids. Seules les runes de
forgemagie entrent : potions, éclats, orbes et transcendances ne ratent jamais.

Un succès est neutre dès que l'objet a payé quelque chose, avec ses lignes ou avec son puits ;
critique seulement s'il n'a rien payé.

Les passages des autres joueurs, reçus par le hub (table shared_fm_passes), entrent aussi : la colonne
source dit d'où vient chaque ligne, et l'exemplaire n'y est qu'une empreinte.

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
from ..share import fm_key, object_token

STATIC_PATH = db.MARKET_PATH.parent / "static" / "dofus.sqlite"
OUT_PATH = db.MARKET_PATH.parent / "fm_dataset.csv"
SC, SN, EC = "sc", "sn", "ec"
POOL_DOWN = 2  # messages.fm.POOL_DOWN


@dataclass
class Pass:
    ts: float
    source: str  # « moi » : capté sur ce PC ; « partage » : reçu par le hub
    object: str  # empreinte de l'exemplaire travaillé (share.object_token)
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
    line_kind: str  # « exo », « over » (au-delà du max), « max », « haut » (80 à 100 % du max), « bas » ; « ? » sans fiche
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
    before_known: int  # l'état d'avant a été vu (1) ou manque (0)
    extra_before: float = 0.0  # poids over et exo de tout l'objet avant le passage (au-delà des max, et lignes exo)
    extra_after: float = 0.0
    line_max_weight: float | None = None  # poids de la ligne visée à son max de base ; None pour un exo
    exo_lost: int = 0  # une ligne exo (autre que la visée) a perdu des points
    has_exo: int = 0  # l'objet portait un exo (autre que la ligne visée) avant le passage
    character_id: int | None = None  # personnage, pour les passages captés sur ce PC


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


def extra_weight(effects: dict[int, int], base: dict[int, tuple[int, int]], density: dict[int, float]) -> float:
    """Poids over et exo de l'objet : ce qui dépasse les max de base, et les lignes que le modèle n'a pas."""
    total = 0.0
    for effect, value in effects.items():
        if value <= 0:
            continue
        if effect not in base:
            total += value * density.get(effect, 0.0)
        elif 0 < base[effect][1] < value:
            total += (value - base[effect][1]) * density.get(effect, 0.0)
    return total


def build(market: sqlite3.Connection, density: dict[int, float]) -> list[Pass]:
    items = {row[0]: (row[1], row[2] or "") for row in market.execute("SELECT id, level, type_name FROM items")}
    templates: dict[int, dict[int, tuple[int, int]]] = defaultdict(dict)
    for item_id, effect, low, high in market.execute("SELECT item_id, effect_id, min_value, max_value FROM item_effects"):
        templates[item_id][effect] = (low, high)
    rows, mine = [], set()
    for ts, uid, character, *rest in market.execute(
        "SELECT ts, uid, character_id, item_id, rune_id, passed, pool, pool_change, pool_before, before, after FROM fm_passes "
        "WHERE kind = 'rune' AND before IS NOT NULL AND after IS NOT NULL ORDER BY ts, source_id"
    ):
        mine.add(fm_key("fm", uid, ts, rest[1]))
        rows.append((ts, "moi", object_token(uid), character, *rest))
    for key, at, *rest in market.execute(
        "SELECT key, at, object, item_id, rune_id, passed, pool, pool_change, pool_before, before, after "
        "FROM shared_fm_passes WHERE kind = 'rune' ORDER BY at, key"
    ):
        if key not in mine:  # nos propres passages revenus par le hub (après un changement de jeton)
            rows.append((at, "partage", rest[0], None, *rest[1:]))
    rows.sort(key=lambda row: row[0])
    out: list[Pass] = []
    for ts, source, obj, character, item_id, rune_id, passed, pool_after, pool_change, pool_before, raw_before, raw_after in rows:
        before = {e: v for e, v in json.loads(raw_before) if v is not None}
        after = {e: v for e, v in json.loads(raw_after) if v is not None}
        rune = templates.get(rune_id, {})
        rune_effect, (rune_value, _) = next(iter(rune.items())) if len(rune) == 1 else (None, (None, None))
        base = templates.get(item_id, {})
        low, high = base.get(rune_effect, (None, None)) if rune_effect is not None else (None, None)
        line_before, line_after = before.get(rune_effect, 0), after.get(rune_effect, 0)
        lost = {e: v - after.get(e, 0) for e, v in before.items() if e != rune_effect and v > 0 and after.get(e, 0) < v}
        paid = lost or pool_change == POOL_DOWN
        outcome = EC if not passed else (SN if paid else SC)
        level, type_name = items.get(item_id, (None, ""))
        out.append(Pass(
            ts=ts, source=source, object=obj, item_id=item_id, item_level=level, item_type=type_name, rune_id=rune_id,
            rune_effect=rune_effect, rune_value=rune_value,
            rune_weight=round(rune_value * density.get(rune_effect, 0.0), 3) if rune_effect is not None and rune_value is not None else None,
            line_before=line_before, line_after=line_after, line_min=low, line_max=high, line_kind=line_kind(line_before, low, high) if rune_effect is not None and base else "?",
            line_weight_before=round(line_before * density.get(rune_effect, 0.0), 3) if rune_effect is not None else 0.0,
            item_weight_before=round(weight_of(before, density), 3),
            item_weight_max=round(sum(h * density.get(e, 0.0) for e, (_, h) in base.items() if h > 0), 3),
            pool_before=pool_before, pool_after=pool_after, pool_change=pool_change, passed=passed, outcome=outcome,
            lost_weight=round(sum(points * density.get(e, 0.0) for e, points in lost.items()), 3),
            lost_lines=json.dumps(lost, separators=(",", ":")), before_known=1,
            extra_before=round(extra_weight(before, base, density), 3), extra_after=round(extra_weight(after, base, density), 3),
            line_max_weight=round(high * density.get(rune_effect, 0.0), 3) if high else None,
            exo_lost=int(any(e not in base for e in lost)), character_id=character,
            has_exo=int(any(e not in base and e != rune_effect and v > 0 for e, v in before.items())),
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
    rows = build(market, densities())
    write(rows, args.out)
    print(f"{len(rows)} passages écrits dans {args.out}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
