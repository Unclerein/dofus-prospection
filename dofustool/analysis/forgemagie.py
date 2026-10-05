"""Repérage des exos et des overs de forgemagie sur un exemplaire d'équipement.

  - exo  : une caractéristique que l'objet de base ne possède pas ;
  - over : une caractéristique de base poussée au-delà de son maximum naturel.

La comparaison se fait avec les caractéristiques de base de l'item (table item_effects).
"""
from dataclasses import dataclass

# Effets qui ne sont pas des caractéristiques : signatures et verrous posés sur l'objet.
MODIFIED_BY = 985
CRAFTED_BY = 988
NO_MORE_FM = 2825
MARKERS = frozenset({MODIFIED_BY, CRAFTED_BY, NO_MORE_FM})


@dataclass(frozen=True, slots=True)
class Classification:
    exo: tuple[int, ...]  # identifiants des effets ajoutés
    over: tuple[int, ...]  # identifiants des effets au-delà du maximum
    modified: bool  # porte la marque « Modifié par » : passé en forgemagie

    @property
    def plain(self) -> bool:
        """Comparable à un exemplaire tout juste fabriqué : ni exo ni over."""
        return not self.exo and not self.over

    @property
    def label(self) -> str:
        parts = (["exo"] if self.exo else []) + (["over"] if self.over else [])
        return " + ".join(parts) if parts else ("forgemagé" if self.modified else "de base")


def classify(effects: list[tuple[int, int | None]], template: dict[int, tuple[int, int]]) -> Classification:
    """effects : (id, valeur) de l'exemplaire ; template : {id: (min, max)} de l'item de base."""
    exo, over = [], []
    for effect_id, value in effects:
        if effect_id in MARKERS or value is None:
            continue
        if effect_id not in template:
            exo.append(effect_id)
            continue
        low, high = template[effect_id]
        # Un malus de base a des bornes négatives : il ne peut pas être « over ».
        if high > 0 and value > high:
            over.append(effect_id)
    return Classification(tuple(exo), tuple(over), any(e == MODIFIED_BY for e, _ in effects))
