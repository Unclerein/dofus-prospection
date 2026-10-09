"""Recherche du launcher Ankama : faux registre, faux dossiers d'installation."""
import dataclasses

from dofustool import ankama, config

EXE = ankama.EXE


def launcher(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_configured_path_wins(tmp_path):
    exe = launcher(tmp_path / "ailleurs" / EXE)
    assert ankama.find(str(exe), env={}, registry=lambda: [], drives=lambda: []) == exe


def test_registry_install_location_and_icon(tmp_path):
    exe = launcher(tmp_path / "Jeux" / "Ankama" / EXE)
    entries = [
        {"DisplayName": "Ankama Launcher 3.12", "InstallLocation": f'"{exe.parent}"'},
    ]
    assert ankama.find("C:/absent.exe", env={}, registry=lambda: entries, drives=lambda: []) == exe
    icon = [{"DisplayName": "Ankama Launcher", "DisplayIcon": f'"{exe}",0'}]
    assert ankama.find("", env={}, registry=lambda: icon, drives=lambda: []) == exe


def test_usual_folders(tmp_path):
    exe = launcher(tmp_path / "Local" / "Programs" / "zaap" / EXE)
    env = {"LOCALAPPDATA": str(tmp_path / "Local")}
    assert ankama.find("", env=env, registry=lambda: [], drives=lambda: []) == exe
    exe2 = launcher(tmp_path / "PF" / "Ankama" / "Ankama Launcher" / EXE)
    assert ankama.find("", env={"ProgramFiles": str(tmp_path / "PF")}, registry=lambda: [], drives=lambda: []) == exe2


def test_shallow_search_on_drives(tmp_path):
    drive = tmp_path / "D"
    exe = launcher(drive / "Jeux" / "Ankama Launcher" / EXE)  # deux niveaux sous la racine du disque
    assert ankama.find("", env={}, registry=lambda: [], drives=lambda: [str(drive)]) == exe
    deep = tmp_path / "E"
    launcher(deep / "a" / "b" / "c" / EXE)  # trop profond : pas cherché
    assert ankama.find("", env={}, registry=lambda: [], drives=lambda: [str(deep)]) is None


def test_nothing_found(tmp_path):
    entries = [{"DisplayName": "Ankama Launcher", "InstallLocation": str(tmp_path / "désinstallé")}]
    assert ankama.find("", env={}, registry=lambda: entries, drives=lambda: []) is None


def test_main_saves_only_a_missing_path(tmp_path, monkeypatch, capsys):
    exe = launcher(tmp_path / "Programs" / "zaap" / EXE)
    path = tmp_path / "config.toml"
    config.save(dataclasses.replace(config.Config(), ankama_path="C:/n/existe/pas.exe", hdv_tax=0.03), path)
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config.load, "__defaults__", (path,))
    monkeypatch.setattr(config.save, "__defaults__", (path,))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(ankama, "registry_entries", lambda: [])
    monkeypatch.setattr(ankama, "windows_drives", lambda: [])
    monkeypatch.setattr("sys.argv", ["ankama", "--save"])
    assert ankama.main() == 0
    assert capsys.readouterr().out.strip() == str(exe)
    saved = config.load(path)
    assert saved.ankama_path == str(exe) and saved.hdv_tax == 0.03  # le reste de la configuration est gardé


def test_api_find_launcher(tmp_path, monkeypatch):
    from dofustool.web.api import Api

    exe = launcher(tmp_path / "x" / EXE)
    api = Api(tmp_path / "market.sqlite", tmp_path / "config.toml")
    monkeypatch.setattr(ankama, "registry_entries", lambda: [])
    monkeypatch.setattr(ankama, "windows_drives", lambda: [])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "vide"))
    assert api.find_launcher(str(exe)) == {"path": str(exe)}
    assert api.find_launcher("C:/absent.exe") == {"path": None}
    assert not (tmp_path / "config.toml").exists()  # chercher n'enregistre rien
