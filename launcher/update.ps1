# Met le code à jour depuis le dépôt, si c'est possible sans rien casser. Appelé par les lanceurs.
# Ne bloque jamais le lancement : hors ligne, dépôt modifié à la main ou git absent, on continue tel quel.
param([string]$Root = (Split-Path -Parent $PSScriptRoot))

$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git -or -not (Test-Path (Join-Path $Root '.git'))) { return }
$python = Join-Path $Root '.venv\Scripts\python.exe'
try {
    # Dépôt devenu privé : le hub donne l'accès aux joueurs qu'il connaît (voir dofustool/share/repo.py).
    if (Test-Path $python) { & $python -m dofustool.share.repo 2>$null | ForEach-Object { Write-Host $_ } }
    $before = & git -C $Root rev-parse HEAD 2>$null
    # Abandonne vite si le réseau ne répond pas ; --ff-only refuse toute fusion hasardeuse.
    & git -C $Root -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=8 pull --ff-only --quiet 2>$null | Out-Null
    $after = & git -C $Root rev-parse HEAD 2>$null
    if ($before -and $after -and $before -ne $after) {
        Write-Host "Prospection a été mis à jour."
        'updated'  # signalé au lanceur, qui relance l'interface si elle tournait avec l'ancien code
        $changed = & git -C $Root diff --name-only $before $after 2>$null
        if ($changed -contains 'pyproject.toml' -and (Test-Path $python)) { & $python -m pip install --quiet -e $Root | Out-Null }
    }
} catch { }
