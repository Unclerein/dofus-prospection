"""Réglages communs aux tests : aucun appel réseau, aucun fil en arrière-plan."""
import pytest

from dofustool.staticdata import effects
from dofustool.web.api import Api


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unreachable():
        raise OSError("pas de réseau dans les tests")

    monkeypatch.setattr(effects, "fetch_version", unreachable)
    monkeypatch.setattr(Api, "background_refresh", False)
