"""Tests du module tuiles : essais et absences, redirections et connexions
gardées contre un vrai serveur local, file continue, arrêt après des erreurs
consécutives, reprise.

    python -m unittest discover -s tests
"""

import http.server
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from nico579_commons import tuiles  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 20


class _Reponse:
    def __init__(self, contenu):
        self.contenu = contenu

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.contenu


def _erreur(code):
    return urllib.error.HTTPError("http://x/", code, "erreur", {}, None)


def _faux(reponses):
    """Un urlopen qui répond selon l'adresse : une valeur, ou une exception à lever."""
    def ouvrir(requete, timeout=None):
        reponse = reponses(requete.full_url) if callable(reponses) else reponses[requete.full_url]
        if isinstance(reponse, BaseException):
            raise reponse
        return _Reponse(reponse)
    return ouvrir


class TelechargerTuile(unittest.TestCase):
    def appel(self, reponse, **options):
        pauses = []
        resultat = tuiles.telecharger_tuile("http://x/1", _faux({"http://x/1": reponse}), tuiles.Debit(),
                                            lambda: True, pause=pauses.append, **options)
        return resultat, pauses

    def test_une_image(self):
        self.assertEqual(self.appel(PNG)[0], PNG)

    def test_absente_en_404_400_et_corps_vide(self):
        for reponse in (_erreur(404), _erreur(400), b""):
            self.assertIsNone(self.appel(reponse)[0])

    def test_une_page_d_erreur_n_est_pas_une_tuile(self):
        with self.assertRaises(tuiles.ErreurTuiles):
            self.appel(b"<html>erreur</html>")

    def test_quatre_essais_espaces_de_2_4_8_s(self):
        pauses = []
        with self.assertRaises(tuiles.ErreurTuiles) as cm:
            tuiles.telecharger_tuile("http://x/1", _faux({"http://x/1": _erreur(503)}), tuiles.Debit(),
                                     lambda: True, pause=pauses.append)
        self.assertEqual(pauses, [2, 4, 8])
        self.assertEqual(cm.exception.code, 503)

    def test_un_refus_de_cle_passager_se_rattrape(self):
        suite = iter([_erreur(401), PNG])
        ouvrir = _faux(lambda url: next(suite))
        self.assertEqual(tuiles.telecharger_tuile("http://x/1", ouvrir, tuiles.Debit(), lambda: True,
                                                  pause=lambda s: None), PNG)

    def test_abandon(self):
        with self.assertRaises(tuiles.Abandon):
            tuiles.telecharger_tuile("http://x/1", _faux({}), tuiles.Debit(), lambda: False)

    def test_est_image(self):
        for contenu in (JPEG, PNG, b"GIF89a....", b"RIFF....WEBP", b"II*\x00...."):
            self.assertTrue(tuiles.est_image(contenu), contenu)
        for contenu in (b"", b"abc", b"<html>...", b"{\"error\": 1}"):
            self.assertFalse(tuiles.est_image(contenu), contenu)


class Serveur(http.server.ThreadingHTTPServer):
    daemon_threads = True


class _Gestionnaire(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    connexions = set()

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.connexions.add(self.client_address)
        if self.path.startswith("/ancien"):
            self.send_response(301)
            self.send_header("Location", "/tuile" + self.path[len("/ancien"):])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/boucle"):
            self.send_response(302)
            self.send_header("Location", "/boucle")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        statut, corps = (404, b"") if self.path.startswith("/absente") else (200, PNG)
        self.send_response(statut)
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)


class ConnexionsGardees(unittest.TestCase):
    def setUp(self):
        _Gestionnaire.connexions = set()
        self.serveur = Serveur(("127.0.0.1", 0), _Gestionnaire)
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()
        self.racine = f"http://127.0.0.1:{self.serveur.server_address[1]}"
        self.connexions = tuiles.Connexions()

    def tearDown(self):
        self.connexions.fermer()
        self.serveur.shutdown()
        self.serveur.server_close()

    def lire(self, chemin):
        import urllib.request
        with self.connexions(urllib.request.Request(self.racine + chemin)) as reponse:
            return reponse.read()

    def test_une_seule_connexion_pour_plusieurs_tuiles(self):
        for i in range(5):
            self.assertEqual(self.lire(f"/tuile/{i}"), PNG)
        self.assertEqual(len(_Gestionnaire.connexions), 1)

    def test_redirection_suivie(self):
        self.assertEqual(self.lire("/ancien/1"), PNG)

    def test_trop_de_redirections(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.lire("/boucle")

    def test_statut_d_erreur_comme_urlopen(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.lire("/absente/1")
        self.assertEqual(cm.exception.code, 404)


class Telecharger(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        self.addCleanup(self.dossier.cleanup)
        self.base = tuiles.ouvrir_base(Path(self.dossier.name) / "t.mbtiles.part", ecriture=True)
        self.addCleanup(self.base.close)
        self.tuiles = [(3, x, y) for x in range(4) for y in range(4)]      # 16 tuiles

    def lancer(self, reponses, **options):
        options.setdefault("fenetre", 2)
        options.setdefault("fils", 2)
        return tuiles.telecharger(self.base, self.tuiles, lambda z, x, y: f"http://x/{z}/{x}/{y}",
                                  total=len(self.tuiles), ouvrir=_faux(reponses), **options)

    def rangees(self):
        return set(self.base.execute("SELECT zoom_level, tile_column, tile_row FROM tiles"))

    def test_toutes_les_tuiles_en_tms(self):
        self.assertEqual(self.lancer(lambda url: PNG), 16)
        self.assertEqual(self.rangees(), {(3, x, 7 - y) for x in range(4) for y in range(4)})

    def test_traiter_dans_le_fil(self):
        self.lancer(lambda url: PNG, traiter=lambda contenu: b"T" + contenu)
        donnees = {d for (d,) in self.base.execute("SELECT tile_data FROM tiles")}
        self.assertEqual(donnees, {b"T" + PNG})

    def test_les_absentes_ne_sont_pas_rangees(self):
        self.assertEqual(self.lancer(lambda url: _erreur(404) if url.endswith("/0") else PNG), 12)

    def test_suivi_a_chaque_enregistrement_et_a_la_fin(self):
        vus = []
        self.lancer(lambda url: PNG, enregistrement=5, suivi=lambda f, t: vus.append((f, t)))
        self.assertEqual(vus[-1], (16, 16))
        self.assertGreater(len(vus), 2)

    def test_une_tuile_en_echec_est_sautee_puis_signalee_en_fin(self):
        self.addCleanup(setattr, tuiles, "ESSAIS", tuiles.ESSAIS)
        tuiles.ESSAIS = 1
        with self.assertRaises(tuiles.ErreurTuiles):
            self.lancer(lambda url: _erreur(500) if url == "http://x/3/1/1" else PNG)
        self.assertEqual(len(self.rangees()), 15)       # les autres sont acquises pour la reprise

    def test_arret_apres_des_erreurs_consecutives(self):
        self.addCleanup(setattr, tuiles, "ESSAIS", tuiles.ESSAIS)
        tuiles.ESSAIS = 1
        appels = []
        def reponses(url):
            appels.append(url)
            return _erreur(500)
        with self.assertRaises(tuiles.ErreurTuiles):
            self.lancer(reponses, erreurs_consecutives=3)
        self.assertLess(len(appels), 16)

    def test_refus_de_cle_arrete_vite(self):
        self.addCleanup(setattr, tuiles, "ESSAIS", tuiles.ESSAIS)
        tuiles.ESSAIS = 1
        with self.assertRaises(tuiles.ErreurTuiles) as cm:
            self.lancer(lambda url: _erreur(401), fils=2)
        self.assertEqual(cm.exception.code, 401)

    def test_abandon_garde_les_tuiles_recues(self):
        etat = {"n": 0}
        def continuer():
            etat["n"] += 1
            return etat["n"] <= 6
        with self.assertRaises(tuiles.Abandon):
            tuiles.telecharger(self.base, self.tuiles, lambda z, x, y: f"http://x/{z}/{x}/{y}", total=16,
                               ouvrir=_faux(lambda url: PNG), continuer=continuer, fils=1, fenetre=1)
        self.assertTrue(0 < len(self.rangees()) < 16)

    def test_reprise(self):
        self.lancer(lambda url: PNG)
        deja = tuiles.deja_rangees(self.base)
        reste = [t for t in self.tuiles if (t[0], t[1], 2 ** t[0] - 1 - t[2]) not in deja]
        self.assertEqual(reste, [])


if __name__ == "__main__":
    unittest.main()
