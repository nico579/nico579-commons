# Chantier nico579-commons : inventaire et suivi

Briques partagées par blink2video, lidar2map, watch2notif et gpxsolar.
Chaque brique : repérée, comparée entre ses copies, puis reprise ici et
branchée dans chaque application.

| Brique | Copies aujourd'hui | État |
|---|---|---|
| tray (icône de zone de notification) | blink2video/tray.py, lidar2map, watch2notif/notifier.py, gpxsolar | module écrit (0.1.0), applications à brancher |
| serveur web local (_serve_web.py) | lidar2map, gpxsolar, watch2notif ; blink2video/serve.py | à comparer |
| rétablissement du LD_LIBRARY_PATH | blink2video/runtime.py, lidar2map, gpxsolar | à comparer |
| écriture atomique, verrou | à relever | à inventorier |
| dossiers d'état et de sorties | à relever | à inventorier |
| démarrage automatique | à relever | à inventorier |
| instance existante, relance | à relever | à inventorier |
| recherche et installation des mises à jour | blink2video/maj.py, watch2notif, lidar2map | à inventorier |
| raccourci sur le Bureau | blink2video/raccourci_bureau.py | à inventorier |
| workflows CI (workflow_call) | les quatre dépôts | à inventorier |
