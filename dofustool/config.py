"""Lecture et écriture de config.toml."""
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"

MAX_JOB_LEVEL = 200


@dataclass(frozen=True, slots=True)
class Config:
    server_name: str = ""
    character_id: int | None = None
    jobs: dict[str, int] = field(default_factory=dict)
    hdv_tax: float = 0.02
    last_sale_max_age_hours: float = 24.0
    min_snapshots_for_trend: int = 5
    min_liquidity: int = 0
    trend_threshold: float = 0.15
    use_estimated_prices: bool = True
    equipment_price: str = "both"  # prix de vente d'un équipement : both, base, any ou avg (voir analysis.prices)
    iface: str | None = None
    avg_prices_timeout_s: float = 60.0
    ankama_path: str = r"C:\Program Files\Ankama\Ankama Launcher\Ankama Launcher.exe"
    dofus_process: str = "Dofus"
    start_dashboard: bool = True
    # Partage des relevés de marché avec quelques amis (voir dofustool/share).
    share_pseudo: str = ""
    share_hub_url: str = ""
    share_token: str = ""
    share_host: bool = False  # ce PC héberge le hub
    share_port: int = 8610
    share_members: dict[str, str] = field(default_factory=dict)  # pseudo -> jeton, côté hub
    onboarded: bool = False  # le tutoriel de premier démarrage a été vu


def load(path: Path = CONFIG_PATH) -> Config:
    """Charge la configuration ; toute valeur absente garde son défaut."""
    # utf-8-sig : le Bloc-notes de Windows peut ajouter un BOM à l'enregistrement.
    return _from_raw(tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {})


def _from_raw(raw: dict) -> Config:
    market, capture, launcher, share = (raw.get(s, {}) for s in ("market", "capture", "launcher", "share"))
    defaults = Config()
    return Config(
        server_name=raw.get("server", {}).get("name", defaults.server_name),
        character_id=int(raw.get("character", {}).get("id", 0)) or None,
        jobs={str(k): int(v) for k, v in raw.get("jobs", {}).items()},
        hdv_tax=float(market.get("hdv_tax", defaults.hdv_tax)),
        last_sale_max_age_hours=float(market.get("last_sale_max_age_hours", defaults.last_sale_max_age_hours)),
        min_snapshots_for_trend=int(market.get("min_snapshots_for_trend", defaults.min_snapshots_for_trend)),
        min_liquidity=int(market.get("min_liquidity", defaults.min_liquidity)),
        trend_threshold=float(market.get("trend_threshold", defaults.trend_threshold)),
        use_estimated_prices=bool(market.get("use_estimated_prices", defaults.use_estimated_prices)),
        equipment_price=str(market.get("equipment_price", defaults.equipment_price)),
        iface=capture.get("iface") or None,
        avg_prices_timeout_s=float(capture.get("avg_prices_timeout_s", defaults.avg_prices_timeout_s)),
        ankama_path=launcher.get("ankama_path", defaults.ankama_path),
        dofus_process=launcher.get("dofus_process", defaults.dofus_process),
        start_dashboard=bool(launcher.get("start_dashboard", defaults.start_dashboard)),
        share_pseudo=str(share.get("pseudo", "")),
        share_hub_url=str(share.get("hub_url", "")),
        share_token=str(share.get("token", "")),
        share_host=bool(share.get("host", False)),
        share_port=int(share.get("port", defaults.share_port)),
        share_members={str(k): str(v) for k, v in share.get("members", {}).items()},
        onboarded=bool(raw.get("app", {}).get("onboarded", False)),
    )


def _text(values: dict, key: str, label: str, max_length: int = 260) -> str:
    value = values.get(key) or ""
    if not isinstance(value, str) or len(value) > max_length or not value.isprintable():
        raise ValueError(f"{label} : texte invalide")
    return value.strip()


def _number(values: dict, key: str, label: str, low: float, high: float, integer: bool = False) -> float:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise ValueError(f"{label} : nombre attendu")
    if integer and value != int(value):
        raise ValueError(f"{label} : nombre entier attendu")
    if not low <= value <= high:
        raise ValueError(f"{label} : doit être entre {low:g} et {high:g}")
    return int(value) if integer else float(value)


def _choice(value, allowed: tuple[str, ...], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"{label} : valeur inconnue")
    return value


def from_values(values: dict) -> Config:
    """Construit une configuration à partir de valeurs saisies ; ValueError (en français) si l'une est invalide."""
    jobs_in = values.get("jobs", {})
    if not isinstance(jobs_in, dict):
        raise ValueError("Métiers : liste invalide")
    jobs = {}
    for name, level in jobs_in.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 40 or not name.isprintable():
            raise ValueError("Métiers : nom invalide")
        jobs[name.strip()] = _number({"v": level}, "v", f"Niveau de {name}", 1, MAX_JOB_LEVEL, integer=True)
    members_in = values.get("share_members", {})
    if not isinstance(members_in, dict) or len(members_in) > 20:
        raise ValueError("Partage : liste d'amis invalide")
    members = {}
    for pseudo, token in members_in.items():
        ok = all(isinstance(x, str) and x.strip() and x.isprintable() and '"' not in x for x in (pseudo, token))
        if not ok or len(pseudo) > 30 or not 16 <= len(token) <= 100 or " " in token:
            raise ValueError("Partage : pseudo ou jeton invalide (un jeton fait au moins 16 caractères, sans espace)")
        members[pseudo.strip()] = token
    hub_url = _text(values, "share_hub_url", "Adresse du hub", 200).rstrip("/")
    if hub_url and not hub_url.startswith(("http://", "https://")):
        raise ValueError("Adresse du hub : elle doit commencer par http:// ou https://")
    token = _text(values, "share_token", "Jeton de partage", 100)
    if " " in token:
        raise ValueError("Jeton de partage : pas d'espace")
    character = values.get("character_id") or 0
    if isinstance(character, bool) or not isinstance(character, int) or character < 0:
        raise ValueError("Personnage : identifiant invalide")
    return Config(
        server_name=_text(values, "server_name", "Serveur", 60),
        character_id=character or None,
        jobs=jobs,
        hdv_tax=_number(values, "hdv_tax", "Taxe HDV", 0, 0.5),
        last_sale_max_age_hours=_number(values, "last_sale_max_age_hours", "Âge maximal de la dernière vente", 0.1, 8760),
        min_snapshots_for_trend=_number(values, "min_snapshots_for_trend", "Relevés pour une tendance", 1, 1000, integer=True),
        min_liquidity=_number(values, "min_liquidity", "Liquidité minimale", 0, 10**9, integer=True),
        trend_threshold=_number(values, "trend_threshold", "Seuil de tendance", 0.01, 5),
        use_estimated_prices=bool(values.get("use_estimated_prices", True)),
        equipment_price=_choice(values.get("equipment_price", "both"), ("both", "base", "any", "avg"), "Prix des équipements"),
        iface=_text(values, "iface", "Interface réseau") or None,
        avg_prices_timeout_s=_number(values, "avg_prices_timeout_s", "Délai d'alerte", 5, 3600),
        ankama_path=_text(values, "ankama_path", "Chemin du launcher"),
        dofus_process=_text(values, "dofus_process", "Processus du jeu", 60),
        start_dashboard=bool(values.get("start_dashboard")),
        share_pseudo=_text(values, "share_pseudo", "Pseudo", 30),
        share_hub_url=hub_url,
        share_token=token,
        share_host=bool(values.get("share_host", False)),
        share_port=_number({"v": values.get("share_port", 8610)}, "v", "Port du hub", 1024, 65535, integer=True),
        share_members=members,
        onboarded=bool(values.get("onboarded", False)),
    )


def _quoted(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)  # une chaîne JSON est une chaîne TOML valide


def _plain(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else repr(float(number))


def render(cfg: Config) -> str:
    """Texte de config.toml pour cette configuration, commentaires compris."""
    jobs = "".join(f"{_quoted(name)} = {level}\n" for name, level in cfg.jobs.items())
    members = "".join(f"{_quoted(pseudo)} = {_quoted(token)}\n" for pseudo, token in cfg.share_members.items())
    return f"""# Paramètres de Prospection. Modifiables ici ou dans l'onglet Config de l'interface,
# qui réécrit ce fichier en entier.

[server]
# Nom de ton serveur de jeu (informatif : un seul serveur est géré).
name = {_quoted(cfg.server_name)}

[character]
# Personnage choisi dans l'onglet Config (identifiant numérique du jeu ; 0 = aucun).
id = {cfg.character_id or 0}

[jobs]
# Tes métiers et leur niveau (facultatif). Ils ne limitent pas le classement : un craft peut être
# confié à un autre joueur. Ils servent à repérer et filtrer ce que tu peux faire toi-même.
{jobs}
[market]
# Taxe de mise en vente en HDV, en proportion du prix (0.02 = 2 %). À vérifier en jeu.
hdv_tax = {repr(cfg.hdv_tax)}
# Au-delà de cet âge, le dernier prix de vente n'est plus utilisé : on retombe sur le prix moyen.
last_sale_max_age_hours = {_plain(cfg.last_sale_max_age_hours)}
# Nombre minimal de relevés avant de calculer une tendance sur les prix moyens.
min_snapshots_for_trend = {cfg.min_snapshots_for_trend}
# Quantité vendue minimale sur 7 jours pour qu'un objet apparaisse par défaut.
min_liquidity = {cfg.min_liquidity}
# Écart à la tendance à partir duquel un objet est signalé sous-coté ou sur-coté (0.15 = 15 %).
trend_threshold = {repr(cfg.trend_threshold)}
# Sans annonce HDV ni vente récente, utiliser le prix estimé (jugé fiable) plutôt que le prix moyen.
use_estimated_prices = {"true" if cfg.use_estimated_prices else "false"}
# Prix de vente d'un équipement face à son coût de craft : "base" = annonce HDV jet de base (ni exo,
# ni over, ni ligne perdue), "any" = annonce la moins chère, exo et over compris (deux lignes perdues
# au plus), "avg" = prix moyen du jeu, "both" = jet de base, sinon prix moyen.
equipment_price = {_quoted(cfg.equipment_price)}

[capture]
# Interface réseau à écouter. Vide = interface par défaut.
# Liste : python -m dofustool.tools.identify --list-ifaces
iface = {_quoted(cfg.iface or "")}
# Délai (s) après la connexion au-delà duquel l'absence de prix moyens déclenche une alerte.
avg_prices_timeout_s = {_plain(cfg.avg_prices_timeout_s)}

[launcher]
ankama_path = {_quoted(cfg.ankama_path)}
# Nom du processus du jeu, sans « .exe ».
dofus_process = {_quoted(cfg.dofus_process)}
# Ouvrir l'interface au lancement.
start_dashboard = {"true" if cfg.start_dashboard else "false"}

[app]
# Le tutoriel de premier démarrage a été vu.
onboarded = {"true" if cfg.onboarded else "false"}

[share]
# Partage des relevés de marché avec quelques amis du même serveur. Les jetons sont des secrets.
pseudo = {_quoted(cfg.share_pseudo)}
# Adresse du hub (par exemple http://100.101.102.103:8610) et jeton personnel reçu de celui qui l'héberge.
hub_url = {_quoted(cfg.share_hub_url)}
token = {_quoted(cfg.share_token)}
# Ce PC héberge le hub : il écoute sur ce port, joignable depuis les autres PC.
host = {"true" if cfg.share_host else "false"}
port = {cfg.share_port}

[share.members]
# Côté hub : un jeton par ami autorisé (pseudo = jeton).
{members}"""


def save(cfg: Config, path: Path = CONFIG_PATH) -> None:
    """Écrit config.toml. Le fichier est remplacé d'un coup : jamais à moitié écrit."""
    text = render(cfg)
    if load_text(text) != cfg:  # garde-fou : ne jamais écrire un fichier qui ne se relit pas à l'identique
        raise ValueError("configuration impossible à enregistrer telle quelle")
    temp = path.with_suffix(".toml.tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)


def load_text(text: str) -> Config:
    """Comme load, depuis un texte TOML."""
    return _from_raw(tomllib.loads(text))
