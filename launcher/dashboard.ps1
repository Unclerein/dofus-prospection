# Ouvre l'interface Prospection seule, sans capture ni jeu.
# Le serveur tourne en arrière-plan, sans fenêtre, et reste actif jusqu'à l'arrêt de la session Windows.
#
# Usage : powershell -ExecutionPolicy Bypass -File launcher\dashboard.ps1 [-Stop]
param([switch]$Stop)

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$port = 8600
$url = "http://localhost:$port"

$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1

if ($Stop) {
    if ($listener) { Stop-Process -Id $listener.OwningProcess -Force; Write-Host "Interface arrêtée." }
    else { Write-Host "L'interface ne tournait pas." }
    exit 0
}

if (-not $listener) {
    & (Join-Path $PSScriptRoot 'update.ps1') -Root $root | Out-Null
    if (-not (Test-Path $python)) { Write-Host "Environnement Python introuvable : $python"; exit 1 }
    Start-Process -FilePath $python -ArgumentList '-m', 'dofustool.web', '--no-browser' -WorkingDirectory $root -WindowStyle Hidden
    # Attendre que le serveur réponde avant d'ouvrir le navigateur.
    foreach ($i in 1..40) {
        Start-Sleep -Milliseconds 250
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { break }
    }
}
Start-Process $url
