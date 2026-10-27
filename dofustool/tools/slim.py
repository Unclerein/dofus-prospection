"""Allège les données locales, après la partie (le lanceur l'appelle une fois la capture arrêtée).

  - archive : messages de plus de KEEP_ALL_DAYS jours supprimés, les autres compressés (une seule fois :
    les nouveaux le sont dès leur arrivée), puis la place rendue au disque ;
  - données du jeu : seuls les textes français sont gardés (l'outil n'en lit pas d'autres) ;
  - base : relevés de prix moyens allégés au-delà d'un mois, place rendue au disque.

Rien n'est fait sur une base occupée (capture lancée, interface en train d'écrire) : ce sera pour la
prochaine fois. Rapide quand il n'y a rien à faire.

Usage : python -m dofustool.tools.slim
"""
import sqlite3
import sys
import time
from pathlib import Path

from .. import db
from ..archive import ARCHIVE_PATH, KEEP_ALL_DAYS, PACK_FROM, PACKED, pack
from ..staticdata.source import DOFUS_SQLITE

LANG = "fr"
BATCH = 5000
WORTH = 32 * 1024 * 1024  # place libre à partir de laquelle on la rend au disque


def _megabytes(path: Path) -> int:
    return round(path.stat().st_size / 1e6) if path.exists() else 0


def _free(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA freelist_count").fetchone()[0] * conn.execute("PRAGMA page_size").fetchone()[0]


def _idle(conn: sqlite3.Connection) -> bool:
    """Personne d'autre n'écrit dans cette base en ce moment."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        return True
    except sqlite3.OperationalError:
        return False


def _vacuum(conn: sqlite3.Connection, path: Path, force: bool = False) -> int:
    """Rend la place libre au disque s'il y en a assez ; renvoie les Mo gagnés."""
    if not force and _free(conn) < WORTH:
        return 0
    before = _megabytes(path)
    conn.execute("VACUUM")
    return before - _megabytes(path)


def slim_archive(path: Path = ARCHIVE_PATH, now: float | None = None) -> str | None:
    if not path.exists():
        return None
    now = time.time() if now is None else now
    conn = sqlite3.connect(path, timeout=2.0)
    try:
        if not _idle(conn):
            return "Archive en cours d'écriture : allègement remis à la prochaine partie."
        conn.execute("CREATE TABLE IF NOT EXISTS slim (packed_up_to INTEGER NOT NULL)")
        removed = conn.execute("DELETE FROM messages WHERE ts < ?", (now - KEEP_ALL_DAYS * 86400,)).rowcount
        conn.commit()
        start = (conn.execute("SELECT MAX(packed_up_to) FROM slim").fetchone()[0] or 0)
        top = conn.execute("SELECT MAX(id) FROM messages").fetchone()[0] or 0
        packed, last = 0, start
        while last < top:
            rows = conn.execute(
                "SELECT id, body FROM messages WHERE id > ? AND id <= ? ORDER BY id LIMIT ?", (last, top, BATCH)
            ).fetchall()
            if not rows:
                break
            last = rows[-1][0]
            updates = []
            for message_id, body in rows:
                body = bytes(body)
                if len(body) >= PACK_FROM and not body.startswith(PACKED) and (smaller := pack(body)) is not body:
                    updates.append((smaller, message_id))
            conn.executemany("UPDATE messages SET body = ? WHERE id = ?", updates)
            conn.execute("DELETE FROM slim")
            conn.execute("INSERT INTO slim VALUES (?)", (last,))
            conn.commit()
            packed += len(updates)
        gained = _vacuum(conn, path, force=packed > 0)
    finally:
        conn.close()
    if not (removed or packed or gained):
        return None
    return f"Archive allégée : {removed} messages de plus de {KEEP_ALL_DAYS} jours supprimés, {packed} compressés, {gained} Mo rendus ({_megabytes(path)} Mo)."


def slim_static(path: Path = DOFUS_SQLITE) -> str | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(path, timeout=2.0)
    try:
        try:
            other = conn.execute("SELECT COUNT(*) FROM translations WHERE lang != ?", (LANG,)).fetchone()[0]
        except sqlite3.Error:
            return None
        if not other or not _idle(conn):
            return None
        with conn:
            conn.execute("DELETE FROM translations WHERE lang != ?", (LANG,))
        gained = _vacuum(conn, path, force=True)
    finally:
        conn.close()
    return f"Données du jeu : textes des autres langues retirés, {gained} Mo rendus ({_megabytes(path)} Mo)."


def slim_market(path: Path = db.MARKET_PATH, now: float | None = None) -> str | None:
    if not path.exists():
        return None
    conn = db.connect(path)
    try:
        thinned = db.thin_snapshots(conn, time.time() if now is None else now)
        try:
            gained = _vacuum(conn, path)
        except sqlite3.OperationalError:
            return None  # l'interface lit la base en ce moment : la place sera rendue la prochaine fois
    finally:
        conn.close()
    if not (thinned or gained):
        return None
    return f"Base allégée : {thinned} relevés de prix moyens anciens retirés, {gained} Mo rendus ({_megabytes(path)} Mo)."


def main() -> int:
    for step in (slim_archive, slim_static, slim_market):
        try:
            note = step()
        except (sqlite3.Error, OSError) as exc:
            note = f"{step.__name__} non fait : {exc}"
        if note:
            print(note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
