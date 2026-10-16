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



def test_dataset_reads_the_journal_and_tells_a_pool_paid_neutral_from_a_critical():
    import json
    import sqlite3 as sq

    from dofustool import db

    conn = db.connect(":memory:")
    conn.execute("INSERT INTO items VALUES (10, 'Geta', 200, 'Bottes', 1, 1, 0, 0)")
    conn.execute("INSERT INTO items VALUES (20, 'Rune Fo', 1, 'Rune de forgemagie', 1, 1, 0, 2)")
    conn.executemany("INSERT INTO item_effects VALUES (?, ?, ?, ?)", [(10, 118, 30, 50), (10, 125, 100, 200), (20, 118, 1, 1)])
    passes = [  # (passé, puits avant, puits après, sens, avant, après, type)
        (1, 0.0, 0.0, 0, {118: 40, 125: 150}, {118: 41, 125: 150}, "rune"),  # rien payé : critique
        (1, 3.0, 2.0, 2, {118: 41, 125: 150}, {118: 42, 125: 150}, "rune"),  # payé par le puits : neutre
        (1, 2.0, 2.0, 1, {118: 42, 125: 150}, {118: 43, 125: 140}, "rune"),  # payé par la vitalité : neutre
        (0, 2.0, 2.0, 0, {118: 43, 125: 140}, {118: 43, 125: 140}, "rune"),  # échec
        (1, 2.0, 2.0, 0, {118: 43, 125: 140}, {118: 43, 125: 140}, "orbe"),  # ne compte pas
    ]
    for i, (ok, pb, pa, change, before, after, kind) in enumerate(passes):
        conn.execute(
            "INSERT INTO fm_passes (source_id, ts, uid, item_id, rune_id, passed, lost, pool, pool_change, kind, pool_before, before, after) "
            "VALUES (?, ?, 1, 10, 20, ?, NULL, ?, ?, ?, ?, ?, ?)",
            (i, float(i), ok, pa, change, kind, pb, json.dumps(list(before.items())), json.dumps(list(after.items()))),
        )
    rows = fmdata.build(conn, {118: 1.0, 125: 0.2})
    assert [r.outcome for r in rows] == ["sc", "sn", "sn", "ec"]
    assert rows[2].lost_weight == 2.0 and rows[0].line_kind == "haut" and rows[0].rune_weight == 1.0
