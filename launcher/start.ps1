# Lanceur unique : capture passive en arrière-plan, launcher Ankama, dashboard (optionnel),
# puis arrêt propre de la capture quand Dofus se ferme.
#
# Usage : powershell -ExecutionPolicy Bypass -File launcher\start.ps1 [-NoLauncher] [-WaitForGameMinutes 30]
param(
    [switch]$NoLauncher,
    [double]$WaitForGameMinutes = 30
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$data = Join-Path $root 'data'
$ready = Join-Path $data 'capture.ready'
$stop = Join-Path $data 'capture.stop'

function Say($text) { Write-Host ("{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $text) }

if (-not (Test-Path $python)) { throw "Environnement Python introuvable : $python (voir README)." }
New-Item -ItemType Directory -Force $data | Out-Null

$cfg = & $python -c "import json, dataclasses; from dofustool import config; print(json.dumps(dataclasses.asdict(config.load())))" | ConvertFrom-Json
$gameName = $cfg.dofus_process

# 1. Capture en arrière-plan.
if (Test-Path $ready) {
    Say "Une capture semble déjà en cours ($ready existe). Si ce n'est pas le cas, supprime ce fichier et relance."
    exit 1
}
Remove-Item $stop -ErrorAction SilentlyContinue
$capture = Start-Process -FilePath $python -ArgumentList '-m', 'dofustool.capture' -WorkingDirectory $root -WindowStyle Hidden -PassThru
$deadline = (Get-Date).AddSeconds(45)
while (-not (Test-Path $ready)) {
    if ($capture.HasExited) { throw "La capture s'est arrêtée au démarrage : voir data\capture.log." }
    if ((Get-Date) -gt $deadline) { Stop-Process -Id $capture.Id -Force; throw "La capture n'est pas prête après 45 s : voir data\capture.log." }
    Start-Sleep -Milliseconds 300
}
Say "Capture prête (processus $($capture.Id))."

$dashboard = $null
try {
    # 2. Launcher Ankama.
    if (-not $NoLauncher) {
        if (Test-Path $cfg.ankama_path) {
            Start-Process -FilePath $cfg.ankama_path
            Say "Launcher Ankama lancé."
        } else {
            Say "Launcher Ankama introuvable ($($cfg.ankama_path)) : corrige ankama_path dans dofustool\config.toml, et lance le jeu à la main."
        }
    }

    # 3. Dashboard (optionnel).
    $app = Join-Path $root 'dofustool\app\main.py'
    if ($cfg.start_dashboard -and (Test-Path $app)) {
        $streamlit = Join-Path $root '.venv\Scripts\streamlit.exe'
        $dashboard = Start-Process -FilePath $streamlit -ArgumentList 'run', "`"$app`"" -WorkingDirectory $root -WindowStyle Hidden -PassThru
        Say "Dashboard lancé."
    }

    # 4. Attendre le jeu, puis sa fermeture.
    Say "En attente du lancement de Dofus…"
    $deadline = (Get-Date).AddMinutes($WaitForGameMinutes)
    while (-not (Get-Process -Name $gameName -ErrorAction SilentlyContinue)) {
        if ($capture.HasExited) { throw "La capture s'est arrêtée : voir data\capture.log." }
        if ((Get-Date) -gt $deadline) { Say "Dofus n'a pas été lancé : arrêt."; return }
        Start-Sleep -Seconds 2
    }
    Say "Dofus détecté. La capture s'arrêtera à sa fermeture."
    while ($true) {
        Start-Sleep -Seconds 3
        if ($capture.HasExited) { throw "La capture s'est arrêtée pendant le jeu : voir data\capture.log." }
        if (Get-Process -Name $gameName -ErrorAction SilentlyContinue) { continue }
        # Le client redémarre parfois (mise à jour, changement de compte) : laisser un délai de grâce.
        Start-Sleep -Seconds 20
        if (-not (Get-Process -Name $gameName -ErrorAction SilentlyContinue)) { break }
    }
    Say "Dofus fermé."
}
finally {
    # Arrêt propre : la capture surveille ce fichier et termine ses écritures avant de sortir.
    if (-not $capture.HasExited) {
        New-Item -ItemType File -Force $stop | Out-Null
        if (-not $capture.WaitForExit(20000)) {
            Say "La capture ne répond pas : arrêt forcé."
            Stop-Process -Id $capture.Id -Force
            Remove-Item $ready, $stop -ErrorAction SilentlyContinue
        }
    }
    if ($dashboard -and -not $dashboard.HasExited) { Stop-Process -Id $dashboard.Id -Force }
    Say "Capture arrêtée."
}
