"""Notifications de vente : regroupement en bandeaux, sans fenêtre."""
import sqlite3

from dofustool import overlay
from dofustool.overlay import Board, Feed, Sale

WOOL, RING = 11, 22


def test_same_item_updates_its_banner():
    board = Board(shown_s=6.0)
    for i in range(5):  # cinq lots d'une laine achetés coup sur coup
        banner = board.add(Sale(i, 100.0 + i, WOOL, 1, 250), now=100.0 + i)
    assert len(board.banners) == 1
    assert (banner.quantity, banner.kamas, banner.version, banner.born, banner.bumped) == (5, 1250, 4, 100.0, 104.0)
    assert banner.until == 110.0  # le délai repart à chaque vente
    assert overlay.kamas(banner.kamas) == "1 250"


def test_other_item_opens_another_banner_and_old_ones_leave():
    board = Board(shown_s=6.0, limit=2)
    board.add(Sale(1, 0.0, WOOL, 10, 900), now=0.0)
    board.add(Sale(2, 1.0, RING, 1, 500_000), now=1.0)
    assert [b.item_id for b in board.banners] == [WOOL, RING]
    board.add(Sale(3, 2.0, 33, 1, 5), now=2.0)
    assert [b.item_id for b in board.banners] == [RING, 33]  # pas plus de deux à la fois
    board.expire(7.5)
    assert [b.item_id for b in board.banners] == [33]
    # le bandeau de la laine est parti : une nouvelle vente en rouvre un, qui repart de zéro
    banner = board.add(Sale(4, 9.0, WOOL, 1, 90), now=9.0)
    assert (banner.quantity, banner.kamas) == (1, 90) and [b.item_id for b in board.banners] == [WOOL]


def test_feed_only_announces_new_fresh_sales():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE trades (source_id INTEGER PRIMARY KEY, ts REAL, kind TEXT, item_id INTEGER, quantity INTEGER, price INTEGER, ref INTEGER)")
    conn.execute("INSERT INTO trades VALUES (5, 10.0, 'sale', ?, 1, 100, NULL)", (WOOL,))
    feed = Feed(conn)
    assert feed.poll(now=11.0) == []  # vendu avant le lancement
    conn.executemany("INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, NULL)", [
        (6, 20.0, "sale", WOOL, 10, 900),
        (7, 20.5, "purchase", RING, 1, 400),  # un achat n'est pas une vente
        (8, 21.0, "sale", RING, 1, 500),
    ])
    assert [(s.item_id, s.quantity, s.price) for s in feed.poll(now=22.0)] == [(WOOL, 10, 900), (RING, 1, 500)]
    assert feed.poll(now=23.0) == []
    conn.execute("INSERT INTO trades VALUES (9, 30.0, 'sale', ?, 1, 100, NULL)", (WOOL,))
    assert feed.poll(now=5000.0) == []  # rattrapage d'une vieille vente : pas d'annonce
    assert feed.last == 9


def test_banners_wait_while_the_game_is_not_in_front():
    board = Board(shown_s=6.0)
    board.add(Sale(1, 0.0, WOOL, 1, 250), now=0.0)
    for now in (3.0, 30.0, 300.0):  # on regarde autre chose
        board.hold(now)
    board.expire(304.0)
    assert len(board.banners) == 1  # encore là au retour sur le jeu, pour six secondes
    board.expire(306.5)
    assert board.banners == []


def test_only_the_game_window_and_its_top_right_corner():
    assert overlay.is_game("UnityWndClass", "Perso - Dofus 3.7.5.5")
    assert not overlay.is_game("Chrome_WidgetWin_1", "Prospection - Dofus")  # le tableau de bord dans un navigateur
    assert not overlay.is_game("UnityWndClass", "Un autre jeu")
    # quand Windows donne le nom du programme, c'est lui qui décide, quel que soit le titre
    assert overlay.is_game("UnityWndClass", "Perso", program="Dofus.exe")
    assert not overlay.is_game("UnityWndClass", "Dofus", program="AutreJeu.exe")
    assert not overlay.is_game("Chrome_WidgetWin_1", "Prospection - Dofus", program="chrome.exe")
    assert overlay.place((100, 50, 1700, 950), width=340, margin_x=24, margin_y=110) == (1336, 160)
    assert overlay.place((0, 0, 300, 200), width=340, margin_x=24, margin_y=10) == (0, 10)  # fenêtre trop étroite : calé à gauche


def test_render_draws_rounded_transparent_banners():
    from dofustool.overlay import render

    def view(**changes):
        base = dict(name="Laine de Bouftou", quantity=5, gain="+ 1 250 kamas", icon=None, age=3.0, left=3.0, total=6.0, bumped=99.0)
        return render.View(**{**base, **changes})

    assert render.render([]) is None
    one, two = render.render([view()]), render.render([view(), view(name="Anneau")])
    assert one.mode == "RGBA" and one.width == render.WIDTH + 2 * render.PAD
    assert two.height - one.height == render.HEIGHT + render.GAP  # un bandeau de plus, empilé
    alpha = one.getchannel("A")
    assert alpha.getpixel((0, 0)) == 0  # hors du bandeau : on voit le jeu
    assert alpha.getpixel((render.PAD + 1, render.PAD + 1)) < 120  # coin arrondi
    assert alpha.getpixel((one.width // 2, one.height // 2)) > 230  # cœur du bandeau, presque opaque
    assert render.render([view()], scale=2.0).size == (one.width * 2, one.height * 2)
    # fondu : à peine apparu ou sur le point de partir, le bandeau est plus transparent
    for faint in (view(age=0.02), view(left=0.03)):
        assert render.render([faint]).getchannel("A").getpixel((one.width // 2, one.height // 2)) < 130
    assert render.visibility(view(left=0.0)) == 0.0


def test_long_names_are_cut():
    from PIL import Image, ImageDraw

    from dofustool.overlay import render

    pen = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    face = render.font(15, bold=True)
    cut = render.fit(pen, "Anneau au nom vraiment beaucoup trop long pour tenir", face, 120)
    assert cut.endswith("…") and pen.textlength(cut, font=face) <= 120
    assert render.fit(pen, "Court", face, 120) == "Court"
