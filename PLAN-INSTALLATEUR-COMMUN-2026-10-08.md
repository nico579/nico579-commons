# Un seul installateur pour les quatre applications

Plan du 2026-10-08, à la demande de Nico : faire passer blink2video sur
l'installateur commun (`nico579_commons.maj_install`), en exigeant que cet
installateur reprenne le meilleur des deux et soit le plus robuste possible.

## Le principe

On ne fait pas entrer blink2video dans l'installateur commun tel qu'il est. On
renforce d'abord le commun avec ce que blink2video fait mieux, au profit des trois
autres applications, puis seulement on y fait passer blink2video. À la fin, il
reste un seul mécanisme : l'échange du dossier entier par un assistant externe, et
la permutation élément par élément de blink2video disparaît, avec son marqueur, sa
reprise et toute la classe de problèmes de l'issue 95.

## Ce que chacun fait mieux

Le commun vient de `self_update.py` de watch2notif, en production sur les trois
systèmes. Ses points forts, que blink2video n'a pas :

- **Il vérifie la nouvelle version après la relance.** L'assistant regarde si elle
  reste en vie ; sinon il remet l'ancienne en place et la relance. blink2video, lui,
  vérifie la nouvelle version avant de permuter (`--version`), mais plus rien après.
- **Il échange le dossier entier** en deux déplacements, au lieu de trois copies
  élément par élément : la fenêtre où l'installation est incohérente est de
  quelques millisecondes au lieu de la durée d'une copie de `_internal`.
- **Une poignée de main avec l'assistant** (« prêt », feu vert, accusé) : rien ne
  bouge tant que l'assistant n'a pas confirmé qu'il tourne.
- **Un refus net des dossiers qui ne sont pas le bundle publié** (`unsafe_install`).
- Le reste existe des deux côtés : empreinte SHA-256 de l'archive, limites
  d'extraction, auto-test avant échange, relance d'un service systemd ou d'un agent
  launchd par son nom, sortie du cgroup du service (`systemd-run`).

blink2video fait mieux sur six points, que le commun doit reprendre :

1. **Plusieurs processus.** Il arrête tout ce qui tourne (serveur, surveillance,
   téléchargement, enfants d'un superviseur) par sa commande `stop` et ses fiches
   d'instances, puis attend qu'aucun ne survive. L'assistant commun n'attend qu'un
   processus, celui qui a lancé la mise à jour (`ParentPid`). Sous Windows, un
   seul processus encore vivant qui tient un fichier de `_internal` fait échouer
   le déplacement du dossier.
2. **Relancer exactement ce qui tournait,** verbe pour verbe, sans doubler les
   enfants d'un superviseur. Le commun sait relancer plusieurs processus
   (`verbes_relance`), mais c'est blink2video qui sait les noter.
3. **La reprise après une coupure.** Si l'assistant commun est tué entre ses deux
   déplacements (coupure de courant, processus tué), le dossier d'installation
   n'existe plus ; seule reste la sauvegarde `.<nom>.backup-<jeton>` à côté, et
   rien ne la remet en place. La fenêtre est courte, mais l'issue 95 a montré
   qu'une interruption finit toujours par arriver chez quelqu'un. Il ne reconnaît
   pas non plus au démarrage suivant les restes d'une transaction ancienne
   (`.update-*`, `.backup-*`, `.failed-*`) : ils s'accumulent.
4. **Un verrou entre processus.** blink2video réserve l'installation
   (`reservation`) : deux mises à jour lancées en même temps, l'une depuis la page
   et l'autre depuis le menu de l'icône, ne peuvent pas se croiser. Le verrou du
   commun ne vaut qu'à l'intérieur d'un processus.
5. **Pas besoin d'écrire dans le dossier parent pour préparer :** blink2video
   prépare dans `<installation>/update`. Le commun prépare à côté de
   l'installation. Pour échanger le dossier entier, il faut de toute façon écrire
   dans le parent ; la différence est donc ailleurs : une installation placée dans
   un dossier protégé perdrait la mise à jour automatique (voir les décisions).
6. **Les cas sans mise à jour automatique** sont déjà propres à blink2video et
   le restent : build Windows 7, image Docker, installation depuis les sources
   (`git pull`), programme de mise à jour importable sans dépendance (`python -S`).

Deux différences de forme à régler en plus :

- **macOS** : l'archive de blink2video contient un dossier `blink2video/`, celles
  de gpxsolar et lidar2map un bundle `.app`. Le commun attend un `.app` sous macOS.
  Il faut, soit que le commun accepte aussi un dossier simple, soit que blink2video
  publie un `.app`. Le premier est moins risqué (rien ne change pour les
  utilisateurs macOS actuels).
- **Les noms d'archives** suivent déjà la convention commune
  (`blink2video-windows-x86_64.zip`, `-linux-x86_64.tar.gz`, `-macos-arm64.zip`,
  dossier racine `blink2video`) : vérifié sur la release 0.22.0. L'archive
  Windows 7 (`windows7-…-legacy`) reste exclue.

## Phase 1 : renforcer le commun (version 0.5.0)

Chaque point arrive avec ses tests, et les trois applications actuelles en
profitent.

1. **Arrêt de plusieurs processus.** `Application` reçoit deux fonctions
   facultatives, `arreter()` et `vivants()`, comme `finaliser` en a déjà. Avant de
   donner le feu vert, l'installateur les appelle et attend qu'aucun processus ne
   survive ; l'assistant reçoit la liste des PID à attendre au lieu d'un seul.
   Une application à un seul processus ne change rien.
2. **Journal de transaction et reprise.** Avant chaque déplacement, l'assistant
   écrit son étape dans un journal placé à côté de l'installation. Au lancement
   suivant d'une application, et au début de la mise à jour suivante, le commun
   lit ce journal. Si l'installation est absente et que la sauvegarde existe, il la
   remet en place. Si l'installation est saine, il efface les restes d'une
   transaction ancienne. Le cas où l'installation manque reste celui où
   l'application ne démarre plus du tout : la réparation doit donc pouvoir se
   faire sans elle (voir les décisions).
3. **Verrou entre processus**, repris de `reservation` : une seule mise à jour à la
   fois par installation, quel que soit le processus qui la demande.
4. **Vérification préalable des droits** : avant de télécharger quoi que ce soit,
   `possible()` vérifie qu'on peut écrire dans le dossier parent, et sinon rend une
   raison lisible (« installé dans un dossier protégé : mettez à jour à la main »).
   Aujourd'hui, l'échec n'arrive qu'après le téléchargement.
5. **macOS sans `.app`** : `Disposition` accepte un dossier simple comme racine.
6. Les messages restent dans les deux langues, et le bandeau commun affiche chaque
   nouvel état.

Livraison : nico579-commons 0.5.0. Comme les applications épinglent `<0.5`, il
faut passer leurs bornes à `<0.6` en même temps (lidar2map, gpxsolar, watch2notif),
avec un essai réel de mise à jour de chacune.

## Phase 2 : blink2video sur l'installateur commun

1. blink2video décrit son `Application` : nom, `noms_toleres` (son verrou, son
   dossier `update` de l'ancien mécanisme le temps de la transition), service
   systemd et agent launchd, `arreter()` par sa commande `stop` et ses fiches,
   `vivants()` par `lire_instances()`, et les verbes à relancer notés au dernier
   moment (`_compositions_en_cours`, qui ne double pas les enfants).
2. Sa `Disposition` vient de `archive_standard`, Windows 7 exclu.
3. Le bouton « Mettre à jour » de sa page et l'entrée du menu de l'icône pilotent
   l'`Installateur` commun et affichent son état (téléchargement, redémarrage,
   erreur), comme dans les trois autres.
4. La recherche de version reste la sienne, importable sans dépendance, pour
   l'installation depuis les sources.

**La transition, le point délicat.** C'est toujours la version installée qui fait
la mise à jour. La dernière version « ancien style » (appelons-la N) installera donc
la première « nouveau style » (N+1) avec l'ancienne permutation : elle télécharge
N+1, la lance avec `update --finaliser`, et c'est N+1 qui termine. N+1 doit donc
encore savoir faire `update --finaliser` à l'ancienne. Et quelqu'un qui saute des
versions, resté en 0.15, passera directement à la dernière par le même chemin. On
garde donc ce point d'entrée, réduit au strict nécessaire (il s'appuie déjà sur
`maj_install.permuter`), tant que des versions anciennes circulent.

Livraison : blink2video 0.23.0, avec le commun 0.5.0.

## Phase 3 : ménage

Quand plus aucune version « ancien style » ne circule (à décider : trois mois après
la 0.23.0, par exemple), on retire de blink2video la permutation et son point
d'entrée `--finaliser`, et du commun `permuter`, `nettoyer_restes`, le marqueur et
la reprise. Une installation restée très ancienne recevra alors un message clair :
mettre à jour à la main, une fois.

## Les essais

- **Tests unitaires** pour chaque capacité nouvelle du commun, y compris des
  interruptions simulées à chaque étape de l'assistant.
- **Essais réels, sans toucher aux installations de Nico** : une copie de la
  release N installée dans un dossier jetable, mise à jour vers N+1 par le vrai
  mécanisme, avec chaque composition de processus.
  - Windows : sur le PC, dans un dossier temporaire, avec `BLINK_HOME` isolé.
  - Linux : la VM Ubuntu, avec le service systemd (le scénario de l'issue 35).
  - macOS : un job sur le runner GitHub.
- **L'interruption pour de vrai** : tuer l'assistant entre ses deux déplacements,
  puis vérifier que le lancement suivant remet tout en place.
- **La transition** : 0.22 installée qui se met à jour vers la 0.23, sur les trois
  systèmes.
- Les trois autres applications refont un essai réel de mise à jour avec le commun
  0.5.0.

## Décisions (Nico, 2026-10-08)

Les quatre propositions ci-dessous sont acceptées telles quelles :

1. une installation dans un dossier protégé perd la mise à jour automatique, avec un
   message clair ;
2. si l'installation a disparu après une coupure : un message dans le README et une
   détection à la mise à jour suivante, pas de lanceur ;
3. l'ancien point d'entrée `--finaliser` est gardé trois mois après la 0.23.0 ;
4. le commun passe en 0.5.0, et les bornes des quatre applications passent à `<0.6`
   en même temps.

## Ce qu'il fallait décider

1. **Installations dans un dossier protégé.** Avec l'échange de dossier entier,
   elles perdent la mise à jour automatique (avec un message clair). Je propose de
   l'accepter : c'est déjà le cas des trois autres applications, et le README
   recommande un dossier de l'utilisateur.
2. **La réparation quand l'installation a disparu.** Puisque l'application ne
   démarre plus, il faut la faire ailleurs : soit le raccourci du Bureau passe par
   un petit lanceur qui répare d'abord, soit on s'en tient à un message dans le
   README (« si blink2video ne démarre plus après une coupure pendant une mise à
   jour, renommez `.blink2video.backup-…` en `blink2video` »). Je recommande le
   message, plus une détection à la mise à jour suivante. Un lanceur est un
   mécanisme de plus à maintenir, pour une fenêtre de quelques millisecondes.
3. **Combien de temps garder l'ancien point d'entrée** (`--finaliser`) : je propose
   trois mois après la 0.23.0.
4. **Le passage du commun en 0.5.0,** qui oblige à relever les bornes des quatre
   applications en même temps.

## Ordre et ampleur

- Phase 1 : le plus gros du travail (assistant PowerShell et sh, journal,
  reprise, verrou, tests d'interruption), puis les trois applications à relever et
  à essayer. Deux à trois sessions.
- Phase 2 : blink2video sur le commun, essais réels sur les trois systèmes et la
  transition. Deux sessions.
- Phase 3 : le ménage, plus tard, une session courte.

Rien de tout cela ne presse : aucun utilisateur n'est bloqué aujourd'hui,
puisque la reprise de la 0.21.2 couvre déjà le cas de l'issue 95.
