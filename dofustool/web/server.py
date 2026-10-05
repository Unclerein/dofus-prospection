"""Serveur HTTP local de l'interface. Bibliothèque standard uniquement.

N'écoute que sur 127.0.0.1 : l'interface n'est pas accessible depuis le réseau.
"""
import json
import logging
import mimetypes
import re
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import db
from .api import Api

log = logging.getLogger("dofustool.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
ICON_DIR = db.MARKET_PATH.parent / "icons"
ICON_URL = "https://api.dofusdb.fr/img/items/{icon_id}.png"
DEFAULT_PORT = 8600


def fetch_icon(icon_id: int, directory: Path = ICON_DIR) -> bytes | None:
    """Image d'un item : lue dans le cache local, sinon téléchargée une fois sur DofusDB."""
    path = directory / f"{icon_id}.png"
    if path.exists():
        return path.read_bytes()
    try:
        request = urllib.request.Request(ICON_URL.format(icon_id=icon_id), headers={"User-Agent": "dofustool"})
        with urllib.request.urlopen(request, timeout=10) as response:
            content = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    if not content.startswith(b"\x89PNG"):
        return None
    directory.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def make_handler(api: Api, icon_dir: Path = ICON_DIR) -> type[BaseHTTPRequestHandler]:
    def to_int(text: str | None) -> int | None:
        return int(text) if text and text.lstrip("-").isdigit() else None

    class Handler(BaseHTTPRequestHandler):
        server_version = "dofustool"

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # pas de journal par requête

        def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _not_found(self) -> None:
            self._json({"error": "introuvable"}, HTTPStatus.NOT_FOUND)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            path = url.path
            try:
                if path == "/api/version":
                    return self._json(api.version())
                if path == "/api/status":
                    return self._json(api.status())
                if path == "/api/crafts":
                    return self._json(api.crafts())
                if path == "/api/trends":
                    return self._json(api.trends())
                if path == "/api/items":
                    return self._json(api.items())
                if match := re.fullmatch(r"/api/item/(\d+)", path):
                    payload = api.item(int(match[1]))
                    return self._json(payload) if payload else self._not_found()
                if path == "/api/forge/options":
                    return self._json(api.forge_options())
                if match := re.fullmatch(r"/api/forge/item/(\d+)", path):
                    payload = api.forge_item(int(match[1]))
                    return self._json(payload) if payload else self._not_found()
                if path == "/api/forge/ranking":
                    return self._json(
                        api.forge_ranking(
                            query.get("criterion", "saved"),
                            to_int(query.get("exo")),
                            to_int(query.get("effect")),
                            to_int(query.get("amount")),
                        )
                    )
                if match := re.fullmatch(r"/icons/(\d+)\.png", path):
                    content = fetch_icon(int(match[1]), icon_dir)
                    if content is None:
                        return self._not_found()
                    return self._send(HTTPStatus.OK, content, "image/png", "max-age=604800")
                return self._static(path)
            except Exception:  # une erreur de calcul ne doit pas faire tomber le serveur
                log.exception("Erreur sur %s", path)
                self._json({"error": "erreur interne, voir la console du serveur"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if match := re.fullmatch(r"/api/forge/item/(\d+)/filter", path):
                    length = min(int(self.headers.get("Content-Length") or 0), 65536)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(payload, dict):
                        raise ValueError("objet JSON attendu")
                    return self._json(api.save_forge_filter(int(match[1]), payload))
                self._not_found()
            except (ValueError, TypeError) as exc:
                self._json({"error": f"requête invalide : {exc}"}, HTTPStatus.BAD_REQUEST)
            except Exception:
                log.exception("Erreur sur %s", path)
                self._json({"error": "erreur interne"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (STATIC_DIR / name).resolve()
            if STATIC_DIR not in target.parents or not target.is_file():
                return self._not_found()
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type.endswith(("javascript", "json")):
                content_type += "; charset=utf-8"
            self._send(HTTPStatus.OK, target.read_bytes(), content_type)

    return Handler


def serve(port: int = DEFAULT_PORT, db_path: Path | str = db.MARKET_PATH) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(Api(db_path)))
