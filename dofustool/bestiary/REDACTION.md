# Rédiger les notes de donjon

Les notes (`data/bestiary/notes/donjon-<id>.toml`, `avis-<monstre>.toml`) complètent la fiche calculée depuis les données du jeu : l'essentiel du donjon,
l'ordre de focus, et pour chaque monstre sa portée, ses déplacements, son spécial et ses sorts. L'overlay les montre
quand on appuie sur la touche des fiches en survolant un monstre.

**Les notes ne vont jamais dans le dépôt** : le guide interdit la reproduction de ses textes et le dépôt est public. Elles
restent dans `data/` et passent d'un joueur à l'autre par le hub de partage. L'envoi est automatique : l'interface
(`python -m dofustool.web`) synchronise toutes les minutes, et une note écrite ou modifiée part à la synchro suivante.
Pour une même note, la plus récente l'emporte : c'est ainsi qu'un ami améliore une fiche.

## La règle d'or

**Fiabilité avant exhaustivité.** Une note courte et vraie vaut mieux qu'une note complète et fausse. Une première
série écrite sans règles a été jugée « très approximative » par le joueur : ordres de focus inventés, mécaniques
prêtées au mauvais monstre. Depuis, les notes suivent les règles ci-dessous, et chacune est vérifiée ligne à ligne.

## Procédure (une session dédiée, par lots)

1. **Budget.** Les notes coûtent cher : l'offre est Pro. Regarder l'usage (outil `get_usage`) avant chaque lot, et
   s'arrêter quand la fenêtre de 5 heures dépasse environ 80 %. Un lot de 5 donjons coûte environ 500 000 jetons Sonnet
   plus la vérification. Garder le contexte de la session principale petit : ne jamais lire un guide entier soi-même,
   seulement chercher (grep) les passages à vérifier.
2. **Préparer le lot** : `.venv/Scripts/python -m dofustool.bestiary.prepare --batch 5`. Pour chaque donjon, il écrit
   `data/bestiary/<id>-guide.txt` (texte brut du guide), `<id>-jeu.txt` (fiche calculée) et `data/bestiary/lot.json`.
   Un donjon sans guide est signalé dans `lot.json` : on le passe.
3. **Un agent par donjon, en parallèle** : outil Agent, `subagent_type` « general-purpose », `model` « sonnet »,
   avec la consigne ci-dessous remplie (les agents tournent d'eux-mêmes en arrière-plan). Pour économiser, écrire la
   consigne une fois dans un fichier et n'envoyer à chaque agent que `{ID}`, `{NOM}`, `{URL}` et le chemin du fichier.
   Une session ouverte dans une worktree ne peut pas écrire dans `C:\dofus-test` : les agents écrivent alors dans le
   dossier temporaire de la session, et on déplace les 5 notes dans `data/bestiary/notes` à la fin du lot.
4. **Vérifier chaque note à son retour**, contre `<id>-guide.txt` :
   - chaque point essentiel ;
   - l'ordre de focus : il doit être dans le guide, sinon `focus = []` ;
   - chaque mécanique de monstre ;
   - quelques sorts tirés au hasard ;
   - les erreurs qui reviennent à chaque lot malgré la consigne (règle 8 de la consigne) : chiffres de dégâts ou de PV,
     focus tiré d'un succès ou de la composition de l'équipe, conseil absent du guide, ligne tirée de l'expédition de
     guilde, section pour une invocation hors du donjon (le test la refuse).

   Chercher les passages avec grep, ne pas lire le guide en entier. Corriger soi-même ce qui est faux ou supprimer la
   ligne. Lire le rapport de l'agent : il signale les contradictions et les trous du guide.
5. **Tests, puis partage** :
   - `.venv/Scripts/python -m pytest -q tests/test_bestiary.py` : toutes les notes de ce PC se lisent, et leurs monstres
     appartiennent à leur donjon ;
   - les notes ne sont PAS commitées. Elles partent au hub toutes seules si l'interface tourne. Un commit sur `essai`
     ne sert que si le code a changé, et jamais de push sur `main` sans le feu vert explicite du joueur.

## Avis de recherche

Même procédure, avec trois différences :
- **Préparation** : `--avis-queue` / `--avis-batch N` au lieu de `--queue` / `--batch`. Les fichiers s'appellent
  `data/bestiary/avis-<monstre>-guide.txt` et `avis-<monstre>-jeu.txt`.
- **Fichier de la note** : `data/bestiary/notes/avis-<monstre>.toml`, avec `avis = <id du monstre recherché>` à la
  place de `donjon = …`.
- **Contenu** : `focus` seulement si le guide en donne un, l'essentiel du combat, puis une section `[monstres.<id>]`
  pour le recherché et pour chaque monstre qui l'accompagne selon le guide.

Pour la consigne de l'agent, reprendre celle des donjons en remplaçant « note de donjon » par « note d'avis de
recherche », `donjon = {ID}` par `avis = {ID}`, le fichier de sortie par `avis-{ID}.toml`, et les chemins des
fichiers par `avis-{ID}-guide.txt` et `avis-{ID}-jeu.txt`.

## Cas particuliers déjà tranchés

- **Comte Harebourg** : Klime, Missiz Frizz, Nileza et Sylargh n'ont pas de section, car le combat à deux boss est un
  cas à part. Même règle pour tout monstre que le guide ne fait apparaître que dans une variante spéciale du combat.
- **Rattacher les sorts aux monstres** : quand le guide met le nom du monstre en image au-dessus de ses sorts, on
  rattache chaque sort par `MonsterData.spells` dans la base.
- **Ordre de focus contradictoire** dans le guide : on prend celui de la stratégie générale, et on le signale dans le
  rapport.

## Consigne pour l'agent (remplacer `{ID}`, `{NOM}`, `{URL}`, `{FICHIER}`)

```
Tu écris UNE note de donjon Dofus 3, en français : **{NOM}** (DungeonData id {ID}). Elle sert à une fiche affichée en
combat. Ta mission est la fiabilité, pas l'exhaustivité : une note courte et vraie vaut mieux qu'une note complète et
fausse.

## Tes sources, déjà sur le disque (n'utilise PAS internet ni WebFetch)
- Le guide (texte brut de {URL}) : C:\dofus-test\data\bestiary\{ID}-guide.txt. Lis-le EN ENTIER avec Read. C'est ta
  SEULE source pour la stratégie et les mécaniques. S'il ne parle pas de ce donjon, arrête-toi et dis-le.
- La fiche calculée depuis le jeu (monstres et leurs ids, portées) : C:\dofus-test\data\bestiary\{ID}-jeu.txt. Elle est
  fiable pour les ids, les noms et les portées, et parfois approximative sur les mécaniques.
- Pour une portée précise, la base est en LECTURE SEULE :
  sqlite3.connect("file:C:/dofus-test/data/static/dofus.sqlite?mode=ro", uri=True), via
  C:\dofus-test\.venv\Scripts\python.exe.
  - SELECT minRange, range, m_flags FROM SpellLevelData WHERE spellId = ? ORDER BY grade DESC LIMIT 1
  - m_flags & 4 = ligne de vue ; m_flags & 1 = en ligne
  - noms : table translations (id passé en texte, lang='fr') ; MonsterData.spells (JSON)
- Exemple de note validée par le joueur, pour le ton et le format :
  C:\dofus-test\data\bestiary\notes\donjon-98.toml. Ne la modifie pas.

## Règles strictes
1. Rien d'inventé, rien de déduit.
   - Chaque affirmation correspond à ce que le guide dit explicitement, ou à un chiffre de la base ou de la fiche
     calculée.
   - N'ajoute pas de conseil (« tue-le vite », « évite-le ») que le guide ne donne pas.
   - focus = seulement les monstres que le guide classe, dans son ordre, dans sa stratégie générale. Pas l'ordre
     d'un succès, pas un ordre qui dépend de la composition de l'équipe (dans ce cas, ne garde que la partie qui ne
     dépend de rien, ou []).
   - Une mécanique n'est rattachée à un monstre que si le guide le fait clairement. Si le guide met les sorts sans nom
     de monstre, recoupe avec MonsterData.spells et n'attribue que ce qui est sûr.
2. Si le guide et la base se contredisent : pour un chiffre, la base gagne ; pour une mécanique, écris celle du guide
   et signale la contradiction.
3. Tes propres mots : aucune phrase copiée, très court, sens exact. Pas de PV, pas de résistances, pas de valeurs de
   dégâts.
4. Pour chaque monstre, le résumé : portee (jusqu'où il frappe, avec ou sans ligne de vue), deplacement (ce qui le
   déplace ou te déplace), mecanique (invisibilité et sa durée, invulnérabilité et comment elle vient et part, autre
   mécanique). Plus la liste de ses sorts que le guide décrit.
5. EXPÉDITION DE GUILDE : ignore-la COMPLÈTEMENT. N'utilise rien de cette partie du guide (ni ses sorts, ni ses
   portées, ni ses mécaniques, ni ses ordres de focus, ni ses onglets « Sorts »), même pour un monstre que le reste du
   guide décrit mal : dans ce cas, laisse le champ vide. Même règle pour toute autre variante spéciale (combat à deux
   boss). Les tactiques de succès ne servent ni à l'essentiel ni au focus. Pas de section pour un monstre qui
   n'apparaît que dans une variante.
6. Sections [monstres.<id>] UNIQUEMENT pour les ids listés dans la fiche calculée ({ID}-jeu.txt). Une invocation
   absente de cette fiche n'a pas de section : mets ce qui compte sur elle dans l'essentiel ou dans la section du
   monstre qui l'invoque.
7. Passe de vérification : relis le guide, retrouve pour chaque ligne le passage qui la justifie, et supprime ce que tu
   ne peux pas justifier.
8. Erreurs que les notes précédentes ont TOUTES faites, corrigées à la main ensuite. Avant de rendre, relis ta note
   une fois pour chacune :
   - un chiffre de dégâts, de PV, de soin ou de pourcentage de résistance → supprime-le ;
   - un focus tiré d'un succès (Duo, Zombie, Premier, Hardi…) ou d'un ordre « si tu as un Iop / un Sacrieur… » →
     garde seulement la partie qui ne dépend de rien, sinon [] ;
   - un conseil que le guide ne donne pas dans son sens (« tue-le en premier », « reste loin ») → supprime-le ;
   - une ligne prise dans l'expédition de guilde ou dans une tactique de succès → supprime-la ;
   - une section pour une invocation absente de {ID}-jeu.txt → supprime la section ;
   - une précision ajoutée « pour faire complet » (durée, nombre de tours, élément) que le guide ne donne pas →
     supprime-la.

## Format : écris C:\dofus-test\data\bestiary\notes\{FICHIER}
donjon = {ID}
nom = "{NOM}"
source = "Dofus pour les Noobs — {URL}"
date_source = "AAAA-MM-JJ"   # dernière mise à jour affichée dans le guide, sinon date de mise en ligne, sinon ""
statut = "à confirmer"
bandeau = "≤ 70 caractères"
focus = [ids]

[[essentiel]]   # 3 à 5
type = "ok" ou "danger"
titre = "≤ 40 caractères"
texte = "≤ 110 caractères"

[monstres.<id>]   # un par monstre de la fiche calculée, boss compris ; nom exact du jeu toujours présent
nom = "…"
portee = "≤ 90"
deplacement = "≤ 90"
mecanique = "≤ 90"   # chaque champ seulement s'il est justifié

[[monstres.<id>.sorts]]
nom = "Nom exact du sort"
portee = "PO min à max, ligne de vue oui/non, en ligne si c'est le cas"   # chiffres de la base
effet = "≤ 110 caractères : élément, effet, en tes mots ; pas de valeurs de dégâts"
limite = "≤ 60 : relance, x par tour/par cible, dès le tour N"   # seulement si connu

Vérifie avec tomllib que le fichier se lit, que TOUTES les longueurs maximales sont respectées (mesure-les en
Python à la fin), et que les ids sont dans la fiche calculée. Ne touche à AUCUN autre
fichier du dépôt. Ni commit, ni push.

## Rendu final (court)
1. Ce que le guide ne dit pas, et que tu as laissé vide.
2. Les contradictions entre le guide et la base.
3. La date du guide.
4. Ce que la relecture de la règle 8 t'a fait retirer.

Pas de recopie de la note.
```

`{FICHIER}` : `donjon-{ID}.toml` pour un donjon, par exemple `donjon-67.toml` ; `avis-{ID}.toml` pour un avis de
recherche.

## Compléter avec Huzounet

[Huzounet](https://huzounet.fr/monsters) a, pour les boss et quelques monstres, une fiche courte : passif, actif,
stratégie, notes. Elle sert de **deuxième source** pour trouver ce qui manque aux notes déjà écrites. Mêmes interdits
que pour le guide : ses mentions légales interdisent la reproduction, donc son texte brut reste dans `data/` et on
n'écrit qu'avec nos mots.

1. **Préparer** : `.venv/Scripts/python -m dofustool.bestiary.prepare --huzounet`. Pour chaque note écrite, il
   télécharge la fiche de chacun de ses monstres dans `data/bestiary/huz/<monstre>.txt` (`<monstre>.aucune` quand le
   site n'en a pas ; une page déjà téléchargée ne l'est plus) et écrit `data/bestiary/huz-lot.json` : note, monstres,
   fichier Huzounet de chacun, et s'il a déjà une section dans la note.
2. **Comparer, par lots** : un agent Sonnet pour 8 notes (une fiche Huzounet est courte), plusieurs agents en
   parallèle, avec la consigne ci-dessous. Seules les notes dont au moins un monstre a une fiche Huzounet sont à faire
   (193 sur 201 le 28/10). Regarder l'usage (`get_usage`) entre deux vagues, comme pour la rédaction.
3. **Vérifier chaque ajout** contre le fichier Huzounet (grep), comme pour une note neuve. Puis
   `pytest -q tests/test_bestiary.py`. Une note modifiée part toute seule au hub, la plus récente l'emporte.
4. **Rapport** : `data/bestiary/huz-rapport.md` (local), une ligne par note : ajouts, contradictions laissées en l'état,
   rien à ajouter.

### Consigne pour l'agent (répéter le premier paragraphe pour chacune de ses notes)

```
Tu complètes des notes Dofus 3 déjà écrites et vérifiées, une à une. Pour chacune : la note {NOTE}. Une deuxième source, Huzounet, décrit certains de
ses monstres : {FICHIERS}. Le guide d'origine (Dofus pour les Noobs) est en texte brut dans {GUIDE}. Fiabilité avant
tout : n'ajoute rien de douteux.

N'utilise PAS internet. Lis la note, puis chaque fichier Huzounet en entier.

Règles :
1. Tu AJOUTES seulement ce que Huzounet dit clairement et que la note n'a pas : une mécanique (invulnérabilité et
   comment elle part, invisibilité, renvoi, état, passif), un déplacement, un point essentiel du combat. Rien de
   déduit, aucun conseil que Huzounet ne donne pas.
2. Tu ne SUPPRIMES rien et tu ne réécris pas ce qui est juste. Tu peux préciser une ligne si Huzounet est plus précis
   ET ne la contredit pas.
3. Si Huzounet contredit la note : regarde le guide (grep). Si le guide confirme la note, ne touche à rien. Sinon,
   ne touche à rien non plus : signale la contradiction dans ton rendu, avec les deux versions.
4. Tes propres mots, très court, mêmes longueurs maximales que la note (texte ≤ 110, mecanique ≤ 90, titre ≤ 40,
   3 à 5 [[essentiel]] au plus : si c'est plein, remplace seulement un point moins important et dis lequel).
   Pas de PV, de résistances ni de valeurs de dégâts.
5. Pas de nouvelle section [monstres.<id>] pour un monstre absent du donjon de la note. Pas de focus tiré de
   Huzounet si la note a déjà le sien ; si la note a focus = [] et que Huzounet en donne un clairement, mets-le et
   dis-le.
6. Ne change ni `source`, ni `statut`, ni `date_source`. Rien d'autre que tes notes n'est modifié. Ni commit, ni push.
7. Vérifie avec tomllib que le fichier se lit et mesure les longueurs en Python.

Rendu (court), note par note : ce que tu as ajouté ou précisé (une ligne chacun), les contradictions trouvées, et « rien à
ajouter » le cas échéant. Pas de recopie de la note.
```
