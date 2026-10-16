"""Jeu de données de forgemagie : classement de la ligne visée et poids, avec des nombres inventés."""
from dofustool.analysis import fmdata


def test_line_kind():
    assert fmdata.line_kind(5, None, None) == "exo"
    assert fmdata.line_kind(10, 5, 50) == "bas"
    assert fmdata.line_kind(40, 5, 50) == "haut"  # 80 % du max
    assert fmdata.line_kind(50, 5, 50) == "max"
    assert fmdata.line_kind(53, 5, 50) == "over"


def test_weight_counts_only_positive_lines():
    density = {118: 1.0, 125: 0.2, 111: 100.0}
    assert fmdata.weight_of({118: 30, 125: 200, 111: 1, 160: 5}, density) == 30 + 40 + 100
    assert fmdata.weight_of({118: -10}, density) == 0
