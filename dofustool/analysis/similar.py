"""Valeur d'un exemplaire d'équipement d'après les annonces HDV du même modèle qui lui ressemblent.

Une annonce est « similaire » si un acheteur pourrait la prendre à la place de l'exemplaire :

  - mêmes exos, chacun au moins aussi élevé ;
  - chaque ligne en over chez l'exemplaire l'est aussi chez elle ;
  - chaque ligne de base vaut au moins celle de l'exemplaire, à une tolérance près ;
  - pas plus de lignes perdues.

L'estimation est le prix de l'annonce similaire la moins chère. Faute d'annonce, la comparaison
se relâche par étapes, et le niveau de confiance le dit.
"""
import json
import sqlite3
from dataclasses import dataclass

from .forgemagie import Classification, Template, base_lines, classify

# Part de la plage d'une ligne (max − min) dont une annonce peut être en dessous de l'exemplaire.
TOLERANCE = 0.10
RELIABLE, LOOSE, ROUGH = "fiable", "approximative", "grossière"


@dataclass(frozen=True, slots=True)
class Estimate:
    price: int
    confidence: str  # RELIABLE, LOOSE ou ROUGH
    count: int  # annonces similaires à ce niveau de confiance
    listing: Classification  # celle qui donne le prix
    floor: int | None  # annonce la moins chère que l'exemplaire surclasse, à titre indicatif
    captured_at: float  # date du relevé des annonces


def _slack(low: int, high: int, tolerance: float) -> float:
    return max(1.0, tolerance * (high - low))


def is_similar(mine: Classification, other: Classification, template: Template, tolerance: float | None) -> bool:
    """other vaut-elle au moins mine ? tolerance None : seuls les exos et les overs sont comparés."""
    if set(other.exo) != set(mine.exo) or other.hunting != mine.hunting:
        return False  # une arme de chasse ne se compare qu'à une arme de chasse
    if any(other.values.get(effect_id, 0) < mine.values.get(effect_id, 0) for effect_id in mine.exo):
        return False
    if not set(mine.over) <= set(other.over):
        return False
    if tolerance is None:
        return True
    if len(other.missing) > len(mine.missing):
        return False
    return all(
        other.values.get(effect_id, 0) >= mine.values.get(effect_id, 0) - _slack(low, high, tolerance)
        for effect_id, (low, high) in base_lines(template).items()
    )


def estimate(
    mine: Classification, listings: list[tuple[int, Classification]], template: Template, captured_at: float
) -> Estimate | None:
    """listings : (prix, lecture) des annonces du modèle, sans celle de l'exemplaire lui-même."""
    # Ce que l'exemplaire surclasse nettement : une annonce moins bonne, même en lui passant la tolérance.
    beaten = [price for price, other in listings if is_similar(other, mine, template, 0.0) and not is_similar(mine, other, template, TOLERANCE)]
    for confidence, tolerance in ((RELIABLE, TOLERANCE), (LOOSE, 2 * TOLERANCE), (ROUGH, None)):
        matches = sorted(((price, other) for price, other in listings if is_similar(mine, other, template, tolerance)), key=lambda m: m[0])
        if matches:
            price, chosen = matches[0]
            # Un plancher au-dessus de l'estimation ne borne rien : des annonces moins bonnes mais plus chères.
            floor = min((b for b in beaten if b < price), default=None)
            return Estimate(price, confidence, len(matches), chosen, floor, captured_at)
    return None


def load_listings(
    conn: sqlite3.Connection, item_id: int, template: Template, ignored: frozenset[int]
) -> tuple[list[tuple[int, Classification]], float | None]:
    """((prix, lecture) des annonces à l'unité du dernier relevé, date de ce relevé)."""
    rows = conn.execute("SELECT p1, effects, captured_at FROM hdv_current WHERE item_id = ? AND p1 > 0", (item_id,)).fetchall()
    listings = [(price, classify([tuple(e) for e in json.loads(effects) if e[0] not in ignored], template)) for price, effects, _ in rows]
    return listings, max((row[2] for row in rows), default=None)


def without_own(listings: list[tuple[int, Classification]], price: int, mine: Classification) -> list[tuple[int, Classification]]:
    """Les annonces sans la mienne : un lot en vente figure aussi dans le relevé de l'HDV, au même prix et aux mêmes jets."""
    out, dropped = [], False
    for entry in listings:
        if not dropped and entry[0] == price and entry[1].values == mine.values:
            dropped = True
            continue
        out.append(entry)
    return out
