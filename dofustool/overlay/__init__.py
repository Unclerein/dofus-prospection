"""Notifications par-dessus le jeu : une petite fenêtre à part, qui affiche et ne fait rien d'autre.

Elle ne reçoit ni clic ni touche et ne touche pas au client : c'est une fenêtre de ce programme, posée
au-dessus des autres. Ce qu'elle montre vient de la base locale, que la capture remplit.

Ce module tient la logique, sans fenêtre : quelles ventes viennent d'arriver, et comment elles se
regroupent en bandeaux.
"""
import sqlite3
from dataclasses import dataclass

SHOWN_S = 6.0  # durée d'un bandeau après la dernière vente qui l'a touché
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
    version: int = 0  # change à chaque vente ajoutée : la fenêtre sait qu'il faut redessiner


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
                return banner
        banner = Banner(sale.item_id, sale.quantity, sale.price, now + self.shown_s)
        self.banners.append(banner)
        del self.banners[: -self.limit]  # trop de bandeaux : les plus anciens cèdent la place
        return banner

    def expire(self, now: float) -> None:
        self.banners = [banner for banner in self.banners if banner.until > now]

    def hold(self, now: float) -> None:
        """Le jeu n'est pas à l'écran : les bandeaux attendent, leur délai ne commencera qu'à son retour."""
        for banner in self.banners:
            banner.until = now + self.shown_s


def is_game(window_class: str, title: str, name: str = "Dofus") -> bool:
    """Vrai pour la fenêtre du jeu : une fenêtre Unity dont le titre porte le nom du jeu."""
    return window_class == "UnityWndClass" and name.lower() in title.lower()


def place(area: tuple[int, int, int, int], width: int, margin_x: int, margin_y: int) -> tuple[int, int]:
    """Coin haut gauche du bandeau, dans l'angle haut droit de la zone de jeu (gauche, haut, droite, bas)."""
    left, top, right, _ = area
    return max(left, right - width - margin_x), top + margin_y


def kamas(amount: int) -> str:
    """« 1 250 000 » : milliers séparés par une espace insécable."""
    return f"{amount:,}".replace(",", " ")


def lines(banner: Banner, name: str) -> tuple[str, str]:
    """Les deux lignes d'un bandeau : ce qui est parti, ce que ça rapporte."""
    return f"{banner.quantity} × {name}", f"+ {kamas(banner.kamas)} kamas"
