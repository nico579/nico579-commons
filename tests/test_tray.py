"""Tests du menu commun, sans zone de notification : un faux pystray
enregistre ce que le module lui demande.

    python -m unittest discover -s tests
"""

import sys
import threading
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import nico579_commons  # noqa: E402
from nico579_commons import tray as apptray  # noqa: E402



class FauxItem:
    def __init__(self, texte, action, default=False, checked=None):
        self.texte, self.action, self.default, self.checked = texte, action, default, checked


class FauxIcon:
    def __init__(self, nom, image, titre, menu=None):
        self.nom, self.image, self.titre, self.menu = nom, image, titre, menu
        self.arretee = threading.Event()
        self.mises_a_jour = 0

    def run(self):
        self.arretee.wait(10)

    def stop(self):
        self.arretee.set()

    def update_menu(self):
        self.mises_a_jour += 1


FAUX_PYSTRAY = types.SimpleNamespace(
    MenuItem=FauxItem, Icon=FauxIcon,
    Menu=lambda generateur: types.SimpleNamespace(items=generateur))


def actions(**options):
    journal = []
    base = dict(ouvrir=lambda: journal.append("ouvrir"),
                redemarrer=lambda: journal.append("redemarrer"),
                arreter=lambda: journal.append("arreter"))
    base.update(options)
    return apptray.Actions(**base), journal


def textes(menu):
    return [item.texte for item in menu]


class Menu(unittest.TestCase):
    def test_menu_minimal(self):
        a, _ = actions()
        menu = apptray.entrees(a, FAUX_PYSTRAY, lambda action: None)
        self.assertEqual(textes(menu), ["Ouvrir", "Redémarrer", "Arrêter"])
        self.assertTrue(menu[0].default)

    def test_menu_complet_dans_l_ordre_commun(self):
        a, _ = actions(version_disponible=lambda: "1.2.3", mettre_a_jour=lambda: None,
                       creer_raccourci=lambda: None,
                       elements=[apptray.Element({"fr": "Pause", "en": "Pause"}, lambda: None)])
        menu = apptray.entrees(a, FAUX_PYSTRAY, lambda action: None)
        self.assertEqual(textes(menu), ["Ouvrir", "Pause", "Mettre à jour vers 1.2.3",
                                        "Redémarrer", "Arrêter",
                                        "Créer un raccourci sur le Bureau"])

    def test_mise_a_jour_seulement_si_une_version_est_connue(self):
        a, _ = actions(version_disponible=lambda: None, mettre_a_jour=lambda: None)
        self.assertNotIn("Mettre à jour", " ".join(
            textes(apptray.entrees(a, FAUX_PYSTRAY, lambda action: None))))

    def test_anglais_et_langue_inconnue(self):
        for langue in ("en", "de"):
            a, _ = actions(langue=lambda langue=langue: langue)
            self.assertEqual(textes(apptray.entrees(a, FAUX_PYSTRAY, lambda action: None)),
                             ["Open", "Restart", "Stop"])

    def test_case_a_cocher(self):
        etat = {"pause": True}
        a, _ = actions(elements=[apptray.Element({"fr": "Pause"}, lambda: None,
                                                 coche=lambda: etat["pause"])])
        item = apptray.entrees(a, FAUX_PYSTRAY, lambda action: None)[1]
        self.assertTrue(item.checked(item))
        etat["pause"] = False
        self.assertFalse(item.checked(item))

    def test_ouvrir_appelle_l_application(self):
        a, journal = actions()
        apptray.entrees(a, FAUX_PYSTRAY, lambda action: None)[0].action(None, None)
        self.assertEqual(journal, ["ouvrir"])

    def test_mise_a_jour_qui_referme_l_icone(self):
        refermees = []
        a, _ = actions(version_disponible=lambda: "1.2.3", mettre_a_jour=lambda: None)
        item = apptray.entrees(a, FAUX_PYSTRAY, refermees.append)[1]
        item.action(None, None)
        self.assertEqual(refermees, [a.mettre_a_jour])

    def test_mise_a_jour_qui_laisse_l_icone(self):
        # Page de la version (lidar2map, gpxsolar), ou téléchargement avant
        # installation (watch2notif) : l'action tourne hors de la pompe de
        # messages, et l'icône reste.
        refermees, faite = [], threading.Event()
        a, _ = actions(version_disponible=lambda: "1.2.3", mettre_a_jour=faite.set,
                       mettre_a_jour_referme=False)
        item = apptray.entrees(a, FAUX_PYSTRAY, refermees.append)[1]
        self.assertEqual(item.texte, "Mettre à jour vers 1.2.3")
        item.action(None, None)
        self.assertTrue(faite.wait(2))
        self.assertEqual(refermees, [])


class Sortie(unittest.TestCase):
    def test_redemarrer_hors_pompe_puis_attente_avant_de_rendre_la_main(self):
        fini = threading.Event()

        def redemarrer():
            time.sleep(0.3)      # un arrêt qui prend son temps
            fini.set()

        a, _ = actions(redemarrer=redemarrer)
        t = apptray.Tray("essai", Path("icone.ico"), a)
        icon = t.construire(pystray=FAUX_PYSTRAY, image=object())
        item = [i for i in apptray.entrees(a, FAUX_PYSTRAY, t._arreter_icone)
                if i.texte == "Redémarrer"][0]
        debut = time.monotonic()
        item.action(icon, item)
        self.assertLess(time.monotonic() - debut, 0.2)   # le rappel rend la main tout de suite
        self.assertTrue(icon.arretee.is_set())
        t.executer()
        self.assertTrue(fini.is_set())                    # executer() a attendu le fil

    def test_arret_leve_par_l_application_referme_l_icone(self):
        a, _ = actions()
        t = apptray.Tray("essai", Path("icone.ico"), a)
        icon = t.construire(pystray=FAUX_PYSTRAY, image=object())
        threading.Timer(0.2, t.arret.set).start()
        debut = time.monotonic()
        t.executer()
        self.assertLess(time.monotonic() - debut, 5)
        self.assertTrue(icon.arretee.is_set())


class FilPrincipal(unittest.TestCase):
    def test_direct_hors_macos(self):
        f = lambda: None  # noqa: E731
        self.assertIs(apptray.sur_le_fil_principal(f, plateforme="win32"), f)

    def test_macos_passe_par_callafter(self):
        appels = []
        faux = types.ModuleType("PyObjCTools")
        faux.AppHelper = types.SimpleNamespace(callAfter=lambda f: appels.append(f))
        sys.modules["PyObjCTools"] = faux
        try:
            f = lambda: None  # noqa: E731
            apptray.sur_le_fil_principal(f, plateforme="darwin")()
            self.assertEqual(appels, [f])
        finally:
            del sys.modules["PyObjCTools"]


class Version(unittest.TestCase):
    def test_version_du_paquet(self):
        texte = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{nico579_commons.__version__}"', texte)
        self.assertEqual(apptray.CADENCE_MENU, 5)


if __name__ == "__main__":
    unittest.main()
