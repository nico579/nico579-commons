"""Tests des modules raccourci et maj, sans réseau ni vrai Bureau :
le Bureau est un dossier temporaire, GitHub et PowerShell sont simulés.

    python -m unittest discover -s tests
"""

import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nico579_commons import maj, raccourci  # noqa: E402


class FauxLancer:
    """Enregistre les commandes ; crée la cible comme le ferait l'outil."""

    def __init__(self, creer_cible=True, code=0):
        self.commandes, self.creer_cible, self.code = [], creer_cible, code

    def __call__(self, commande):
        self.commandes.append(list(commande))
        if self.creer_cible and commande[0] == "powershell":
            # Chaîne PowerShell : l'apostrophe y est doublée.
            cible = commande[-1].split("CreateShortcut('")[1].split("');")[0]
            Path(cible.replace("''", "'")).write_bytes(b"lnk")
        if self.creer_cible and commande[0] == "osacompile":
            Path(commande[2]).mkdir(parents=True)
        return types.SimpleNamespace(returncode=self.code, stderr="")


class Raccourci(unittest.TestCase):
    def setUp(self):
        dossier = tempfile.TemporaryDirectory()
        self.addCleanup(dossier.cleanup)
        self.bureau = Path(dossier.name) / "Bureau"
        self.bureau.mkdir()
        self.messages = []

    def creer(self, plateforme, **options):
        lancer = options.pop("lancer", FauxLancer())
        code = raccourci.creer(
            "monapp", [r"C:\Apps\monapp.exe", "start", "--open-browser"],
            Path(r"C:\Donnees\monapp"), icone=Path(r"C:\Apps\assets\monapp.ico"),
            description="Ouvrir monapp", plateforme=plateforme,
            dossier_bureau=self.bureau, lancer=lancer, ecrire=self.messages.append,
            **options)
        return code, lancer

    def test_windows_lnk_par_wscript_shell(self):
        code, lancer = self.creer("win32", reduit=True)
        self.assertEqual(code, 0)
        script = lancer.commandes[0][-1]
        self.assertIn("CreateShortcut('", script)
        self.assertIn("$s.TargetPath = 'C:\\Apps\\monapp.exe'", script)
        self.assertIn("$s.Arguments = 'start --open-browser'", script)
        self.assertIn("$s.IconLocation = 'C:\\Apps\\assets\\monapp.ico'", script)
        self.assertIn("$s.WindowStyle = 7", script)
        self.assertTrue((self.bureau / "monapp.lnk").exists())
        self.assertIn("Raccourci créé", self.messages[-1])

    def test_windows_fenetre_normale_et_apostrophes(self):
        lancer = FauxLancer()
        code = raccourci.creer(
            "l'app", ["C:\\l'app\\app.exe"], Path("C:\\x"), plateforme="win32",
            dossier_bureau=self.bureau, lancer=lancer, ecrire=self.messages.append)
        self.assertEqual(code, 0)
        script = lancer.commandes[0][-1]
        self.assertIn("$s.WindowStyle = 1", script)
        self.assertIn("'C:\\l''app\\app.exe'", script)
        self.assertTrue((self.bureau / "l'app.lnk").exists())

    def test_windows_echec_rapporte(self):
        code, _ = self.creer("win32", lancer=FauxLancer(creer_cible=False, code=1))
        self.assertEqual(code, 1)
        self.assertIn("Échec", self.messages[-1])

    def test_simulation_ne_touche_a_rien(self):
        for plateforme in ("win32", "darwin", "linux"):
            with self.subTest(plateforme=plateforme):
                lancer = FauxLancer()
                code, _ = self.creer(plateforme, simulation=True, lancer=lancer)
                self.assertEqual(code, 0)
                self.assertEqual(lancer.commandes, [])
                self.assertEqual(list(self.bureau.iterdir()), [])

    def test_macos_app_par_osacompile(self):
        code, lancer = self.creer("darwin")
        self.assertEqual(code, 0)
        self.assertEqual(lancer.commandes[0][:2], ["osacompile", "-o"])
        self.assertIn("do shell script", lancer.commandes[0][-1])
        self.assertTrue((self.bureau / "monapp.app").is_dir())

    def test_linux_desktop_de_confiance(self):
        code, lancer = self.creer("linux", terminal=True)
        self.assertEqual(code, 0)
        contenu = (self.bureau / "monapp.desktop").read_text(encoding="utf-8")
        self.assertIn("Name=monapp", contenu)
        self.assertIn("Terminal=true", contenu)
        self.assertIn("Icon=", contenu)
        self.assertEqual(lancer.commandes[0][:2], ["gio", "set"])

    def test_plateforme_inconnue(self):
        code, _ = self.creer("sunos5")
        self.assertEqual(code, 1)


class Maj(unittest.TestCase):
    def test_numeros(self):
        self.assertGreater(maj.numeros("v1.6.10"), maj.numeros("1.6.9"))
        self.assertEqual(maj.numeros("pas une version"), (0,))

    def verificateur(self, reponse, locale="1.6.2"):
        appels = []

        def ouvrir(url):
            appels.append(url)
            if isinstance(reponse, Exception):
                raise reponse
            return reponse
        return maj.Verificateur("nico579/gpxsolar", locale, ouvrir=ouvrir), appels

    def test_rien_avant_la_premiere_reponse(self):
        v, appels = self.verificateur({"tag_name": "v9.9.9"})
        self.assertIsNone(v.disponible())
        self.assertEqual(appels, [])

    def test_version_plus_recente(self):
        v, appels = self.verificateur({"tag_name": "v1.6.3",
                                       "html_url": "https://github.com/x/r/releases/tag/v1.6.3"})
        self.assertTrue(v.verifier())
        self.assertEqual(v.disponible(), {"version": "1.6.3",
                                          "page": "https://github.com/x/r/releases/tag/v1.6.3",
                                          "assets": []})
        self.assertEqual(appels, ["https://api.github.com/repos/nico579/gpxsolar/releases/latest"])

    def test_les_fichiers_de_la_release_sont_conserves_reduits(self):
        v, _ = self.verificateur({"tag_name": "v1.6.3", "assets": [
            {"name": "x.zip", "browser_download_url": "https://github.com/x.zip",
             "size": 10, "digest": "sha256:ab", "state": "uploaded", "secret": "non"},
            "pas un dict"]})
        v.verifier()
        self.assertEqual(v.disponible()["assets"], [
            {"name": "x.zip", "browser_download_url": "https://github.com/x.zip",
             "size": 10, "digest": "sha256:ab", "state": "uploaded"}])

    def test_brouillon_et_preversion_ne_sont_jamais_proposes(self):
        for champ in ("draft", "prerelease"):
            with self.subTest(champ=champ):
                v, _ = self.verificateur({"tag_name": "v9.9.9", champ: True})
                self.assertFalse(v.verifier())
                self.assertIsNone(v.disponible())

    def test_deja_a_jour(self):
        v, _ = self.verificateur({"tag_name": "v1.6.2"})
        self.assertTrue(v.verifier())
        self.assertIsNone(v.disponible())

    def test_hors_ligne_garde_ce_qu_on_savait(self):
        v, _ = self.verificateur({"tag_name": "v1.7.0"})
        v.verifier()
        v._ouvrir = lambda url: (_ for _ in ()).throw(OSError("hors ligne"))
        self.assertFalse(v.verifier())
        self.assertEqual(v.disponible()["version"], "1.7.0")

    def test_page_par_defaut(self):
        v, _ = self.verificateur({"tag_name": "2.0.0"})
        v.verifier()
        self.assertEqual(v.disponible()["page"],
                         "https://github.com/nico579/gpxsolar/releases/latest")

    def test_veiller_verifie_tout_de_suite_puis_s_arrete(self):
        v, appels = self.verificateur({"tag_name": "v1.6.3"})
        arret = threading.Event()
        fil = v.veiller(arret)
        for _ in range(100):
            if appels:
                break
            threading.Event().wait(0.01)
        arret.set()
        fil.join(2)
        self.assertFalse(fil.is_alive())
        self.assertEqual(len(appels), 1)
        self.assertEqual(v.disponible()["version"], "1.6.3")


if __name__ == "__main__":
    unittest.main()
