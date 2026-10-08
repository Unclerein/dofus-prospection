"""Almanax d'un jour : le bonus et l'offrande demandée, lus sur l'API publique de DofusDB.

Un jour ne change jamais une fois publié : la réponse est gardée dans data/market.sqlite, et
DofusDB n'est interrogé qu'une fois par jour consulté. Une panne de DofusDB ne coûte rien d'autre
que cette case de l'interface.
"""
import json
import re
import sqlite3
import time
import urllib.request
from datetime import date

API = "https://api.dofusdb.fr/almanax?date={month:02d}/{day:02d}/{year}"  # mois/jour/année
_HEADERS = {"User-Agent": "dofustool"}
_TAGS = re.compile(r"<[^>]+>")
# Renvoi du jeu vers une fiche : {{monsterRace,229::Firefoux}} se lit « Firefoux ».
_LINKS = re.compile(r"\{\{[^{}]*?::([^{}]*)\}\}")
_BRACES = re.compile(r"\{\{[^{}]*\}\}")


def plain(text: str) -> str:
    """Texte du jeu sans balises ni renvois : seul le libellé d'un renvoi est gardé."""
    return " ".join(_BRACES.sub("", _LINKS.sub(r"\1", _TAGS.sub("", text))).split())


MAX_ITEMS = 10


def parse(payload: dict) -> dict:
    """Garde le nom du bonus, sa description sans balises, et l'offrande : [(objet, quantité, nom)]."""
    items = []
    names = {int(item["id"]): str((item.get("name") or {}).get("fr") or "") for item in payload.get("items") or [] if "id" in item}
    for item_id, quantity in list(zip(payload.get("itemIds") or [], payload.get("quantities") or []))[:MAX_ITEMS]:
        items.append([int(item_id), int(quantity), names.get(int(item_id), "")[:80]])
    return {
        "name": str((payload.get("name") or {}).get("fr") or "")[:120],
        "desc": plain(str((payload.get("desc") or {}).get("fr") or ""))[:400],
        "items": items,
    }


def fetch(day: date) -> dict:
    request = urllib.request.Request(API.format(month=day.month, day=day.day, year=day.year), headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=8) as response:
        return parse(json.load(response))


def cached(conn: sqlite3.Connection, day: date) -> dict | None:
    row = conn.execute("SELECT payload FROM almanax WHERE day = ?", (day.isoformat(),)).fetchone()
    return json.loads(row[0]) if row else None


def store(conn: sqlite3.Connection, day: date, almanax: dict) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO almanax VALUES (?, ?, ?)", (day.isoformat(), json.dumps(almanax), time.time()))
