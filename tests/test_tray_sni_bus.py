"""StatusNotifierItem sur un vrai bus de session D-Bus : un faux watcher (le rôle
de l'extension de GNOME), l'icône réelle, et un « hôte » qui l'interroge comme
le fait la barre du bureau (GetAll, GetLayout, Event, Activate).

Demande un bus de session : sous Linux, lancer les tests dans

    dbus-run-session -- python -m unittest tests.test_tray_sni_bus -v

Sans bus (poste Windows ou macOS, Linux sans session), ils sont sautés.
"""

import os
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from jeepney import (DBusAddress, HeaderFields, MatchRule, MessageType,
                         new_method_call, new_method_return)
    from jeepney.bus_messages import message_bus
    from jeepney.io.blocking import open_dbus_connection
    from PIL import Image
    ABSENT = None
except ImportError:
    ABSENT = "jeepney et Pillow non installés"

if ABSENT is None:
    from nico579_commons import tray_sni as sni

if ABSENT is None and not (sys.platform.startswith("linux")
                           and os.environ.get("DBUS_SESSION_BUS_ADDRESS")):
    ABSENT = "pas de bus de session D-Bus (dbus-run-session)"


class FauxWatcher(threading.Thread):
    """Ce que fait l'extension : posséder le nom, prendre les inscriptions."""

    def __init__(self, hote=True):
        super().__init__(daemon=True)
        self.hote = hote
        self.connexion = open_dbus_connection(bus="SESSION")
        reponse = self.connexion.send_and_get_reply(message_bus.RequestName(sni.WATCHER))
        assert reponse.body[0] == 1, reponse.body
        self.enregistres = []
        self.inscrit = threading.Event()
        self.arret = threading.Event()
        self.start()

    def run(self):
        while not self.arret.is_set():
            try:
                message = self.connexion.receive(timeout=0.2)
            except TimeoutError:
                continue
            except OSError:
                return
            if message.header.message_type != MessageType.method_call:
                continue
            membre = message.header.fields.get(HeaderFields.member)
            if membre == "RegisterStatusNotifierItem":
                self.enregistres.append(message.body[0])
                self.inscrit.set()
                reponse = new_method_return(message)
            elif membre == "Get":
                reponse = new_method_return(message, "v", (("b", self.hote),))
            else:
                continue
            self.connexion.send(reponse)

    def fermer(self):
        self.arret.set()
        self.join(timeout=2)
        self.connexion.close()


def adresse(destination, chemin, interface):
    return DBusAddress(chemin, bus_name=destination, interface=interface)


@unittest.skipIf(ABSENT, ABSENT or "")
class SurUnVraiBus(unittest.TestCase):
    def setUp(self):
        self.journal = []
        self.arretee = threading.Event()
        image = Image.new("RGBA", (32, 32), (200, 30, 30, 255))
        self.items = [
            sni.MenuItem("Ouvrir", lambda i, e: self.journal.append("ouvrir"), default=True),
            sni.MenuItem("Arrêter", lambda i, e: self.arretee.set()),
        ]
        menu = sni.Menu(lambda: iter(self.items))
        self.icone = sni.Icon("essai", image, "Essai", menu=menu)
        self.client = open_dbus_connection(bus="SESSION")
        self.addCleanup(self.client.close)
        self.fils = None

    def demarrer(self):
        self.fils = threading.Thread(target=self.icone.run, daemon=True)
        self.fils.start()

    def arreter(self):
        self.icone.stop()
        if self.fils is not None:
            self.fils.join(timeout=5)
            self.assertFalse(self.fils.is_alive(), "run() ne rend pas la main après stop()")

    def appeler(self, destination, chemin, interface, methode, signature=None, corps=()):
        message = new_method_call(adresse(destination, chemin, interface), methode,
                                  signature, corps)
        reponse = self.client.send_and_get_reply(message, timeout=5)
        self.assertNotEqual(reponse.header.message_type, MessageType.error, reponse.body)
        return reponse.body

    def test_hote_disponible_selon_le_watcher(self):
        self.assertFalse(sni.hote_disponible(delai_s=2), "aucun watcher : faux")
        sans_hote = FauxWatcher(hote=False)
        try:
            self.assertFalse(sni.hote_disponible(delai_s=2), "watcher sans hôte : faux")
        finally:
            sans_hote.fermer()
        avec_hote = FauxWatcher(hote=True)
        try:
            self.assertTrue(sni.hote_disponible(delai_s=2))
        finally:
            avec_hote.fermer()

    def test_inscription_puis_dialogue_avec_l_hote(self):
        watcher = FauxWatcher()
        self.addCleanup(watcher.fermer)
        self.demarrer()
        self.addCleanup(self.arreter)
        self.assertTrue(watcher.inscrit.wait(5), "l'icône ne s'est pas inscrite au watcher")
        nom_bus = watcher.enregistres[0]
        self.assertTrue(nom_bus.startswith("org.kde.StatusNotifierItem-"), nom_bus)

        proprietes, = self.appeler(nom_bus, sni.CHEMIN_ITEM, sni.IF_PROPS, "GetAll", "s",
                                   (sni.IF_ITEM,))
        self.assertEqual(proprietes["Title"], ("s", "Essai"))
        self.assertEqual(proprietes["Id"], ("s", "essai"))
        self.assertEqual(proprietes["IconPixmap"][1][0][:2], (sni.TAILLES_ICONE[0],) * 2)

        revision, (racine, _, enfants) = self.appeler(
            nom_bus, sni.CHEMIN_MENU, sni.IF_MENU, "GetLayout", "iias", (0, -1, []))
        libelles = {n[1][1]["label"][1]: n[1][0] for n in enfants}
        self.assertEqual(list(libelles), ["Ouvrir", "Arrêter"])

        # Clic gauche, puis clic sur « Arrêter » dans le menu.
        self.appeler(nom_bus, sni.CHEMIN_ITEM, sni.IF_ITEM, "Activate", "ii", (0, 0))
        deadline = time.monotonic() + 3
        while "ouvrir" not in self.journal and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.journal, ["ouvrir"])
        self.appeler(nom_bus, sni.CHEMIN_MENU, sni.IF_MENU, "Event", "isvu",
                     (libelles["Arrêter"], "clicked", ("i", 0), 0))
        self.assertTrue(self.arretee.wait(3), "le clic sur Arrêter n'a rien déclenché")

    def test_changement_de_menu_emet_layoutupdated(self):
        watcher = FauxWatcher()
        self.addCleanup(watcher.fermer)
        self.demarrer()
        self.addCleanup(self.arreter)
        self.assertTrue(watcher.inscrit.wait(5))
        regle = MatchRule(type="signal", interface=sni.IF_MENU, member="LayoutUpdated")
        self.client.send_and_get_reply(message_bus.AddMatch(regle), timeout=5)
        self.items.insert(1, sni.MenuItem("Mettre à jour vers 2.0", lambda i, e: None))
        self.icone.update_menu()
        signal = None
        deadline = time.monotonic() + 3
        while signal is None and time.monotonic() < deadline:
            try:
                message = self.client.receive(timeout=0.3)
            except TimeoutError:
                continue
            if message.header.message_type == MessageType.signal:
                signal = message
        self.assertIsNotNone(signal, "aucun LayoutUpdated reçu")
        self.assertEqual(signal.body[1], 0)

    def test_menu_inchange_n_emet_rien(self):
        watcher = FauxWatcher()
        self.addCleanup(watcher.fermer)
        self.demarrer()
        self.addCleanup(self.arreter)
        self.assertTrue(watcher.inscrit.wait(5))
        regle = MatchRule(type="signal", interface=sni.IF_MENU, member="LayoutUpdated")
        self.client.send_and_get_reply(message_bus.AddMatch(regle), timeout=5)
        self.icone.update_menu()
        with self.assertRaises(TimeoutError):
            while True:
                message = self.client.receive(timeout=0.6)
                self.assertNotEqual(message.header.message_type, MessageType.signal)

    def test_le_watcher_qui_redemarre_recoit_une_nouvelle_inscription(self):
        premier = FauxWatcher()
        self.demarrer()
        self.addCleanup(self.arreter)
        self.assertTrue(premier.inscrit.wait(5))
        premier.fermer()
        second = FauxWatcher()
        self.addCleanup(second.fermer)
        self.assertTrue(second.inscrit.wait(5), "pas de réinscription au nouveau watcher")

    def test_icone_demarree_avant_le_watcher_s_inscrit_quand_il_arrive(self):
        self.demarrer()
        self.addCleanup(self.arreter)
        time.sleep(0.5)
        watcher = FauxWatcher()
        self.addCleanup(watcher.fermer)
        self.assertTrue(watcher.inscrit.wait(5), "l'icône n'a pas attendu le watcher")

    def test_stop_rend_la_main(self):
        watcher = FauxWatcher()
        self.addCleanup(watcher.fermer)
        self.demarrer()
        self.assertTrue(watcher.inscrit.wait(5))
        debut = time.monotonic()
        self.arreter()
        self.assertLess(time.monotonic() - debut, 3)


if __name__ == "__main__":
    unittest.main()
