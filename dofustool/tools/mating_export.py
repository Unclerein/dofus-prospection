"""Écrit les aperçus d'accouplement et les naissances enregistrés, pour étudier la formule des couleurs.

Seulement des nombres : couleurs (identifiants du jeu), niveaux, sexes, chances. Ni identifiant de monture, ni nom.

Usage : python -m dofustool.tools.mating_export [--out data/apercus-elevage.txt]
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from .. import db


def describe(sheet: dict) -> str:
    sex = "mâle" if sheet["male"] else "femelle"
    parents = "×".join(str(c) for c in sheet["parents"]) or "sans parents"
    return f"couleur {sheet['color']} {sex} niv. {sheet['level']} ({sheet['xp']} xp) parents {parents}"


def lines(conn: sqlite3.Connection) -> list[str]:
    out = []
    for ts, parents, outcomes, extra in conn.execute("SELECT ts, parents, outcomes, extra FROM mating_previews ORDER BY ts"):
        a, b = json.loads(parents)
        chances = " ; ".join(f"{color}{' (cible)' if target else ''} {chance:.2f} %" for color, chance, target in json.loads(outcomes))
        out.append(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))} APERÇU | {describe(a)} || {describe(b)} || {chances} || autres {extra}")
    for ts, parents, baby in conn.execute("SELECT ts, parents, baby FROM mating_births ORDER BY ts"):
        a, b = json.loads(parents)
        out.append(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))} NAISSANCE | {describe(a)} || {describe(b)} || bébé {describe(json.loads(baby))}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=db.MARKET_PATH.parent / "apercus-elevage.txt")
    args = parser.parse_args()
    if not db.MARKET_PATH.exists():
        print(f"Base absente : {db.MARKET_PATH}")
        return 1
    conn = db.connect()
    found = lines(conn)
    args.out.write_text("\n".join(found) + "\n", encoding="utf-8")
    print(f"{len(found)} lignes écrites dans {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
