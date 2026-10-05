"""Extrait de l'archive le corps du dernier message d'un nom logique vers tests/fixtures/<nom>.bin.

Seuls les messages qu'un parseur valide sont extraits : la fixture est commitée, elle ne
doit donc contenir que des données de marché (identifiants, prix, quantités, dates de vente),
sans texte libre ni donnée de session.

Usage : python -m dofustool.tools.extract_fixture avg_prices|market_history
"""
import argparse
import sys
from pathlib import Path

from ..archive import Archive
from ..messages import avg_prices, hdv_listings, load_keymap, market_history

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
PARSERS = {"avg_prices": avg_prices.parse, "market_history": market_history.parse, "hdv_listings": hdv_listings.parse}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", choices=sorted(PARSERS))
    args = parser.parse_args()

    mapping = load_keymap()[args.name]
    archive = Archive()
    body = archive.latest_body(mapping.key)
    archive.close()
    if body is None:
        print(f"Aucun message de clé {mapping.key} dans l'archive.")
        return 1
    parsed = PARSERS[args.name](body, mapping)
    if args.name == "hdv_listings" and parsed is not None and any(l.effects for l in parsed.listings):
        # Les annonces d'équipement peuvent porter le nom d'un joueur : jamais dans une fixture commitée.
        print("La dernière liste HDV concerne un équipement : ouvre une ressource à l'HDV puis relance.")
        return 1
    if parsed is None:
        print(f"Le message {mapping.key} n'a pas la forme attendue pour {args.name} : rien n'est écrit.")
        return 1
    target = FIXTURES / f"{args.name}.bin"
    target.write_bytes(body)
    print(f"{target.relative_to(FIXTURES.parents[1])} : {len(body)} octets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
