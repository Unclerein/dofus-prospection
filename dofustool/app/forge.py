"""Données de la page Forgemagie : annonces HDV d'équipements lues à travers leurs caractéristiques.

Tous les prix sont des prix demandés à l'HDV, pas des ventes constatées.
"""
import json
import sqlite3
import statistics

import pandas as pd

from .. import db
from ..analysis.forgemagie import MARKERS, Classification, Filter, Template, base_lines, classify, perfect_filter
from ..analysis.prices import EQUIPMENT
from .data import Workspace, _local_dates

# Critères du classement général.
SAVED = "Mes critères par objet"
PERFECT = "Jets parfaits"
EXO = "Un exo"
OVER = "Un over"


def base_line_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """Caractéristiques de base rencontrées sur les équipements connus, avec le nombre d'objets qui les portent."""
    counts: dict[int, int] = {}
    for item_id in equipment_options(conn):
        template = load_template(conn, item_id)
        for effect_id in base_lines(template or {}):
            counts[effect_id] = counts.get(effect_id, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def effect_names(conn: sqlite3.Connection) -> dict[int, str]:
    return dict(conn.execute("SELECT id, name FROM effects"))


def equipment_options(conn: sqlite3.Connection) -> dict[int, str]:
    """Équipements dont on connaît des annonces, avec leur nombre."""
    rows = conn.execute(
        "SELECT i.id, i.name, i.level, COUNT(*) FROM hdv_current h JOIN items i ON i.id = h.item_id "
        "WHERE i.category_id = ? GROUP BY i.id ORDER BY i.name",
        (EQUIPMENT,),
    )
    return {item_id: f"{name} (niv. {level}) · {count} annonce(s)" for item_id, name, level, count in rows}


def load_template(conn: sqlite3.Connection, item_id: int) -> Template | None:
    """Caractéristiques de base de l'item, ou None si elles n'ont pas encore été récupérées."""
    if conn.execute("SELECT 1 FROM item_effects_fetched WHERE item_id = ?", (item_id,)).fetchone() is None:
        return None
    # Dans l'ordre d'une infobulle du jeu (priorité d'affichage de l'effet), pas par identifiant.
    # Seules les vraies caractéristiques : les lignes de dégâts d'une arme sont dans load_fixed_lines.
    return _template_lines(conn, item_id, stat=True)


def _template_lines(conn: sqlite3.Connection, item_id: int, stat: bool) -> Template:
    return {
        effect_id: (low, high)
        for effect_id, low, high in conn.execute(
            "SELECT e.effect_id, e.min_value, e.max_value FROM item_effects e "
            "LEFT JOIN effect_meta m ON m.effect_id = e.effect_id "
            "WHERE e.item_id = ? AND COALESCE(m.is_stat, 1) = ? ORDER BY COALESCE(m.priority, 1000000), e.effect_id",
            (item_id, int(stat)),
        )
    }


def load_fixed_lines(conn: sqlite3.Connection, item_id: int) -> Template:
    """Lignes de base qui ne se forgemagent pas (dégâts d'une arme, « Arme de chasse »…) : affichées telles quelles."""
    return _template_lines(conn, item_id, stat=False)


def non_stat_effects(conn: sqlite3.Connection) -> frozenset[int]:
    """Effets à ne jamais lire comme un exo, un over ou une ligne perdue. Les marqueurs restent lus à part."""
    return frozenset(row[0] for row in conn.execute("SELECT effect_id FROM effect_meta WHERE is_stat = 0")) - MARKERS


def effect_assets(conn: sqlite3.Connection) -> dict[int, str]:
    """Nom de l'image de la caractéristique de chaque effet (pour l'interface)."""
    return dict(conn.execute("SELECT effect_id, asset FROM effect_meta WHERE asset IS NOT NULL"))


def effect_priorities(conn: sqlite3.Connection) -> dict[int, int]:
    return dict(conn.execute("SELECT effect_id, priority FROM effect_meta"))


def _describe(ids: tuple[int, ...], values: dict[int, int], names: dict[int, str]) -> str:
    return ", ".join(f"{names.get(i, f'effet {i}')} {values.get(i, 0)}" for i in ids)


def read_listings(
    conn: sqlite3.Connection, item_id: int, template: Template, current: bool = True
) -> list[tuple[int, Classification, float, float]]:
    """Renvoie (prix, lecture de la forgemagie, première vue, dernière vue), du moins cher au plus cher."""
    table = "hdv_current" if current else "hdv_listings"
    rows = conn.execute(
        f"SELECT p1, effects, first_seen, captured_at FROM {table} WHERE item_id = ? AND p1 > 0 ORDER BY p1", (item_id,)
    )
    ignored = non_stat_effects(conn)
    return [
        (
            price,
            classify([tuple(e) for e in json.loads(effects) if e[0] not in ignored], template),
            first_seen or seen,
            seen,
        )
        for price, effects, first_seen, seen in rows.fetchall()
    ]


def own_listings(conn: sqlite3.Connection, item_id: int, template: Template) -> list[tuple[int, Classification]]:
    """Mes exemplaires en vente (prix, jets), relevés dans l'onglet Vendre de l'HDV."""
    ignored = non_stat_effects(conn)
    return [
        (price, classify([tuple(e) for e in json.loads(effects) if e[0] not in ignored], template))
        for price, effects in conn.execute(
            "SELECT price, effects FROM my_sales WHERE item_id = ? AND lot = 1 AND effects IS NOT NULL", (item_id,)
        )
    ]


def mark_own(listings: list[tuple[int, Classification, float, float]], own: list[tuple[int, Classification]]) -> list[bool]:
    """Pour chaque annonce, vrai si c'est l'un de mes exemplaires : même prix et mêmes jets.

    Les annonces de l'HDV n'ont pas le même identifiant que mes lots : c'est la seule façon de les reconnaître.
    Chacun de mes lots ne reconnaît qu'une annonce.
    """
    left = list(own)
    out = []
    for price, item, *_ in listings:
        match = next((i for i, (mine, c) in enumerate(left) if mine == price and c.values == item.values), None)
        if match is not None:
            del left[match]
        out.append(match is not None)
    return out


def listings_frame(
    listings: list[tuple[int, Classification, float, float]], template: Template, names: dict[int, str]
) -> pd.DataFrame:
    lines = base_lines(template)
    rows = []
    for price, item, first_seen, _ in listings:
        row = {
            "Prix": price,
            "Forgemagie": item.label,
            "Qualité %": round(item.quality * 100) if item.quality is not None else None,
            "Exo": _describe(item.exo, item.values, names),
            "Over": _describe(item.over, item.values, names),
            "Lignes manquantes": ", ".join(names.get(i, f"effet {i}") for i in item.missing),
        }
        for effect_id in lines:
            row[names.get(effect_id, f"effet {effect_id}")] = item.values.get(effect_id, 0)
        row["Vue depuis"] = first_seen
        rows.append(row)
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["Vue depuis"] = _local_dates(frame["Vue depuis"])
    return frame


def exo_counts(listings: list[tuple[int, Classification, float, float]]) -> dict[int, int]:
    """Nombre d'annonces par caractéristique ajoutée."""
    counts: dict[int, int] = {}
    for _, item, _, _ in listings:
        for effect_id in item.exo:
            counts[effect_id] = counts.get(effect_id, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def craft_cost(ws: Workspace, item_id: int) -> float | None:
    recipe = ws.calculator.recipes.get(item_id)
    return ws.calculator.evaluate(recipe).recursive_cost if recipe is not None else None


def summarize(
    ws: Workspace, item_id: int, listings: list[tuple[int, Classification, float, float]], flt: Filter | None
) -> dict:
    """Chiffres clés d'un équipement : craft, moins cher, moins cher de base, moins cher selon les critères."""
    tax = ws.calculator.tax
    cost = craft_cost(ws, item_id)
    cheapest = listings[0] if listings else None
    plain = next((entry for entry in listings if entry[1].plain), None)
    matching = [entry for entry in listings if flt.matches(entry[1])] if flt is not None else []
    match_price = matching[0][0] if matching else None
    plain_price = plain[0] if plain else None
    return {
        "craft_cost": cost,
        "count": len(listings),
        "cheapest_price": cheapest[0] if cheapest else None,
        "cheapest_label": cheapest[1].label if cheapest else None,
        "cheapest_missing": cheapest[1].missing if cheapest else (),
        "plain_price": plain_price,
        "plain_count": sum(1 for entry in listings if entry[1].plain),
        # Fabriquer pour revendre un exemplaire de base : prix net de taxe moins le coût.
        "craft_margin": plain_price * (1 - tax) - cost if plain_price is not None and cost is not None else None,
        "match_count": len(matching),
        "match_price": match_price,
        "match_median": statistics.median(entry[0] for entry in matching) if matching else None,
        # Ce que la forgemagie ajoute au prix affiché, avant le coût des runes.
        "premium_vs_plain": match_price - plain_price if match_price is not None and plain_price is not None else None,
        "premium_vs_craft": match_price * (1 - tax) - cost if match_price is not None and cost is not None else None,
    }


def gone_frame(conn: sqlite3.Connection, item_id: int, template: Template, names: dict[int, str]) -> pd.DataFrame:
    """Annonces vues lors d'une visite précédente et absentes de la dernière : vendues ou retirées."""
    latest = conn.execute("SELECT MAX(captured_at) FROM hdv_listings WHERE item_id = ?", (item_id,)).fetchone()[0]
    gone = [entry for entry in read_listings(conn, item_id, template, current=False) if entry[3] < latest]
    frame = listings_frame(gone, template, names)
    if not frame.empty:
        frame.insert(1, "Vue pour la dernière fois", _local_dates(pd.Series([entry[3] for entry in gone])))
    return frame


def ranking(
    conn: sqlite3.Connection,
    ws: Workspace,
    criterion: str,
    exo: int | None = None,
    over: tuple[int, int] | None = None,
) -> pd.DataFrame:
    """Une ligne par équipement dont on connaît les annonces et les caractéristiques de base.

    over : (caractéristique, valeur au-dessus du jet parfait). Les objets qui n'ont pas cette
    caractéristique de base sont écartés : ce serait un exo, pas un over.
    """
    names = effect_names(conn)
    rows = []
    for item_id in equipment_options(conn):
        template = load_template(conn, item_id)
        if template is None:
            continue
        listings = read_listings(conn, item_id, template)
        if criterion == OVER:
            lines = base_lines(template)
            if over is None or over[0] not in lines:
                continue
            flt: Filter | None = Filter({over[0]: lines[over[0]][1] + over[1]})
        elif criterion == PERFECT:
            flt: Filter | None = perfect_filter(template)
        elif criterion == EXO:
            flt = Filter({}, exo=exo) if exo else None
        else:
            saved = db.load_fm_filter(conn, item_id)
            flt = Filter.from_config(saved) if saved else None
        s = summarize(ws, item_id, listings, flt)
        item = ws.items[item_id]
        warning = ", ".join(names.get(i, f"effet {i}") for i in s["cheapest_missing"])
        rows.append(
            {
                "item_id": item_id,
                "Objet": item.name,
                "Niveau": item.level,
                "Annonces": s["count"],
                "Coût de craft": s["craft_cost"],
                "Moins cher": s["cheapest_price"],
                "Moins cher de base": s["plain_price"],
                "Marge du craft": s["craft_margin"],
                "Correspondent": s["match_count"] if flt is not None else None,
                "Moins cher selon critère": s["match_price"],
                "Prime sur la base": s["premium_vs_plain"],
                "Gain sur le craft": s["premium_vs_craft"],
                "Attention": f"le moins cher a perdu : {warning}" if warning else "",
            }
        )
    columns = [
        "item_id", "Objet", "Niveau", "Annonces", "Coût de craft", "Moins cher", "Moins cher de base", "Marge du craft",
        "Correspondent", "Moins cher selon critère", "Prime sur la base", "Gain sur le craft", "Attention",
    ]  # fmt: skip
    return pd.DataFrame(rows, columns=columns)


def all_exo_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """Exos rencontrés sur l'ensemble des annonces courantes, pour le sélecteur du classement."""
    counts: dict[int, int] = {}
    for item_id in equipment_options(conn):
        template = load_template(conn, item_id)
        if template is None:
            continue
        for effect_id, count in exo_counts(read_listings(conn, item_id, template)).items():
            counts[effect_id] = counts.get(effect_id, 0) + count
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
