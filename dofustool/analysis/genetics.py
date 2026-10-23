"""Génétique des dragodindes : le chemin d'accouplements qui mène à une couleur, à partir de l'étable.

L'arbre vient des données du jeu (RidesData) : chaque couleur a sa génération et un seul couple de couleurs
parentes. Pour une couleur visée, on remonte l'arbre jusqu'aux couleurs que l'on possède (une monture
non stérile de cette couleur) ; chaque nœud qui manque devient une étape : accoupler un mâle d'une couleur
parente et une femelle de l'autre.

Les chances n'y sont pas encore : seule la génération cible a une règle connue, la répartition du reste
est en cours d'étude (voir les aperçus d'accouplement). Le chemin ne compte qu'un accouplement par étape.
"""
import json
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


@dataclass(frozen=True)
class Owned:
    uid: str
    color: int
    male: bool
    level: int
    sterile: bool
    gauges: tuple[int, int, int]  # amour, maturité, endurance
    place: int  # 0 : étable, sinon numéro de l'enclos

    @property
    def fecund(self) -> bool:
        return not self.sterile and min(self.gauges) >= GAUGE_MAX


def owned(conn: sqlite3.Connection) -> dict[int, list[Owned]]:
    """Montures non stériles par couleur : celles qui peuvent encore servir à un accouplement."""
    out: dict[int, list[Owned]] = {}
    for uid, color, male, level, sterile, love, maturity, stamina, place in conn.execute(
        "SELECT uid, color, male, level, sterile, love, maturity, stamina, place FROM mounts WHERE sterile = 0"
    ):
        out.setdefault(color, []).append(Owned(uid, color, bool(male), level, bool(sterile), (love, maturity, stamina), place))
    for mounts in out.values():
        mounts.sort(key=lambda m: (not m.fecund, -min(m.gauges), -m.level))
    return out


def plan(tree: Tree, have: dict[int, list[Owned]], target: int) -> dict | None:
    """Le chemin vers une couleur : les étapes à faire, de la plus basse génération à la cible.

    Une couleur que l'on possède n'est pas à produire ; chaque monture ne sert qu'une fois dans le chemin
    (une couleur demandée deux fois en demande deux). Une couleur sauvage que l'on n'a pas est à capturer."""
    if target not in tree.colors:
        return None
    spare = {color: list(mounts) for color, mounts in have.items()}
    steps: list[dict] = []
    capture: list[int] = []

    def need(color: int) -> dict:
        """Une monture de cette couleur pour l'étape du dessus : prise dans l'étable, ou produite par une étape."""
        if spare.get(color):
            return {"color": color, "owned": True, "mount": spare[color].pop(0).uid}
        parents = tree.colors[color].parents
        if parents is None:
            capture.append(color)
            return {"color": color, "owned": False, "capture": True}
        sides = [need(parents[0]), need(parents[1])]
        steps.append({"id": len(steps), "child": color, "generation": tree.colors[color].generation, "parents": sides})
        return {"color": color, "owned": False, "step": len(steps) - 1}

    if spare.get(target):
        return {"target": target, "owned": True, "steps": [], "capture": []}
    root = need(target)
    return {"target": target, "owned": False, "root": root, "steps": steps, "capture": sorted(set(capture))}


def candidates(have: dict[int, list[Owned]], color: int) -> list[dict]:
    """Les montures que l'on peut choisir pour un côté d'une étape : sexe, niveau, état, ce qui manque."""
    out = []
    for m in have.get(color, []):
        missing = {name: GAUGE_MAX - value for name, value in zip(("amour", "maturité", "endurance"), m.gauges) if value < GAUGE_MAX}
        out.append({"uid": m.uid, "male": m.male, "level": m.level, "fecund": m.fecund, "missing": missing, "place": m.place})
    return out


def overview(conn: sqlite3.Connection, tree: Tree, target: int | None, fetch_names: bool = True) -> dict:
    """Ce que montre l'onglet Génétique : toutes les couleurs (avec ce qu'on en possède) et, pour la cible, le chemin."""
    label = names(conn, fetch_names)
    have = owned(conn)
    colors = [{
        "id": c.id, "name": label.get(c.id, f"Couleur {c.id}"), "generation": c.generation, "parents": list(c.parents) if c.parents else None,
        "tint": list(c.tint), "weight": c.weight,
        "owned": len(have.get(c.id, [])), "fecund": sum(m.fecund for m in have.get(c.id, [])),
        "male": sum(m.male for m in have.get(c.id, [])), "female": sum(not m.male for m in have.get(c.id, [])),
    } for c in sorted(tree.colors.values(), key=lambda c: (c.generation, c.id))]  # fmt: skip
    route = plan(tree, have, target) if target is not None else None
    if route is not None:
        route.update(summarize(route, have))
    return {"colors": colors, "plan": route}


def pair_state(a: list[dict], b: list[dict]) -> str:
    """« ready » : un mâle et une femelle féconds, un de chaque couleur ; « prepare » : un tel couple existe, jauges à remplir ;
    « sex » : pas de sexes opposés entre les deux couleurs ; « waiting » : une des couleurs manque."""
    if not a or not b:
        return "waiting"
    pairs = [(x, y) for x in a for y in b if x["male"] != y["male"] and x["uid"] != y["uid"]]
    if not pairs:
        return "sex"
    return "ready" if any(x["fecund"] and y["fecund"] for x, y in pairs) else "prepare"


def summarize(route: dict, have: dict[int, list[Owned]]) -> dict:
    """Le chemin regroupé par couleur à produire (une ligne par couleur, avec son nombre d'accouplements), et le schéma :
    chaque couleur du chemin avec ce qu'il faut en produire, en prendre dans l'étable, en capturer."""
    rows: dict[int, dict] = {}
    nodes: dict[int, dict] = {}
    edges: set[tuple[int, int]] = set()

    def node(color: int) -> dict:
        return nodes.setdefault(color, {"color": color, "make": 0, "use": 0, "capture": 0})

    for step in route["steps"]:
        child = step["child"]
        a, b = step["parents"]
        row = rows.setdefault(child, {"child": child, "generation": step["generation"], "parents": [a["color"], b["color"]], "count": 0, "from_owned": 0})
        row["count"] += 1
        row["from_owned"] += a["owned"] and b["owned"]
        node(child)["make"] += 1
        for side in (a, b):
            edges.add((side["color"], child))
            if side["owned"]:
                node(side["color"])["use"] += 1
            elif side.get("capture"):
                node(side["color"])["capture"] += 1
    for row in rows.values():
        sides = [candidates(have, color) for color in row["parents"]]
        row["candidates"] = sides
        row["status"] = pair_state(*sides) if row["from_owned"] else "waiting"
    ordered = sorted(rows.values(), key=lambda r: (r["generation"], -r["count"]))
    capture = {color: n["capture"] for color, n in nodes.items() if n["capture"]}
    return {"matings": len(route["steps"]), "rows": ordered, "nodes": list(nodes.values()), "edges": sorted(edges), "capture_counts": capture,
            "ready": sum(r["from_owned"] for r in ordered if r["status"] == "ready")}
