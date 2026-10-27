"""Affiche les fiches calculées des monstres d'un donjon, pour les relire.

Usage : python -m dofustool.bestiary <identifiant du donjon> [...]
"""
import argparse
import sys

from .monster import Game


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dungeons", nargs="+", type=int)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    game = Game()
    for dungeon in args.dungeons:
        name, bosses, sheets, common = game.dungeon_sheets(dungeon)
        print(f"== {name} ({dungeon})")
        if common:
            print(f"  Tous ses monstres : {' · '.join(common)}")
        for sheet in sheets:
            print(f"  {'BOSS ' if sheet.id in bosses else ''}{sheet.name} ({sheet.id}) · niv. {sheet.level} · {sheet.life} PV · {sheet.ap} PA {sheet.mp} PM · faible {sheet.weakest}")
            for label, value in (("portée", sheet.reach), ("déplacement", " · ".join(sheet.moves)), ("spécial", " · ".join(sheet.mechanics))):
                if value:
                    print(f"      {label} : {value}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
