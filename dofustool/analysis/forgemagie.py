"""Lecture de la forgemagie d'un exemplaire d'équipement, par comparaison avec l'objet de base.

  - exo            : une caractéristique que l'objet de base ne possède pas ;
  - over           : une caractéristique de base poussée au-delà de son maximum naturel ;
  - ligne manquante : une caractéristique de base absente ou tombée à 0 ;
  - qualité        : où se situent les jets entre le minimum et le maximum de base.

Les caractéristiques de base viennent de la table item_effects : {id d'effet: (min, max)}.
Un malus de base a des bornes négatives : il n'est ni filtrable, ni « over », ni « manquant ».
"""
from dataclasses import dataclass

# Effets qui ne sont pas des caractéristiques : signatures et verrous posés sur l'objet.
MODIFIED_BY = 985
CRAFTED_BY = 988
NO_MORE_FM = 2825
MARKERS = frozenset({MODIFIED_BY, CRAFTED_BY, NO_MORE_FM})
# « Arme de chasse » : posée par une rune de chasse, sur une arme seulement, et en plus d'un éventuel exo.
# Ce n'est donc ni une caractéristique ni un exo comme les autres : une propriété à part, lue comme telle.
HUNTING = 795
WEAPON_TYPES = frozenset({"Arc", "Baguette", "Bâton", "Dague", "Épée", "Faux", "Hache", "Lance", "Marteau", "Pelle"})

Template = dict[int, tuple[int, int]]


# Au-delà de ce nombre de lignes perdues, un exemplaire n'est plus comparable à l'objet d'origine.
MAX_MISSING_LINES = 2


@dataclass(frozen=True, slots=True)
class Classification:
    values: dict[int, int]  # valeur de chaque caractéristique de l'exemplaire
    exo: tuple[int, ...]  # identifiants des effets ajoutés
    over: tuple[int, ...]  # identifiants des effets au-delà du maximum
    missing: tuple[int, ...]  # caractéristiques de base absentes ou nulles
    quality: float | None  # moyenne des jets de base, 0 = tous au minimum, 1 = tous au maximum
    modified: bool  # porte la marque « Modifié par » : passé en forgemagie
    transcended: bool = False  # porte « Empêche les futures forgemagies » : marque laissée par une rune de transcendance
    hunting: bool = False  # arme de chasse par une rune (pas d'origine) : se cumule avec un exo

    @property
    def plain(self) -> bool:
        """Comparable à un exemplaire tout juste fabriqué : ni exo, ni over, ni ligne manquante, ni rune de chasse."""
        return not self.exo and not self.over and not self.missing and not self.hunting

    @property
    def sellable(self) -> bool:
        """Encore le même objet aux yeux d'un acheteur : exo, over ou transcendance admis, deux lignes perdues au plus."""
        return len(self.missing) <= MAX_MISSING_LINES

    @property
    def perfect(self) -> bool:
        """Toutes les caractéristiques de base au maximum (ou au-delà)."""
        return self.quality is not None and self.quality >= 1 and not self.missing

    @property
    def label(self) -> str:
        parts = (["exo"] if self.exo else []) + (["over"] if self.over else [])
        if self.missing:
            parts.append("ligne manquante")
        if self.hunting:
            parts.append("chasse")
        if parts:
            return " + ".join(parts)
        if self.perfect:
            return "jets parfaits"
        return "forgemagé" if self.modified else "de base"


def base_lines(template: Template) -> dict[int, tuple[int, int]]:
    """Caractéristiques de base positives, celles qu'on peut vouloir filtrer ou améliorer."""
    return {effect_id: (low, high) for effect_id, (low, high) in template.items() if high > 0 and effect_id != HUNTING}


def classify(effects: list[tuple[int, int | None]], template: Template) -> Classification:
    """effects : (id, valeur) de l'exemplaire ; template : caractéristiques de l'item de base."""
    values = {
        effect_id: value for effect_id, value in effects if effect_id not in MARKERS and effect_id != HUNTING and value is not None
    }
    lines = base_lines(template)
    exo = tuple(effect_id for effect_id in values if effect_id not in template)
    over = tuple(effect_id for effect_id, (_, high) in lines.items() if values.get(effect_id, 0) > high)
    missing = tuple(effect_id for effect_id in lines if values.get(effect_id, 0) <= 0)
    # Seules les lignes à jet variable comptent : une ligne fixe (1 PA) n'a pas de « bon » jet.
    rolls = [
        min(1.0, max(0.0, (values.get(effect_id, 0) - low) / (high - low)))
        for effect_id, (low, high) in lines.items()
        if high > low
    ]
    quality = sum(rolls) / len(rolls) if rolls else None
    marks = {effect_id for effect_id, _ in effects}
    # Une arme de chasse d'origine (la propriété est dans sa fiche) ne doit rien à une rune.
    hunting = HUNTING in marks and HUNTING not in template
    return Classification(values, exo, over, missing, quality, MODIFIED_BY in marks, NO_MORE_FM in marks, hunting)


@dataclass(frozen=True, slots=True)
class Filter:
    """Critères réglés dans le dashboard pour un item.

    minimums : valeur minimale exigée par caractéristique de base.
    exo      : None = peu importe ; 0 = aucun exo accepté ; sinon l'identifiant de l'exo exigé.
    exo_min  : valeur minimale de l'exo exigé.
    exact    : les valeurs (minimums et exo_min) doivent être atteintes pile, pas seulement dépassées.
    hunting  : None = peu importe ; True = armes de chasse (rune) seulement ; False = sans rune de chasse.
    """

    minimums: dict[int, int]
    exo: int | None = None
    exo_min: int = 1
    transcended: bool | None = None  # None = peu importe ; True = transcendés seulement ; False = sans transcendance
    exact: bool = False  # classement : « 1 % de dommages aux sorts » ne doit pas montrer ceux à 2 %
    hunting: bool | None = None

    @classmethod
    def from_config(cls, config: dict) -> "Filter":
        return cls(
            {int(k): int(v) for k, v in (config.get("minimums") or {}).items()},
            config.get("exo"),
            int(config.get("exo_min") or 1),
            config.get("transcended") if isinstance(config.get("transcended"), bool) else None,
            hunting=config.get("hunting") if isinstance(config.get("hunting"), bool) else None,
        )

    def to_config(self) -> dict:
        config = {"minimums": {str(k): v for k, v in self.minimums.items()}, "exo": self.exo, "exo_min": self.exo_min}
        if self.transcended is not None:
            config["transcended"] = self.transcended
        if self.hunting is not None:
            config["hunting"] = self.hunting
        return config

    def matches(self, item: Classification) -> bool:
        if any(
            (value != minimum) if self.exact else (value < minimum)
            for value, minimum in ((item.values.get(effect_id, 0), minimum) for effect_id, minimum in self.minimums.items())
        ):
            return False
        if self.transcended is not None and item.transcended != self.transcended:
            return False
        if self.hunting is not None and item.hunting != self.hunting:
            return False
        if self.exo == 0:
            return not item.exo
        if self.exo is not None:
            if self.exo not in item.exo:
                return False
            return item.values[self.exo] == self.exo_min if self.exact else item.values[self.exo] >= self.exo_min
        return True


def perfect_filter(template: Template) -> Filter:
    """Tous les jets de base au maximum, sans exo."""
    return Filter({effect_id: high for effect_id, (_, high) in base_lines(template).items()}, exo=0, hunting=False)
