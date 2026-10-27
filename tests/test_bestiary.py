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
