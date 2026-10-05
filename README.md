# dofustool

Outil personnel d'analyse de marché Dofus 3, par capture réseau **strictement passive**. Voir [PLAN.md](PLAN.md).

## Installation (Windows, Npcap requis)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Données statiques (non commitées) : télécharge la dernière release de `ledouxm/dofus-sqlite`
si elle a changé, puis importe items, recettes et métiers dans `data/market.sqlite`. À relancer
après chaque mise à jour du jeu.

```powershell
.\.venv\Scripts\python.exe -m dofustool.staticdata.update
```

## Utilisation

Le raccourci bureau « Dofus + dofustool » (créé par `launcher\install_shortcut.ps1`) lance
`launcher\start.ps1` : la capture démarre en arrière-plan, le launcher Ankama s'ouvre, et la
capture s'arrête d'elle-même à la fermeture de Dofus. Les réglages sont dans `dofustool\config.toml`.

La capture seule, sans lanceur (Ctrl+C pour arrêter) :

```powershell
.\.venv\Scripts\python.exe -m dofustool.capture
```

Le dashboard (crafts, tendances, fiche objet, état), ouvert dans le navigateur :

```powershell
.\.venv\Scripts\streamlit.exe run dofustool\app\main.py
```

Pour qu'il s'ouvre avec le lanceur, passe `start_dashboard` à `true` dans `config.toml`.

Le journal est dans `data\capture.log` : une ligne par relevé enregistré et par alerte.

Tests :

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Limites connues

- **Les prix moyens sont théoriques.** Ils sont lissés, en retard sur le marché et ne distinguent pas les lots. Il n'y a pas de profondeur de marché en v1.
- **Les données précises ne couvrent que les objets consultés manuellement.**
- **Les tendances sont faibles au début** pour les objets jamais consultés dans le cours du marché.
- **Les marges ignorent** le temps passé, la valeur des ressources farmées soi-même au-delà du coût d'opportunité et l'XP de métier.
- **Chaque mise à jour du jeu peut casser le décodage** jusqu'à ré-identification.
- **Un seul serveur,** Windows avec Npcap uniquement.
- **Zone grise vis-à-vis des CGU d'Ankama.** Ne pas diffuser l'outil, les archives ni les captures.
