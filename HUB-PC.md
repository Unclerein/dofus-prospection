# Héberger le hub de partage sur son propre PC

Variante gratuite de [HUB-SERVEUR.md](HUB-SERVEUR.md) : le hub tourne sur le PC de l'un des joueurs,
et **Tailscale** relie les PC de façon chiffrée, sans rien ouvrir sur sa box. Le partage ne
fonctionne que lorsque ce PC est allumé avec Prospection lancé.

## Celui qui héberge le hub

1. Installer Tailscale et s'y connecter :

   ```powershell
   winget install --id Tailscale.Tailscale -e
   ```

2. Autoriser le hub dans le pare-feu Windows, pour le réseau Tailscale seulement. Dans un
   PowerShell **ouvert en administrateur** (clic droit, « Exécuter en tant qu'administrateur ») :

   ```powershell
   New-NetFirewallRule -DisplayName "Prospection hub" -Direction Inbound -Protocol TCP -LocalPort 8610 -Action Allow -InterfaceAlias "Tailscale"
   ```

3. Dans Prospection, onglet **Config**, section « Partage avec des amis » :
   - cocher **« Ce PC héberge le hub »** ;
   - ajouter chaque ami par son pseudo : un jeton est créé pour lui ;
   - s'ajouter soi-même à la liste, puis coller son propre jeton dans « Mon jeton », avec
     l'adresse `http://127.0.0.1:8610` dans « Adresse du hub » ;
   - **Enregistrer**, puis fermer et relancer Prospection.

4. Partager son PC avec chaque ami dans Tailscale : sur
   <https://login.tailscale.com/admin/machines>, menu « … » de son PC, **Share**, et envoyer le
   lien à l'ami (documentation : <https://tailscale.com/kb/1084/sharing>).

5. Envoyer à chaque ami, en privé : **l'adresse du hub** affichée dans Config (elle commence par
   `http://100.`) et **son jeton**. Un jeton est un mot de passe : un par ami, jamais dans un
   salon public.

Le partage fonctionne tant que ce PC est allumé avec Prospection lancé ; le jeu n'a pas besoin de
tourner. PC éteint, chacun continue à utiliser l'outil de son côté, et les relevés partent au hub
dès qu'il revient.

Pour retirer un ami : le supprimer de la liste dans Config et enregistrer. Son jeton cesse de
fonctionner aussitôt. Pour retirer aussi du hub ce qu'il avait envoyé :

```powershell
.\.venv\Scripts\python.exe -m dofustool.share.hub --purge SonPseudo
```

## Chaque ami

1. Installer Tailscale, s'y connecter avec son propre compte, et accepter le lien de partage reçu :

   ```powershell
   winget install --id Tailscale.Tailscale -e
   ```

2. Dans Prospection (tutoriel de démarrage, ou onglet **Config**) : saisir son pseudo, l'adresse
   du hub et son jeton, puis enregistrer.

3. Vérifier dans l'onglet **État**, panneau « Partage » : « Synchronisé à l'instant », et la liste
   des amis avec leur dernier passage.
