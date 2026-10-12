"""Les scripts de page communs, exécutés dans un vrai navigateur.

maj_banniere.js (bandeau de mise à jour), reglages.js (bouton et panneau
Réglages) et langue.js (choix FR / EN) tournent dans toutes les applications
qui servent une page. Les autres tests vérifient qu'ils sont servis et ce
qu'ils contiennent ; ici, Playwright (bibliothèque de Microsoft qui pilote un
vrai Chromium) les fait tourner sur une petite page de démonstration, servie
par le vrai serveweb avec les vraies routes de maj_install et de langue. Seuls
l'installateur et le vérificateur sont factices, pour décider des états sans
réseau.

Sans Playwright, ces tests sont sautés ; la CI l'installe (job « navigateur »).

    python -m pip install playwright
    python -m playwright install chromium
    python -m unittest tests.test_pages_navigateur -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nico579_commons import langue, maj_install, serveweb  # noqa: E402

try:
    from playwright.sync_api import Error as ErreurPlaywright, expect, sync_playwright
except ImportError:
    sync_playwright = None

PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Démonstration</title></head>
<body><header><span id="nico579-reglages"></span><span data-nico579-langue></span></header>
<script src="/nico579-maj.js"></script><script src="/nico579-reglages.js"></script>
<script src="/nico579-langue.js"></script></body></html>
"""

INACTIF = {"etat": "inactif", "version": None, "recu": 0, "total": 0, "erreur": None}


class FauxVerificateur:
    page_des_releases = "https://example.invalid/releases/latest"

    def __init__(self, version_locale="1.0.0"):
        self.version_locale = version_locale
        self.info = None
        self.verifie_a = None
        self.questions = 0

    def disponible(self):
        return self.info

    def verifier(self):
        self.questions += 1
        self.verifie_a = 1_700_000_000.0
        return True


class FauxInstallateur:
    """Ce que les routes lisent d'un Installateur ; l'état se règle depuis le test."""

    def __init__(self, version_locale="1.0.0"):
        self.verificateur = FauxVerificateur(version_locale)
        self.etat_courant = dict(INACTIF)
        self.possible_ = (True, "")
        self.demarrages = 0

    def possible(self):
        return self.possible_

    def etat(self):
        return dict(self.etat_courant)

    def demarrer(self):
        self.demarrages += 1
        self.etat_courant.update(etat="telechargement", recu=50, total=100)
        return True


@unittest.skipIf(sync_playwright is None, "Playwright absent")
class PagesCommunes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dossier = tempfile.TemporaryDirectory()
        gui = Path(cls.dossier.name)
        (gui / "index.html").write_text(PAGE, encoding="utf-8")
        cls.serveur = serveweb.demarrer(bind="127.0.0.1", port=0, trusted_host="", gui_dir=gui,
                                        api_routes={})
        cls.adresse = f"http://127.0.0.1:{cls.serveur.server_port}/"
        cls.playwright = sync_playwright().start()
        try:
            cls.navigateur = cls.playwright.chromium.launch(headless=True)
        except ErreurPlaywright as erreur:
            cls.playwright.stop()
            cls.serveur.shutdown()
            cls.serveur.server_close()
            cls.dossier.cleanup()
            raise unittest.SkipTest(f"Chromium absent : {erreur}")

    @classmethod
    def tearDownClass(cls):
        cls.navigateur.close()
        cls.playwright.stop()
        cls.serveur.shutdown()
        cls.serveur.server_close()
        cls.dossier.cleanup()

    def servir(self, version_locale="1.0.0"):
        """Routes neuves pour chaque test, et après une « mise à jour » : celles de la
        nouvelle version. Le serveur lit les routes de sa classe à chaque requête."""
        self.installateur = FauxInstallateur(version_locale)
        maj_get, maj_post = maj_install.routes(self.installateur, lambda: self.langue["code"])
        langue_get, langue_post = langue.routes(lambda: self.langue["code"],
                                                lambda code: self.langue.update(code=code))
        self.serveur.handler.api_routes = {**maj_get, **langue_get}
        self.serveur.handler.post_routes = {**maj_post, **langue_post}

    def setUp(self):
        self.langue = {"code": "fr"}
        self.servir()
        self.contexte = self.navigateur.new_context(locale="fr-FR")
        self.page = self.contexte.new_page()
        self.erreurs = []
        # /api/autostart répond 404 : la démonstration n'a pas de démarrage
        # automatique, ce que reglages.js lit justement ainsi.
        self.page.on("pageerror", lambda erreur: self.erreurs.append(str(erreur)))
        self.page.on("response", lambda r: self.erreurs.append(f"HTTP {r.status} {r.url}")
                     if r.status >= 400 and not r.url.endswith("/api/autostart") else None)

    def tearDown(self):
        self.contexte.close()
        self.assertEqual(self.erreurs, [])

    def disponible(self, version="9.9.9"):
        self.installateur.verificateur.info = {"version": version, "page": "https://example.invalid/r"}

    def test_le_bandeau_installe_puis_recharge_la_page_sur_la_nouvelle_version(self):
        self.disponible()
        self.page.goto(self.adresse)
        bandeau = self.page.locator("#nico579-maj")
        expect(bandeau).to_contain_text("Version 9.9.9 disponible.")
        bandeau.get_by_role("button", name="Installer").click()
        expect(bandeau).to_contain_text("Téléchargement de la mise à jour… 50 %")
        self.assertEqual(self.installateur.demarrages, 1)
        self.installateur.etat_courant.update(etat="redemarrage")
        expect(bandeau).to_contain_text("Mise à jour installée, redémarrage…")
        self.page.evaluate("window.avantRedemarrage = true")
        # La nouvelle version répond : plus rien à installer, la page se recharge.
        self.installateur.verificateur.info = None
        self.installateur.etat_courant = dict(INACTIF)
        self.page.wait_for_function("window.avantRedemarrage === undefined", timeout=10000)
        expect(self.page.locator("#nico579-maj")).to_be_hidden()

    def test_une_installation_lancee_d_ailleurs_recharge_aussi_la_page(self):
        # Lancée depuis le menu de l'icône : le bandeau n'a rien demandé, et l'état
        # « redémarrage » passe trop vite pour qu'il le voie. Il retrouve le serveur
        # à sa question suivante, avec une autre version : la page la rejoint.
        self.page.clock.install()
        self.page.goto(self.adresse)
        self.page.wait_for_load_state("networkidle")
        self.page.evaluate("window.avantRedemarrage = true")
        self.servir(version_locale="2.0.0")
        self.page.clock.fast_forward(31000)
        self.page.wait_for_function("window.avantRedemarrage === undefined", timeout=10000)

    def test_sans_installation_possible_le_bandeau_renvoie_a_la_release(self):
        self.disponible()
        self.installateur.possible_ = (False, "source_mode")
        self.page.goto(self.adresse)
        bandeau = self.page.locator("#nico579-maj")
        expect(bandeau.get_by_role("link", name="Voir la version")).to_have_attribute(
            "href", "https://example.invalid/r")
        expect(bandeau.get_by_role("button", name="Installer")).to_have_count(0)

    def test_le_bandeau_ferme_reste_ferme_pendant_la_session(self):
        self.disponible()
        self.page.goto(self.adresse)
        bandeau = self.page.locator("#nico579-maj")
        expect(bandeau).to_be_visible()
        bandeau.get_by_role("button", name="Fermer").click()
        expect(bandeau).to_be_hidden()
        self.page.reload()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.locator("#nico579-maj")).to_be_hidden()

    def test_une_erreur_s_affiche_dans_la_langue_de_l_application(self):
        self.installateur.etat_courant.update(etat="erreur", erreur={
            "code": "download_failed", "categorie": "download_failed",
            "message": {"fr": "réseau coupé", "en": "network down"}})
        self.page.goto(self.adresse)
        bandeau = self.page.locator("#nico579-maj")
        expect(bandeau).to_contain_text("La mise à jour a échoué : réseau coupé")
        expect(bandeau.get_by_role("button", name="Réessayer")).to_be_visible()

    def test_le_panneau_reglages_donne_la_version_et_verifie_a_la_demande(self):
        self.page.goto(self.adresse)
        self.page.locator("#nico579-reglages-bouton").click()
        panneau = self.page.get_by_role("dialog", name="Réglages")
        expect(panneau).to_contain_text("Version 1.0.0")
        panneau.get_by_role("button", name="Vérifier les mises à jour").click()
        expect(panneau).to_contain_text("Vous avez la dernière version.")
        self.assertEqual(self.installateur.verificateur.questions, 1)
        self.page.keyboard.press("Escape")
        expect(panneau).to_be_hidden()

    def test_le_panneau_propose_d_installer_une_version_plus_recente(self):
        self.disponible("2.0.0")
        self.page.goto(self.adresse)
        self.page.locator("#nico579-reglages-bouton").click()
        panneau = self.page.get_by_role("dialog", name="Réglages")
        panneau.get_by_role("button", name="Vérifier les mises à jour").click()
        expect(panneau).to_contain_text("2.0.0")
        panneau.get_by_role("button", name="Installer").click()
        expect(self.page.locator("#nico579-maj")).to_contain_text("Téléchargement de la mise à jour")
        self.assertEqual(self.installateur.demarrages, 1)

    def test_le_choix_de_langue_est_enregistre_et_suivi_par_les_reglages(self):
        self.page.goto(self.adresse)
        bouton = self.page.locator("#nico579-reglages-bouton")
        expect(bouton).to_have_text("⚙ Réglages…")
        self.page.get_by_role("button", name="EN", exact=True).click()
        expect(self.page.locator("html")).to_have_attribute("lang", "en")
        expect(bouton).to_have_text("⚙ Settings…")
        self.assertEqual(self.langue["code"], "en")


if __name__ == "__main__":
    unittest.main()
