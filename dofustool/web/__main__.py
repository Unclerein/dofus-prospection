"""Lance l'interface web locale.

Usage : python -m dofustool.web [--port 8600] [--no-browser]
"""
import argparse
import logging
import sys
import threading
import webbrowser

from .. import config
from ..messages import runtime_keymap_path
from ..share import hub as share_hub
from ..share.client import Syncer
from .server import DEFAULT_PORT, serve


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        server = serve(args.port)
    except OSError as exc:
        print(f"Impossible d'écouter sur le port {args.port} : {exc}. L'interface tourne peut-être déjà.")
        return 1
    # Partage : le hub (si ce PC l'héberge) et la synchronisation tournent avec l'interface.
    cfg = config.load()
    if cfg.share_host:
        try:
            hub_server = share_hub.serve(cfg.share_port)
            threading.Thread(target=hub_server.serve_forever, daemon=True).start()
            print(f"Hub de partage : port {cfg.share_port}")
        except OSError as exc:
            print(f"Hub de partage non démarré (port {cfg.share_port}) : {exc}")
    Syncer(keymap_path=runtime_keymap_path()).start()
    url = f"http://localhost:{args.port}"
    print(f"Prospection : {url}  (Ctrl+C pour arrêter)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
