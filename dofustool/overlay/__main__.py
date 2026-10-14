"""Fenêtre des notifications de vente, posée au-dessus du jeu.

Une fenêtre de ce programme, sans bordure, toujours au premier plan, que la souris traverse : elle
ne prend ni clic ni touche et n'envoie rien au jeu. Elle lit la base locale, en lecture seule.

Elle se pose dans l'angle haut droit de la fenêtre du jeu, et seulement quand le jeu est la fenêtre
active : pendant qu'on regarde autre chose, les bandeaux attendent son retour. Du jeu, elle ne lit
que ce que Windows dit de sa fenêtre (titre, position, nom du programme), comme le fait le
gestionnaire des tâches ; jamais sa mémoire.

Usage : python -m dofustool.overlay [--demo] [--anywhere] [--probe]
"""
import argparse
import ctypes
import sqlite3
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

from .. import db
from ..fight import live
from . import Board, Feed, Sale, is_game, kamas, place
from . import aim
from . import render as draw
from ..fight import grid, harebourg

POLL_MS = 400  # au repos : nouvelles ventes, fenêtre active
FRAME_MS = 33  # pendant qu'un bandeau est à l'écran
STALE_FIGHT_S = 900.0  # un combat sans nouvelle depuis ce temps n'est plus affiché (capture arrêtée en plein combat)
MARGIN_X, MARGIN_Y = 10, 96  # depuis l'angle haut droit de la zone de jeu, ombre comprise

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000, 0x20, 0x80, 0x8000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 2, 0, 1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW, HWND_TOPMOST = 0x1, 0x2, 0x10, 0x40, -1


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _Size(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _Blend(ctypes.Structure):
    _fields_ = [("op", ctypes.c_byte), ("flags", ctypes.c_byte), ("alpha", ctypes.c_ubyte), ("format", ctypes.c_byte)]


class _BitmapHeader(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("width", ctypes.c_long), ("height", ctypes.c_long), ("planes", wintypes.WORD),
        ("bits", wintypes.WORD), ("compression", wintypes.DWORD), ("image_size", wintypes.DWORD),
        ("x_ppm", ctypes.c_long), ("y_ppm", ctypes.c_long), ("used", wintypes.DWORD), ("important", wintypes.DWORD),
    ]  # fmt: skip


def program_of(handle: int) -> str:
    """Nom du fichier du programme qui possède cette fenêtre (« Dofus.exe »), ou "" si Windows le refuse."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    kernel32.OpenProcess.restype = ctypes.c_void_p
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return ""
    try:
        path, size = ctypes.create_unicode_buffer(1024), ctypes.c_ulong(1024)
        found = kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(process), 0, path, ctypes.byref(size))
        return Path(path.value).name if found else ""
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(process))


def front_window() -> tuple[int, str, str, str] | None:
    """(fenêtre, type, titre, programme) de la fenêtre active ; None s'il n'y en a pas ou qu'elle est réduite."""
    user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    if not handle or user32.IsIconic(handle):
        return None
    window_class, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(handle, window_class, 64)
    user32.GetWindowTextW(handle, title, 256)
    return handle, window_class.value, title.value, program_of(handle)


def game_area(name: str = "Dofus") -> tuple[int, int, int, int] | None:
    """Zone de jeu à l'écran (gauche, haut, droite, bas) si le jeu est la fenêtre active, sinon None."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    front = front_window()
    if front is None or not is_game(front[1], front[2], front[3], name):
        return None
    handle = front[0]
    rect, origin = _Rect(), _Point(0, 0)
    if not user32.GetClientRect(handle, ctypes.byref(rect)) or not user32.ClientToScreen(handle, ctypes.byref(origin)):
        return None
    return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom


def screen_scale() -> float:
    """Échelle d'affichage de Windows (1.25 pour 125 %)."""
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96)
    except (AttributeError, OSError):
        return 1.0


class Layer:
    """Fenêtre qui montre une image avec sa transparence, sans jamais prendre la main ni un clic."""

    def __init__(self, root: tk.Tk) -> None:
        self.user32, self.gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        root.update_idletasks()
        self.handle = self.user32.GetParent(root.winfo_id()) or root.winfo_id()
        style = self.user32.GetWindowLongW(self.handle, GWL_EXSTYLE)
        self.user32.SetWindowLongW(self.handle, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        for function in (self.gdi32.CreateCompatibleDC, self.gdi32.CreateDIBSection, self.gdi32.SelectObject, self.user32.GetDC):
            function.restype = ctypes.c_void_p
        self.shown = False

    def show(self, image, x: int, y: int) -> bool:
        """Pose l'image (PIL, RGBA) à cet endroit de l'écran."""
        width, height = image.size
        pixels = image.convert("RGBa").tobytes("raw", "BGRa")  # couleurs déjà multipliées par la transparence
        header = _BitmapHeader(ctypes.sizeof(_BitmapHeader), width, -height, 1, 32, 0, len(pixels), 0, 0, 0, 0)
        screen = self.user32.GetDC(None)
        memory = self.gdi32.CreateCompatibleDC(ctypes.c_void_p(screen))
        bits = ctypes.c_void_p()
        bitmap = self.gdi32.CreateDIBSection(ctypes.c_void_p(memory), ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits:
            self.gdi32.DeleteDC(ctypes.c_void_p(memory))
            self.user32.ReleaseDC(None, ctypes.c_void_p(screen))
            return False
        ctypes.memmove(bits, pixels, len(pixels))
        previous = self.gdi32.SelectObject(ctypes.c_void_p(memory), ctypes.c_void_p(bitmap))
        done = self.user32.UpdateLayeredWindow(
            ctypes.c_void_p(self.handle), ctypes.c_void_p(screen), ctypes.byref(_Point(x, y)), ctypes.byref(_Size(width, height)),
            ctypes.c_void_p(memory), ctypes.byref(_Point(0, 0)), 0, ctypes.byref(_Blend(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)), ULW_ALPHA,
        )  # fmt: skip
        self.gdi32.SelectObject(ctypes.c_void_p(memory), ctypes.c_void_p(previous))
        self.gdi32.DeleteObject(ctypes.c_void_p(bitmap))
        self.gdi32.DeleteDC(ctypes.c_void_p(memory))
        self.user32.ReleaseDC(None, ctypes.c_void_p(screen))
        if not self.shown:
            self.user32.SetWindowPos(ctypes.c_void_p(self.handle), ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
            self.shown = True
        return bool(done)

    def hide(self) -> None:
        if self.shown:
            self.user32.ShowWindow(ctypes.c_void_p(self.handle), 0)
            self.shown = False


KEYS = {"XBUTTON1": 0x05, "XBUTTON2": 0x06, "MBUTTON": 0x04, "SHIFT": 0x10, "CTRL": 0x11, "ALT": 0x12, "CAPSLOCK": 0x14, "TAB": 0x09}
KEYS |= {f"F{n}": 0x6F + n for n in range(1, 25)}
RECALIBRATE_S = 3.0  # touche maintenue sur son propre personnage : on refait le calibrage


def key_code(name: str) -> int | None:
    """Code Windows d'une touche nommée dans la configuration : XBUTTON2 (souris 5), F8, ALT, une lettre…"""
    name = (name or "").strip().upper()
    if name in KEYS:
        return KEYS[name]
    if len(name) == 1 and (name.isalpha() or name.isdigit()):
        return ord(name)
    return None


def key_down(code: int) -> bool:
    """La touche est enfoncée en ce moment. Simple lecture de l'état du clavier : rien n'est envoyé ni retenu."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(code) & 0x8000)


def cursor() -> tuple[int, int]:
    point = _Point(0, 0)
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


VERDICT_SHORT = {
    "SAFE": "sans danger", "SWAP_FIRE": "échange : 81 à 100 dommages Feu à vous deux", "DELOCK": "délock",
    "DELOCK_SUMMON": "délock : ton invocation explose (alliés à 3 cases ou moins)", "WIPE": "AIR DU TEMPS : NE FRAPPE PAS",
}  # fmt: skip


class AimHelper:
    """Visée du Comte Harebourg : touche maintenue, la case survolée, la case où cliquer et la case d'arrivée.

    Ne s'occupe que de lire la position de la souris et l'état d'une touche ; ne clique ni n'appuie jamais."""

    def __init__(self, root: tk.Tk, key: int, scale: float) -> None:
        window = tk.Toplevel(root)
        window.overrideredirect(True)
        window.withdraw()
        self.layer, self.key, self.scale = Layer(window), key, scale
        self.layout = grid.load("comte")
        self.points: list[tuple[int, float, float]] = []  # calibrage en cours : (case, x, y)
        self.was_down = False
        self.down_since = 0.0
        self.drawn = None
        self.message = ""

    def tick(self, now: float, area, state: dict) -> bool:
        """Vrai tant que la touche est enfoncée (la fenêtre se redessine alors souvent)."""
        in_fight = state.get("active") and state.get("map") == aim.COMTE_MAP
        down = bool(area) and in_fight and key_down(self.key)
        pressed = down and not self.was_down
        self.was_down = down
        if not down:
            self.layer.hide()
            self.drawn = None
            return False
        if pressed:
            self.down_since = now
        left, top, right, bottom = area
        size = (right - left, bottom - top)
        x, y = cursor()
        at = (x - left, y - top)
        frame = aim.load_frame(size)
        me = next((f.get("cell") for f in state.get("fighters", []) if f.get("kind") == "moi"), None)
        comte = next((f.get("cell") for f in state.get("fighters", []) if f.get("monster") == aim.COMTE_MONSTER), None)
        if frame is not None and me is not None and frame.cell_at(*at) == me and now - self.down_since >= RECALIBRATE_S:
            frame, self.points, self.message = None, [], "Calibrage recommencé."
            aim.save_frame(size, aim.Frame(0, 0, 0))  # efface l'ancien : il sera remplacé au prochain calibrage
        if frame is None or frame.width <= 0:
            self._calibrate(pressed, at, size, me, comte)
            self._show(size, [], self._calibration_lines(), at, left, top)
            return True
        self._show_plan(frame, at, size, state, left, top)
        return True

    def _calibrate(self, pressed: bool, at, size, me, comte) -> None:
        if not pressed:
            return
        wanted = me if not self.points else comte
        if wanted is None:
            self.message = "Position inconnue : attends le début du combat."
            return
        self.points.append((wanted, *at))
        if len(self.points) < 2:
            return
        try:
            frame, error = aim.calibrate(self.points)
        except ValueError:
            frame, error = None, 1.0
        if frame is None or error > aim.MAX_RESIDUAL or frame.width < 20:
            self.points, self.message = [], "Calibrage raté : recommence, bien au centre des cases."
            return
        aim.save_frame(size, frame)
        self.points, self.message = [], "Calibrage enregistré."

    def _calibration_lines(self) -> list[tuple[str, tuple]]:
        step = "survole le centre de TON personnage et appuie" if not self.points else "survole le centre du COMTE et appuie"
        lines = [(f"Calibrage {len(self.points) + 1}/2 : {step}", draw.TEXT)]
        if self.message:
            lines.append((self.message, draw.MUTED))
        return lines

    def _show_plan(self, frame: aim.Frame, at, size, state: dict, left: int, top: int) -> None:
        target = frame.cell_at(*at)
        plan = aim.plan(state, target, self.layout) if target is not None else None
        key = (target, plan, state.get("confusion"), state.get("round"), size)
        if key == self.drawn:
            return
        self.drawn = key
        rotation = state.get("confusion")
        parity = "impair : celui qui frappe le Comte part" if int(state.get("round") or 1) % 2 else "pair : le Comte part"
        lines = [(f"Confusion {rotation}° horaire" if rotation else "Pas de confusion", draw.TEXT),
                 (f"Tour {state.get('round')} · {parity}", draw.MUTED)]  # fmt: skip
        cells = []
        if target is not None:
            cells.append((frame.diamond(target), (255, 255, 255), 0, ""))
        if plan is not None:
            same = plan.aim is not None and plan.aim == plan.landing  # viser la case même où l'autre va arriver
            if plan.aim is not None and not same:
                cells.append((frame.diamond(plan.aim), draw.AIM_GOLD, 110, "VISE ICI"))
            if not plan.aim_ok:
                lines.append(("La case à viser n'est pas une case du sol", draw.AIM_FIRE))
            for c in plan.blast:
                cells.append((frame.diamond(c), draw.AIM_DEATH, 40, ""))
            if plan.landing is not None and plan.verdict is not None:
                colour = draw.VERDICT_COLOURS[plan.verdict.name]
                label = ("TU ARRIVES" if plan.mover == "toi" else "LE COMTE ARRIVE") if plan.verdict.name != "WIPE" else "AIR DU TEMPS"
                cells.append((frame.diamond(plan.landing), colour, 130, f"VISE ICI · {label}" if same else label))
                lines.append((f"Frapper le Comte : {plan.mover} -> {VERDICT_SHORT[plan.verdict.name]}", colour))
            if plan.me_in_blast:
                lines.append(("Tu es dans l'explosion de ton invocation !", draw.AIM_DEATH))
        self._show(size, cells, lines, at, left, top)

    def _show(self, size, cells, lines, at, left: int, top: int) -> None:
        self.layer.show(draw.aim_image(size, cells, lines, at, self.scale), left, top)


class Items:
    """Nom et image d'un item, depuis la base et le cache d'images du tableau de bord."""

    def __init__(self, conn: sqlite3.Connection | None, icon_dir: Path) -> None:
        self.conn, self.icon_dir = conn, icon_dir
        self.fetching: set[int] = set()
        self.demo_names: dict[int, str] = {}

    def name(self, item_id: int) -> str:
        if item_id in self.demo_names:
            return self.demo_names[item_id]
        row = self.conn.execute("SELECT name FROM items WHERE id = ?", (item_id,)).fetchone() if self.conn else None
        return row[0] if row else f"Objet {item_id}"

    def icon(self, item_id: int) -> Path | None:
        """Le fichier de l'image s'il est dans le cache ; sinon None, et il est demandé à DofusDB en arrière-plan."""
        row = self.conn.execute("SELECT icon_id FROM item_icons WHERE item_id = ?", (item_id,)).fetchone() if self.conn else None
        if row is None:
            return None
        path = self.icon_dir / f"{row[0]}.png"
        if path.exists():
            return path
        if item_id not in self.fetching:
            self.fetching.add(item_id)
            threading.Thread(target=self._fetch, args=(row[0],), daemon=True).start()
        return None

    def _fetch(self, icon_id: int) -> None:
        from ..web.server import fetch_icon

        fetch_icon(icon_id, self.icon_dir)


class Window:
    def __init__(self, feed: Feed | None, items: Items, demo: list[tuple[float, Sale]] | None = None, anywhere: bool = False, game: str = "Dofus",
                 element: bool = False, demo_element: str | None = None, aim_key: int | None = None) -> None:  # fmt: skip
        self.feed, self.items, self.demo = feed, items, demo or []
        self.is_demo = demo is not None
        self.show_element, self.element = element, demo_element  # pastille de l'élément de mon dernier sort, en combat
        self.anywhere, self.game = anywhere, game  # anywhere : angle de l'écran, que le jeu soit là ou non
        self.board = Board()
        self.clock = 0.0  # temps de la démonstration : il n'avance que lorsque les bandeaux peuvent se voir
        self.checked = 0.0  # dernier passage sur la base et la fenêtre active
        self.area: tuple[int, int, int, int] | None = None
        self.scale = screen_scale()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.withdraw()
        self.layer = Layer(self.root)
        self.aim = AimHelper(self.root, aim_key, self.scale) if aim_key is not None else None
        self.fight: dict = {"active": False}
        self.root.after(POLL_MS, self.tick)

    def tick(self) -> None:
        now = time.time()
        if now - self.checked >= POLL_MS / 1000:
            if self.area is not None and self.checked:
                self.clock += now - self.checked
            self.checked = now
            try:
                sales = self.feed.poll(now) if self.feed else []
            except sqlite3.Error:
                sales = []  # base occupée par la capture : on réessaie au prochain passage
            while self.demo and self.clock >= self.demo[0][0]:
                sales.append(self.demo.pop(0)[1])
            for sale in sales:
                self.board.add(sale, now)
            if (self.show_element or self.aim) and not self.is_demo:
                fight = live.read()
                fresh = now - float(fight.get("updated") or 0) <= STALE_FIGHT_S
                self.fight = fight if fight.get("active") and fresh else {"active": False}
                if self.show_element:
                    self.element = self.fight.get("element")
            self.area = (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()) if self.anywhere else game_area(self.game)
        if self.area is None:
            self.board.hold(now)
            self.layer.hide()
        else:
            self.board.expire(now)
            self.paint(now)
        aiming = self.aim.tick(now, self.area, self.fight) if self.aim is not None else False
        if self.is_demo and not self.demo and not self.board.banners:
            self.root.destroy()  # démonstration terminée
            return
        fast = aiming or (self.board.banners and self.area) or (self.aim is not None and self.fight.get("map") == aim.COMTE_MAP)
        self.root.after(FRAME_MS if fast else POLL_MS, self.tick)

    def paint(self, now: float) -> None:
        views = [
            draw.View(
                name=self.items.name(banner.item_id), quantity=banner.quantity, gain=f"+ {kamas(banner.kamas)} kamas",
                icon=self.items.icon(banner.item_id), age=now - banner.born, left=banner.until - now,
                total=self.board.shown_s, bumped=now - banner.bumped if banner.version else 99.0,
            )
            for banner in self.board.banners
        ]  # fmt: skip
        image = draw.render(views, self.scale, self.element if self.show_element else None)
        if image is None:
            self.layer.hide()
            return
        x, y = place(self.area, image.width, round(MARGIN_X * self.scale), round(MARGIN_Y * self.scale))
        self.layer.show(image, x, y)

    def run(self) -> None:
        self.root.mainloop()


def demo_sales(conn: sqlite3.Connection | None) -> tuple[list[tuple[float, Sale]], dict[int, str]]:
    """Ventes inventées pour voir la fenêtre sans rien vendre : cinq lots d'une ressource, puis un équipement.

    Avec la base, ce sont de vrais objets, pour voir aussi leur image ; rien n'y est écrit."""
    wool, ring, names = -1, -2, {-1: "Laine de Bouftou", -2: "Anneau de démonstration"}
    if conn is not None:
        found = [conn.execute("SELECT i.id FROM items i JOIN item_icons c ON c.item_id = i.id WHERE i.name LIKE ? ORDER BY i.id LIMIT 1", (like,)).fetchone()
                 for like in ("Laine de Bouftou", "Anneau%")]  # fmt: skip
        if all(found):
            (wool,), (ring,) = found
            names = {}
    script = [(0.5 + 0.8 * i, Sale(i, 0.0, wool, 1, 250)) for i in range(5)] + [(5.5, Sale(9, 0.0, ring, 1, 1_250_000))]
    return script, names


def probe(name: str, seconds: int = 12) -> None:
    """Dit, seconde après seconde, ce que la fenêtre active est pour ce programme. Aucun titre n'est affiché."""
    print(f"Clique sur la fenêtre du jeu. Jeu attendu : {name}.")
    for _ in range(seconds):
        front = front_window()
        if front is None:
            print("  aucune fenêtre active")
        else:
            _, window_class, title, program = front
            area = game_area(name)
            print(f"  programme {program or '(inconnu)'} · type {window_class} · titre de {len(title)} caractères, "
                  f"contient le nom du jeu : {'oui' if name.lower() in title.lower() else 'non'} · "
                  f"reconnu : {'oui' if area else 'non'}{' · zone ' + str(area) if area else ''}")  # fmt: skip
        time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--demo", action="store_true", help="affiche des ventes inventées ; rien n'est écrit dans la base")
    parser.add_argument("--anywhere", action="store_true", help="dans l'angle de l'écran, même si le jeu n'est pas la fenêtre active")
    parser.add_argument("--element", choices=live.ELEMENTS, help="avec --demo : montre aussi la pastille de cet élément")
    parser.add_argument("--probe", action="store_true", help="dit pendant douze secondes si la fenêtre active est reconnue comme le jeu")
    args = parser.parse_args()
    if sys.platform != "win32":
        print("Les notifications par-dessus le jeu n'existent que sous Windows.")
        return 1
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # positions en vrais pixels, texte net à 125 % ou 150 %
    except (AttributeError, OSError):
        pass
    from .. import config

    game = config.load().dofus_process or "Dofus"
    if args.probe:
        probe(game)
        return 0
    icon_dir = db.MARKET_PATH.parent / "icons"
    conn = sqlite3.connect(f"file:{db.MARKET_PATH.as_posix()}?mode=ro", uri=True, timeout=1.0) if db.MARKET_PATH.exists() else None
    if args.demo:
        script, names = demo_sales(conn)
        items = Items(conn, icon_dir)
        items.demo_names = names
        Window(None, items, script, anywhere=args.anywhere, game=game, element=bool(args.element), demo_element=args.element).run()
        return 0
    if conn is None:
        print(f"Base absente : {db.MARKET_PATH}")
        return 1
    cfg = config.load()
    key = key_code(cfg.aim_key) if cfg.harebourg_aim else None
    if cfg.harebourg_aim and key is None:
        print(f"Touche de visée inconnue : « {cfg.aim_key} ». Exemples : XBUTTON2 (souris 5), XBUTTON1, F8, ALT, une lettre.")
    Window(Feed(conn) if cfg.sale_notifications else None, Items(conn, icon_dir), anywhere=args.anywhere, game=game, element=cfg.fight_element,
           aim_key=key).run()  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
