"""Dessin des bandeaux : une image avec transparence, que la fenêtre n'a plus qu'à poser à l'écran.

Tout est dessiné ici plutôt qu'avec des boutons et des cadres de fenêtre : coins arrondis, ombre
douce, fondu d'apparition. Les couleurs sont celles du tableau de bord.
"""
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

WIDTH, HEIGHT, GAP = 348, 76, 10  # un bandeau, en points (multipliés par l'échelle de l'écran)
PAD = 14  # marge autour des bandeaux, pour l'ombre
RADIUS, ICON, WELL = 14, 44, 54

SURFACE, RAISED, LINE = (21, 29, 33), (27, 37, 42), (44, 58, 66)
GROUND, TEXT, MUTED, ACCENT = (14, 19, 21), (233, 238, 236), (154, 168, 166), (227, 179, 65)

APPEAR_S, LEAVE_S, BUMP_S = 0.22, 0.35, 0.45


@dataclass(frozen=True)
class View:
    """Un bandeau tel qu'il doit être dessiné à un instant donné."""

    name: str
    quantity: int
    gain: str  # « + 1 250 kamas »
    icon: Path | None
    age: float  # secondes depuis son apparition
    left: float  # secondes avant sa disparition
    total: float  # durée d'affichage après la dernière vente
    bumped: float  # secondes depuis la dernière vente qui l'a complété ; grand si aucune
    label: str = "VENDU"
    badge: str | None = None  # None : « ×quantité » ; "" : pas de pastille
    star: bool = False  # une étoile dorée à la place de l'image


@lru_cache(maxsize=32)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in (("seguisb.ttf", "segoeuib.ttf") if bold else ("segoeui.ttf",)) + ("arial.ttf",):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


@lru_cache(maxsize=64)
def icon_image(path: Path, size: int) -> Image.Image | None:
    try:
        return Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
    except (OSError, ValueError):
        return None


def fit(draw: ImageDraw.ImageDraw, text: str, face, width: float) -> str:
    """Le texte, coupé par « … » s'il dépasse la largeur."""
    if draw.textlength(text, font=face) <= width:
        return text
    while len(text) > 1 and draw.textlength(text + "…", font=face) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def visibility(view: View) -> float:
    """0 à 1 : fondu à l'apparition et à la disparition."""
    return min(ease(view.age / APPEAR_S), ease(view.left / LEAVE_S))


@lru_cache(maxsize=4)
def card_base(scale: float) -> Image.Image:
    """Fond d'un bandeau, ombre comprise : le même pour tous, dessiné une fois par échelle."""
    s = lambda v: round(v * scale)  # noqa: E731
    size = (s(WIDTH + 2 * PAD), s(HEIGHT + 2 * PAD))
    box = (s(PAD), s(PAD), s(PAD + WIDTH) - 1, s(PAD + HEIGHT) - 1)
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + s(3), box[2], box[3] + s(3)), s(RADIUS), fill=(0, 0, 0, 150))
    base = shadow.filter(ImageFilter.GaussianBlur(s(6)))
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle(box, s(RADIUS), fill=(*SURFACE, 245), outline=(*LINE, 255), width=max(1, s(1)))
    # Liseré doré à gauche, et puits sombre pour l'image.
    draw.rounded_rectangle((box[0] + s(1), box[1] + s(14), box[0] + s(4), box[3] - s(14)), s(2), fill=(*ACCENT, 255))
    well = (box[0] + s(14), box[1] + s((HEIGHT - WELL) / 2))
    draw.rounded_rectangle((*well, well[0] + s(WELL), well[1] + s(WELL)), s(11), fill=(*GROUND, 255), outline=(*LINE, 255), width=max(1, s(1)))
    base.alpha_composite(card)
    return base


def card(view: View, scale: float) -> Image.Image:
    s = lambda v: round(v * scale)  # noqa: E731
    image = card_base(scale).copy()
    draw = ImageDraw.Draw(image)
    left, top, right = s(PAD), s(PAD), s(PAD + WIDTH)
    well = (left + s(14), top + s((HEIGHT - WELL) / 2))
    picture = icon_image(view.icon, s(ICON)) if view.icon else None
    if view.star:
        centre = (well[0] + s(WELL / 2), well[1] + s(WELL / 2))
        points = []
        for i in range(10):
            radius = s(19) if i % 2 == 0 else s(8)
            angle = math.pi / 2 + i * math.pi / 5
            points.append((centre[0] + radius * math.cos(angle), centre[1] - radius * math.sin(angle)))
        draw.polygon(points, fill=(*ACCENT, 255))
    elif picture is not None:
        image.alpha_composite(picture, (well[0] + s((WELL - ICON) / 2), well[1] + s((WELL - ICON) / 2)))
    else:
        centre = (well[0] + s(WELL / 2), well[1] + s(WELL / 2))
        draw.ellipse((centre[0] - s(5), centre[1] - s(5), centre[0] + s(5), centre[1] + s(5)), fill=(*LINE, 255))

    text_x = well[0] + s(WELL + 12)
    # Pastille de quantité, à droite : elle s'allume un instant quand une vente complète le bandeau.
    badge_face = font(s(13), bold=True)
    badge = f"×{view.quantity}" if view.badge is None else view.badge
    if badge:
        badge_w = round(draw.textlength(badge, font=badge_face)) + s(16)
        badge_box = (right - s(14) - badge_w, top + s(13), right - s(14), top + s(13 + 24))
        glow = 1 - ease(view.bumped / BUMP_S)
        fill = tuple(round(RAISED[i] + (ACCENT[i] - RAISED[i]) * glow) for i in range(3))
        ink = GROUND if glow > 0.3 else ACCENT
        draw.rounded_rectangle(badge_box, s(12), fill=(*fill, 255))
        draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2 - s(1)), badge, font=badge_face, fill=(*ink, 255), anchor="mm")
    else:
        badge_box = (right - s(4), 0, right, 0)

    draw.text((text_x, top + s(12)), view.label, font=font(s(10), bold=True), fill=(*(ACCENT if view.star else MUTED), 255))
    name_face = font(s(15), bold=True)
    draw.text((text_x, top + s(25)), fit(draw, view.name, name_face, badge_box[0] - s(10) - text_x), font=name_face, fill=(*TEXT, 255))
    gain_face = font(s(14), bold=True)
    draw.text((text_x, top + s(47)), fit(draw, view.gain, gain_face, right - s(14) - text_x), font=gain_face, fill=(*ACCENT, 255))

    # Fil du temps restant, en bas.
    share = min(1.0, max(0.0, view.left / view.total)) if view.total else 0.0
    line_left, line_right, line_y = left + s(RADIUS), right - s(RADIUS), top + s(HEIGHT) - s(4)
    if share > 0:
        draw.rounded_rectangle((line_left, line_y, line_left + max(s(2), round((line_right - line_left) * share)), line_y + max(1, s(2))), s(1), fill=(*ACCENT, 150))
    return image


RUNE_SIZE, RUNE_ICON = 48, 36  # carré de la dernière rune : juste l'icône de l'élément
RUNE_WIDTH = RUNE_HEIGHT = RUNE_SIZE
ELEMENT_COLOURS = {"terre": (190, 140, 82), "feu": (232, 101, 74), "eau": (79, 166, 232), "air": (108, 199, 122)}
ELEMENT_ICONS = {"terre": "tx_strength", "feu": "tx_intelligence", "eau": "tx_chance", "air": "tx_agility"}  # icônes du jeu


def rune_card(element: str, scale: float, icon: Path | None = None, changed: float = 99.0) -> Image.Image:
    """La dernière rune : l'icône de l'élément dans un petit carré sombre bordé de sa couleur.

    changed : secondes depuis le changement de rune ; le carré s'allume un instant à la couleur de l'élément."""
    s = lambda v: round(v * scale)  # noqa: E731
    colour = ELEMENT_COLOURS.get(element, MUTED)
    size = (s(RUNE_SIZE + 2 * PAD), s(RUNE_SIZE + 2 * PAD))
    box = (s(PAD), s(PAD), s(PAD + RUNE_SIZE) - 1, s(PAD + RUNE_SIZE) - 1)
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + s(3), box[2], box[3] + s(3)), s(12), fill=(0, 0, 0, 150))
    image = shadow.filter(ImageFilter.GaussianBlur(s(6)))
    top = Image.new("RGBA", size, (0, 0, 0, 0))
    glow = 1 - ease(changed / BUMP_S)
    fill = tuple(round(SURFACE[i] + (colour[i] - SURFACE[i]) * 0.4 * glow) for i in range(3))
    ImageDraw.Draw(top).rounded_rectangle(box, s(12), fill=(*fill, 245), outline=(*colour, 255), width=max(2, s(2)))
    image.alpha_composite(top)
    picture = icon_image(icon, s(RUNE_ICON)) if icon else None
    offset = s(PAD + (RUNE_SIZE - RUNE_ICON) / 2)
    if picture is not None:
        image.alpha_composite(picture, (offset, offset))
    else:
        centre = s(PAD + RUNE_SIZE / 2)
        ImageDraw.Draw(image).ellipse((centre - s(10), centre - s(10), centre + s(10), centre + s(10)), fill=(*colour, 255))
    return image


def render(views: list[View], scale: float = 1.0, rune: str | None = None, rune_icon: Path | None = None, rune_changed: float = 99.0) -> Image.Image | None:
    """La pastille de la dernière rune s'il y en a une, puis les bandeaux, le plus récent en haut. None s'il n'y a rien à montrer."""
    if not views and not rune:
        return None
    s = lambda v: round(v * scale)  # noqa: E731
    step = s(HEIGHT + GAP)
    pill = rune_card(rune, scale, rune_icon, rune_changed) if rune else None
    offset = pill.height - s(2 * PAD) + s(GAP) if pill is not None else 0
    height = offset + (s(2 * PAD + HEIGHT) + step * (len(views) - 1) if views else s(2 * PAD) - s(GAP))
    canvas = Image.new("RGBA", (s(WIDTH + 2 * PAD), height), (0, 0, 0, 0))
    if pill is not None:
        canvas.alpha_composite(pill, (canvas.width - pill.width, 0))
    for row, view in enumerate(reversed(views)):
        shown = visibility(view)
        if shown <= 0:
            continue
        image = card(view, scale)
        if shown < 1:
            image.putalpha(image.getchannel("A").point(lambda a: round(a * shown)))
        slide = s(18 * (1 - shown))  # glisse depuis la droite en apparaissant
        canvas.alpha_composite(image.crop((0, 0, image.width - slide, image.height)), (slide, offset + row * step))
    return canvas
