"""Notifications par-dessus le jeu : une petite fenêtre à part, qui affiche et ne fait rien d'autre.

Elle ne reçoit ni clic ni touche et ne touche pas au client : c'est une fenêtre de ce programme, posée
au-dessus des autres. Ce qu'elle montre vient de la base locale, que la capture remplit.

Ce module tient la logique, sans fenêtre : quelles ventes viennent d'arriver, et comment elles se
regroupent en bandeaux ; quand rappeler la mission de forgemagie, et quels succès annoncer.
"""
import json
import sqlite3
from dataclasses import dataclass

from ..analysis import breeding
from ..analysis.collecte import fits, pick

SHOWN_S = 3.0  # durée d'un bandeau après la dernière vente qui l'a touché
MAX_BANNERS = 4
FRESH_S = 60.0  # une vente plus vieille que ça vient d'un rattrapage de l'archive : on ne l'annonce pas


@dataclass(frozen=True)
class Sale:
    source_id: int
    ts: float
    item_id: int
    quantity: int  # taille du lot
    price: int  # prix du lot entier


@dataclass
class Banner:
    item_id: int
    quantity: int
    kamas: int
    until: float
    version: int = 0  # nombre de ventes venues compléter le bandeau
    born: float = 0.0  # apparition à l'écran, pour le fondu d'entrée
    bumped: float = 0.0  # dernière vente qui l'a complété, pour allumer la quantité
    # Bandeau qui n'est pas une vente (mission de forgemagie, succès) : son texte tel quel.
    label: str = "VENDU"
    title: str = ""
    text: str = ""
    badge: str | None = None  # None : la quantité vendue ; "" : pas de pastille
    star: bool = False  # une étoile à la place de l'image
    shown_s: float = 0.0  # durée propre, sinon celle du tableau
    kind: str = ""  # « élevage » : couleur et cadran à part
    deadline: float = 0.0  # heure limite : la pastille décompte le temps qu'il reste
    gesture: tuple | None = None  # élevage : le geste demandé ; le bandeau s'efface quand il est fait
    glyph: str = ""  # élevage : la jauge en cause (« amour »…), pour sa couleur et son symbole ; sinon le cadran de sérénité
    dismissed: bool = False  # effacé avant son terme : il ne revient pas au retour du jeu


class Feed:
    """Ventes enregistrées depuis le dernier passage."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        row = conn.execute("SELECT MAX(source_id) FROM trades").fetchone()
        self.last = row[0] or 0  # rien de ce qui précède le lancement n'est annoncé

    def poll(self, now: float) -> list[Sale]:
        rows = self.conn.execute(
            "SELECT source_id, ts, item_id, quantity, price FROM trades WHERE kind = 'sale' AND source_id > ? ORDER BY source_id",
            (self.last,),
        ).fetchall()
        if rows:
            self.last = rows[-1][0]
        return [Sale(*row) for row in rows if now - row[1] <= FRESH_S]


class Board:
    """Bandeaux à l'écran. Une vente d'un item déjà affiché complète son bandeau au lieu d'en ouvrir un autre."""

    def __init__(self, shown_s: float = SHOWN_S, limit: int = MAX_BANNERS) -> None:
        self.shown_s, self.limit = shown_s, limit
        self.banners: list[Banner] = []

    def add(self, sale: Sale, now: float) -> Banner:
        self.expire(now)
        for banner in self.banners:
            if banner.item_id == sale.item_id:
                banner.quantity += sale.quantity
                banner.kamas += sale.price
                banner.until = now + self.shown_s
                banner.version += 1
                banner.bumped = now
                return banner
        banner = Banner(sale.item_id, sale.quantity, sale.price, now + self.shown_s, born=now)
        self.banners.append(banner)
        del self.banners[: -self.limit]  # trop de bandeaux : les plus anciens cèdent la place
        return banner

    def note(self, label: str, title: str, text: str, now: float, item_id: int = 0, badge: str = "", star: bool = False, shown_s: float = 6.0,
             kind: str = "", deadline: float = 0.0, gesture: tuple | None = None, glyph: str = "") -> Banner:  # fmt: skip
        """Un bandeau d'annonce (mission, succès, élevage) : il ne se regroupe avec aucun autre."""
        self.expire(now)
        banner = Banner(item_id, 0, 0, now + shown_s, born=now, label=label, title=title, text=text, badge=badge, star=star, shown_s=shown_s,
                        kind=kind, deadline=deadline, gesture=gesture, glyph=glyph)  # fmt: skip
        self.banners.append(banner)
        del self.banners[: -self.limit]
        return banner

    def expire(self, now: float) -> None:
        self.banners = [banner for banner in self.banners if banner.until > now]

    def dismiss(self, gesture: tuple, now: float, fade_s: float = 0.35) -> None:
        """Efface en fondu les bandeaux de ce geste : il vient d'être fait."""
        for banner in self.banners:
            if banner.gesture == gesture and not banner.dismissed:
                banner.dismissed = True
                banner.until = min(banner.until, now + fade_s)

    def hold(self, now: float) -> None:
        """Le jeu n'est pas à l'écran : les bandeaux attendent, leur délai ne commencera qu'à son retour."""
        for banner in self.banners:
            if banner.dismissed:
                continue  # déjà fait : il ne revient pas
            banner.until = now + (banner.shown_s or self.shown_s)
            banner.born = now  # il apparaîtra au retour du jeu


FORGE_GAP_S = 20 * 60.0  # la mission n'est rappelée qu'une fois par séance d'atelier…
OTHER_ITEM_GAP_S = 2 * 60.0  # … sauf pour un autre modèle d'objet, au plus toutes les deux minutes
PLACED_FRESH_S = 60.0  # un objet posé il y a plus longtemps ne déclenche plus rien
SUCCESS_FRESH_S = 30 * 60.0  # une mission réussie il y a plus longtemps n'est plus annoncée


class Forge:
    """Ce qu'il faut annoncer en forgemagie : la mission du moment à l'ouverture de l'atelier, les succès du groupe.

    forge_path : écrit par la capture quand un objet est posé sur l'atelier ; focus_path : la mission du
    moment, écrite par l'interface ; les succès viennent de la base (les siens, et ceux reçus par le hub).
    Rien de ce qui précède le lancement n'est annoncé."""

    def __init__(self, conn: sqlite3.Connection, forge_path, focus_path, titles: dict[str, tuple[str, int]], now: float,
                 density: dict[int, float] | None = None) -> None:  # fmt: skip
        self.conn, self.forge_path, self.focus_path, self.titles = conn, forge_path, focus_path, titles
        self.density = density or {}  # poids d'un point de chaque caractéristique, pour juger l'objet posé
        self.shown_at = float("-inf")  # aucun rappel encore
        self.shown_item = 0
        self.placed = _read(forge_path).get("placed", 0.0)
        self.mine = {row[0] for row in conn.execute("SELECT mission_id FROM fm_missions_done")}
        self.theirs = set(self._others())

    def _others(self):
        for pseudo, character, missions in self.conn.execute("SELECT pseudo, character, missions FROM shared_fm_scores"):
            try:
                for mission, at in json.loads(missions):
                    yield pseudo, mission, at, character
            except (ValueError, TypeError):
                continue

    def mission(self, now: float) -> dict | None:
        """La mission à rappeler, si un objet vient d'être posé sur l'atelier."""
        data = _read(self.forge_path)
        placed = data.get("placed", 0.0)
        if not isinstance(placed, (int, float)) or placed <= self.placed:
            return None
        self.placed = placed
        item_id = data.get("item_id") if isinstance(data.get("item_id"), int) else 0
        gap = FORGE_GAP_S if item_id == self.shown_item else OTHER_ITEM_GAP_S
        if now - placed > PLACED_FRESH_S or now - self.shown_at < gap:
            return None
        focus = _read(self.focus_path)
        missions = [m for m in focus.get("missions") or [focus.get("mission")] if isinstance(m, dict) and m.get("title")]
        if not missions:
            return None
        chosen = pick(missions, self._verdicts(missions, item_id, data.get("effects")))
        self.shown_at, self.shown_item = now, item_id
        return chosen

    def _verdicts(self, missions: list[dict], item_id: int, raw) -> dict:
        """Pour chaque mission : va-t-elle avec l'objet posé ? (rien si l'objet est inconnu)"""
        if not item_id or not isinstance(raw, list):
            return {}
        try:
            effects = {e: v for e, v in raw if isinstance(e, int) and isinstance(v, int)}
            base = {e: (low, high) for e, low, high in self.conn.execute("SELECT effect_id, min_value, max_value FROM item_effects WHERE item_id = ?", (item_id,))}
            group = self.conn.execute("SELECT (SELECT COUNT(*) FROM fm_passes WHERE item_id = ?) + (SELECT COUNT(*) FROM shared_fm_passes WHERE item_id = ?)",
                                      (item_id, item_id)).fetchone()[0]  # fmt: skip
        except (sqlite3.Error, ValueError, TypeError):
            return {}
        return {m["id"]: fits(m["id"], effects, base, self.density, group) for m in missions}

    def successes(self, now: float, me: str | None) -> list[tuple[str, str, int]]:
        """Missions réussies depuis le dernier passage : (qui, mission, bonus)."""
        out = []
        for mission, at, character_id in self.conn.execute("SELECT mission_id, at, character_id FROM fm_missions_done"):
            if mission in self.mine:
                continue
            self.mine.add(mission)
            if now - at <= SUCCESS_FRESH_S and mission in self.titles:
                row = self.conn.execute("SELECT name FROM characters WHERE id = ?", (character_id,)).fetchone() if character_id else None
                out.append((row[0] if row else me or "Toi", *self.titles[mission]))
        for entry in list(self._others()):
            pseudo, mission, at, character = entry
            if (pseudo, mission, at, character) in self.theirs:
                continue
            self.theirs.add(entry)
            if now - at <= SUCCESS_FRESH_S and mission in self.titles:
                out.append((character or pseudo, *self.titles[mission]))
        return out


BREEDING_SHOWN_S = 300.0  # un bandeau d'élevage reste 5 minutes : on peut être occupé quand il tombe
BREEDING_AGAIN_S = 3600.0  # le même geste sur le même enclos n'est pas rappelé avant une heure


class Breeding:
    """Ce qu'il faut annoncer en élevage : le prochain geste de sérénité de chaque enclos, à son heure.

    Le calcul est refait seulement quand la base a vu l'enclos ou un clic sur ses objets ; chaque geste
    n'est annoncé qu'une fois. Un geste dont l'heure limite est passée n'est plus annoncé."""

    def __init__(self, conn: sqlite3.Connection, tiers=breeding.TIERS) -> None:
        self.conn, self.tiers = conn, tiers
        self.key: tuple | None = None
        self.advice: list[breeding.Advice] = []
        self.fired: dict[tuple, float] = {}
        self.waiting: dict[tuple, float] = {}  # gestes annoncés pas encore faits : quand ils l'ont été

    def due(self, now: float) -> list[breeding.Advice]:
        key = self.conn.execute(
            "SELECT (SELECT MAX(seen_at) FROM paddocks), (SELECT COUNT(*) FROM paddock_toggles), (SELECT MAX(ts) FROM paddock_toggles)"
        ).fetchone()
        if key != self.key:
            self.key = key
            self.advice = [a for p in breeding.load(self.conn) for a in breeding.gestures(p, self.tiers)]
        out = []
        for advice in self.advice:
            gesture = (advice.paddock_id, advice.kind, advice.running, advice.then)
            if not advice.at <= now <= max(advice.until, advice.at + 60):
                continue
            # Objet vide, jauges pleines : une fois, jusqu'à ce qu'on y touche. Sérénité : au plus une fois par heure.
            lasting = advice.kind in ("empty", "full")
            if (gesture in self.fired) if lasting else now - self.fired.get(gesture, -1e18) < BREEDING_AGAIN_S:
                continue
            self.fired[gesture] = self.waiting[gesture] = now
            out.append(advice)
        return out

    def done(self, now: float) -> list[tuple]:
        """Gestes annoncés que le joueur vient de faire : objet coupé, ou bascule faite (vu par ses clics ou une ouverture
        de l'enclos depuis l'annonce)."""
        if not self.waiting:
            return []
        out = []
        paddocks = {p.paddock_id: p for p in breeding.load(self.conn)}
        for gesture, at in list(self.waiting.items()):
            paddock_id, kind, running, then = gesture
            p = paddocks.get(paddock_id)
            if p is None:
                continue
            active, seen = set(p.active), p.seen_at
            for ts, index, on in p.toggles:
                (active.add if on else active.discard)(index)
                seen = max(seen, ts)
            if seen < at:
                continue  # rien de nouveau sur cet enclos depuis l'annonce
            refilled = kind == "empty" and p.seen_at >= at and p.objects[running] > 0
            if (running not in active and (then is None or then in active)) or refilled:
                out.append(gesture)
                del self.waiting[gesture]
                if kind in ("empty", "full"):
                    self.fired.pop(gesture, None)  # réglé : pourra être annoncé de nouveau plus tard
        return out


def breeding_text(advice: breeding.Advice) -> str:
    """Le détail d'un bandeau d'élevage : la zone visée, et ce qu'il faut couper."""
    who = f"{advice.mounts} DD"
    if advice.kind == "full":
        full = f"{advice.gauge.capitalize()} {breeding.FULL[advice.gauge]}"
        return f"{full} pour les {who}" if advice.mounts > 1 else full
    if advice.kind == "empty":
        return f"Recharge-le ou coupe-le · {who}"
    low, high = (kamas(abs(v)) if v >= 0 else "−" + kamas(abs(v)) for v in advice.zone)
    if advice.kind == "switch":
        return f"{breeding.OBJECT_NAMES[advice.running].capitalize()} → {breeding.OBJECT_NAMES[advice.then]} · {who} vers {low} à {high}"
    return f"{who} entre {low} et {high}"


def _read(path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def is_game(window_class: str, title: str, program: str = "", name: str = "Dofus") -> bool:
    """Vrai pour la fenêtre du jeu : celle du programme du jeu, ou à défaut une fenêtre Unity à son nom.

    program : nom du fichier lancé (« Dofus.exe »), quand Windows a bien voulu le dire."""
    if program:
        return program.lower().removesuffix(".exe") == name.lower()
    return window_class == "UnityWndClass" and name.lower() in title.lower()


def place(area: tuple[int, int, int, int], width: int, margin_x: int, margin_y: int) -> tuple[int, int]:
    """Coin haut gauche du bandeau, dans l'angle haut droit de la zone de jeu (gauche, haut, droite, bas)."""
    left, top, right, _ = area
    return max(left, right - width - margin_x), top + margin_y


def kamas(amount: int) -> str:
    """« 1 250 000 » : milliers séparés par une espace insécable."""
    return f"{amount:,}".replace(",", " ")

