"""Fabrique l'archive à déposer sur un serveur pour y faire tourner le hub de partage.

Elle ne contient que le code dont le hub a besoin (bibliothèque standard de Python uniquement) et
les fichiers d'installation de deploy/hub. Aucune donnée, aucun réglage personnel.

Usage : python -m dofustool.share.bundle   ->   data/prospection-hub.zip
"""
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "hub"
TARGET = ROOT / "data" / "prospection-hub.zip"


def hub_modules() -> list[Path]:
    """Fichiers du paquet réellement chargés par le hub, relevés dans un interpréteur neuf."""
    code = "import sys, dofustool.share.hub as _; print('\\n'.join(m.__file__ for n, m in sys.modules.items() if n.split('.')[0] == 'dofustool'))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True).stdout
    return sorted(Path(line) for line in out.splitlines() if line)


def build(target: Path = TARGET) -> list[str]:
    target.parent.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in hub_modules():
            name = path.relative_to(ROOT).as_posix()
            archive.write(path, name)
            names.append(name)
        for path in sorted(DEPLOY.iterdir()):
            # Fins de ligne Unix obligatoires pour les scripts lancés sur le serveur.
            archive.writestr(path.name, path.read_bytes().replace(b"\r\n", b"\n"))
            names.append(path.name)
    return names


def main() -> int:
    names = build()
    print(f"{TARGET} : {len(names)} fichiers, {TARGET.stat().st_size // 1024} Ko")
    return 0


if __name__ == "__main__":
    sys.exit(main())
