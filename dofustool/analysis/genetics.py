"""Génétique des dragodindes : le chemin d'accouplements qui mène à une couleur, à partir de l'étable.

L'arbre vient des données du jeu (RidesData) : chaque couleur a sa génération et un seul couple de couleurs
parentes. Pour une couleur visée, on remonte l'arbre jusqu'aux couleurs que l'on possède (une monture
non stérile de cette couleur) ; chaque nœud qui manque devient une étape : accoupler un mâle d'une couleur
parente et une femelle de l'autre.

Les chances d'un accouplement (formule retrouvée sur 40 aperçus du jeu, exacte à l'arrondi près) :

  1. chaque parent forme un « côté » : sa couleur compte 5, chacun de ses deux parents 3, fois leur poids génétique
     (90 ou 20) ; chaque côté pèse 1 au total (une monture capturée compte entièrement pour sa couleur) ;
  2. chaque couple de couleurs (une de chaque côté) qui a un enfant dans l'arbre donne à cet enfant le produit
     de leurs poids ;
  3. score d'une couleur = son poids des deux côtés + ce que lui donnent les couples. La génération cible est la
     plus haute génération des enfants ; ses couleurs se partagent 30 % + 0,15 % par niveau des deux parents
     (+ 20 % avec une Optimakina, plafonné à 100 %), les autres le reste, au prorata du score.

Le chemin compte, pour chaque couleur à produire, les réussites nécessaires et les essais moyens (réussites ÷
chance). Un raté n'est pas compté comme perdu : son bébé a presque toujours la couleur d'un parent ou d'un
grand-parent et resservira, et les deux parents devenus stériles se clonent en une monture fertile.
"""
import json
from collections import defaultdict
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .breeding import GAUGE_MAX

STATIC_PATH = Path(__file__).resolve().parents[2] / "data" / "static" / "dofus.sqlite"
DRAGODINDE = 1  # espèce des dragodindes dans RidesData
NAMES_URL = "https://api.dofusdb.fr/mounts?familyId=1&$limit=50&$skip={skip}&$select[]=id&$select[]=name"


@dataclass(frozen=True)
class Color:
    id: int
    generation: int
    parents: tuple[int, int] | None  # couple de couleurs qui la donne ; None pour une couleur sauvage
    weight: int  # poids génétique (90 ou 20)
    tint: tuple[str, str]  # deux teintes de la robe, pour l'affichage (#rrggbb)


@dataclass
class Tree:
    colors: dict[int, Color]
    children: dict[tuple[int, int], int] = field(default_factory=dict)  # couple (trié) → couleur obtenue

    def child(self, a: int, b: int) -> int | None:
        return self.children.get(tuple(sorted((a, b))))


def _tint(look: str) -> tuple[str, str]:
    """Les deux premières couleurs de l'apparence du jeu (« {639||1=16747520,2=16747520,…} »)."""
    try:
        parts = dict(item.split("=") for item in look.split("||")[1].split("|")[0].split(","))
        return tuple(f"#{int(parts[k]):06x}" for k in ("1", "2"))  # type: ignore[return-value]
    except (IndexError, KeyError, ValueError):
        return ("#9aa8a6", "#9aa8a6")


def load_tree(path: Path = STATIC_PATH) -> Tree:
    """L'arbre des dragodindes, depuis les données du jeu ; vide si elles manquent."""
    try:
        static = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return Tree({})
    try:
        rows = static.execute(
            "SELECT r.id, r.generation, r.parents, r.geneticWeight, m.look FROM RidesData r JOIN MountData m ON m.id = r.id "
            "WHERE r.speciesId = ? AND r.generation >= 1", (DRAGODINDE,),
        ).fetchall()  # fmt: skip
    except sqlite3.Error:
        return Tree({})
    finally:
        static.close()
    tree = Tree({})
    for ride_id, generation, parents, weight, look in rows:
        pair = next(((p["parent1"], p["parent2"]) for p in json.loads(parents or "[]")), None)
        tree.colors[ride_id] = Color(ride_id, generation, pair, weight, _tint(look))
        if pair:
            tree.children[tuple(sorted(pair))] = ride_id
    return tree


def names(conn: sqlite3.Connection, fetch: bool = True) -> dict[int, str]:
    """Noms français des dragodindes (« Amande et Rousse »), gardés en base ; demandés une fois à DofusDB."""
    known = dict(conn.execute("SELECT id, name FROM ride_names"))
    if known or not fetch:
        return known
    found: dict[int, str] = {}
    try:
        for skip in (0, 50):
            request = urllib.request.Request(NAMES_URL.format(skip=skip), headers={"User-Agent": "dofustool"})
            with urllib.request.urlopen(request, timeout=10) as response:
                for mount in json.loads(response.read()).get("data", []):
                    name = (mount.get("name") or {}).get("fr", "")
                    found[int(mount["id"])] = name.removeprefix("Dragodinde ").strip() or name
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
        return {}
    with conn:
        conn.executemany("INSERT OR REPLACE INTO ride_names VALUES (?, ?)", found.items())
    return found


TARGET_BASE, PER_LEVEL, OPTIMAKINA = 30.0, 0.15, 20.0
PARENT, GRANDPARENT = 5, 3  # poids d'une couleur dans un côté : le parent, chacun de ses parents
BABY_LEVEL = 1  # une monture à produire naît au niveau 1


@dataclass(frozen=True)
class Owned:
    uid: str
    color: int
    male: bool
    level: int
    sterile: bool
    gauges: tuple[int, int, int]  # amour, maturité, endurance
    place: int  # 0 : étable, sinon numéro de l'enclos
    parents: tuple[int, ...] = ()  # couleurs de ses parents (vide : capturée)

    @property
    def fecund(self) -> bool:
        return not self.sterile and min(self.gauges) >= GAUGE_MAX


def owned(conn: sqlite3.Connection) -> dict[int, list[Owned]]:
    """Montures non stériles par couleur : celles qui peuvent encore servir à un accouplement."""
    out: dict[int, list[Owned]] = {}
    for uid, color, male, level, sterile, love, maturity, stamina, place, parents in conn.execute(
        "SELECT uid, color, male, level, sterile, love, maturity, stamina, place, parents FROM mounts WHERE sterile = 0"
    ):
        out.setdefault(color, []).append(
            Owned(uid, color, bool(male), level, bool(sterile), (love, maturity, stamina), place, tuple(json.loads(parents or "[]")))
        )
    for mounts in out.values():
        mounts.sort(key=lambda m: (not m.fecund, -min(m.gauges), -m.level))
    return out


Sheet = tuple[int, tuple[int, ...], int]  # (couleur, couleurs de ses parents, niveau)


def side(tree: Tree, color: int, parents: tuple[int, ...]) -> dict[int, float]:
    """Le côté d'un parent : sa couleur 5, ses parents 3 chacun, fois le poids génétique ; total 1."""
    weights: dict[int, float] = defaultdict(float)
    for c, position in [(color, PARENT)] + [(p, GRANDPARENT) for p in parents]:
        weights[c] += position * (tree.colors[c].weight if c in tree.colors else 1)
    total = sum(weights.values())
    return {c: w / total for c, w in weights.items()}


def chances(tree: Tree, a: Sheet, b: Sheet, optimakina: bool = False) -> dict[int, float]:
    """Chances (en %) de chaque couleur pour le bébé de ces deux parents, comme l'aperçu du jeu."""
    one, two = side(tree, a[0], a[1]), side(tree, b[0], b[1])
    score: dict[int, float] = defaultdict(float)
    children: dict[int, float] = defaultdict(float)
    for x, px in one.items():
        for y, py in two.items():
            child = tree.child(x, y) if x != y else None
            if child is not None:
                children[child] += px * py
    for weights in (one, two, children):
        for c, w in weights.items():
            score[c] += w

    def gen(c: int) -> int:
        return tree.colors[c].generation if c in tree.colors else 0

    top = max((gen(c) for c in children), default=None)
    target = min(100.0, TARGET_BASE + PER_LEVEL * (a[2] + b[2]) + (OPTIMAKINA if optimakina else 0.0)) if top is not None else 0.0
    high = {c: w for c, w in score.items() if top is not None and gen(c) >= top}
    low = {c: w for c, w in score.items() if top is None or gen(c) < top}
    out = {c: target * w / sum(high.values()) for c, w in high.items()} if high else {}
    rest = 100.0 - (target if high else 0.0)
    for c, w in low.items():
        out[c] = rest * w / sum(low.values())
    return out


def produced(tree: Tree, color: int) -> Sheet:
    """Une monture de cette couleur que le chemin fera naître : au niveau 1, issue du couple qui la donne."""
    parents = tree.colors[color].parents
    return (color, tuple(parents) if parents else (), BABY_LEVEL)


def best_pair(tree: Tree, have: dict[int, list[Owned]], child: int, optimakina: bool = False, limit: int = 40) -> dict:
    """Le meilleur couple de l'étable pour obtenir cette couleur : un mâle et une femelle des deux couleurs parentes,
    la plus forte chance (puis féconds, puis plus hauts niveaux). Une couleur parente que l'on n'a pas est prise
    comme une monture à produire (niveau 1)."""
    a_color, b_color = tree.colors[child].parents
    side_a = have.get(a_color, [])[:limit]
    side_b = have.get(b_color, [])[:limit]
    best = None
    for x in side_a or [None]:
        for y in side_b or [None]:
            if x is not None and y is not None and (x.male == y.male or x.uid == y.uid):
                continue
            sa = (x.color, x.parents, x.level) if x else produced(tree, a_color)
            sb = (y.color, y.parents, y.level) if y else produced(tree, b_color)
            chance = chances(tree, sa, sb, optimakina).get(child, 0.0)
            key = (round(chance, 6), bool(x and x.fecund) + bool(y and y.fecund), (x.level if x else 0) + (y.level if y else 0))
            if best is None or key > best[0]:
                best = (key, chance, x, y)
    if best is None:  # les deux couleurs sont là, mais toutes du même sexe : on compte des montures à produire
        chance = chances(tree, produced(tree, a_color), produced(tree, b_color), optimakina).get(child, 0.0)
        return {"chance": chance, "a": None, "b": None}
    _, chance, x, y = best
    return {"chance": chance, "a": x.uid if x else None, "b": y.uid if y else None}


def route(tree: Tree, have: dict[int, list[Owned]], target: int, optimakina: bool = False) -> dict | None:
    """Le chemin vers une couleur : pour chaque couleur à produire, les réussites nécessaires et les essais moyens
    (réussites ÷ chance avec le meilleur couple). Chaque réussite demande un parent de chaque couleur parente, pris dans
    l'étable tant qu'il en reste (chaque monture ne sert qu'une fois), sinon à produire à son tour, ou à capturer pour une
    couleur sauvage. Les ratés sont supposés recyclés (bébé réutilisé, parents clonés) : voir le haut du module."""
    if target not in tree.colors:
        return None
    left = {c: len(ms) for c, ms in have.items()}
    if left.get(target):
        return {"target": target, "owned": True, "rows": [], "nodes": [], "edges": [], "capture_counts": {}, "matings": 0, "ready": 0}
    need: dict[int, float] = defaultdict(float)
    need[target] = 1.0
    pending = {target}
    use: dict[int, float] = defaultdict(float)
    capture: dict[int, float] = defaultdict(float)
    rows, edges = [], set()
    while pending:
        color = max(pending, key=lambda c: tree.colors[c].generation)  # tout ce qui la demande est déjà compté
        pending.discard(color)
        take = min(need[color], left.get(color, 0))
        left[color] = left.get(color, 0) - take
        use[color] += take
        missing = need[color] - take
        if missing <= 1e-9:
            continue
        parents = tree.colors[color].parents
        if parents is None:
            capture[color] += missing
            continue
        pair = best_pair(tree, have, color, optimakina)
        tries = missing / (max(pair["chance"], 0.01) / 100)
        rows.append({"child": color, "generation": tree.colors[color].generation, "parents": list(parents), "needed": missing,
                     "tries": tries, "chance": pair["chance"], "best": [pair["a"], pair["b"]]})  # fmt: skip
        for p in parents:
            need[p] += missing
            pending.add(p)
            edges.add((p, color))
    nodes = {c: {"color": c, "make": 0.0, "use": use.get(c, 0.0), "capture": capture.get(c, 0.0)} for c in set(use) | set(capture)}
    for row in rows:
        nodes.setdefault(row["child"], {"color": row["child"], "make": 0.0, "use": 0.0, "capture": 0.0})["make"] = row["tries"]
    rows.sort(key=lambda r: (r["generation"], -r["tries"]))
    for row in rows:
        sides = [candidates(have, c) for c in row["parents"]]
        row["candidates"] = sides
        row["status"] = pair_state(*sides)
    return {
        "target": target, "owned": False, "rows": rows, "edges": sorted(edges),
        "nodes": [n for n in nodes.values() if n["make"] or n["use"] or n["capture"]],
        "capture_counts": {c: n for c, n in capture.items() if n > 1e-9},
        "matings": sum(r["tries"] for r in rows), "ready": sum(r["status"] == "ready" for r in rows),
    }  # fmt: skip


def candidates(have: dict[int, list[Owned]], color: int) -> list[dict]:
    """Les montures que l'on peut choisir pour un côté d'une étape : sexe, niveau, état, ce qui manque, ses parents."""
    out = []
    for m in have.get(color, []):
        missing = {name: GAUGE_MAX - value for name, value in zip(("amour", "maturité", "endurance"), m.gauges) if value < GAUGE_MAX}
        out.append({"uid": m.uid, "male": m.male, "level": m.level, "fecund": m.fecund, "missing": missing, "place": m.place,
                    "parents": list(m.parents)})  # fmt: skip
    return out


def pair_state(a: list[dict], b: list[dict]) -> str:
    """« ready » : un mâle et une femelle féconds, un de chaque couleur ; « prepare » : un tel couple existe, jauges à remplir ;
    « sex » : pas de sexes opposés entre les deux couleurs ; « waiting » : une des couleurs manque."""
    if not a or not b:
        return "waiting"
    pairs = [(x, y) for x in a for y in b if x["male"] != y["male"] and x["uid"] != y["uid"]]
    if not pairs:
        return "sex"
    return "ready" if any(x["fecund"] and y["fecund"] for x, y in pairs) else "prepare"


def overview(conn: sqlite3.Connection, tree: Tree, target: int | None, fetch_names: bool = True, optimakina: bool = False) -> dict:
    """Ce que montre l'onglet Génétique : toutes les couleurs (avec ce qu'on en possède) et, pour la cible, le chemin."""
    label = names(conn, fetch_names)
    have = owned(conn)
    colors = [{
        "id": c.id, "name": label.get(c.id, f"Couleur {c.id}"), "generation": c.generation, "parents": list(c.parents) if c.parents else None,
        "tint": list(c.tint), "weight": c.weight,
        "owned": len(have.get(c.id, [])), "fecund": sum(m.fecund for m in have.get(c.id, [])),
        "male": sum(m.male for m in have.get(c.id, [])), "female": sum(not m.male for m in have.get(c.id, [])),
    } for c in sorted(tree.colors.values(), key=lambda c: (c.generation, c.id))]  # fmt: skip
    return {"colors": colors, "plan": route(tree, have, target, optimakina) if target is not None else None, "optimakina": optimakina}
