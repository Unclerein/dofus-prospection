"""Capture live : écoute passive du port de jeu, archivage et écriture en base.

S'arrête sur Ctrl+C ou quand le fichier data/capture.stop apparaît (utilisé par le lanceur).
Le fichier data/capture.ready existe tant que l'écoute est en place.

Usage : python -m dofustool.capture [--iface NOM]
"""
import argparse
import logging
import sys
import time

from .. import config, db
from ..archive import ARCHIVE_PATH, Archive
from ..archive import connect as archive_reader
from ..db.backfill import backfill_breeding, backfill_fm, fm_backfill_due
from ..messages import load_keymap, runtime_keymap_path
from . import GAME_PORT
from .pipeline import Pipeline, log

DATA_DIR = db.MARKET_PATH.parent
READY_FILE = DATA_DIR / "capture.ready"
STOP_FILE = DATA_DIR / "capture.stop"
LOG_FILE = DATA_DIR / "capture.log"

# La page État dit quand un paquet du jeu a été entendu pour la dernière fois : écrit au plus à ce rythme.
PACKET_NOTE_S = 20.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--iface", help="interface réseau (défaut : config.toml, sinon celle de scapy)")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")],
    )
    logging.getLogger("scapy").setLevel(logging.ERROR)

    cfg = config.load()
    from ..fight.live import FIGHT_PATH
    from .live_source import LiveSource  # import tardif : scapy est lent à charger

    STOP_FILE.unlink(missing_ok=True)
    market = db.connect()
    archive = Archive()
    keymap_path = runtime_keymap_path()
    keymap = load_keymap(keymap_path)
    try:
        removed = archive.prune(time.time(), {mapping.key for mapping in keymap.values()})
        if removed:
            log.info("Archive allégée : %d anciens messages non décodés supprimés.", removed)
        thinned = db.thin_snapshots(market, time.time())
        if thinned:
            log.info("Anciens relevés de prix moyens : %d supprimés, un par jour est gardé.", thinned)
    except Exception as exc:  # le ménage ne doit jamais empêcher la capture
        log.warning("Ménage des anciennes données non fait : %s", exc)
    pipeline = Pipeline(archive, market, keymap, cfg.avg_prices_timeout_s, keymap_path, started_at=time.time(), fight_path=FIGHT_PATH,
                        forge_path=FIGHT_PATH.with_name("forge.json"))
    source = LiveSource(iface=args.iface or cfg.iface)
    try:
        source.start()
    except Exception as exc:
        log.error("Impossible de démarrer l'écoute sur « %s » : %s", source.iface, exc)
        return 1
    db.set_status(market, started_ts=time.time(), stopped_ts="", decode_alert="", awaiting_prices_since="", last_packet_ts="")
    READY_FILE.write_text(str(time.time()), encoding="utf-8")
    log.info("Capture démarrée (écoute passive, tcp port %d, interface « %s »).", GAME_PORT, source.iface)
    # Rattrapage du journal de forgemagie, une fois après la mise à jour qui garde l'état des objets à chaque
    # passage. Il peut durer : il se fait écoute déjà lancée, les paquets du jeu attendent leur tour.
    try:
        if fm_backfill_due(market):
            log.info("Journal de forgemagie : rattrapage depuis l'archive, la capture écoute déjà…")
            done = backfill_fm(archive_reader(ARCHIVE_PATH), market)
            log.info("Journal de forgemagie complété depuis l'archive : %s.", ", ".join(f"{v} {k}" for k, v in done.items()))
    except Exception as exc:  # le rattrapage ne doit jamais empêcher la capture
        log.warning("Rattrapage du journal de forgemagie non fait : %s", exc)
    try:  # élevage vu avant que la capture sache le lire (étable, enclos, aperçus d'accouplement) : relu une fois
        done = backfill_breeding(archive_reader(ARCHIVE_PATH), market)
        if done:
            log.info("Élevage relu dans l'archive : %s.", ", ".join(f"{v} {k}" for k, v in done.items()))
    except Exception as exc:
        log.warning("Rattrapage de l'élevage non fait : %s", exc)
    # Version du jeu des passages récents, poids des caractéristiques et fiches des objets de cette version :
    # de quoi relire un passage avec les règles de son époque après une mise à jour du jeu.
    try:
        from ..analysis.fmdata import densities
        from ..staticdata import effects as base_effects

        tagged = db.tag_game_version(market)
        if tagged:
            log.info("Version du jeu notée sur %d passages de forgemagie.", tagged)
        db.record_densities(market, densities())
        db.snapshot_templates(market, base_effects.known_version(market), time.time())
    except Exception as exc:  # noqa: BLE001 — jamais au prix de la capture
        log.warning("Version du jeu des passages non notée : %s", exc)
    noted = 0.0  # dernière fois que l'arrivée d'un paquet du jeu a été notée dans l'état
    try:
        while not STOP_FILE.exists():
            segment = source.poll(1.0)
            heard = segment is not None
            while segment is not None:
                pipeline.handle(segment)
                segment = source.poll(0)
            now = time.time()
            if heard and now - noted >= PACKET_NOTE_S:
                db.set_status(market, last_packet_ts=now)
                noted = now
            pipeline.tick(now)
    except KeyboardInterrupt:
        pass
    finally:
        source.stop()
        pipeline.tick(time.time())
        db.set_status(market, stopped_ts=time.time())
        archive.close()
        market.close()
        READY_FILE.unlink(missing_ok=True)
        STOP_FILE.unlink(missing_ok=True)
        log.info("Capture arrêtée.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
