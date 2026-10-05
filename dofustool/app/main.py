"""Dashboard Streamlit. Lancement : streamlit run dofustool/app/main.py"""
import os
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

from dofustool import config, db
from dofustool.analysis.crafts import CRAFT
from dofustool.analysis.trends import OVER, UNDER
from dofustool.app import data

DB_PATH = Path(os.environ.get("DOFUSTOOL_DB", db.MARKET_PATH))
PAGES = ["Crafts", "Tendances", "Fiche objet", "État"]

KAMAS = st.column_config.NumberColumn(format="localized")
PERCENT = st.column_config.NumberColumn(format="%.0f %%")


def kamas(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}".replace(",", " ") + " kamas"


def ago(ts: float | None, now: float) -> str:
    if ts is None:
        return "jamais"
    seconds = max(0.0, now - ts)
    if seconds < 90:
        return "à l'instant"
    if seconds < 5400:
        return f"il y a {seconds / 60:.0f} min"
    if seconds < 172800:
        return f"il y a {seconds / 3600:.0f} h"
    return f"il y a {seconds / 86400:.0f} j"


def when(ts: float | None) -> str:
    return "—" if ts is None else datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")


def data_version() -> tuple:
    """Empreinte des données affichées : change dès qu'une capture écrit quelque chose de nouveau.

    Requête légère, appelée toutes les quelques secondes par l'actualisation automatique.
    """
    conn = db.connect(DB_PATH)
    try:
        stamp = conn.execute(
            "SELECT (SELECT COUNT(*) FROM snapshots), (SELECT MAX(captured_at) FROM market_history), "
            "(SELECT MAX(captured_at) FROM hdv_listings), (SELECT COUNT(*) FROM item_effects_fetched), "
            "(SELECT value FROM static_meta WHERE key = 'imported_at'), "
            "(SELECT group_concat(key || '=' || value, '|') FROM capture_status "
            " WHERE key IN ('started_ts', 'stopped_ts', 'decode_alert'))"
        ).fetchone()
    finally:
        conn.close()
    cfg_mtime = config.CONFIG_PATH.stat().st_mtime if config.CONFIG_PATH.exists() else 0.0
    return (*stamp, cfg_mtime)


@st.fragment(run_every=5)
def watch_for_new_data(shown: tuple) -> None:
    """Relance l'affichage quand la base a changé depuis le dernier rendu."""
    if data_version() != shown:
        st.rerun()


@st.cache_resource(ttl=120, max_entries=2, show_spinner="Calcul des marges…")
def load(version: tuple) -> dict:
    cfg = config.load()
    conn = db.connect(DB_PATH)
    try:
        now = time.time()
        ws = data.build_workspace(conn, cfg, now)
        trends, insufficient = data.trends_frame(conn, cfg, ws)
        return {
            "cfg": cfg,
            "ws": ws,
            "crafts": data.crafts_frame(conn, ws),
            "trends": trends,
            "insufficient": insufficient,
            "options": data.item_options(conn),
        }
    finally:
        conn.close()


def open_item(item_id: int) -> None:
    st.session_state["item_id"] = int(item_id)
    st.session_state["page"] = "Fiche objet"


def selectable_table(df, key: str, column_config: dict) -> None:
    """Tableau dont la sélection d'une ligne propose d'ouvrir la fiche de l'objet."""
    event = st.dataframe(
        df.drop(columns=["item_id"]),
        hide_index=True,
        width="stretch",
        column_config=column_config,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    rows = event.selection.rows
    if rows:
        row = df.iloc[rows[0]]
        st.button(f"Ouvrir la fiche : {row['Objet']}", on_click=open_item, args=(row["item_id"],), key=f"{key}-open")
    else:
        st.caption("Sélectionne une ligne pour ouvrir la fiche de l'objet.")


def page_crafts(state: dict) -> None:
    cfg, crafts = state["cfg"], state["crafts"]
    st.title("Crafts")
    if crafts.empty:
        st.info("Aucune recette : importe les données statiques (voir README).")
        return
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
        jobs = c1.multiselect("Métiers", sorted(crafts["Métier"].unique()), placeholder="Tous les métiers")
        levels = c2.slider("Niveau de la recette", 1, 200, (1, 200))
        capital = c3.number_input("Capital maximum (kamas)", min_value=0, value=0, step=100_000, help="0 = sans limite")
        liquidity = c4.number_input(
            "Vendus sur 7 j, minimum", min_value=0, value=cfg.min_liquidity, step=10,
            help="Les objets dont la liquidité est inconnue restent affichés.",
        )  # fmt: skip
        c5, c6, c7 = st.columns([2, 2, 3])
        sort = c5.radio("Classer par", ["Marge", "Marge %"], horizontal=True)
        own = c6.toggle("Mes métiers seulement", disabled=not cfg.jobs, help="Métiers et niveaux de config.toml.")
        incomplete = c7.toggle("Afficher les recettes incalculables", help="Prix manquant ou résultat non échangeable.")
    view = data.filter_crafts(
        crafts, jobs, levels, capital or None, int(liquidity), only_own_jobs=own, only_computable=not incomplete
    )
    key = "Marge pondérée" if sort == "Marge" else "Marge %"
    view = view.sort_values(key, ascending=False, na_position="last").drop(columns=["Marge pondérée"])
    if not cfg.jobs:
        view = view.drop(columns=["Mon métier"])
    hidden = len(crafts) - int(crafts["Marge"].notna().sum())
    st.caption(
        f"{len(view)} recettes affichées sur {len(crafts)}. {hidden} sont incalculables avec les prix actuels. "
        f"Taxe HDV : {cfg.hdv_tax:.0%}. Le coût retient le moins cher entre achat et craft de chaque ingrédient."
    )
    selectable_table(
        view.head(500),
        "crafts",
        {
            "Prix de vente": KAMAS, "Coût": KAMAS, "Marge": KAMAS, "Marge sans sous-craft": KAMAS,
            "Marge %": PERCENT, "Vendus 7 j": KAMAS, "Vendus 24 h": KAMAS,
        },
    )  # fmt: skip
    if len(view) > 500:
        st.caption("Seules les 500 premières lignes sont affichées : affine les filtres pour voir la suite.")


def page_trends(state: dict) -> None:
    cfg, trends = state["cfg"], state["trends"]
    st.title("Tendances")
    if trends.empty:
        st.info(
            f"Données insuffisantes pour {state['insufficient']} objets. Une tendance demande au moins "
            f"{cfg.min_snapshots_for_trend} relevés de prix moyens ({state['status']['snapshots']} pour l'instant), "
            "ou l'historique du cours du marché de l'objet."
        )
        return
    st.caption(
        f"Écart du prix courant à sa moyenne (30 j, sinon 7 j). Seuil de signal : {cfg.trend_threshold:.0%}. "
        f"{state['insufficient']} objets sont en données insuffisantes."
    )
    config_cols = {"Prix": KAMAS, "Moyenne 7 j": KAMAS, "Moyenne 30 j": KAMAS, "Écart %": PERCENT, "Vendus 7 j": KAMAS}
    under, over = st.tabs(["Sous-cotés (à acheter)", "Sur-cotés (à vendre)"])
    with under:
        view = trends[trends["Signal"] == UNDER].sort_values("Écart %").drop(columns=["Signal"])
        selectable_table(view.head(500), "under", config_cols) if not view.empty else st.info("Aucun objet sous-coté.")
    with over:
        view = trends[trends["Signal"] == OVER].sort_values("Écart %", ascending=False).drop(columns=["Signal"])
        selectable_table(view.head(500), "over", config_cols) if not view.empty else st.info("Aucun objet sur-coté.")


def price_chart(frame, column: str, empty: str) -> None:
    if len(frame) >= 2:
        st.line_chart(frame, x="Date", y=column, height=260)
    elif len(frame) == 1:
        st.caption(f"Un seul point pour l'instant : {kamas(frame[column].iloc[0])} le {frame['Date'].iloc[0]:%d/%m/%Y}.")
    elif empty:
        st.caption(empty)


def page_item(state: dict) -> None:
    options, ws = state["options"], state["ws"]
    st.title("Fiche objet")
    if not options:
        st.info("Aucun objet connu : importe les données statiques et lance une capture.")
        return
    ids = list(options)
    if st.session_state.get("item_id") not in options:
        st.session_state["item_id"] = ids[0]
    item_id = st.selectbox("Objet", ids, format_func=options.get, key="item_id")
    conn = db.connect(DB_PATH)
    try:
        detail = data.item_detail(conn, ws, item_id)
    finally:
        conn.close()
    item, ref, liquidity = detail["item"], detail["ref"], detail["liquidity"]

    st.subheader(item.name)
    st.caption(f"{detail['type']} · niveau {item.level} · " + ("échangeable" if item.exchangeable else "non échangeable"))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Prix de référence", kamas(ref.price if ref else None))
    c1.caption(f"{ref.source}, {ago(ref.ts, ws.now)}" if ref else "Aucun prix connu.")
    cost = detail["unit_cost"]
    c2.metric("Coût le plus bas", kamas(cost.cost))
    c2.caption(f"par {cost.mode}" if cost.mode else "Prix manquant.")
    c3.metric("Vendus sur 24 h", "—" if liquidity.qty_24h is None else f"{liquidity.qty_24h:,}".replace(",", " "))
    c4.metric("Vendus sur 7 j", "—" if liquidity.qty_7d is None else f"{liquidity.qty_7d:,}".replace(",", " "))
    if not liquidity.known:
        c3.caption("Inconnu : cours du marché jamais consulté.")
    else:
        c3.caption(f"Cours consulté {ago(detail['market_seen_at'], ws.now)}.")

    st.subheader("Cours du marché")
    if detail["history"]:
        for tab, frame in zip(st.tabs(list(detail["history"])), detail["history"].values()):
            with tab:
                price_chart(frame, "Prix", "")
                if frame["Quantité vendue"].notna().any():
                    st.bar_chart(frame, x="Date", y="Quantité vendue", height=180)
    else:
        st.caption("Pas d'historique : ouvre l'onglet « Cours du marché » de cet objet en jeu pendant une capture.")

    st.subheader("Hôtel de vente")
    hdv = detail["hdv"]
    if hdv is None:
        st.caption("Pas d'annonce connue : ouvre la fiche d'achat de cet objet à l'HDV pendant une capture.")
    elif hdv["kind"] == "lots":
        st.caption(f"Prix les plus bas par taille de lot, relevés {ago(hdv['captured_at'], ws.now)}.")
        st.dataframe(
            hdv["frame"],
            hide_index=True,
            column_config={"Prix du lot": KAMAS, "Prix unitaire": st.column_config.NumberColumn(format="%.2f")},
        )
    else:
        counts = ", ".join(f"{n} {label}" for label, n in hdv["counts"].items())
        st.caption(f"{len(hdv['frame'])} exemplaires en vente, relevés {ago(hdv['captured_at'], ws.now)} : {counts}.")
        if hdv["template_known"]:
            st.caption(f"Caractéristiques de base : {hdv['base'] or 'aucune'}.")
        else:
            st.warning(
                "Caractéristiques de base inconnues : exos et overs ne peuvent pas être repérés. "
                "Lance « python -m dofustool.staticdata.effects »."
            )
        st.dataframe(hdv["frame"], hide_index=True, width="stretch", column_config={"Prix": KAMAS})

    st.subheader("Prix moyen au fil des relevés")
    price_chart(detail["snapshots"], "Prix moyen", "Cet objet n'apparaît dans aucun relevé de prix moyens.")

    st.subheader("Recette")
    craft = detail["craft"]
    if craft is None:
        st.caption("Cet objet ne se fabrique pas.")
    else:
        r = craft["result"]
        own = {True: " · dans tes métiers", False: " · hors de tes métiers", None: ""}[r.own_job]
        st.caption(f"{r.job} niveau {r.recipe.level}{own}")
        m1, m2, m3 = st.columns(3)
        m1.metric("Vente nette de taxe", kamas(r.revenue))
        m2.metric("Coût de fabrication", kamas(r.recursive_cost))
        m3.metric("Marge", kamas(r.recursive_margin), None if r.margin_pct is None else f"{r.margin_pct:.0%}")
        if r.flags:
            st.warning("À noter : " + ", ".join(r.flags) + ".")
        selectable_table(
            craft["ingredients"].rename(columns={"Ingrédient": "Objet"}),
            "ingredients",
            {"Prix unitaire": KAMAS, "Coût retenu": KAMAS, "Sous-total": KAMAS},
        )
        if (craft["ingredients"]["Mode"] == CRAFT).any():
            st.caption("Mode « craft » : fabriquer cet ingrédient coûte moins cher que l'acheter.")

    st.subheader("Utilisé dans")
    if detail["used_in"].empty:
        st.caption("Cet objet n'entre dans aucune recette.")
    else:
        selectable_table(detail["used_in"], "used-in", {"Prix de vente": KAMAS, "Marge": KAMAS})


def page_status(state: dict) -> None:
    s, now = state["status"], time.time()
    st.title("État")
    c1, c2, c3 = st.columns(3)
    c1.metric("Capture", "En cours" if s["running"] else "Arrêtée")
    c1.caption(f"Démarrée {ago(s['started_ts'], now)}." if s["running"] else f"Dernier arrêt : {when(s['stopped_ts'])}.")
    c2.metric("Dernier relevé de prix", ago(s["last_snapshot_ts"], now))
    c2.caption(f"{when(s['last_snapshot_ts'])} · {s['priced_items']} objets · {s['snapshots']} relevé(s) en base.")
    c3.metric("Décodage", "Alerte" if s["decode_alert"] else "Normal")
    c3.caption(f"Derniers prix moyens décodés : {when(s['last_avg_prices_ts'])}.")
    if s["decode_alert"]:
        st.error(f"{s['decode_alert']} (alerte du {when(s['decode_alert_ts'])})")

    st.subheader("Données")
    st.dataframe(
        {
            "Élément": [
                "Dernière connexion au jeu captée", "Objets avec un dernier prix de vente",
                "Objets avec un historique du cours du marché", "Objets avec des annonces HDV",
                "Version des données statiques",
                "Import des données statiques", "Objets connus", "Recettes connues",
            ],
            "Valeur": [
                when(s["last_connection_ts"]), str(s["last_sales"]), str(s["history_items"]), str(s["hdv_items"]),
                s["static_version"] or "non importées", when(s["static_imported_at"]), str(s["items"]), str(s["recipes"]),
            ],
        },
        hide_index=True,
        width="stretch",
    )  # fmt: skip
    if state["ws"].unknown_jobs:
        st.warning("Métiers inconnus dans config.toml : " + ", ".join(state["ws"].unknown_jobs))
    st.subheader("Limites à garder en tête")
    st.markdown(
        "- Les prix moyens sont théoriques : lissés, en retard sur le marché, sans distinction de lot.\n"
        "- Les données précises ne couvrent que les objets consultés en jeu.\n"
        "- Les marges ignorent le temps passé et l'XP de métier.\n"
        "- Une mise à jour du jeu peut casser le décodage jusqu'à ré-identification."
    )


def main() -> None:
    st.set_page_config(page_title="dofustool", layout="wide")
    version = data_version()
    conn = db.connect(DB_PATH)
    try:
        # L'état de la capture (en cours ou non) dépend de l'heure : jamais mis en cache.
        state = {**load(version), "status": data.status(conn, time.time())}
    finally:
        conn.close()
    with st.sidebar:
        st.header("dofustool")
        page = st.radio("Page", PAGES, key="page", label_visibility="collapsed")
        s = state["status"]
        st.caption(f"Relevé : {ago(s['last_snapshot_ts'], time.time())}")
        st.caption("Capture en cours" if s["running"] else "Capture arrêtée")
        if st.toggle("Actualisation automatique", value=True, help="Affiche les nouvelles données dès leur capture."):
            watch_for_new_data(version)
        if st.button("Recharger les données"):
            load.clear()
            st.rerun()
    if state["status"]["decode_alert"] and page != "État":
        st.error("Alerte de décodage : voir la page État.")
    {"Crafts": page_crafts, "Tendances": page_trends, "Fiche objet": page_item, "État": page_status}[page](state)


main()
