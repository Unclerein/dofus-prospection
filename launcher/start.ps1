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

# 0. Mise à jour du code, si possible. Une interface restée ouverte avec l'ancien code est relancée.
$updated = & (Join-Path $PSScriptRoot 'update.ps1') -Root $root
if ($updated -contains 'updated') {
    $ui = Get-NetTCPConnection -LocalPort 8600 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ui) { Stop-Process -Id $ui.OwningProcess -Force -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 500 }
}

$cfg = & $python -c "import json, dataclasses; from dofustool import config; print(json.dumps(dataclasses.asdict(config.load())))" | ConvertFrom-Json
$gameName = $cfg.dofus_process

# Chemin du launcher Ankama introuvable (installé ailleurs que prévu) : on le cherche, et on l'enregistre.
if (-not $NoLauncher -and -not (Test-Path $cfg.ankama_path)) {
    $found = & $python -m dofustool.ankama --save | Select-Object -Last 1
    if ($LASTEXITCODE -eq 0 -and $found) {
        $cfg.ankama_path = $found
        Say "Launcher Ankama trouvé : $found (enregistré dans la configuration)."
    }
}

# 1. Capture en arrière-plan.
#    Une capture déjà en cours n'est gardée que si un autre lanceur la surveille encore. Sinon elle est
#    restée orpheline (fenêtre du lanceur fermée, plantage) : on l'arrête proprement et on repart.
function Get-Captures { Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -match 'dofustool\.capture' } }
$others = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" |
    Where-Object { $_.CommandLine -match 'start\.ps1' -and $_.ProcessId -ne $PID }
if (Get-Captures) {
    if ($others) {
        Say "Un lanceur surveille déjà une capture : rien à relancer."
        if (-not $NoLauncher -and (Test-Path $cfg.ankama_path)) { Start-Process -FilePath $cfg.ankama_path }
        exit 0
    }
    Say "Une capture tournait sans lanceur : arrêt, puis redémarrage."
    New-Item -ItemType File -Force $stop | Out-Null
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Captures) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
    Get-Captures | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
Remove-Item $ready, $stop -ErrorAction SilentlyContinue
$capture = Start-Process -FilePath $python -ArgumentList '-m', 'dofustool.capture' -WorkingDirectory $root -WindowStyle Hidden -PassThru
$deadline = (Get-Date).AddSeconds(45)
while (-not (Test-Path $ready)) {
    if ($capture.HasExited) { throw "La capture s'est arrêtée au démarrage : voir data\capture.log." }
    if ((Get-Date) -gt $deadline) { Stop-Process -Id $capture.Id -Force; throw "La capture n'est pas prête après 45 s : voir data\capture.log." }
    Start-Sleep -Milliseconds 300
}
Say "Capture prête (processus $($capture.Id))."

try {
    # 2. Launcher Ankama.
    if (-not $NoLauncher) {
        if (Test-Path $cfg.ankama_path) {
            Start-Process -FilePath $cfg.ankama_path
            Say "Launcher Ankama lancé."
        } else {
            Say "Launcher Ankama introuvable, même en le cherchant ($($cfg.ankama_path)) : indique son chemin dans l'onglet Config de l'interface, et lance le jeu à la main."
        }
    }

    # 3. Interface web (optionnelle). Elle reste ouverte après la fermeture du jeu.
    if ($cfg.start_dashboard) {
        if (Get-NetTCPConnection -LocalPort 8600 -State Listen -ErrorAction SilentlyContinue) {
            Start-Process 'http://localhost:8600'
            Say "Interface déjà lancée : http://localhost:8600"
        } else {
            Start-Process -FilePath $python -ArgumentList '-m', 'dofustool.web' -WorkingDirectory $root -WindowStyle Hidden
            Say "Interface lancée : http://localhost:8600"
        }
    }

    # 4. Attendre le jeu, puis la fin de la session.
    #    La session dure tant que le launcher Ankama est ouvert : on relance souvent le jeu depuis lui, sans
    #    repasser par ce raccourci, et la capture doit alors déjà écouter. Elle ne s'arrête que lorsque le jeu
    #    ET le launcher Ankama sont fermés. Si le launcher est introuvable, seule la fermeture du jeu compte.
    $ankamaName = if ($cfg.ankama_path) { [IO.Path]::GetFileNameWithoutExtension($cfg.ankama_path) } else { $null }
    function Test-Ankama { [bool]($ankamaName -and (Get-Process -Name $ankamaName -ErrorAction SilentlyContinue)) }
    function Test-Game { [bool](Get-Process -Name $gameName -ErrorAction SilentlyContinue) }
    Say "En attente du lancement de Dofus…"
    $deadline = (Get-Date).AddMinutes($WaitForGameMinutes)
    while (-not (Test-Game)) {
        if ($capture.HasExited) { throw "La capture s'est arrêtée : voir data\capture.log." }
        if ((Get-Date) -gt $deadline -and -not (Test-Ankama)) { Say "Dofus n'a pas été lancé : arrêt."; return }
        Start-Sleep -Seconds 2
    }
    Say "Dofus détecté. La capture s'arrêtera quand le jeu et le launcher Ankama seront fermés."
    $waiting = $false
    while ($true) {
        Start-Sleep -Seconds 3
        if ($capture.HasExited) { throw "La capture s'est arrêtée pendant le jeu : voir data\capture.log." }
        if (Test-Game) {
            if ($waiting) { Say "Dofus relancé : la capture écoutait déjà."; $waiting = $false }
            continue
        }
        if (Test-Ankama) {
            if (-not $waiting) { Say "Dofus fermé, launcher Ankama encore ouvert : la capture continue."; $waiting = $true }
            continue
        }
        # Le client redémarre parfois (mise à jour, changement de compte) : laisser un délai de grâce.
        Start-Sleep -Seconds 20
        if (-not (Test-Game) -and -not (Test-Ankama)) { break }
    }
    Say "Dofus et le launcher Ankama sont fermés."
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
    Say "Capture arrêtée."
    # Caractéristiques de base des équipements vus à l'HDV (DofusDB) : après la capture, jamais pendant.
    try { & $python -m dofustool.staticdata.effects | ForEach-Object { Say $_ } } catch { Say "DofusDB injoignable : à relancer plus tard." }
}
