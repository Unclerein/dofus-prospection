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


RUNE_WIDTH, RUNE_HEIGHT, RUNE_WELL, RUNE_ICON = 214, 62, 44, 36
ELEMENT_COLOURS = {"terre": (190, 140, 82), "feu": (232, 101, 74), "eau": (79, 166, 232), "air": (108, 199, 122)}
ELEMENT_ICONS = {"terre": "tx_strength", "feu": "tx_intelligence", "eau": "tx_chance", "air": "tx_agility"}  # icônes du jeu


@lru_cache(maxsize=4)
def rune_base(scale: float) -> Image.Image:
    """Fond de la carte de rune, ombre comprise, comme celui des bandeaux de vente."""
    s = lambda v: round(v * scale)  # noqa: E731
    size = (s(RUNE_WIDTH + 2 * PAD), s(RUNE_HEIGHT + 2 * PAD))
    box = (s(PAD), s(PAD), s(PAD + RUNE_WIDTH) - 1, s(PAD + RUNE_HEIGHT) - 1)
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + s(3), box[2], box[3] + s(3)), s(RADIUS), fill=(0, 0, 0, 150))
    base = shadow.filter(ImageFilter.GaussianBlur(s(6)))
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle(box, s(RADIUS), fill=(*SURFACE, 245), outline=(*LINE, 255), width=max(1, s(1)))
    base.alpha_composite(card)
    return base


def rune_card(element: str, scale: float, icon: Path | None = None, changed: float = 99.0) -> Image.Image:
    """Carte « Dernière rune » : l'icône de l'élément dans un puits sombre, son nom, un liseré à sa couleur.

    changed : secondes depuis le changement de rune ; le puits s'allume un instant à la couleur de l'élément."""
    s = lambda v: round(v * scale)  # noqa: E731
    colour = ELEMENT_COLOURS.get(element, MUTED)
    image = rune_base(scale).copy()
    draw = ImageDraw.Draw(image)
    left, top = s(PAD), s(PAD)
    draw.rounded_rectangle((left + s(1), top + s(13), left + s(4), top + s(RUNE_HEIGHT - 13)), s(2), fill=(*colour, 255))
    glow = 1 - ease(changed / BUMP_S)
    well = (left + s(14), top + s((RUNE_HEIGHT - RUNE_WELL) / 2))
    well_box = (*well, well[0] + s(RUNE_WELL), well[1] + s(RUNE_WELL))
    fill = tuple(round(GROUND[i] + (colour[i] - GROUND[i]) * 0.35 * glow) for i in range(3))
    edge = tuple(round(LINE[i] + (colour[i] - LINE[i]) * max(0.45, glow)) for i in range(3))
    draw.rounded_rectangle(well_box, s(10), fill=(*fill, 255), outline=(*edge, 255), width=max(1, s(1.5)))
    picture = icon_image(icon, s(RUNE_ICON)) if icon else None
    if picture is not None:
        image.alpha_composite(picture, (well[0] + s((RUNE_WELL - RUNE_ICON) / 2), well[1] + s((RUNE_WELL - RUNE_ICON) / 2)))
    else:
        centre = (well[0] + s(RUNE_WELL / 2), well[1] + s(RUNE_WELL / 2))
        draw.ellipse((centre[0] - s(9), centre[1] - s(9), centre[0] + s(9), centre[1] + s(9)), fill=(*colour, 255))
    text_x = well[0] + s(RUNE_WELL + 12)
    draw.text((text_x, top + s(13)), "DERNIÈRE RUNE", font=font(s(10), bold=True), fill=(*MUTED, 255))
    draw.text((text_x, top + s(27)), element.capitalize(), font=font(s(17), bold=True), fill=(*colour, 255))
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


# --- visée du Comte Harebourg : dessinée sur toute la zone de jeu ------------------------------------

AIM_GOLD, AIM_SAFE, AIM_DELOCK, AIM_FIRE, AIM_DEATH = ACCENT, (88, 196, 236), (92, 200, 140), (240, 150, 70), (232, 72, 64)
VERDICT_COLOURS = {"SAFE": AIM_SAFE, "SWAP_FIRE": AIM_FIRE, "DELOCK": AIM_DELOCK, "DELOCK_SUMMON": AIM_DELOCK, "WIPE": AIM_DEATH}


def aim_image(size: tuple[int, int], cells: list[tuple[list, tuple, int, str]], lines: list[tuple[str, tuple]],
              at: tuple[float, float], scale: float = 1.0) -> Image.Image:  # fmt: skip
    """Image de la taille de la zone de jeu : des losanges sur des cases, et un cartouche de texte près du curseur.

    cells : (coins du losange, couleur, opacité du fond 0-255, étiquette) ; lines : (texte, couleur)."""
    s = lambda v: round(v * scale)  # noqa: E731
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for corners, colour, fill, label in cells:
        draw.polygon(corners, fill=(*colour, fill), outline=(*colour, 255), width=max(2, s(2)))
        if label:
            cx = sum(x for x, _ in corners) / 4
            top = min(y for _, y in corners)
            face = font(s(12), bold=True)
            w = draw.textlength(label, font=face)
            draw.rounded_rectangle((cx - w / 2 - s(6), top - s(22), cx + w / 2 + s(6), top - s(4)), s(6), fill=(*SURFACE, 235))
            draw.text((cx, top - s(13)), label, font=face, fill=(*colour, 255), anchor="mm")
    if lines:
        face = font(s(13), bold=True)
        width = max(draw.textlength(text, font=face) for text, _ in lines) + s(20)
        height = s(10) + len(lines) * s(19)
        x = min(at[0] + s(24), size[0] - width - s(4))
        y = min(at[1] + s(24), size[1] - height - s(4))
        draw.rounded_rectangle((x, y, x + width, y + height), s(8), fill=(*SURFACE, 238), outline=(*LINE, 255), width=max(1, s(1)))
        for row, (text, colour) in enumerate(lines):
            draw.text((x + s(10), y + s(5) + row * s(19)), text, font=face, fill=(*colour, 255))
    return image
