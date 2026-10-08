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
    else:
        _add_new_messages(RUNTIME_KEYMAP_PATH)
    return RUNTIME_KEYMAP_PATH


def _add_new_messages(path: Path) -> None:
    """Ajoute à la table de ce PC les messages qu'une mise à jour du code vient d'apprendre à décoder.

    Seulement si les deux tables décrivent le même build du jeu (mêmes prix moyens) : sinon les clés
    livrées sont celles d'un autre build, et la capture retrouvera ces messages par leur structure.
    """
    try:
        shipped = json.loads(KEYMAP_PATH.read_text(encoding="utf-8"))
        local = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if local.get("avg_prices") != shipped.get("avg_prices"):
        return
    changed = False
    for name, entry in shipped.items():
        mine = local.get(name)
        if mine is None:
            local[name] = entry
            changed = True
        elif mine.get("key") == entry["key"] and not mine.get("stale"):
            # Même message, mieux connu : seuls les champs que ce PC n'a pas sont ajoutés, s'il n'en contredit aucun.
            extra = {field: number for field, number in entry["fields"].items() if field not in mine["fields"]}
            agrees = all(mine["fields"][field] == number for field, number in entry["fields"].items() if field in mine["fields"])
            if extra and agrees:
                mine["fields"].update(extra)
                changed = True
    if not changed:
        return
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(local, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    temp.replace(path)


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
