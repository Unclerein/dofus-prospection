import pytest

from dofustool.staticdata import source

pytestmark = pytest.mark.skipif(
    not source.DOFUS_SQLITE.exists(), reason="data/static/dofus.sqlite absent (voir README)"
)

GELANO = 2469


@pytest.fixture(scope="module")
def conn():
    c = source.connect()
    yield c
    c.close()


def test_find_item_by_french_name(conn):
    assert source.find_item_ids(conn, "Gelano") == [GELANO]


def test_recipe_of_known_item(conn):
    recipe = source.recipe_of(conn, GELANO)
    assert recipe["job"] == "Bijoutier"
    assert recipe["level"] == 60
    assert recipe["ingredients"]
    assert all(i["name"] and i["quantity"] > 0 for i in recipe["ingredients"])


def test_weapon_recipe_resolves_names(conn):
    (sword,) = source.find_item_ids(conn, "Épée de Boisaille")
    recipe = source.recipe_of(conn, sword)
    assert recipe["job"] == "Forgeron"
    assert all(i["name"] for i in recipe["ingredients"])


def test_uncraftable_item_has_no_recipe(conn):
    (wheat,) = source.find_item_ids(conn, "Blé")
    assert source.recipe_of(conn, wheat) is None
