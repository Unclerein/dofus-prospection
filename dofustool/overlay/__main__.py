"""Fenêtre des notifications de vente, posée au-dessus du jeu.

Une fenêtre de ce programme, sans bordure, toujours au premier plan, que la souris traverse : elle
ne prend ni clic ni touche et n'envoie rien au jeu. Elle lit la base locale, en lecture seule.

Elle se pose dans l'angle haut droit de la fenêtre du jeu, et seulement quand le jeu est la fenêtre
active : pendant qu'on regarde autre chose, les bandeaux attendent son retour. Du jeu, elle ne lit
que ce que Windows dit de sa fenêtre (titre et position), jamais le programme lui-même.

Le jeu doit être en mode fenêtré ou plein écran fenêtré : en plein écran exclusif, Windows ne laisse
aucune autre fenêtre passer devant.

Usage : python -m dofustool.overlay [--demo] [--anywhere]
"""
import argparse
import ctypes
import sqlite3
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

from .. import db
from . import Banner, Board, Feed, Sale, is_game, lines, place

POLL_MS = 400
MARGIN_X, MARGIN_Y = 24, 110  # depuis l'angle haut droit de la zone de jeu
WIDTH, GAP = 340, 8
ICON_PX = 44
TRANSPARENT = "#010203"  # couleur rendue invisible : les espaces entre bandeaux laissent voir le jeu
BACK, EDGE, TEXT, GOLD = "#1c1e24", "#3a3d47", "#f1f1f1", "#e9bd55"

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000, 0x20, 0x80, 0x8000000


def pass_through(window: tk.Tk) -> None:
    """La souris traverse la fenêtre, qui ne prend jamais la main et n'apparaît pas dans la barre des tâches."""
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    handle = user32.GetParent(window.winfo_id()) or window.winfo_id()
    style = user32.GetWindowLongW(handle, GWL_EXSTYLE)
    user32.SetWindowLongW(handle, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def game_area(name: str = "Dofus") -> tuple[int, int, int, int] | None:
    """Zone de jeu à l'écran (gauche, haut, droite, bas) si le jeu est la fenêtre active, sinon None."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    if not handle or user32.IsIconic(handle):
        return None
    window_class, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(handle, window_class, 64)
    user32.GetWindowTextW(handle, title, 256)
    if not is_game(window_class.value, title.value, name):
        return None
    rect, origin = _Rect(), _Point(0, 0)
    if not user32.GetClientRect(handle, ctypes.byref(rect)) or not user32.ClientToScreen(handle, ctypes.byref(origin)):
        return None
    return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom


class Items:
    """Nom et image d'un item, depuis la base et le cache d'images du tableau de bord."""

    def __init__(self, conn: sqlite3.Connection | None, icon_dir: Path) -> None:
        self.conn, self.icon_dir = conn, icon_dir
        self.images: dict[int, tk.PhotoImage | None] = {}
        self.fetching: set[int] = set()
        self.demo_names: dict[int, str] = {}

    def name(self, item_id: int) -> str:
        if item_id in self.demo_names:
            return self.demo_names[item_id]
        row = self.conn.execute("SELECT name FROM items WHERE id = ?", (item_id,)).fetchone() if self.conn else None
        return row[0] if row else f"Objet {item_id}"

    def icon_id(self, item_id: int) -> int | None:
        row = self.conn.execute("SELECT icon_id FROM item_icons WHERE item_id = ?", (item_id,)).fetchone() if self.conn else None
        return row[0] if row else None

    def image(self, item_id: int) -> tk.PhotoImage | None:
        """L'image si elle est dans le cache ; sinon None, et elle est demandée à DofusDB en arrière-plan."""
        if self.images.get(item_id) is not None:
            return self.images[item_id]
        icon_id = self.icon_id(item_id)
        if icon_id is None:
            return None
        path = self.icon_dir / f"{icon_id}.png"
        if path.exists():
            try:
                full = tk.PhotoImage(file=str(path))
                self.images[item_id] = full.subsample(max(1, round(full.width() / ICON_PX)))
            except tk.TclError:
                self.images[item_id] = None
            return self.images[item_id]
        if item_id not in self.fetching:
            self.fetching.add(item_id)
            threading.Thread(target=self._fetch, args=(icon_id,), daemon=True).start()
        return None

    def _fetch(self, icon_id: int) -> None:
        from ..web.server import fetch_icon

        fetch_icon(icon_id, self.icon_dir)


class Window:
    def __init__(self, feed: Feed | None, items: Items, demo: list[tuple[float, Sale]] | None = None, anywhere: bool = False, game: str = "Dofus") -> None:
        self.feed, self.items, self.demo = feed, items, demo or []
        self.anywhere, self.game = anywhere, game  # anywhere : angle de l'écran, que le jeu soit là ou non
        self.at: tuple[int, int] | None = None
        self.board = Board()
        self.started = time.time()
        self.drawn: list[tuple[int, int, bool]] = []  # ce qui est à l'écran : (item, version, image présente)
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.94)
        self.root.configure(bg=TRANSPARENT)
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            pass
        self.root.withdraw()
        self.root.update_idletasks()
        pass_through(self.root)
        self.root.after(POLL_MS, self.tick)

    def tick(self) -> None:
        now = time.time()
        try:
            sales = self.feed.poll(now) if self.feed else []
        except sqlite3.Error:
            sales = []  # base occupée par la capture : on réessaie au prochain passage
        while self.demo and now - self.started >= self.demo[0][0]:
            sales.append(self.demo.pop(0)[1])
        for sale in sales:
            self.board.add(sale, now)
        area = (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()) if self.anywhere else game_area(self.game)
        if area is None:
            self.board.hold(now)
            self.root.withdraw()
            self.drawn, self.at = [], None
        else:
            self.board.expire(now)
            self.draw(place(area, WIDTH, MARGIN_X, MARGIN_Y))
        if self.feed is None and not self.demo and not self.board.banners:
            self.root.destroy()  # démonstration terminée
            return
        self.root.after(POLL_MS, self.tick)

    def draw(self, at: tuple[int, int]) -> None:
        wanted = [(b.item_id, b.version, self.items.image(b.item_id) is not None) for b in self.board.banners]
        if wanted == self.drawn and at == self.at:
            return
        self.drawn, self.at = wanted, at
        for child in self.root.winfo_children():
            child.destroy()
        if not self.board.banners:
            self.root.withdraw()
            return
        for banner in reversed(self.board.banners):  # la vente la plus récente en haut
            self.banner(banner).pack(fill="x", pady=(0, GAP))
        self.root.update_idletasks()
        height = self.root.winfo_reqheight()
        self.root.geometry(f"{WIDTH}x{height}+{at[0]}+{at[1]}")
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        pass_through(self.root)

    def banner(self, banner: Banner) -> tk.Frame:
        what, gain = lines(banner, self.items.name(banner.item_id))
        frame = tk.Frame(self.root, bg=BACK, highlightbackground=EDGE, highlightthickness=1)
        image = self.items.image(banner.item_id)
        icon = tk.Label(frame, image=image, bg=BACK, width=ICON_PX, height=ICON_PX) if image else tk.Frame(frame, bg=BACK, width=ICON_PX, height=ICON_PX)
        icon.pack(side="left", padx=(10, 8), pady=8)
        text = tk.Frame(frame, bg=BACK)
        text.pack(side="left", fill="x", expand=True, pady=6)
        tk.Label(text, text="Vendu", bg=BACK, fg="#9aa0ad", font=("Segoe UI", 8), anchor="w").pack(fill="x")
        tk.Label(text, text=what, bg=BACK, fg=TEXT, font=("Segoe UI Semibold", 11), anchor="w", wraplength=WIDTH - ICON_PX - 40, justify="left").pack(fill="x")
        tk.Label(text, text=gain, bg=BACK, fg=GOLD, font=("Segoe UI Semibold", 11), anchor="w").pack(fill="x")
        return frame

    def run(self) -> None:
        self.root.mainloop()


def demo_sales() -> tuple[list[tuple[float, Sale]], dict[int, str]]:
    """Ventes inventées pour voir la fenêtre sans rien vendre : cinq lots d'une ressource, puis un équipement."""
    wool, ring = -1, -2
    script = [(0.5 + 0.7 * i, Sale(i, 0.0, wool, 1, 250)) for i in range(5)] + [(5.0, Sale(9, 0.0, ring, 1, 1_250_000))]
    return script, {wool: "Laine de Bouftou", ring: "Anneau de démonstration"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--demo", action="store_true", help="affiche des ventes inventées, sans lire la base")
    parser.add_argument("--anywhere", action="store_true", help="dans l'angle de l'écran, même si le jeu n'est pas la fenêtre active")
    args = parser.parse_args()
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # texte net sur un écran à 125 % ou 150 %
        except (AttributeError, OSError):
            pass
    icon_dir = db.MARKET_PATH.parent / "icons"
    if args.demo:
        script, names = demo_sales()
        items = Items(None, icon_dir)
        items.demo_names = names
        Window(None, items, script, anywhere=args.anywhere).run()
        return 0
    if not db.MARKET_PATH.exists():
        print(f"Base absente : {db.MARKET_PATH}")
        return 1
    conn = sqlite3.connect(f"file:{db.MARKET_PATH.as_posix()}?mode=ro", uri=True, timeout=1.0)
    from .. import config

    Window(Feed(conn), Items(conn, icon_dir), anywhere=args.anywhere, game=config.load().dofus_process).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
