# Chantier nico579-commons : inventaire et suivi

Briques partagées par blink2video, lidar2map, watch2notif et gpxsolar.
Méthode, pour chacune : repérer les copies, les comparer, prendre la plus
éprouvée comme référence, la reprendre ici, puis brancher les applications
une à une. Analyse faite le 2026-09-28 sur les branches publiées des quatre
dépôts (relevé par thèmes, puis comparaison des fonctions de chaque copie).

## Où en est chaque brique

| # | Brique | Copies (lignes) | Référence | État |
|---|---|---|---|---|
| 1 | Icône de zone de notification | blink2video tray.py ; lidar2map, gpxsolar (dans le fichier principal) ; watch2notif notifier.py | blink2video | module `tray` (0.1.0 ; menu identique imposé en 0.3.0) ; blink2video branché (0.14.7) ; watch2notif branché (branche feat/commons) |
| 2 | Raccourci sur le Bureau | blink2video raccourci_bureau.py (204), seul | blink2video | module `raccourci` (0.2.0) ; watch2notif branché (branche) ; les trois autres à brancher |
| 3 | Recherche de nouvelle version | blink2video maj.py ; watch2notif update_check.py (128) ; lidar2map check_update, sans cache ; gpxsolar aucune | nouvelle | module `maj` (0.2.0) ; lidar2map et gpxsolar à brancher |
| 4 | Serveur web local | lidar2map _serve_web.py (400) ⊃ gpxsolar (283) ⊃ watch2notif (215) ; blink2video serve.py, à part | lidar2map | à reprendre |
| 5 | Écriture atomique, JSON, verrou | lidar2map _atomic_files.py (200) ⊃ gpxsolar (124) ; watch2notif json_store.py (88) ; blink2video runtime.py | lidar2map | à reprendre |
| 6 | Dossiers d'état et de sorties | lidar2map _dossiers.py (175) = gpxsolar (177) ; watch2notif data_paths.py (66) ; blink2video runtime.py | lidar2map | à reprendre |
| 7 | Démarrage automatique | lidar2map _autostart.py (252) ≈ watch2notif autostart_manager.py (217) ; blink2video autostart.py (682), le plus éprouvé (service systemd, issue #35) ; gpxsolar aucun | lidar2map, enrichi de blink2video | à reprendre |
| 8 | Environnement des programmes lancés (LD_LIBRARY_PATH) | blink2video runtime.py ; lidar2map _bootstrap_runtime.py ; gpxsolar _installation.py ; trois copies identiques | lidar2map | module `environnement` (0.3.0) ; watch2notif branché (branche), le trou est comblé ; les trois autres à brancher |
| 9 | Instance, port, relance | lidar2map = gpxsolar (_instance_existante, _port_libre, _premier_port_libre, _commande_relance, _relancer_process) ; watch2notif single_instance.py (verrou, pas de relance) ; blink2video, serveur unique | lidar2map, complété de systemd et launchd | module `relance` (0.3.0), éprouvé en CI sous un vrai service systemd ; watch2notif branché (branche) ; lidar2map et gpxsolar à brancher ; instance et port restent à reprendre |
| 10 | Bootstrap des dépendances (sources) | lidar2map _bootstrap_runtime.py (872) et _bootstrap_policy.py ; gpxsolar (dans gpxsolar.py) ; blink2video runtime.py ; watch2notif aucun | lidar2map | à comparer de près |
| 11 | Page : pont window.api, parcours de dossiers, fenêtre d'instance | lidar2map = gpxsolar (web_bridge.js, browse-dir) | lidar2map | à reprendre (fichiers statiques du paquet) |
| 12 | Outillage de publication | deploy.py : blink2video (375), lidar2map (394), gpxsolar (410) ; exe_smoke : lidar2map, gpxsolar ; specs : VERSIONINFO, crochet certifi ; release.yml : corps de release et SHA-256 | à définir | workflows réutilisables (workflow_call) et scripts partagés |
| 13 | Installation des mises à jour | blink2video maj.py (1381) ; watch2notif self_update.py (1079) | à définir | le plus gros doublon, et le plus délicat (vérification des archives, systemd, launchd) : en dernier |

Traduction FR/EN : chaque application a ses dictionnaires, côté page comme
côté Python. Les modules partagés portent leurs propres libellés dans les
deux langues ; pas de brique commune de traduction à ce stade.

## Écarts relevés en chemin

- watch2notif ne rétablit pas le LD_LIBRARY_PATH d'origine sous Linux alors
  qu'il lance xdg-open et systemctl : défaut de l'issue #23 de blink2video,
  corrigé dans les trois autres. Corrigé en le branchant (brique 8, branche
  feat/commons).
- Le Redémarrer de lidar2map relance par un simple nouveau processus. Sous
  le service systemd de son démarrage automatique, systemd tue ce processus
  à la sortie de l'ancien : lidar2map s'arrête au lieu de redémarrer, comme
  blink2video dans l'issue #35 (le témoin de l'essai systemd de la CI le
  montre). Sous son agent launchd, même sort, par le groupe de processus.
  Corrigé en le branchant sur `relance` (brique 9).
- Le service systemd de watch2notif écrit ExecStart sans guillemets : un
  chemin d'installation contenant une espace le casserait. blink2video et
  lidar2map échappent chaque argument. À reprendre avec la brique 7.
- Le serveur web de watch2notif ne consulte pas Sec-Fetch-Site : il vérifie
  Origin, que les navigateurs envoient sur tout POST venu d'un autre site,
  donc pas de faille, mais une défense de moins que lidar2map et gpxsolar.
- Le verrou des builds de blink2video (requirements-build.txt) ne se
  régénère pas depuis Windows sans perdre les dépendances de macOS et Linux :
  entrées complétées à la main (voir son historique).

## Menu commun de l'icône, application par application

Exigence (Nico, 2026-09-28) : le même menu dans les quatre, sans élément
propre à l'une d'elles. Ouvrir, Mettre à jour quand une version est
disponible, Redémarrer, Arrêter, Créer le raccourci ; tout le reste passe
par la page qu'ouvre Ouvrir, l'aide comprise. Depuis la 0.3.0, le module
`tray` l'impose : les cinq actions sont obligatoires, et rien ne permet
d'en ajouter une.

| Application | Ce qui manque |
|---|---|
| blink2video | rien (0.14.7) ; son raccourci passera par le module `raccourci` |
| watch2notif | rien sur la branche feat/commons : Redémarrer et Créer le raccourci ajoutés, « Quitter » devient Arrêter ; Pause, Historique et Aide quittent le menu (la page les a, l'aide par un lien dans l'en-tête) |
| lidar2map | Mettre à jour (ouvre la page de la release) ; Créer le raccourci |
| gpxsolar | Mettre à jour (ouvre la page de la release) ; Créer le raccourci |

## Ordre proposé

1. Briques 1 à 3 dans les quatre applications (menu commun complet).
2. Brique 8 (petite, et elle comble le trou de watch2notif).
3. Briques 4, 5, 6 et 9 : lidar2map et gpxsolar en ont des copies presque
   identiques, le gain est immédiat ; watch2notif ensuite.
4. Brique 11, puis 7.
5. Briques 10 et 12.
6. Brique 13 en dernier.
