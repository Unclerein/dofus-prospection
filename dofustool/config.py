"""Lecture de config.toml."""
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"


@dataclass(frozen=True, slots=True)
class Config:
    server_name: str = ""
    jobs: dict[str, int] = field(default_factory=dict)
    hdv_tax: float = 0.02
    last_sale_max_age_hours: float = 24.0
    min_snapshots_for_trend: int = 5
    min_liquidity: int = 0
    trend_threshold: float = 0.15
    iface: str | None = None
    avg_prices_timeout_s: float = 60.0
    ankama_path: str = ""
    dofus_process: str = "Dofus"
    start_dashboard: bool = False


def load(path: Path = CONFIG_PATH) -> Config:
    """Charge la configuration ; toute valeur absente garde son défaut."""
    # utf-8-sig : le Bloc-notes de Windows peut ajouter un BOM à l'enregistrement.
    raw = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    market, capture, launcher = (raw.get(s, {}) for s in ("market", "capture", "launcher"))
    defaults = Config()
    return Config(
        server_name=raw.get("server", {}).get("name", defaults.server_name),
        jobs={str(k): int(v) for k, v in raw.get("jobs", {}).items()},
        hdv_tax=float(market.get("hdv_tax", defaults.hdv_tax)),
        last_sale_max_age_hours=float(market.get("last_sale_max_age_hours", defaults.last_sale_max_age_hours)),
        min_snapshots_for_trend=int(market.get("min_snapshots_for_trend", defaults.min_snapshots_for_trend)),
        min_liquidity=int(market.get("min_liquidity", defaults.min_liquidity)),
        trend_threshold=float(market.get("trend_threshold", defaults.trend_threshold)),
        iface=capture.get("iface") or None,
        avg_prices_timeout_s=float(capture.get("avg_prices_timeout_s", defaults.avg_prices_timeout_s)),
        ankama_path=launcher.get("ankama_path", defaults.ankama_path),
        dofus_process=launcher.get("dofus_process", defaults.dofus_process),
        start_dashboard=bool(launcher.get("start_dashboard", defaults.start_dashboard)),
    )
