"""Fenêtre des notifications de vente, posée au-dessus du jeu.

Une fenêtre de ce programme, sans bordure, toujours au premier plan, que la souris traverse : elle
ne prend ni clic ni touche et n'envoie rien au jeu. Elle lit la base locale, en lecture seule.

Elle se pose dans l'angle haut droit de la fenêtre du jeu, et seulement quand le jeu est la fenêtre
active : pendant qu'on regarde autre chose, les bandeaux attendent son retour. Du jeu, elle ne lit
que ce que Windows dit de sa fenêtre (titre, position, nom du programme), comme le fait le
gestionnaire des tâches ; jamais sa mémoire.

Usage : python -m dofustool.overlay [--demo] [--demo-breeding] [--anywhere] [--probe]
"""
import argparse
import ctypes
import sqlite3
import sys
import threading
import time
import tkinter as tk
import traceback
from ctypes import wintypes
from pathlib import Path

from .. import db
from ..fight import live
from . import BREEDING_SHOWN_S, Board, Breeding, Feed, Forge, Sale, breeding_text, is_game, kamas, place
from . import render as draw
from . import sound

POLL_MS = 400  # au repos : nouvelles ventes, fenêtre active
FORGE_POLL_S = 3.0  # atelier de forgemagie et succès du groupe
BREEDING_POLL_S = 5.0  # enclos : le prochain geste de sérénité
SOUND_PATH = db.MARKET_PATH.parent / "sounds" / "chime.wav"
LOG_PATH = db.MARKET_PATH.parent / "overlay.log"
FRAME_MS = 33  # pendant qu'un bandeau est à l'écran
STALE_FIGHT_S = 180.0  # un combat sans nouvelle depuis ce temps n'est plus affiché (capture arrêtée en plein combat)
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


class Items:
    """Nom et image d'un item, depuis la base et le cache d'images du tableau de bord."""

    def __init__(self, conn: sqlite3.Connection | None, icon_dir: Path) -> None:
        self.conn, self.icon_dir = conn, icon_dir
        self.fetching: set[int] = set()
        self.demo_names: dict[int, str] = {}

    def rune_item(self) -> int:
        """Une rune de forgemagie, pour l'image du bandeau de mission."""
        row = self.conn.execute("SELECT id FROM items WHERE name = 'Rune Cri'").fetchone() if self.conn else None
        return row[0] if row else 0

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

    def effect_icon(self, asset: str | None) -> Path | None:
        """Icône de caractéristique du jeu (élément…), depuis le cache du tableau de bord ; demandée à DofusDB sinon."""
        if not asset:
            return None
        path = self.icon_dir / "effects" / f"{asset}.png"
        if path.exists():
            return path
        if asset not in self.fetching:
            self.fetching.add(asset)
            threading.Thread(target=self._fetch_effect, args=(asset,), daemon=True).start()
        return None

    def _fetch_effect(self, asset: str) -> None:
        from ..web.server import fetch_effect_icon

        fetch_effect_icon(asset, self.icon_dir)


class Window:
    def __init__(self, feed: Feed | None, items: Items, demo: list[tuple[float, Sale]] | None = None, anywhere: bool = False, game: str = "Dofus",
                 rune: bool = False, demo_rune: str | None = None, forge: Forge | None = None, me: str | None = None,
                 breeding: Breeding | None = None, chime: bool = False) -> None:  # fmt: skip
        self.feed, self.items, self.demo = feed, items, demo or []
        self.forge, self.me = forge, me  # forgemagie : mission du moment et succès du groupe
        self.forge_checked = 0.0
        self.breeding, self.chime = breeding, chime  # élevage : le prochain geste de chaque enclos, et un petit son
        self.breeding_checked = 0.0
        self.breeding_shown = BREEDING_SHOWN_S
        self.reported: set[str] = set()  # erreurs déjà notées dans le journal
        self.rune_icon_item = items.rune_item() if forge else 0
        self.is_demo = demo is not None
        self.anywhere, self.game = anywhere, game  # anywhere : angle de l'écran, que le jeu soit là ou non
        self.show_rune, self.rune = rune, demo_rune  # carte de ma dernière rune (Huppermage), en combat
        self.rune_changed = 0.0  # quand la rune a changé, pour l'éclat du puits
        self.board = Board()
        self.clock = 0.0  # temps de la démonstration : il n'avance que lorsque les bandeaux peuvent se voir
        self.checked = 0.0  # dernier passage sur la base et la fenêtre active
        self.area: tuple[int, int, int, int] | None = None
        self.scale = screen_scale()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.withdraw()
        self.layer = Layer(self.root)
        self.fight: dict = {"active": False}
        self.root.after(POLL_MS, self.tick)

    def tick(self) -> None:
        """Un passage, puis le suivant. Une erreur imprévue est notée dans data/overlay.log au lieu d'arrêter la fenêtre :
        sans cela, la boucle s'arrêterait en silence et plus aucun bandeau ne s'afficherait."""
        try:
            delay = self._tick()
        except Exception as exc:  # noqa: BLE001
            self.report(exc)
            delay = POLL_MS
        if delay is not None:
            self.root.after(delay, self.tick)

    def report(self, exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}"
        if message in self.reported:
            return
        self.reported.add(message)
        try:
            with LOG_PATH.open("a", encoding="utf-8") as log:
                log.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "".join(traceback.format_exception(exc)) + chr(10))
        except OSError:
            pass

    def _tick(self) -> int | None:
        """Délai avant le prochain passage (ms), ou None quand la démonstration est finie."""
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
            if self.forge is not None and now - self.forge_checked >= FORGE_POLL_S:
                self.forge_checked = now
                try:
                    self.announce(now)
                except sqlite3.Error:
                    pass  # base occupée : au prochain passage
            if self.breeding is not None and now - self.breeding_checked >= BREEDING_POLL_S:
                self.breeding_checked = now
                try:
                    for advice in self.breeding.due(now):
                        self.breed(advice, now)
                    for gesture in self.breeding.done(now):
                        self.board.dismiss(gesture, now)
                except sqlite3.Error:
                    pass  # base occupée, ou d'avant l'élevage : au prochain passage
            if not self.is_demo:  # combat en cours : rune du Huppermage, confusion au Comte
                fight = live.read()
                fresh = now - float(fight.get("updated") or 0) <= STALE_FIGHT_S
                self.fight = fight if fight.get("active") and fresh else {"active": False}
                if self.show_rune and self.fight.get("rune") != self.rune:
                    self.rune, self.rune_changed = self.fight.get("rune"), now
            self.area = (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()) if self.anywhere else game_area(self.game)
        if self.area is None:
            self.board.hold(now)
            self.layer.hide()
        else:
            self.board.expire(now)
            self.paint(now)
        if self.is_demo and not self.demo and not self.board.banners and not getattr(self.breeding, "script", None):
            self.root.destroy()  # démonstration terminée
            return None
        fast = bool(self.board.banners and self.area)
        fast = fast or (self.show_rune and self.rune and now - self.rune_changed < draw.BUMP_S + 0.2)
        return FRAME_MS if fast else POLL_MS

    def announce(self, now: float) -> None:
        mission = self.forge.mission(now)
        if mission:
            progress = f"{mission['count']}/{mission['target']}"
            text = f"+{mission['bonus']} pts · ≈ {str(mission['worth']).replace('.', ',')} pts par passage"
            self.board.note("FORGEMAGIE · MISSION DU MOMENT", mission["title"], text, now, item_id=self.rune_icon_item, badge=progress, shown_s=7.0)
        for who, title, bonus in self.forge.successes(now, self.me):
            self.board.note("SUCCÈS DÉBLOQUÉ", title, f"{who} · +{bonus} pts", now, star=True, shown_s=7.0)

    def breed(self, advice, now: float) -> None:
        """Bandeau d'élevage : il reste 5 minutes, sa pastille décompte le temps avant l'heure limite, et un petit son l'annonce."""
        text = breeding_text(advice)
        if advice.kind == "stop":
            text += " · avant " + time.strftime("%H:%M", time.localtime(advice.until))
        deadline = advice.until if advice.kind in ("stop", "switch") else 0.0  # objet vide, jauges pleines : pas d'heure limite
        gesture = (advice.paddock_id, advice.kind, advice.running, advice.then)
        self.board.note(f"ÉLEVAGE · ENCLOS {advice.paddock_id}", advice.title, text, now, shown_s=self.breeding_shown, kind="élevage",
                        deadline=deadline, gesture=gesture, glyph=advice.gauge if advice.kind in ("full", "empty") and advice.gauge else "")  # fmt: skip
        if self.chime:
            sound.play(SOUND_PATH)

    def paint(self, now: float) -> None:
        views = [
            draw.View(
                name=self.items.name(banner.item_id), quantity=banner.quantity, gain=f"+ {kamas(banner.kamas)} kamas",
                icon=self.items.icon(banner.item_id), age=now - banner.born, left=banner.until - now,
                total=self.board.shown_s, bumped=now - banner.bumped if banner.version else 99.0,
            ) if banner.label == "VENDU" else draw.View(
                name=banner.title, quantity=0, gain=banner.text, icon=self.items.icon(banner.item_id) if banner.item_id else None,
                age=now - banner.born, left=banner.until - now, total=banner.shown_s, bumped=99.0,
                label=banner.label, badge=countdown(banner.deadline - now) if banner.deadline else banner.badge, star=banner.star,
                **({"accent": draw.GAUGE_COLORS.get(banner.glyph, draw.BREEDING), "glyph": banner.glyph or "gauge"} if banner.kind == "élevage" else {}),
            )
            for banner in self.board.banners
        ]  # fmt: skip
        rune = self.rune if self.show_rune else None
        confusion = self.fight.get("confusion") if self.fight.get("active") and self.fight.get("map") == live.COMTE_MAP else None
        image = draw.render(views, self.scale, rune, self.items.effect_icon(draw.ELEMENT_ICONS.get(rune)), now - self.rune_changed, confusion)
        if image is None:
            self.layer.hide()
            return
        x, y = place(self.area, image.width, round(MARGIN_X * self.scale), round(MARGIN_Y * self.scale))
        self.layer.show(image, x, y)

    def run(self) -> None:
        self.root.mainloop()


def countdown(seconds: float) -> str:
    """Temps restant avant l'heure limite, en clair : « 1 h 05 », « 12 min », « 45 s », « 0 s »."""
    seconds = max(0, round(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60:02d}"
    if seconds >= 60:
        return f"{seconds // 60} min"
    return f"{seconds} s"


class DemoBreeding:
    """Deux gestes inventés pour voir les bandeaux d'élevage : une bascule, puis l'arrêt dans la zone."""

    def __init__(self, now: float) -> None:
        from ..analysis.breeding import LOVER, PATTER, SLAPPER, TROUGH, Advice

        self.script = [
            Advice(1, "switch", now + 0.5, now + 300, (1, 2000), PATTER, SLAPPER, 10),
            Advice(2, "stop", now + 3.5, now + 1980, (1, 2000), SLAPPER, None, 8),
            Advice(1, "full", now + 6.5, now + 3600, None, LOVER, None, 10),
            Advice(2, "empty", now + 9.5, now + 3600, None, TROUGH, None, 8),
        ]

    def due(self, now: float):
        ready = [a for a in self.script if a.at <= now]
        self.script = [a for a in self.script if a.at > now]
        return ready

    def done(self, now: float):
        return []


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
    parser.add_argument("--rune", choices=sorted(draw.ELEMENT_COLOURS), help="avec --demo : montre aussi la pastille de dernière rune")
    parser.add_argument("--probe", action="store_true", help="dit pendant douze secondes si la fenêtre active est reconnue comme le jeu")
    parser.add_argument("--demo-breeding", action="store_true", help="affiche deux bandeaux d'élevage inventés, avec le son ; rien n'est lu ni écrit")
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
    if args.demo_breeding:
        window = Window(None, Items(conn, icon_dir), [], anywhere=args.anywhere, game=game, breeding=DemoBreeding(time.time()), chime=True)
        window.is_demo, window.breeding_shown = True, 12.0
        window.run()
        return 0
    if args.demo:
        script, names = demo_sales(conn)
        items = Items(conn, icon_dir)
        items.demo_names = names
        Window(None, items, script, anywhere=args.anywhere, game=game, rune=bool(args.rune), demo_rune=args.rune).run()
        return 0
    if conn is None:
        print(f"Base absente : {db.MARKET_PATH}")
        return 1
    cfg = config.load()
    forge = None
    if cfg.fm_banners:
        from ..analysis.collecte import FOCUS_PATH, MISSIONS
        from ..analysis.fmdata import densities
        from ..fight.live import FIGHT_PATH

        titles = {m.id: (m.title, m.bonus) for m in MISSIONS}
        try:
            try:
                density = densities()
            except (sqlite3.Error, OSError):
                density = {}  # sans les données du jeu, les missions liées au poids de l'objet ne sont pas proposées
            forge = Forge(conn, FIGHT_PATH.with_name("forge.json"), FOCUS_PATH, titles, time.time(), density)
        except sqlite3.Error:
            forge = None  # base d'avant la collecte : la capture la mettra à jour
    breeding = None
    if cfg.breeding_banners:
        from ..analysis.breeding import load_tiers

        breeding = Breeding(conn, load_tiers())
    Window(Feed(conn) if cfg.sale_notifications else None, Items(conn, icon_dir), anywhere=args.anywhere, game=game,
           rune=cfg.huppermage_rune, forge=forge, me=cfg.share_pseudo or None, breeding=breeding, chime=cfg.breeding_sound).run()  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
