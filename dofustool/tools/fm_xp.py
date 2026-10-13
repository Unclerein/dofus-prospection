"""XP des métiers de forgemagie : mesurée passage par passage dans l'archive, puis comparée à des formules.

Pour chaque rune passée, l'archive garde l'objet avant et après (donc le gain réel de la rune) et,
juste après, la mise à jour de l'XP du métier. Le poids d'une rune vaut la densité de sa
caractéristique (EffectData.effectPowerRate, dans les données du jeu) multipliée par son gain.

N'affiche que des nombres et des noms d'objets tirés des données du jeu. Écrit aussi data/fm_xp.csv.

Usage : python -m dofustool.tools.fm_xp [--since AAAA-MM-JJ]
"""
import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import db, identify
from ..archive import ARCHIVE_PATH
from ..messages import KEYMAP_PATH, RUNTIME_KEYMAP_PATH, Mapping, characters, fm
from ..protocol.tcp import S2C
from ..staticdata.source import DOFUS_SQLITE

NAMES = ("fm_object", "fm_result", "job_levels")
XP_WINDOW_S = 3.0  # une mise à jour de l'XP à moins de 3 s d'un passage lui est attribuée
SC, SN, EC = "SC", "SN", "EC"


@dataclass(slots=True)
class Pass:
    ts: float
    item_id: int
    rune_id: int
    result: str  # SC (passée, rien perdu), SN (passée, une autre ligne a baissé), EC (pas passée)
    gains: dict[int, int]  # caractéristique -> variation
    xp: int | None = None  # XP gagnée ; None : aucune mise à jour de l'XP vue
    job_id: int | None = None
    job_level: int | None = None
    effect_id: int | None = None  # caractéristique de la rune, déduite des passages réussis
    value: int | None = None  # gain de la rune
    weight: float | None = None  # densité × gain
    extra: dict = field(default_factory=dict)


def keymaps(data_dir: Path) -> dict[str, tuple[str, Mapping]]:
    """{clé: (nom, correspondance)} pour tous les builds connus : table livrée, table de ce PC et ses sauvegardes."""
    paths = [KEYMAP_PATH, RUNTIME_KEYMAP_PATH, *sorted((data_dir / "keymap-backups").glob("*.json"))]
    out: dict[str, tuple[str, Mapping]] = {}
    for path in paths:
        if not path.exists():
            continue
        try:
            raw = identify.read_keymap(path)
        except (OSError, ValueError):
            continue
        for name in NAMES:
            entry = raw.get(name)
            if entry and entry.get("key") and entry.get("fields"):
                out.setdefault(entry["key"], (name, Mapping(entry["key"], entry["fields"])))
    return out


def replay(archive: sqlite3.Connection, maps: dict[str, tuple[str, Mapping]], runes: set[int], since: float = 0.0) -> list[Pass]:
    """Rejoue les connexions de l'archive et renvoie les passages de rune, avec l'XP qui les a suivis."""
    passes: list[Pass] = []
    keys = sorted(maps)
    if not keys:
        return passes
    marks = ",".join("?" * len(keys))
    rows = archive.execute(
        f"SELECT connection_id, ts, key, body FROM messages WHERE direction = ? AND key IN ({marks}) AND ts >= ? "
        "ORDER BY connection_id, id",
        (S2C, *keys, since),
    )
    connection = None
    for connection_id, ts, key, body in rows:
        if connection_id != connection:
            connection, rune, objects, xp = connection_id, None, {}, {}
            pending: Pass | None = None  # passage en attente de son XP
            loose: tuple[float, int, int, int] | None = None  # XP arrivée avant son passage : (ts, métier, gain, niveau)
        name, mapping = maps[key]
        if name == "fm_object":
            placed = fm.parse_object(body, mapping)
            if placed is None:
                continue
            if placed.item_id in runes:
                rune = placed.item_id
            else:
                objects[placed.uid] = dict((e, v) for e, v in placed.effects if v is not None)
        elif name == "fm_result":
            result = fm.parse_result(body, mapping)
            used, rune = rune, None
            if result is None or not result.forged or used is None or result.object.item_id in runes:
                continue
            obj = result.object
            after = dict((e, v) for e, v in obj.effects if v is not None)
            before = objects.get(obj.uid)
            objects[obj.uid] = after
            if before is None:
                continue  # état d'avant inconnu : gain illisible
            gains = {e: after.get(e, 0) - before.get(e, 0) for e in set(before) | set(after) if after.get(e, 0) != before.get(e, 0)}
            lost = any(g < 0 for g in gains.values())
            pending = Pass(ts, obj.item_id, used, SC if result.passed and not lost else SN if result.passed else EC, gains)
            passes.append(pending)
            if loose is not None and ts - loose[0] <= XP_WINDOW_S:
                pending.xp, pending.job_id, pending.job_level = loose[2], loose[1], loose[3]
                pending, loose = None, None
        else:  # job_levels
            jobs = characters.parse_jobs(body, mapping)
            for job in jobs or ():
                gained = job.xp - xp[job.job_id] if job.job_id in xp else None
                xp[job.job_id] = job.xp
                if not gained or gained <= 0:
                    continue
                if pending is not None and pending.xp is None and ts - pending.ts <= XP_WINDOW_S:
                    pending.xp, pending.job_id, pending.job_level = gained, job.job_id, job.level
                    pending = None
                else:
                    loose = (ts, job.job_id, gained, job.level)
    return passes


def weigh(passes: list[Pass], density: dict[int, float]) -> dict[int, tuple[int, int]]:
    """Caractéristique et gain de chaque rune, d'après ses passages réussis ; pose le poids de chaque passage."""
    seen: dict[int, Counter] = defaultdict(Counter)
    for p in passes:
        if p.result != EC:
            ups = [(e, g) for e, g in p.gains.items() if g > 0]
            if len(ups) == 1:
                seen[p.rune_id][ups[0]] += 1
    runes = {rune_id: counter.most_common(1)[0][0] for rune_id, counter in seen.items()}
    for p in passes:
        if p.rune_id in runes:
            p.effect_id, p.value = runes[p.rune_id]
            rate = density.get(p.effect_id)
            p.weight = rate * p.value if rate else None
    return runes


def _fit(rows: list[tuple[float, ...]], ys: list[float]) -> tuple[list[float], float] | None:
    """Moindres carrés (sans dépendance) : coefficients et R²."""
    n, k = len(rows), len(rows[0]) if rows else 0
    if n <= k:
        return None
    a = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    b = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(k)]
    for col in range(k):  # élimination de Gauss
        pivot = max(range(col, k), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot], b[col], b[pivot] = a[pivot], a[col], b[pivot], b[col]
        for r in range(k):
            if r != col:
                factor = a[r][col] / a[col][col]
                a[r] = [x - factor * y for x, y in zip(a[r], a[col])]
                b[r] -= factor * b[col]
    coef = [b[i] / a[i][i] for i in range(k)]
    mean = sum(ys) / n
    total = sum((y - mean) ** 2 for y in ys)
    error = sum((y - sum(c * x for c, x in zip(coef, r))) ** 2 for r, y in zip(rows, ys))
    return coef, 1 - error / total if total else 1.0


def analyse(passes: list[Pass], levels: dict[int, int]) -> list[str]:
    """Résumé lisible : XP par résultat, puis plusieurs formules candidates avec leur ajustement."""
    out = []
    by_result = defaultdict(list)
    for p in passes:
        by_result[p.result].append(p)
    out.append(f"{len(passes)} passages lus.")
    for result in (SC, SN, EC):
        group = by_result[result]
        known = [p.xp for p in group if p.xp is not None]
        out.append(
            f"  {result} : {len(group)} passages, XP vue sur {len(known)}"
            + (f", médiane {sorted(known)[len(known) // 2]}, de {min(known)} à {max(known)}" if known else "")
        )
    usable = [p for p in passes if p.xp and p.weight and levels.get(p.item_id) and p.result != EC]
    out.append(f"{len(usable)} passages exploitables (XP, poids et niveau de l'objet connus).")
    if len(usable) < 5:
        out.append("Trop peu de passages pour ajuster une formule.")
        return out
    ys = [p.xp for p in usable]
    w = [p.weight for p in usable]
    lvl = [levels[p.item_id] for p in usable]
    job = [p.job_level or 0 for p in usable]
    sn = [1.0 if p.result == SN else 0.0 for p in usable]
    candidates = {
        "XP = a × poids": [(x,) for x in w],
        "XP = a × poids × niveau de l'objet": [(x * l,) for x, l in zip(w, lvl)],
        "XP = a × poids + b × niveau de l'objet": [(x, l) for x, l in zip(w, lvl)],
        "XP = a × poids × niveau + b × poids": [(x * l, x) for x, l in zip(w, lvl)],
        "XP = a × poids × niveau + b × poids × écart (métier − objet)": [(x * l, x * (j - l)) for x, l, j in zip(w, lvl, job)],
    }
    out.append("Formules candidates (R² proche de 1 = colle aux mesures) :")
    for label, rows in candidates.items():
        found = _fit(rows, ys)
        if found:
            coef, r2 = found
            out.append(f"  R² {r2:6.3f}  {label}  " + "  ".join(f"{'ab'[i]} = {c:.4g}" for i, c in enumerate(coef)))
    # Loi de puissance : log XP = c + p·log poids + q·log niveau + s·SN
    logs = [(1.0, math.log(x), math.log(l), s) for x, l, s in zip(w, lvl, sn)]
    found = _fit(logs, [math.log(y) for y in ys])
    if found:
        (c, p, q, s), r2 = found
        out.append(
            f"  R² {r2:6.3f}  XP = {math.exp(c):.4g} × poids^{p:.3f} × niveau^{q:.3f}"
            f"  (succès neutre : × {math.exp(s):.3f})  [ajusté en logarithmes]"
        )
    # Rapport XP / poids par niveau d'objet : montre à l'œil la dépendance au niveau.
    out.append("XP par point de poids, selon le niveau de l'objet (succès critiques) :")
    ratios = defaultdict(list)
    for p in usable:
        if p.result == SC:
            ratios[levels[p.item_id]].append(p.xp / p.weight)
    for level in sorted(ratios):
        values = sorted(ratios[level])
        out.append(f"  niveau {level:3d} : {len(values):3d} passages, médiane {values[len(values) // 2]:.3f}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", help="ne lire que les passages depuis cette date (AAAA-MM-JJ)")
    args = parser.parse_args()
    data_dir = db.MARKET_PATH.parent
    if not ARCHIVE_PATH.exists() or not db.MARKET_PATH.exists():
        print("Archive ou base introuvable : lance d'abord une capture.")
        return 1
    since = datetime.fromisoformat(args.since).timestamp() if args.since else 0.0
    market = db.connect()
    runes = {row[0] for row in market.execute(identify.RUNES_SQL)}
    levels = dict(market.execute("SELECT id, level FROM items"))
    names = dict(market.execute("SELECT id, name FROM items"))
    density: dict[int, float] = {}
    if DOFUS_SQLITE.exists():
        static = sqlite3.connect(f"file:{DOFUS_SQLITE}?mode=ro", uri=True)
        density = {e: r for e, r in static.execute("SELECT id, effectPowerRate FROM EffectData WHERE effectPowerRate > 0")}
        static.close()
    else:
        print("Données du jeu absentes (data/static/dofus.sqlite) : lance dofustool.staticdata.update pour avoir les densités.")
    archive = sqlite3.connect(f"file:{ARCHIVE_PATH}?mode=ro", uri=True)
    passes = replay(archive, keymaps(data_dir), runes, since)
    rune_stats = weigh(passes, density)
    print("Runes reconnues (caractéristique, gain, densité, poids) :")
    for rune_id, (effect_id, value) in sorted(rune_stats.items(), key=lambda kv: names.get(kv[0], "")):
        rate = density.get(effect_id)
        print(f"  {names.get(rune_id, rune_id)} : effet {effect_id}, +{value}, densité {rate}, poids {rate * value if rate else '?'}")
    for line in analyse(passes, levels):
        print(line)
    out = data_dir / "fm_xp.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["date", "objet", "niveau_objet", "rune", "resultat", "effet", "gain", "poids", "metier", "niveau_metier", "xp", "variations"])
        for p in passes:
            writer.writerow([
                datetime.fromtimestamp(p.ts).isoformat(timespec="seconds"), names.get(p.item_id, p.item_id), levels.get(p.item_id),
                names.get(p.rune_id, p.rune_id), p.result, p.effect_id, p.value, p.weight, p.job_id, p.job_level, p.xp,
                json.dumps(p.gains, sort_keys=True),
            ])  # fmt: skip
    print(f"Détail : {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
