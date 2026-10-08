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
EFFECT_ICON_URL = "https://dofusdb.fr/icons/characteristics/{asset}.png"
# Images que DofusDB range sous un autre nom que celui des données du jeu (vérifié le 05/10/2026).
EFFECT_ICON_ALIASES = {
    "tx_strengthRes": "tx_res_strength",
    "tx_agilityRes": "tx_res_agility",
    "tx_chanceRes": "tx_res_chance",
    "tx_intelligenceRes": "tx_res_intelligence",
    "tx_neutralRes": "tx_res_neutral",
    "tx_resMelee": "tx_res_melee",
    "tx_distanceRes": "tx_res_distance",
    "tx_weaponRes": "tx_res_weapon",
    "tx_spellsRes": "tx_res_spell",
    "tx_damageMelee": "tx_meleeDamage",
    "tx_distance": "tx_distanceDamage",
    "tx_spells": "tx_spellDamage",
    "tx_weapon": "tx_weaponDamage",
}
DEFAULT_PORT = 8600


def fetch_effect_icon(asset: str, directory: Path = ICON_DIR) -> bytes | None:
    """Image d'une caractéristique (vitalité, force…), avec le même cache que les images d'items."""
    remote = EFFECT_ICON_ALIASES.get(asset, asset)
    return fetch_icon(asset, directory / "effects", EFFECT_ICON_URL.format(asset=remote))


def fetch_icon(icon_id: int | str, directory: Path = ICON_DIR, url: str | None = None) -> bytes | None:
    """Image d'un item : lue dans le cache local, sinon téléchargée une fois sur DofusDB."""
    path = directory / f"{icon_id}.png"
    if path.exists():
        return path.read_bytes()
    try:
        request = urllib.request.Request(url or ICON_URL.format(icon_id=icon_id), headers={"User-Agent": "dofustool"})
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
                if path == "/api/config":
                    return self._json(api.config())
                if path == "/api/jobs":
                    return self._json(api.jobs())
                if path == "/api/jobs/plan":
                    exclude = frozenset(int(x) for x in query.get("exclude", "").split(",") if x.isdigit())
                    try:
                        bonus = float(query.get("bonus", "100"))
                    except ValueError:
                        bonus = 100.0
                    return self._json(
                        api.job_plan(
                            to_int(query.get("job")) or 0,
                            max(0, to_int(query.get("xp")) or 0),
                            to_int(query.get("target")) or 1,
                            min(1000.0, max(0.0, bonus)),
                            query.get("resale") == "1",
                            exclude,
                        )
                    )
                if path == "/api/ignored":
                    return self._json(api.ignored())
                if path == "/api/workshop":
                    return self._json(api.workshop())
                if path == "/api/today":
                    try:
                        since = float(query["since"]) if "since" in query else None
                    except ValueError:
                        since = None
                    return self._json(api.today(since))
                if path == "/api/almanax":
                    return self._json(api.almanax(query.get("day")))
                if path == "/api/sales":
                    return self._json(api.sales())
                if path == "/api/stock":
                    return self._json(api.stock())
                if path == "/api/stock/crafts":
                    return self._json(api.stock_crafts())
                if path == "/api/items":
                    return self._json(api.items())
                if match := re.fullmatch(r"/api/item/(\d+)/tip", path):
                    payload = api.item_tip(int(match[1]))
                    return self._json(payload) if payload else self._not_found()
                if match := re.fullmatch(r"/api/item/(\d+)", path):
                    payload = api.item(int(match[1]))
                    return self._json(payload) if payload else self._not_found()
                if path == "/api/forge/options":
                    return self._json(api.forge_options())
                if match := re.fullmatch(r"/api/forge/item/(\d+)", path):
                    payload = api.forge_item(int(match[1]))
                    return self._json(payload) if payload else self._not_found()
                if path == "/api/forge/journal":
                    return self._json(api.forge_journal())
                if path == "/api/forge/ranking":
                    return self._json(
                        api.forge_ranking(
                            query.get("criterion", "saved"),
                            to_int(query.get("exo")),
                            to_int(query.get("effect")),
                            to_int(query.get("amount")),
                        )
                    )
                if match := re.fullmatch(r"/icons/effects/([A-Za-z0-9_]{1,64})\.png", path):
                    content = fetch_effect_icon(match[1], icon_dir)
                    if content is None:
                        return self._not_found()
                    return self._send(HTTPStatus.OK, content, "image/png", "max-age=604800")
                if match := re.fullmatch(r"/icons/(\d+)\.png", path):
                    content = fetch_icon(int(match[1]), icon_dir)
                    if content is None:
                        return self._not_found()
                    return self._send(HTTPStatus.OK, content, "image/png", "max-age=604800")
                return self._static(path)
            except Exception:  # une erreur de calcul ne doit pas faire tomber le serveur
                log.exception("Erreur sur %s", path)
                self._json({"error": "erreur interne, voir la console du serveur"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _local_request(self) -> bool:
            """Vrai si la requête vient bien de l'interface elle-même, pas d'une page d'un autre site."""
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            origin = self.headers.get("Origin")
            json_body = (self.headers.get("Content-Type") or "").split(";")[0].strip() == "application/json"
            return (
                host in ("localhost", "127.0.0.1")
                and json_body
                and (origin is None or urlparse(origin).hostname in ("localhost", "127.0.0.1"))
            )

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if not self._local_request():
                # Lire le corps avant de répondre : sinon la connexion est coupée sous les pieds du client.
                self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536))
                return self._json({"error": "requête refusée"}, HTTPStatus.FORBIDDEN)
            try:
                if path == "/api/equipment-price":
                    length = min(int(self.headers.get("Content-Length") or 0), 4096)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(payload, dict) or not isinstance(payload.get("mode"), str):
                        raise ValueError("mode attendu")
                    return self._json(api.set_equipment_price(payload["mode"]))
                if path == "/api/config":
                    length = min(int(self.headers.get("Content-Length") or 0), 65536)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(payload, dict):
                        raise ValueError("objet JSON attendu")
                    return self._json(api.save_config(payload))
                if match := re.fullmatch(r"/api/forge/item/(\d+)/filter", path):
                    length = min(int(self.headers.get("Content-Length") or 0), 65536)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(payload, dict):
                        raise ValueError("objet JSON attendu")
                    return self._json(api.save_forge_filter(int(match[1]), payload))
                if path == "/api/forge/journal/base":
                    length = min(int(self.headers.get("Content-Length") or 0), 4096)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    cost = payload.get("cost") if isinstance(payload, dict) else None
                    if not isinstance(payload, dict) or not isinstance(payload.get("uid"), int):
                        raise ValueError("uid attendu")
                    if cost is not None and (not isinstance(cost, int) or isinstance(cost, bool) or not 0 <= cost <= 10**12):
                        raise ValueError("prix invalide")
                    return self._json(api.set_forge_base_cost(payload["uid"], cost))
                if path == "/api/workshop":
                    length = min(int(self.headers.get("Content-Length") or 0), 65536)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(payload, dict):
                        raise ValueError("objet JSON attendu")
                    return self._json(api.workshop_action(payload))
                if path == "/api/fight/harebourg":
                    length = min(int(self.headers.get("Content-Length") or 0), 16384)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(payload, dict):
                        raise ValueError("objet JSON attendu")
                    return self._json(api.harebourg(payload))
                if path == "/api/ignore-type":
                    length = min(int(self.headers.get("Content-Length") or 0), 4096)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
                        raise ValueError("type attendu")
                    return self._json(api.set_type_ignored(payload["type"], bool(payload.get("ignored", True))))
                if path == "/api/ignore":
                    length = min(int(self.headers.get("Content-Length") or 0), 4096)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(payload, dict) or not isinstance(payload.get("item_id"), int):
                        raise ValueError("item_id attendu")
                    return self._json(api.set_ignored(payload["item_id"], bool(payload.get("ignored", True))))
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
