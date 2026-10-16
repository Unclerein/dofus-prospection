"""Notifications par-dessus le jeu : une petite fenêtre à part, qui affiche et ne fait rien d'autre.

Elle ne reçoit ni clic ni touche et ne touche pas au client : c'est une fenêtre de ce programme, posée
au-dessus des autres. Ce qu'elle montre vient de la base locale, que la capture remplit.

Ce module tient la logique, sans fenêtre : quelles ventes viennent d'arriver, et comment elles se
regroupent en bandeaux ; quand rappeler la mission de forgemagie, et quels succès annoncer.
"""
import json
import sqlite3
from dataclasses import dataclass

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

    def note(self, label: str, title: str, text: str, now: float, item_id: int = 0, badge: str = "", star: bool = False, shown_s: float = 6.0) -> Banner:
        """Un bandeau d'annonce (mission, succès) : il ne se regroupe avec aucun autre."""
        self.expire(now)
        banner = Banner(item_id, 0, 0, now + shown_s, born=now, label=label, title=title, text=text, badge=badge, star=star, shown_s=shown_s)
        self.banners.append(banner)
        del self.banners[: -self.limit]
        return banner

    def expire(self, now: float) -> None:
        self.banners = [banner for banner in self.banners if banner.until > now]

    def hold(self, now: float) -> None:
        """Le jeu n'est pas à l'écran : les bandeaux attendent, leur délai ne commencera qu'à son retour."""
        for banner in self.banners:
            banner.until = now + (banner.shown_s or self.shown_s)
            banner.born = now  # il apparaîtra au retour du jeu


FORGE_GAP_S = 20 * 60.0  # la mission n'est rappelée qu'une fois par séance d'atelier
PLACED_FRESH_S = 60.0  # un objet posé il y a plus longtemps ne déclenche plus rien
SUCCESS_FRESH_S = 30 * 60.0  # une mission réussie il y a plus longtemps n'est plus annoncée


class Forge:
    """Ce qu'il faut annoncer en forgemagie : la mission du moment à l'ouverture de l'atelier, les succès du groupe.

    forge_path : écrit par la capture quand un objet est posé sur l'atelier ; focus_path : la mission du
    moment, écrite par l'interface ; les succès viennent de la base (les siens, et ceux reçus par le hub).
    Rien de ce qui précède le lancement n'est annoncé."""

    def __init__(self, conn: sqlite3.Connection, forge_path, focus_path, titles: dict[str, tuple[str, int]], now: float) -> None:
        self.conn, self.forge_path, self.focus_path, self.titles = conn, forge_path, focus_path, titles
        self.shown_at = float("-inf")  # aucun rappel encore
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
        placed = _read(self.forge_path).get("placed", 0.0)
        if not isinstance(placed, (int, float)) or placed <= self.placed:
            return None
        self.placed = placed
        if now - placed > PLACED_FRESH_S or now - self.shown_at < FORGE_GAP_S:
            return None
        focus = _read(self.focus_path).get("mission")
        if not isinstance(focus, dict) or not focus.get("title"):
            return None
        self.shown_at = now
        return focus

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

