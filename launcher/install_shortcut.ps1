# Crée les raccourcis « Dofus + Prospection » et « Prospection » sur le bureau.
# Usage : powershell -ExecutionPolicy Bypass -File launcher\install_shortcut.ps1
$start = Join-Path $PSScriptRoot 'start.ps1'
$link = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Dofus + Prospection.lnk'

$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
$shortcut.TargetPath = Join-Path $PSHOME 'powershell.exe'
# -ExecutionPolicy Bypass ne vaut que pour ce processus : aucun réglage système n'est modifié.
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$start`""
$shortcut.WorkingDirectory = Split-Path -Parent $PSScriptRoot
$shortcut.WindowStyle = 7  # fenêtre réduite
$shortcut.Description = 'Lance la capture passive de Prospection puis le launcher Ankama'
$shortcut.IconLocation = (Join-Path $PSScriptRoot 'dofustool.ico')
$shortcut.Save()
Write-Host "Raccourci créé : $link"

# Second raccourci : l'interface seule, sans lancer le jeu ni la capture.
$dashboard = Join-Path $PSScriptRoot 'dashboard.ps1'
$link = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Prospection.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
$shortcut.TargetPath = Join-Path $PSHOME 'powershell.exe'
$shortcut.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dashboard`""
$shortcut.WorkingDirectory = Split-Path -Parent $PSScriptRoot
$shortcut.WindowStyle = 7
$shortcut.Description = 'Ouvre Prospection sans lancer le jeu'
$shortcut.IconLocation = (Join-Path $PSScriptRoot 'dofustool.ico')
$shortcut.Save()
Write-Host "Raccourci créé : $link"
