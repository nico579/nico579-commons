# Chantier nico579-commons : inventaire et suivi

Briques partagées par blink2video, lidar2map, watch2notif et gpxsolar.
Chaque brique : repérée, comparée entre ses copies, puis reprise ici et
branchée dans chaque application.

## Briques

| Brique | Copies aujourd'hui | État |
|---|---|---|
| tray (icône de zone de notification) | blink2video/tray.py, lidar2map, watch2notif/notifier.py, gpxsolar | 0.1.0 publiée ; blink2video branché (branche feat/commons-tray) |
| raccourci sur le Bureau | blink2video/raccourci_bureau.py seul | à reprendre ici : les trois autres en ont besoin pour le menu commun |
| recherche de mise à jour (GitHub, cache) | blink2video/maj.py, watch2notif/update_check.py, lidar2map | à comparer ; gpxsolar n'en a pas |
| serveur web local (_serve_web.py) | lidar2map, gpxsolar, watch2notif ; blink2video/serve.py | à comparer |
| rétablissement du LD_LIBRARY_PATH | blink2video/runtime.py, lidar2map, gpxsolar | à comparer |
| écriture atomique, verrou | à relever | à inventorier |
| dossiers d'état et de sorties | à relever | à inventorier |
| démarrage automatique | à relever | à inventorier |
| instance existante, relance | à relever | à inventorier |
| workflows CI (workflow_call) | les quatre dépôts | à inventorier |

## Menu commun de l'icône, application par application

Exigence (Nico, 2026-09-28) : Ouvrir, Mettre à jour quand une version est
disponible, Redémarrer, Arrêter, Créer le raccourci, dans les quatre.

| Application | Ce qui manque |
|---|---|
| blink2video | rien : branché sur tray |
| watch2notif | Redémarrer ; « Quitter » devient Arrêter ; raccourci ; Pause, Historique et Aide restent en éléments propres |
| lidar2map | Mettre à jour (sa recherche de version existe, pas d'installation automatique : ouvrir la page de la release) ; raccourci |
| gpxsolar | toute la recherche de mise à jour ; raccourci |
