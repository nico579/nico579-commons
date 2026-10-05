"""Icône de zone de notification commune à blink2video, lidar2map,
watch2notif et gpxsolar.

Le même menu dans les quatre, sans élément propre à l'une d'elles (Nico,
2026-09-28) : Ouvrir (action par défaut, double clic), « Mettre à jour
vers x.y » seulement quand une version plus récente est connue,
Redémarrer, Arrêter, « Créer un raccourci sur le Bureau ». Tout le reste
passe par la page qu'ouvre Ouvrir. L'application ne donne que ses
actions, toutes obligatoires ; ce module porte ce que les quatre ont
appris à leurs dépens, repris du tray.py de blink2video :

- le menu est reconstruit toutes les CADENCE_MENU secondes, sans quoi le
  backend win32 de pystray garde le menu construit au démarrage (langue,
  mise à jour) ; sous macOS cette reconstruction passe par le fil
  principal, faute de quoi macOS 27 tue le processus (SIGTRAP, issue #31
  de blink2video) ;
- Redémarrer, Arrêter et Mettre à jour tournent sur un fil à part, jamais
  sur celui de la pompe de messages de l'icône : un arrêt qui prend
  quinze secondes gelait l'icône, restée seule à l'écran ;
- executer() attend la fin de ce fil avant de rendre la main : sinon
  l'appelant sort, et le fil démon meurt avant d'avoir relancé quoi que
  ce soit (« Redémarrer » qui arrête sans relancer, vécu le 2026-09-03).
"""

from __future__ import annotations

import functools
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# Secondes entre deux reconstructions du menu.
CADENCE_MENU = 5
# Attente maximale, à la sortie, du fil de Redémarrer/Arrêter/Mettre à jour.
ATTENTE_SORTIE_S = 30

LIBELLES = {
    "fr": {"ouvrir": "Ouvrir", "maj": "Mettre à jour vers {version}",
           "redemarrer": "Redémarrer", "arreter": "Arrêter",
           "raccourci": "Créer un raccourci sur le Bureau"},
    "en": {"ouvrir": "Open", "maj": "Update to {version}",
           "redemarrer": "Restart", "arreter": "Stop",
           "raccourci": "Create a Desktop shortcut"},
}


@dataclass
class Actions:
    """Ce que l'application fournit, une action par entrée du menu.

    ``redemarrer``, ``arreter`` et ``mettre_a_jour`` tournent sur un fil à
    part, l'icône se refermant aussitôt : ils arrêtent l'application (et la
    relancent, pour les deux derniers). ``version_disponible`` est appelée à
    chaque reconstruction du menu : elle doit rendre vite, sans réseau
    (un cache entretenu ailleurs), la version plus récente ou None.

    ``mettre_a_jour_referme`` faux : l'icône reste après Mettre à jour,
    l'action tournant toujours sur un fil à part. C'est le cas d'une
    application qui ouvre la page de la version (lidar2map, gpxsolar), ou
    qui télécharge d'abord et lève elle-même ``arret`` quand l'installation
    est prête (watch2notif)."""
    ouvrir: Callable[[], None]
    redemarrer: Callable[[], None]
    arreter: Callable[[], None]
    version_disponible: Callable[[], Optional[str]]
    mettre_a_jour: Callable[[], None]
    creer_raccourci: Callable[[], None]
    langue: Callable[[], str] = lambda: "fr"
    mettre_a_jour_referme: bool = True


@functools.lru_cache(maxsize=1)
def _par_statusnotifier() -> bool:
    """Vrai sous Linux quand un hôte StatusNotifierItem est présent (GNOME avec
    l'extension AppIndicator, KDE...) : l'icône passe alors par ce protocole
    (tray_sni) plutôt que par pystray, dont le mode X11 n'ouvre aucun menu sous
    Wayland. Calculé une fois : l'attente éventuelle de l'hôte au démarrage
    de la session ne se paie pas deux fois."""
    try:
        from . import tray_sni
        return tray_sni.utilisable()
    except Exception:
        return False


def disponible() -> bool:
    """Faux si Pillow ne se charge pas, ou si aucune zone de notification n'est
    employable : ni hôte StatusNotifierItem, ni pystray (bibliothèque absente,
    Linux sans AppIndicator/GTK, session sans affichage). L'application
    continue alors sans icône."""
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        return False
    if _par_statusnotifier():
        return True
    try:
        import pystray  # noqa: F401
    except Exception:
        return False
    return True


def sur_le_fil_principal(fonction, plateforme=None):
    """``fonction``, sûre à appeler depuis un autre fil que celui
    d'icon.run(). Sous macOS, pystray passe l'appel tel quel à AppKit, qui
    n'admet les changements d'interface que depuis le fil principal ;
    PyObjCTools.AppHelper.callAfter, la façon documentée de PyObjC, confie
    l'appel à la boucle que fait tourner icon.run(). Ailleurs, l'appel
    direct suffit."""
    if (plateforme or sys.platform) != "darwin":
        return fonction
    from PyObjCTools import AppHelper
    return lambda: AppHelper.callAfter(fonction)


def libelles(langue: str) -> dict:
    return LIBELLES.get(langue, LIBELLES["en"])


def en_fond(action) -> None:
    """``action`` sur un fil à part, hors de la pompe de messages de l'icône."""
    threading.Thread(target=action, daemon=True).start()


def entrees(actions: Actions, pystray, arreter_icone) -> list:
    """Les entrées du menu, dans l'ordre commun, pour la langue du moment.

    ``arreter_icone(action)`` : lance ``action`` sur un fil à part puis
    referme l'icône (voir Tray)."""
    mots = libelles(actions.langue())
    menu = [pystray.MenuItem(mots["ouvrir"], lambda icon, item: actions.ouvrir(),
                             default=True)]
    version = actions.version_disponible()
    if version:
        lancer = arreter_icone if actions.mettre_a_jour_referme else en_fond
        menu.append(pystray.MenuItem(mots["maj"].format(version=version),
                                     lambda icon, item: lancer(actions.mettre_a_jour)))
    menu.append(pystray.MenuItem(mots["redemarrer"],
                                 lambda icon, item: arreter_icone(actions.redemarrer)))
    menu.append(pystray.MenuItem(mots["arreter"],
                                 lambda icon, item: arreter_icone(actions.arreter)))
    menu.append(pystray.MenuItem(mots["raccourci"],
                                 lambda icon, item: actions.creer_raccourci()))
    return menu


class Tray:
    """L'icône d'une application. ``executer()`` bloque sur la boucle de
    l'icône, à appeler depuis le fil principal (exigé par macOS)."""

    def __init__(self, nom: str, icone: Path, actions: Actions,
                 titre: Optional[str] = None, arret: Optional[threading.Event] = None):
        self.nom = nom
        self.icone = Path(icone)
        self.actions = actions
        self.titre = titre or nom
        # Levé par l'application quand elle s'arrête d'elle-même : l'icône
        # se referme et executer() rend la main.
        self.arret = arret or threading.Event()
        self._fil_de_sortie: Optional[threading.Thread] = None
        self._icon = None

    def _arreter_icone(self, action) -> None:
        self._fil_de_sortie = threading.Thread(target=action, daemon=True)
        self._fil_de_sortie.start()
        self.arret.set()
        if self._icon is not None:
            self._icon.stop()

    def construire(self, pystray=None, image=None):
        """L'objet pystray.Icon, sans le lancer (séparé pour les tests)."""
        if pystray is None:
            if _par_statusnotifier():
                from .tray_sni import PYSTRAY_COMPATIBLE as pystray
            else:
                import pystray
        if image is None:
            from PIL import Image
            # Copie chargée, fichier refermé : Image.open seul le garde
            # ouvert jusqu'au premier affichage de l'icône.
            with Image.open(str(self.icone)) as source:
                image = source.copy()
        menu = pystray.Menu(lambda: iter(entrees(self.actions, pystray, self._arreter_icone)))
        self._icon = pystray.Icon(self.nom, image, self.titre, menu=menu)
        return self._icon

    def executer(self) -> None:
        icon = self._icon or self.construire()

        def veille():
            self.arret.wait()
            icon.stop()

        def rafraichir():
            maj_du_menu = sur_le_fil_principal(icon.update_menu)
            while not self.arret.wait(timeout=CADENCE_MENU):
                try:
                    maj_du_menu()
                except Exception:
                    pass

        threading.Thread(target=veille, daemon=True).start()
        threading.Thread(target=rafraichir, daemon=True).start()
        icon.run()
        self.attendre_la_sortie()

    def attendre_la_sortie(self, delai_s: float = ATTENTE_SORTIE_S) -> None:
        if self._fil_de_sortie is not None:
            self._fil_de_sortie.join(timeout=delai_s)
