"""Valeur d'un équipement d'après les annonces similaires. Données synthétiques uniquement."""
from dofustool.analysis import similar
from dofustool.analysis.forgemagie import classify
from dofustool.analysis.similar import LOOSE, RELIABLE, ROUGH

# Vitalité 251 à 300, Sagesse 31 à 40, Critique 4 à 6. L'effet 111 (PA) n'est pas une ligne de base : c'est un exo.
TEMPLATE = {125: (251, 300), 124: (31, 40), 115: (4, 6)}
PA = 111


def item(vitality=280, wisdom=35, critical=5, **extra):
    effects = [(125, vitality), (124, wisdom), (115, critical), *((int(k[1:]), v) for k, v in extra.items())]
    return classify([e for e in effects if e[1]], TEMPLATE)


def test_similar_means_at_least_as_good_within_the_tolerance():
    mine = item()
    assert similar.is_similar(mine, item(280, 35, 5), TEMPLATE, 0.10)
    assert similar.is_similar(mine, item(276, 34, 4), TEMPLATE, 0.10)  # 4,9 points de vitalité et 1 point ailleurs de moins
    assert not similar.is_similar(mine, item(274, 35, 5), TEMPLATE, 0.10)  # trop en dessous
    assert similar.is_similar(mine, item(274, 35, 5), TEMPLATE, 0.20)
    assert not similar.is_similar(mine, item(300, 40, 6, e111=1), TEMPLATE, 0.10)  # un exo que je n'ai pas : autre marché
    assert similar.is_similar(mine, item(300, 0, 6), TEMPLATE, None)  # sans tolérance : exos et overs seulement
    assert not similar.is_similar(mine, item(300, 0, 6), TEMPLATE, 0.10)  # une ligne perdue de plus que moi

    exo = item(260, 33, 4, e111=1)
    assert similar.is_similar(exo, item(258, 33, 4, e111=1), TEMPLATE, 0.10)
    assert not similar.is_similar(exo, item(300, 40, 6), TEMPLATE, None)  # sans l'exo
    over = item(280, 44, 5)  # sagesse au-dessus de son maximum
    assert not similar.is_similar(over, item(300, 40, 6), TEMPLATE, 0.20)  # jets parfaits, mais pas over
    assert similar.is_similar(over, item(280, 43, 5), TEMPLATE, 0.10) and similar.is_similar(over, item(251, 41, 4), TEMPLATE, None)


def test_estimate_takes_the_cheapest_similar_listing_and_says_how_sure_it_is():
    mine = item(280, 35, 5, e111=1)
    listings = [
        (9_000_000, item(300, 40, 6, e111=1)),
        (6_000_000, item(279, 35, 5, e111=1)),
        (4_000_000, item(260, 31, 4, e111=1)),  # même exo, jets nettement moins bons
        (1_000_000, item(300, 40, 6)),  # sans exo : jamais comparé
    ]
    found = similar.estimate(mine, listings, TEMPLATE, 1234.0)
    assert (found.price, found.confidence, found.count, found.floor, found.captured_at) == (6_000_000, RELIABLE, 2, 4_000_000, 1234.0)
    assert found.listing.values[125] == 279
    # Sans annonce assez proche, la comparaison se relâche : d'abord la tolérance, puis les seuls exos et overs.
    assert similar.estimate(mine, [(5_000_000, item(272, 35, 5, e111=1))], TEMPLATE, 0.0).confidence == LOOSE
    rough = similar.estimate(mine, listings[2:], TEMPLATE, 0.0)
    assert (rough.price, rough.confidence, rough.floor) == (4_000_000, ROUGH, None)  # elle donne déjà le prix : pas un plancher
    assert similar.estimate(mine, listings[3:], TEMPLATE, 0.0) is None and similar.estimate(mine, [], TEMPLATE, 0.0) is None
    # Une annonce moins bonne mais plus chère ne borne rien.
    assert similar.estimate(mine, [(6_000_000, item(280, 35, 5, e111=1)), (7_000_000, item(260, 31, 4, e111=1))], TEMPLATE, 0.0).floor is None


def test_my_own_listing_is_not_compared_to_itself():
    mine = item(280, 35, 5)
    listings = [(2_000_000, item(280, 35, 5)), (2_000_000, item(280, 35, 5)), (2_500_000, item(290, 36, 5))]
    kept = similar.without_own(listings, 2_000_000, mine)
    assert [price for price, _ in kept] == [2_000_000, 2_500_000]  # une seule annonce retirée : l'autre est un vrai concurrent
    assert len(similar.without_own(listings, 1_999_999, mine)) == 3
