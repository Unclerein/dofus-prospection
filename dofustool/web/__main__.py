"""Lance l'interface web locale.

Usage : python -m dofustool.web [--port 8600] [--no-browser]
"""
import argparse
import logging
import sys
import threading
import webbrowser

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
    url = f"http://localhost:{args.port}"
    print(f"Interface dofustool : {url}  (Ctrl+C pour arrêter)")
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
