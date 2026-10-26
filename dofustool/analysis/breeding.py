"""Élevage : ce que deviennent les enclos d'ici les prochaines heures, et quand intervenir.

L'enclos avance par ticks de 10 secondes, que l'interface soit ouverte ou non. À chaque tick, chaque
objet d'élevage en marche dépense des points selon son palier (10, 20, 30 ou 40) et chaque monture de
l'enclos en gagne autant :

  - baffeur / caresseur : sérénité en moins / en plus, bornée à −5 000 et 5 000 ;
  - foudroyeur : endurance, si la sérénité est entre −5 000 et −1 ;
  - abreuvoir  : maturité, si elle est entre −2 000 et 2 000 ;
  - dragofesse : amour, si elle est entre 1 et 5 000 ;
  - mangeoire  : expérience (non suivie).

La sérénité qui compte est celle du début du tick. Un objet ne dépense rien quand aucune monture ne
peut en profiter (vérifié sur une nuit : la dragofesse a dépensé exactement l'amour gagné).

Le conseil de sérénité : les jauges qui restent à remplir dans l'enclos donnent la zone de sérénité à
viser (amour et maturité ensemble : 1 à 2 000 ; endurance et maturité : −2 000 à −1). Toutes les
montures bougent du même nombre de points ; seuls les plafonds à ±5 000 resserrent leur écart. On
continue donc l'objet en marche jusqu'au premier moment où passer à l'objet inverse laisse au moins
MARGIN_S pendant lesquelles toutes les montures sont dans la zone, ou jusqu'à ce que l'objet en
marche y amène lui-même toutes les montures.
"""
import json
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SLAPPER, PATTER, BLASTER, TROUGH, LOVER, FEEDER = range(6)
OBJECT_NAMES = ("baffeur", "caresseur", "foudroyeur", "abreuvoir", "dragofesse", "mangeoire")
THE = ("le baffeur", "le caresseur", "le foudroyeur", "l’abreuvoir", "la dragofesse", "la mangeoire")
TO_THE = ("au baffeur", "au caresseur", "au foudroyeur", "à l’abreuvoir", "à la dragofesse", "à la mangeoire")
SERENITY, LOVE, MATURITY, STAMINA = range(4)  # colonnes de l'état d'une monture
GAUGE_NAMES = {LOVE: "amour", MATURITY: "maturité", STAMINA: "endurance"}
FULL = {"amour": "plein", "maturité": "pleine", "endurance": "pleine"}  # l'amour est masculin
GAUGE_OF = {BLASTER: STAMINA, TROUGH: MATURITY, LOVER: LOVE}
ZONES = {STAMINA: (-5000, -1), MATURITY: (-2000, 2000), LOVE: (1, 5000)}
LIMIT, GAUGE_MAX = 5000, 20_000
TICK_S = 10.0
MARGIN_S = 300.0  # temps laissé pour couper l'objet : toutes les montures restent dans la zone au moins ce temps-là
HORIZON_S = 3 * 86400.0
# Paliers des objets d'élevage depuis la 3.7 (données du jeu, PaddockGaugesData) : jusqu'à tant de points, tant par tick.
TIERS = ((80_000, 10), (140_000, 20), (180_000, 30), (200_000, 40))
STATIC_PATH = Path(__file__).resolve().parents[2] / "data" / "static" / "dofus.sqlite"


def load_tiers(path: Path = STATIC_PATH) -> tuple[tuple[int, int], ...]:
    """Paliers lus dans les données du jeu (les mêmes pour les six objets), sinon ceux de la 3.7."""
    try:
        static = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            rows = static.execute("SELECT tierMaxValues FROM PaddockGaugesData ORDER BY id LIMIT 1").fetchone()
        finally:
            static.close()
        tiers = tuple(sorted((int(t["tier"]), int(t["consumptionTick"])) for t in json.loads(rows[0])))
        return tiers or TIERS
    except (sqlite3.Error, TypeError, ValueError, KeyError):
        return TIERS


def rate(points: int, tiers=TIERS) -> int:
    """Points dépensés par tick par un objet qui en a tant."""
    if points <= 0:
        return 0
    return next((tick for top, tick in tiers if points <= top), tiers[-1][1])


@dataclass
class Paddock:
    paddock_id: int
    seen_at: float
    objects: list[int]
    active: set[int]
    mounts: list[list[int]]  # [sérénité, amour, maturité, endurance] de chaque monture
    toggles: list[tuple[float, int, bool]] = field(default_factory=list)  # (quand, objet, en marche) après seen_at

    def copy(self) -> "Paddock":
        return Paddock(self.paddock_id, self.seen_at, list(self.objects), set(self.active), [list(m) for m in self.mounts], list(self.toggles))


def step(p: Paddock, tiers=TIERS) -> None:
    """Un tick de l'enclos."""
    if not p.mounts:
        return
    start = [m[SERENITY] for m in p.mounts]
    for index in sorted(p.active):
        amount = min(rate(p.objects[index], tiers), p.objects[index])
        if amount <= 0:
            continue
        used = False
        if index in (SLAPPER, PATTER):
            sign = -1 if index == SLAPPER else 1
            for m in p.mounts:
                moved = max(-LIMIT, min(LIMIT, m[SERENITY] + sign * amount))
                used |= moved != m[SERENITY]
                m[SERENITY] = moved
        elif index in GAUGE_OF:
            gauge = GAUGE_OF[index]
            low, high = ZONES[gauge]
            for m, serenity in zip(p.mounts, start):
                if low <= serenity <= high and m[gauge] < GAUGE_MAX:
                    m[gauge] = min(GAUGE_MAX, m[gauge] + amount)
                    used = True
        else:
            used = True  # la mangeoire nourrit l'expérience, que l'on ne suit pas
        if used:
            p.objects[index] -= amount


def target_zone(mounts: list[list[int]]) -> tuple[int, int] | None:
    """Zone de sérénité où montent ensemble les jauges encore incomplètes de l'enclos ; None si toutes sont pleines.

    L'amour (sérénité positive) et l'endurance (négative) ne montent jamais ensemble : quand les deux manquent, l'enclos
    suit le côté où sont déjà ses DD (sérénité médiane), et l'autre jauge viendra ensuite. Un enclos négatif vise donc
    −2 000 à −1 (endurance et maturité), un positif 1 à 2 000 (amour et maturité)."""
    missing = {g for m in mounts for g in GAUGE_NAMES if m[g] < GAUGE_MAX}
    if not missing:
        return None
    if LOVE in missing and STAMINA in missing:
        serenities = sorted(m[SERENITY] for m in mounts)
        missing.discard(LOVE if serenities[len(serenities) // 2] < 0 else STAMINA)
    low = max(ZONES[g][0] for g in missing)
    high = min(ZONES[g][1] for g in missing)
    return (low, high) if low <= high else None


def _window(low_s: int, high_s: int, direction: int, points: int, zone: tuple[int, int], tiers) -> tuple[int, int] | None:
    """Ticks (premier, dernier) où toutes les montures sont dans la zone si l'objet de ce sens tourne seul dès maintenant ;
    low_s, high_s : plus basse et plus haute sérénité. None si jamais. Le palier de l'objet est supposé constant."""
    r = rate(points, tiers)
    if r <= 0:
        return None
    lo, hi = zone
    if direction < 0:
        first, last = math.ceil(max(0, high_s - hi) / r), math.floor((low_s - lo) / r)
    else:
        first, last = math.ceil(max(0, lo - low_s) / r), math.floor((hi - high_s) / r)
    if last < first or first * r > points:
        return None
    return first, min(last, points // r)


@dataclass(frozen=True)
class Advice:
    """Le prochain geste pour la sérénité d'un enclos."""

    paddock_id: int
    # « switch » : passer à l'objet inverse ; « stop » : couper l'objet, tout le monde est dans la zone ;
    # « full » : toutes les montures ont la jauge de cet objet pleine ; « empty » : l'objet est vide.
    kind: str
    at: float  # quand le faire
    until: float  # au plus tard : toutes les montures restent dans la zone jusque-là (pour une bascule : la marge)
    zone: tuple[int, int] | None
    running: int  # objet en marche à couper (ou vide)
    then: int | None  # objet à lancer ensuite (bascule)
    mounts: int

    @property
    def title(self) -> str:
        if self.kind == "switch":
            return f"Passe {TO_THE[self.then]}"
        if self.kind == "empty":
            return f"{THE[self.running].capitalize()} est vide"
        return f"Coupe {THE[self.running]}"

    @property
    def gauge(self) -> str | None:
        """Jauge remplie par l'objet (pour « full »)."""
        return GAUGE_NAMES.get(GAUGE_OF.get(self.running, -1))


def _serenity_direction(active: set[int]) -> int:
    on = {i for i in active if i in (SLAPPER, PATTER)}
    return 0 if len(on) != 1 else (-1 if SLAPPER in on else 1)


def advise(p: Paddock, tiers=TIERS, margin_s: float = MARGIN_S, horizon_s: float = HORIZON_S) -> Advice | None:
    """Le prochain geste pour amener toutes les montures dans la zone de sérénité de leurs jauges, ou None si rien à faire
    (pas de zone, ou ni baffeur ni caresseur en marche). Les clics connus après la dernière vue (toggles) sont rejoués."""
    state = p.copy()
    zone = target_zone(state.mounts)
    if zone is None or not state.mounts:
        return None
    margin = math.ceil(margin_s / TICK_S)
    toggles = sorted(state.toggles)
    now = state.seen_at
    for tick in range(int(horizon_s / TICK_S)):
        while toggles and toggles[0][0] <= now:
            _, index, on = toggles.pop(0)
            (state.active.add if on else state.active.discard)(index)
        direction = _serenity_direction(state.active)
        if direction == 0:
            if not toggles:
                return None  # ni baffeur ni caresseur, et aucun clic à venir
            step(state, tiers)
            now += TICK_S
            continue
        running = PATTER if direction > 0 else SLAPPER
        other = SLAPPER if direction > 0 else PATTER
        serenities = [m[SERENITY] for m in state.mounts]
        low, high = min(serenities), max(serenities)
        lo, hi = zone
        if lo <= low and high <= hi:  # déjà dans la zone : l'objet en marche les en fera sortir
            ahead = _window(low, high, direction, state.objects[running], zone, tiers)
            last = ahead[1] if ahead else 0
            return Advice(p.paddock_id, "stop", now, now + last * TICK_S, zone, running, None, len(state.mounts))
        turn = _window(low, high, -direction, state.objects[other], zone, tiers)
        if turn is not None and turn[1] - turn[0] >= margin:
            return Advice(p.paddock_id, "switch", now, now + margin_s, zone, running, other, len(state.mounts))
        step(state, tiers)
        now += TICK_S
        if state.objects[running] <= 0 and running in state.active:
            return None  # l'objet se vide avant : à recharger (annoncé à part)
    return None


EVENT_KEEP_S = 6 * 3600.0  # un objet vide ou une jauge pleine reste à signaler tant qu'on n'y a pas touché, jusqu'à 6 h


def forecast(p: Paddock, tiers=TIERS, horizon_s: float = HORIZON_S) -> tuple[dict[int, float], dict[int, float]]:
    """(vide, plein) : quand chaque objet en marche sera vide, et quand toutes les montures auront la jauge de chaque objet
    en marche pleine. Ce qui l'est déjà à la dernière vue compte à cette heure-là. Clics compris."""
    state = p.copy()
    toggles = sorted(state.toggles)
    now = state.seen_at
    empty: dict[int, float] = {}
    full: dict[int, float] = {}

    def check() -> None:
        for index in state.active:
            if state.objects[index] <= 0:
                empty.setdefault(index, now)
            gauge = GAUGE_OF.get(index)
            if gauge is not None and state.mounts and all(m[gauge] >= GAUGE_MAX for m in state.mounts):
                full.setdefault(index, now)

    check()
    for _ in range(int(horizon_s / TICK_S)):
        while toggles and toggles[0][0] <= now:
            _, index, on = toggles.pop(0)
            (state.active.add if on else state.active.discard)(index)
        before = list(state.objects)
        step(state, tiers)
        now += TICK_S
        check()
        if state.objects == before and not toggles:
            break  # plus rien ne bouge : rien d'autre n'arrivera
    return empty, full


def gestures(p: Paddock, tiers=TIERS) -> list[Advice]:
    """Tous les gestes à venir d'un enclos, dans l'ordre : sérénité, jauges pleines, objets vides. Un objet qui ne dépense
    plus rien parce que toutes les jauges qu'il remplit sont pleines n'est pas annoncé vide."""
    out = []
    serenity = advise(p, tiers)
    if serenity is not None:
        out.append(serenity)
    empty, full = forecast(p, tiers)
    mounts = len(p.mounts)
    # Un objet coupé depuis (clic après l'événement) n'a plus rien à annoncer : sinon il reviendrait sitôt réglé.
    active = set(p.active)
    for _, index, on in sorted(p.toggles):
        (active.add if on else active.discard)(index)
    for index, at in full.items():
        if index in active:
            out.append(Advice(p.paddock_id, "full", at, at + EVENT_KEEP_S, None, index, None, mounts))
    for index, at in empty.items():
        if index in active and (index not in full or at < full[index]):
            out.append(Advice(p.paddock_id, "empty", at, at + EVENT_KEEP_S, None, index, None, mounts))
    return sorted(out, key=lambda a: a.at)


def load(conn: sqlite3.Connection) -> list[Paddock]:
    """Les enclos à leur dernière vue, avec leurs montures et les clics sur leurs objets depuis."""
    out = []
    for paddock_id, objects, active, seen_at in conn.execute("SELECT paddock_id, objects, active, seen_at FROM paddocks ORDER BY paddock_id"):
        mounts = [list(row) for row in conn.execute("SELECT serenity, love, maturity, stamina FROM mounts WHERE place = ?", (paddock_id,))]
        toggles = [(ts, index, bool(on)) for ts, index, on in conn.execute(
            "SELECT ts, object, active FROM paddock_toggles WHERE paddock_id = ? AND ts > ? ORDER BY ts", (paddock_id, seen_at))]  # fmt: skip
        out.append(Paddock(paddock_id, seen_at, json.loads(objects), set(json.loads(active)), mounts, toggles))
    return out


def project(p: Paddock, until: float, tiers=TIERS) -> Paddock:
    """L'enclos tel qu'il sera à cette heure-là, clics compris ; au plus HORIZON_S après sa dernière vue."""
    state = p.copy()
    toggles = sorted(state.toggles)
    now, stop = state.seen_at, min(until, state.seen_at + HORIZON_S)
    while now + TICK_S <= stop:
        while toggles and toggles[0][0] <= now:
            _, index, on = toggles.pop(0)
            (state.active.add if on else state.active.discard)(index)
        step(state, tiers)
        now += TICK_S
    for _, index, on in (t for t in toggles if t[0] <= stop):
        (state.active.add if on else state.active.discard)(index)
    state.toggles, state.seen_at = [], now
    return state


def hint(p: Paddock, zone: tuple[int, int] | None) -> str:
    """Quoi faire quand il n'y a pas de geste à annoncer."""
    if not p.mounts:
        return "Enclos vide."
    if zone is None:
        missing = {g for m in p.mounts for g in GAUGE_NAMES if m[g] < GAUGE_MAX}
        return "Toutes les jauges sont pleines." if not missing else "Les jauges à remplir n'ont pas de zone de sérénité commune : sépare les DD."
    serenities = [m[SERENITY] for m in p.mounts]
    low, high = min(serenities), max(serenities)
    lo, hi = zone
    if lo <= low and high <= hi:
        return "Toutes les DD sont dans la zone."
    if high - low > hi - lo - MARGIN_S / TICK_S * 10:
        wanted, why = (PATTER, " pour les regrouper à 5 000") if lo > 0 else (SLAPPER, " pour les regrouper à −5 000")
    else:
        wanted, why = (SLAPPER, "") if high > hi else (PATTER, "")
    if wanted in p.active:
        return f"Laisse tourner le {OBJECT_NAMES[wanted]}{why}."
    return f"Lance le {OBJECT_NAMES[wanted]}{why}."


def stable_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Toutes les montures connues, étable et enclos : par état (féconde, fertile, stérile) et par sexe."""
    row = conn.execute(
        "SELECT COUNT(*), SUM(sterile), SUM(sterile = 0 AND love >= ? AND maturity >= ? AND stamina >= ?), SUM(male) FROM mounts",
        (GAUGE_MAX, GAUGE_MAX, GAUGE_MAX),
    ).fetchone()
    count, sterile, fecund, male = (v or 0 for v in row)
    return {"count": count, "fecund": fecund, "fertile": count - fecund - sterile, "sterile": sterile, "male": male, "female": count - male}


def summary(conn: sqlite3.Connection, now: float, tiers=TIERS) -> dict:
    """Ce que montre la page Élevage : l'étable en chiffres, et chaque enclos maintenant, avec son prochain geste."""
    paddocks = []
    for p in load(conn):
        current = project(p, now, tiers)
        zone = target_zone(current.mounts)
        mounts = current.mounts

        def spread(column: int) -> list[int] | None:
            return [min(m[column] for m in mounts), max(m[column] for m in mounts)] if mounts else None

        empty, full = forecast(p, tiers)
        filler = {g: i for i, g in GAUGE_OF.items()}
        paddocks.append({
            "id": p.paddock_id, "seen_at": p.seen_at, "mounts": len(mounts), "zone": list(zone) if zone else None,
            "serenity": spread(SERENITY),
            "gauges": {GAUGE_NAMES[g]: {"range": spread(g), "full_at": full.get(filler[g]) if filler[g] in current.active else None}
                       for g in (LOVE, MATURITY, STAMINA)},
            "objects": [{"index": i, "name": OBJECT_NAMES[i], "points": current.objects[i], "active": i in current.active,
                         "rate": rate(current.objects[i], tiers), "empty_at": empty.get(i) if i in current.active and i not in full else None}
                        for i in range(len(OBJECT_NAMES))],
            "gestures": [{
                "kind": g.kind, "title": g.title, "at": g.at, "until": g.until, "gauge": g.gauge,
                "running": OBJECT_NAMES[g.running], "then": OBJECT_NAMES[g.then] if g.then is not None else None,
            } for g in gestures(p, tiers) if g.until >= now],
            "hint": hint(current, zone),
        })  # fmt: skip
    return {"now": now, "stable": stable_counts(conn), "paddocks": paddocks}
