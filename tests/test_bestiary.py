import pytest

from dofustool.staticdata import source

pytestmark = pytest.mark.skipif(not source.DOFUS_SQLITE.exists(), reason="data/static/dofus.sqlite absent")


@pytest.fixture(scope="module")
def game():
    from dofustool.bestiary.monster import Game

    return Game()


def test_line_of_sight_and_in_line_flags_match_known_class_spells(game):
    from dofustool.bestiary import effects as fx

    flags = lambda spell: game.levels[spell][3]  # noqa: E731
    assert flags(13106) & fx.LINE_OF_SIGHT and not flags(13106) & fx.IN_LINE  # Pression
    assert flags(13108) & fx.IN_LINE  # Intimidation : en ligne
    assert not flags(12736) & fx.LINE_OF_SIGHT  # Transposition : sans ligne de vue


def test_klime_is_invulnerable_until_pushed_into_something(game):
    sheet = game.sheet(3384)
    assert sheet.name == "Klime" and sheet.reach == "Tape à 7 cases, sans ligne de vue"
    assert sheet.mechanics[0] == "Invulnérable ; saute quand il subit des dommages de poussée"
    assert "Attire de 6 cases" in sheet.moves and "Galuchat" not in sheet.passive and "Invulnérable" in sheet.passive


def test_reflecting_and_invisible_monsters_of_the_comte(game):
    assert game.sheet(3415).mechanics[0].startswith("Quand il subit des dommages de mêlée : renvoie 100 % des dommages")  # Nocturlabe
    sinistrofu = game.sheet(3414)
    assert sinistrofu.mechanics[0] == "Devient invisible 1 tour, tous les 2 tours"
    assert sinistrofu.reach == "Tape à 6 cases, sans ligne de vue"


def test_a_dungeon_says_once_what_all_its_monsters_share(game):
    name, bosses, sheets, common = game.dungeon_sheets(140)
    assert name == "Chambre des maléfices" and bosses == [7210]
    assert [s.name for s in sheets] == ["Soldalia", "Caméliache", "Armuguet", "Coquelicogne", "Statulipe", "Belladone"]
    assert common and all(line not in s.mechanics for s in sheets if s.id != 7210 for line in common)
    belladone = sheets[-1]
    assert belladone.reach == "Frappe partout sur la carte"
    assert any("Échange de position sur dommages directs reçus" in line for line in belladone.mechanics)
    assert next(s for s in sheets if s.name == "Statulipe").reach == "Tape à 8 cases (en ligne) ; 4 sans ligne de vue"


def test_effect_text_reads_like_the_game_tooltip(game):
    assert game.effect_text({"actionId": 99, "diceNum": 21, "diceSide": 30}) == "21 à 30 dommages Feu"
    assert game.effect_text({"actionId": 6, "diceNum": 6}) == "Attire de 6 cases"
    assert game.effect_text({"actionId": 6, "diceNum": 1}) == "Attire de 1 case"
    assert game.effect_text({"actionId": 99, "diceNum": 255297}) == ""  # valeur codée, pas un nombre de dommages


def test_every_written_note_reads_and_only_names_monsters_of_its_dungeon(game):
    from dofustool.bestiary import notes

    loaded = notes.load()
    assert {98, 71, 75, 28, 107} <= set(loaded)
    for dungeon, note in loaded.items():
        _, bosses, monsters = game.dungeon(dungeon)
        assert set(note.monsters) <= set(monsters) and set(note.focus) <= set(monsters), dungeon
        assert all(kind in ("ok", "danger") for kind, _, _ in note.essentials)
    assert 3384 not in loaded[71].monsters  # Klime, Frizz, Nileza, Sylargh : le Comte à deux boss est un cas à part


def test_a_card_takes_the_note_first_and_the_game_data_where_the_note_says_nothing(game):
    from dofustool.bestiary import card, notes

    loaded = notes.load()
    klutiste = card.build(game, loaded, 4452, 98)
    assert klutiste.focus_rank == 1 and not klutiste.boss and klutiste.weakest == "Terre"
    assert klutiste.subtitle == "Temple de Koutoulou · niv. 212" and [s.name for s in klutiste.spells][-1] == "Mélopée Pernicieuse"
    larve = card.build(game, loaded, 4453, game.dungeon_of_map(None))
    assert larve.boss and len(larve.essentials) == 5 and [name for _, name, _ in larve.focus] == ["Klûtiste", "Li-Fo", "Shokkoth"]
    soldalia = card.build(game, loaded, 7205)  # pas de note : la fiche calculée
    assert soldalia.rows[0] == ("PORTÉE", "Tape à 12 cases") and soldalia.footer == "Calculé depuis les données du jeu"
    assert game.dungeon_of_map(112206595) == 71  # salle du Comte : retrouvée par sa sous-zone


def test_the_sheet_image_marks_its_close_cross_its_header_and_the_boss_spells(game):
    from dofustool.bestiary import card, notes
    from dofustool.overlay import render

    loaded = notes.load()
    image, regions = render.sheet_image(card.build(game, loaded, 4453, 98))
    assert set(regions) == {"close", "drag", "spells"} and regions["close"][0] > image.width / 2
    opened, _ = render.sheet_image(card.build(game, loaded, 4453, 98), open_spells=True)
    assert opened.height > image.height  # les sorts du boss se déplient
    small, regions = render.sheet_image(card.build(game, loaded, 4452, 98), scale=1.0)
    big, _ = render.sheet_image(card.build(game, loaded, 4452, 98), scale=1.5)
    assert "spells" not in regions and big.width > small.width


def test_guide_pages_are_found_by_dungeon_or_boss_name(game):
    from dofustool.bestiary import prepare

    assert prepare.plain("Donjon du Comte Harebourg") == "comte harebourg" == prepare.plain("Comte  Harebourg")
    assert prepare.plain("Canopée du Kimbo") == "canopee du kimbo"
    links = {"comte harebourg": "https://x/comte.html", "temple de koutoulou": "https://x/kout.html"}
    assert prepare.guide_url(game, 71, links) == "https://x/comte.html"
    assert prepare.guide_url(game, 98, links) == "https://x/kout.html" and prepare.guide_url(game, 28, links) is None
    text = prepare.to_text("<p>Folie&nbsp;2 :<br>reste à 3 PO</p><script>x()</script><div>fin</div>")
    assert text == "Folie 2 :\nreste à 3 PO\nfin"


def test_a_wanted_poster_note_makes_its_monster_the_star_of_the_card(game, tmp_path):
    from dofustool.bestiary import card, notes

    (tmp_path / "460-frakacia.toml").write_text(
        'avis = 460\nnom = "Frakacia Leukocytine"\nsource = "Dofus pour les Noobs — https://x"\ndate_source = "2021-06-15"\n'
        'statut = "à confirmer"\nfocus = []\n\n[[essentiel]]\ntype = "danger"\ntitre = "Seule"\ntexte = "Elle frappe fort."\n\n'
        '[monstres.460]\nnom = "Frakacia Leukocytine"\nmecanique = "Se soigne"\n',
        encoding="utf-8",
    )
    wanted = notes.load_wanted(tmp_path)
    assert set(wanted) == {460} and wanted[460].dungeon == 0
    sheet = card.build(game, notes.load(), 460, None, wanted)
    assert sheet.boss and sheet.label == "AVIS DE RECHERCHE" and sheet.subtitle.startswith("Avis de recherche")
    assert ("SPÉCIAL", "Se soigne") in sheet.rows and sheet.essentials == (("danger", "Seule", "Elle frappe fort."),)
