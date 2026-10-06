# Installer Prospection

Compter un quart d'heure. Il faut Windows 10 ou 11 et le jeu installé par le launcher Ankama.

Les commandes se tapent dans **PowerShell** : menu Démarrer, taper `powershell`, Entrée.

## 1. Installer les trois prérequis

**Git** (pour récupérer l'outil et ses mises à jour) :

```powershell
winget install --id Git.Git -e
```

**Python 3.13** :

```powershell
winget install --id Python.Python.3.13 -e
```

**Npcap** (le pilote qui permet d'écouter ce que le jeu reçoit) : télécharger l'installeur sur
<https://npcap.com/#download>, le lancer, garder les options par défaut. Il demande les droits
administrateur : c'est normal.

Fermer ensuite PowerShell et en rouvrir un, pour que Git et Python soient reconnus.

## 2. Récupérer l'outil

Le dépôt est privé : il faut un compte GitHub (<https://github.com/signup>) et y avoir été
invité. L'invitation arrive par mail ; l'accepter avant de continuer.

```powershell
git clone https://github.com/Unclerein/dofus-prospection.git C:\prospection
```

Une fenêtre de connexion GitHub s'ouvre la première fois. Si rien ne se passe, elle est peut-être
cachée derrière les autres fenêtres (Alt+Tab).

## 3. Lancer l'installation

```powershell
cd C:\prospection
```

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

Le script vérifie Python et Npcap, installe les dépendances, télécharge les données du jeu
(environ 360 Mo) et crée deux raccourcis sur le bureau. S'il s'arrête, il dit pourquoi : corriger,
puis le relancer.

## 4. Premier lancement

1. Double-cliquer **« Dofus + Prospection »** sur le bureau. Il démarre l'écoute, ouvre le launcher
   Ankama et ouvre Prospection dans le navigateur, sur le tutoriel.
2. Lancer le jeu et choisir son personnage, comme d'habitude.
3. Dans le tutoriel, renseigner son serveur, son pseudo, et l'adresse du hub et le jeton si on en a
   reçu (voir « Partager avec des amis »). Les étapes se cochent toutes seules.

Toujours lancer le jeu par ce raccourci : l'écoute doit démarrer avant la connexion. Le second
raccourci, **« Prospection »**, ouvre l'outil seul, sans le jeu.

L'outil se met à jour tout seul à chaque lancement.

---

# Partager avec des amis

Chacun installe l'outil comme ci-dessus. L'un des joueurs héberge le **hub** : son PC reçoit les
relevés de marché de chacun et les redistribue. Seuls les prix circulent ; le stock, les ventes,
les personnages et le chat ne quittent jamais le PC de chacun.

Pour que les PC se joignent à distance sans rien ouvrir sur sa box, on utilise **Tailscale**
(gratuit) : il relie les PC comme s'ils étaient sur le même réseau, de façon chiffrée.

## Celui qui héberge le hub

1. Installer Tailscale et s'y connecter :

   ```powershell
   winget install --id Tailscale.Tailscale -e
   ```

2. Autoriser le hub dans le pare-feu Windows, pour le réseau Tailscale seulement. Dans un
   PowerShell **ouvert en administrateur** (clic droit, « Exécuter en tant qu'administrateur ») :

   ```powershell
   New-NetFirewallRule -DisplayName "Prospection hub" -Direction Inbound -Protocol TCP -LocalPort 8610 -Action Allow -InterfaceAlias "Tailscale"
   ```

3. Dans Prospection, onglet **Config**, section « Partage avec des amis » :
   - cocher **« Ce PC héberge le hub »** ;
   - ajouter chaque ami par son pseudo : un jeton est créé pour lui ;
   - s'ajouter soi-même à la liste, puis coller son propre jeton dans « Mon jeton », avec
     l'adresse `http://127.0.0.1:8610` dans « Adresse du hub » ;
   - **Enregistrer**, puis fermer et relancer Prospection.

4. Partager son PC avec chaque ami dans Tailscale : sur
   <https://login.tailscale.com/admin/machines>, menu « … » de son PC, **Share**, et envoyer le
   lien à l'ami (documentation : <https://tailscale.com/kb/1084/sharing>).

5. Envoyer à chaque ami, en privé : **l'adresse du hub** affichée dans Config (elle commence par
   `http://100.`) et **son jeton**. Un jeton est un mot de passe : un par ami, jamais dans un
   salon public.

Le partage fonctionne tant que ce PC est allumé avec Prospection lancé ; le jeu n'a pas besoin de
tourner. PC éteint, chacun continue à utiliser l'outil de son côté, et les relevés partent au hub
dès qu'il revient.

Pour retirer un ami : le supprimer de la liste dans Config et enregistrer. Son jeton cesse de
fonctionner aussitôt. Pour retirer aussi du hub ce qu'il avait envoyé :

```powershell
.\.venv\Scripts\python.exe -m dofustool.share.hub --purge SonPseudo
```

## Chaque ami

1. Installer Tailscale, s'y connecter avec son propre compte, et accepter le lien de partage reçu :

   ```powershell
   winget install --id Tailscale.Tailscale -e
   ```

2. Dans Prospection (tutoriel de démarrage, ou onglet **Config**) : saisir son pseudo, l'adresse
   du hub et son jeton, puis enregistrer.

3. Vérifier dans l'onglet **État**, panneau « Partage » : « Synchronisé à l'instant », et la liste
   des amis avec leur dernier passage.

---

# En cas de problème

| Ce qui se passe | Quoi faire |
|---|---|
| `winget` n'est pas reconnu | Installer « Programme d'installation d'application » depuis le Microsoft Store, ou télécharger Git (<https://git-scm.com/download/win>) et Python (<https://www.python.org/downloads/>) à la main, en cochant « Add python.exe to PATH ». |
| `git clone` répond « Repository not found » | L'invitation GitHub n'est pas acceptée, ou la connexion s'est faite avec un autre compte. |
| Le raccourci ne fait rien | Ouvrir `C:\prospection\data\capture.log` : la dernière ligne dit ce qui bloque. |
| L'onglet État affiche « Capture arrêtée » en jeu | Le jeu a été lancé sans le raccourci : le fermer et le relancer par « Dofus + Prospection ». |
| Aucun prix après le choix du personnage | VPN actif ? Désigner la bonne interface réseau dans Config. Sinon, attendre une minute : après une mise à jour du jeu, l'outil retrouve seul ses repères. |
| Partage : « hub injoignable » | Le PC qui héberge est éteint, Tailscale n'est pas connecté d'un côté, ou la règle de pare-feu manque. |
| Partage : « jeton inconnu » | Jeton mal copié, ou l'hôte n'a pas enregistré sa liste d'amis. |

L'outil lit seulement ce que le jeu reçoit : il n'envoie rien, ne clique pas et ne modifie pas le
jeu. Cette lecture reste une zone grise vis-à-vis des conditions d'utilisation d'Ankama : chacun
l'utilise sous sa propre responsabilité, et l'outil ne se diffuse pas au-delà du groupe.
