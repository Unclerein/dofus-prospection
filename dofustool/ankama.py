"""Retrouve le launcher Ankama sur ce PC, quand le chemin de config.toml ne mène à rien.

Dans l'ordre : les programmes installés déclarés à Windows (registre), les dossiers d'installation
habituels, puis une recherche peu profonde sur chaque disque (un launcher installé dans D:\\Jeux\\…).

Usage : python -m dofustool.ankama [--save]
  Affiche le chemin trouvé (code de sortie 1 si rien). --save l'écrit dans config.toml, seulement si
  le chemin enregistré n'existe pas.
"""
import argparse
import os
import string
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from . import config

EXE = "Ankama Launcher.exe"
UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"


def registry_entries() -> list[dict[str, str]]:
    """Programmes installés (DisplayName, InstallLocation, DisplayIcon) dont le nom parle d'Ankama."""
    try:
        import winreg
    except ImportError:  # hors Windows
        return []
    found = []
    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    views = (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY)
    for root in roots:
        for view in views:
            try:
                base = winreg.OpenKey(root, UNINSTALL, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with base:
                for index in range(winreg.QueryInfoKey(base)[0]):
                    try:
                        with winreg.OpenKey(base, winreg.EnumKey(base, index)) as key:
                            entry = {}
                            for name in ("DisplayName", "InstallLocation", "DisplayIcon"):
                                try:
                                    entry[name] = str(winreg.QueryValueEx(key, name)[0])
                                except OSError:
                                    pass
                    except OSError:
                        continue
                    if "ankama" in entry.get("DisplayName", "").lower():
                        found.append(entry)
    return found


def _from_registry(entries: Iterable[dict[str, str]]) -> list[Path]:
    out = []
    for entry in entries:
        location = entry.get("InstallLocation", "").strip().strip('"')
        if location:
            out.append(Path(location) / EXE)
        icon = entry.get("DisplayIcon", "").split(",")[0].strip().strip('"')
        if icon.lower().endswith(".exe"):
            out.append(Path(icon))
    return out


def _usual(env: dict[str, str]) -> list[Path]:
    out = []
    local = env.get("LOCALAPPDATA")
    if local:
        out += [Path(local, "Programs", "zaap", EXE), Path(local, "Programs", "Ankama Launcher", EXE)]
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = env.get(key)
        if base:
            out += [Path(base, "Ankama", "Ankama Launcher", EXE), Path(base, "Ankama Launcher", EXE)]
    return out


def _search_roots(env: dict[str, str], drives: Iterable[str]) -> list[Path]:
    roots = [Path(env["LOCALAPPDATA"], "Programs")] if env.get("LOCALAPPDATA") else []
    roots += [Path(env[key]) for key in ("ProgramFiles", "ProgramFiles(x86)") if env.get(key)]
    return roots + [Path(d) for d in drives]


def windows_drives() -> list[str]:
    return [f"{letter}:\\" for letter in string.ascii_uppercase if os.path.isdir(f"{letter}:\\")]


def find(
    configured: str = "",
    env: dict[str, str] | None = None,
    registry: Callable[[], list[dict[str, str]]] | None = None,
    drives: Callable[[], list[str]] | None = None,
) -> Path | None:
    """Chemin du launcher : celui de la configuration s'il existe, sinon le premier trouvé."""
    env = dict(os.environ) if env is None else env
    registry = registry or registry_entries
    drives = drives or windows_drives
    if configured and Path(configured).is_file():
        return Path(configured)
    for path in [*_from_registry(registry()), *_usual(env)]:
        if path.name.lower() == EXE.lower() and path.is_file():
            return path
    # Dernier recours : le launcher installé dans un dossier de son choix, à deux niveaux au plus.
    for root in _search_roots(env, drives()):
        for pattern in (EXE, f"*/{EXE}", f"*/*/{EXE}"):
            try:
                match = next((p for p in root.glob(pattern) if p.is_file()), None)
            except OSError:
                match = None
            if match is not None:
                return match
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", action="store_true", help="écrire le chemin trouvé dans config.toml")
    args = parser.parse_args()
    cfg = config.load()
    path = find(cfg.ankama_path)
    if path is None:
        if not args.save:  # les lanceurs PowerShell lisent la sortie : rien sur stderr pour eux
            print("Launcher Ankama introuvable.", file=sys.stderr)
        return 1
    if args.save and str(path) != cfg.ankama_path:
        import dataclasses

        config.save(dataclasses.replace(cfg, ankama_path=str(path)))
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
