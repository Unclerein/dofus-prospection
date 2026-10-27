"""Codes d'effet et de déclencheur du jeu qui comptent pour une fiche de monstre.

Repérés sur les sorts des monstres de donjon (dofus.sqlite) : seuls ceux dont le sens est sûr sont ici.
"""

# Dommages : directs, vol de vie, en pourcentage, fixes, par PA ou PM utilisé, selon les PV.
DAMAGE = frozenset({
    87, 88, 89, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 144, 275, 276, 277, 278, 279, 671,
    1012, 1013, 1014, 1015, 1063, 1064, 1065, 1066, 1069, 1070, 1071, 1092, 1094, 1095, 1096,
    1118, 1119, 1120, 1121, 1122, 1123, 1125, 1128, 1131, 1132, 1133, 1135, 1136, 1137, 1138, 1139, 1140,
    1223, 1228, 2822, 2828, 2829,
})  # fmt: skip
SUBSPELL = frozenset({792, 793, 1017, 1018, 1019, 1160, 2160, 2792, 2793, 2794})  # lance un autre sort (#1 : son id)

# Déplacements du lanceur lui-même, ou de sa cible.
TELEPORT = frozenset({4, 1101})  # sur la case ciblée
SWAP = frozenset({8, 1023})
ADVANCE, RETREAT = 1042, 1041
SYMMETRY = frozenset({1104, 1105, 1106})
SEND_BACK = frozenset({784, 1099, 1100})  # case de début de tour, de combat, précédente
PULL = frozenset({6, 1022})
PUSH = frozenset({5, 1103})
GAIN_MP = frozenset({78, 128})

INVISIBLE = 150
ADD_STATE, REMOVE_STATE, DISABLE_STATE = 950, 951, 952
REFLECT = frozenset({107, 220})  # dommages renvoyés
RETURN_SHARE = frozenset({1123, 1125, 1128, 1223, 1228, 2829})  # dommages : n % des dommages subis (le type Stalak)
NOTHING = 666  # « pas d'effet » : sous-sort qui ne sert qu'à porter une mécanique dans son nom
# Rien à en dire dans une fiche : pas d'effet, retrait d'état ou d'effets de sort, retouches de sorts.
SILENT = frozenset({NOTHING, 406, 951, 952, 283, 284, 285, 286, 287, 289, 290, 291, 293, 281, 1036, 1045, 333, 149, 2868, 1060, 2871})
KILL = 141
GLYPHS = frozenset({401, 402, 1091, 1165})
SUMMON = frozenset({180, 181, 405, 780, 1189})
HEAL = frozenset({108, 1109, 2998, 2999, 3000})

# Déclencheurs d'un effet (champ `triggers`, plusieurs séparés par « | ») : quand l'effet se produit.
TRIGGERS = {
    "D": "quand il subit des dommages", "DM": "quand il subit des dommages de mêlée", "DCAC": "quand il est frappé au corps à corps",
    "DR": "quand il subit des dommages à distance", "DF": "quand il subit des dommages Feu", "DW": "quand il subit des dommages Eau",
    "DE": "quand il subit des dommages Terre", "DA": "quand il subit des dommages Air", "DN": "quand il subit des dommages Neutre",
    "PD": "quand il subit des dommages de poussée", "PPD": "quand il subit des dommages de poussée", "P": "quand il est poussé", "H": "quand il est soigné",
    "TB": "au début de son tour", "TE": "à la fin de son tour", "M": "quand il se déplace",
}  # fmt: skip

# Drapeaux d'un niveau de sort (SpellLevelData.m_flags), vérifiés sur des sorts de classe connus.
IN_LINE, LINE_OF_SIGHT = 0x1, 0x4
WHOLE_MAP = 40  # portée au-delà de laquelle un sort touche partout
