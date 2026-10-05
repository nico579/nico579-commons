"""Icône de zone de notification par le protocole StatusNotifierItem.

Pourquoi : sous GNOME Wayland (Ubuntu), pystray retombe sur son mode X11
(XEmbed) dont le menu est une fenêtre X11 que Wayland n'ouvre pas : l'icône
s'affiche, le clic droit ne fait rien. Le bureau offre pourtant un hôte
StatusNotifierItem (extension ubuntu-appindicators de GNOME, KDE Plasma...),
standard freedesktop né de KDE en 2009 et repris par Canonical (AppIndicator).
On parle donc directement ce protocole sur le bus de session D-Bus, sans GTK ni
PyGObject, qu'un paquet gelé n'embarque pas : une bibliothèque D-Bus en Python
pur, jeepney (la même que celle de keyring), et une centaine de lignes.

Ce module est un sous-ensemble compatible de pystray : Icon, Menu et MenuItem
(texte, action, default), ce que tray.py emploie. tray.Tray le choisit sous
Linux quand un hôte est présent (utilisable()), pystray sinon.

Deux objets sont exportés sur le bus :
- /StatusNotifierItem (org.kde.StatusNotifierItem) : titre, icône en pixmap
  ARGB, clic gauche (Activate) ;
- /MenuBar (com.canonical.dbusmenu) : le menu du clic droit, que l'hôte
  demande par GetLayout et dont il renvoie les clics par Event.

La logique du protocole (Objets.repondre) ne touche pas au bus : elle reçoit
un message et rend la réponse, ce qui la rend testable partout. Icon la
branche sur le bus de session.
"""

from __future__ import annotations

import os
import sys
import threading
import types
from typing import Callable, Optional

WATCHER = "org.kde.StatusNotifierWatcher"
CHEMIN_WATCHER = "/StatusNotifierWatcher"
CHEMIN_ITEM = "/StatusNotifierItem"
CHEMIN_MENU = "/MenuBar"
IF_ITEM = "org.kde.StatusNotifierItem"
IF_MENU = "com.canonical.dbusmenu"
IF_PROPS = "org.freedesktop.DBus.Properties"
IF_INTRO = "org.freedesktop.DBus.Introspectable"

# Tailles proposées à l'hôte, qui choisit la plus proche de la sienne.
TAILLES_ICONE = (22, 32, 48, 64)
# Secondes entre deux contrôles de l'arrêt, dans la boucle de réception.
PAS_DE_BOUCLE_S = 0.5

# Bureaux dont l'hôte StatusNotifierItem peut arriver après notre démarrage
# (extension GNOME chargée avec la session) : on l'attend un peu avant de
# se rabattre sur pystray.
BUREAUX_SNI = ("gnome", "kde", "unity", "budgie", "cinnamon", "pantheon", "ubuntu")

PROPRIETES_ITEM = {
    "Category": "s", "Id": "s", "Title": "s", "Status": "s", "WindowId": "i",
    "IconName": "s", "IconPixmap": "a(iiay)", "OverlayIconName": "s",
    "OverlayIconPixmap": "a(iiay)", "AttentionIconName": "s",
    "AttentionIconPixmap": "a(iiay)", "AttentionMovieName": "s",
    "ToolTip": "(sa(iiay)ss)", "ItemIsMenu": "b", "Menu": "o",
    "IconThemePath": "s",
}
PROPRIETES_MENU = {"Version": "u", "TextDirection": "s", "Status": "s",
                   "IconThemePath": "as"}

_INTROSPECTION_ITEM = """<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="org.kde.StatusNotifierItem">
%(proprietes)s
    <method name="ContextMenu"><arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/></method>
    <method name="Activate"><arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/></method>
    <method name="Scroll"><arg type="i" name="delta" direction="in"/><arg type="s" name="orientation" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewStatus"><arg type="s" name="status"/></signal>
  </interface>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
    <method name="GetAll"><arg type="s" direction="in"/><arg type="a{sv}" direction="out"/></method>
  </interface>
  <interface name="org.freedesktop.DBus.Introspectable">
    <method name="Introspect"><arg type="s" direction="out"/></method>
  </interface>
</node>
"""

_INTROSPECTION_MENU = """<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="com.canonical.dbusmenu">
%(proprietes)s
    <method name="GetLayout"><arg type="i" name="parentId" direction="in"/><arg type="i" name="recursionDepth" direction="in"/><arg type="as" name="propertyNames" direction="in"/><arg type="u" name="revision" direction="out"/><arg type="(ia{sv}av)" name="layout" direction="out"/></method>
    <method name="GetGroupProperties"><arg type="ai" name="ids" direction="in"/><arg type="as" name="propertyNames" direction="in"/><arg type="a(ia{sv})" name="properties" direction="out"/></method>
    <method name="GetProperty"><arg type="i" name="id" direction="in"/><arg type="s" name="name" direction="in"/><arg type="v" name="value" direction="out"/></method>
    <method name="Event"><arg type="i" name="id" direction="in"/><arg type="s" name="eventId" direction="in"/><arg type="v" name="data" direction="in"/><arg type="u" name="timestamp" direction="in"/></method>
    <method name="EventGroup"><arg type="a(isvu)" name="events" direction="in"/><arg type="ai" name="idErrors" direction="out"/></method>
    <method name="AboutToShow"><arg type="i" name="id" direction="in"/><arg type="b" name="needUpdate" direction="out"/></method>
    <method name="AboutToShowGroup"><arg type="ai" name="ids" direction="in"/><arg type="ai" name="updatesNeeded" direction="out"/><arg type="ai" name="idErrors" direction="out"/></method>
    <signal name="LayoutUpdated"><arg type="u" name="revision"/><arg type="i" name="parent"/></signal>
    <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})" name="updatedProps"/><arg type="a(ias)" name="removedProps"/></signal>
    <signal name="ItemActivationRequested"><arg type="i" name="id"/><arg type="u" name="timestamp"/></signal>
  </interface>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
    <method name="GetAll"><arg type="s" direction="in"/><arg type="a{sv}" direction="out"/></method>
  </interface>
  <interface name="org.freedesktop.DBus.Introspectable">
    <method name="Introspect"><arg type="s" direction="out"/></method>
  </interface>
</node>
"""


def _xml_proprietes(table: dict) -> str:
    return "\n".join(f'    <property name="{nom}" type="{sig}" access="read"/>'
                     for nom, sig in table.items())


# --------------------------------------------------------------------------
# Le sous-ensemble de pystray que tray.py emploie

class MenuItem:
    """Une entrée : texte, action(icon, item), default (clic gauche)."""

    def __init__(self, text, action=None, default=False, enabled=True, visible=True):
        self.text, self.action = text, action
        self.default, self.enabled, self.visible = default, enabled, visible

    @property
    def libelle(self) -> str:
        return str(self.text(self) if callable(self.text) else self.text)


class Menu:
    """Un menu : des entrées, ou une fonction qui rend un itérable d'entrées
    (relue à chaque reconstruction, comme avec pystray)."""

    def __init__(self, *entrees):
        self._source = entrees[0] if len(entrees) == 1 and callable(entrees[0]) else None
        self._entrees = entrees

    def __iter__(self):
        return iter(self._source() if self._source else self._entrees)


# --------------------------------------------------------------------------
# Logique du protocole, sans bus

def _en_fond(fonction: Callable[[], None]) -> None:
    """Une action de menu tourne sur un fil à part : la boucle qui répond à
    l'hôte ne doit jamais attendre l'application (Arrêter peut durer)."""
    threading.Thread(target=fonction, daemon=True).start()


class Objets:
    """Les deux objets exportés. ``repondre(message)`` rend la réponse à un
    appel de méthode reçu, ou None s'il n'y en a pas à faire."""

    def __init__(self, nom: str, titre: str, pixmaps: list,
                 menu: Optional[Menu] = None, icone=None,
                 lancer: Callable = _en_fond):
        self.nom, self.titre, self.pixmaps = nom, titre, pixmaps
        self.menu = menu
        self.icone = icone
        self.lancer = lancer
        self.revision = 1
        self._verrou = threading.Lock()
        # Identifiants stables d'un libellé à l'autre reconstruction : un clic
        # qui répond à un ancien menu retombe sur la bonne entrée tant que son
        # libellé n'a pas changé.
        self._ids: dict = {}
        self._entrees: list = []
        self._signature: Optional[tuple] = None
        self.reconstruire()

    # -- menu -------------------------------------------------------------

    def reconstruire(self) -> bool:
        """Relit le menu ; vrai s'il a changé (la révision monte alors)."""
        entrees = list(self.menu) if self.menu is not None else []
        with self._verrou:
            vues = []
            for entree in entrees:
                libelle = entree.libelle
                identifiant = self._ids.setdefault(libelle, len(self._ids) + 1)
                vues.append((identifiant, entree))
            signature = tuple((i, e.libelle, e.enabled, e.visible) for i, e in vues)
            precedente = self._signature
            changee = signature != precedente
            self._entrees = vues
            self._signature = signature
            # La première lecture n'est pas un changement : l'hôte n'a encore
            # rien vu, la révision de départ lui convient.
            if changee and precedente is not None:
                self.revision += 1
        return changee

    def _proprietes_entree(self, entree) -> dict:
        proprietes = {"label": ("s", entree.libelle)}
        if not entree.enabled:
            proprietes["enabled"] = ("b", False)
        return proprietes

    def _structure(self, enfants: bool = True):
        with self._verrou:
            noeuds = [("(ia{sv}av)", (identifiant, self._proprietes_entree(entree), []))
                      for identifiant, entree in self._entrees if entree.visible]
        return (0, {"children-display": ("s", "submenu")}, noeuds if enfants else [])

    def _entree(self, identifiant: int):
        with self._verrou:
            return next((e for i, e in self._entrees if i == identifiant), None)

    def entree_par_defaut(self):
        with self._verrou:
            return next((e for _, e in self._entrees if e.default), None)

    def _declencher(self, entree) -> None:
        if entree is not None and entree.action is not None:
            self.lancer(lambda: entree.action(self.icone, entree))

    # -- propriétés -------------------------------------------------------

    def valeur_item(self, nom: str):
        valeurs = {
            "Category": "ApplicationStatus", "Id": self.nom, "Title": self.titre,
            "Status": "Active", "WindowId": 0, "IconName": "",
            "IconPixmap": [(w, h, donnees) for w, h, donnees in self.pixmaps],
            "OverlayIconName": "", "OverlayIconPixmap": [],
            "AttentionIconName": "", "AttentionIconPixmap": [],
            "AttentionMovieName": "",
            "ToolTip": ("", [], self.titre, ""),
            "ItemIsMenu": False, "Menu": CHEMIN_MENU, "IconThemePath": "",
        }
        return PROPRIETES_ITEM[nom], valeurs[nom]

    def valeur_menu(self, nom: str):
        valeurs = {"Version": 3, "TextDirection": "ltr", "Status": "normal",
                   "IconThemePath": []}
        return PROPRIETES_MENU[nom], valeurs[nom]

    # -- messages ---------------------------------------------------------

    def repondre(self, message):
        from jeepney import new_error, new_method_return

        champs = message.header.fields
        # Les champs sont indexés par HeaderFields : on les lit par leur nom.
        chemin, interface, membre = _champs(champs)
        corps = message.body

        def erreur(nom, texte):
            return new_error(message, nom, "s", (texte,))

        if interface == IF_INTRO and membre == "Introspect":
            if chemin == CHEMIN_ITEM:
                xml = _INTROSPECTION_ITEM % {"proprietes": _xml_proprietes(PROPRIETES_ITEM)}
            elif chemin == CHEMIN_MENU:
                xml = _INTROSPECTION_MENU % {"proprietes": _xml_proprietes(PROPRIETES_MENU)}
            else:
                xml = "<node/>"
            return new_method_return(message, "s", (xml,))

        if chemin == CHEMIN_ITEM:
            if interface == IF_PROPS:
                return self._proprietes(message, corps, PROPRIETES_ITEM, IF_ITEM,
                                        self.valeur_item, erreur)
            if interface == IF_ITEM and membre in ("Activate", "SecondaryActivate",
                                                   "ContextMenu", "Scroll"):
                if membre == "Activate":
                    self._declencher(self.entree_par_defaut())
                return new_method_return(message)
            return erreur("org.freedesktop.DBus.Error.UnknownMethod",
                          f"{interface}.{membre} inconnue")

        if chemin == CHEMIN_MENU:
            if interface == IF_PROPS:
                return self._proprietes(message, corps, PROPRIETES_MENU, IF_MENU,
                                        self.valeur_menu, erreur)
            if interface == IF_MENU:
                return self._methode_menu(message, membre, corps, erreur)
            return erreur("org.freedesktop.DBus.Error.UnknownInterface", str(interface))

        return erreur("org.freedesktop.DBus.Error.UnknownObject", str(chemin))

    def _proprietes(self, message, corps, table, interface_propre, valeur, erreur):
        from jeepney import new_method_return

        membre = _champs(message.header.fields)[2]
        if membre == "GetAll":
            interface = corps[0] if corps else ""
            if interface not in (interface_propre, ""):
                return new_method_return(message, "a{sv}", ({},))
            return new_method_return(message, "a{sv}", ({
                nom: valeur(nom) for nom in table},))
        if membre == "Get":
            interface, nom = corps[0], corps[1]
            if interface != interface_propre:
                return erreur("org.freedesktop.DBus.Error.UnknownInterface", str(interface))
            if nom not in table:
                return erreur("org.freedesktop.DBus.Error.UnknownProperty", str(nom))
            return new_method_return(message, "v", (valeur(nom),))
        if membre == "Set":
            return erreur("org.freedesktop.DBus.Error.PropertyReadOnly", "lecture seule")
        return erreur("org.freedesktop.DBus.Error.UnknownMethod", str(membre))

    def _methode_menu(self, message, membre, corps, erreur):
        from jeepney import new_method_return

        if membre == "GetLayout":
            parent, profondeur = corps[0], corps[1]
            if parent == 0:
                racine = self._structure(enfants=profondeur != 0)
            else:
                entree = self._entree(parent)
                if entree is None:
                    return erreur("org.freedesktop.DBus.Error.InvalidArgs",
                                  f"identifiant {parent} inconnu")
                racine = (parent, self._proprietes_entree(entree), [])
            return new_method_return(message, "u(ia{sv}av)", (self.revision, racine))
        if membre == "GetGroupProperties":
            identifiants = corps[0] or [0] + [i for i, _ in self._entrees]
            reponse = []
            for identifiant in identifiants:
                if identifiant == 0:
                    reponse.append((0, {"children-display": ("s", "submenu")}))
                    continue
                entree = self._entree(identifiant)
                if entree is not None:
                    reponse.append((identifiant, self._proprietes_entree(entree)))
            return new_method_return(message, "a(ia{sv})", (reponse,))
        if membre == "GetProperty":
            identifiant, nom = corps[0], corps[1]
            entree = self._entree(identifiant)
            proprietes = self._proprietes_entree(entree) if entree is not None else {}
            if nom not in proprietes:
                return erreur("org.freedesktop.DBus.Error.InvalidArgs",
                              f"propriété {nom} absente")
            return new_method_return(message, "v", (proprietes[nom],))
        if membre == "Event":
            identifiant, evenement = corps[0], corps[1]
            if evenement == "clicked":
                self._declencher(self._entree(identifiant))
            return new_method_return(message)
        if membre == "EventGroup":
            inconnus = []
            for identifiant, evenement, _donnees, _horodatage in corps[0]:
                entree = self._entree(identifiant)
                if entree is None:
                    inconnus.append(identifiant)
                elif evenement == "clicked":
                    self._declencher(entree)
            return new_method_return(message, "ai", (inconnus,))
        if membre == "AboutToShow":
            return new_method_return(message, "b", (self.reconstruire(),))
        if membre == "AboutToShowGroup":
            changee = self.reconstruire()
            return new_method_return(message, "aiai", ([0] if changee else [], []))
        return erreur("org.freedesktop.DBus.Error.UnknownMethod", f"{membre} inconnue")


def _champs(champs) -> tuple:
    """(chemin, interface, membre) d'un message, quelle que soit la forme où
    jeepney indexe les champs d'en-tête."""
    from jeepney import HeaderFields

    return (champs.get(HeaderFields.path), champs.get(HeaderFields.interface),
            champs.get(HeaderFields.member))


# --------------------------------------------------------------------------
# Image -> pixmaps ARGB32

def pixmaps_depuis_image(image, tailles=TAILLES_ICONE) -> list:
    """Liste de (largeur, hauteur, octets) : ARGB32, octet de poids fort en
    tête (l'ordre réseau de la spécification), plusieurs tailles."""
    from PIL import Image

    reechantillon = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
    base = image.convert("RGBA")
    pixmaps = []
    for taille in tailles:
        carre = base.resize((taille, taille), reechantillon)
        rouge, vert, bleu, alpha = carre.split()
        argb = Image.merge("RGBA", (alpha, rouge, vert, bleu))
        pixmaps.append((taille, taille, argb.tobytes()))
    return pixmaps


# --------------------------------------------------------------------------
# Détection de l'hôte

def _bureau_sni() -> bool:
    bureau = (os.environ.get("XDG_CURRENT_DESKTOP", "") + ":"
              + os.environ.get("DESKTOP_SESSION", "")).lower()
    return any(nom in bureau for nom in BUREAUX_SNI)


def hote_disponible(delai_s: float = 2.0) -> bool:
    """Vrai si un StatusNotifierWatcher avec au moins un hôte tourne sur le
    bus de session. Faux (jamais d'exception) sans bus, sans jeepney, ou sans
    hôte : l'appelant se rabat alors sur pystray."""
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.bus_messages import message_bus
        from jeepney.io.blocking import open_dbus_connection

        connexion = open_dbus_connection(bus="SESSION")
        try:
            presente = connexion.send_and_get_reply(
                message_bus.NameHasOwner(WATCHER), timeout=delai_s).body[0]
            if not presente:
                return False
            adresse = DBusAddress(CHEMIN_WATCHER, bus_name=WATCHER, interface=IF_PROPS)
            reponse = connexion.send_and_get_reply(new_method_call(
                adresse, "Get", "ss", (WATCHER, "IsStatusNotifierHostRegistered")),
                timeout=delai_s)
            valeur = reponse.body[0]
            if isinstance(valeur, tuple):
                valeur = valeur[1]
            return bool(valeur)
        finally:
            connexion.close()
    except Exception:
        return False


def utilisable(attente_s: float = 10.0, pas_s: float = 1.0, horloge=None,
               dormir=None) -> bool:
    """Faut-il employer ce module plutôt que pystray ?

    Seulement sous Linux et si un hôte est là. Sur un bureau qui en a un par
    construction (GNOME, KDE...), il peut s'enregistrer un peu après nous, la
    session venant de s'ouvrir : on l'attend ``attente_s`` secondes avant de
    renoncer."""
    import time

    if not sys.platform.startswith("linux"):
        return False
    try:
        import jeepney  # noqa: F401
    except ImportError:
        return False
    if hote_disponible():
        return True
    if not _bureau_sni():
        return False
    horloge = horloge or time.monotonic
    dormir = dormir or time.sleep
    fin = horloge() + attente_s
    while horloge() < fin:
        dormir(pas_s)
        if hote_disponible():
            return True
    return False


# --------------------------------------------------------------------------
# L'icône, branchée sur le bus de session

class Icon:
    """Remplace pystray.Icon : run() bloque jusqu'à stop()."""

    def __init__(self, nom, image, titre, menu=None):
        self.nom, self.titre, self.menu = nom, titre, menu
        self._pixmaps = pixmaps_depuis_image(image)
        self._arret = threading.Event()
        self._envoi = threading.Lock()
        self._connexion = None
        self._nom_bus = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.objets = Objets(nom, titre, self._pixmaps, menu, icone=self)

    # -- API pystray ------------------------------------------------------

    def stop(self) -> None:
        self._arret.set()

    def update_menu(self) -> None:
        if self.objets.reconstruire():
            self._emettre_layout()

    def run(self) -> None:
        from jeepney import MessageType
        from jeepney.bus_messages import message_bus
        from jeepney.io.blocking import open_dbus_connection

        connexion = open_dbus_connection(bus="SESSION")
        self._connexion = connexion
        try:
            # Avant d'être connus de quiconque : on peut attendre les réponses.
            connexion.send_and_get_reply(message_bus.RequestName(self._nom_bus), timeout=5)
            connexion.send_and_get_reply(message_bus.AddMatch(self._regle_watcher()), timeout=5)
            if connexion.send_and_get_reply(
                    message_bus.NameHasOwner(WATCHER), timeout=5).body[0]:
                self._enregistrer()
            self._pomper(connexion, MessageType)
        finally:
            self._connexion = None
            try:
                connexion.close()
            except OSError:
                pass

    # -- bus --------------------------------------------------------------

    @staticmethod
    def _regle_watcher():
        from jeepney import MatchRule

        regle = MatchRule(type="signal", sender="org.freedesktop.DBus",
                          interface="org.freedesktop.DBus", member="NameOwnerChanged",
                          path="/org/freedesktop/DBus")
        regle.add_arg_condition(0, WATCHER)
        return regle

    def _envoyer(self, message) -> None:
        connexion = self._connexion
        if connexion is None:
            return
        with self._envoi:
            connexion.send(message)

    def _enregistrer(self) -> None:
        """S'annonce au watcher, sans attendre sa réponse : l'hôte nous
        interroge dès l'enregistrement, et ces appels doivent être servis par
        la boucle, pas par une attente qui les laisserait sans réponse."""
        from jeepney import DBusAddress, new_method_call

        adresse = DBusAddress(CHEMIN_WATCHER, bus_name=WATCHER, interface=WATCHER)
        self._envoyer(new_method_call(
            adresse, "RegisterStatusNotifierItem", "s", (self._nom_bus,)))

    def _emettre_layout(self) -> None:
        from jeepney import DBusAddress, new_signal

        adresse = DBusAddress(CHEMIN_MENU, interface=IF_MENU)
        self._envoyer(new_signal(adresse, "LayoutUpdated", "ui", (self.objets.revision, 0)))

    def _pomper(self, connexion, MessageType) -> None:
        while not self._arret.is_set():
            try:
                message = connexion.receive(timeout=PAS_DE_BOUCLE_S)
            except TimeoutError:
                continue
            except OSError:
                return  # bus fermé : plus d'icône, l'application continue
            genre = message.header.message_type
            if genre == MessageType.method_call:
                reponse = self.objets.repondre(message)
                if reponse is not None:
                    self._envoyer(reponse)
            elif genre == MessageType.signal:
                corps = message.body
                if corps and corps[0] == WATCHER and len(corps) >= 3 and corps[2]:
                    # Le watcher (re)démarre : on se réenregistre.
                    self._enregistrer()


# Ce que tray.Tray reçoit à la place du module pystray.
PYSTRAY_COMPATIBLE = types.SimpleNamespace(Icon=Icon, Menu=Menu, MenuItem=MenuItem)
