"""Icône de zone de notification commune à blink2video, lidar2map,
watch2notif et gpxsolar.

Menu, dans cet ordre : Ouvrir (action par défaut, double clic), les
éléments propres à l'application, « Mettre à jour vers x.y » seulement
quand une version plus récente est connue, Redémarrer, Arrêter, et
« Créer un raccourci sur le Bureau » si l'application le fournit.
L'application ne donne que ses actions ; ce module porte ce que les quatre
ont appris à leurs dépens, repris du tray.py de blink2video :

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

import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

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
class Element:
    """Élément propre à une application, placé juste après Ouvrir.

    ``libelles`` : {"fr": ..., "en": ...}. ``coche`` : appelable rendant
    l'état d'une case à cocher (Pause de watch2notif), ou None."""
    libelles: dict
    action: Callable[[], None]
    coche: Optional[Callable[[], bool]] = None


@dataclass
class Actions:
    """Ce que l'application fournit. Seuls ouvrir, redemarrer et arreter
    sont obligatoires.

    ``redemarrer``, ``arreter`` et ``mettre_a_jour`` tournent sur un fil à
    part, l'icône se refermant aussitôt : ils arrêtent l'application (et la
    relancent, pour les deux derniers). ``version_disponible`` est appelée à
    chaque reconstruction du menu : elle doit rendre vite, sans réseau
    (un cache entretenu ailleurs), la version plus récente ou None."""
    ouvrir: Callable[[], None]
    redemarrer: Callable[[], None]
    arreter: Callable[[], None]
    version_disponible: Optional[Callable[[], Optional[str]]] = None
    mettre_a_jour: Optional[Callable[[], None]] = None
    creer_raccourci: Optional[Callable[[], None]] = None
    elements: Sequence[Element] = field(default_factory=tuple)
    langue: Callable[[], str] = lambda: "fr"


def disponible() -> bool:
    """Faux si pystray ou Pillow ne se chargent pas : bibliothèque absente,
    ou aucune zone de notification (Linux sans AppIndicator/GTK, session
    sans affichage). L'application continue alors sans icône."""
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
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


def entrees(actions: Actions, pystray, arreter_icone) -> list:
    """Les entrées du menu, dans l'ordre commun, pour la langue du moment.

    ``arreter_icone(action)`` : lance ``action`` sur un fil à part puis
    referme l'icône (voir Tray)."""
    langue = actions.langue()
    mots = libelles(langue)
    menu = [pystray.MenuItem(mots["ouvrir"], lambda icon, item: actions.ouvrir(),
                             default=True)]
    for element in actions.elements:
        texte = element.libelles.get(langue) or element.libelles.get("en", "")
        coche = None
        if element.coche is not None:
            coche = (lambda e: lambda item: e.coche())(element)
        menu.append(pystray.MenuItem(
            texte, (lambda e: lambda icon, item: e.action())(element), checked=coche))
    version = actions.version_disponible() if actions.version_disponible else None
    if version and actions.mettre_a_jour is not None:
        menu.append(pystray.MenuItem(mots["maj"].format(version=version),
                                     lambda icon, item: arreter_icone(actions.mettre_a_jour)))
    menu.append(pystray.MenuItem(mots["redemarrer"],
                                 lambda icon, item: arreter_icone(actions.redemarrer)))
    menu.append(pystray.MenuItem(mots["arreter"],
                                 lambda icon, item: arreter_icone(actions.arreter)))
    if actions.creer_raccourci is not None:
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
            import pystray
        if image is None:
            from PIL import Image
            image = Image.open(str(self.icone))
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
