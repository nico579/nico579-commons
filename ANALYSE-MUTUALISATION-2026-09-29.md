# Mutualisation des quatre projets : analyse et propositions

Rapport du 2026-09-29, demandé par Nico : « une analyse complète que tu me
proposes, je déciderai des priorités ». Aucune décision n'est prise ici ; les
choix à faire sont réunis au § 7.

## 0. En bref

- La bibliothèque publiée (0.3.2) porte déjà cinq briques : icône et menu,
  raccourci, recherche de version, relance, environnement. Les quatre
  applications utilisent l'icône et la relance ; le raccourci et la recherche
  de version ne sont pas encore partout (§ 2).
- Reste, d'après le relevé, treize familles de code présentes en plusieurs
  exemplaires, soit environ 9 000 lignes sur 81 000 de Python (hors tests). Six
  sont petites et sûres (atomique, dossiers, instance et port, version de la
  page, langue, colle du menu) ; quatre sont moyennes (serveur web, démarrage
  automatique, notifications, page) ; trois sont grosses et délicates
  (bootstrap, installation des mises à jour, outillage de publication).
- Le serveur web est la famille qui compte le plus, moins pour ses lignes que
  pour sa sécurité : quatre copies qui divergent, dont une (watch2notif) sans
  plusieurs durcissements faits ailleurs. Et la meilleure version de la
  logique de provenance des requêtes n'est pas celle que j'ai prise pour
  référence : c'est celle de blink2video (§ 3.1, décision 1).
- Mon ordre proposé : d'abord les familles petites et sûres, ensuite le
  démarrage automatique, le bootstrap, et en dernier l'installation des mises
  à jour (§ 5).

## 1. Méthode

Relevé du 2026-09-29 sur les branches par défaut des quatre dépôts
(blink2video 6e2d9b3 = v0.15.0, lidar2map 92ef05c = v1.56.0, gpxsolar 02104d2 =
v1.7.1, watch2notif 8aad71e = v0.3.0), sans rien modifier.

- Python : chaque fichier (hors tests) analysé par `ast` ; chaque fonction,
  classe et méthode comparée aux autres dépôts par nom, par structure (arbre
  syntaxique sans docstring ni numéro de ligne) et par ressemblance ligne à
  ligne (difflib).
- Fichiers de même nom, workflows GitHub, spécifications PyInstaller, scripts de
  construction, pages (JS, CSS, HTML) comparés de la même façon.
- Lecture des copies pour juger : une ressemblance mesure la forme, pas
  l'usage.

Volumes : blink2video 35 fichiers et 23 600 lignes, lidar2map 145 fichiers et
46 200 lignes (le gros est du métier : relief, tuiles, OSM), gpxsolar 10 fichiers
et 8 000 lignes, watch2notif 19 fichiers et 3 600 lignes. 36 groupes de
définitions sont strictement identiques entre dépôts (638 lignes en double) ;
les familles « presque identiques » (70 à 95 %) pèsent bien plus.

## 2. Ce qui est déjà mutualisé

| Brique | Module | Publié | Branchée dans |
|---|---|---|---|
| Icône et menu identique | `tray` | 0.3.0 | les quatre |
| Raccourci sur le Bureau | `raccourci` | 0.2.0 | lidar2map, gpxsolar, watch2notif (blink2video garde sa copie de 204 lignes) |
| Recherche de nouvelle version (menu) | `maj` | 0.2.0 | lidar2map, gpxsolar (watch2notif et blink2video gardent la leur, § 3.5) |
| Relance, sortie du service systemd | `relance` | 0.3.1 | les quatre (blink2video depuis la 0.15.0) |
| Environnement des programmes lancés | `environnement` | 0.3.0 | watch2notif ; les autres gardent leur copie (elle tourne avant le bootstrap) |
| pystray et Pillow en extra | (packaging) | 0.3.2 | sans objet |

Sur `main` de la bibliothèque, non taguée et non publiée : `atomique`,
`dossiers` et `serveweb` (0.4.0), écrits avant ta consigne de ne pas coder.
Aucune application n'en dépend, la CI de la bibliothèque est verte (107 tests,
dont Python 3.8 sous Windows). On peut les garder, les adapter (décisions 1 et
2) ou les retirer par un commit de retrait.

## 3. Constats par famille

Sigles : b2v blink2video, l2m lidar2map, gpx gpxsolar, w2n watch2notif.
Tailles : S (une demi-journée), M (une journée), L (plusieurs jours), XL
(une à deux semaines) de travail soigné, tests compris.

### 3.1 Serveur web local (brique 4) : gain moyen, sécurité forte, taille M

- Copies : l2m `_serve_web.py` 400 lignes, gpx 283, w2n 215, et le `Handler` de
  b2v `serve.py`, 2 652 lignes, à part.
- Mesure : `Handler` gpx~l2m 93 %, gpx~w2n 71 %, l2m~w2n 66 % ; `_refuser`,
  `send_json`, `send_static` identiques dans les trois ; `do_POST` de gpx et
  l2m identique.
- Défauts de la copie de w2n (ni Sec-Fetch-Site, ni réponse 500 en JSON quand
  une route lève, ni garde sur un Content-Length négatif) : elle les reçoit
  en se branchant.
- Point de sécurité : `hote_autorise` de b2v est un sur-ensemble de celui de
  l2m. Il admet plusieurs hôtes de confiance et des sous-réseaux CIDR, et
  journalise les accès refusés. Celui de l2m, repris dans `serveweb`, n'a
  qu'un hôte et pas de journal. Une bibliothèque commune devrait prendre la
  version la plus complète, ce que j'avais raté en prenant l2m comme
  référence (décision 1).
- Contrainte : en mode sources, le module ne peut être importé qu'après le
  bootstrap (lidar2map l'importe déjà paresseusement).
- Risque : moyen (sécurité), atténué par les tests des applications, qui
  servent de spécification.

### 3.2 Écriture atomique, JSON, verrou (brique 5) : gain faible, taille S

- Copies : l2m `_atomic_files.py` 200 lignes ⊃ gpx 124 (quatre fonctions
  identiques : `lire_json`, `remplacer`, `verrou_inter_processus`,
  `chemin_part`) ; `ecrire_json` recopié dans les deux `_dossiers.py` ; w2n
  `json_store.py` 88 lignes (verrou par fil en plus) ; b2v écrit
  autrement (`runtime`, `merge_daily`).
- Gain : environ 150 lignes, et une seule implémentation des pièges déjà payés
  (refus Windows passager, BOM, fichier corrompu qui efface l'historique).
- Reste chez lidar2map : les primitives SQLite. Chez blink2video : ses
  écritures, trop nombreuses pour un gain qui justifie le remaniement.

### 3.3 Dossiers d'état et de sorties (brique 6) : gain faible, taille S

- Copies : l2m `_dossiers.py` 175 = gpx 177 (113 lignes identiques par
  l'arbre syntaxique, seules les constantes changent) ; w2n `data_paths.py`
  66 ; b2v `runtime.py` (mêmes noms de fonctions, corps différents).
- Gain : environ 250 lignes. `Dossiers(application, ...)` fait des constantes
  ses paramètres.
- Risque : faible pour l2m et gpx, à surveiller pour b2v : ce sont les chemins
  de la production de l'utilisateur. Je proposerais de ne pas y toucher sans
  test qui prouve les mêmes chemins.

### 3.4 Instance, port, console (reste de la brique 9) : gain faible, taille S

- Copies dans `lidar2map.py` et `gpxsolar.py` : `_terminal_interactif` et
  `_console_windows_visible` identiques ; `_premier_port_libre` 82 %,
  `_langue_console` 74 %, `_port_libre` 61 %, `_demarrer_nouvelle_instance`
  59 % ; w2n a `single_instance.py` (verrou, autre mécanisme) ; b2v impose un
  serveur unique.
- Port, instance existante : déjà dans `serveweb` (0.4.0). Reste la console.

### 3.5 Vérification de version côté page (reste de la brique 3) : taille S

- l2m et gpx utilisent `maj.Verificateur` pour le menu ; le bandeau de la
  page de l2m interroge GitHub à part. w2n `update_check.py` (128 lignes) et
  b2v `maj.disponible` refont la même chose, avec `_numeros` identique.
- Prérequis de la famille 3.8.

### 3.6 Langue et traduction Python : gain faible, taille S

- Quatre façons de trouver FR ou EN : b2v lit la langue de la dernière page
  chargée, w2n appelle `locale.getdefaultlocale()` (obsolète depuis
  Python 3.11), l2m et gpx `_langue_console` (préférence, puis
  `locale.getlocale()`, puis `$LANG`).
- Une petite fonction commune règle le cas obsolète de w2n et uniformise.

### 3.7 Démarrage automatique (brique 7) : gain moyen, risque élevé, taille L

- Copies : l2m `_autostart.py` 260, w2n `autostart_manager.py` 217, b2v
  `autostart.py` 673, gpx aucune.
- Mesure : l2m et w2n partagent `enable`, `disable`, `is_enabled` à
  l'identique, `_enable_mac` 89 %, `_enable_linux` 84 %, `_enable_windows`
  77 %.
- b2v est un sur-ensemble éprouvé : échappement des arguments d'`ExecStart`
  (audit B07), environnement de l'utilisateur pour systemctl, sortie du
  cgroup, politique des étiquettes launchd, unités par verbe, liste des
  services. w2n écrit encore `ExecStart` sans guillemets (bug latent).
- Bénéfices : correction chez w2n (et peut-être l2m), autostart possible pour
  gpxsolar (décision 5).
- Risque : b2v a cinq fichiers de tests dédiés, et un service systemd en
  service chez un utilisateur (Markus).

### 3.8 Bootstrap et installation des dépendances (brique 10, suite) : taille L

- Copies : l2m `_bootstrap_runtime.py` 686 lignes, gpx `_installation.py` 316
  plus les fonctions de bootstrap de `gpxsolar.py`, b2v `runtime.bootstrap`
  (sans verrou : il installe des noms non figés).
- Ce sont des jumeaux à 57 lignes strictement identiques
  (`retablir_environnement_systeme`, `dependances_directes`,
  `dependances_absentes`, `relancer_dans_venv`), qui ne peuvent pas vivre dans
  la bibliothèque : ils tournent avant que celle-ci soit installée.
- Proposition : un « démarreur » minimal, d'environ 60 lignes par application
  (créer le venv, `pip install --require-hashes -r requirements.txt`,
  relancer), le reste passant dans la bibliothèque après relance. Effet de
  bord utile : le mode sources de b2v installerait enfin le verrou (et donc
  aiortc, absent aujourd'hui).
- Risque : moyen à élevé, c'est le chemin de démarrage de chaque lancement
  depuis les sources.

### 3.9 Installation des mises à jour (brique 13) : gain fort, risque maximal, taille XL

- b2v `maj.py` 1 407 lignes et w2n `self_update.py` 1 079 lignes : même
  chaîne (choisir l'archive, la télécharger, vérifier le SHA-256, l'extraire
  avec contrôle des liens et des chemins, permuter les dossiers, relancer),
  deux implémentations aux architectures différentes (permutation chez b2v,
  transaction préparée avec agent launchd chez w2n). l2m et gpx ne
  s'installent pas eux-mêmes : ils ouvrent la page de la release.
- Gain : environ 1 000 lignes, un seul code à auditer pour la partie
  sensible (listes d'URL autorisées, liens symboliques), et la mise à jour
  automatique offerte à l2m et gpx si tu la veux (décision 4).
- Risque : le plus élevé (un défaut peut laisser une installation
  inutilisable ; Windows, macOS, systemd). À traiter en dernier, après une
  comparaison écrite des deux architectures.

### 3.10 Notifications de bureau : gain faible, taille M

- b2v `runtime.toast` (PowerShell, osascript, notify-send : aucune
  dépendance) contre w2n `notify_backend` (win11toast, pync, plyer : clic qui
  ouvre le lien). l2m et gpx n'en ont pas (une fin de traitement long
  pourrait en émettre : décision 6).
- Il faut choisir une technique avant de fusionner deux comportements
  différents, dont le clic.

### 3.11 Outillage de publication (brique 12) : gain moyen, taille M à L

- `deploy.py` : b2v 375 lignes, gpx 410, l2m 394. Fonctions identiques :
  `read_code_version`, `_sha_remote`, `commit_and_push` (les trois),
  `verifier_depot`, `_remote_officiel`, `compute_diff`,
  `verifier_fichiers_nouveaux` (gpx = l2m) ; `watch_release`, `run`,
  `_publier_tag` à 76 à 91 %. Environ 250 lignes communes. l2m et gpx ont en
  plus une mécanique propre (clone temporaire, patch local ou cloud).
- Workflows : `cross_platform.yml` l2m = gpx à 96 %, `release.yml` l2m~gpx à
  71 %, le reste à 12 à 40 %. Étapes communes : « Verrous des dépendances »
  (trois dépôts), « Installer uv » (trois), la forme du job « Release (build 3
  OS) » (quatre). Proposition : des workflows réutilisables (`workflow_call`)
  pour le contrôle des verrous et la publication (archives, SHA-256, notes),
  les constructions restant propres à chaque application.
- Spécifications PyInstaller : 7 à 55 % de ressemblance, `hiddenimports`
  propres à chaque application : à ne pas partager. Seuls petits morceaux
  communs (bloc VERSIONINFO dans trois spécifications, crochet certifi, signature
  macOS) pourraient aller dans un utilitaire, comme le fait déjà
  `build_support.py` de b2v.
- Scripts `setup_build_*` : l2m~gpx 59 à 69 %, minces depuis le verrou.

### 3.12 Page (brique 11) : gain faible, deux applications seulement, taille S à M

- Seules l2m et gpx partagent du JavaScript : `t`, `tf`, `detectLang` à 100
  %, `applyI18n` 87 %, la fenêtre de parcours de dossiers 100 %, la fenêtre
  « nouvelle instance » 96 à 100 % ; environ vingt-huit fonctions de même
  nom. b2v et w2n ont des pages sans rapport.
- CSS : 0 à 18 % de ressemblance ; onze variables de thème communes
  (`--bg`, `--fg`, `--ac`...), à l'exclusion de b2v.
- Proposition : ne partager que ce que deux applications recopient déjà mot pour
  mot, en fichiers statiques que `serveweb` sert.

### 3.13 Colle du menu d'icône : gain faible, taille S

- `_actions_tray` : gpx~l2m 85 %, w2n 19 %, trente à cinquante lignes chacun
  pour brancher les cinq actions imposées. Un constructeur commun en
  supprimerait une centaine, à faire avec 3.5.

## 4. Ce que je déconseille de mutualiser

- Le TLS de Blink (`blink_auth`, garde-fou de `AGENTS.md`) : aucune
  modification sans ton autorisation explicite. Celui de lidar2map
  (`_bootstrap_tls`) répond à un autre besoin.
- Le code métier, la page de b2v et de w2n, le CSS, les `hiddenimports`.
- Les journaux : seul l2m a un vrai module (`TeeLogger` 232 lignes, masquage des
  secrets). Rien à dédoublonner, seulement du bon code à offrir aux autres,
  plus tard.
- Les écritures de b2v (registre, JSON) et son `runtime.py` de 2 597 lignes :
  trop de points d'appel pour le gain.

## 5. Contraintes transversales

- Le mode sources : la bibliothèque n'existe qu'après le bootstrap. Un module
  utilisé plus tôt ne peut pas y vivre (d'où le démarreur du § 3.8).
- PyInstaller : les imports doivent rester détectables ; les imports
  paresseux demandent un `hiddenimports`.
- Python 3.8 : l'édition Windows 7 de b2v. La CI de la bibliothèque le teste
  déjà sous Windows.
- Bibliothèque en bibliothèque standard seule ; pystray et Pillow restent un
  extra.
- Versions : les applications épinglent `>=0.3.x,<0.4` ; chaque mineure de la
  bibliothèque oblige à relever la borne et à régénérer le verrou
  (`--refresh-package` quand PyPI vient de publier). Décision 7.
- Les fusions vers `main` des applications passent par une avance rapide que
  tu lances toi-même (`!`), puis j'étiquette et je suis la release.
- Les tests des applications servent de spécification : elles doivent passer
  telles quelles (sauf les points d'accroche que l'extraction déplace).
- Aucun test ne touche à l'état réel de tes applications.

## 6. Ordres possibles

Estimation du gain : lignes supprimées dans les applications, moins le code
ajouté à la bibliothèque.

| Scénario | Contenu | Gain net estimé | Risque | Releases |
|---|---|---|---|---|
| A. Sûr | 3.1 à 3.6 et 3.13 | 1 200 à 1 500 lignes, sécurité du serveur uniformisée | faible à moyen | bibliothèque 0.4, puis gpx, l2m, w2n ; b2v seulement pour `hote_autorise` |
| B. Équilibré (mon choix) | A + démarrage automatique (3.7) + notifications (3.10) + deploy.py partagé (3.11) | +1 000 lignes, correction du guillemetage de w2n, autostart possible pour gpx | moyen | une vague de plus, b2v concerné |
| C. Complet | B + démarreur (3.8) + installation des mises à jour (3.9) + workflows réutilisables (3.11) | +1 500 lignes, mise à jour automatique possible partout | élevé | plusieurs semaines |

Ordre que je recommande à l'intérieur de A : atomique, dossiers, serveweb
(déjà écrits, à adapter selon la décision 1), puis instance et port, langue,
version de la page, colle du menu. Chaque étape branche d'abord gpxsolar
(le plus petit), puis lidar2map, puis watch2notif, et laisse blink2video
pour la fin.

## 7. Décisions à prendre

1. **Sécurité du serveur** : la version commune adopte-t-elle la logique de
   b2v (plusieurs hôtes de confiance, sous-réseaux CIDR, journal des
   refus) pour les quatre applications, ou garde-t-elle un seul hôte de
   confiance comme l2m, gpx et w2n ? Les sous-réseaux CIDR élargissent
   l'exposition si on les règle mal, mais restent un réglage explicite.
2. **Les modules déjà sur `main`** (0.4.0, non taguée) : les garder, les
   adapter à la décision 1, ou les retirer.
3. **Le périmètre de la première vague** : A, B ou C, ou un autre mélange.
4. **Mise à jour automatique pour lidar2map et gpxsolar** (aujourd'hui, la
   page de la release) : oui ou non. Elle conditionne la famille 3.9.
5. **Démarrage automatique pour gpxsolar** : oui ou non (3.7).
6. **Notifications de fin de traitement pour lidar2map et gpxsolar** : oui ou
   non, et quelle technique (sans dépendance comme b2v, ou avec clic comme w2n).
7. **Version de la bibliothèque** : garder des bornes serrées dans chaque
   application (`<0.4`, ce qui oblige à les relever à chaque mineure) ou
   les élargir à `<1`.
8. **Rythme des releases** : une par vague et par application, ou des vagues
   groupées pour limiter le nombre de publications.
9. **`deploy.py`** : un module partagé (les dépôts l'importent, la
   bibliothèque installée dans ton Python de développement), ou trois
   copies avec un test de dérive.

## 8. Annexe : ce qui est strictement identique aujourd'hui

Groupes de définitions au même arbre syntaxique entre dépôts, par nombre de
lignes en double : `_dossiers.py` gpx = l2m 113 ; `deploy.py` gpx = l2m 88 ;
`deploy.py` les trois 74 ; `_atomic_files.py` gpx = l2m 71 ; `_serve_web.py`
gpx = l2m 66 ; `_installation.py` gpx = `_bootstrap_runtime.py` l2m 57 ;
`_serve_web.py` gpx = l2m = w2n 48 ; `serve.py` b2v = les trois `_serve_web.py`
39 (`_refuser`) ; autostart l2m = w2n 35 ; `lidar2map.py` = `gpxsolar.py` 25 ;
`maj.py` b2v = `update_check.py` w2n 9.

Le relevé est reproductible : `python outils/inventaire_communs.py` le refait
en une minute (analyse `ast`, difflib), en lecture seule, sur les dépôts voisins
de celui de la bibliothèque (`--racine` et `--depot sigle=chemin` pour d'autres
emplacements). Vérifié le 2026-09-29 : mêmes volumes, mêmes 76 définitions de
même nom, mêmes 36 groupes et 638 lignes en double que ceux de ce rapport.

## 9. Décisions de Nico (2026-09-29) et plan qui en découle

| # | Décision | Conséquence |
|---|---|---|
| 1 | Logique de sécurité de blink2video pour les quatre « si c'est la meilleure solution (à améliorer ?) » | Oui, en prenant le meilleur des quatre, pas seulement celle de blink2video (voir ci-dessous) |
| 2 | Garder les modules 0.4.0 déjà sur `main` | Ils servent de base ; `serveweb` sera complété (décision 1) |
| 3 | Les quatre projets | blink2video est branché comme les autres, avec les précautions du § 3.3 pour ses chemins d'état |
| 4 | Mise à jour automatique pour lidar2map et gpxsolar : oui | La famille 3.9 (installation des mises à jour) est retenue, en dernier |
| 5 | Pas de démarrage automatique pour gpxsolar ni pour lidar2map (« usage ponctuel, pas de la surveillance ») | Le partage du démarrage automatique (3.7) ne concerne que blink2video et watch2notif. À clarifier : lidar2map a déjà le sien (voir ci-dessous) |
| 6 | Pas de notifications pour lidar2map ni gpxsolar (l'ouverture du dossier des résultats suffit) | La famille 3.10 est abandonnée |
| 7 | Bornes de version élargies | Les applications déclarent `nico579-commons>=x,<1` ; plus de relèvement à chaque mineure |
| 8 | Releases groupées | Une release par application à la fin d'une série de vagues, pas une par vague |
| 9 | `deploy.py` partagé | Un module de la bibliothèque, importé par un `deploy.py` mince dans chaque dépôt |

Le codage se fait par vagues, selon le quota disponible ; rien n'est engagé
avant que Nico le décide.

### Sécurité du serveur : « le meilleur, et à améliorer »

Mesure faite pour la décision 1 (2026-09-29). Ce que chaque application a :

- blink2video : plusieurs hôtes de confiance et sous-réseaux CIDR ; journal des
  accès refusés ; jeton par processus exigé sur l'API (`X-Blink-Token`) ;
  en-têtes `Content-Security-Policy` (dont `frame-ancestors`),
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`. Pas de
  contrôle `Sec-Fetch-Site` (le jeton en tient lieu).
- lidar2map et gpxsolar : un hôte de confiance, proxy local par variable
  d'environnement, `Sec-Fetch-Site`. Ni jeton, ni CSP, ni en-têtes.
- watch2notif : le noyau seulement (Host, client, Origin). Ni `Sec-Fetch-Site`, ni
  en-têtes.

La version commune doit donc réunir : les hôtes et sous-réseaux de confiance et
le journal de blink2video, `Sec-Fetch-Site` de lidar2map, et les gardes de
chemin. Améliorations proposées, dans l'ordre où je les mettrais :

1. Les trois en-têtes de blink2video sur toutes les réponses des quatre
   applications : gratuit, aucune page ne change.
2. Le jeton par processus en option de la classe de base, activé pour les
   quatre : il protège des autres processus et des autres utilisateurs de la
   machine, ce que Host, Origin et Sec-Fetch-Site ne font pas. Coût : la page
   doit l'envoyer (un seul point à modifier, le `fetch` de `web_bridge.js`
   chez lidar2map et gpxsolar), et `/api/init`, qui sert à repérer une
   instance déjà lancée, reste ouverte.
3. Un avertissement dans le journal quand un sous-réseau de confiance est plus
   large qu'un /24, et une limite du débit du journal des refus (un client
   insistant ne doit pas remplir le disque).

### Précisions du même jour (démarrage automatique et icône du Bureau)

- **Décision 5, tranchée** : personne n'a activé le démarrage automatique de
  lidar2map, il ne sert à rien sur ce projet : il sera retiré, avec la case de
  la page, l'API `set-autostart` et `_autostart.py`. Précautions : nettoyer au
  premier lancement d'une version récente toute entrée laissée par une
  ancienne (service systemd, agent launchd, dossier Démarrage, script .vbs des
  versions <= 1.53), et garder ce dont `raccourci_bureau()` a besoin, qui
  s'appuie aujourd'hui sur la commande du démarrage automatique. Le partage du
  démarrage automatique ne concerne donc que blink2video et watch2notif.
- **Décision 10, nouvelle** : les quatre projets créent une icône sur le Bureau
  au premier lancement. Le module `raccourci` de la bibliothèque sait déjà la
  créer (elle sert au menu de l'icône) ; il manque le déclenchement unique.
  Propositions : un marqueur dans le dossier d'état, une seule tentative, jamais
  recréée si l'utilisateur la supprime ; seulement pour un programme installé
  (figé), pas pour des sources lancées d'un dépôt ; blink2video passe alors sur
  le module de la bibliothèque, ce qui supprime sa copie de 204 lignes. Les
  tests ne doivent jamais toucher au vrai Bureau. C'est un effet de bord
  persistant posé sans clic : il devrait rester visible et retirable (une
  ligne dans les réglages, ou dans l'aide).

### Précisions suivantes : l'icône du Bureau se demande par un bandeau

- **Forme retenue (Nico, 2026-09-29)** : un bandeau dans la page web de
  l'application, à sa première ouverture, deux boutons (« Créer l'icône sur le
  Bureau », « Non merci »). Non bloquant ; le choix est mémorisé dans le dossier
  d'état et ne se redemande pas ; « Créer un raccourci » reste dans le menu de
  l'icône. Programme installé seulement. Pas de création automatique.
- **Langue** : le bandeau réutilise la langue déjà choisie par la page
  (préférence enregistrée, sinon celle du navigateur : « fr » donne le
  français, tout le reste l'anglais), et ses textes FR et EN suivent le
  sélecteur de la page. Composant commun : JavaScript et CSS de la
  bibliothèque servis par `serveweb`, plus une route ; une ligne à ajouter à la
  page de chaque application.
- **watch2notif à adapter** : sa page démarre en anglais (`let lang = 'en'`)
  avant que sa préférence ne s'applique, au lieu de détecter la langue du
  navigateur comme les trois autres. À corriger dans la première vague, sans
  quoi le bandeau s'y affiche d'abord en anglais.

### Ordre des vagues proposé

1. Serveur commun complété (décision 1), écriture atomique, dossiers, puis
   instance et port, langue, version de la page, colle du menu, et l'icône du
   Bureau au premier lancement (décision 10). Branchement de
   gpxsolar, lidar2map, watch2notif, blink2video, bornes `<1`.
2. `deploy.py` partagé (décision 9), et les workflows réutilisables si Nico les
   retient.
3. Démarrage automatique commun à blink2video et watch2notif, sur la base de
   blink2video (corrige le guillemetage de watch2notif).
4. Démarreur minimal du bootstrap (3.8), qui donne aussi le verrou au mode
   sources de blink2video.
5. Installation des mises à jour commune (3.9), précédée d'une note qui compare
   les deux architectures ; offre la mise à jour automatique à lidar2map et
   gpxsolar.
