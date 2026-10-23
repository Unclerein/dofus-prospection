from dofustool import db
from dofustool.analysis import genetics
from dofustool.analysis.genetics import Color, Owned, Tree

# Un petit arbre : trois sauvages, leurs croisements, et une génération 3.
ROUSSE, DOREE, AMANDE, AR, AD, DR, INDIGO = 10, 18, 20, 38, 33, 46, 17
TREE = Tree({})
for color, generation, parents in ((ROUSSE, 1, None), (DOREE, 1, None), (AMANDE, 1, None), (AR, 2, (ROUSSE, AMANDE)),
                                   (AD, 2, (DOREE, AMANDE)), (DR, 2, (ROUSSE, DOREE)), (INDIGO, 3, (AD, AR))):  # fmt: skip
    TREE.colors[color] = Color(color, generation, parents, 90, ("#ff8c00", "#ff8c00"))
    if parents:
        TREE.children[tuple(sorted(parents))] = color


def mount(n, color, male=True, full=True, sterile=False):
    gauges = (20000, 20000, 20000) if full else (5000, 0, 20000)
    return Owned(f"m{n}", color, male, 53, sterile, gauges, 0)


def test_the_path_climbs_down_to_what_the_stable_has():
    have = {ROUSSE: [mount(1, ROUSSE)], AMANDE: [mount(2, AMANDE, male=False), mount(3, AMANDE, male=False)], AD: [mount(4, AD)]}
    route = genetics.plan(TREE, have, INDIGO)
    # Indigo = Amande et Dorée (dans l'étable) × Amande et Rousse (à produire : Rousse × Amande).
    assert [s["child"] for s in route["steps"]] == [AR, INDIGO] and route["capture"] == []
    summary = genetics.summarize(route, have)
    assert summary["matings"] == 2 and [r["child"] for r in summary["rows"]] == [AR, INDIGO]
    assert summary["rows"][0]["status"] == "ready" and summary["rows"][1]["status"] == "waiting"
    assert {(n["color"], n["make"], n["use"]) for n in summary["nodes"]} >= {(AR, 1, 0), (INDIGO, 1, 0), (AD, 0, 1), (ROUSSE, 0, 1)}


def test_each_mount_serves_once_and_missing_wild_ones_are_to_capture():
    have = {ROUSSE: [mount(1, ROUSSE)], AMANDE: [mount(2, AMANDE, male=False)]}
    route = genetics.plan(TREE, have, INDIGO)
    # Deux Amandes nécessaires (une par croisement), une seule dans l'étable ; aucune Dorée.
    assert route["capture"] == [DOREE, AMANDE]  # triées par identifiant
    assert genetics.summarize(route, have)["capture_counts"] == {AMANDE: 1, DOREE: 1}
    assert genetics.plan(TREE, {INDIGO: [mount(9, INDIGO)]}, INDIGO)["owned"]


def test_pair_state_needs_opposite_sexes_and_full_gauges():
    a, b = [{"uid": "a", "male": True, "fecund": True}], [{"uid": "b", "male": False, "fecund": False}]
    assert genetics.pair_state(a, b) == "prepare"
    assert genetics.pair_state(a, [{"uid": "c", "male": True, "fecund": True}]) == "sex"
    assert genetics.pair_state(a, [{"uid": "c", "male": False, "fecund": True}]) == "ready"
    assert genetics.pair_state(a, []) == "waiting"


def test_tints_and_names_without_network():
    assert genetics._tint("{639||1=16747520,2=16766720,3=1|120}") == ("#ff8c00", "#ffd700")
    conn = db.connect(":memory:")
    assert genetics.names(conn, fetch=False) == {}
    conn.execute("INSERT INTO ride_names VALUES (17, 'Indigo')")
    assert genetics.names(conn) == {17: "Indigo"}
