"""Prépare la rédaction des notes de donjon : la file des donjons à faire, et pour chacun le texte du guide et sa fiche calculée.

Usage :
  python -m dofustool.bestiary.prepare --queue [N]   la file : donjons joués d'abord (d'après l'archive locale), sans note
  python -m dofustool.bestiary.prepare --batch N     prépare les N prochains de la file
  python -m dofustool.bestiary.prepare <id> [...]    prépare ces donjons-là

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
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from .. import db
from .monster import Game
from .notes import load

GUIDE_SITE = "https://www.dofuspourlesnoobs.com"
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dungeons", nargs="*", type=int)
    parser.add_argument("--queue", nargs="?", const=30, type=int, help="affiche la file (30 premiers par défaut)")
    parser.add_argument("--batch", type=int, help="prépare les N prochains donjons de la file")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    game = Game()
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
