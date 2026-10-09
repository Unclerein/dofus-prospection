"""Guides de l'application Ganymède : faux dossier de données, objets et quantités lus dans le texte des étapes."""
import json

import pytest

from dofustool import ganymede
from dofustool.web.api import Api

from .test_app import app_db  # noqa: F401  (fixture : une base complète sur disque)


def tag(item_id: int, name: str = "Objet") -> str:
    return (
        f'<span data-type="custom-tag" data-id="{item_id}" class="tag-item" id="9" type="item" name="{name}" '
        f'imageurl="https://exemple.invalid/{item_id}.png" dofusdbid="{item_id}"><span>{name}</span></span>'
    )


def box(content: str) -> str:
    return f'<li data-checked="false" data-type="taskItem"><label><input type="checkbox"><span></span></label><div><p>{content}</p></div></li>'


STEPS = [
    # À préparer : la liste à cocher annonce ce qui servira plus loin.
    {"web_text": f'<ul data-type="taskList">{box("10 " + tag(3))}{box(tag(2))}{box("1 000 " + tag(1))}</ul>'},
    {"web_text": f"<p>Donnez 10 {tag(3)} au garde, puis 5 {tag(3)} de plus.</p>"},  # 15 en tout : plus que la liste
    {"web_text": f'<p>Parlez à <span data-type="custom-tag" type="quest" dofusdbid="3">une quête</span> puis donnez {tag(2)}.</p>'},
    {"web_text": f"<p>Achetez 4 x {tag(500)} et ramassez {tag(999_999)}.</p>"},
]


@pytest.fixture
def root(tmp_path):
    (tmp_path / "guides" / "gp").mkdir(parents=True)
    (tmp_path / "guides" / "gp" / "7.json").write_text(json.dumps({"id": 7, "name": "[GP7]  Guide  test", "steps": STEPS}), encoding="utf-8")
    (tmp_path / "guides" / "8.json").write_text(json.dumps({"id": 8, "name": "Autre guide", "steps": STEPS[:1]}), encoding="utf-8")
    (tmp_path / "guides" / "9.json").write_text("pas du json", encoding="utf-8")
    conf = {
        "profileInUse": "b",
        "profiles": [
            {"id": "a", "progresses": [{"id": 8, "currentStep": 1, "updatedAt": "2026-10-01"}]},
            {"id": "b", "progresses": [{"id": 7, "currentStep": 1, "updatedAt": "2026-10-09", "steps": {"0": {"checkboxes": [0]}}}]},
        ],
    }
    (tmp_path / "conf.json").write_text(json.dumps(conf), encoding="utf-8")
    return tmp_path


def test_guides_put_the_ones_in_progress_first(root, tmp_path):
    found = ganymede.guides(root)
    assert [(g.id, g.name, g.steps, g.current_step, g.started, g.done, g.listed) for g in found] == [
        (7, "[GP7]  Guide  test", 4, 1, True, False, False),
        (8, "Autre guide", 1, 0, False, False, False),  # avancé par un autre profil : pas commencé pour celui-ci
    ]
    assert ganymede.guides(tmp_path / "absent") == []


def test_a_guide_without_an_opening_list_asks_for_nothing(root):
    """Pas de liste de ressources au début : rien à réunir, même si le texte cite des objets."""
    assert ganymede.needs(7, root) is None and ganymede.needs(7, root, from_start=True) is None
    assert ganymede.needs(9, root) is None and ganymede.needs(404, root) is None


def quest(name: str, status: str) -> str:
    return f'<div data-type="quest-block" class="quest-block" title="{name}" questid="1" questname="{name}" status="{status}"><p>…</p></div>'


def test_the_opening_list_is_the_only_source(root):
    """La liste des ressources du début fait foi : ni récompense, ni objet cité en route. Une quête finie n'est plus comptée."""
    steps = [
        {"web_text": f"<p>Ce guide permet l'obtention de {tag(500, 'Dofus')}.</p>"},
        {"web_text": "<p>Prévoyez les ressources suivantes :</p>"
                     f"<p>L'épopée du moine :</p>" f'<ul data-type="taskList">{box("5 " + tag(1))}{box(tag(2))}</ul>'
                     f'<p>Deux souffles, une inspiration :</p><ul data-type="taskList">{box("10x " + tag(1))}{box("3 " + tag(3))}</ul>'},
        {"web_text": f'<p>(Suite ressources)</p><p>Sans quête :</p><ul data-type="taskList">{box("2 " + tag(3))}</ul>'},
        {"web_text": quest("L'épopée du moine", "start") + f"<p>Donnez 5 {tag(1)} et ramassez {tag(999_999)}.</p>"},
        {"web_text": quest("L'épopée du moine", "end") + f"<p>Vous obtenez {tag(500, 'Dofus')}.</p>"},
        {"web_text": quest("Deux souffles, une inspiration", "start") + f'<ul data-type="taskList">{box("40 " + tag(2))}</ul>'},
    ]
    (root / "guides" / "7.json").write_text(json.dumps({"id": 7, "name": "Guide à liste", "steps": steps}), encoding="utf-8")
    (root / "guides" / "gp" / "7.json").unlink()
    whole = ganymede.needs(7, root, from_start=True)
    assert whole.items == {1: 15, 2: 1, 3: 5} and whole.peak == {1: 10, 2: 1, 3: 3}  # ni le Dofus, ni l'objet ramassé, ni la liste de l'étape 5
    assert ganymede.needs(7, root).items == {1: 15, 2: 1, 3: 5}  # étape 1 : aucune quête finie
    conf = json.loads((root / "conf.json").read_text(encoding="utf-8"))
    conf["profiles"][1]["progresses"][0]["currentStep"] = 5  # la fin de la première quête est derrière
    (root / "conf.json").write_text(json.dumps(conf), encoding="utf-8")
    assert ganymede.needs(7, root).items == {1: 10, 3: 5}


def test_a_guide_becomes_a_workshop_list(app_db, root):  # noqa: F811
    api = Api(app_db)
    api.ganymede_dir = root
    assert api.ganymede_guides()["guides"] == []  # aucun des deux guides n'a de liste de ressources
    with pytest.raises(ValueError):
        api.workshop_action({"action": "ganymede", "guide": 7})
    steps = [
        {"web_text": "<p>Liste des ressources :</p>"
                     f'<p>Première quête :</p><ul data-type="taskList">{box("15 " + tag(3))}{box(tag(500))}{box(tag(999_999))}</ul>'
                     f'<p>Seconde quête :</p><ul data-type="taskList">{box("2 " + tag(3))}{box(tag(500))}</ul>'},
        *STEPS,
    ]
    (root / "guides" / "gp" / "7.json").write_text(json.dumps({"id": 7, "name": "[GP7]  Guide  test", "steps": steps}), encoding="utf-8")
    assert [g["id"] for g in api.ganymede_guides()["guides"]] == [7]
    done = api.workshop_action({"action": "ganymede", "guide": 7})
    assert done["skipped"] == 1  # l'objet inconnu de la base
    (imported,) = [entry for entry in api.workshop()["lists"] if entry["id"] == done["list"]]
    assert imported["name"] == "[GP7] Guide test"
    # L'équipement 500, demandé par les deux quêtes, est le même exemplaire ; la ressource 3 s'additionne.
    assert sorted((goal["item_id"], goal["quantity"]) for goal in imported["goals"]) == [(3, 17), (500, 1)]
    with pytest.raises(ValueError):
        api.workshop_action({"action": "ganymede", "guide": 404})
