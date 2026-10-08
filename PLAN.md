# PLAN — Outil d'analyse de marché Dofus 3 (v1, Python)

> Cadrage pour Claude Code. Travailler **phase par phase**. À la fin de chaque phase, s'arrêter, résumer ce qui a été fait et attendre la validation de l'utilisateur.

## 1. Contexte et objectifs

Outil personnel, 100 % Python, qui capte **passivement** le trafic du client Dofus 3 pour :
- enregistrer les **prix moyens** de tout le catalogue, reçus par le client à la connexion ;
- enregistrer le **dernier prix de vente, l'historique et les quantités vendues** de l'onglet « Cours du marché », pour les objets que l'utilisateur consulte en jeu ;
- classer les **crafts les plus rentables** sur ses métiers, y compris le craft récursif ;
- repérer les **ressources dont le prix s'écarte de leur tendance** (achat-revente).

### Faits établis (étape 0, validée sur une capture réelle)
- Serveur de jeu : TCP **port 5555**, **en clair** (pas de TLS), contenu **protobuf**.
- Une connexion courte vers un **serveur d'authentification** (aussi en 5555) précède la connexion de jeu. Elle contient probablement le jeton de session.
- Une capture réelle est disponible en local dans `tests/fixtures/capture.pcapng` (jamais commitée).

## 2. Références externes (à consulter, pas à intégrer)

| Ressource | Usage |
|---|---|
| **SniffSniffSquared** — `github.com/Miou-zora/SniffSniffSquared` (Rust, MIT) | **Référence principale.** Framing connu, méthode d'identification des messages, keymap, archivage des messages, décodage des prix HDV. Le cloner en lecture seule dans `reference/` (gitignoré). Lire en priorité son README, son RUNBOOK et `sniffer/src/messages.rs`. |
| **dofus3-sniffer-tui** — `github.com/tikkamasala/dofus3-sniffer-tui` (Go) | Référence secondaire : désenveloppement des messages et fichier de correspondance des noms obfusqués. |
| **dofus-sqlite** — `github.com/ledouxm/dofus-sqlite` (releases) | **Source des données statiques** : `dofus.sqlite` (items, recettes, traductions FR), mis à jour toutes les heures et aligné sur la version du jeu. Contient aussi le `dofus.proto` obfusqué. |

**Framing documenté par SniffSniffSquared**, à valider sur notre capture :
```
TCP :5555 → frame préfixé par sa longueur (varint)
  └─ Frame { oneof Request | Response | Payload(event) }
      └─ google.protobuf.Any  type_url = "type.ankama.com/<clé>"   ← identité du message
          └─ corps du message
```
Les **clés obfusquées changent à chaque build** du client. Les messages s'identifient donc empiriquement, en les corrélant avec ce qui s'affiche à l'écran.

**Licence** : SniffSniffSquared est sous MIT. Reprendre des idées ou des algorithmes est libre. Si du code est transposé de façon substantielle, conserver la mention de copyright MIT dans un fichier `THIRD_PARTY_NOTICES.md`.

## 3. Règles non négociables

1. **Capture strictement passive.** Ne jamais envoyer, modifier, rejouer ou injecter de paquet. Pas de proxy, pas de lecture ni d'injection mémoire du client, pas de Frida.
2. **Aucune automatisation d'action en jeu** (clics, touches, déplacements).
3. **Affichage pendant le développement :**
   - autorisé : la structure (tailles, numéros de champ, types, compteurs) et les **valeurs numériques** des messages du serveur de jeu ;
   - interdit : les **chaînes de caractères** et les octets bruts ;
   - interdit : tout contenu des **premiers messages de la connexion de jeu** (le handshake, qui peut contenir le jeton) ;
   - interdit : tout contenu du **flux d'authentification**, qui est entièrement ignoré.
4. **Archivage brut autorisé uniquement en local** : la base `data/archive.sqlite`, gitignorée et jamais partagée. Elle contient le chat et doit être traitée comme une donnée privée.
5. Ne jamais commiter `*.pcap`, `*.pcapng`, `data/`, `reference/`. Mettre en place le `.gitignore` **dès le premier commit**.

## 4. Périmètre v1

**Inclus :**
- prix moyens au login ;
- cours du marché (dernier prix de vente, historique, quantités vendues) ;
- archivage des messages ;
- marges de craft, y compris le craft récursif ;
- tendances ;
- capture live et lanceur unique ;
- dashboard Streamlit ;
- un seul serveur de jeu.

**Exclus (v2+) :**
- listings HDV par lot. Coût réduit : ces messages sont déjà décodés dans la référence ;
- lecture automatique des métiers ;
- multi-serveurs ;
- brisage et runes ;
- croisement avec le farm.

## 5. Stack

- **Python** : l'utilisateur a la 3.14. Si une dépendance ne l'accepte pas, utiliser `uv` avec un environnement en 3.13.
- **Dépendances** : `scapy` (`AsyncSniffer` en live, `PcapReader` hors ligne), `protobuf` et/ou `bbpb` pour le décodage sans schéma, `sqlite3`, `streamlit`, `pytest`.
- **Système** : Windows 11 avec Npcap (déjà installé avec Wireshark). Le mode « WinPcap API-compatible » peut être requis selon la façon dont scapy charge la DLL : le vérifier en phase 0.

## 6. Architecture

```
dofustool/
  config.toml           # serveur, métiers+niveaux, taxe HDV, seuils, chemin du launcher Ankama
  keymap.json           # nom logique -> clé obfusquée du build courant (seul fichier à éditer après une rotation)
  capture/              # PcapSource (fichier) / LiveSource (sniff) : même interface
  protocol/             # réassemblage TCP, deframing varint, Frame, désenveloppement de Any
  messages/             # parseurs par nom logique : avg_prices, market_history…
  archive/              # stockage brut de tous les messages du serveur de jeu
  staticdata/           # import de dofus.sqlite
  db/                   # schéma + accès (data/market.sqlite)
  analysis/             # marges, craft récursif, liquidité, tendances
  app/                  # Streamlit
  tools/                # explore.py, identify.py, extract_fixture.py
launcher/start.ps1      # lanceur unique
reference/              # clones de référence (gitignoré)
tests/fixtures/         # capture.pcapng (gitignoré) + *.bin nettoyés (commités)
```

**Principe :** le code ne manipule que des **noms logiques**. `keymap.json` fait le lien avec les clés du build courant.

## 7. Phases

### Phase 0 : environnement
- Initialiser le dépôt git avec le `.gitignore`. Cloner les références dans `reference/`.
- Installer les dépendances et vérifier leur compatibilité avec Python 3.14 (repli sur la 3.13 si besoin).
- Télécharger le `dofus.sqlite` de la dernière release, puis inventorier les tables utiles : items, recettes, métiers, traductions.
- **Critère d'acceptation** : `pytest` tourne, `streamlit hello` se lance, et `dofus.sqlite` est lisible avec une requête de test qui renvoie les recettes d'un item connu.

### Phase 1 : décodage du flux (hors ligne)
- Réassemblage TCP et deframing varint, en s'inspirant de la référence, puis décodage de `Frame` et `Any`.
- Distinguer automatiquement le flux d'auth (ignoré) du flux de jeu.
- Mettre en place l'archivage : une ligne par message (horodatage, direction, clé, taille, corps).
- `tools/explore.py` : statistiques par clé (nombre, taille moyenne et maximale, direction, premier horodatage relatif à la connexion).
- **Critère d'acceptation** : 100 % du flux de jeu de la capture découpé sans reste, et la liste des clés avec leurs statistiques.

### Phase 2 : outillage d'identification et prix moyens
- `tools/identify.py` : pendant une capture live, l'utilisateur tape une étiquette au moment d'une action en jeu (« ouvre HDV », « ouvre cours du marché item X »…). L'outil liste les messages reçus dans les secondes qui suivent, avec leurs valeurs numériques.
- Identifier le message des prix moyens. Candidat : un gros message serveur → client reçu peu après la sélection du personnage. La référence signale un message d'environ 70 Ko non identifié. Il devrait contenir des paires (identifiant d'item, prix) en champ répété.
- Faire la correspondance entre les identifiants et les items de `dofus.sqlite`.
- **Critère d'acceptation** : au moins 90 % des identifiants correspondent à un item connu, et 10 couples (nom, prix) sont présentés à l'utilisateur pour un contrôle de vraisemblance.
- `tools/extract_fixture.py` : extraire le corps de ce message en `tests/fixtures/avg_prices.bin` et écrire les tests associés.

### Phase 2b : onglet « Cours du marché »
- Capture dédiée : l'utilisateur ouvre le cours du marché de 2 ou 3 objets aux prix très différents, passe par les trois périodes (24 h / 7 j / 30 j) et **note les valeurs affichées** : dernier prix de vente, quelques points de courbe et leurs quantités.
- Identifier les messages correspondants (un par période est possible). Décoder l'identifiant d'item, le **dernier prix de vente** et la série temporelle (horodatage ou tranche, prix, quantité). Déterminer les unités : prix unitaire ou par lot, granularité des tranches.
- **Critère d'acceptation** : le dernier prix de vente et au moins 5 points de courbe correspondent **exactement** à l'écran pour chaque objet testé. Ajouter les fixtures `.bin` et les tests.

### Phase 3 : données statiques
- Importer depuis `dofus.sqlite` les items (id, nom FR, type, niveau, échangeable ou non), les recettes (résultat, ingrédients, quantités) et le métier et niveau requis.
- Commande de mise à jour qui télécharge la dernière release et réimporte.

### Phase 4 : stockage (`data/market.sqlite`)
- `snapshots(id, ts, content_hash UNIQUE)` : **dédoublonnage par hash de contenu**, pas par intervalle de temps ;
- `avg_prices(snapshot_id, item_id, price)` ;
- `last_sales(item_id, price, captured_at)` ;
- `market_history(item_id, period, bucket_ts, price, qty_sold, captured_at)`, unique sur (item_id, period, bucket_ts), la valeur la plus récente gagne ;
- les tables statiques issues de la phase 3.

### Phase 5 : capture live et lanceur
- `python -m dofustool.capture` : sniff passif sur `tcp port 5555`, archivage, parsing des messages connus, écriture en base.
- **Surveillance du décodage :** si un flux de jeu est actif mais qu'aucun message `avg_prices` n'est décodé dans les 70 minutes qui suivent la connexion (le jeu les renvoie toutes les heures, pas toujours au choix du personnage), émettre une alerte claire (log et indicateur dans le dashboard). C'est le signe d'une mise à jour qui a cassé le keymap. Ne jamais planter, ne jamais écrire de données douteuses.
- `launcher/start.ps1` :
  1. démarre la capture en arrière-plan et attend qu'elle soit prête ;
  2. lance le launcher Ankama, dont le chemin est dans `config.toml` ;
  3. lance Streamlit (optionnel) ;
  4. arrête proprement la capture quand le processus Dofus se termine.
  
  Fournir aussi un raccourci bureau.
- Logs : une ligne par événement utile (snapshot enregistré avec son nombre d'items, cours du marché enregistré pour tel item, alerte). Rien d'autre.

### Phase 6 : analyse
- **Prix de référence** : dernier prix de vente s'il est plus récent que le seuil configuré, sinon prix moyen du dernier snapshot. Toujours exposer la source et l'âge du prix.
- **Marge de craft** = prix(résultat) × (1 − taxe_hdv) − Σ quantité × prix(ingrédient).
- **Craft récursif** : min(achat, coût de craft) pour chaque ingrédient craftable, avec mémoïsation et garde anti-cycle.
- **Filtres** : métiers et niveaux de `config.toml`. Les recettes avec un prix manquant ou un ingrédient non échangeable sont signalées, pas masquées.
- **Liquidité** : quantités vendues sur 24 h et 7 j quand elles sont connues. Elles pondèrent les classements et signalent les objets peu échangés.
- **Tendance** : si un historique du cours du marché existe, écart du dernier prix de vente aux moyennes 7 j / 30 j, avec un signal immédiat. Sinon, écart du prix moyen à la moyenne glissante des snapshots, actif seulement à partir du nombre minimal de snapshots configuré. En dessous, afficher « données insuffisantes ».

### Phase 7 : dashboard Streamlit
- **Crafts** : classement par marge absolue et en %, avec filtres par métier, niveau, capital maximum et liquidité minimale.
- **Tendances** : objets sous-cotés et sur-cotés.
- **Fiche item** : prix de référence avec sa source et son âge, courbes (cours du marché et snapshots), quantités vendues, recettes qui l'utilisent et qui le produisent.
- **État** : dernière capture, âge du dernier snapshot, santé du décodage, version des données statiques.

### Phase 8 : procédure de maintenance
Rédiger `MAINTENANCE.md`, la procédure à suivre après une mise à jour du jeu qui casse le décodage :
1. capture avec `identify.py` ;
2. ré-identification par **structure** (nombre et type des champs, taille, moment d'arrivée) en s'appuyant sur les fixtures ;
3. mise à jour de `keymap.json` ;
4. re-décodage des messages archivés depuis la mise à jour (backfill).

Consulter aussi le keymap de la référence, qui a peut-être déjà été mis à jour en amont.

## 8. Limites connues (à afficher dans le README)
- **Les prix moyens sont théoriques.** Ils sont lissés, en retard sur le marché et ne distinguent pas les lots. Il n'y a pas de profondeur de marché en v1.
- **Les données précises ne couvrent que les objets consultés manuellement.**
- **Les tendances sont faibles au début** pour les objets jamais consultés dans le cours du marché.
- **Les marges ignorent** le temps passé, la valeur des ressources farmées soi-même au-delà du coût d'opportunité et l'XP de métier.
- **Chaque mise à jour du jeu peut casser le décodage** jusqu'à ré-identification.
- **Un seul serveur,** Windows avec Npcap uniquement.
- **Zone grise vis-à-vis des CGU d'Ankama.** Ne pas diffuser l'outil, les archives ni les captures.

## 9. Paramètres utilisateur (`config.toml`)
- nom du serveur ;
- métiers et niveaux ;
- taxe HDV ;
- seuil de fraîcheur du dernier prix de vente ;
- nombre minimal de snapshots pour les tendances ;
- liquidité minimale par défaut ;
- chemin du launcher Ankama.
