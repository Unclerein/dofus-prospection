# Héberger le hub de partage sur un serveur loué

Le hub reçoit les relevés de marché de chaque joueur et les redistribue aux autres. Sur un petit
serveur loué, il est disponible en permanence et personne n'a rien d'autre à installer : chaque
joueur saisit une adresse `https://…` et son jeton, c'est tout.

Compter une heure la première fois. Trois choses à obtenir : un serveur, un nom de domaine
(gratuit), et l'archive du hub.

## 1. Louer le serveur

Le hub est minuscule : la plus petite offre de n'importe quel hébergeur suffit largement (1 cœur,
1 Go de mémoire). Ce qui compte : **Ubuntu 24.04**, une adresse IPv4, et un tarif sans mauvaise
surprise.

| Offre | Prix relevé le 6 octobre 2026 | Remarques |
|---|---|---|
| **IONOS VPS S+** | 2 € HT/mois pendant 3 mois, puis 5 € HT/mois ; 10 € de mise en service | Site et assistance en français, 30 jours d'essai. Prix vérifié sur ionos.fr. |
| OVHcloud VPS-1 | entre 5,50 et 7,80 € HT/mois selon les sources | Français, sans engagement. Prix à vérifier sur leur site. |
| Hetzner CX23 | entre 4 et 6 € HT/mois selon les sources | Le moins cher à long terme, interface en anglais ; affiché indisponible ce jour-là. |

Les prix bougent souvent : les vérifier au moment de commander. Ajouter 20 % de TVA.

Pour un premier serveur, **IONOS VPS S+** est le plus simple : <https://www.ionos.fr/serveurs/vps>.

À la commande :

- système : **Ubuntu 24.04** ;
- aucune option payante n'est nécessaire (ni Plesk, ni sauvegarde, ni domaine).

Une fois le serveur livré, noter dans l'espace client son **adresse IP** et le **mot de passe
root**. Vérifier aussi que le pare-feu de l'hébergeur laisse passer les ports **22, 80 et 443**
(c'est le réglage par défaut chez IONOS).

## 2. Obtenir un nom de domaine gratuit

Le chiffrement HTTPS exige un nom, pas seulement une adresse IP. DuckDNS en donne un gratuitement.

1. Aller sur <https://www.duckdns.org> et se connecter (compte Google ou GitHub).
2. Choisir un nom, par exemple `mon-hub`, et cliquer **add domain**.
3. Dans le champ **current ip** de ce domaine, saisir l'adresse IP du serveur, puis **update ip**.

Le hub sera joignable à `https://mon-hub.duckdns.org` (avec le nom choisi).

## 3. Fabriquer l'archive du hub

Sur son PC, dans PowerShell :

```powershell
cd C:\dofus-test
```

```powershell
.\.venv\Scripts\python.exe -m dofustool.share.bundle
```

Le fichier `data\prospection-hub.zip` est créé (40 Ko). Il ne contient que le code du hub : aucune
donnée, aucun réglage personnel.

## 4. Envoyer l'archive et installer

Toujours dans PowerShell, en remplaçant `1.2.3.4` par l'adresse IP du serveur. À la première
connexion, répondre `yes` à la question sur l'empreinte, puis saisir le mot de passe root.

```powershell
scp C:\dofus-test\data\prospection-hub.zip root@1.2.3.4:/root/
```

```powershell
ssh root@1.2.3.4
```

On est maintenant sur le serveur. Y lancer, une ligne après l'autre, en mettant son propre nom de
domaine :

```bash
apt-get update && apt-get install -y unzip
```

```bash
unzip -o prospection-hub.zip -d hub
```

```bash
bash hub/setup.sh mon-hub.duckdns.org
```

Le script installe Python et Caddy (qui gère le HTTPS), place le hub dans `/opt/prospection`, le
lance comme un service qui redémarre tout seul, et ferme tous les ports sauf SSH, HTTP et HTTPS.
Il se termine par « Le hub tourne ».

## 5. Déclarer le serveur de jeu et les joueurs

Toujours sur le serveur :

```bash
prospection-hub --server Kourial
```

Puis un jeton par joueur, **soi-même compris** :

```bash
prospection-hub --add MonPseudo
```

La commande affiche le jeton une seule fois : le copier tout de suite. Un jeton est un mot de
passe ; l'envoyer à chaque ami en privé, jamais dans un salon public.

Pour vérifier que tout répond, depuis le serveur :

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://mon-hub.duckdns.org/
```

La réponse attendue est `404` : le hub est joignable en HTTPS et ne répond qu'aux applis. (Une
erreur de certificat dans la première minute est normale : Caddy est en train de l'obtenir.)

Taper `exit` pour quitter le serveur.

## 6. Brancher chaque appli

Chez chaque joueur, dans Prospection, onglet **Config**, section « Partage avec des amis » :

- **Mon pseudo** : celui déclaré sur le hub ;
- **Adresse du hub** : `https://mon-hub.duckdns.org` ;
- **Mon jeton** : celui reçu ;
- laisser **« Ce PC héberge le hub »** décoché ;
- **Enregistrer**.

Dans la minute, l'onglet **État** affiche « Synchronisé à l'instant » et la liste des joueurs.

## Au quotidien

Tout se fait sur le serveur, après `ssh root@1.2.3.4`.

| Besoin | Commande |
|---|---|
| Voir les joueurs et leur dernier passage | `prospection-hub --list` |
| Ajouter un joueur | `prospection-hub --add SonPseudo` |
| Retirer un joueur (son jeton cesse de marcher aussitôt) | `prospection-hub --remove SonPseudo` |
| Retirer aussi ce qu'il avait envoyé au hub | `prospection-hub --purge SonPseudo` |
| Suivre l'activité en direct | `journalctl -u prospection-hub -f` (Ctrl+C pour sortir) |
| Le hub tourne-t-il ? | `systemctl status prospection-hub` |

**Mettre le hub à jour** après une nouvelle version de l'outil : refaire les étapes 3 et 4
(archive, `scp`, `unzip -o`, `bash hub/setup.sh …`). La liste des joueurs et les relevés déjà reçus
sont conservés.

**Changer de serveur** : installer le hub sur le nouveau, recréer les jetons, puis chaque joueur
change l'adresse dans Config. Chaque appli renvoie d'elle-même ses relevés récents.

## Dépôt GitHub privé

Les mises à jour automatiques font un `git pull` sur GitHub. Si le dépôt est privé, le hub donne
lui-même l'accès aux joueurs qu'il connaît : rien à faire de leur côté.

1. **Mettre le hub à jour** (étapes 3 et 4) avec une version qui connaît `--repo-token`.
2. **Créer le jeton sur GitHub** : *Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token*. Accès au seul dépôt `dofus-prospection`, permission
   **Contents : Read-only**, rien d'autre. Choisir la durée la plus longue proposée.
3. **Le donner au hub** : `prospection-hub --repo-token`, puis coller le jeton (rien ne s'affiche
   pendant la saisie).
4. **Passer le dépôt en privé** sur GitHub.

À la mise à jour suivante, chaque appli voit que le dépôt ne répond plus, demande l'accès au hub avec
son jeton de partage, l'inscrit, et la mise à jour passe. Le PC du propriétaire, déjà identifié sur
GitHub, n'est pas touché.

| Besoin | Que faire |
|---|---|
| Ajouter un joueur | `prospection-hub --add SonPseudo` affiche aussi la ligne `git clone` à lui donner pour l'installation |
| Renouveler le jeton GitHub (expiré, ou un joueur retiré) | en créer un nouveau, `prospection-hub --repo-token`, puis révoquer l'ancien : les applis prennent le nouveau d'elles-mêmes |
| Ne plus donner d'accès | `prospection-hub --repo-token`, puis Entrée sans rien coller |

Le jeton GitHub finit dans le dossier de Prospection de chaque joueur (dans `.git\config`). Il ne
permet que de lire le code. Un joueur retiré du hub garde celui qu'il a déjà : le renouveler si
besoin.

## En cas de problème

| Ce qui se passe | Quoi faire |
|---|---|
| `ssh` ou `scp` : « Connection timed out » | Mauvaise adresse IP, ou le pare-feu de l'hébergeur bloque le port 22. |
| `setup.sh` : « Python 3.12 ou plus récent est nécessaire » | Le serveur n'est pas en Ubuntu 24.04 : le réinstaller avec ce système depuis l'espace client. |
| L'appli affiche « hub injoignable » | Vérifier l'adresse (bien `https://`), que DuckDNS pointe sur la bonne IP, et que les ports 80 et 443 sont ouverts chez l'hébergeur. |
| Erreur de certificat qui dure | `journalctl -u caddy -n 30` : le plus souvent, le nom DuckDNS ne pointe pas encore sur le serveur. |
| L'appli affiche « jeton inconnu » | Jeton mal copié : `prospection-hub --remove Pseudo` puis `--add Pseudo`, et renvoyer le nouveau. |
| « ce hub partage le serveur Kourial, pas … » | Le joueur a un autre nom de serveur dans son onglet Config. |

## Ce que le serveur contient

Des relevés de marché : annonces HDV (3 jours), cours du marché (60 jours), prix moyens (3 jours),
et la table des clés du build. Et tous les passages de forgemagie de chacun, gardés sans limite de
durée pour l'étude des chances : l'objet avant et après, ce qui a été posé, le puits ; l'exemplaire
n'y est qu'une empreinte et l'heure est arrondie à la minute. Enfin, le score de collecte de chacun
pour le classement (points, passages, kamas investis, missions réussies) avec le nom du personnage
qui forge. Ni chat, ni stock, ni ventes, ni prix payés, ni les autres personnages. Les données
nominatives sont les pseudos choisis, le nom du personnage qui forge et la date du dernier passage de
chacun.

Un hub d'avant le partage de la forgemagie (ou d'avant le classement) refuse ces passages ou ces
scores : les applis les gardent et les renvoient d'elles-mêmes une fois le hub mis à jour.
