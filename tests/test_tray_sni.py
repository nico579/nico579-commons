"""Tests du protocole StatusNotifierItem (tray_sni), sans bus : Objets.repondre
reçoit un message D-Bus et rend la réponse. Chaque réponse est aussi
sérialisée puis relue par l'analyseur de jeepney, ce qui éprouve les
signatures (un type mal déclaré lève à la sérialisation, pas chez l'hôte).

    python -m unittest discover -s tests
"""

import sys
import threading
import unittest
import xml.dom.minidom
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import jeepney
    from jeepney import DBusAddress, MessageType, Parser, new_method_call
    from PIL import Image
except ImportError:  # extra « tray » non installé
    jeepney = None

if jeepney is not None:
    from nico579_commons import tray as apptray
    from nico579_commons import tray_sni as sni


def relu(message):
    """Le message, sérialisé puis relu comme le ferait l'hôte."""
    messages = Parser().feed(message.serialise(serial=7))
    assert len(messages) == 1, messages
    return messages[0]


def appel(chemin, interface, methode, signature="", corps=()):
    adresse = DBusAddress(chemin, bus_name=":1.99", interface=interface)
    message = new_method_call(adresse, methode, signature or None, corps)
    return relu(message)


@unittest.skipIf(jeepney is None, "jeepney et Pillow non installés")
class Base(unittest.TestCase):
    def setUp(self):
        self.journal = []
        self.items = [
            sni.MenuItem("Ouvrir", lambda icon, item: self.journal.append("ouvrir"),
                         default=True),
            sni.MenuItem("Arrêter", lambda icon, item: self.journal.append("arreter")),
        ]
        self.menu = sni.Menu(lambda: iter(self.items))
        # Les actions tournent ici sur le fil de l'appel : le test reste déterministe.
        self.objets = sni.Objets("blink2video", "Blink", [(2, 2, b"\x00" * 16)],
                                 self.menu, icone="ICONE", lancer=lambda f: f())

    def repondre(self, *args, **kw):
        reponse = self.objets.repondre(appel(*args, **kw))
        self.assertIsNotNone(reponse)
        return relu(reponse)

    def layout(self):
        reponse = self.repondre(sni.CHEMIN_MENU, sni.IF_MENU, "GetLayout", "iias",
                                (0, -1, []))
        revision, (racine, proprietes, enfants) = reponse.body
        return revision, racine, proprietes, enfants


class Proprietes(Base):
    def test_getall_de_l_item(self):
        reponse = self.repondre(sni.CHEMIN_ITEM, sni.IF_PROPS, "GetAll", "s", (sni.IF_ITEM,))
        proprietes, = reponse.body
        self.assertEqual(set(proprietes), set(sni.PROPRIETES_ITEM))
        self.assertEqual(proprietes["Id"], ("s", "blink2video"))
        self.assertEqual(proprietes["Title"], ("s", "Blink"))
        self.assertEqual(proprietes["Status"], ("s", "Active"))
        self.assertEqual(proprietes["Menu"], ("o", sni.CHEMIN_MENU))
        # Clic gauche = Activate, pas le menu : ItemIsMenu faux.
        self.assertEqual(proprietes["ItemIsMenu"], ("b", False))
        self.assertEqual(proprietes["IconPixmap"][0], "a(iiay)")
        self.assertEqual(proprietes["IconPixmap"][1], [(2, 2, b"\x00" * 16)])

    def test_get_d_une_propriete(self):
        reponse = self.repondre(sni.CHEMIN_ITEM, sni.IF_PROPS, "Get", "ss",
                                (sni.IF_ITEM, "Title"))
        self.assertEqual(reponse.body, (("s", "Blink"),))

    def test_propriete_inconnue_et_ecriture_refusees(self):
        for methode, signature, corps, nom in (
            ("Get", "ss", (sni.IF_ITEM, "Nope"), "UnknownProperty"),
            ("Get", "ss", ("autre.Interface", "Title"), "UnknownInterface"),
            ("Set", "ssv", (sni.IF_ITEM, "Title", ("s", "x")), "PropertyReadOnly"),
        ):
            with self.subTest(methode=methode, nom=nom):
                reponse = self.repondre(sni.CHEMIN_ITEM, sni.IF_PROPS, methode, signature, corps)
                self.assertEqual(reponse.header.message_type, MessageType.error)
                self.assertTrue(reponse.header.fields[jeepney.HeaderFields.error_name]
                                .endswith(nom))

    def test_proprietes_du_menu(self):
        reponse = self.repondre(sni.CHEMIN_MENU, sni.IF_PROPS, "GetAll", "s", (sni.IF_MENU,))
        proprietes, = reponse.body
        self.assertEqual(proprietes["Version"], ("u", 3))
        self.assertEqual(proprietes["Status"], ("s", "normal"))

    def test_introspection_bien_formee(self):
        for chemin, interface in ((sni.CHEMIN_ITEM, sni.IF_ITEM), (sni.CHEMIN_MENU, sni.IF_MENU)):
            with self.subTest(chemin=chemin):
                reponse = self.repondre(chemin, sni.IF_INTRO, "Introspect")
                document = xml.dom.minidom.parseString(reponse.body[0])
                noms = [n.getAttribute("name") for n in document.getElementsByTagName("interface")]
                self.assertIn(interface, noms)


class Menu(Base):
    def test_layout_a_les_libelles_dans_l_ordre(self):
        revision, racine, proprietes, enfants = self.layout()
        self.assertEqual(racine, 0)
        self.assertEqual(proprietes["children-display"], ("s", "submenu"))
        libelles = [noeud[1][1]["label"][1] for noeud in enfants]
        self.assertEqual(libelles, ["Ouvrir", "Arrêter"])
        identifiants = [noeud[1][0] for noeud in enfants]
        self.assertEqual(len(set(identifiants)), 2)
        self.assertNotIn(0, identifiants)
        self.assertIsInstance(revision, int)

    def test_clic_declenche_l_action_de_la_bonne_entree(self):
        _, _, _, enfants = self.layout()
        identifiant = enfants[1][1][0]
        self.repondre(sni.CHEMIN_MENU, sni.IF_MENU, "Event", "isvu",
                      (identifiant, "clicked", ("i", 0), 0))
        self.assertEqual(self.journal, ["arreter"])

    def test_l_action_recoit_l_icone_et_l_entree(self):
        recu = []
        self.items[0].action = lambda icon, item: recu.append((icon, item))
        _, _, _, enfants = self.layout()
        self.repondre(sni.CHEMIN_MENU, sni.IF_MENU, "Event", "isvu",
                      (enfants[0][1][0], "clicked", ("i", 0), 0))
        self.assertEqual(recu, [("ICONE", self.items[0])])

    def test_autres_evenements_ne_declenchent_rien(self):
        _, _, _, enfants = self.layout()
        for evenement in ("opened", "closed", "hovered"):
            self.repondre(sni.CHEMIN_MENU, sni.IF_MENU, "Event", "isvu",
                          (enfants[0][1][0], evenement, ("i", 0), 0))
        self.assertEqual(self.journal, [])

    def test_event_d_un_identifiant_inconnu_est_sans_effet(self):
        reponse = self.repondre(sni.CHEMIN_MENU, sni.IF_MENU, "Event", "isvu",
                                (999, "clicked", ("i", 0), 0))
        self.assertEqual(reponse.header.message_type, MessageType.method_return)
        self.assertEqual(self.journal, [])

    def test_eventgroup_rend_les_identifiants_inconnus(self):
        _, _, _, enfants = self.layout()
        reponse = self.repondre(sni.CHEMIN_MENU, sni.IF_MENU, "EventGroup", "a(isvu)",
                                ([(enfants[0][1][0], "clicked", ("i", 0), 0),
                                  (555, "clicked", ("i", 0), 0)],))
        self.assertEqual(reponse.body, ([555],))
        self.assertEqual(self.journal, ["ouvrir"])

    def test_clic_gauche_active_l_entree_par_defaut(self):
        self.repondre(sni.CHEMIN_ITEM, sni.IF_ITEM, "Activate", "ii", (10, 20))
        self.assertEqual(self.journal, ["ouvrir"])

    def test_activate_sans_entree_par_defaut_ne_plante_pas(self):
        self.items[0].default = False
        reponse = self.repondre(sni.CHEMIN_ITEM, sni.IF_ITEM, "Activate", "ii", (0, 0))
        self.assertEqual(reponse.header.message_type, MessageType.method_return)
        self.assertEqual(self.journal, [])

    def test_getgroupproperties_et_getproperty(self):
        _, _, _, enfants = self.layout()
        identifiant = enfants[0][1][0]
        groupe = self.repondre(sni.CHEMIN_MENU, sni.IF_MENU, "GetGroupProperties", "aias",
                               ([identifiant], []))
        self.assertEqual(groupe.body, ([(identifiant, {"label": ("s", "Ouvrir")})],))
        valeur = self.repondre(sni.CHEMIN_MENU, sni.IF_MENU, "GetProperty", "is",
                               (identifiant, "label"))
        self.assertEqual(valeur.body, (("s", "Ouvrir"),))

    def test_entree_desactivee_ou_masquee(self):
        self.items[1].enabled = False
        self.items.append(sni.MenuItem("Caché", None, visible=False))
        self.objets.reconstruire()
        _, _, _, enfants = self.layout()
        self.assertEqual([n[1][1]["label"][1] for n in enfants], ["Ouvrir", "Arrêter"])
        self.assertEqual(enfants[1][1][1]["enabled"], ("b", False))


class Reconstruction(Base):
    def test_menu_inchange_ne_monte_pas_la_revision(self):
        revision = self.objets.revision
        self.assertFalse(self.objets.reconstruire())
        self.assertEqual(self.objets.revision, revision)

    def test_menu_change_monte_la_revision_et_aboutToShow_le_dit(self):
        revision = self.objets.revision
        reponse = self.repondre(sni.CHEMIN_MENU, sni.IF_MENU, "AboutToShow", "i", (0,))
        self.assertEqual(reponse.body, (False,))
        self.items.insert(1, sni.MenuItem("Mettre à jour vers 2.0", lambda i, e: None))
        reponse = self.repondre(sni.CHEMIN_MENU, sni.IF_MENU, "AboutToShow", "i", (0,))
        self.assertEqual(reponse.body, (True,))
        self.assertEqual(self.objets.revision, revision + 1)

    def test_les_identifiants_survivent_a_l_insertion_d_une_entree(self):
        _, _, _, avant = self.layout()
        ids_avant = {n[1][1]["label"][1]: n[1][0] for n in avant}
        self.items.insert(1, sni.MenuItem("Mettre à jour vers 2.0", lambda i, e: None))
        self.objets.reconstruire()
        _, _, _, apres = self.layout()
        ids_apres = {n[1][1]["label"][1]: n[1][0] for n in apres}
        # Un clic envoyé d'après l'ancien menu retombe sur la bonne entrée.
        self.assertEqual(ids_apres["Ouvrir"], ids_avant["Ouvrir"])
        self.assertEqual(ids_apres["Arrêter"], ids_avant["Arrêter"])
        self.assertNotIn(ids_apres["Mettre à jour vers 2.0"], ids_avant.values())

    def test_aboutToShowGroup(self):
        reponse = self.repondre(sni.CHEMIN_MENU, sni.IF_MENU, "AboutToShowGroup", "ai", ([0],))
        self.assertEqual(reponse.body, ([], []))


class Erreurs(Base):
    def test_methode_inconnue(self):
        reponse = self.repondre(sni.CHEMIN_ITEM, sni.IF_ITEM, "Fabuleuse", "")
        self.assertEqual(reponse.header.message_type, MessageType.error)

    def test_objet_inconnu(self):
        reponse = self.repondre("/Autre", sni.IF_ITEM, "Activate", "ii", (0, 0))
        self.assertEqual(reponse.header.message_type, MessageType.error)

    def test_getlayout_d_un_identifiant_inconnu(self):
        reponse = self.repondre(sni.CHEMIN_MENU, sni.IF_MENU, "GetLayout", "iias", (42, 1, []))
        self.assertEqual(reponse.header.message_type, MessageType.error)


@unittest.skipIf(jeepney is None, "jeepney et Pillow non installés")
class Pixmaps(unittest.TestCase):
    def test_ordre_argb_et_tailles(self):
        image = Image.new("RGBA", (8, 8), (255, 0, 0, 128))
        pixmaps = sni.pixmaps_depuis_image(image, tailles=(4, 6))
        self.assertEqual([(w, h) for w, h, _ in pixmaps], [(4, 4), (6, 6)])
        for w, h, donnees in pixmaps:
            self.assertEqual(len(donnees), 4 * w * h)
            # alpha, rouge, vert, bleu pour chaque pixel
            self.assertEqual(donnees[:4], bytes([128, 255, 0, 0]))

    def test_image_sans_alpha(self):
        pixmaps = sni.pixmaps_depuis_image(Image.new("RGB", (4, 4), (0, 255, 0)), tailles=(2,))
        self.assertEqual(pixmaps[0][2][:4], bytes([255, 0, 255, 0]))

    def test_les_pixmaps_passent_la_serialisation(self):
        image = Image.new("RGBA", (22, 22), (10, 20, 30, 255))
        objets = sni.Objets("x", "X", sni.pixmaps_depuis_image(image), sni.Menu(), lancer=lambda f: f())
        reponse = objets.repondre(appel(sni.CHEMIN_ITEM, sni.IF_PROPS, "Get", "ss",
                                        (sni.IF_ITEM, "IconPixmap")))
        envoye = relu(reponse).body[0]
        self.assertEqual([(w, h) for w, h, _ in envoye[1]], [(s, s) for s in sni.TAILLES_ICONE])


@unittest.skipIf(jeepney is None, "jeepney et Pillow non installés")
class Choix(unittest.TestCase):
    def test_pas_de_statusnotifier_hors_linux(self):
        from unittest import mock
        with mock.patch.object(sni.sys, "platform", "win32"):
            self.assertFalse(sni.utilisable())

    def test_hote_present_tout_de_suite(self):
        from unittest import mock
        with mock.patch.object(sni.sys, "platform", "linux"), \
                mock.patch.object(sni, "hote_disponible", return_value=True):
            self.assertTrue(sni.utilisable())

    def test_bureau_gnome_attend_l_hote_puis_le_trouve(self):
        from unittest import mock
        reponses = iter([False, False, True])
        temps = [0.0]

        def dormir(pas):
            temps[0] += pas

        with mock.patch.object(sni.sys, "platform", "linux"), \
                mock.patch.dict(sni.os.environ, {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}), \
                mock.patch.object(sni, "hote_disponible", side_effect=lambda: next(reponses)):
            self.assertTrue(sni.utilisable(attente_s=10, pas_s=1,
                                           horloge=lambda: temps[0], dormir=dormir))
        self.assertEqual(temps[0], 2.0)

    def test_bureau_gnome_renonce_apres_le_delai(self):
        from unittest import mock
        temps = [0.0]

        def dormir(pas):
            temps[0] += pas

        with mock.patch.object(sni.sys, "platform", "linux"), \
                mock.patch.dict(sni.os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}), \
                mock.patch.object(sni, "hote_disponible", return_value=False):
            self.assertFalse(sni.utilisable(attente_s=5, pas_s=1,
                                            horloge=lambda: temps[0], dormir=dormir))
        self.assertGreaterEqual(temps[0], 5.0)

    def test_bureau_sans_statusnotifier_n_attend_pas(self):
        from unittest import mock
        appels = []

        def dormir(pas):
            appels.append(pas)

        with mock.patch.object(sni.sys, "platform", "linux"), \
                mock.patch.dict(sni.os.environ, {"XDG_CURRENT_DESKTOP": "XFCE",
                                                 "DESKTOP_SESSION": "xfce"}), \
                mock.patch.object(sni, "hote_disponible", return_value=False):
            self.assertFalse(sni.utilisable(dormir=dormir))
        self.assertEqual(appels, [])

    def test_hote_disponible_ne_leve_jamais(self):
        from unittest import mock
        with mock.patch.dict(sni.os.environ, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/inexistant"}):
            self.assertFalse(sni.hote_disponible(delai_s=0.2))


@unittest.skipIf(jeepney is None, "jeepney et Pillow non installés")
class AvecTray(unittest.TestCase):
    """Le menu commun de tray.py, construit sur ce sous-ensemble de pystray."""

    def test_le_menu_commun_traverse_le_protocole(self):
        journal = []
        actions = apptray.Actions(
            ouvrir=lambda: journal.append("ouvrir"),
            redemarrer=lambda: journal.append("redemarrer"),
            arreter=lambda: journal.append("arreter"),
            version_disponible=lambda: "1.2.3",
            mettre_a_jour=lambda: journal.append("maj"),
            creer_raccourci=lambda: journal.append("raccourci"))
        tray = apptray.Tray("app", Path("inutile.png"), actions)
        icon = tray.construire(pystray=sni.PYSTRAY_COMPATIBLE,
                               image=Image.new("RGBA", (16, 16), (0, 0, 0, 255)))
        icon.objets.lancer = lambda f: f()
        reponse = relu(icon.objets.repondre(appel(
            sni.CHEMIN_MENU, sni.IF_MENU, "GetLayout", "iias", (0, -1, []))))
        enfants = reponse.body[1][2]
        libelles = [n[1][1]["label"][1] for n in enfants]
        self.assertEqual(libelles, ["Ouvrir", "Mettre à jour vers 1.2.3", "Redémarrer",
                                    "Arrêter", "Créer un raccourci sur le Bureau"])
        # Clic gauche : « Ouvrir », l'entrée par défaut du menu commun.
        icon.objets.repondre(appel(sni.CHEMIN_ITEM, sni.IF_ITEM, "Activate", "ii", (0, 0)))
        self.assertEqual(journal, ["ouvrir"])

    def test_arreter_referme_l_icone(self):
        actions = apptray.Actions(
            ouvrir=lambda: None, redemarrer=lambda: None, arreter=lambda: None,
            version_disponible=lambda: None, mettre_a_jour=lambda: None,
            creer_raccourci=lambda: None)
        tray = apptray.Tray("app", Path("inutile.png"), actions)
        icon = tray.construire(pystray=sni.PYSTRAY_COMPATIBLE,
                               image=Image.new("RGBA", (16, 16), (0, 0, 0, 255)))
        icon.objets.lancer = lambda f: f()
        reponse = relu(icon.objets.repondre(appel(
            sni.CHEMIN_MENU, sni.IF_MENU, "GetLayout", "iias", (0, -1, []))))
        identifiant = next(n[1][0] for n in reponse.body[1][2]
                           if n[1][1]["label"][1] == "Arrêter")
        icon.objets.repondre(appel(sni.CHEMIN_MENU, sni.IF_MENU, "Event", "isvu",
                                   (identifiant, "clicked", ("i", 0), 0)))
        self.assertTrue(tray.arret.is_set())
        tray.attendre_la_sortie(delai_s=2)

    def test_disponible_prefere_statusnotifier_sans_pystray(self):
        from unittest import mock
        apptray._par_statusnotifier.cache_clear()
        self.addCleanup(apptray._par_statusnotifier.cache_clear)
        with mock.patch.object(sni, "utilisable", return_value=True), \
                mock.patch.dict(sys.modules, {"pystray": None}):
            self.assertTrue(apptray.disponible())

    def test_disponible_se_rabat_sur_pystray(self):
        from unittest import mock
        apptray._par_statusnotifier.cache_clear()
        self.addCleanup(apptray._par_statusnotifier.cache_clear)
        with mock.patch.object(sni, "utilisable", return_value=False), \
                mock.patch.dict(sys.modules, {"pystray": None}):
            self.assertFalse(apptray.disponible())
        apptray._par_statusnotifier.cache_clear()
        with mock.patch.object(sni, "utilisable", return_value=False), \
                mock.patch.dict(sys.modules, {"pystray": object()}):
            self.assertTrue(apptray.disponible())

    def test_verrou_de_l_action_en_fond(self):
        # L'action lancée par défaut (hors test) tourne sur un fil à part.
        fait = threading.Event()
        sni._en_fond(fait.set)
        self.assertTrue(fait.wait(2))


if __name__ == "__main__":
    unittest.main()
