# Chantier nico579-commons : inventaire et suivi

Briques partagées par blink2video, lidar2map, watch2notif et gpxsolar.
Méthode, pour chacune : repérer les copies, les comparer, prendre la plus
éprouvée comme référence, la reprendre ici, puis brancher les applications
une à une. Analyse faite le 2026-09-28 sur les branches publiées des quatre
dépôts (relevé par thèmes, puis comparaison des fonctions de chaque copie).

## Où en est chaque brique

| # | Brique | Copies (lignes) | Référence | État |
|---|---|---|---|---|
| 1 | Icône de zone de notification | blink2video tray.py ; lidar2map, gpxsolar (dans le fichier principal) ; watch2notif notifier.py | blink2video | module `tray` (0.1.0 ; menu identique imposé en 0.3.0) ; blink2video branché (0.14.7) ; watch2notif, lidar2map et gpxsolar branchés sur leurs branches feat/commons, CI verte, non publiés |
| 2 | Raccourci sur le Bureau | blink2video raccourci_bureau.py (204), seul | blink2video | module `raccourci` (0.2.0) ; watch2notif, lidar2map, gpxsolar branchés (branches) ; blink2video garde sa copie, à brancher |
| 3 | Recherche de nouvelle version | blink2video maj.py ; watch2notif update_check.py (128) ; lidar2map check_update, sans cache ; gpxsolar aucune | nouvelle | module `maj` (0.2.0) ; lidar2map et gpxsolar branchés pour le menu (branches) ; le bandeau de la page de lidar2map interroge encore GitHub à part (check_update), à faire lire au même vérificateur |
| 4 | Serveur web local | lidar2map _serve_web.py (400) ⊃ gpxsolar (283, 93 % de ressemblance pour Handler) ⊃ watch2notif (215, copie plus ancienne : sans Sec-Fetch-Site, sans réponse 500 JSON, sans garde Content-Length négatif) ; blink2video serve.py (Handler de 2 652 lignes), à part | lidar2map | module `serveweb` (0.4.0) : Handler de base (sécurité, JSON, statiques), routes à paramètres par `arguments_get`, `demarrer`, ports et instance déjà lancée, `EcouteHoteConfiance` ; à brancher sur gpxsolar, lidar2map, watch2notif ; blink2video : seul `hote_autorise` pourrait migrer |
| 5 | Écriture atomique, JSON, verrou | lidar2map _atomic_files.py (200) ⊃ gpxsolar (124), quatre fonctions identiques (lire_json, remplacer, verrou_inter_processus, chemin_part) ; ecrire_json recopié dans les deux _dossiers.py ; watch2notif json_store.py (88, verrou par fil en plus) ; blink2video runtime.py | lidar2map | module `atomique` (0.4.0) ; les primitives SQLite de lidar2map restent chez lui ; à brancher sur gpxsolar et lidar2map, puis watch2notif |
| 6 | Dossiers d'état et de sorties | lidar2map _dossiers.py (175) = gpxsolar (177), identiques aux constantes près ; watch2notif data_paths.py (66) ; blink2video runtime.py (mêmes noms de fonctions, corps différents) | lidar2map | module `dossiers` (0.4.0) : `Dossiers(application, ...)`, les constantes deviennent des paramètres ; à brancher sur gpxsolar et lidar2map, puis watch2notif |
| 7 | Démarrage automatique | lidar2map _autostart.py (252) ≈ watch2notif autostart_manager.py (217) ; blink2video autostart.py (682), le plus éprouvé (service systemd, issue #35) ; gpxsolar aucun | lidar2map, enrichi de blink2video | à reprendre |
| 8 | Environnement des programmes lancés (LD_LIBRARY_PATH) | blink2video runtime.py ; lidar2map _bootstrap_runtime.py ; gpxsolar _installation.py ; trois copies identiques | lidar2map | module `environnement` (0.3.0) ; watch2notif branché (branche), le trou est comblé. lidar2map et gpxsolar gardent leur copie : elle tourne avant que leur bootstrap ait installé les dépendances, bibliothèque comprise (à revoir avec la brique 10) |
| 9 | Instance, port, relance | lidar2map = gpxsolar (_instance_existante, _port_libre, _premier_port_libre, _commande_relance, _relancer_process) ; watch2notif single_instance.py (verrou, pas de relance) ; blink2video, serveur unique | lidar2map, complété de systemd et launchd | module `relance` (0.3.0), éprouvé en CI sous un vrai service systemd ; watch2notif, lidar2map et gpxsolar branchés (branches) ; instance et port restent à reprendre |
| 10 | Dépendances : déclaration, installation, verrou | lidar2map _bootstrap_runtime.py (899 lignes) : quatre listes codées en dur, qui divergent, plus deux listes pip dans la CI ; gpxsolar : une liste dans gpxsolar.py, plus deux dans la CI ; blink2video requirements.in/.txt (pip-compile, empreintes, complété à la main) ; watch2notif requirements.txt figé, sans empreintes | blink2video pour le format, uv pour l'outil | fait pour lidar2map (branche feat/dependances, CI verte, non publiée) ; gpxsolar et blink2video à faire ; voir « Dépendances » plus bas |
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
- lidar2map, bouton « Nouvelle instance » : sous le service systemd du
  démarrage automatique, la seconde instance naissait dans le cgroup du
  service. Qu'on arrête la première, et systemd tuait aussi la seconde, qui
  a pourtant sa propre icône. Même cause que l'issue #35 de blink2video.
  Corrigé par `relance.hors_du_service` (0.3.1, portée systemd à part,
  éprouvée en CI sous un vrai service), sur la branche feat/commons de
  lidar2map. blink2video garde encore son propre remède
  (autostart.sortir_du_service) : à brancher sur la bibliothèque après la
  brique 10, qui rendra son verrou régénérable.
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
| lidar2map | rien sur la branche feat/commons : Mettre à jour (page de la release) et Créer le raccourci ajoutés, menu traduit (il n'existait qu'en français) |
| gpxsolar | rien sur la branche feat/commons : Mettre à jour (page de la release) et Créer le raccourci ajoutés |

Reste à publier les trois : version de chaque application, sur feu vert de
Nico.

## Dépendances (brique 10)

Question de Nico (2026-09-29) : peut-on simplifier la gestion des
dépendances de lidar2map ? Oui. Constat, en y ajoutant nico579-commons :
quatre listes codées en dur dans _bootstrap_runtime.py, qui divergeaient
déjà (platformdirs manquait à l'une, pystray et platformdirs au contrôle du
mode none), plus deux listes pip dans la CI, et aucune version figée : deux
constructions du même commit à un mois d'écart n'embarquaient pas les mêmes
bibliothèques. gpxsolar a le même schéma, en plus simple.

Choix : le format de blink2video, l'outil en moins. Les dépendances directes
sont déclarées une fois (requirements.in, noms seuls), verrouillées dans un
requirements.txt avec version exacte et empreinte SHA-256 de chaque paquet,
plus une variante -build qui ajoute PyInstaller. Ce format est le plus
répandu et le plus lisible pour un projet de ce genre, et le pip d'un Python
nu l'installe : le bootstrap n'a pas besoin d'un outil de plus. Mais le
verrou de lidar2map est généré par `uv pip compile --universal`, pas par
pip-compile : celui-ci résout pour la machine où il tourne, et depuis
Windows il perd les paquets propres à macOS et Linux, d'où les entrées que
blink2video complète à la main dans son verrou. uv produit en un passage un
seul fichier valable partout, dans le même format ; blink2video pourra donc
y passer sans changer ses fichiers ni ses workflows. Écartés : `uv lock`
(uv.lock, pyproject), plus puissant mais qui exige uv au moment d'installer,
alors que le bootstrap tourne dans un Python nu ; Poetry ou PDM, plus lourds
que le besoin.

Fait pour lidar2map (branche feat/dependances, CI verte sur les trois
systèmes, non publiée) : 83 paquets verrouillés, _bootstrap_runtime.py de
899 à 674 lignes, le venv du mode sources réinstallé quand le verrou change,
un job de CI qui vérifie que les deux verrous s'installent en roues seules
pour les quatre systèmes de construction de release. Essayé pour de vrai :
bootstrap dans un dossier personnel redirigé, puis les deux suites de tests
complètes dans le venv ainsi installé, et un mini-build PyInstaller qui
embarque la bibliothèque et pystray depuis ce venv. Trois conséquences :

- l'installation est tout ou rien : un paquet sans roue pour la version de
  Python employée bloque tout, alors que osmium et numba, facultatifs,
  étaient laissés de côté. La CI garantit les roues pour Python 3.12 ;
- `--bootstrap=pip` installe le verrou dans l'environnement courant, quitte
  à changer la version de paquets déjà installés ;
- les Mac Intel gardent la dernière pile qui a des roues (numba 0.60,
  llvmlite 0.43, numpy 2.0), portée par un marqueur de requirements.in ; le
  filtre CSF y est compilé depuis ses sources, comme avant.

Fait ensuite pour gpxsolar (branche feat/dependances, CI verte, non
publiée) : 82 paquets verrouillés, même format. Son bootstrap
(_installation.py) est le jumeau de celui de lidar2map (_bootstrap_runtime.py)
et ne peut pas vivre ici : il s'exécute avant que le moindre paquet, cette
bibliothèque comprise, soit installé. Deux écarts voulus entre les jumeaux :
numba est exclu des Mac Intel chez gpxsolar (son build 1.6.2 s'en passait
déjà) alors que lidar2map le garde à 0.60 ; et py7zr, facultatif chez
gpxsolar avant le verrou, y est désormais exigé au démarrage.

Fait pour blink2video (branche feat/uv-et-bibliotheque, non publiée) : les
verrous passent de pip-compile à uv, sans blocs à la main. En les
régénérant, on a constaté que requirements-build.txt datait d'avant WebRTC :
les bundles publiés n'ont jamais embarqué aiortc, av et leurs sept voisins
(« smoketest --webrtc » du 0.14.3 : No module named 'aioice'), et la page se
repliait en silence sur MSE. Régénéré, le bundle Windows passe de 123 à
202 Mo et sait faire du WebRTC ; à décider avant de publier (les roues macOS
de av exigent macOS 14). Une garde en CI (smoketest --webrtc sur chaque
bundle) empêche le retour du silence.

0.3.2 (2026-09-29) : pystray et Pillow deviennent l'extra `tray`, la
bibliothèque seule n'exige plus rien. Seule l'icône en a besoin ; relance,
raccourci, maj et environnement n'emploient que la bibliothèque standard.
Raison : brancher autostart.sortir_du_service de blink2video sur
relance.hors_du_service en faisait, sinon, des dépendances d'exécution
imposées aux installations depuis les sources et à l'image Docker, où
l'icône est facultative. lidar2map, gpxsolar et watch2notif, qui listent déjà
pystray et Pillow eux-mêmes, n'ont rien à changer.

0.4.0 (2026-10-05) : modules `atomique`, `dossiers` et `serveweb` (briques 4, 5
et 6), et l'icône StatusNotifierItem (`tray_sni`). Sous GNOME Wayland, pystray
retombait sur son mode X11 : l'icône s'affichait mais le clic droit n'ouvrait
aucun menu. `tray.Tray` parle maintenant directement le protocole
StatusNotifierItem et dbusmenu sur le bus de session (jeepney, en Python pur,
extra `tray` sous Linux) quand un hôte est présent, et garde pystray sinon.
Vérifié sur un vrai bus D-Bus (CI) et sous un vrai GNOME Shell 50.1 avec
l'extension AppIndicator d'Ubuntu (VM Ubuntu 26.04) : l'icône s'inscrit et
l'hôte lit son menu sans erreur. Reste à voir, dans une session ouverte, que
l'icône s'affiche et que le clic droit ouvre le menu.

0.4.1 (2026-10-06) : le commun couvre maintenant ce que faisaient les copies des projets,
sans nouvelle fonction publique : `atomique.lire_json(..., tolerer_corrompu=)` et
`atomique.ecrire_json(..., indent=)` (watch2notif lit sa config sans la remplacer
quand elle est corrompue, et écrit ses états en JSON compact), `maj.Verificateur`
conserve les fichiers de la release (nom, URL, taille, empreinte, état) et écarte
brouillons et préversions, `Dossiers.copier_si_absent` copie aussi un dossier.
Raccourci Linux corrigé : Bureau lu dans XDG_DESKTOP_DIR, Exec conforme à la
spécification Desktop Entry (sans `sh -c`), fichier marqué de confiance
(`gio set metadata::trusted true`) pour que GNOME ne le déclare plus
« Untrusted Desktop File ». Branché sur gpxsolar, lidar2map et watch2notif,
qui perdent leurs copies locales.

0.4.2 (2026-10-06) : la logique de mise à jour devient commune. `maj_archive`
fusionne ce que blink2video (maj.py) et watch2notif (self_update.py) faisaient
chacun de leur côté : choix de l'unique fichier finalisé de la release, URL liée
au dépôt et au format de tag, redirections limitées à GitHub, téléchargement
publié seulement après taille et SHA-256, extraction refusant traversée, liens
vers l'extérieur, noms non portables, collisions de casse et bombes de
décompression. `maj.Verificateur` gagne `cache=` (la dernière réponse survit au
redémarrage, hors ligne comprise) et `verifie_a`. `serveweb.Handler.hote_autorise`
reprend la garde complète de blink2video : trusted_host en liste ou en sous-réseaux
CIDR, Origin comparée aux mêmes entrées, trace des refus (`_journaliser_acces_refuse`).
Reste propre à chaque application : la permutation de l'installation avec retour
arrière et la relance (dossier échangé chez blink2video, assistant externe chez
watch2notif), qui dépendent de la façon dont l'application est livrée.

0.4.3 (2026-10-06) : `maj_install`, l'installation automatique d'une version
téléchargée avec retour arrière, tirée de self_update.py de watch2notif et
généralisée (nom, données conservées, service systemd, agent launchd, arguments
de relance, style de fenêtre). Le même protocole pour toutes les applications
livrées en dossier PyInstaller ou en .app : préparation (maj_archive, auto-test),
assistant externe prêt puis feu vert, échange des dossiers après la fin de
l'application, relance vérifiée, retour arrière si la nouvelle version ne tient pas.
Testé pour de vrai sur le système de la CI (PowerShell ou sh), dont le retour
arrière. watch2notif en est le premier client (self_update.py : 1079 à 148 lignes).

0.4.4 (2026-10-06) : `demarrage`, le démarrage automatique avec la session, que
blink2video (autostart.py), lidar2map (_autostart.py) et watch2notif
(autostart_manager.py) écrivaient chacun de leur côté. Une Entree (nom, commande,
dossier, description, label macOS) ; activer, desactiver, est_actif, installees,
apercu. Le meilleur des trois : arguments systemd entre guillemets avec % et $
doublés, environnement de session de « systemctl --user » déduit de /run/user/<uid>,
refus de systemd rapportés au lieu d'être annoncés comme un succès, agent launchd
écrit par plistlib (toujours valide), raccourci Windows par raccourci.creer,
migration du vieux .vbs. Dérive trouvée en comparant : watch2notif n'échappait ni
les arguments systemd ni le XML de l'agent macOS.

0.4.5 (2026-10-06) : `maj_install` gagne la seconde stratégie de remplacement, celle de
blink2video : la NOUVELLE version, lancée depuis son dossier de préparation, arrête
l'ancienne, copie ses fichiers un à un à la place des anciens (`permuter`) et relance
(`finaliser`). Elle ne demande aucun droit sur le dossier parent, sait relancer plusieurs
processus (l'application fournit `noter` et `relancer`), et un marqueur écrit avant la
première modification permet de refuser de purger la seule sauvegarde après un arrêt
brutal (`nettoyer_restes`, `RestaurationIncomplete`, `reservation` par verrou de l'OS).
Tests repris de blink2video. Les deux stratégies (échange de dossier par assistant
externe, copie élément par élément) partagent `preparer`.

0.4.6 (2026-10-07) : `amorcage`, l'amorçage des dépendances en mode sources : modes
auto, force, pip et none, venv `~/.<nom>/venv` remis au verrou à empreintes quand il
change, garde contre un environnement conda ou venv déjà actif, installation sans
verrou (blink2video en Python 3.8), image Docker (`reutiliser_environnement`), messages
en français et en anglais. Fusion des trois versions de lidar2map, gpxsolar et
blink2video. Il tourne avant l'installation de ce paquet : chaque application en garde
une copie octet pour octet (`_amorcage.py`), comparée au paquet installé par un test.

0.4.7 (2026-10-07) : `serveweb.Handler.send_json` répond en UTF-8 lisible (accents en
clair, `ensure_ascii=False`) et vide le tampon aussitôt, comme le faisait blink2video
seul : son override disparaît. Les autres applications (watch2notif, qui appelle
`send_json`) changent d'octets mais pas de sens : la page décode les deux de la même façon.

0.4.8 (2026-10-07) : le bouton « ⚙ Réglages… » commun (`reglages.js`, servi à
`/nico579-reglages.js`) : la version et « Vérifier les mises à jour », avec l'installation
proposée si une version est là, et une place pour les lignes propres à chaque application
(`window.nico579Reglages.ajouter`). Même affordance que blink2video, qui l'avait déjà. Route
`POST /api/maj-verifier` dans `maj_install.routes` (deux clics rapprochés n'en font qu'une
question à GitHub), et `/api/maj` donne désormais `version_locale` et `verifie_a`.
`serveweb.fichiers_manquants()` : les exécutables doivent embarquer les données du paquet
(`collect_data_files("nico579_commons")`), ce que ni lidar2map, ni gpxsolar, ni watch2notif ne
faisaient : leur bandeau de mise à jour réclamait un fichier absent (404). `relance.relancer`
donne par défaut `stdout` et `stderr` à DEVNULL (un exécutable sans console relancé par
« Redémarrer » plantait au premier print).

0.4.9 (2026-10-07) : `Verificateur.veiller` ne redemande pas à GitHub une réponse encore
fraîche (celle du cache disque d'un démarrage récent) : il attend ce qu'il reste de l'heure.
Une réponse datée du futur (horloge revenue en arrière) est tenue pour périmée. C'est ce que
faisait seule la boucle de blink2video (`veiller_sur_les_versions`), qui disparaît ; les trois
autres applications donnent maintenant un `cache=` à leur `Verificateur`, et les quatre
interrogent GitHub de la même façon : au plus une fois par heure, redémarrages compris.

## Ordre proposé

1. Briques 1 à 3 dans les quatre applications (menu commun complet) :
   fait sur branches, à publier.
2. Brique 8 (petite, et elle comble le trou de watch2notif) : fait pour
   watch2notif.
3. Brique 10 : fait pour lidar2map, gpxsolar et blink2video (uv), sur
   branches ; reste à publier.
4. Briques 4, 5, 6 et 9 : lidar2map et gpxsolar en ont des copies presque
   identiques, le gain est immédiat ; watch2notif ensuite.
5. Brique 11, puis 7.
6. Brique 12.
7. Brique 13 en dernier.

0.4.10 (2026-10-07) : le bouton Réglages suit la langue de la page. Son libellé venait du serveur,
lu une fois au chargement : basculer FR/EN ne le changeait pas. `/api/maj` donne désormais les
textes dans toutes les langues (`libelles_par_langue`) et `reglages.js` suit l'attribut `lang` de
<html>, que les applications posent quand on bascule.
