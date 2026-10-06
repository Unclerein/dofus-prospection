"""Parseurs par nom logique. keymap.json fait le lien avec les clés et champs du build courant."""
import json
from dataclasses import dataclass
from pathlib import Path

KEYMAP_PATH = Path(__file__).resolve().parents[1] / "keymap.json"  # table livrée avec le code
RUNTIME_KEYMAP_PATH = Path(__file__).resolve().parents[2] / "data" / "keymap.json"


def runtime_keymap_path() -> Path:
    """Table des clés de ce PC, que la capture corrige seule après une mise à jour du jeu.

    Elle vit dans data/ (hors du dépôt) : une mise à jour du code ne l'écrase pas, et elle ne
    bloque pas la mise à jour. La table livrée ne sert qu'à la créer.
    """
    if not RUNTIME_KEYMAP_PATH.exists():
        RUNTIME_KEYMAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_KEYMAP_PATH.write_bytes(KEYMAP_PATH.read_bytes())
    return RUNTIME_KEYMAP_PATH


def load_runtime_keymap() -> dict[str, "Mapping"]:
    return load_keymap(runtime_keymap_path())


@dataclass(frozen=True, slots=True)
class Mapping:
    key: str
    fields: dict[str, int]


def load_keymap(path: Path = KEYMAP_PATH) -> dict[str, Mapping]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    # « stale » : clé d'un ancien build, pas encore retrouvée sur le nouveau. L'utiliser ferait lire
    # un autre message sous l'ancien nom.
    return {name: Mapping(entry["key"], entry["fields"]) for name, entry in raw.items() if not entry.get("stale")}
