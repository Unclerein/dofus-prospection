"""Retrouve les messages connus après une mise à jour du jeu, par leur structure.

Les clés et les numéros de champ changent à chaque build. Chaque message garde en revanche sa
forme, et son contenu se contrôle : des identifiants d'objets connus, des niveaux de métier
cohérents avec leur expérience, une moyenne des ventes qui redonne le prix moyen du jeu…
Un candidat n'est retenu que si le vrai parseur l'accepte ; s'il y en a plusieurs pour un même
message, rien n'est retenu.

Ne lit que des nombres et la forme des champs. Le seul texte examiné est le nom d'un personnage,
dont on vérifie seulement qu'il est lisible.
"""
import json
import shutil
import sqlite3
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .analysis.jobxp import level_to_xp
from .messages import Mapping, avg_prices, characters, hdv_listings, market_history, sales, storage, trades
from .protocol.tcp import C2S, S2C
from .protocol.wire import LEN, VARINT, WireError, iter_fields, read_varint

NAMES = (
    "avg_prices", "inventory", "bank", "character_list", "character_select",
    "job_levels", "market_history", "hdv_listings", "my_sales", "info_text", "my_sale_update",
)  # fmt: skip
TEXT_MAX_BYTES = 200  # un message d'information est court
TEXT_MIN_SEEN = 3  # nombre de messages à paramètres chiffrés qu'il faut avoir vus pour reconnaître la clé
INVENTORY_WINDOW_S = 90.0  # l'inventaire arrive tout seul juste après le choix du personnage ; la banque, non
MIN_KNOWN = 0.95


@dataclass(slots=True)
class Context:
    """Ce que la base sait déjà, pour contrôler le contenu d'un candidat."""

    items: set[int]
    jobs: set[int]
    effects: set[int]
    avg: dict[int, int]

    @classmethod
    def load(cls, market: sqlite3.Connection) -> "Context":
        latest = market.execute("SELECT id FROM snapshots ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
        avg = dict(market.execute("SELECT item_id, price FROM avg_prices WHERE snapshot_id = ?", latest)) if latest else {}
        return cls(
            {row[0] for row in market.execute("SELECT id FROM items")},
            {row[0] for row in market.execute("SELECT id FROM jobs")},
            {row[0] for row in market.execute("SELECT id FROM effects")},
            avg,
        )


@dataclass(frozen=True, slots=True)
class Found:
    name: str
    key: str
    fields: dict[str, int]
    partial: bool = False  # annonces HDV vues sans équipement : champs des effets encore inconnus


# --- lecture générique -------------------------------------------------------


def _fields(body: bytes) -> list[tuple[int, int, object]] | None:
    try:
        return list(iter_fields(body))
    except WireError:
        return None


def _varints(fields) -> dict[int, int]:
    return {n: v for n, t, v in fields if t == VARINT}


def _packed(value: bytes) -> list[int] | None:
    out, pos = [], 0
    try:
        while pos < len(value):
            v, pos = read_varint(value, pos)
            out.append(v)
    except WireError:
        return None
    return out


def _repeated(fields) -> dict[int, list[bytes]]:
    groups: dict[int, list[bytes]] = {}
    for n, t, v in fields:
        if t == LEN:
            groups.setdefault(n, []).append(v)
    return groups


def _everywhere(rows: list[dict[int, int]], share: float = MIN_KNOWN) -> list[int]:
    """Numéros des champs présents dans presque toutes les lignes."""
    counts = Counter(n for row in rows for n in row)
    return [n for n, c in counts.items() if c >= share * len(rows)]


# --- un détecteur par message ------------------------------------------------


def match_avg_prices(body: bytes, ctx: Context) -> dict[str, int] | None:
    top = _fields(body)
    if not top or len(top) < avg_prices.MIN_ENTRIES or any(t != LEN for _, t, _ in top) or len({n for n, _, _ in top}) != 1:
        return None
    rows = []
    for _, _, value in top[:3000]:
        sub = _fields(value)
        if sub is None or any(t != VARINT for _, t, _ in sub):
            return None
        rows.append(_varints(sub))
    numbers = {n for row in rows for n in row}
    if len(numbers) != 2:
        return None
    known = {n: sum(row.get(n) in ctx.items for row in rows) / len(rows) for n in numbers}
    item_field = max(known, key=known.get)
    if known[item_field] < 0.9:
        return None
    fields = {"entries": top[0][0], "item_id": item_field, "price": next(n for n in numbers if n != item_field)}
    return fields if avg_prices.parse(body, Mapping("", fields)) else None


def match_storage(body: bytes, ctx: Context) -> dict[str, int] | None:
    """Inventaire ou banque : des entrées (position, objet), l'objet portant identifiant et quantité."""
    top = _fields(body)
    if not top:
        return None
    groups = _repeated(top)
    kamas = [n for n, t, _ in top if t == VARINT]
    if not groups or len(kamas) != 1:
        return None
    entries_field = max(groups, key=lambda n: len(groups[n]))
    entries = [_fields(e) for e in groups[entries_field][:400]]
    if len(entries) < 3 or any(e is None for e in entries):
        return None
    positions = [_varints(e) for e in entries]
    position_field = next(
        (n for n in _everywhere(positions) if Counter(p.get(n) for p in positions).most_common(1)[0][0] == storage.BAG_POSITION),
        None,
    )
    if position_field is None:
        return None
    for object_field in _everywhere([dict.fromkeys(_repeated(e)) for e in entries]):
        objects = []
        for e in entries:
            sub = _fields(_repeated(e).get(object_field, [b""])[0])
            objects.append(_varints(sub) if sub else {})
        common = _everywhere(objects)
        if len(common) < 2:
            continue
        values = {n: [o[n] for o in objects if n in o] for n in common}
        known = {n: sum(v in ctx.items for v in vs) / len(vs) for n, vs in values.items()}
        item_field = max(common, key=lambda n: (known[n] >= MIN_KNOWN, statistics.median(values[n])))
        if known[item_field] < MIN_KNOWN:
            continue
        # L'identifiant unique d'un exemplaire : toujours différent, et très grand.
        uid = {n for n in common if len(set(values[n])) == len(values[n]) and statistics.median(values[n]) >= 100_000}
        quantity = [n for n in common if n != item_field and n not in uid]
        if len(quantity) != 1:
            continue
        fields = {
            "kamas": kamas[0], "entries": entries_field, "position": position_field,
            "object": object_field, "quantity": quantity[0], "item_id": item_field,
        }  # fmt: skip
        parsed = storage.parse(body, Mapping("", fields))
        if parsed is not None and len(parsed.stacks) == len(groups[entries_field]):
            return fields
    return None


def match_job_levels(body: bytes, ctx: Context) -> dict[str, int] | None:
    top = _fields(body)
    if not top or len({n for n, _, _ in top}) != 1 or len(top) < 10 or any(t != LEN for _, t, _ in top):
        return None
    rows = []
    for _, _, value in top:
        sub = _fields(value)
        if sub is None or any(t != VARINT for _, t, _ in sub):
            return None
        rows.append(_varints(sub))
    numbers = {n for row in rows for n in row}
    job = [n for n in numbers if all(n in r and r[n] in ctx.jobs for r in rows) and len({r[n] for r in rows}) == len(rows)]

    def fits(level_field: int, xp_field: int) -> bool:
        for row in rows:
            level, xp = row.get(level_field, 0), row.get(xp_field, 0)
            if not 1 <= level <= characters.MAX_JOB_LEVEL or xp < level_to_xp(level):
                return False
            if level < characters.MAX_JOB_LEVEL and xp >= level_to_xp(level + 1):
                return False
        return True

    found = []
    for job_field in job:
        others = numbers - {job_field}
        for level_field in others:
            for xp_field in others - {level_field}:
                if fits(level_field, xp_field):
                    # Le seuil du niveau passe le même contrôle que l'expérience : l'écarter.
                    is_floor = all(r.get(xp_field, 0) == level_to_xp(r.get(level_field, 0)) for r in rows)
                    found.append((is_floor, {"entries": top[0][0], "job_id": job_field, "xp": xp_field, "level": level_field}))
    real = [f for is_floor, f in found if not is_floor] or [f for _, f in found]
    return real[0] if len(real) == 1 else None


def match_character_list(body: bytes, chosen: int) -> dict[str, int] | None:
    """Liste des personnages : une entrée par personnage, dont celui que le client vient de choisir."""
    top = _fields(body)
    if not top:
        return None
    for entries_field, raw in _repeated(top).items():
        entries = [_fields(e) for e in raw]
        if any(e is None for e in entries):
            continue
        ids = [n for n in _everywhere([_varints(e) for e in entries], 1.0) if any(_varints(e)[n] == chosen for e in entries)]
        if len(ids) != 1:
            continue
        for info_field in _everywhere([dict.fromkeys(_repeated(e)) for e in entries], 1.0):
            infos = [_fields(_repeated(e)[info_field][0]) for e in entries]
            if any(i is None for i in infos):
                continue
            levels = _everywhere([_varints(i) for i in infos], 1.0)
            names = []
            for n in _everywhere([dict.fromkeys(_repeated(i)) for i in infos], 1.0):
                try:
                    texts = [_repeated(i)[n][0].decode("utf-8") for i in infos]
                except UnicodeDecodeError:
                    continue
                if all(2 <= len(t) <= 40 and t.isprintable() for t in texts):
                    names.append(n)
            if len(levels) != 1 or len(names) != 1:
                continue
            fields = {"entries": entries_field, "info": info_field, "id": ids[0], "level": levels[0], "name": names[0]}
            parsed = characters.parse_list(body, Mapping("", fields))
            if parsed is not None and any(c.id == chosen for c in parsed):
                return fields
    return None


def match_market_history(body: bytes, ctx: Context) -> dict[str, int] | None:
    top = _fields(body)
    if not top:
        return None
    groups = _repeated(top)
    if len(groups) != 2 or any(t != LEN for _, t, _ in top):
        return None  # une série vide ne permet pas de retrouver son numéro de champ : attendre un autre objet
    sample = _fields(next(iter(groups.values()))[0])
    if sample is None:
        return None
    dates = [n for n, t, _ in sample if t == LEN]
    numbers = [n for n, t, _ in sample if t == VARINT]
    if len(dates) != 1 or len(numbers) != 3:
        return None
    item = [n for n in numbers if _varints(sample)[n] in ctx.items]
    a, b = groups
    found = []
    for item_field in item:
        price_field, quantity_field = (n for n in numbers if n != item_field)
        for price, quantity in ((price_field, quantity_field), (quantity_field, price_field)):
            fields = {"hourly": a, "daily": b, "quantity": quantity, "date": dates[0], "price": price, "item_id": item_field}
            parsed = market_history.parse(body, Mapping("", fields))
            if parsed is None or len(parsed.hourly) < 2 or len(parsed.daily) < 2:
                continue

            def gap(points) -> float:
                stamps = sorted(p.sold_at for p in points)
                return statistics.median(y - x for x, y in zip(stamps, stamps[1:]))

            if gap(parsed.hourly) > gap(parsed.daily):  # la série journalière est celle aux points les plus espacés
                fields["hourly"], fields["daily"] = b, a
                parsed = market_history.parse(body, Mapping("", fields))
            # Le prix moyen du jeu est la moyenne des ventes journalières pondérée par les quantités.
            sold = sum(p.quantity for p in parsed.daily)
            reference = ctx.avg.get(parsed.item_id)
            if not sold or not reference:
                continue
            mean = sum(p.price * p.quantity for p in parsed.daily) / sold
            if abs(mean - reference) <= max(2.0, 0.03 * reference):
                found.append(fields)
    return found[0] if len(found) == 1 else None


def match_hdv_listings(body: bytes, ctx: Context) -> tuple[dict[str, int], bool] | None:
    """Renvoie (champs, partiel). Partiel : aucune annonce d'équipement, donc champs des effets inconnus."""
    top = _fields(body)
    if not top:
        return None
    top_values = _varints(top)
    for entries_field, raw in _repeated(top).items():
        entries = [_fields(e) for e in raw]
        if any(not e for e in entries):
            continue
        for item_field, item_id in top_values.items():
            if item_id not in ctx.items:
                continue
            first = _varints(entries[0])
            entry_item = [n for n, v in first.items() if v == item_id]
            # Prix : un champ présent une seule fois par annonce, fait de quatre entiers.
            prices = [
                n for n, values in _repeated(entries[0]).items()
                if all(len(_repeated(e).get(n, [])) == 1 and len(_packed(_repeated(e)[n][0]) or []) == 4 for e in entries)
            ]  # fmt: skip
            uid = [n for n, v in first.items() if v != item_id and v not in top_values.values()]
            if len(entry_item) != 1 or len(prices) != 1 or len(uid) != 1:
                continue
            fields = {
                "item_id": item_field, "entries": entries_field, "effects": 0, "entry_item": entry_item[0],
                "uid": uid[0], "prices": prices[0], "effect_id": 0, "effect_value": 0,
            }  # fmt: skip
            effect_fields = {n for e in entries for n in _repeated(e) if n != prices[0]}
            if len(effect_fields) > 1:
                continue
            for effects_field in effect_fields:
                subs = [_fields(v) for e in entries for v in _repeated(e).get(effects_field, [])]
                if any(s is None for s in subs):
                    return None
                rows = [_varints(s) for s in subs]
                ids = [n for n in _everywhere(rows, 1.0) if all(r[n] in ctx.effects for r in rows)]
                if len(ids) != 1:
                    return None
                values = Counter(n for r in rows for n in r if n != ids[0])
                if not values:
                    return None
                fields.update(effects=effects_field, effect_id=ids[0], effect_value=values.most_common(1)[0][0])
            if hdv_listings.parse(body, Mapping("", fields)) is not None:
                return fields, not effect_fields
    return None


def match_my_sales(body: bytes, ctx: Context) -> dict[str, int] | None:
    top = _fields(body)
    if not top:
        return None
    for entries_field, raw in _repeated(top).items():
        entries = [_fields(e) for e in raw]
        if len(entries) < 3 or any(e is None for e in entries):
            continue
        refs = _everywhere([dict.fromkeys(_repeated(e)) for e in entries], 1.0)
        outer = _everywhere([_varints(e) for e in entries])
        if len(refs) != 1 or len(outer) != 2:
            continue
        inner = [_varints(_fields(_repeated(e)[refs[0]][0]) or []) for e in entries]
        numbers = _everywhere(inner, 1.0)
        if len(numbers) != 3:
            continue
        lot = [n for n in numbers if all(r[n] in sales.LOT_SIZES for r in inner)]
        item = [n for n in numbers if n not in lot and sum(r[n] in ctx.items for r in inner) >= MIN_KNOWN * len(inner)]
        if len(lot) != 1 or len(item) != 1:
            continue
        uid = next(n for n in numbers if n not in (lot[0], item[0]))
        found = []
        for price, remaining in (outer, outer[::-1]):
            fields = {
                "entries": entries_field, "ref": refs[0], "uid": uid, "item_id": item[0], "lot": lot[0],
                "price": price, "remaining": remaining,
            }  # fmt: skip
            parsed = sales.parse(body, Mapping("", fields))
            if parsed is None or len(parsed.sales) != len(entries):
                continue
            # Le prix demandé pour un lot reste dans l'ordre de grandeur du prix moyen de l'objet.
            priced = [s for s in parsed.sales if ctx.avg.get(s.item_id)]
            near = sum(0.1 <= s.price / s.lot / ctx.avg[s.item_id] <= 10 for s in priced)
            if priced and near >= 0.6 * len(priced):
                found.append(fields)
        if len(found) == 1:
            return found[0]
    return None


def match_sale_update(body: bytes, ctx: Context) -> dict[str, int] | None:
    """Lot créé ou modifié : la même forme qu'une entrée de la liste des lots en vente, seule dans son message."""
    top = _fields(body)
    if not top or len(top) != 3:
        return None
    refs = [(n, v) for n, t, v in top if t == LEN]
    outer = [n for n, t, _ in top if t == VARINT]
    if len(refs) != 1 or len(outer) != 2:
        return None
    inner = _fields(refs[0][1])
    if inner is None:
        return None
    values = _varints(inner)  # un lot d'équipement porte aussi ses effets : seuls les entiers comptent
    lot = [n for n, v in values.items() if v in sales.LOT_SIZES]
    item = [n for n, v in values.items() if n not in lot and v in ctx.items]
    if len(lot) != 1 or len(item) != 1 or len(values) != 3:
        return None
    uid = next(n for n in values if n not in (lot[0], item[0]))
    found = []
    for price, remaining in (outer, outer[::-1]):
        fields = {"price": price, "ref": refs[0][0], "remaining": remaining, "item_id": item[0], "uid": uid, "lot": lot[0]}
        parsed = trades.parse_lot_update(body, Mapping("", fields))
        reference = ctx.avg.get(parsed.item_id) if parsed else None
        # Comme pour la liste des lots : le prix demandé reste dans l'ordre de grandeur du prix moyen.
        if reference and 0.1 <= parsed.price / parsed.lot / reference <= 10:
            found.append(fields)
    return found[0] if len(found) == 1 else None


def text_signature(body: bytes) -> tuple[int, int, bool] | None:
    """Forme d'un message d'information : (champ du numéro de texte, champ des paramètres, paramètres tous chiffrés).

    None si le message n'a pas cette forme : des entiers, et au plus un champ répété de courtes chaînes.
    """
    top = _fields(body)
    if not top:
        return None
    texts = {n for n, t, _ in top if t == LEN}
    numbers = [n for n, t, _ in top if t == VARINT]
    if len(texts) > 1 or not numbers or any(t not in (LEN, VARINT) for _, t, _ in top):
        return None
    params = [v for _, t, v in top if t == LEN]
    digits = len(params) >= 2 and all(v.isdigit() and len(v) <= trades.MAX_DIGITS for v in params)
    return numbers[0], next(iter(texts), 0), digits  # 0 : texte sans paramètre


# --- balayage d'une connexion ------------------------------------------------


@dataclass(slots=True)
class Scan:
    """Plus gros message de chaque clé d'une connexion, alimenté au fil de l'eau."""

    started_at: float
    last_id: int = 0
    best: dict[tuple[str, str], bytes] = field(default_factory=dict)
    first_ts: dict[tuple[str, str], float] = field(default_factory=dict)
    order: dict[tuple[str, str], int] = field(default_factory=dict)
    dirty: set[tuple[str, str]] = field(default_factory=set)
    matches: dict[tuple[str, str], dict[str, Found]] = field(default_factory=dict)
    # Messages d'information : par clé, les formes vues parmi les messages courts (None : autre forme).
    texts: dict[tuple[str, str], Counter] = field(default_factory=dict)

    def feed(self, archive: sqlite3.Connection, connection_id: int) -> None:
        rows = archive.execute(
            "SELECT id, ts, direction, key, body FROM messages WHERE connection_id = ? AND id > ? ORDER BY id",
            (connection_id, self.last_id),
        )
        for message_id, ts, direction, key, body in rows:
            self.last_id = message_id
            slot = (direction, key)
            if slot not in self.best:
                self.first_ts[slot] = ts
                self.order[slot] = message_id
            if slot not in self.best or len(body) > len(self.best[slot]):
                self.best[slot] = body
                self.dirty.add(slot)
            if direction == S2C and 0 < len(body) <= TEXT_MAX_BYTES:
                self.texts.setdefault(slot, Counter())[text_signature(body)] += 1


def identify(scan: Scan, ctx: Context, wanted=NAMES) -> tuple[dict[str, Found], dict[str, str]]:
    """Cherche les messages demandés dans ce que la connexion a déjà échangé.

    Renvoie (messages retrouvés, problèmes). Un message jamais échangé n'est ni l'un ni l'autre.
    """
    for slot in sorted(scan.dirty):
        direction, key = slot
        body = scan.best[slot]
        found: dict[str, Found] = {}
        if direction == S2C:
            simple = {
                "avg_prices": match_avg_prices,
                "job_levels": match_job_levels,
                "market_history": match_market_history,
                "my_sales": match_my_sales,
                "my_sale_update": match_sale_update,
            }
            for name, matcher in simple.items():
                fields = matcher(body, ctx)
                if fields:
                    found[name] = Found(name, key, fields)
            fields = match_storage(body, ctx)
            if fields:
                found["storage"] = Found("storage", key, fields)
            listing = match_hdv_listings(body, ctx)
            if listing:
                found["hdv_listings"] = Found("hdv_listings", key, listing[0], listing[1])
        scan.matches[slot] = found
    scan.dirty.clear()

    candidates: dict[str, list[Found]] = {}
    for slot, found in scan.matches.items():
        for name, item in found.items():
            if name == "storage":
                # Même forme pour les deux : l'inventaire est celui que le serveur envoie de lui-même à la connexion.
                early = scan.first_ts[slot] - scan.started_at <= INVENTORY_WINDOW_S
                name = "inventory" if early else "bank"
                item = Found(name, item.key, item.fields)
            candidates.setdefault(name, []).append(item)

    # Message d'information : une clé dont tous les messages courts ont la même forme (un numéro de texte et
    # des paramètres), et dont plusieurs ont des paramètres entièrement chiffrés.
    if "info_text" in wanted:
        for slot, seen in scan.texts.items():
            shapes = {(sig[0], sig[1]) for sig in seen if sig is not None and sig[1]}
            strong = sum(n for sig, n in seen.items() if sig is not None and sig[2])
            if None in seen or len(shapes) != 1 or strong < TEXT_MIN_SEEN or len(scan.best[slot]) > TEXT_MAX_BYTES:
                continue
            id_field, params_field = next(iter(shapes))
            if any(sig[0] != id_field for sig in seen):
                continue
            candidates.setdefault("info_text", []).append(Found("info_text", slot[1], {"id": id_field, "params": params_field}))

    # Choix du personnage : une requête du client faite d'un seul entier, que la liste reçue juste avant contient.
    if "character_select" in wanted or "character_list" in wanted:
        pairs = []
        for (direction, key), body in scan.best.items():
            sent = _fields(body) if direction == C2S else None
            if not sent or len(sent) != 1 or sent[0][1] != VARINT or sent[0][2] < 1_000_000:
                continue
            for (other, list_key), list_body in scan.best.items():
                if other != S2C or scan.order[(other, list_key)] > scan.order[(direction, key)] or len(list_body) > 20_000:
                    continue
                fields = match_character_list(list_body, sent[0][2])
                if fields:
                    rank = scan.order[(direction, key)]
                    pairs.append((rank, Found("character_select", key, {"id": sent[0][0]}), Found("character_list", list_key, fields)))
        # D'autres requêtes ont la même forme plus tard dans la partie : le choix du personnage est la première.
        earliest = min((rank for rank, _, _ in pairs), default=None)
        pairs = [(select, listing) for rank, select, listing in pairs if rank == earliest]
        if len(pairs) == 1:
            candidates["character_select"], candidates["character_list"] = [pairs[0][0]], [pairs[0][1]]
        elif pairs:
            candidates["character_select"] = [p[0] for p in pairs]
            candidates["character_list"] = [p[1] for p in pairs]

    found, problems = {}, {}
    for name in wanted:
        options = {(c.key, tuple(sorted(c.fields.items()))): c for c in candidates.get(name, [])}
        if len(options) == 1:
            found[name] = next(iter(options.values()))
        elif options:
            problems[name] = "plusieurs candidats : " + ", ".join(sorted(c.key for c in options.values()))
    return found, problems


# --- keymap.json -------------------------------------------------------------


def read_keymap(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def pending(raw: dict) -> set[str]:
    """Messages à retrouver : absents, marqués périmés, ou connus seulement en partie."""
    return {name for name in NAMES if name not in raw or raw[name].get("stale") or raw[name].get("partial")}


def changes(raw: dict, found: dict[str, Found]) -> dict[str, Found]:
    """Ce qui diffère réellement de keymap.json (un résultat partiel ne remplace jamais un résultat complet)."""
    out = {}
    for name, item in found.items():
        current = raw.get(name, {})
        same = current.get("key") == item.key and current.get("fields") == item.fields
        if same and not current.get("stale") and bool(current.get("partial")) == item.partial:
            continue
        if item.partial and current.get("key") == item.key and not current.get("stale") and not current.get("partial"):
            continue
        out[name] = item
    return out


def write_keymap(path: Path, found: dict[str, Found], new_build: bool = False, backup_dir: Path | None = None) -> None:
    """Écrit les messages retrouvés. new_build : toutes les autres entrées deviennent périmées (clés d'un ancien build)."""
    raw = read_keymap(path)
    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, backup_dir / f"keymap-{time.strftime('%Y%m%d-%H%M%S')}.json")
    if new_build:
        for name, entry in raw.items():
            if name not in found:
                entry["stale"] = True
    for name, item in found.items():
        raw[name] = {"key": item.key, "fields": item.fields}
        if item.partial:
            raw[name]["partial"] = True
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    temp.replace(path)
