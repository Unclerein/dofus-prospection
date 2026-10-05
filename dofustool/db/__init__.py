"""Base de travail data/market.sqlite."""
import sqlite3
from pathlib import Path

MARKET_PATH = Path(__file__).resolve().parents[2] / "data" / "market.sqlite"


def connect(path: Path | str = MARKET_PATH) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)
