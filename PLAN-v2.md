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

### Le combat (sources : guides communautaires, à confirmer en jeu sur Dofus 3)
- **Salle du boss** : le Comte Harebourg, un Granduk, un Cycloïde et un Nocturlabe, plus des monstres supplémentaires selon la taille du groupe.
- **Confusion horaire** : en début de tour, chaque personnage reçoit une confusion selon son pourcentage de PV. Ses sorts sont alors déviés : pour toucher une case, il faut viser la case tournée autour de soi.

  | PV restants | Déviation | Il faut viser |
  |---|---|---|
  | 100 – 91 % | 90° horaire | ¼ de tour anti-horaire |
  | 90 – 75 % | 90° anti-horaire | ¼ de tour horaire |
  | 74 – 46 % | 180° | case opposée |
  | 45 – 31 % | 90° anti-horaire | ¼ de tour horaire |
  | 30 – 1 % | 90° horaire | ¼ de tour anti-horaire |

  Aux valeurs limites, les deux confusions voisines sont possibles. Il vaut donc mieux **lire l'état réel dans les paquets** que le déduire des PV.
- **Corps à corps** : chaque ligne de dégâts infligée au contact ajoute 90° à la confusion, dans la limite de 10 ajouts par monstre et par tour. Un sort sans dégâts n'ajoute rien.
- **Invulnérabilité** : le Comte est invulnérable. On ne peut lever cette protection qu'aux **tours pairs**, en le frappant de façon à ce qu'il soit téléporté sur une case occupée par un allié.
- **Téléportation symétrique** : aux tours impairs, frapper le Comte téléporte l'attaquant à l'opposé de lui (symétrie de centre le Comte). Aux tours pairs, c'est le Comte qui est téléporté à l'opposé de l'attaquant.
- **Air du Temps** : si le Comte est téléporté sur un obstacle ou une case noire, toute l'équipe meurt. C'est l'erreur la plus fréquente.
- **Mi-temps / Carillon** : en début de tour, un glyphe en croix de taille 3 se pose autour du Comte, centre compris, pour 1 tour. Un allié qui commence son tour dessus meurt.
- **Monstres** : le Cycloïde attire et retire des PM, le Granduk frappe en ligne et vole de l'intelligence, le Nocturlabe rend la cible insoignable. L'ordre conseillé est Cycloïde, Nocturlabe, puis Granduk.

### Faisabilité : oui, en lecture seule
Tout ce qu'il faut est envoyé au client : carte, positions, PV, tour, états, glyphes. L'outil peut donc calculer et **afficher** où viser et quelles cases sont mortelles, sans rien envoyer ni cliquer. C'est ce que fait déjà manuellement le simulateur [comteharebourg.com](https://www.comteharebourg.com/simulator), mais ici sans saisie, puisque les positions viennent du flux.

### Étapes
1. **Identifier les messages de combat** avec `tools/identify.py`. Les messages génériques peuvent s'identifier dans n'importe quel combat, sans aller chez le Comte :
   - début de combat et identifiant de la carte ;
   - liste des combattants (id, équipe, case, PV, PV max) ;
   - début de tour (combattant, numéro du tour) ;
   - déplacements, téléportations, poussées ;
   - variations de PV ;
   - pose et retrait d'états ou de buffs ;
   - glyphes posés ;
   - fin de combat.

   Les états propres au Comte (confusion, invulnérabilité), les identifiants de ses sorts et Mi-temps demandent **au moins une capture du vrai combat**. Il faudra aussi des fixtures `.bin` et des tests, comme pour les autres messages. On vérifiera au passage ce que SniffSniffSquared et dofus3-sniffer-tui décodent déjà.
2. **La carte de la salle** : les numéros de case, ainsi que les cases noires et les obstacles. La source est à trouver : données de carte du client, DofusDB, ou à défaut un relevé fait une fois à la main dans une page de calibration. La salle ne change pas, donc on n'a besoin de cette carte qu'une seule fois.
3. **Moteur de règles** (`fight/harebourg.py`, en Python pur et entièrement testé) :
   - conversion entre numéro de case et coordonnées sur la grille isométrique ;
   - rotation inverse : pour une case visée, la case à cliquer selon la confusion courante et les ajouts au corps à corps ;
   - symétries des tours pairs et impairs ;
   - pour chaque case d'où l'on peut frapper le Comte, un verdict :
     - ✅ lève l'invulnérabilité,
     - ⚠️ atterrit dans Mi-temps,
     - ☠️ Air du Temps.

   Les règles seront validées sur le simulateur, puis en jeu.
4. **Affichage, en deux temps** :
   - **4a. Mini-carte compagnon** dans l'appli web, sur un second écran ou le téléphone. On y voit la grille, les combattants, le numéro de tour et sa parité, la confusion de chaque personnage, la case à viser pour la cible choisie et les cases mortelles en rouge. C'est simple et robuste, sans fenêtre au-dessus du jeu.
   - **4b. Vrai overlay** : une fenêtre transparente, toujours au premier plan et qui laisse passer les clics (fenêtre « layered » Windows), dessinée par-dessus la grille du jeu. Il faut caler la grille isométrique sur la fenêtre de Dofus (résolution, zoom de caméra). C'est plus fragile ; à faire seulement une fois la 4a validée.

### Risques et inconnues
- **Les guides datent souvent de Dofus 2.** Les seuils, les symétries et la limite de 10 ajouts sont à revérifier sur Dofus 3.
- **Il faut faire le donjon pour capturer** : clé, équipe niveau 200, plusieurs passages. En multicompte sur un seul PC, une capture voit toute l'équipe, ce qui est un avantage.
- **Le zoom et la caméra de Dofus 3** compliquent l'overlay superposé (4b).
- **CGU** : la lecture passive est déjà en zone grise. Un overlay purement informatif ne change pas la nature de l'outil. Il reste interdit d'automatiser la moindre action.

## Ordre proposé
1. Lot HDV (1), petit chantier.
2. Badge de quantité (2), petit chantier.
3. Overlay : identification des messages génériques de combat lors de tes prochains combats, puis la carte, le moteur de règles, la mini-carte 4a et enfin l'overlay 4b.
