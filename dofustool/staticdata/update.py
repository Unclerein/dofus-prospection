"""Télécharge la dernière release de ledouxm/dofus-sqlite si elle a changé, puis réimporte.

Usage : python -m dofustool.staticdata.update [--force] [--import-only] [--if-new]
  --if-new : ne rien faire si la release locale est déjà la dernière (appel du lanceur, après chaque partie).
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from . import source
from .importer import VERSION_FILE, import_static, read_version

RELEASE_API = "https://api.github.com/repos/ledouxm/dofus-sqlite/releases/latest"
ASSETS = ("dofus.sqlite", "dofus.proto")
_HEADERS = {"User-Agent": "dofustool"}


def latest_release() -> tuple[str, dict[str, str]]:
    """Renvoie (tag, {nom de fichier: URL}) de la dernière release."""
    request = urllib.request.Request(RELEASE_API, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    urls = {a["name"]: a["browser_download_url"] for a in release["assets"] if a["name"] in ASSETS}
    missing = set(ASSETS) - urls.keys()
    if missing:
        raise RuntimeError(f"fichiers absents de la release {release['tag_name']} : {sorted(missing)}")
    return release["tag_name"], urls


def download(url: str, target: Path) -> None:
    """Télécharge vers un fichier temporaire, renommé seulement une fois complet."""
    part = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as out:
        expected = int(response.headers.get("Content-Length") or 0)
        while chunk := response.read(1 << 20):
            out.write(chunk)
    if expected and part.stat().st_size != expected:
        part.unlink()
        raise RuntimeError(f"téléchargement incomplet de {target.name}")
    os.replace(part, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="retélécharger même si la version est la même")
    parser.add_argument("--import-only", action="store_true", help="réimporter le fichier local sans rien télécharger")
    parser.add_argument("--if-new", action="store_true", help="ne rien faire si la release locale est déjà la dernière")
    args = parser.parse_args()

    directory = source.DOFUS_SQLITE.parent
    directory.mkdir(parents=True, exist_ok=True)
    if not args.import_only:
        tag, urls = latest_release()
        current = read_version()
        if tag == current and source.DOFUS_SQLITE.exists() and not args.force:
            print(f"Données statiques déjà à jour ({tag}).")
            if args.if_new:
                return 0
        else:
            print(f"Téléchargement de la release {tag} (version locale : {current})…")
            for name in ASSETS:
                download(urls[name], directory / name)
            VERSION_FILE.write_text(tag + "\n", encoding="utf-8")
    counts = import_static()
    print(f"Import ({read_version()}) : " + ", ".join(f"{n} {t}" for t, n in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
