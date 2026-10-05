# Crée le raccourci « Dofus + dofustool » sur le bureau.
# Usage : powershell -ExecutionPolicy Bypass -File launcher\install_shortcut.ps1
$start = Join-Path $PSScriptRoot 'start.ps1'
$link = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Dofus + dofustool.lnk'

$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
$shortcut.TargetPath = Join-Path $PSHOME 'powershell.exe'
# -ExecutionPolicy Bypass ne vaut que pour ce processus : aucun réglage système n'est modifié.
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$start`""
$shortcut.WorkingDirectory = Split-Path -Parent $PSScriptRoot
$shortcut.WindowStyle = 7  # fenêtre réduite
$shortcut.Description = 'Lance la capture passive dofustool puis le launcher Ankama'
$shortcut.Save()
Write-Host "Raccourci créé : $link"

# Second raccourci : l'interface seule, sans lancer le jeu ni la capture.
$dashboard = Join-Path $PSScriptRoot 'dashboard.ps1'
$link = Join-Path ([Environment]::GetFolderPath('Desktop')) 'dofustool.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
$shortcut.TargetPath = Join-Path $PSHOME 'powershell.exe'
$shortcut.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dashboard`""
$shortcut.WorkingDirectory = Split-Path -Parent $PSScriptRoot
$shortcut.WindowStyle = 7
$shortcut.Description = 'Ouvre l''interface dofustool sans lancer le jeu'
$shortcut.Save()
Write-Host "Raccourci créé : $link"
