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
BREEDING = (92, 200, 168)  # le vert « gain » du tableau de bord : les bandeaux d'élevage se reconnaissent d'un coup d'œil
# Jauges d'élevage, aux couleurs du tableau de bord : amour rouge, endurance jaune, maturité bleu clair.
GAUGE_COLORS = {"amour": (242, 114, 122), "endurance": (245, 212, 83), "maturité": (127, 207, 245)}

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
    accent: tuple[int, int, int] = ACCENT  # liseré, étiquette, ligne du bas et fil du temps
    glyph: str | None = None  # « gauge » : un cadran de sérénité ; « amour », « endurance », « maturité » : le symbole de la jauge


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


@lru_cache(maxsize=8)
def card_base(scale: float, accent: tuple[int, int, int] = ACCENT) -> Image.Image:
    """Fond d'un bandeau, ombre comprise : le même pour tous, dessiné une fois par échelle et par couleur."""
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
    draw.rounded_rectangle((box[0] + s(1), box[1] + s(14), box[0] + s(4), box[3] - s(14)), s(2), fill=(*accent, 255))
    well = (box[0] + s(14), box[1] + s((HEIGHT - WELL) / 2))
    draw.rounded_rectangle((*well, well[0] + s(WELL), well[1] + s(WELL)), s(11), fill=(*GROUND, 255), outline=(*LINE, 255), width=max(1, s(1)))
    base.alpha_composite(card)
    return base


def gauge_glyph(draw: ImageDraw.ImageDraw, centre: tuple[float, float], scale: float, accent: tuple[int, int, int]) -> None:
    """Cadran de sérénité : un arc de −5 000 à 5 000, la zone visée allumée, et l'aiguille dedans."""
    s = lambda v: round(v * scale)  # noqa: E731
    radius, width = s(17), max(2, s(4))
    box = (centre[0] - radius, centre[1] - radius + s(5), centre[0] + radius, centre[1] + radius + s(5))
    draw.arc(box, 180, 360, fill=(*LINE, 255), width=width)
    draw.arc(box, 268, 310, fill=(*accent, 255), width=width)  # la zone : un peu à droite du milieu
    angle = math.radians(289)
    pivot = (centre[0], centre[1] + s(5))
    tip = (pivot[0] + (radius - s(6)) * math.cos(angle), pivot[1] + (radius - s(6)) * math.sin(angle))
    draw.line((pivot, tip), fill=(*TEXT, 255), width=max(2, s(2)))
    draw.ellipse((pivot[0] - s(3), pivot[1] - s(3), pivot[0] + s(3), pivot[1] + s(3)), fill=(*TEXT, 255))


def gauge_symbol(draw: ImageDraw.ImageDraw, centre: tuple[float, float], scale: float, gauge: str) -> None:
    """Symbole d'une jauge, comme dans le tableau de bord : coeur (amour), éclair (endurance), goutte (maturité).
    Dessiné sur une grille de 24, centrée sur le puits."""
    unit = 34 * scale / 24
    at = lambda x, y: (centre[0] + (x - 12) * unit, centre[1] + (y - 12) * unit)  # noqa: E731
    disc = lambda x, y, r: (*at(x - r, y - r), *at(x + r, y + r))  # noqa: E731
    fill = (*GAUGE_COLORS[gauge], 255)
    if gauge == "amour":
        draw.ellipse(disc(7.4, 9, 4.9), fill=fill)
        draw.ellipse(disc(16.6, 9, 4.9), fill=fill)
        draw.polygon([at(2.7, 10.6), at(12, 7.5), at(21.3, 10.6), at(12, 20.5)], fill=fill)
    elif gauge == "endurance":
        draw.polygon([at(13.5, 2), at(4.5, 13.5), at(10.5, 13.5), at(9.5, 22), at(18.5, 10.5), at(12.5, 10.5)], fill=fill)
    else:
        draw.ellipse(disc(12, 14.6, 6.5), fill=fill)
        draw.polygon([at(12, 2.5), at(5.8, 12.6), at(18.2, 12.6)], fill=fill)


def card(view: View, scale: float) -> Image.Image:
    s = lambda v: round(v * scale)  # noqa: E731
    image = card_base(scale, view.accent).copy()
    draw = ImageDraw.Draw(image)
    left, top, right = s(PAD), s(PAD), s(PAD + WIDTH)
    well = (left + s(14), top + s((HEIGHT - WELL) / 2))
    picture = icon_image(view.icon, s(ICON)) if view.icon else None
    if view.glyph == "gauge":
        gauge_glyph(draw, (well[0] + s(WELL / 2), well[1] + s(WELL / 2)), scale, view.accent)
    elif view.glyph in GAUGE_COLORS:
        gauge_symbol(draw, (well[0] + s(WELL / 2), well[1] + s(WELL / 2)), scale, view.glyph)
    elif view.star:
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
        fill = tuple(round(RAISED[i] + (view.accent[i] - RAISED[i]) * glow) for i in range(3))
        ink = GROUND if glow > 0.3 else view.accent
        draw.rounded_rectangle(badge_box, s(12), fill=(*fill, 255))
        draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2 - s(1)), badge, font=badge_face, fill=(*ink, 255), anchor="mm")
    else:
        badge_box = (right - s(4), 0, right, 0)

    draw.text((text_x, top + s(12)), view.label, font=font(s(10), bold=True), fill=(*(view.accent if view.star or view.glyph else MUTED), 255))
    name_face = font(s(15), bold=True)
    draw.text((text_x, top + s(25)), fit(draw, view.name, name_face, badge_box[0] - s(10) - text_x), font=name_face, fill=(*TEXT, 255))
    gain_face = font(s(14), bold=True)
    draw.text((text_x, top + s(47)), fit(draw, view.gain, gain_face, right - s(14) - text_x), font=gain_face, fill=(*view.accent, 255))

    # Fil du temps restant, en bas.
    share = min(1.0, max(0.0, view.left / view.total)) if view.total else 0.0
    line_left, line_right, line_y = left + s(RADIUS), right - s(RADIUS), top + s(HEIGHT) - s(4)
    if share > 0:
        draw.rounded_rectangle((line_left, line_y, line_left + max(s(2), round((line_right - line_left) * share)), line_y + max(1, s(2))), s(1), fill=(*view.accent, 150))
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


CONFUSION_WIDTH = 92  # pastille de la confusion (Comte Harebourg), à gauche du carré de la rune
CONFUSION_COLOURS = {0: MUTED, 90: ACCENT, 180: (240, 150, 70), 270: (232, 72, 64)}


def confusion_card(degrees: int, scale: float) -> Image.Image:
    """Ma confusion du moment, en degrés horaires : une petite pastille de la hauteur du carré de la rune."""
    s = lambda v: round(v * scale)  # noqa: E731
    colour = CONFUSION_COLOURS.get(degrees % 360, ACCENT)
    size = (s(CONFUSION_WIDTH + 2 * PAD), s(RUNE_SIZE + 2 * PAD))
    box = (s(PAD), s(PAD), s(PAD + CONFUSION_WIDTH) - 1, s(PAD + RUNE_SIZE) - 1)
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + s(3), box[2], box[3] + s(3)), s(12), fill=(0, 0, 0, 150))
    image = shadow.filter(ImageFilter.GaussianBlur(s(6)))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(box, s(12), fill=(*SURFACE, 245), outline=(*colour, 255), width=max(2, s(2)))
    centre = s(PAD + CONFUSION_WIDTH / 2)
    draw.text((centre, s(PAD + 20)), f"{degrees % 360}°", font=font(s(20), bold=True), fill=(*colour, 255), anchor="mm")
    draw.text((centre, s(PAD + 38)), "CONFUSION" if degrees % 360 else "AUCUNE", font=font(s(9), bold=True), fill=(*MUTED, 255), anchor="mm")
    return image


def render(views: list[View], scale: float = 1.0, rune: str | None = None, rune_icon: Path | None = None, rune_changed: float = 99.0,
           confusion: int | None = None) -> Image.Image | None:  # fmt: skip
    """Les pastilles (confusion, puis dernière rune à droite) s'il y en a, puis les bandeaux, le plus récent en haut.
    None s'il n'y a rien à montrer."""
    pills = [p for p in (confusion_card(confusion, scale) if confusion is not None else None,
                         rune_card(rune, scale, rune_icon, rune_changed) if rune else None) if p is not None]  # fmt: skip
    if not views and not pills:
        return None
    s = lambda v: round(v * scale)  # noqa: E731
    step = s(HEIGHT + GAP)
    row = sum(p.width for p in pills) - (len(pills) - 1) * (s(2 * PAD) - s(GAP))  # ombres l'une sur l'autre, un écart entre
    offset = max(p.height for p in pills) - s(2 * PAD) + s(GAP) if pills else 0
    height = offset + (s(2 * PAD + HEIGHT) + step * (len(views) - 1) if views else s(2 * PAD) - s(GAP))
    canvas = Image.new("RGBA", (max(s(WIDTH + 2 * PAD), row), height), (0, 0, 0, 0))
    x = canvas.width
    for pill in reversed(pills):
        x -= pill.width
        canvas.alpha_composite(pill, (x, 0))
        x += s(2 * PAD) - s(GAP)
    for row_index, view in enumerate(reversed(views)):
        shown = visibility(view)
        if shown <= 0:
            continue
        image = card(view, scale)
        if shown < 1:
            image.putalpha(image.getchannel("A").point(lambda a: round(a * shown)))
        slide = s(18 * (1 - shown))  # glisse depuis la droite en apparaissant
        canvas.alpha_composite(image.crop((0, 0, image.width - slide, image.height)), (slide, offset + row_index * step))
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


# --- fiche d'un monstre : une fenêtre qu'on ouvre en survolant un monstre ---------------------------------

SHEET_WIDTH, SHEET_PAD, SHEET_IMAGE = 400, 16, 64
SHEET_WARN, SHEET_OK, SHEET_SOFT = (240, 150, 70), (92, 200, 140), (201, 210, 208)
WEAK_COLOURS = {"Terre": ELEMENT_COLOURS["terre"], "Feu": ELEMENT_COLOURS["feu"], "Eau": ELEMENT_COLOURS["eau"], "Air": ELEMENT_COLOURS["air"], "Neutre": SHEET_SOFT}


def wrap(draw: ImageDraw.ImageDraw, text: str, face, width: float) -> list[str]:
    """Le texte coupé en lignes qui tiennent dans la largeur, mot à mot."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=face) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


def sheet_image(card, scale: float = 1.0, icons: dict | None = None, open_spells: bool = False) -> tuple[Image.Image, dict[str, tuple]]:
    """La fiche dessinée, ombre comprise, et ses zones cliquables (coordonnées dans l'image) :
    « close » la croix, « drag » l'en-tête par où on la déplace, « spells » le dépliage des sorts d'un boss."""
    s = lambda v: round(v * scale)  # noqa: E731
    icons = icons or {}
    width, pad = s(SHEET_WIDTH), s(SHEET_PAD)
    inner = width - 2 * pad
    sheet = Image.new("RGBA", (width, s(2400)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    small, label, body, bold, title = font(s(11)), font(s(11), True), font(s(13)), font(s(13), True), font(s(20), True)
    regions: dict[str, tuple] = {}

    # En-tête : image, nom, ligne du donjon, chiffres ; la croix à droite.
    y = pad
    well = (pad, y, pad + s(SHEET_IMAGE), y + s(SHEET_IMAGE))
    draw.rounded_rectangle(well, s(12), fill=(*GROUND, 255), outline=(*(ACCENT if card.boss else LINE), 255), width=max(1, s(2 if card.boss else 1)))
    picture = icon_image(icons[card.monster], s(SHEET_IMAGE - 4)) if icons.get(card.monster) else None
    if picture is not None:
        sheet.alpha_composite(picture, (well[0] + s(2), well[1] + s(2)))
    left = well[2] + s(12)
    text_y = y + s(2)
    if card.boss:
        draw.text((left, text_y), card.label or "BOSS", font=label, fill=(*ACCENT, 255))
        text_y += s(16)
    name = fit(draw, card.name, title, inner - (left - pad) - s(44) - (s(70) if card.focus_rank else 0))
    draw.text((left, text_y), name, font=title, fill=(*TEXT, 255))
    if card.focus_rank:
        badge_x = left + draw.textlength(name, font=title) + s(8)
        tag = f"FOCUS {card.focus_rank}"
        draw.rounded_rectangle((badge_x, text_y + s(5), badge_x + draw.textlength(tag, font=label) + s(12), text_y + s(23)), s(6), fill=(*ACCENT, 255))
        draw.text((badge_x + s(6), text_y + s(6)), tag, font=label, fill=(*SURFACE, 255))
    text_y += s(28)
    if card.subtitle:
        draw.text((left, text_y), fit(draw, card.subtitle, small, inner - (left - pad)), font=small, fill=(*MUTED, 255))
        text_y += s(16)
    stats = f"{card.stats} · faible "
    draw.text((left, text_y), stats, font=small, fill=(*MUTED, 255))
    draw.text((left + draw.textlength(stats, font=small), text_y), card.weakest, font=label, fill=(*WEAK_COLOURS.get(card.weakest, SHEET_SOFT), 255))
    close = (width - pad - s(36), pad - s(6), width - pad + s(6), pad + s(36))
    cx, cy = (close[0] + close[2]) / 2, (close[1] + close[3]) / 2
    for dx, dy in ((1, 1), (1, -1)):
        draw.line((cx - s(7) * dx, cy - s(7) * dy, cx + s(7) * dx, cy + s(7) * dy), fill=(*MUTED, 255), width=max(2, s(2)))
    regions["close"] = close
    y = max(well[3], text_y + s(16)) + s(12)
    regions["drag"] = (0, 0, close[0], y)

    def rule() -> None:
        nonlocal y
        draw.line((pad, y, width - pad, y), fill=(*LINE, 255), width=max(1, s(1)))
        y += s(12)

    def heading(text: str) -> None:
        nonlocal y
        draw.text((pad, y), text, font=label, fill=(*MUTED, 255))
        y += s(18)

    # Boss : l'essentiel du donjon, puis l'ordre de focus.
    if card.essentials:
        for kind, head, text in card.essentials:
            colour = SHEET_OK if kind == "ok" else SHEET_WARN
            draw.ellipse((pad, y + s(2), pad + s(14), y + s(16)), fill=(*colour, 255))
            draw.text((pad + s(7), y + s(9)), "✓" if kind == "ok" else "!", font=label, fill=(*SURFACE, 255), anchor="mm")
            for line in wrap(draw, f"{head}. {text}" if head else text, body, inner - s(24)):
                draw.text((pad + s(24), y), line, font=body, fill=(*TEXT, 255))
                y += s(18)
            y += s(5)
        y += s(3)
        rule()
    if card.focus:
        heading("ORDRE DE FOCUS")
        x = pad + s(5)
        y += s(4)
        for rank, (monster, who, _) in enumerate(card.focus, 1):
            box = (x, y, x + s(46), y + s(46))
            draw.rounded_rectangle(box, s(10), fill=(*GROUND, 255), outline=(*LINE, 255), width=max(1, s(1)))
            thumb = icon_image(icons[monster], s(42)) if icons.get(monster) else None
            if thumb is not None:
                sheet.alpha_composite(thumb, (box[0] + s(2), box[1] + s(2)))
            draw.ellipse((box[0] - s(5), box[1] - s(5), box[0] + s(13), box[1] + s(13)), fill=(*ACCENT, 255))
            draw.text((box[0] + s(4), box[1] + s(4)), str(rank), font=label, fill=(*SURFACE, 255), anchor="mm")
            draw.text((box[0] + s(23), box[3] + s(4)), fit(draw, who, small, s(70)), font=small, fill=(*TEXT, 255), anchor="ma")
            x += s(76)
        y += s(68)
        rule()

    # Ses trois lignes : portée, déplacements, spécial.
    for name, text in card.rows:
        draw.text((pad, y + s(2)), name, font=label, fill=(*MUTED, 255))
        for line in wrap(draw, text, body, inner - s(86)):
            draw.text((pad + s(86), y), line, font=body, fill=(*(SHEET_WARN if name == "SPÉCIAL" else TEXT), 255))
            y += s(18)
        y += s(6)

    # Sorts : toujours pour un monstre, dépliés à la demande pour un boss (sa fiche est déjà longue).
    if card.spells:
        y += s(2)
        if card.boss and not open_spells:
            text = f"{len(card.spells)} sorts ›"
            draw.text((pad, y), text, font=label, fill=(*ACCENT, 255))
            regions["spells"] = (pad - s(4), y - s(8), pad + draw.textlength(text, font=label) + s(8), y + s(22))
            y += s(20)
        else:
            rule()
            heading("SORTS")
            for spell in card.spells:
                draw.text((pad, y), fit(draw, spell.name, bold, inner), font=bold, fill=(*TEXT, 255))
                y += s(19)
                for line in wrap(draw, " · ".join(part for part in (spell.reach, spell.limit) if part), small, inner):
                    draw.text((pad, y), line, font=small, fill=(*MUTED, 255))
                    y += s(15)
                for line in wrap(draw, spell.effect, small, inner):
                    draw.text((pad, y), line, font=small, fill=(*SHEET_SOFT, 255))
                    y += s(15)
                y += s(8)
            if card.boss:
                text = "replier ‹"
                draw.text((pad, y), text, font=label, fill=(*ACCENT, 255))
                regions["spells"] = (pad - s(4), y - s(8), pad + draw.textlength(text, font=label) + s(8), y + s(22))
                y += s(20)
    if card.footer:
        y += s(2)
        draw.text((pad, y), card.footer, font=small, fill=(*MUTED, 200))
        y += s(16)
    height = y + pad - s(4)

    # Fond et ombre, puis le contenu par-dessus.
    margin = s(PAD)
    image = Image.new("RGBA", (width + 2 * margin, height + 2 * margin), (0, 0, 0, 0))
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((margin, margin + s(4), margin + width - 1, margin + height - 1 + s(4)), s(14), fill=(0, 0, 0, 160))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(s(8))))
    ImageDraw.Draw(image).rounded_rectangle((margin, margin, margin + width - 1, margin + height - 1), s(14), fill=(*SURFACE, 250), outline=(*LINE, 255), width=max(1, s(1)))
    image.alpha_composite(sheet.crop((0, 0, width, height)), (margin, margin))
    shifted = {key: (box[0] + margin, box[1] + margin, box[2] + margin, box[3] + margin) for key, box in regions.items()}
    return image, shifted
