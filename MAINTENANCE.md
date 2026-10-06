# Maintenance : quand une mise à jour du jeu casse le décodage

Les clés des messages (et parfois leurs numéros de champ) changent à chaque build du client.
Après une mise à jour, l'outil ne reconnaît plus ses messages tant que `dofustool/keymap.json`
n'a pas été remis à jour. Rien n'est perdu entre-temps : tout le flux de jeu reste archivé brut
dans `data/archive.sqlite` et sera redécodé à la fin.

Les règles du `PLAN.md` §3 s'appliquent à toute la procédure : capture passive uniquement, et
aucun affichage de chaîne, d'octet brut, de handshake ou de flux d'authentification.

## Reconnaître la panne

- Le journal `data/capture.log` contient `ALERTE : Flux de jeu actif depuis … sans prix moyens décodés`.
- Le dashboard affiche « Alerte de décodage », et la page **État** indique un dernier relevé ancien.

Fausse alerte possible : les prix moyens n'arrivent qu'après le choix du personnage. Si tu es
resté plus d'une minute sur l'écran de sélection, l'alerte se lève toute seule ensuite (ligne
`Alerte levée` dans le journal). Dans ce cas, il n'y a rien à faire.

## Procédure

### 0. Mettre à jour les données statiques

Le `dofus.proto` et le `dofus.sqlite` suivent le build du jeu. Les récupérer d'abord, sinon les
étapes suivantes s'appuient sur un schéma périmé :

```powershell
.\.venv\Scripts\python.exe -m dofustool.staticdata.update
```

Si la commande répond « déjà à jour » alors que le jeu vient de changer, la release de
dofus-sqlite n'a pas encore suivi. La ré-identification des prix moyens reste faisable (elle
repose sur la structure), mais `identify.py` signalera des `[désaccord schéma]`.

### 1. Faire une capture avec le nouveau build

Si une capture a déjà tourné depuis la mise à jour (via le lanceur), les messages sont dans
l'archive et cette étape est faite. Sinon :

```powershell
.\.venv\Scripts\python.exe -m dofustool.tools.identify
```

Lance le jeu, choisis ton personnage, attends quelques secondes. Pour les prix moyens, aucune
étiquette n'est nécessaire. Pour un message lié à une action (cours du marché), tape une
étiquette juste avant de faire l'action en jeu. Ligne vide pour quitter.

### 2. Ré-identifier par la structure

**Prix moyens : automatique.** L'outil cherche dans l'archive un gros message serveur → client
fait d'un seul champ répété, dont chaque entrée porte deux entiers : un identifiant d'item connu
et un prix. Il retrouve la clé et les numéros de champ.

```powershell
.\.venv\Scripts\python.exe -m dofustool.tools.reidentify --since 2026-10-05
```

`--since` (date de la mise à jour) évite de retomber sur les messages de l'ancien build. La
sortie attendue est un seul candidat avec au moins 90 % d'items connus.

**Si aucun candidat ne sort, ou plusieurs :** identification manuelle.

1. Lister les clés du nouveau build avec leurs tailles et leur moment d'arrivée :

   ```powershell
   .\.venv\Scripts\python.exe -m dofustool.tools.explore chemin\vers\capture.pcapng --no-archive
   ```

2. Chercher la clé qui ressemble à la fixture `tests/fixtures/avg_prices.bin` : un seul message,
   serveur → client, d'environ 80 Ko, reçu une à trois secondes après le choix du personnage.
3. Vérifier sa structure avec `identify.py`, qui affiche les numéros de champ et les valeurs
   numériques : un champ répété, et dans chaque entrée un identifiant et un prix.

Repères du build du 5 octobre 2026, pour comparaison : clé `itn`, 8 924 entrées, champ répété 1,
identifiant en champ 3, prix en champ 5.

**Cours du marché : manuel.** Lancer `identify.py`, taper une étiquette, puis ouvrir l'onglet
« Cours du marché » d'un objet. Le message cherché est la réponse du serveur (environ 2 Ko pour
un objet très échangé) qui suit une requête du client ne contenant que l'identifiant de l'objet.
Il porte deux champs répétés (série horaire, série journalière) dont chaque entrée contient une
quantité, une date, un prix et l'identifiant de l'objet. Changer de période en jeu n'envoie rien.

Repères du build du 5 octobre 2026 : clé `iuk` ; série horaire en champ 1, journalière en
champ 2 ; dans une entrée, quantité en 1, date en 2, prix en 3, identifiant en 4. La fixture
`tests/fixtures/market_history.bin` est le cours du Blé, et ses tests rappellent les valeurs
lues à l'écran ce jour-là.

**Personnages et métiers : manuel.** Trois messages du début de la connexion de jeu :

- `character_list` : serveur → client, juste avant le choix du personnage ; une entrée par
  personnage, avec son identifiant, son niveau et son nom ;
- `character_select` : client → serveur, quelques octets, l'identifiant du personnage choisi seul ;
- `job_levels` : serveur → client, quelques secondes après ; une entrée par métier (identifiant
  du métier, expérience, niveau), puis une seule entrée à chaque gain d'expérience.

Repères du build du 5 octobre 2026 : `ksw` (entrées en champ 1 ; dans une entrée, identifiant
en 3 et infos en 2 ; dans les infos, niveau en 4 et nom en 5), `kth` (identifiant en 1), `irl`
(entrées en champ 2 ; métier en 1, expérience en 2, niveau en 5).

### 3. Mettre à jour `keymap.json`

Automatiquement, si `reidentify` a trouvé un candidat unique et sûr :

```powershell
.\.venv\Scripts\python.exe -m dofustool.tools.reidentify --since 2026-10-05 --write
```

Ou à la main, dans `dofustool/keymap.json` (c'est le seul fichier à modifier) :

```json
{
  "avg_prices": {
    "key": "itn",
    "fields": { "entries": 1, "item_id": 3, "price": 5 }
  }
}
```

Puis vérifier que rien d'autre n'est cassé :

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Le test `test_fixture_parses_with_current_keymap` échoue si les numéros de champ ont changé :
la fixture date de l'ancien build. Dans ce cas, la régénérer après l'étape 4 avec
`python -m dofustool.tools.extract_fixture avg_prices`, puis ajuster dans ce test le nombre
d'items et les deux prix de contrôle.

### 4. Redécoder les messages archivés (backfill)

Tout ce qui a été capté entre la mise à jour et la correction est encore dans l'archive :

```powershell
.\.venv\Scripts\python.exe -m dofustool.db.backfill
```

La commande rejoue toute l'archive avec le keymap courant. Elle est sans risque : les relevés
déjà enregistrés sont reconnus par leur contenu et ne sont pas dupliqués, et les messages qui
n'ont pas la forme attendue sont comptés comme « rejetés », jamais enregistrés.

Contrôler ensuite la page **État** du dashboard : le dernier relevé doit être récent, et
l'alerte disparaît au prochain relevé décodé en live.

## Consulter la référence

Le projet SniffSniffSquared a peut-être déjà ré-identifié ses propres messages sur le nouveau
build. Ses noms logiques ne sont pas les nôtres (il ne décode pas les prix moyens), mais son
keymap et ses notes donnent des indices sur l'ampleur de la rotation :

```powershell
git -C reference\SniffSniffSquared pull
```

Fichiers utiles : `sniffer/keymap.json`, `sniffer/schema.json`, `docs/observations.md`.
À lire seulement : sa section sur Frida et l'extraction de schéma à l'exécution est hors de
notre périmètre (capture strictement passive).

## Pièges connus

- **Une clé peut survivre à une rotation en changeant de sens.** Un keymap périmé qui « marche
  encore » écrirait n'importe quoi. C'est pour ça que les parseurs refusent tout message qui n'a
  pas exactement la forme attendue : ne pas assouplir ces contrôles pour faire passer un message.
- **Le tri authentification / jeu** repose sur la présence de `type.ankama.com/…` dans la
  première frame d'une connexion. Si `explore` annonce « Aucun flux de jeu trouvé » sur une
  capture valide, c'est l'enveloppe qui a changé : voir `dofustool/protocol/frame.py`.
- **Capture commencée en cours de partie** : sans le début de la connexion, le flux ne peut pas
  être découpé. Lancer la capture avant le jeu (le lanceur le fait).
- **VPN actif** : le trafic passe par une autre interface. La désigner avec `iface` dans
  `config.toml` (liste : `python -m dofustool.tools.identify --list-ifaces`).
