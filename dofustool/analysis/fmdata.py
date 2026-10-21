"""Jeu de données de forgemagie : chaque passage de rune capté, avec tout ce qui peut peser sur son résultat.

But : retrouver par la mesure comment le jeu tire le résultat d'un passage (succès critique, neutre,
échec) et ce qu'il retire, pour un simulateur calé sur de vrais passages. Ankama n'a jamais publié
la formule ; le calcul se fait sur le serveur, la capture n'en voit que les résultats.

Chaque passage du journal (table fm_passes) garde l'objet juste avant et juste après, et le puits
d'avant. On y joint la rune, la ligne visée, ses bornes de base et les poids. Seules les runes de
forgemagie entrent : potions, éclats, orbes et transcendances ne ratent jamais.

Un succès est neutre dès que l'objet a payé quelque chose, avec ses lignes ou avec son puits ;
critique seulement s'il n'a rien payé.

Une ligne négative (« -7 % critique ») est un effet à part, le malus, que le jeu fait baisser quand on
passe la rune de la caractéristique. Ces passages sont classés « malus », la ligne lue avec son signe
(-7 → -6) ; la baisse du malus n'est pas une perte. La rune n'y pèse que le poids du malus, la moitié
de son poids habituel, tant que la ligne reste négative ; pour l'amener à 0 (de -1 à 0), son poids
normal (vu sur le puits).

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


@dataclass(slots=True)
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
    line_kind: str  # « exo », « over » (au-delà du max), « max », « haut » (80 à 100 % du max), « bas », « malus » ; « ? » sans fiche
    line_weight_before: float  # poids de la ligne visée avant le passage
    item_weight_before: float  # poids de toutes les lignes positives avant le passage
    item_weight_max: float  # poids des lignes de base à leur jet max
    pool_before: float | None  # puits avant (connu à partir du deuxième passage de la séance)
    pool_after: float
    pool_change: int
    passed: int
    outcome: str  # sc, sn, ec
    lost_weight: float  # poids retiré aux autres lignes
    lost_lines: dict  # {caractéristique: points retirés} (en JSON dans le fichier écrit)
    before_known: int  # l'état d'avant a été vu (1) ou manque (0)
    extra_before: float = 0.0  # poids over et exo de tout l'objet avant le passage (au-delà des max, et lignes exo)
    extra_after: float = 0.0
    line_max_weight: float | None = None  # poids de la ligne visée à son max de base ; None pour un exo
    exo_lost: int = 0  # une ligne exo (autre que la visée) a perdu des points
    has_exo: int = 0  # l'objet portait un exo (autre que la ligne visée) avant le passage
    rune_tier: str = ""  # taille de la rune : « simple », « Pa » ou « Ra »
    game_version: str | None = None  # version du jeu au moment du passage, si elle est connue
    rune_weight_normal: float | None = None  # poids habituel de la rune (sur un malus, rune_weight peut en être la moitié)
    rune_weight_malus: float | None = None  # sur un malus : poids d'autant de points de malus que la rune en retire
    character_id: int | None = None  # personnage, pour les passages captés sur ce PC


def densities(path: Path = STATIC_PATH) -> dict[int, float]:
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {row[0]: float(row[1]) for row in source.execute("SELECT id, effectPowerRate FROM EffectData") if row[1]}
    finally:
        source.close()


def malus_of(path: Path = STATIC_PATH) -> dict[int, int]:
    """Effet malus de chaque caractéristique (« Force » → « -Force »), d'après les données du jeu."""
    try:
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        bonus_type = {row[0]: row[1] for row in source.execute("SELECT id, bonusType FROM EffectData")}
        return {effect: opposite for effect, opposite in source.execute("SELECT id, oppositeId FROM EffectData WHERE oppositeId")
                if bonus_type.get(effect) == 1 and bonus_type.get(opposite) == -1}  # fmt: skip
    except sqlite3.Error:
        return {}
    finally:
        source.close()


def rune_tier(name: str) -> str:
    """Taille d'une rune d'après son nom : « Rune Pa Fo » est une Pa, « Rune Ra Fo » une Ra, « Rune Fo » une simple."""
    words = name.split()
    return words[1] if len(words) > 2 and words[0] == "Rune" and words[1] in ("Pa", "Ra") else "simple"


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


def build(market: sqlite3.Connection, density: dict[int, float], malus: dict[int, int] | None = None) -> list[Pass]:
    malus = malus_of() if malus is None else malus
    items = {row[0]: (row[1], row[2] or "") for row in market.execute("SELECT id, level, type_name FROM items")}
    tiers = {row[0]: rune_tier(row[1]) for row in market.execute("SELECT id, name FROM items WHERE type_name = 'Rune de forgemagie'")}
    templates: dict[int, dict[int, tuple[int, int]]] = defaultdict(dict)
    for item_id, effect, low, high in market.execute("SELECT item_id, effect_id, min_value, max_value FROM item_effects"):
        templates[item_id][effect] = (low, high)
    rows, mine = [], set()
    for source_id, ts, uid, character, version, *rest in market.execute(
        "SELECT source_id, ts, uid, character_id, game_version, item_id, rune_id, passed, pool, pool_change, pool_before, before, after "
        "FROM fm_passes WHERE kind = 'rune' AND before IS NOT NULL AND after IS NOT NULL ORDER BY ts, source_id"
    ):
        mine.add(fm_key("fm", uid, ts, rest[1]))
        rows.append(((ts, 0, source_id), "moi", object_token(uid), character, version, *rest))
    for key, at, seq, version, *rest in market.execute(
        "SELECT key, at, seq, game_version, object, item_id, rune_id, passed, pool, pool_change, pool_before, before, after "
        "FROM shared_fm_passes WHERE kind = 'rune' ORDER BY at, key"
    ):
        if key not in mine:  # nos propres passages revenus par le hub (après un changement de jeton)
            # À la minute près, l'ordre d'un même exemplaire se retrouve par son rang quand il est connu.
            rows.append(((at, 1, seq or 0), "partage", rest[0], None, version, *rest[1:]))
    rows.sort(key=lambda row: row[0])
    # Ce qui ne dépend que de l'objet, de la rune ou du texte d'un état est calculé une fois : un même
    # modèle revient des milliers de fois, et l'état d'après d'un passage est l'état d'avant du suivant.
    parsed: dict[str, dict[int, int]] = {}
    per_item: dict[int, tuple] = {}
    per_rune: dict[int, tuple] = {}

    def effects_of(raw: str) -> dict[int, int]:
        found = parsed.get(raw)
        if found is None:
            found = parsed[raw] = {e: v for e, v in json.loads(raw) if v is not None}
        return found

    out: list[Pass] = []
    for order, source, obj, character, version, item_id, rune_id, passed, pool_after, pool_change, pool_before, raw_before, raw_after in rows:
        ts = order[0]
        before, after = effects_of(raw_before), effects_of(raw_after)
        if rune_id not in per_rune:
            rune = templates.get(rune_id, {})
            effect, (value, _) = next(iter(rune.items())) if len(rune) == 1 else (None, (None, None))
            weight = round(value * density.get(effect, 0.0), 3) if effect is not None and value is not None else None
            per_rune[rune_id] = (effect, value, weight, density.get(effect, 0.0) if effect is not None else 0.0, tiers.get(rune_id, ""))
        rune_effect, rune_value, rune_weight, unit, tier = per_rune[rune_id]
        if item_id not in per_item:
            base = templates.get(item_id, {})
            level, type_name = items.get(item_id, (None, ""))
            per_item[item_id] = (base, level, type_name, round(sum(h * density.get(e, 0.0) for e, (_, h) in base.items() if h > 0), 3))
        base, level, type_name, weight_max = per_item[item_id]
        low, high = base.get(rune_effect, (None, None)) if rune_effect is not None else (None, None)
        line_before, line_after = before.get(rune_effect, 0), after.get(rune_effect, 0)
        negative = malus.get(rune_effect)
        on_malus = negative is not None and (before.get(negative, 0) > 0 or (rune_effect not in base and negative in base))
        if on_malus:
            # La ligne visée est le malus : lue avec son signe, bornes de la fiche (déjà négatives chez DofusDB).
            low, high = base.get(negative, (None, None))
            line_before = before.get(rune_effect, 0) - before.get(negative, 0)
            line_after = after.get(rune_effect, 0) - after.get(negative, 0)
        target = {rune_effect, negative} if on_malus else {rune_effect}
        # Sur un malus, la rune ne pèse que le poids d'un point de malus (la moitié de son poids habituel) tant que
        # la ligne reste négative ; pour l'amener à 0 ou au-delà, son poids normal.
        malus_weight = round(rune_value * abs(density.get(negative, 0.0)), 3) if on_malus and rune_value is not None else None
        weight = malus_weight if malus_weight is not None and line_before + rune_value < 0 else rune_weight
        line_unit = abs(density.get(negative, 0.0)) if on_malus else unit
        lost = {e: v - after.get(e, 0) for e, v in before.items() if e not in target and v > 0 and after.get(e, 0) < v}
        paid = lost or pool_change == POOL_DOWN
        outcome = EC if not passed else (SN if paid else SC)
        out.append(Pass(
            ts=ts, source=source, object=obj, item_id=item_id, item_level=level, item_type=type_name, rune_id=rune_id,
            rune_effect=rune_effect, rune_value=rune_value, rune_weight=weight,
            line_before=line_before, line_after=line_after, line_min=low, line_max=high, line_kind="malus" if on_malus else line_kind(line_before, low, high) if rune_effect is not None and base else "?",
            line_weight_before=round(line_before * line_unit, 3) if rune_effect is not None else 0.0,
            item_weight_before=round(weight_of(before, density), 3), item_weight_max=weight_max,
            pool_before=pool_before, pool_after=pool_after, pool_change=pool_change, passed=passed, outcome=outcome,
            lost_weight=round(sum(points * density.get(e, 0.0) for e, points in lost.items()), 3) if lost else 0.0,
            lost_lines=lost, before_known=1,
            extra_before=round(extra_weight(before, base, density), 3), extra_after=round(extra_weight(after, base, density), 3),
            line_max_weight=round(high * unit, 3) if high and not on_malus else None,
            exo_lost=int(any(e not in base for e in lost)) if lost else 0, character_id=character,
            has_exo=int(any(e not in base and e != rune_effect and v > 0 for e, v in before.items())), rune_tier=tier, game_version=version,
            rune_weight_normal=rune_weight, rune_weight_malus=malus_weight,
        ))  # fmt: skip
    return out


def write(rows: list[Pass], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Pass.__dataclass_fields__))
        writer.writeheader()
        writer.writerows({**asdict(row), "lost_lines": json.dumps(row.lost_lines, separators=(",", ":"))} for row in rows)


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
