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
                 delai_s: float = 10, ouvrir: Optional[Callable[[str], dict]] = None):
        self.depot = depot
        self.version_locale = str(version_locale).lstrip("vV")
        self.fraicheur_s = fraicheur_s
        agent = f"{depot.split('/')[-1]}/{self.version_locale}"
        self._ouvrir = ouvrir or (lambda url: _ouvrir_github(url, agent, delai_s))
        self._derniere: Optional[dict] = None
        self._verifie_a: Optional[float] = None
        self._verrou = threading.Lock()

    @property
    def page_des_releases(self) -> str:
        return f"https://github.com/{self.depot}/releases/latest"

    def verifier(self) -> bool:
        """Interroge GitHub ; vrai s'il a répondu (même sans nouveauté)."""
        try:
            release = self._ouvrir(f"https://api.github.com/repos/{self.depot}/releases/latest")
            version = str(release.get("tag_name") or "").lstrip("vV")
            if not version:
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
        """Fil démon : une vérification tout de suite, puis toutes les
        `fraicheur_s` secondes, jusqu'à `arret`."""
        arret = arret or threading.Event()

        def boucle():
            while True:
                self.verifier()
                if arret.wait(self.fraicheur_s):
                    return

        fil = threading.Thread(target=boucle, name="verification-version", daemon=True)
        fil.start()
        return fil
