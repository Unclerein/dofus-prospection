from dofustool import db
from dofustool.analysis import genetics
from dofustool.analysis.genetics import Color, Owned, Tree

ROUSSE, DOREE, AMANDE, AR, AD, DR, INDIGO = 10, 18, 20, 38, 33, 46, 17
WEIGHT = {ROUSSE: 90, AMANDE: 90, DOREE: 20, AR: 20, AD: 20, DR: 20, INDIGO: 90}
TREE = Tree({})
for color, generation, parents in ((ROUSSE, 1, None), (DOREE, 1, None), (AMANDE, 1, None), (AR, 2, (ROUSSE, AMANDE)),
                                   (AD, 2, (DOREE, AMANDE)), (DR, 2, (ROUSSE, DOREE)), (INDIGO, 3, (AD, AR))):  # fmt: skip
    TREE.colors[color] = Color(color, generation, parents, WEIGHT[color], ("#ff8c00", "#ff8c00"))
    if parents:
        TREE.children[tuple(sorted(parents))] = color


def rounded(odds):
    return {c: round(v, 2) for c, v in odds.items()}


def test_chances_match_the_game_previews():
    # Aperçus relevés en jeu (24/10/2026) : la formule doit les retrouver à l'arrondi près.
    assert rounded(genetics.chances(TREE, (AMANDE, (), 53), (DOREE, (), 53))) == {AD: 45.9, AMANDE: 27.05, DOREE: 27.05}
    assert rounded(genetics.chances(TREE, (DOREE, (), 53), (AMANDE, (AMANDE, ROUSSE), 53))) == {AD: 33.38, DR: 12.52, DOREE: 27.05, AMANDE: 19.67, ROUSSE: 7.38}
    assert rounded(genetics.chances(TREE, (ROUSSE, (DOREE, ROUSSE), 53), (AMANDE, (), 1))) == {AR: 35.17, AD: 2.93, ROUSSE: 28.57, DOREE: 2.38, AMANDE: 30.95}
    assert rounded(genetics.chances(TREE, (AD, (AMANDE, DOREE), 53), (AR, (DOREE, ROUSSE), 53))) == {
        INDIGO: 45.9, AD: 6.74, AR: 13.2, AMANDE: 13.22, ROUSSE: 13.22, DOREE: 5.88, DR: 1.84}  # le jeu affiche 1,85 : arrondi
    assert rounded(genetics.chances(TREE, (AR, (AMANDE, ROUSSE), 53), (DR, (DOREE, ROUSSE), 57))) == {
        AR: 25.39, DR: 17.57, AD: 3.55, ROUSSE: 34.86, DOREE: 4.63, AMANDE: 14.01}  # le jeu affiche 25,38
    assert genetics.chances(TREE, (DOREE, (), 1), (DOREE, (), 53)) == {DOREE: 100.0}
    assert round(genetics.chances(TREE, (AMANDE, (), 200), (DOREE, (), 200))[AD], 2) == 90.0
    assert round(genetics.chances(TREE, (AMANDE, (), 200), (DOREE, (), 200), optimakina=True)[AD], 2) == 100.0


def mount(n, color, male=True, full=True, level=53, parents=()):
    gauges = (20000, 20000, 20000) if full else (5000, 0, 20000)
    return Owned(f"m{n}", color, male, level, False, gauges, 0, parents)


def test_the_route_uses_the_stable_and_the_best_pair():
    have = {ROUSSE: [mount(1, ROUSSE)], AMANDE: [mount(2, AMANDE, male=False), mount(3, AMANDE, male=False)], AD: [mount(4, AD, male=False)],
            AR: [mount(5, AR, male=True, level=1, full=False)]}  # fmt: skip
    route = genetics.route(TREE, have, INDIGO)
    # Indigo : Amande et Dorée (♀) × Amande et Rousse (♂), tous deux dans l'étable.
    assert [r["child"] for r in route["rows"]] == [INDIGO] and route["capture_counts"] == {}
    [row] = route["rows"]
    assert row["best"] == ["m4", "m5"] and round(row["chance"], 2) == round(30 + 0.15 * 54, 2)
    assert row["status"] == "prepare" and round(row["tries"], 2) == round(100 / row["chance"], 2)
    assert genetics.route(TREE, {INDIGO: [mount(9, INDIGO)]}, INDIGO)["owned"]


def test_missing_parents_are_produced_or_captured():
    have = {ROUSSE: [mount(1, ROUSSE)], AMANDE: [mount(2, AMANDE, male=False)]}
    route = genetics.route(TREE, have, INDIGO)
    children = [r["child"] for r in route["rows"]]
    assert set(children) == {AD, AR, INDIGO} and children[-1] == INDIGO
    # Il faut deux Amandes (une pour chaque bicolore) : une dans l'étable, une à capturer ; et une Dorée.
    assert route["capture_counts"] == {AMANDE: 1.0, DOREE: 1.0}
    assert genetics.pair_state([{"uid": "a", "male": True, "fecund": True}], [{"uid": "b", "male": False, "fecund": True}]) == "ready"
    assert genetics.pair_state([{"uid": "a", "male": True, "fecund": True}], [{"uid": "c", "male": True, "fecund": True}]) == "sex"


def test_tints_and_names_without_network():
    assert genetics._tint("{639||1=16747520,2=16766720,3=1|120}") == ("#ff8c00", "#ffd700")
    conn = db.connect(":memory:")
    assert genetics.names(conn, fetch=False) == {}
    conn.execute("INSERT INTO ride_names VALUES (17, 'Indigo')")
    assert genetics.names(conn) == {17: "Indigo"}
