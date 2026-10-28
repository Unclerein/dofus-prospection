"""Prépare la rédaction des notes de donjon : la file des donjons à faire, et pour chacun le texte du guide et sa fiche calculée.

Usage :
  python -m dofustool.bestiary.prepare --queue [N]   la file : donjons joués d'abord (d'après l'archive locale), sans note
  python -m dofustool.bestiary.prepare --batch N     prépare les N prochains de la file
  python -m dofustool.bestiary.prepare <id> [...]    prépare ces donjons-là
  python -m dofustool.bestiary.prepare --avis-queue [N] / --avis-batch N   de même pour les avis de recherche
  python -m dofustool.bestiary.prepare --huzounet    fiches Huzounet des monstres des notes écrites (data/bestiary/huz)

Pour chaque donjon : data/bestiary/<id>-guide.txt (texte brut de la page Dofus pour les Noobs) et <id>-jeu.txt (fiche
calculée, avec les ids des monstres), plus data/bestiary/lot.json qui résume le lot. Tout reste local (data/ n'est
jamais commité). La procédure complète est dans dofustool/bestiary/REDACTION.md.
"""
import argparse
import difflib
import html
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from .. import db
from .monster import Game
from .notes import NOTES_DIR, load, load_wanted

GUIDE_SITE = "https://www.dofuspourlesnoobs.com"
HUZOUNET = "https://huzounet.fr"
WORK_DIR = db.MARKET_PATH.parent / "bestiary"
MATCH = 0.86  # ressemblance minimale entre un nom de donjon et le texte d'un lien du guide


def plain(text: str) -> str:
    """Pour comparer des noms : sans accents, sans casse, sans « donjon du / de la »."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"^(donjon|antre|repaire)( des| du| de la| de l| de)? ", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def to_text(page: str) -> str:
    """Le texte d'une page, une ligne par paragraphe."""
    page = re.sub(r"(?is)<(script|style).*?</\1>", "", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr)>", "\n", page)
    text = re.sub(r"[ \t\xa0]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def guide_links() -> dict[str, str]:
    """Texte du lien → adresse, depuis la liste des donjons du guide."""
    page = fetch(f"{GUIDE_SITE}/donjons.html")
    links = {}
    for href, label in re.findall(r'<a[^>]+href="(/[^"#?]+\.html)"[^>]*>(.*?)</a>', page, re.S):
        label = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", label))).strip()
        if label:
            links.setdefault(plain(label), GUIDE_SITE + href)
    return links


def guide_url(game: Game, dungeon: int, links: dict[str, str]) -> str | None:
    """La page du guide pour ce donjon : par son nom, ou celui de son boss (le guide titre parfois « Comte Harebourg »)."""
    name, bosses, _ = game.dungeon(dungeon)
    for candidate in [name] + [game.monster_name(b) for b in bosses]:
        key = plain(candidate)
        if key in links:
            return links[key]
        close = difflib.get_close_matches(key, list(links), n=1, cutoff=MATCH)
        if close:
            return links[close[0]]
    return None


def played(game: Game) -> Counter:
    """Donjons où je suis allé, d'après les changements de carte de l'archive locale ; vide sans archive."""
    from ..archive import connect
    from ..fight import journal, live

    visits: Counter = Counter()
    try:
        archive = connect(db.MARKET_PATH.parent / "archive.sqlite")
        rows = archive.execute("SELECT body FROM messages WHERE key = ? AND direction = 's2c'", (live.MAP_CHANGE,)).fetchall()
    except Exception:  # noqa: BLE001 — pas d'archive : la file suit seulement le niveau
        return visits
    for (body,) in rows:
        dungeon = game.dungeon_of_map(journal.number(live.flat(body, live.MAP_CHANGE), "1"))
        if dungeon:
            visits[dungeon] += 1
    return visits


def queue(game: Game) -> list[tuple[int, str, int]]:
    """(donjon, nom, visites) des donjons sans note : les plus joués d'abord, puis du plus haut niveau au plus bas."""
    done = set(load())
    visits = played(game)
    rows = game.conn.execute("SELECT id, minLevel FROM DungeonData WHERE bosses != '[]'").fetchall()
    todo = [(dungeon, level) for dungeon, level in rows if dungeon not in done]
    todo.sort(key=lambda row: (-visits[row[0]], -(row[1] or 0)))
    return [(dungeon, game.dungeon(dungeon)[0], visits[dungeon]) for dungeon, _ in todo]


def wanted_links() -> dict[str, str]:
    """Nom du recherché → adresse de sa page « On recherche », depuis la page des avis de recherche du guide."""
    page = fetch(f"{GUIDE_SITE}/avis-de-recherche.html")
    links = {}
    for href in set(re.findall(r'href="(/on-recherche-[^"#?]+\.html)"', page)):
        links[plain(href.removeprefix("/on-recherche-").removesuffix(".html").replace("-", " "))] = GUIDE_SITE + href
    return links


def wanted_url(game: Game, monster: int, links: dict[str, str]) -> str | None:
    key = plain(game.monster_name(monster))
    if key in links:
        return links[key]
    close = difflib.get_close_matches(key, list(links), n=1, cutoff=MATCH)
    return links[close[0]] if close else None


def wanted_queue(game: Game) -> list[tuple[int, str, int]]:
    """(monstre, nom, niveau) des avis de recherche sans note, du plus haut niveau au plus bas."""
    done = set(load_wanted())
    rows = []
    for monster, grades in game.conn.execute("SELECT id, grades FROM MonsterData WHERE isBounty = 1"):
        if monster not in done:
            rows.append((monster, game.monster_name(monster), json.loads(grades)[-1]["level"]))
    return sorted(rows, key=lambda row: -row[2])


def describe_monster(game: Game, monster: int) -> str:
    sheet = game.sheet(monster)
    lines = [f"== Avis de recherche : {sheet.name} ({monster}) · niv. {sheet.level} · {sheet.life} PV · {sheet.ap} PA {sheet.mp} PM · faible {sheet.weakest}"]
    for label, value in (("portée", sheet.reach), ("déplacement", " · ".join(sheet.moves)), ("spécial", " · ".join(sheet.mechanics))):
        if value:
            lines.append(f"      {label} : {value}")
    return "\n".join(lines) + "\n"


def prepare_wanted(game: Game, monsters: list[int], links: dict[str, str]) -> list[dict]:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for monster in monsters:
        entry = {"avis": monster, "nom": game.monster_name(monster), "guide": wanted_url(game, monster, links)}
        (WORK_DIR / f"avis-{monster}-jeu.txt").write_text(describe_monster(game, monster), encoding="utf-8")
        entry["jeu"] = str(WORK_DIR / f"avis-{monster}-jeu.txt")
        if entry["guide"]:
            try:
                (WORK_DIR / f"avis-{monster}-guide.txt").write_text(to_text(fetch(entry["guide"])), encoding="utf-8")
                entry["texte"] = str(WORK_DIR / f"avis-{monster}-guide.txt")
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                entry["erreur"] = f"guide injoignable : {error}"
        else:
            entry["erreur"] = "page « On recherche » introuvable"
        out.append(entry)
    (WORK_DIR / "lot.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def prepare(game: Game, dungeons: list[int], links: dict[str, str]) -> list[dict]:
    from .__main__ import describe

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for dungeon in dungeons:
        name = game.dungeon(dungeon)[0]
        entry = {"donjon": dungeon, "nom": name, "guide": guide_url(game, dungeon, links)}
        (WORK_DIR / f"{dungeon}-jeu.txt").write_text(describe(game, dungeon), encoding="utf-8")
        entry["jeu"] = str(WORK_DIR / f"{dungeon}-jeu.txt")
        if entry["guide"]:
            try:
                (WORK_DIR / f"{dungeon}-guide.txt").write_text(to_text(fetch(entry["guide"])), encoding="utf-8")
                entry["texte"] = str(WORK_DIR / f"{dungeon}-guide.txt")
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                entry["erreur"] = f"guide injoignable : {error}"
        else:
            entry["erreur"] = "page du guide introuvable dans la liste des donjons"
        out.append(entry)
    (WORK_DIR / "lot.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def huzounet_text(monster: int) -> str | None:
    """Passif, actif, stratégie et notes d'un monstre sur Huzounet ; None s'il n'en a pas (monstres ordinaires)."""
    page = fetch(f"{HUZOUNET}/monsters/{monster}")
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', page)  # données de la page (Next.js)
    flight = "".join(json.loads('"' + chunk + '"') for chunk in chunks)
    start = flight.find('"details":')
    if start < 0:
        return None
    details, _ = json.JSONDecoder().raw_decode(flight, start + len('"details":'))
    if not isinstance(details, dict):
        return None

    def lines(node: dict, out: list[str], depth: int = 0) -> None:
        children = node.get("children") or []
        if node.get("type") in ("listitem", "paragraph", "heading"):
            text = "".join(child.get("text", "") for child in children if child.get("type") == "text").strip()
            if text:
                out.append("  " * depth + "- " + text)
            for child in children:
                if child.get("type") == "list":
                    lines(child, out, depth + 1)
        else:
            for child in children:
                lines(child, out, depth)

    parts = []
    for part, title in (("passive", "PASSIF"), ("active", "ACTIF"), ("strategy", "STRATÉGIE"), ("notes", "NOTES")):
        out: list[str] = []
        if isinstance(details.get(part), dict) and isinstance(details[part].get("root"), dict):
            lines(details[part]["root"], out)
        if out:
            parts.append(f"== {title}\n" + "\n".join(out))
    return "\n\n".join(parts) or None


def prepare_huzounet(game: Game) -> list[dict]:
    """Pour chaque note écrite, la fiche Huzounet de chacun de ses monstres : data/bestiary/huz/<monstre>.txt."""
    folder = WORK_DIR / "huz"
    folder.mkdir(parents=True, exist_ok=True)
    seen: dict[int, str | None] = {}

    def text_of(monster: int) -> str | None:
        if monster not in seen:
            path, none = folder / f"{monster}.txt", folder / f"{monster}.aucune"
            if not path.exists() and not none.exists():
                try:
                    text = huzounet_text(monster)
                except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                    print(f"  {monster} : injoignable ({error})")
                    seen[monster] = None
                    return None
                (path if text else none).write_text(text or "", encoding="utf-8")
                time.sleep(0.5)  # une page à la fois, sans presser le site
            seen[monster] = str(path) if path.exists() else None
        return seen[monster]

    out = []
    notes = [(f"donjon-{d}.toml", "donjon", d, note) for d, note in sorted(load().items())]
    notes += [(f"avis-{m}.toml", "avis", m, note) for m, note in sorted(load_wanted().items())]
    for filename, kind, ident, note in notes:
        monsters = sorted(set(game.dungeon(ident)[2]) if kind == "donjon" else {ident, *note.monsters})
        entry = {"note": str(NOTES_DIR / filename), "type": kind, "id": ident, "nom": note.name, "monstres": []}
        for monster in monsters:
            entry["monstres"].append({"id": monster, "nom": game.monster_name(monster), "huzounet": text_of(monster),
                                      "dans_la_note": monster in note.monsters})  # fmt: skip
        out.append(entry)
        print(f"{kind:6} {ident:5}  {note.name}  →  {sum(1 for m in entry['monstres'] if m['huzounet'])} fiches Huzounet")
    (WORK_DIR / "huz-lot.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dungeons", nargs="*", type=int)
    parser.add_argument("--queue", nargs="?", const=30, type=int, help="affiche la file (30 premiers par défaut)")
    parser.add_argument("--batch", type=int, help="prépare les N prochains donjons de la file")
    parser.add_argument("--avis-queue", nargs="?", const=30, type=int, help="la file des avis de recherche")
    parser.add_argument("--avis-batch", type=int, help="prépare les N prochains avis de recherche")
    parser.add_argument("--huzounet", action="store_true", help="les fiches Huzounet des monstres de chaque note écrite")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    game = Game()
    if args.huzounet:
        prepare_huzounet(game)
        print(f"Lot écrit : {WORK_DIR / 'huz-lot.json'}")
        return 0
    if args.avis_queue:
        for monster, name, level in wanted_queue(game)[: args.avis_queue]:
            print(f"{monster:5}  {name}  (niv. {level})")
        return 0
    if args.avis_batch:
        monsters = [monster for monster, _, _ in wanted_queue(game)[: args.avis_batch]]
        for entry in prepare_wanted(game, monsters, wanted_links()):
            print(f"{entry['avis']:5}  {entry['nom']}  →  {entry.get('erreur') or entry['guide']}")
        print(f"Lot écrit : {WORK_DIR / 'lot.json'}")
        return 0
    if args.queue:
        for dungeon, name, visits in queue(game)[: args.queue]:
            print(f"{dungeon:4}  {name}  ({visits} passages)" if visits else f"{dungeon:4}  {name}")
        return 0
    dungeons = args.dungeons or [dungeon for dungeon, _, _ in queue(game)[: args.batch or 0]]
    if not dungeons:
        parser.print_help()
        return 1
    for entry in prepare(game, dungeons, guide_links()):
        print(f"{entry['donjon']:4}  {entry['nom']}  →  {entry.get('erreur') or entry['guide']}")
    print(f"Lot écrit : {WORK_DIR / 'lot.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
