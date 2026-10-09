"""Lit les guides de l'application Ganymède installée sur ce PC, pour en tirer une liste de courses.

Ganymède garde ses guides téléchargés et la progression du joueur dans son dossier de données
(%APPDATA%\\com.ganymede.ganymede-app). Ces fichiers sont seulement lus, jamais modifiés, et rien
n'en sort : la liste des objets part dans l'atelier, sur ce PC.

Dans le texte d'une étape, un objet est une balise qui porte son identifiant du jeu, précédée de la
quantité quand il en faut plusieurs (« 10 [Dagues de Boisaille] »).

La plupart des guides s'ouvrent sur la liste des ressources à prévoir, quête par quête : c'est elle qui
fait foi, et rien d'autre n'est retenu (le reste du texte cite aussi des récompenses, des objets de
quête, des prérequis). Une quête que le joueur a déjà terminée n'est plus comptée. Un guide sans cette
liste est lu en entier, faute de mieux : un même besoin y est souvent écrit deux fois (à préparer, puis
à l'étape où l'objet sert), et l'on retient pour chaque objet le plus grand des deux totaux.

C'est une lecture du texte d'un guide, pas une donnée exacte : à relire avant d'acheter.
"""
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

ITEM = re.compile(r'(\d[\d\s  ]*)?\s*(?:[x×]\s*)?<span data-type="custom-tag"[^>]*\btype="item"[^>]*\bdofusdbid="(\d+)"')
TASK = re.compile(r'<li[^>]*data-type="taskItem"[^>]*>(.*?)</li>', re.S)
MAX_QUANTITY = 100_000  # au-delà, le nombre lu devant l'objet n'était pas une quantité
TASK_LIST = re.compile(r'<ul data-type="taskList">(.*?)</ul>', re.S)
PARAGRAPH = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
QUEST_BLOCK = re.compile(r'data-type="quest-block"[^>]*>')
OPENING = re.compile(r"ressources|pr[ée]voyez", re.I)  # « Liste des ressources », « Prévoyez les ressources suivantes »…
OPENING_STEPS = 8  # la liste des ressources tient dans les toutes premières étapes
OPENING_CHARS = 160  # … et s'annonce dès le début de l'étape


def data_dir() -> Path | None:
    base = os.environ.get("APPDATA")
    return Path(base) / "com.ganymede.ganymede-app" if base else None


@dataclass(frozen=True, slots=True)
class Guide:
    id: int
    name: str
    steps: int
    current_step: int  # étape où en est le joueur (0 : pas commencé)
    updated_at: str  # dernière progression, texte ISO ou ""

    @property
    def started(self) -> bool:
        return self.current_step > 0

    @property
    def done(self) -> bool:
        return self.started and self.current_step >= self.steps - 1


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _progress(root: Path) -> dict[int, dict]:
    """Progression du profil en cours, par guide."""
    conf = _read(root / "conf.json")
    if not isinstance(conf, dict):
        return {}
    profiles = [p for p in conf.get("profiles") or [] if isinstance(p, dict)]
    profile = next((p for p in profiles if p.get("id") == conf.get("profileInUse")), profiles[0] if profiles else None)
    if profile is None:
        return {}
    return {p["id"]: p for p in profile.get("progresses") or [] if isinstance(p, dict) and isinstance(p.get("id"), int)}


def _files(root: Path) -> dict[int, Path]:
    found = {}
    for path in (root / "guides").rglob("*.json"):
        if path.stem.isdigit():
            found[int(path.stem)] = path
    return found


def guides(root: Path | None = None) -> list[Guide]:
    """Guides téléchargés, ceux en cours d'abord (le plus récemment avancé en tête), puis les autres par nom."""
    root = root or data_dir()
    if root is None or not (root / "guides").is_dir():
        return []
    progress = _progress(root)
    out = []
    for guide_id, path in _files(root).items():
        content = _read(path)
        if not isinstance(content, dict) or not isinstance(content.get("steps"), list):
            continue
        state = progress.get(guide_id, {})
        current = state.get("currentStep") if isinstance(state.get("currentStep"), int) else 0
        out.append(Guide(guide_id, str(content.get("name") or f"Guide {guide_id}"), len(content["steps"]), current, str(state.get("updatedAt") or "")))
    out.sort(key=lambda g: g.name.lower())
    out.sort(key=lambda g: g.updated_at, reverse=True)
    out.sort(key=lambda g: not (g.started and not g.done))
    return out


def _mentions(text: str) -> list[tuple[int, int]]:
    out = []
    for match in ITEM.finditer(text):
        quantity = int(re.sub(r"\D", "", match.group(1))) if match.group(1) else 1
        out.append((int(match.group(2)), quantity if 0 < quantity <= MAX_QUANTITY else 1))
    return out


def _plain(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ").split())


def _key(text: str) -> str:
    """Nom d'une quête sans accents, casse ni ponctuation, pour rapprocher un titre de section de son bloc de quête."""
    bare = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", bare).strip()


def _opening_sections(steps: list) -> list[tuple[str, list[tuple[int, int]]]] | None:
    """[(titre de la section, [(objet, quantité)])] de la liste des ressources du début du guide, ou None sans elle.

    Le titre d'une section est la ligne qui précède sa liste à cocher : d'ordinaire le nom de la quête.
    """
    sections = []
    for step in steps[:OPENING_STEPS]:
        text = step.get("web_text") or "" if isinstance(step, dict) else ""
        if not OPENING.search(_plain(text)[:OPENING_CHARS]):
            continue
        end = 0
        for found in TASK_LIST.finditer(text):
            titles = [title for title in map(_plain, PARAGRAPH.findall(text[end:found.start()])) if title]
            items = [mention for task in TASK.findall(found.group(0)) for mention in _mentions(task)]
            if items:
                sections.append((_key(titles[-1]) if titles else "", items))
            end = found.end()
    return sections or None


def _finished_quests(steps: list, current: int) -> set[str]:
    """Quêtes dont la fin est écrite dans une étape déjà passée."""
    done = set()
    for step in steps[:current]:
        text = step.get("web_text") or "" if isinstance(step, dict) else ""
        for block in QUEST_BLOCK.findall(text):
            name = re.search(r'questname="([^"]*)"', block)
            if name and 'status="end"' in block:
                done.add(_key(name.group(1)))
    return done


@dataclass(frozen=True, slots=True)
class Needs:
    items: dict[int, int]  # objet -> quantité, les demandes de toutes les quêtes ajoutées
    listed: bool  # vrai : tiré de la liste des ressources du début ; faux : guide sans liste, lu en entier
    # objet -> plus grosse demande d'une seule quête. Pour ce qu'une quête demande d'avoir sur soi sans le
    # consommer (un Dofus, un équipement), c'est le vrai besoin : le même exemplaire sert d'une quête à l'autre.
    peak: dict[int, int] = field(default_factory=dict)


def needs(guide_id: int, root: Path | None = None, from_start: bool = False) -> Needs | None:
    """Ce que demande ce qu'il reste du guide, ou None s'il est introuvable.

    from_start : tout le guide, sans tenir compte de la progression. Les cases cochées ne sont pas lues : leur
    numérotation ne correspond pas toujours au texte du guide téléchargé, et l'atelier déduit de toute façon le stock.
    """
    root = root or data_dir()
    if root is None:
        return None
    path = _files(root).get(guide_id) if (root / "guides").is_dir() else None
    content = _read(path) if path is not None else None
    if not isinstance(content, dict) or not isinstance(content.get("steps"), list):
        return None
    steps = content["steps"]
    state = {} if from_start else _progress(root).get(guide_id, {})
    current = state.get("currentStep") if isinstance(state.get("currentStep"), int) else 0
    sections = _opening_sections(steps)
    if sections is not None:
        finished = _finished_quests(steps, current)
        items: dict[int, int] = {}
        peak: dict[int, int] = {}
        for title, mentions in sections:
            if title in finished:
                continue  # quête déjà rendue : ses ressources ont servi
            asked: dict[int, int] = {}
            for item_id, quantity in mentions:
                asked[item_id] = asked.get(item_id, 0) + quantity
            for item_id, quantity in asked.items():
                items[item_id] = items.get(item_id, 0) + quantity
                peak[item_id] = max(peak.get(item_id, 0), quantity)
        return Needs(items, True, peak)
    listed: dict[int, int] = {}
    told: dict[int, int] = {}
    for index, step in enumerate(steps):
        if index < current or not isinstance(step, dict):
            continue
        text = step.get("web_text") or ""
        for task in TASK.findall(text):
            for item_id, quantity in _mentions(task):
                listed[item_id] = listed.get(item_id, 0) + quantity
        for item_id, quantity in _mentions(TASK.sub("", text)):
            told[item_id] = told.get(item_id, 0) + quantity
    return Needs({item_id: max(listed.get(item_id, 0), told.get(item_id, 0)) for item_id in listed.keys() | told.keys()}, False)
