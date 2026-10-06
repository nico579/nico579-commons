"""Dossiers d'une application : l'état dans le dossier de données standard de
l'OS, les sorties dans un dossier visible, et la reprise unique de l'état
qu'une version plus ancienne rangeait dans son dossier de travail.

Repris de _dossiers.py, que lidar2map et gpxsolar avaient en deux jumeaux
identiques à leurs constantes près (noms, liste des fichiers, variable
d'environnement) : ces constantes sont désormais les paramètres de Dossiers.

L'état, ce sont les préférences, l'historique et les journaux : de petits
fichiers que l'utilisateur n'a pas à voir. Les sorties sont des gigaoctets
qu'il cherche, copie et supprime lui-même, d'où Documents/<application> par
défaut. La variable <APPLICATION>_HOME, si elle est fournie, regroupe l'un et
l'autre (essais, installation portable).

Le dossier d'état s'appelle <application>-data et non <application> : sous ce
nom-là, le dossier standard est celui où le lanceur d'une version ancienne
extrayait le programme, qu'une version récente supprime au lancement. Les
réglages et l'historique ne doivent pas partir avec lui.

Bibliothèque standard seule (platformdirs est utilisé s'il est installé).
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional

from . import atomique


class Dossiers:
    """Dossiers d'une application, calculés sans rien créer sur le disque
    (sauf preparer_etat(), qui copie l'ancien état une seule fois).

    ``application``     nom court (« gpxsolar »), base des autres noms.
    ``nom_etat``        dossier d'état dans le dossier standard de l'OS.
    ``nom_sorties``     dossier de sorties sous Documents.
    ``variable_home``   variable d'environnement qui force tout au même endroit.
    ``preferences``     fichier de préférences, dans le dossier d'état.
    ``cle_sorties``     clé des préférences qui désigne le dossier de sorties.
    ``fichiers_etat``   ce qu'une ancienne version rangeait dans son dossier
                        de travail et que la reprise copie.
    ``dossiers_sorties`` dossiers que l'ancienne version y écrivait : s'il y
                        en a, le réglage des sorties y pointe.
    ``marqueur``        fichier posé dans le dossier d'état quand la reprise a
                        eu lieu.
    """

    def __init__(self, application: str, *, nom_etat: Optional[str] = None,
                 nom_sorties: Optional[str] = None,
                 variable_home: Optional[str] = None,
                 preferences: str = "preferences.json",
                 cle_sorties: str = "dossier_sorties",
                 fichiers_etat: Iterable[str] = (),
                 dossiers_sorties: Iterable[str] = (),
                 marqueur: Optional[str] = None):
        self.application = application
        self.nom_etat = nom_etat or f"{application}-data"
        self.nom_sorties = nom_sorties or application
        self.variable_home = variable_home or f"{application.upper()}_HOME"
        self.preferences = preferences
        self.cle_sorties = cle_sorties
        self.fichiers_etat = tuple(fichiers_etat)
        self.dossiers_sorties = tuple(dossiers_sorties)
        self.marqueur = marqueur or f".{application}_etat_migre.json"

    # ------------------------------------------------------------ calculs purs

    def force(self, environnement) -> Optional[Path]:
        """Le dossier que désigne la variable <APPLICATION>_HOME, ou None."""
        valeur = (environnement.get(self.variable_home) or "").strip()
        return Path(valeur).expanduser().resolve() if valeur else None

    def dossier_etat(self, environnement=None) -> Path:
        """<APPLICATION>_HOME s'il est fourni, sinon le dossier standard de
        l'OS. Calcul pur : rien n'est créé sur le disque."""
        environnement = os.environ if environnement is None else environnement
        return self.force(environnement) or self.dossier_etat_standard()

    def dossier_etat_standard(self) -> Path:
        """Celui que donne platformdirs. Des sources lancées avant que le
        bootstrap l'ait installé appliquent les mêmes règles pour les trois
        systèmes pris en charge."""
        try:
            from platformdirs import user_data_dir
        except ImportError:
            if os.name == "nt":
                base = (os.environ.get("LOCALAPPDATA")
                        or str(Path.home() / "AppData" / "Local"))
            elif sys.platform == "darwin":
                base = str(Path.home() / "Library" / "Application Support")
            else:
                base = (os.environ.get("XDG_DATA_HOME", "").strip()
                        or str(Path.home() / ".local" / "share"))
            return (Path(base) / self.nom_etat).resolve()
        return Path(user_data_dir(self.nom_etat, appauthor=False)).resolve()

    def documents(self) -> Path:
        """Le dossier Documents, même déplacé (OneDrive, autre disque) quand
        platformdirs est là ; ~/Documents sinon."""
        try:
            from platformdirs import user_documents_dir
        except ImportError:
            return Path.home() / "Documents"
        return Path(user_documents_dir())

    def dossier_sorties(self, etat=None, environnement=None) -> Path:
        """Racine des sorties : le réglage des préférences (que pose la
        reprise d'une ancienne version), sinon <APPLICATION>_HOME, sinon
        Documents/<application>. Calcul pur, comme dossier_etat()."""
        environnement = os.environ if environnement is None else environnement
        etat = self.dossier_etat(environnement) if etat is None else Path(etat)
        preferences = atomique.lire_json(etat / self.preferences, {})
        reglage = preferences.get(self.cle_sorties) if isinstance(preferences, dict) else None
        if isinstance(reglage, str) and reglage.strip():
            return Path(reglage.strip()).expanduser().resolve()
        return self.force(environnement) or (self.documents() / self.nom_sorties).resolve()

    # ------------------------------------------------------------ reprise unique

    def preparer_etat(self, ancien, *, version: str = "", environnement=None) -> list:
        """Reprend, une fois, l'état qu'une ancienne version rangeait dans son
        dossier de travail `ancien` (celui du programme, ou des sources), et
        rend la liste de ce qui a été repris. Sans effet avec
        <APPLICATION>_HOME.

        À appeler au démarrage, sous __main__ seulement : un simple calcul de
        chemin, dans un test, ne doit jamais copier d'état.

        Les fichiers d'état sont copiés sans rien écraser, jamais déplacés :
        revenir à l'ancienne version reste possible. Les sorties ne bougent
        pas : si l'ancien dossier en contient, le réglage des sorties y
        pointe. Le marqueur n'est posé que si quelque chose a été repris ;
        sans rien à reprendre, l'examen recommence au lancement suivant, pour
        quelques accès disque."""
        environnement = os.environ if environnement is None else environnement
        if self.force(environnement):
            return []
        etat = self.dossier_etat_standard()
        ancien = Path(ancien).resolve()
        if ancien == etat or (etat / self.marqueur).is_file():
            return []
        # Démarrage automatique et lancement manuel peuvent partir ensemble :
        # un seul processus reprend, l'autre attend son verrou et trouve le
        # marqueur.
        with atomique.verrou_inter_processus(etat / self.marqueur, delai_s=60):
            if (etat / self.marqueur).is_file():
                return []
            return self._reprendre(ancien, etat, version)

    def _reprendre(self, ancien: Path, etat: Path, version: str) -> list:
        repris = [nom for nom in self.fichiers_etat
                  if self.copier_si_absent(ancien / nom, etat / nom)]

        sorties = ""
        if any((ancien / nom).is_dir() for nom in self.dossiers_sorties):
            chemin = etat / self.preferences
            # Même verrou que l'écriture d'une préférence par l'application :
            # une instance déjà passée à la nouvelle version peut écrire une
            # préférence au même moment.
            with atomique.verrou_inter_processus(chemin):
                preferences = atomique.lire_json(chemin, {})
                if not isinstance(preferences, dict):
                    preferences = {}
                if not str(preferences.get(self.cle_sorties) or "").strip():
                    preferences[self.cle_sorties] = sorties = str(ancien)
                    atomique.ecrire_json(chemin, preferences)

        if not (repris or sorties):
            return []
        atomique.ecrire_json(etat / self.marqueur, {
            "version": version,
            "date": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "depuis": str(ancien),
            "repris": repris,
            self.cle_sorties: sorties,
        })
        return repris + ([self.cle_sorties] if sorties else [])

    @staticmethod
    def copier_si_absent(source: Path, cible: Path) -> bool:
        """Copie atomique d'un fichier ou d'un dossier, sans jamais écraser la
        cible."""
        if cible.exists() or not (source.is_file() or source.is_dir()):
            return False
        cible.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            temporaire = atomique.chemin_part(cible)
            try:
                shutil.copytree(source, temporaire)
                atomique.remplacer(temporaire, cible)
            finally:
                shutil.rmtree(temporaire, ignore_errors=True)
            return True
        temporaire = atomique.chemin_part(cible)
        try:
            shutil.copy2(source, temporaire)
            atomique.remplacer(temporaire, cible)
        finally:
            temporaire.unlink(missing_ok=True)
        return True
