#!/usr/bin/env bash
# Installe (ou met à jour) le hub de partage de Prospection sur un serveur Ubuntu 24.04.
# À lancer en root depuis le dossier où l'archive a été décompressée :
#
#     bash setup.sh mon-hub.duckdns.org
#
# Sans danger à relancer : la liste des joueurs et les relevés déjà reçus sont conservés.
set -euo pipefail

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
    echo "Usage : bash setup.sh <nom-de-domaine>   (exemple : bash setup.sh mon-hub.duckdns.org)"
    exit 1
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "Ce script doit être lancé en root."
    exit 1
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/prospection

echo "== 1/5  Paquets système (Python, Caddy, pare-feu)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 caddy ufw > /dev/null
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    echo "Python 3.12 ou plus récent est nécessaire (Ubuntu 24.04 le fournit). Version trouvée : $(python3 --version)"
    exit 1
fi

echo "== 2/5  Code du hub dans $APP"
id prospection > /dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin prospection
mkdir -p "$APP/data"
rm -rf "$APP/dofustool.new"
cp -r "$HERE/dofustool" "$APP/dofustool.new"
# Les réglages du hub (liste des joueurs, serveur de jeu) survivent à une mise à jour du code.
if [ -f "$APP/dofustool/config.toml" ]; then cp "$APP/dofustool/config.toml" "$APP/dofustool.new/config.toml"; fi
rm -rf "$APP/dofustool"
mv "$APP/dofustool.new" "$APP/dofustool"
chown -R prospection:prospection "$APP"
chmod 700 "$APP/data"
if [ -f "$APP/dofustool/config.toml" ]; then chmod 600 "$APP/dofustool/config.toml"; fi

echo "== 3/5  Commande « prospection-hub » et service"
cat > /usr/local/bin/prospection-hub <<'EOF'
#!/usr/bin/env bash
# Gestion du hub : prospection-hub --add Pseudo | --remove Pseudo | --list | --server Nom | --purge Pseudo
cd /opt/prospection
exec runuser -u prospection -- python3 -m dofustool.share.hub "$@"
EOF
chmod 755 /usr/local/bin/prospection-hub
cp "$HERE/prospection-hub.service" /etc/systemd/system/prospection-hub.service
systemctl daemon-reload
systemctl enable --quiet prospection-hub
systemctl restart prospection-hub

echo "== 4/5  HTTPS pour $DOMAIN (Caddy obtient le certificat tout seul)"
sed "s/__DOMAIN__/$DOMAIN/" "$HERE/Caddyfile" > /etc/caddy/Caddyfile
systemctl enable --quiet caddy
systemctl restart caddy

echo "== 5/5  Pare-feu : SSH, HTTP et HTTPS seulement"
ufw allow OpenSSH > /dev/null
ufw allow 80/tcp > /dev/null
ufw allow 443/tcp > /dev/null
ufw --force enable > /dev/null

sleep 3
echo
if systemctl is-active --quiet prospection-hub; then
    echo "Le hub tourne. Adresse à donner à tes amis : https://$DOMAIN"
else
    echo "Le hub n'a pas démarré. Regarde : journalctl -u prospection-hub -n 30"
    exit 1
fi
echo "Étapes suivantes :"
echo "  prospection-hub --server Kourial     (le serveur de jeu que vous partagez)"
echo "  prospection-hub --add TonPseudo      (un jeton par joueur, toi compris)"
