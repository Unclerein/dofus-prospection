"""Accès au dépôt du code quand il est privé : le hub le donne aux joueurs qu'il connaît.

Avant chaque mise à jour, le lanceur appelle ce module. Si le dépôt répond tel quel (public, ou PC
du propriétaire déjà identifié), rien ne change. Sinon, il demande au hub, avec le jeton de partage,
le jeton GitHub en lecture seule et l'inscrit dans l'adresse du dépôt : la mise à jour qui suit passe.
Quand le jeton GitHub est renouvelé, le même chemin installe le nouveau.

Le jeton n'est jamais affiché. Bibliothèque standard seulement : le hub s'en sert aussi.

Usage : python -m dofustool.share.repo
"""
import gzip
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "github.com/Unclerein/dofus-prospection.git"
ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_S = 15
TOKEN = re.compile(r"^[A-Za-z0-9_]{20,255}$")  # jetons GitHub : ghp_…, github_pat_…


def clone_url(token: str) -> str:
    """Adresse du dépôt avec le jeton : sert au clonage d'un nouveau joueur et aux mises à jour."""
    return f"https://x-access-token:{token}@{REPO}"


def clone_line(token: str, folder: str = r"C:\prospection") -> str:
    return f"git clone {clone_url(token)} {folder}"


def _git(*args: str) -> subprocess.CompletedProcess:
    # Jamais de question à l'écran : sans accès, git doit échouer tout de suite, pas attendre un mot de passe.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_ASKPASS": "echo"}
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=TIMEOUT_S, env=env)


def reachable() -> bool:
    """Le dépôt répond avec l'adresse et les accès actuels de ce PC."""
    try:
        return _git("ls-remote", "--heads", "origin").returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return True  # git absent ou réseau lent : on ne touche à rien


def ask_hub(hub_url: str, share_token: str) -> str | None:
    """Le jeton GitHub que le hub donne aux joueurs, ou None (hub injoignable, jeton refusé, rien de prévu)."""
    request = urllib.request.Request(
        hub_url.rstrip("/") + "/repo", data=b"{}", method="POST",
        headers={"Authorization": f"Bearer {share_token}", "Content-Type": "application/json"},
    )  # fmt: skip
    try:
        context = None
        if hub_url.startswith("https://"):
            from .client import _tls_context

            context = _tls_context()
        with urllib.request.urlopen(request, timeout=TIMEOUT_S, context=context) as response:
            raw = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    try:
        token = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).get("token")
    except (ValueError, AttributeError):
        return None
    return token if isinstance(token, str) and TOKEN.match(token) else None


def refresh(hub_url: str, share_token: str, check=reachable, ask=ask_hub, set_url=None) -> str:
    """Rétablit l'accès au dépôt si besoin. Renvoie ce qui s'est passé, en une phrase, sans le jeton."""
    if check():
        return ""
    if not hub_url or not share_token:
        return "Mise à jour impossible : le dépôt ne répond pas, et le partage n'est pas configuré pour demander l'accès au hub."
    token = ask(hub_url, share_token)
    if token is None:
        return "Mise à jour impossible : le dépôt ne répond pas, et le hub n'a pas donné d'accès."
    (set_url or _set_url)(clone_url(token))
    if not check():
        return "Mise à jour impossible : l'accès donné par le hub est refusé par GitHub (jeton expiré ?)."
    return "Accès aux mises à jour rétabli par le hub."


def _set_url(url: str) -> None:
    _git("remote", "set-url", "origin", url)


def main() -> int:
    from .. import config

    try:
        cfg = config.load()
    except (OSError, ValueError):
        return 0
    message = refresh(cfg.share_hub_url, cfg.share_token)
    if message:
        print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
