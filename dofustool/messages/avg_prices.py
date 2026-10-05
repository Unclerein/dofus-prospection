"""Prix moyens de tout le catalogue, envoyés par le serveur peu après la sélection du personnage."""
from ..protocol.wire import LEN, VARINT, WireError, iter_fields
from . import Mapping

# En dessous, ce n'est pas la liste du catalogue : mieux vaut ne rien enregistrer.
MIN_ENTRIES = 1000


def parse(body: bytes, mapping: Mapping) -> dict[int, int] | None:
    """Renvoie {id d'item: prix moyen}, ou None si le corps n'a pas la forme attendue.

    Les entrées sans prix (item jamais vendu) sont omises.
    """
    f_entries, f_item, f_price = (mapping.fields[n] for n in ("entries", "item_id", "price"))
    prices: dict[int, int] = {}
    entries = 0
    try:
        for number, wire_type, entry in iter_fields(body):
            if number != f_entries or wire_type != LEN:
                return None
            entries += 1
            item_id = price = None
            for sub_number, sub_type, value in iter_fields(entry):
                if sub_type != VARINT:
                    return None
                if sub_number == f_item:
                    item_id = value
                elif sub_number == f_price:
                    price = value
                else:
                    return None
            if item_id is None:
                return None
            if price:
                prices[item_id] = price
    except WireError:
        return None
    return prices if entries >= MIN_ENTRIES else None
