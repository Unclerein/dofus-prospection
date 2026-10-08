# Maintenance : quand une mise à jour du jeu casse le décodage

Les clés des messages (et parfois leurs numéros de champ) changent à chaque build du client.
Après une mise à jour, l'outil ne reconnaît plus ses messages tant que `dofustool/keymap.json`
n'a pas été remis à jour. Rien n'est perdu entre-temps : tout le flux de jeu reste archivé brut
dans `data/archive.sqlite` et sera redécodé à la fin.

Les règles du `PLAN.md` §3 s'appliquent à toute la procédure : capture passive uniquement, et
aucun affichage de chaîne, d'octet brut, de handshake ou de flux d'authentification.

## Ce qui se fait tout seul

La capture retrouve elle-même les messages par leur structure (`dofustool/identify.py`) :

- si les prix moyens ne sont pas décodés une minute après la connexion, elle cherche dans ce que
  la connexion a échangé. Si elle retrouve la liste des prix sous une autre clé, c'est une mise à
  jour : elle réécrit `keymap.json`, marque « périmées » (`"stale": true`) les entrées pas encore
  retrouvées pour qu'elles ne servent plus, et décode ce qui était déjà arrivé ;
- ensuite, toutes les 20 secondes, elle cherche les messages encore manquants. Ceux qui dépendent
  d'une action (banque, annonces HDV, cours du marché, onglet Vendre) sont retrouvés la première
  fois que tu fais l'action en jeu. Les annonces HDV restent « partielles » (`"partial": true`)
  tant qu'aucun équipement n'a été ouvert : il faut en voir un pour retrouver le champ des effets ;
- un message n'est retenu que si son contenu se contrôle (objets connus, niveaux cohérents avec
  l'expérience, moyenne des ventes égale au prix moyen du jeu…) et s'il n'y a qu'un seul candidat ;
- l'ancienne table est copiée dans `data/keymap-backups/` avant chaque écriture.

Le journal `data/capture.log` le raconte : `Mise à jour du jeu détectée`, puis une ligne
`Message retrouvé : …` par message. Pour vérifier ou rattraper une connexion archivée :

```powershell
.\.venv\Scripts\python.exe -m dofustool.tools.reidentify            # dernière connexion, sans rien écrire
.\.venv\Scripts\python.exe -m dofustool.tools.reidentify --write    # écrit dans keymap.json
```

La suite de ce document ne sert que si l'alerte persiste : la recherche automatique n'a rien
trouvé, ou a trouvé plusieurs candidats (`Message … non retrouvé : plusieurs candidats`).

## Reconnaître la panne

- Le journal `data/capture.log` contient `ALERTE : Flux de jeu actif depuis … sans prix moyens décodés`.
- Le dashboard affiche « Alerte de décodage », et la page **État** indique un dernier relevé ancien.

Le jeu envoie les prix moyens au choix du personnage, puis une fois par heure à partir de là. Si la
capture est lancée alors que le jeu est déjà connecté, elle rate le premier envoi et attend le
suivant, jusqu'à une heure : rien n'est cassé, et tout le reste est décodé entre-temps. Pendant cette
attente, la page **État** affiche « Décodage : En attente » avec la raison, et la capture cherche déjà
si les clés ont changé. L'alerte n'apparaît qu'après 70 minutes de jeu sans aucun prix moyen.
Pour l'éviter : lancer le jeu par le raccourci Prospection, qui démarre la capture avant la connexion.

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

**Inventaire : manuel.** Gros message serveur → client (une dizaine de Ko) reçu quelques
secondes après le choix du personnage : un champ répété d'entrées (position, objet), l'objet
portant son identifiant et sa quantité, plus les kamas au premier niveau.

Repères du build du 6 octobre 2026 (les clés **et** les numéros de champ ont changé) :
prix moyens `isr` (entrées en 3, identifiant en 1, prix en 2) ; inventaire `irl` (entrées en 2,
kamas en 4 ; dans une entrée, objet en 1 et position en 4 ; dans l'objet, quantité en 3 et
identifiant en 4) ; personnages `ksc` (entrées en 2 ; identifiant en 3, infos en 4 ; niveau en 3
et nom en 6) ; choix `kta` (identifiant en 1) ; métiers `ipz` (entrées en 1 ; niveau en 1,
expérience en 2, métier en 5). Les messages gardent leur place et leur taille dans le début de
la connexion d'un build à l'autre : comparer les deux séquences donne les candidats en une minute.

Même build, messages liés à une action : banque `irp` (kamas en 1, entrées en 2, même forme
d'entrée que l'inventaire) ; cours du marché `ire` (série horaire en 1, journalière en 3 ; prix
en 1, identifiant en 2, date en 3, quantité en 4 — contrôle : la moyenne des prix journaliers
pondérée par les quantités redonne le prix moyen du jeu) ; annonces HDV `jzs`, demandées par
`kcy` (identifiant en 1, ouverture en 2) : entrées en 1, identifiant en 3 ; dans une entrée,
identifiant en 1, prix en 2, uid en 3. Pour un équipement, chaque entrée porte en plus ses effets en champ 5 (répété) : identifiant
de l'effet en 10, valeur en 5 ; les lignes de dégâts d'une arme n'ont pas de valeur simple.
Contrôle : la vitalité d'un exemplaire doit tomber dans la fourchette de base de l'objet.
Une annonce portant un sous-message inconnu est refusée plutôt qu'enregistrée sans effets.

Ventes, achats et lots modifiés (build du 6 octobre 2026) : le message d'information `lof` porte un
numéro de texte en champ 2 et ses paramètres, écrits en chiffres, en champ 4 répété. Texte 65 = vente
conclue (prix du lot, objet, objet, taille du lot) ; texte 252 = achat (objet, identifiant interne,
taille du lot, prix du lot). Ces numéros de texte ne changent pas d'un build à l'autre. `jxt` renvoie un
lot créé ou modifié : prix en 1, temps restant en 3, référence en 2 (objet en 1, lot en 3, taille en 4).
Les deux sont retrouvés automatiquement, comme les autres.

Forgemagie (build du 6 octobre 2026) : `kch` annonce un objet posé sur l'atelier (enveloppe en 3, objet
en 1 : quantité en 3, modèle en 4, identifiant de l'exemplaire en 5, effets en 7 répété avec valeur en 5
et identifiant en 10). Il arrive à chaque passage pour la rune, et une fois pour l'équipement. `kco`
donne le résultat : état en 1 (2 = rune passée, 1 = échec), puis en 2 le puits (décimal en 2, absent
s'il est vide), son sens de variation en 3 (0 inchangé, 1 en hausse, 2 en baisse) et l'objet après le
passage en 4. Les deux sont retrouvés automatiquement au troisième passage de rune : la clé de l'objet
posé est celle qui précède chaque résultat, d'autres messages ayant la même forme.

Ventes hors ligne : `ita` arrive à la connexion avec, en champ 1, les kamas des ventes conclues pendant
l'absence (vide = zéro ; remis à zéro quand on retire des kamas de la banque). Un entier seul ne se
reconnaît pas à sa forme : la clé n'est retrouvée que si le total annoncé est égal au dernier total
connu et vaut au moins 1 000. Sinon, la chercher à la main : un message serveur d'un seul entier, reçu
avec l'inventaire, dont la valeur monte du prix des lots partis d'une connexion à l'autre.

La capture relit `keymap.json` dès qu'il change : inutile de la relancer après la correction.

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
