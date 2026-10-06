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

```powershell
git clone https://github.com/Unclerein/dofus-prospection.git C:\prospection
```

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
   Ankama et ouvre Prospection dans le navigateur.
2. Une **visite guidée** s'affiche : y saisir son serveur de jeu et son pseudo, puis, à l'étape
   « Partager avec tes amis », l'adresse du hub et le jeton reçus (voir plus bas).
3. Lancer le jeu et choisir son personnage, comme d'habitude. Les étapes de la visite se cochent
   toutes seules.

Toujours lancer le jeu par ce raccourci : l'écoute doit démarrer avant la connexion. Le second
raccourci, **« Prospection »**, ouvre l'outil seul, sans le jeu.

L'outil se met à jour tout seul à chaque lancement.

## 5. Partager les prix avec le groupe

Celui qui t'a donné l'outil t'envoie deux choses en privé :

- **l'adresse du hub**, qui commence par `https://` ;
- **ton jeton**, un mot de passe personnel. Ne le partage pas.

Les saisir dans la visite guidée, ou plus tard dans l'onglet **Config**, section « Partage avec des
amis », avec ton pseudo. Laisser « Ce PC héberge le hub » décoché, puis **Enregistrer**.

Dans la minute, l'onglet **État** affiche « Synchronisé à l'instant » et la liste des joueurs du
groupe. À partir de là, les prix que chacun relève à l'HDV profitent à tous.

Seuls les prix du marché circulent. Ton stock, tes ventes, tes personnages et le chat ne quittent
jamais ton PC.

---

# En cas de problème

| Ce qui se passe | Quoi faire |
|---|---|
| `winget` n'est pas reconnu | Installer « Programme d'installation d'application » depuis le Microsoft Store, ou télécharger Git (<https://git-scm.com/download/win>) et Python (<https://www.python.org/downloads/>) à la main, en cochant « Add python.exe to PATH ». |
| Le raccourci ne fait rien | Ouvrir `C:\prospection\data\capture.log` : la dernière ligne dit ce qui bloque. |
| L'onglet État affiche « Capture arrêtée » en jeu | Le jeu a été lancé sans le raccourci : le fermer et le relancer par « Dofus + Prospection ». |
| Aucun prix après le choix du personnage | VPN actif ? Désigner la bonne interface réseau dans Config. Sinon, attendre une minute : après une mise à jour du jeu, l'outil retrouve seul ses repères. |
| Partage : « hub injoignable » | Vérifier l'adresse du hub (elle commence par `https://`) et sa connexion Internet. Si ça dure, prévenir celui qui gère le hub. |
| Partage : « jeton inconnu » | Jeton mal copié (attention aux espaces). Sinon, en redemander un à celui qui gère le hub. |

L'outil lit seulement ce que le jeu reçoit : il n'envoie rien, ne clique pas et ne modifie pas le
jeu. Cette lecture reste une zone grise vis-à-vis des conditions d'utilisation d'Ankama : chacun
l'utilise sous sa propre responsabilité, et l'outil ne se diffuse pas au-delà du groupe.

Pour mettre en place le hub du groupe : [HUB-SERVEUR.md](HUB-SERVEUR.md) (serveur loué) ou
[HUB-PC.md](HUB-PC.md) (sur son propre PC).
