# Installe Prospection dans ce dossier : environnement Python, dépendances, données du jeu, raccourcis.
# À lancer une fois après avoir récupéré le code (voir INSTALL.md). Sans danger à relancer.
#
# Usage : powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host ""; Write-Host "ARRÊT : $text" -ForegroundColor Red; exit 1 }

Step "1/5  Python"
$python = $null
foreach ($candidate in @('py -3', 'python')) {
    try {
        $version = & cmd /c "$candidate -c ""import sys; print('%d.%d' % sys.version_info[:2])""" 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]'3.13') { $python = $candidate; break }
    } catch { }
}
if (-not $python) { Fail "Python 3.13 ou plus récent est introuvable. Installe-le (winget install --id Python.Python.3.13 -e), ferme cette fenêtre, rouvre-en une et relance." }
Write-Host "Python $version trouvé."

Step "2/5  Npcap (le pilote qui permet d'écouter le jeu)"
if (-not (Test-Path "$env:SystemRoot\System32\Npcap\wpcap.dll")) {
    Fail "Npcap n'est pas installé. Télécharge-le sur https://npcap.com/#download, installe-le avec les options par défaut, puis relance ce script."
}
Write-Host "Npcap est installé."

Step "3/5  Environnement Python et dépendances (une à deux minutes)"
if (-not (Test-Path '.venv\Scripts\python.exe')) { & cmd /c "$python -m venv .venv" }
$venv = Join-Path $root '.venv\Scripts\python.exe'
& $venv -m pip install --quiet --upgrade pip
& $venv -m pip install --quiet -e .
if ($LASTEXITCODE -ne 0) { Fail "L'installation des dépendances a échoué : copie le message ci-dessus à celui qui t'a donné l'outil." }
Write-Host "Dépendances installées."

Step "4/5  Données du jeu : objets, recettes, métiers (environ 360 Mo à télécharger)"
& $venv -m dofustool.staticdata.update
if ($LASTEXITCODE -ne 0) { Fail "Le téléchargement des données du jeu a échoué. Vérifie ta connexion et relance ce script." }

Step "5/5  Raccourcis sur le bureau"
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'launcher\install_shortcut.ps1')

Write-Host ""
Write-Host "Installation terminée." -ForegroundColor Green
Write-Host "Lance maintenant le raccourci « Dofus + Prospection » sur ton bureau : le tutoriel s'ouvre dans ton navigateur."
