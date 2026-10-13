"""Dessin des bandeaux : une image avec transparence, que la fenêtre n'a plus qu'à poser à l'écran.

Tout est dessiné ici plutôt qu'avec des boutons et des cadres de fenêtre : coins arrondis, ombre
douce, fondu d'apparition. Les couleurs sont celles du tableau de bord.
"""
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
    if picture is not None:
        image.alpha_composite(picture, (well[0] + s((WELL - ICON) / 2), well[1] + s((WELL - ICON) / 2)))
    else:
        centre = (well[0] + s(WELL / 2), well[1] + s(WELL / 2))
        draw.ellipse((centre[0] - s(5), centre[1] - s(5), centre[0] + s(5), centre[1] + s(5)), fill=(*LINE, 255))

    text_x = well[0] + s(WELL + 12)
    # Pastille de quantité, à droite : elle s'allume un instant quand une vente complète le bandeau.
    badge_face = font(s(13), bold=True)
    badge = f"×{view.quantity}"
    badge_w = round(draw.textlength(badge, font=badge_face)) + s(16)
    badge_box = (right - s(14) - badge_w, top + s(13), right - s(14), top + s(13 + 24))
    glow = 1 - ease(view.bumped / BUMP_S)
    fill = tuple(round(RAISED[i] + (ACCENT[i] - RAISED[i]) * glow) for i in range(3))
    ink = GROUND if glow > 0.3 else ACCENT
    draw.rounded_rectangle(badge_box, s(12), fill=(*fill, 255))
    draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2 - s(1)), badge, font=badge_face, fill=(*ink, 255), anchor="mm")

    draw.text((text_x, top + s(12)), "VENDU", font=font(s(10), bold=True), fill=(*MUTED, 255))
    name_face = font(s(15), bold=True)
    draw.text((text_x, top + s(25)), fit(draw, view.name, name_face, badge_box[0] - s(10) - text_x), font=name_face, fill=(*TEXT, 255))
    draw.text((text_x, top + s(47)), view.gain, font=font(s(14), bold=True), fill=(*ACCENT, 255))

    # Fil du temps restant, en bas.
    share = min(1.0, max(0.0, view.left / view.total)) if view.total else 0.0
    line_left, line_right, line_y = left + s(RADIUS), right - s(RADIUS), top + s(HEIGHT) - s(4)
    if share > 0:
        draw.rounded_rectangle((line_left, line_y, line_left + max(s(2), round((line_right - line_left) * share)), line_y + max(1, s(2))), s(1), fill=(*ACCENT, 150))
    return image


def render(views: list[View], scale: float = 1.0) -> Image.Image | None:
    """Tous les bandeaux, le plus récent en haut. None s'il n'y a rien à montrer."""
    if not views:
        return None
    s = lambda v: round(v * scale)  # noqa: E731
    step = s(HEIGHT + GAP)
    canvas = Image.new("RGBA", (s(WIDTH + 2 * PAD), s(2 * PAD + HEIGHT) + step * (len(views) - 1)), (0, 0, 0, 0))
    for row, view in enumerate(reversed(views)):
        shown = visibility(view)
        if shown <= 0:
            continue
        image = card(view, scale)
        if shown < 1:
            image.putalpha(image.getchannel("A").point(lambda a: round(a * shown)))
        slide = s(18 * (1 - shown))  # glisse depuis la droite en apparaissant
        canvas.alpha_composite(image.crop((0, 0, image.width - slide, image.height)), (slide, row * step))
    return canvas
