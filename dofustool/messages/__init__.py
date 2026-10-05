"""Parseurs par nom logique. keymap.json fait le lien avec les clés et champs du build courant."""
import json
from dataclasses import dataclass
from pathlib import Path

KEYMAP_PATH = Path(__file__).resolve().parents[1] / "keymap.json"


@dataclass(frozen=True, slots=True)
class Mapping:
    key: str
    fields: dict[str, int]


def load_keymap(path: Path = KEYMAP_PATH) -> dict[str, Mapping]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {name: Mapping(entry["key"], entry["fields"]) for name, entry in raw.items()}
