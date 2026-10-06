# PLAN v2 — Lots HDV, quantités sur les icônes, overlay du Comte Harebourg

> Brouillon à valider. Les règles non négociables de `PLAN.md` (§3) s'appliquent : capture passive, aucune action automatisée en jeu.

## 1. Préciser le lot du prix HDV (x1, x10, x100, x1000)

### Constat
- L'HDV renvoie, pour une ressource, le prix le plus bas de chaque taille de lot : x1, x10, x100 et x1000. Le décodage se fait dans `messages/hdv_listings.py`.
- `analysis/prices.py:unit_price` garde le meilleur prix **unitaire** parmi les quatre lots, mais **oublie de quel lot il vient**. `PriceRef` ne porte que `price`, `source` et `ts`.
- L'interface affiche donc « HDV · 2 h » sans dire que 98 k/u correspond en fait à un lot de 100 à 9 800 k.

### À faire
1. `unit_price` renvoie `(prix unitaire, taille du lot)`, et `PriceRef` gagne un champ `lot: int | None`. Ce champ vaut `None` pour les autres sources et pour les équipements.
2. Ces champs sont propagés dans `PriceBook._hdv`, `hdv_price` et `hdv_ask`, ainsi que dans toutes les réponses de `web/api.py` qui exposent une source de prix : crafts, stock, liste de courses, fiche objet, ingrédients de la forgemagie. Il faut aussi adapter l'appelant de `hdv_price` (`api.py`, vers la ligne 626).
3. Interface (`app.js`) :
   - `shortSource` et `sourceLine` affichent le lot : **« HDV x100 · 2 h »** ;
   - l'infobulle du prix donne les quatre lots, par exemple « x1 120 · x10 1 050 · x100 9 800 · x1000 — », avec le prix total du lot retenu ;
   - sur la fiche objet, le KPI « Prix de référence » porte le même libellé.
4. Tests : `unit_price` avec des lots manquants (0), ex æquo entre lots, et `PriceRef.lot` exposé par l'API.

### Option 1b (à décider)
Le prix unitaire du x1000 est trompeur quand on n'a besoin que de 3 unités. La liste de courses pourrait retenir, pour chaque ingrédient, la **combinaison de lots la moins chère pour la quantité réellement à acheter**, puis afficher « 1 × x10 » plutôt qu'un coût unitaire théorique.

## 2. Quantité possédée en bas à droite de l'icône, comme en jeu

### Constat
- Toutes les icônes passent par `tile(iconId, big)` dans `app.js` (ligne 58).
- Le stock (inventaire et banque) est déjà chargé côté API (`analysis/stock.Stock`). Seules quelques réponses exposent `have` : les ingrédients et le stock.

### À faire
1. API : un petit utilitaire ajoute `icon` et `have` (le total possédé) à chaque ligne qui porte un `item_id`. Il remplace les `row["icon"] = state["icons"].get(...)` dispersés dans `api.py`.
2. Front : `tile(iconId, big, qty)` ajoute un badge en bas à droite, avec un chiffre blanc à contour sombre comme dans le jeu. Le badge est masqué si la quantité vaut 0 ou est inconnue, et devient compact au-delà de 9 999 (« 12k »).
3. Le badge apparaît sur toutes les listes : crafts, stock, HDV, forgemagie, fiche objet, infobulles. Son infobulle donne le détail inventaire / banque.
4. CSS : `.tile` passe en `position: relative`, plus une classe `.tile .qty`, avec une variante pour les petites tuiles (24 px) des ingrédients.

### Questions
- Afficher le **total** (inventaire + banque, tous personnages connus) ou seulement l'inventaire du personnage connecté ? Proposition : le total, avec le détail au survol.
- Dans les recettes, garder « 3 / 10 » à côté du nom en plus du badge ?

## 3. Overlay pour le combat du Comte Harebourg

### Ce qui existe déjà : harebourg-ux
[github.com/Drayken/harebourg-ux](https://github.com/Drayken/harebourg-ux), en Python avec la seule bibliothèque standard. Ses phases 1 à 3 tournent en jeu depuis le 27/09/2026.
- **Fenêtre** : layered GDI, transparente aux clics, qui suit la fenêtre de Dofus et gère le DPI par écran.
- **Grille** : calée une fois par carte et par taille de fenêtre (origine et largeur de case), avec la projection `écranX = oX + (x − y)·l/2` et `écranY = oY + (x + y)·h/2`.
- **Confusion** : lue par **OCR du chat de combat**, avec l'OCR intégré de Windows, et corrigeable au clavier.
- **Positions saisies à la main** : souris 4 marque sa propre case, à refaire après chaque déplacement ; souris 5 marque la cible. La case à viser s'affiche en direct.
- **Abandonnés** : l'aperçu de la téléportation des tours pairs et impairs, les cartes des lieutenants, la ligne de vue.
- **Licence** : **aucun fichier de licence**, donc tous droits réservés par défaut. On peut s'en inspirer et reprendre les faits de jeu, mais pas copier le code sans l'accord de l'auteur.

### Règles du combat, corrigées par leurs tests en jeu
- **La confusion est fixée en début de tour.** Les PV qui bougent pendant le tour ne la changent pas. Le chat fait foi ; le tableau des PV n'est qu'indicatif :

  | PV en début de tour | Confusion |
  |---|---|
  | 100 – 90 % | 90° horaire |
  | 89 – 75 % | 90° anti-horaire |
  | 74 – 45 % | 180° |
  | 44 – 30 % | 90° anti-horaire |
  | 29 – 0 % | 90° horaire |

- **Comtoise** : en début de tour, chaque personnage lance « Comtoise » sur lui-même. La ligne de confusion qui l'accompagne donne l'angle absolu du tour.
- **Corps à corps** : chaque ligne de dégâts au contact ajoute **+90° horaire**, en boucle : 90° horaire → 180° → 90° anti-horaire → **0°** → 90° horaire. Pour un sort à deux lignes, la ligne 1 part avec l'ancien angle et la ligne 2 avec le nouveau. Dans le chat, cet ajout s'affiche toujours comme « horaire : 1 Pi/2 », parce qu'il est relatif. Au-delà de 10 coups reçus, un monstre ne fait plus tourner.
- **Les invocations ne sont jamais confuses.**
- **Échec critique** : un sort projeté hors de la carte ou sur une case non marchable échoue en critique. Au corps à corps, l'échec critique termine le tour.
- **Calcul**, en coordonnées de grille carrée (la carte iso tournée de 45°) :
  - `atterrissage = rotation(curseur − moi) + moi`
  - `à viser = rotation⁻¹(cible − moi) + moi`
- **Symétries** : aux tours impairs, l'attaquant est envoyé à 180° autour du Comte ; aux tours pairs, c'est le Comte qui est envoyé à 180° autour de l'attaquant. Une destination impossible tue toute l'équipe.
- **Carte de la salle** : grille carrée de 22 × 22 avec les cases vides, marchables et murs. Le mode Édition de comteharebourg.com l'exporte en JSON (`size` et `details`). **La question de la carte est donc réglée.**

### Ce que la capture réseau apporte en plus
harebourg-ux s'interdit de lire les paquets. C'est justement notre force, et ce sont ses points faibles :
1. **Positions automatiques** de tous les combattants, mises à jour à chaque déplacement, poussée ou téléportation. Plus de souris 4 à refaire après chaque déplacement.
2. **Confusion lue dans le flux**, sur l'état posé par Comtoise ou la valeur numérique du buff, au lieu de l'OCR. On n'a plus à régler la zone de chat ni à subir ses erreurs de lecture. Les coups au corps à corps se comptent sur les dégâts réels, limite de 10 comprise.
3. **Numéro du tour, donc sa parité**, plus la position du Comte : on peut prévisualiser la symétrie et marquer les coups qui déclencheraient **Air du Temps**, ce qu'ils ont abandonné. Même chose pour la croix **Mi-temps** : les cases où il ne faut pas finir son tour.
4. **La cible** peut se choisir sur la mini-carte (clic sur un monstre), sans calage de la grille.

### Étapes révisées
1. **Identifier les messages de combat** avec `tools/identify.py` :
   - messages génériques, dans n'importe quel combat : début de combat et carte, combattants, début de tour, déplacements et téléportations, PV, états et buffs, glyphes, sort lancé ;
   - messages spécifiques, avec au moins une capture du vrai combat : Comtoise, état de confusion, compteur de corps à corps, invulnérabilité.
2. **Conversion entre numéro de case Dofus et coordonnées de grille carrée.** On y cale la carte de la salle, ressaisie depuis le simulateur ou relevée en jeu, sans copier leur fichier.
3. **Moteur de règles** (`fight/harebourg.py`, testé) : visée et atterrissage, cycle du corps à corps, échec critique, symétries pair et impair, Air du Temps, Mi-temps.
4. **Mini-carte compagnon** dans l'appli web : positions en direct, confusion de chacun, case à viser pour la cible choisie, coups mortels en rouge.
5. **Superposition au jeu, au choix** :
   - **(a)** utiliser harebourg-ux tel quel à côté, la mini-carte ne servant qu'à vérifier ;
   - **(b)** écrire notre propre overlay en reprenant ses idées : layered GDI, profil de calage par taille de fenêtre, projection iso ;
   - **(c)** demander à l'auteur une licence, MIT par exemple, puis brancher son overlay sur notre flux via un petit fichier d'état local. C'est contraire à ses principes affichés (« pas de lecture de paquets »), donc ce serait un fork personnel, pas une contribution.

### Risques
- Les messages de combat sont nombreux, et leurs clés changent à chaque mise à jour, comme les autres. Il faudra les ajouter à `MAINTENANCE.md`.
- Il faut faire le donjon au moins une fois avec la capture active pour les messages spécifiques.
- CGU : un overlay purement informatif ne change pas la nature de l'outil, mais aucune action ne doit être automatisée.

## Avancement (06/10/2026)
- [x] **1. Lot du prix HDV.** Affiché partout où apparaît la source du prix (« HDV x100 »), avec le prix du lot en infobulle. L'option 1b reste à décider.
- [x] **2. Badge de quantité** sur toutes les icônes (inventaire + banque), détail au survol. Il est masqué dans les puces d'ingrédients, qui affichent déjà « possédé / requis ».
- [ ] **3. Harebourg** :
  - [x] Moteur de règles testé : `dofustool/fight/harebourg.py`.
  - [x] Géométrie et carte : `dofustool/fight/grid.py`, avec la conversion numéro de case ↔ grille et le calage de la salle sur les positions observées (`fit_offset`).
  - [x] Page « Combat » : simulation à la main.
  - [ ] Identifier les messages de combat sur la capture du combat.
  - [ ] Écrire l'état du combat en direct depuis la capture, puis brancher la page dessus.
  - [ ] Overlay au-dessus de Dofus (5b) : fenêtre transparente aux clics, calage de la grille par taille de fenêtre, alimentée par le même état.

## Ordre proposé
1. Lot HDV (1), petit chantier.
2. Badge de quantité (2), petit chantier.
3. Overlay : identifier les messages génériques de combat lors de tes prochains combats, puis la conversion des cases, le moteur de règles, la mini-carte, et enfin la superposition (5a, 5b ou 5c).
