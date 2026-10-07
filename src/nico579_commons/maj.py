"""Recherche de nouvelle version sur GitHub, légère et partagée.

Une question à GitHub au plus par `fraicheur_s` (une heure par défaut), faite
par un fil de fond ; le menu de l'icône et la page lisent la dernière réponse
sans jamais attendre le réseau. L'installation n'est pas ici : blink2video et
watch2notif ont chacun la leur, lidar2map et gpxsolar ouvrent la page de la
release.

Hors ligne, API limitée (60 requêtes par heure et par adresse sans compte) ou
réponse inattendue : aucune erreur, on garde ce qu'on savait.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional


def numeros(version: str) -> tuple:
    """« v1.6.10 » devient (1, 6, 10), comparable à un autre tuple : une
    comparaison de chaînes rangerait 1.6.10 avant 1.6.9."""
    return tuple(int(n) for n in re.findall(r"\d+", str(version))[:3]) or (0,)


def _ouvrir_github(url: str, agent: str, delai_s: float) -> dict:
    requete = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": agent})
    with urllib.request.urlopen(requete, timeout=delai_s) as reponse:
        return json.loads(reponse.read())


class Verificateur:
    """Dernière release publiée d'un dépôt GitHub, comparée à la version en
    place. `disponible()` ne touche jamais le réseau ; `verifier()` et le fil
    de `veiller()` le font."""

    def __init__(self, depot: str, version_locale: str, *, fraicheur_s: float = 3600,
                 delai_s: float = 10, ouvrir: Optional[Callable[[str], dict]] = None,
                 cache: Optional[Path] = None):
        self.depot = depot
        self.version_locale = str(version_locale).lstrip("vV")
        self.fraicheur_s = fraicheur_s
        agent = f"{depot.split('/')[-1]}/{self.version_locale}"
        self._ouvrir = ouvrir or (lambda url: _ouvrir_github(url, agent, delai_s))
        self._derniere: Optional[dict] = None
        self._verifie_a: Optional[float] = None
        self._verrou = threading.Lock()
        # Dernière réponse gardée sur disque : une mise à jour signalée hier
        # reste vraie au démarrage suivant, même hors ligne.
        self._cache = Path(cache) if cache else None
        self._lire_cache()

    @property
    def verifie_a(self) -> Optional[float]:
        """Heure (time.time) de la dernière réponse de GitHub, ou None."""
        return self._verifie_a

    def _lire_cache(self) -> None:
        if self._cache is None:
            return
        try:
            donnees = json.loads(self._cache.read_text(encoding="utf-8"))
            version = str(donnees["version"])
            verifie = float(donnees["verifie"])
            fichiers = donnees.get("assets") or []
            page = str(donnees.get("page") or self.page_des_releases)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return
        if version and isinstance(fichiers, list):
            self._derniere = {"version": version, "page": page, "assets": fichiers}
            self._verifie_a = verifie

    def _ecrire_cache(self) -> None:
        if self._cache is None or self._derniere is None:
            return
        try:
            from . import atomique
            atomique.ecrire_json(self._cache, dict(self._derniere, verifie=self._verifie_a),
                                 indent=None)
        except OSError:
            pass   # le cache n'est qu'un confort

    @property
    def page_des_releases(self) -> str:
        return f"https://github.com/{self.depot}/releases/latest"

    def verifier(self) -> bool:
        """Interroge GitHub ; vrai s'il a répondu (même sans nouveauté)."""
        try:
            release = self._ouvrir(f"https://api.github.com/repos/{self.depot}/releases/latest")
            version = str(release.get("tag_name") or "").lstrip("vV")
            # releases/latest ne renvoie jamais de brouillon ni de préversion ;
            # une réponse qui en serait une n'annonce rien.
            if not version or release.get("draft") or release.get("prerelease"):
                return False
            page = str(release.get("html_url") or self.page_des_releases)
            # Les fichiers de la release, réduits à ce que vérifie un
            # installateur (nom, adresse, taille, empreinte, état) : l'appelant
            # qui en a besoin (watch2notif) les lit ici, sans refaire la requête.
            fichiers = [{cle: brut.get(cle) for cle in
                         ("name", "browser_download_url", "size", "digest", "state")}
                        for brut in release.get("assets") or [] if isinstance(brut, dict)]
        except Exception:
            return False
        with self._verrou:
            self._derniere = {"version": version, "page": page, "assets": fichiers}
            self._verifie_a = time.time()
        self._ecrire_cache()
        return True

    def disponible(self) -> Optional[dict]:
        """{"version", "page", "assets"} si une version plus récente est connue,
        sinon None. Instantané : ne lit que la dernière réponse."""
        with self._verrou:
            derniere = self._derniere
        if derniere and numeros(derniere["version"]) > numeros(self.version_locale):
            return dict(derniere)
        return None

    def veiller(self, arret: Optional[threading.Event] = None) -> threading.Thread:
        """Fil démon : une vérification quand la dernière réponse a plus de
        `fraicheur_s` secondes (ou n'existe pas), puis toutes les `fraicheur_s`
        secondes, jusqu'à `arret`. Une réponse encore fraîche, celle du cache disque
        d'un démarrage récent par exemple, n'est pas redemandée : on attend ce qu'il
        en reste. Hors ligne, la question suivante n'est reposée qu'une heure plus
        tard."""
        arret = arret or threading.Event()

        def boucle():
            while True:
                verifie = self.verifie_a
                age = None if verifie is None else time.time() - verifie
                # Une réponse datée du futur (horloge revenue en arrière) est périmée :
                # sinon la veille resterait muette jusqu'à ce que l'heure la rattrape.
                if age is None or age < 0 or age >= self.fraicheur_s:
                    self.verifier()
                    reste = self.fraicheur_s
                else:
                    reste = self.fraicheur_s - age
                if arret.wait(reste):
                    return

        fil = threading.Thread(target=boucle, name="verification-version", daemon=True)
        fil.start()
        return fil
