import pytest

from dofustool import db
from dofustool.analysis.forgemagie import Filter, perfect_filter
from dofustool.app import data, forge
from dofustool.config import Config

NOW = 2_000_000.0
PA, VITA, CHANCE = 111, 125, 123
RING, SHIELD = 500, 600


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    c.executemany(
        "INSERT INTO items VALUES (?, ?, 1, 'type', 60, 1, 0, ?)",
        [(RING, "Anneau", 0), (SHIELD, "Bouclier", 0), (1, "Gelée", 2), (700, "Cape sans base connue", 0)],
    )
    c.execute("INSERT INTO jobs VALUES (16, 'Bijoutier')")
    c.execute("INSERT INTO recipes VALUES (?, 16, 60)", (RING,))
    c.execute("INSERT INTO recipe_ingredients VALUES (?, 1, 100)", (RING,))
    c.executemany("INSERT INTO effects VALUES (?, ?)", [(PA, "PA"), (VITA, "Vitalité"), (CHANCE, "Chance")])
    c.executemany("INSERT INTO item_effects VALUES (?, ?, ?, ?)", [(RING, PA, 1, 1), (RING, VITA, 201, 250)])
    c.executemany("INSERT INTO item_effects_fetched VALUES (?, ?)", [(RING, NOW), (SHIELD, NOW)])
    db.save_snapshot(c, NOW - 60, {1: 400})  # la Gelée vaut 400 : le craft de l'anneau coûte 40 000
    listings = [
        # (item, uid, prix, effets, dernière vue)
        (RING, 1, 30_000, [[PA, 1]], NOW),  # le moins cher : a perdu sa Vitalité
        (RING, 2, 50_000, [[PA, 1], [VITA, 210]], NOW),  # de base, petit jet
        (RING, 3, 90_000, [[PA, 1], [VITA, 250]], NOW),  # jets parfaits
        (RING, 4, 400_000, [[PA, 1], [VITA, 240], [CHANCE, 15]], NOW),  # exo
        (RING, 5, 600_000, [[PA, 1], [VITA, 260]], NOW),  # over
        (RING, 6, 70_000, [[PA, 1], [VITA, 230]], NOW - 86400),  # disparue depuis hier
        (SHIELD, 7, 1_000_000, [[CHANCE, 5]], NOW),  # objet sans caractéristique de base
        (700, 8, 5_000, [[PA, 1]], NOW),  # caractéristiques de base jamais récupérées
    ]
    c.executemany(
        "INSERT INTO hdv_listings VALUES (?, ?, ?, 0, 0, 0, ?, ?, ?)",
        [(item, uid, price, str(effects), seen, seen - 3600) for item, uid, price, effects, seen in listings],
    )
    yield c
    c.close()


def workspace(conn):
    return data.build_workspace(conn, Config(hdv_tax=0.02), NOW)


def test_options_and_templates(conn):
    assert set(forge.equipment_options(conn)) == {RING, SHIELD, 700}  # la Gelée n'est pas un équipement
    assert "5 annonce(s)" in forge.equipment_options(conn)[RING]  # l'annonce disparue n'est pas comptée
    assert forge.load_template(conn, RING) == {PA: (1, 1), VITA: (201, 250)}
    assert forge.load_template(conn, SHIELD) == {} and forge.load_template(conn, 700) is None


def test_lines_follow_game_tooltip_order(conn):
    # Par identifiant, PA (111) précède Vitalité (125) ; le jeu affiche la Vitalité d'abord.
    assert list(forge.load_template(conn, RING)) == [PA, VITA]
    conn.executemany(
        "INSERT INTO effect_meta (effect_id, priority, asset) VALUES (?, ?, ?)",
        [(VITA, 5000, "tx_vitality"), (PA, 7000, "tx_actionPoints")],
    )
    assert list(forge.load_template(conn, RING)) == [VITA, PA]
    frame = forge.listings_frame(forge.read_listings(conn, RING, forge.load_template(conn, RING)), forge.load_template(conn, RING), forge.effect_names(conn))
    assert list(frame.columns).index("Vitalité") < list(frame.columns).index("PA")
    assert forge.effect_assets(conn) == {VITA: "tx_vitality", PA: "tx_actionPoints"}


def test_weapon_damage_lines_are_not_forgemagie(conn):
    DAMAGE, HUNT, LOCK = 97, 795, 2825
    conn.execute("INSERT INTO items VALUES (800, 'Épée', 1, 'Épée', 100, 1, 1, 0)")
    conn.executemany("INSERT INTO item_effects VALUES (800, ?, ?, ?)", [(DAMAGE, 21, 30), (VITA, 101, 150)])
    conn.execute("INSERT INTO item_effects_fetched VALUES (800, ?)", (NOW,))
    conn.executemany(
        "INSERT INTO effect_meta (effect_id, priority, asset, is_stat) VALUES (?, ?, ?, ?)",
        [(DAMAGE, 200, None, 0), (HUNT, 900, None, 0), (VITA, 5000, "tx_vitality", 1), (LOCK, 96600, None, 0)],
    )
    conn.executemany(
        "INSERT INTO hdv_listings VALUES (800, ?, ?, 0, 0, 0, ?, ?, ?)",
        [
            # La ligne de dégâts arrive sans valeur numérique : ce n'est ni une ligne perdue ni un exo.
            (1, 90_000, f"[[{DAMAGE}, null], [{VITA}, 120]]", NOW, NOW),
            (2, 300_000, f"[[{DAMAGE}, null], [{VITA}, 150], [{CHANCE}, 12], [{LOCK}, null]]", NOW, NOW),
            # « Arme de chasse », posée par une rune : ni exo ni ligne perdue, mais plus un exemplaire de base.
            (3, 70_000, f"[[{DAMAGE}, null], [{HUNT}, 1], [{VITA}, 120]]", NOW, NOW),
            (4, 400_000, f"[[{DAMAGE}, null], [{HUNT}, 1], [{VITA}, 150], [{CHANCE}, 12]]", NOW, NOW),
        ],
    )
    assert forge.load_template(conn, 800) == {VITA: (101, 150)}
    assert forge.load_fixed_lines(conn, 800) == {DAMAGE: (21, 30)}
    hunt, plain, trans, both = (entry[1] for entry in forge.read_listings(conn, 800, forge.load_template(conn, 800)))
    assert plain.plain and plain.label == "de base" and not plain.transcended and plain.values == {VITA: 120}
    assert trans.exo == (CHANCE,) and trans.transcended and not trans.missing and not trans.hunting
    assert hunt.hunting and not hunt.plain and hunt.label == "chasse" and not hunt.exo and not hunt.missing and hunt.values == {VITA: 120}
    assert both.hunting and both.exo == (CHANCE,) and both.label == "exo + chasse"
    # Le prix de référence retient l'exemplaire de base de l'arme, pas l'arme de chasse moins chère.
    assert data.build_workspace(conn, Config(), NOW).prices.get(800).price == 90_000

    # Chercher un exo : avec, sans, ou peu importe la rune de chasse.
    assert Filter({}, exo=CHANCE).matches(trans) and Filter({}, exo=CHANCE).matches(both)
    assert Filter({}, exo=CHANCE, hunting=False).matches(trans) and not Filter({}, exo=CHANCE, hunting=False).matches(both)
    assert Filter({}, exo=CHANCE, hunting=True).matches(both) and not Filter({}, exo=CHANCE, hunting=True).matches(trans)
    assert Filter.from_config(Filter({}, hunting=True).to_config()).hunting is True and "hunting" not in Filter({}).to_config()
    ws = data.build_workspace(conn, Config(), NOW)
    pick = lambda frame: frame.set_index("item_id").loc[800, "Moins cher selon critère"]  # noqa: E731
    assert pick(forge.ranking(conn, ws, forge.EXO, CHANCE)) == 300_000
    assert pick(forge.ranking(conn, ws, forge.EXO, CHANCE, hunting=True)) == 400_000
    assert pick(forge.ranking(conn, ws, forge.EXO, CHANCE, hunting=False)) == 300_000

    # Une arme de chasse d'origine (la propriété est dans sa fiche) ne doit rien à une rune : elle reste « de base ».
    conn.execute("INSERT INTO items VALUES (801, 'Arc de chasse', 1, 'Arc', 100, 1, 1, 0)")
    conn.executemany("INSERT INTO item_effects VALUES (801, ?, ?, ?)", [(HUNT, 1, 1), (VITA, 101, 150)])
    conn.execute("INSERT INTO item_effects_fetched VALUES (801, ?)", (NOW,))
    conn.execute(f"INSERT INTO hdv_listings VALUES (801, 1, 50000, 0, 0, 0, '[[{HUNT}, 1], [{VITA}, 130]]', ?, ?)", (NOW, NOW))
    template = forge.load_template(conn, 801)
    assert template == {HUNT: (1, 1), VITA: (101, 150)} and list(forge.base_lines(template)) == [VITA]
    (native,) = (entry[1] for entry in forge.read_listings(conn, 801, template))
    assert native.plain and not native.hunting and native.label == "de base"
    assert data.build_workspace(conn, Config(), NOW).prices.get(801).price == 50_000

    assert Filter({}, transcended=True).matches(trans) and not Filter({}, transcended=True).matches(plain)
    assert Filter({}, transcended=False).matches(plain) and not Filter({}, transcended=False).matches(trans)
    assert Filter.from_config(Filter({}, transcended=False).to_config()).transcended is False
    assert "transcended" not in Filter({}).to_config()


def test_listings_frame_reads_each_line(conn):
    template = forge.load_template(conn, RING)
    listings = forge.read_listings(conn, RING, template)
    frame = forge.listings_frame(listings, template, forge.effect_names(conn))
    assert list(frame["Prix"]) == [30_000, 50_000, 90_000, 400_000, 600_000]
    assert list(frame["Forgemagie"]) == ["ligne manquante", "de base", "jets parfaits", "exo", "over"]
    assert list(frame["Vitalité"]) == [0, 210, 250, 240, 260] and list(frame["PA"]) == [1] * 5
    assert frame["Lignes manquantes"].iloc[0] == "Vitalité"
    assert frame["Exo"].iloc[3] == "Chance 15" and frame["Over"].iloc[4] == "Vitalité 260"
    assert frame["Qualité %"].iloc[1] == 18 and frame["Qualité %"].iloc[2] == 100
    assert forge.exo_counts(listings) == {CHANCE: 1}


def test_summary_compares_craft_base_and_criteria(conn):
    ws = workspace(conn)
    template = forge.load_template(conn, RING)
    listings = forge.read_listings(conn, RING, template)

    s = forge.summarize(ws, RING, listings, Filter({}))
    assert s["craft_cost"] == 40_000
    assert (s["cheapest_price"], s["cheapest_missing"]) == (30_000, (VITA,))  # à signaler : ligne perdue
    assert (s["plain_price"], s["plain_count"]) == (50_000, 2)
    assert s["craft_margin"] == pytest.approx(50_000 * 0.98 - 40_000)

    exo = forge.summarize(ws, RING, listings, Filter({}, exo=CHANCE))
    assert (exo["match_count"], exo["match_price"]) == (1, 400_000)
    assert exo["premium_vs_plain"] == 350_000
    assert exo["premium_vs_craft"] == pytest.approx(400_000 * 0.98 - 40_000)

    line = forge.summarize(ws, RING, listings, Filter({VITA: 240}))  # ligne par ligne
    assert (line["match_count"], line["match_price"], line["match_median"]) == (3, 90_000, 400_000)
    perfect = forge.summarize(ws, RING, listings, perfect_filter(template))
    assert (perfect["match_count"], perfect["match_price"]) == (2, 90_000)  # jets parfaits et over, sans exo
    none = forge.summarize(ws, RING, listings, Filter({VITA: 999}))
    assert none["match_count"] == 0 and none["match_price"] is None and none["premium_vs_plain"] is None


def test_gone_listings_are_history(conn):
    gone = forge.gone_frame(conn, RING, forge.load_template(conn, RING), forge.effect_names(conn))
    assert list(gone["Prix"]) == [70_000] and gone["Forgemagie"].iloc[0] == "de base"
    assert forge.gone_frame(conn, SHIELD, {}, {}).empty


def test_ranking_by_criterion(conn):
    ws = workspace(conn)
    saved = forge.ranking(conn, ws, forge.SAVED).set_index("Objet")
    assert set(saved.index) == {"Anneau", "Bouclier"}  # la cape sans base connue est écartée
    assert saved.loc["Anneau", "Attention"] == "le moins cher a perdu : Vitalité"
    assert saved.loc["Anneau", "Moins cher de base"] == 50_000 and saved.loc["Anneau", "Annonces"] == 5
    assert pd_isna(saved.loc["Anneau", "Moins cher selon critère"])  # aucun critère enregistré

    db.save_fm_filter(conn, RING, Filter({VITA: 250}).to_config())
    saved = forge.ranking(conn, ws, forge.SAVED).set_index("Objet")
    assert saved.loc["Anneau", "Moins cher selon critère"] == 90_000 and saved.loc["Anneau", "Prime sur la base"] == 40_000

    exo = forge.ranking(conn, ws, forge.EXO, CHANCE).set_index("Objet")
    assert exo.loc["Anneau", "Prime sur la base"] == 350_000
    assert exo.loc["Bouclier", "Moins cher selon critère"] == 1_000_000  # exo sur un objet sans ligne de base
    assert pd_isna(exo.loc["Bouclier", "Coût de craft"])
    # Over : Vitalité au moins 5 au-dessus du jet parfait (250). Le bouclier n'a pas cette ligne : écarté.
    over = forge.ranking(conn, ws, forge.OVER, over=(VITA, 5)).set_index("Objet")
    assert list(over.index) == ["Anneau"]
    assert over.loc["Anneau", "Moins cher selon critère"] == 600_000 and over.loc["Anneau", "Correspondent"] == 1
    assert over.loc["Anneau", "Prime sur la base"] == 550_000
    assert pd_isna(forge.ranking(conn, ws, forge.OVER, over=(VITA, 20)).set_index("Objet").loc["Anneau", "Prime sur la base"])
    assert forge.ranking(conn, ws, forge.OVER, over=(CHANCE, 1)).empty  # Chance n'est une ligne de base nulle part
    assert forge.base_line_counts(conn) == {PA: 1, VITA: 1}
    perfect = forge.ranking(conn, ws, forge.PERFECT).set_index("Objet")
    assert perfect.loc["Anneau", "Moins cher selon critère"] == 90_000
    assert forge.all_exo_counts(conn) == {CHANCE: 2}


def pd_isna(value) -> bool:
    import pandas as pd

    return bool(pd.isna(value))


def test_ranking_exact_value(conn):
    """« 1 % de dommages aux sorts » ne doit pas montrer ceux à 2 %."""
    ws = workspace(conn)
    pick = lambda frame, name: frame.set_index("Objet").loc[name, "Moins cher selon critère"]  # noqa: E731
    # Exo Chance : l'anneau l'a à 15, le bouclier à 5.
    exact5 = forge.ranking(conn, ws, forge.EXO, CHANCE, exo_value=5, exact=True)
    assert pd_isna(pick(exact5, "Anneau")) and pick(exact5, "Bouclier") == 1_000_000
    at_least5 = forge.ranking(conn, ws, forge.EXO, CHANCE, exo_value=5)
    assert pick(at_least5, "Anneau") == 400_000 and pick(at_least5, "Bouclier") == 1_000_000
    exact15 = forge.ranking(conn, ws, forge.EXO, CHANCE, exo_value=15, exact=True)
    assert pick(exact15, "Anneau") == 400_000 and pd_isna(pick(exact15, "Bouclier"))
    # Sans valeur, « exact » ne change rien : n'importe quelle valeur de l'exo.
    assert pick(forge.ranking(conn, ws, forge.EXO, CHANCE, exact=True), "Anneau") == 400_000
    # L'over reste « au moins N au-dessus du jet parfait », même si exact est demandé : l'annonce à 260 passe pour +5.
    assert pick(forge.ranking(conn, ws, forge.OVER, over=(VITA, 5), exact=True), "Anneau") == 600_000


def test_filter_exact():
    from dofustool.analysis.forgemagie import classify

    template = {VITA: (201, 250)}
    one, two = classify([(VITA, 250), (CHANCE, 1)], template), classify([(VITA, 250), (CHANCE, 2)], template)
    assert Filter({}, exo=CHANCE, exo_min=1, exact=True).matches(one)
    assert not Filter({}, exo=CHANCE, exo_min=1, exact=True).matches(two)
    assert Filter({}, exo=CHANCE, exo_min=1).matches(two)
    assert Filter({VITA: 250}, exact=True).matches(one) and not Filter({VITA: 240}, exact=True).matches(one)
    assert "exact" not in Filter({VITA: 250}, exact=True).to_config()  # jamais enregistré dans les critères par objet
