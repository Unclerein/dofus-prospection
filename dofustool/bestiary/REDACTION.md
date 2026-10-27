# Rédiger les notes de donjon

Les notes (`notes/<id>-<nom>.toml`) complètent la fiche calculée depuis les données du jeu : l'essentiel du donjon,
l'ordre de focus, et pour chaque monstre sa portée, ses déplacements, son spécial et ses sorts. L'overlay les montre
quand on appuie sur la touche des fiches en survolant un monstre.

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
   `run_in_background` vrai, avec la consigne ci-dessous remplie.
4. **Vérifier chaque note à son retour**, contre `<id>-guide.txt` :
   - chaque point essentiel ;
   - l'ordre de focus : il doit être dans le guide, sinon `focus = []` ;
   - chaque mécanique de monstre ;
   - quelques sorts tirés au hasard.

   Chercher les passages avec grep, ne pas lire le guide en entier. Corriger soi-même ce qui est faux ou supprimer la
   ligne. Lire le rapport de l'agent : il signale les contradictions et les trous du guide.
5. **Tests puis commit sur `essai`** :
   - `.venv/Scripts/python -m pytest -q tests/test_bestiary.py` (toutes les notes se lisent, et leurs monstres
     appartiennent à leur donjon), puis toute la suite ;
   - un commit par lot, qui nomme les donjons.

   Jamais de push sur `main` sans le feu vert explicite du joueur.

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
  C:\dofus-test\dofustool\bestiary\notes\98-temple-koutoulou.toml. Ne la modifie pas.

## Règles strictes
1. Rien d'inventé, rien de déduit.
   - Chaque affirmation correspond à ce que le guide dit explicitement, ou à un chiffre de la base ou de la fiche
     calculée.
   - focus = seulement les monstres que le guide classe, dans son ordre, sinon [].
   - Une mécanique n'est rattachée à un monstre que si le guide le fait clairement. Si le guide met les sorts sans nom
     de monstre, recoupe avec MonsterData.spells et n'attribue que ce qui est sûr.
2. Si le guide et la base se contredisent : pour un chiffre, la base gagne ; pour une mécanique, écris celle du guide
   et signale la contradiction.
3. Tes propres mots : aucune phrase copiée, très court, sens exact. Pas de PV, pas de résistances, pas de valeurs de
   dégâts.
4. Pour chaque monstre, le résumé : portee (jusqu'où il frappe, avec ou sans ligne de vue), deplacement (ce qui le
   déplace ou te déplace), mecanique (invisibilité et sa durée, invulnérabilité et comment elle vient et part, autre
   mécanique). Plus la liste de ses sorts que le guide décrit.
5. Ne fais pas de section pour un monstre qui n'apparaît que dans une variante spéciale du combat (combat à deux boss,
   expédition).
6. Passe de vérification : relis le guide, retrouve pour chaque ligne le passage qui la justifie, et supprime ce que tu
   ne peux pas justifier.

## Format : écris C:\dofus-test\dofustool\bestiary\notes\{FICHIER}
donjon = {ID}
nom = "{NOM}"
source = "Dofus pour les Noobs — {URL}"
date_source = "AAAA-MM-JJ"   # dernière mise à jour affichée dans le guide, sinon date de mise en ligne
statut = "à confirmer"
bandeau = "≤ 70 caractères"
focus = [ids]

[[essentiel]]   # 3 à 5
type = "ok" ou "danger"
titre = "≤ 40 caractères"
texte = "≤ 110 caractères"

[monstres.<id>]   # un par monstre du donjon, boss compris ; nom exact du jeu toujours présent
nom = "…"
portee = "≤ 90"
deplacement = "≤ 90"
mecanique = "≤ 90"   # chaque champ seulement s'il est justifié

[[monstres.<id>.sorts]]
nom = "Nom exact du sort"
portee = "PO min à max, ligne de vue oui/non, en ligne si c'est le cas"   # chiffres de la base
effet = "≤ 110 caractères : élément, effet, en tes mots ; pas de valeurs de dégâts"
limite = "≤ 60 : relance, x par tour/par cible, dès le tour N"   # seulement si connu

Vérifie avec tomllib que le fichier se lit, et que les ids existent et appartiennent au donjon. Ne touche à AUCUN autre
fichier du dépôt. Ni commit, ni push.

## Rendu final (court)
1. Ce que le guide ne dit pas, et que tu as laissé vide.
2. Les contradictions entre le guide et la base.
3. La date du guide.

Pas de recopie de la note.
```

`{FICHIER}` : `<id>-<nom en minuscules, sans accents, mots séparés par des tirets>.toml`, par exemple
`67-transporteur-sylargh.toml`.
