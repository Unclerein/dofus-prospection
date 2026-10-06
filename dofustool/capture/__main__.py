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
from ..archive import Archive
from ..messages import KEYMAP_PATH, load_keymap
from . import GAME_PORT
from .pipeline import Pipeline, log

DATA_DIR = db.MARKET_PATH.parent
READY_FILE = DATA_DIR / "capture.ready"
STOP_FILE = DATA_DIR / "capture.stop"
LOG_FILE = DATA_DIR / "capture.log"


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
    from .live_source import LiveSource  # import tardif : scapy est lent à charger

    STOP_FILE.unlink(missing_ok=True)
    market = db.connect()
    archive = Archive()
    pipeline = Pipeline(archive, market, load_keymap(), cfg.avg_prices_timeout_s, KEYMAP_PATH)
    source = LiveSource(iface=args.iface or cfg.iface)
    try:
        source.start()
    except Exception as exc:
        log.error("Impossible de démarrer l'écoute sur « %s » : %s", source.iface, exc)
        return 1
    db.set_status(market, started_ts=time.time(), stopped_ts="", decode_alert="")
    READY_FILE.write_text(str(time.time()), encoding="utf-8")
    log.info("Capture démarrée (écoute passive, tcp port %d, interface « %s »).", GAME_PORT, source.iface)
    try:
        while not STOP_FILE.exists():
            segment = source.poll(1.0)
            while segment is not None:
                pipeline.handle(segment)
                segment = source.poll(0)
            pipeline.tick(time.time())
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
