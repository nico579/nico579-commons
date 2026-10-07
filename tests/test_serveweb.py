"""Tests du module serveweb, sur un vrai port de la boucle locale : page et
fichiers, routes GET et POST en JSON, provenances refusées, routes à
paramètres, recherche d'un port libre et d'une instance déjà lancée. Cas
repris de test_serve_web.py de gpxsolar et de lidar2map.

    python -m unittest discover -s tests
"""

import http.client
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nico579_commons import serveweb  # noqa: E402


def port_disponible():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def requete(url, methode="GET", corps=None, entetes=None):
    """(statut, corps) d'une requête ; une erreur HTTP est un résultat."""
    demande = urllib.request.Request(url, data=corps, method=methode, headers=entetes or {})
    try:
        with urllib.request.urlopen(demande, timeout=10) as reponse:
            return reponse.status, reponse.read()
    except urllib.error.HTTPError as erreur:
        return erreur.code, erreur.read()


class Demo(serveweb.Handler):
    """La sous-classe qu'écrirait une application."""
    variable_proxy_local = "DEMO_TRUSTED_LOOPBACK_PROXY"


def preparer_gui():
    tmp = tempfile.TemporaryDirectory()
    gui = Path(tmp.name)
    for nom, contenu in (("index.html", "<title>page de test</title>"), ("app.js", "// app"),
                         ("style.css", "/* css */"), ("web_bridge.js", "// pont")):
        (gui / nom).write_text(contenu, encoding="utf-8")
    (gui / "icone.ico").write_bytes(b"\x00\x00\x01\x00icone")
    return tmp, gui


class Serveur(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._gui, gui = preparer_gui()
        cls.recus = []

        def boom():
            raise RuntimeError("route cassée")

        def parcourir(chemin, mode):
            cls.recus.append((chemin, mode))
            return {"ok": True}

        class Handler(Demo):
            arguments_get = {"parcourir": lambda q: ((q.get("path") or [""])[0],
                                                     (q.get("mode") or [""])[0])}

        cls.handler = Handler
        cls.serveur = serveweb.demarrer(
            bind="127.0.0.1", port=0, trusted_host="", gui_dir=gui, handler=Handler,
            api_routes={"init": lambda: {"app": "demo"}, "boom": boom, "parcourir": parcourir},
            post_routes={"echo": lambda payload: {"recu": payload}},
            favicon=gui / "icone.ico")
        cls.port = cls.serveur.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.serveur.shutdown()
        cls.serveur.server_close()
        cls._gui.cleanup()

    def test_page_et_fichiers_servis(self):
        statut, corps = requete(self.base + "/")
        self.assertEqual(statut, 200)
        self.assertIn(b"page de test", corps)
        for chemin, attendu in (("/app.js", b"// app"), ("/style.css", b"/* css */"),
                                ("/web_bridge.js", b"// pont")):
            self.assertEqual(requete(self.base + chemin), (200, attendu), chemin)

    def test_json_en_utf8_lisible_avec_la_bonne_longueur(self):
        # Le même contenu que blink2video écrivait : accents en clair, pas \u00e9, et
        # Content-Length compté en octets UTF-8 (pas en caractères).
        demande = urllib.request.Request(
            self.base + "/api/echo", data=json.dumps({"nom": "Élodie ✓"}).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(demande, timeout=10) as reponse:
            corps = reponse.read()
            self.assertEqual(reponse.headers["Content-Type"], "application/json; charset=utf-8")
            self.assertEqual(int(reponse.headers["Content-Length"]), len(corps))
        self.assertIn("Élodie ✓".encode("utf-8"), corps)
        self.assertNotIn(b"\u00c9", corps)
        self.assertEqual(json.loads(corps), {"recu": {"nom": "Élodie ✓"}})

    def test_icone_de_l_onglet_gardee_une_semaine(self):
        with urllib.request.urlopen(self.base + "/favicon.ico", timeout=10) as reponse:
            self.assertEqual(reponse.headers["Content-Type"], "image/x-icon")
            self.assertEqual(reponse.headers["Cache-Control"], "public, max-age=604800")
            self.assertEqual(reponse.read(), b"\x00\x00\x01\x00icone")

    def test_route_get_en_json(self):
        statut, corps = requete(self.base + "/api/init")
        self.assertEqual((statut, json.loads(corps)), (200, {"app": "demo"}))

    def test_routes_inconnues(self):
        self.assertEqual(requete(self.base + "/api/absente")[0], 404)
        self.assertEqual(requete(self.base + "/autre.html")[0], 404)

    def test_exception_de_route_en_500_json(self):
        statut, corps = requete(self.base + "/api/boom")
        self.assertEqual(statut, 500)
        self.assertIn("route cassée", json.loads(corps)["error"])

    def test_post_json(self):
        statut, corps = requete(self.base + "/api/echo", "POST", b'{"a": 1}',
                                {"Content-Type": "application/json"})
        self.assertEqual((statut, json.loads(corps)), (200, {"recu": {"a": 1}}))

    def test_post_corps_illisible_ou_non_utf8_et_route_inconnue(self):
        self.assertEqual(requete(self.base + "/api/echo", "POST", b"{pas du json")[0], 400)
        self.assertEqual(requete(self.base + "/api/echo", "POST", b"\xff\xfe\x00")[0], 400)
        self.assertEqual(requete(self.base + "/api/absente", "POST", b"{}")[0], 404)

    def test_post_sans_corps_vaut_objet_vide(self):
        statut, corps = requete(self.base + "/api/echo", "POST")
        self.assertEqual((statut, json.loads(corps)), (200, {"recu": {}}))

    def test_content_length_negatif_refuse_sans_bloquer(self):
        connexion = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connexion.putrequest("POST", "/api/echo")
            connexion.putheader("Content-Length", "-1")
            connexion.endheaders()
            reponse = connexion.getresponse()
            self.assertEqual(reponse.status, 400)
            reponse.read()
        finally:
            connexion.close()

    def test_provenances_etrangeres_refusees(self):
        for entetes in ({"Host": "evil.example"},
                        {"Origin": "http://evil.example"},
                        {"Origin": "http://[abc"},
                        {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(entetes=entetes):
                self.assertEqual(requete(self.base + "/api/init", entetes=entetes)[0], 403)
                self.assertEqual(requete(self.base + "/api/echo", "POST", b"{}", entetes)[0], 403)
        self.assertEqual(requete(self.base + "/api/init",
                                 entetes={"Sec-Fetch-Site": "same-origin"})[0], 200)
        # Un client hors navigateur n'envoie pas Sec-Fetch-Site : accepté.
        self.assertEqual(requete(self.base + "/api/init")[0], 200)

    def test_refus_de_post_repond_meme_avec_un_gros_corps(self):
        # Sous Windows, fermer avec des octets non lus coupait la réponse.
        for _ in range(20):
            self.assertEqual(requete(self.base + "/api/absente", "POST", b"x" * 60000)[0], 404)

    def test_chemin_invalide_en_400(self):
        # Requête écrite à la main : http.client refuse déjà cette forme absolue
        # (urlparse lève ValueError), un client hostile, non.
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sonde:
            sonde.sendall(b"GET http://[abc/x HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                          b"Connection: close\r\n\r\n")
            reponse = b""
            while chunk := sonde.recv(4096):
                reponse += chunk
        self.assertTrue(reponse.startswith(b"HTTP/1.1 400"), reponse[:40])

    def test_route_a_parametres_lit_sa_requete(self):
        type(self).recus.clear()
        statut, _ = requete(self.base + "/api/parcourir?path=C%3A%2Fdonnees&mode=file")
        self.assertEqual(statut, 200)
        self.assertEqual(self.recus, [("C:/donnees", "file")])


class Provenance(unittest.TestCase):
    """hote_autorise() sans socket : le Handler est fabriqué sans connexion."""

    def handler(self, entetes, client="127.0.0.1", trusted_host=""):
        h = Demo.__new__(Demo)
        h.headers = entetes
        h.client_address = (client, 12345)
        h.trusted_host = trusted_host
        return h

    def test_boucle_locale(self):
        self.assertTrue(self.handler({"Host": "127.0.0.1:8765"}).hote_autorise())
        self.assertTrue(self.handler({"Host": "localhost"}).hote_autorise())
        self.assertTrue(self.handler({"Host": "[::1]:8765"}, client="::1").hote_autorise())

    def test_client_distant_refuse_sauf_hote_de_confiance(self):
        distant = self.handler({"Host": "127.0.0.1"}, client="192.168.1.20")
        self.assertFalse(distant.hote_autorise())
        vpn = self.handler({"Host": "100.64.1.2:8765", "Origin": "http://100.64.1.2:8765"},
                           client="100.64.9.9", trusted_host="100.64.1.2")
        self.assertTrue(vpn.hote_autorise())
        # L'hôte de confiance ne rend pas l'Origin étrangère acceptable.
        self.assertFalse(self.handler({"Host": "100.64.1.2", "Origin": "http://evil"},
                                      client="100.64.9.9", trusted_host="100.64.1.2").hote_autorise())

    def test_proxy_local_seulement_si_l_application_le_declare(self):
        proxy = self.handler({"Host": "localhost"}, client="10.0.0.5")
        with mock.patch.dict(os.environ, {"DEMO_TRUSTED_LOOPBACK_PROXY": "1"}):
            self.assertTrue(proxy.hote_autorise())
        with mock.patch.dict(os.environ, {"DEMO_TRUSTED_LOOPBACK_PROXY": "0"}):
            self.assertFalse(proxy.hote_autorise())
        # La classe de base ne lit aucune variable : jamais de proxy admis.
        base = serveweb.Handler.__new__(serveweb.Handler)
        base.headers, base.client_address, base.trusted_host = {"Host": "localhost"}, ("10.0.0.5", 1), ""
        with mock.patch.dict(os.environ, {"DEMO_TRUSTED_LOOPBACK_PROXY": "1"}):
            self.assertFalse(base.hote_autorise())


class Isolation(unittest.TestCase):
    def test_sans_handler_la_classe_de_base_n_est_pas_modifiee(self):
        tmp, gui = preparer_gui()
        self.addCleanup(tmp.cleanup)
        serveur = serveweb.demarrer(bind="127.0.0.1", port=0, trusted_host="vpn.exemple",
                                    gui_dir=gui, api_routes={"init": lambda: {}})
        self.addCleanup(serveur.server_close)
        self.addCleanup(serveur.shutdown)
        self.assertEqual(serveur.handler.trusted_host, "vpn.exemple")
        self.assertEqual(serveweb.Handler.trusted_host, "")
        self.assertIsNone(serveweb.Handler.gui_dir)
        self.assertIsNot(serveur.handler, serveweb.Handler)

    def test_trusted_host_modifiable_a_chaud_sur_la_classe_de_l_application(self):
        tmp, gui = preparer_gui()
        self.addCleanup(tmp.cleanup)

        class Handler(Demo):
            pass

        serveur = serveweb.demarrer(bind="127.0.0.1", port=0, trusted_host="", gui_dir=gui,
                                    handler=Handler, api_routes={"init": lambda: {"ok": 1}})
        self.addCleanup(serveur.server_close)
        self.addCleanup(serveur.shutdown)
        base = f"http://127.0.0.1:{serveur.server_address[1]}"
        self.assertEqual(requete(base + "/api/init", entetes={"Origin": "http://100.64.1.2"})[0], 403)
        Handler.trusted_host = "100.64.1.2"
        self.assertEqual(requete(base + "/api/init", entetes={"Origin": "http://100.64.1.2"})[0], 200)


class Ports(unittest.TestCase):
    def test_port_libre_puis_occupe(self):
        with socket.socket() as occupant:
            occupant.bind(("127.0.0.1", 0))
            occupant.listen()
            port = occupant.getsockname()[1]
            self.assertFalse(serveweb.port_libre("127.0.0.1", port))
        self.assertTrue(serveweb.port_libre("127.0.0.1", port))

    def test_premier_port_libre_saute_les_ports_pris(self):
        tmp, gui = preparer_gui()
        self.addCleanup(tmp.cleanup)
        with socket.socket() as occupant:
            occupant.bind(("127.0.0.1", 0))
            occupant.listen()
            depart = occupant.getsockname()[1]
            serveur, port = serveweb.premier_port_libre(
                "127.0.0.1", depart, 5, trusted_host="", gui_dir=gui,
                api_routes={"init": lambda: {"app": "demo"}})
            self.addCleanup(serveur.server_close)
            self.addCleanup(serveur.shutdown)
            self.assertNotEqual(port, depart)
            self.assertGreater(port, depart)
            self.assertTrue(serveweb.instance_existante("demo", "127.0.0.1", port))

    def test_toute_la_plage_prise_rend_none(self):
        with mock.patch.object(serveweb, "demarrer", side_effect=OSError("pris")):
            self.assertEqual(serveweb.premier_port_libre("127.0.0.1", 9000, 3), (None, None))

    def test_instance_existante_distingue_l_application_et_un_service_tiers(self):
        tmp, gui = preparer_gui()
        self.addCleanup(tmp.cleanup)
        serveur = serveweb.demarrer(bind="127.0.0.1", port=0, trusted_host="", gui_dir=gui,
                                    api_routes={"init": lambda: {"app": "demo"}})
        self.addCleanup(serveur.server_close)
        self.addCleanup(serveur.shutdown)
        port = serveur.server_address[1]
        self.assertTrue(serveweb.instance_existante("demo", "127.0.0.1", port))
        self.assertFalse(serveweb.instance_existante("autre", "127.0.0.1", port))
        self.assertFalse(serveweb.instance_existante("demo", "127.0.0.1", port_disponible()))


class EcouteHoteConfiance(unittest.TestCase):
    def test_sans_hote_inactif_et_boucle_locale_deja_servie(self):
        ecoute = serveweb.EcouteHoteConfiance(port_disponible())
        self.addCleanup(ecoute.arreter)
        self.assertEqual(ecoute.definir(""), "inactif")
        self.assertEqual(ecoute.definir("localhost"), "actif")   # servi par le principal
        self.assertEqual(ecoute.definir("127.0.0.1"), "actif")
        ecoute.arreter()
        self.assertEqual(ecoute.etat, "inactif")

    def test_hote_introuvable_reste_en_attente(self):
        ecoute = serveweb.EcouteHoteConfiance(port_disponible(), delai_s=3600)
        self.addCleanup(ecoute.arreter)
        with mock.patch.object(ecoute, "_adresses", return_value=[]):
            self.assertEqual(ecoute.definir("vpn.exemple"), "en attente")

    def test_ecoute_sur_l_adresse_de_l_hote_puis_s_arrete(self):
        # Repris de lidar2map : le serveur principal reste sur la boucle locale
        # et l'écoute en plus sur l'adresse de l'hôte de confiance (VPN maillé),
        # qui cesse de répondre à l'arrêt.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sonde:
            try:
                sonde.connect(("203.0.113.1", 80))     # sans trafic réel
                adresse = sonde.getsockname()[0]
            except OSError:
                adresse = "127.0.0.1"
        if adresse.startswith("127."):
            self.skipTest("aucune interface réseau hors boucle locale")
        tmp, gui = preparer_gui()
        self.addCleanup(tmp.cleanup)
        serveur = serveweb.demarrer(
            bind="127.0.0.1", port=0, trusted_host=adresse, gui_dir=gui,
            api_routes={"init": lambda: {"app": "demo"}}, handler=Demo)
        self.addCleanup(serveur.server_close)
        self.addCleanup(serveur.shutdown)
        port = serveur.server_address[1]
        ecoute = serveweb.EcouteHoteConfiance(port, handler=Demo)
        self.addCleanup(ecoute.arreter)
        self.assertEqual(ecoute.definir(adresse), "actif")
        with urllib.request.urlopen(f"http://{adresse}:{port}/api/init", timeout=5) as reponse:
            self.assertEqual(json.loads(reponse.read())["app"], "demo")
        # La boucle locale reste servie par le serveur principal.
        self.assertTrue(serveweb.instance_existante("demo", "127.0.0.1", port))
        ecoute.arreter()
        self.assertEqual(ecoute.etat, "inactif")
        with self.assertRaises(OSError):
            urllib.request.urlopen(f"http://{adresse}:{port}/api/init", timeout=3)

    def test_utilise_le_handler_de_l_application(self):
        class Handler(Demo):
            pass

        self.assertIs(serveweb.EcouteHoteConfiance(1, handler=Handler).handler, Handler)
        self.assertIs(serveweb.EcouteHoteConfiance(1).handler, serveweb.Handler)


class HotesDeConfiance(unittest.TestCase):
    """trusted_host en liste ou en sous-réseau CIDR, Origin, et la trace des
    refus : repris des tests de blink2video, d'où la garde est venue."""

    handler = Provenance.handler

    def refus(self, entetes, client, trusted_host=""):
        traces = []
        h = self.handler(entetes, client=client, trusted_host=trusted_host)
        h._journaliser_acces_refuse = traces.append
        return h.hote_autorise(), traces

    def test_liste_separee_par_des_virgules(self):
        for hote in ("100.64.1.2", "tablette.local"):
            with self.subTest(hote=hote):
                self.assertTrue(self.handler({"Host": hote}, client="100.64.9.9",
                                             trusted_host="100.64.1.2, tablette.local").hote_autorise())
        self.assertFalse(self.handler({"Host": "autre"}, client="100.64.9.9",
                                      trusted_host="100.64.1.2, tablette.local").hote_autorise())

    def test_sous_reseau_cidr(self):
        reglage = "192.168.1.0/24"
        for ip in ("192.168.1.5", "192.168.1.200"):
            with self.subTest(ip=ip):
                self.assertTrue(self.handler({"Host": ip, "Origin": f"http://{ip}"}, client=ip,
                                             trusted_host=reglage).hote_autorise())
        self.assertFalse(self.handler({"Host": "192.168.2.5"}, client="192.168.2.5",
                                      trusted_host=reglage).hote_autorise())

    def test_adresse_de_machine_collee_dans_le_cidr_est_toleree(self):
        self.assertTrue(self.handler({"Host": "192.168.1.9"}, client="192.168.1.9",
                                     trusted_host="192.168.1.5/24").hote_autorise())

    def test_valeur_corrompue_ne_leve_jamais_et_refuse(self):
        for reglage in ("not/a/cidr", "1.2.3.4/99", "[::"):
            with self.subTest(reglage=reglage):
                self.assertFalse(self.handler({"Host": "10.0.0.8"}, client="10.0.0.8",
                                              trusted_host=reglage).hote_autorise())

    def test_origin_doit_etre_locale_ou_de_confiance(self):
        ok = self.handler({"Host": "100.64.1.2", "Origin": "http://100.64.1.2:8081"},
                          client="100.64.9.9", trusted_host="100.64.1.2")
        self.assertTrue(ok.hote_autorise())
        for origine in ("http://evil.example", "null", "http://[abc"):
            with self.subTest(origine=origine):
                self.assertFalse(self.handler({"Host": "100.64.1.2", "Origin": origine},
                                              client="100.64.9.9",
                                              trusted_host="100.64.1.2").hote_autorise())

    def test_chaque_refus_dit_pourquoi(self):
        ok, traces = self.refus({"Host": "100.64.1.2"}, "100.64.9.9", trusted_host="10.9.9.9")
        self.assertFalse(ok)
        self.assertIn("Host '100.64.1.2' ni local, ni couvert par trusted_host", traces[-1])
        ok, traces = self.refus({"Host": "127.0.0.1"}, "192.168.1.20")
        self.assertFalse(ok)
        self.assertIn("ni boucle locale (IP cliente '192.168.1.20')", traces[-1])
        ok, traces = self.refus({"Host": "localhost", "Origin": "http://evil"}, "127.0.0.1")
        self.assertFalse(ok)
        self.assertIn("Origin 'http://evil'", traces[-1])
        ok, traces = self.refus({"Host": "localhost"}, "127.0.0.1")
        self.assertTrue(ok)
        self.assertEqual(traces, [])

    def test_entrees_confiance(self):
        self.assertEqual(serveweb.entrees_confiance(" a, b ,,10.0.0.0/8 "), ["a", "b", "10.0.0.0/8"])
        self.assertEqual(serveweb.entrees_confiance(""), [])
        self.assertEqual(serveweb.entrees_confiance(None), [])


class BandeauMaj(unittest.TestCase):
    def test_le_script_du_bandeau_est_servi_et_embarque(self):
        script = Path(serveweb.__file__).with_name("maj_banniere.js")
        self.assertTrue(script.is_file())
        h = Demo.__new__(Demo)
        h.headers = {"Host": "127.0.0.1"}
        h.client_address = ("127.0.0.1", 1)
        h.trusted_host = ""
        h.path = serveweb.ROUTE_BANDEAU_MAJ
        envoye = {}
        h.send_static = lambda chemin, type_, cache=None: envoye.update(chemin=chemin, type=type_)
        h.do_GET()
        self.assertEqual(envoye["chemin"], script)
        self.assertTrue(envoye["type"].startswith("text/javascript"))

    def test_le_bouton_reglages_est_servi_avec_le_bandeau(self):
        self.assertEqual(serveweb.fichiers_manquants(), [])
        script = Path(serveweb.__file__).with_name("reglages.js")
        self.assertTrue(script.is_file())
        h = Demo.__new__(Demo)
        h.headers = {"Host": "127.0.0.1"}
        h.client_address = ("127.0.0.1", 1)
        h.trusted_host = ""
        h.path = serveweb.ROUTE_REGLAGES
        envoye = {}
        h.send_static = lambda chemin, type_, cache=None: envoye.update(chemin=chemin, type=type_)
        h.do_GET()
        self.assertEqual(envoye["chemin"], script)
        self.assertTrue(envoye["type"].startswith("text/javascript"))

    def test_fichiers_manquants_nomme_ce_qui_n_est_pas_embarque(self):
        # Un exécutable dont le .spec oublie collect_data_files("nico579_commons").
        with mock.patch.object(serveweb.Path, "is_file", return_value=False):
            self.assertEqual(sorted(serveweb.fichiers_manquants()),
                             ["maj_banniere.js", "reglages.js"])

    def test_le_bouton_parle_aux_routes_de_maj_install(self):
        texte = Path(serveweb.__file__).with_name("reglages.js").read_text(encoding="utf-8")
        for attendu in ("/api/maj", "/api/maj-verifier", "/api/maj-installer", "nico579-reglages",
                        "window.nico579Reglages", "libelles"):
            self.assertIn(attendu, texte)

    def test_le_bouton_suit_la_langue_de_la_page(self):
        # Le libelle du bouton venait du serveur, lu une fois au chargement : basculer FR/EN
        # dans la page ne le changeait pas. Il suit maintenant l'attribut lang de <html>
        # (que les applications posent quand on bascule), avec les deux langues deja recues.
        texte = Path(serveweb.__file__).with_name("reglages.js").read_text(encoding="utf-8")
        for attendu in ("libelles_par_langue", "MutationObserver", "attributeFilter: ['lang']"):
            self.assertIn(attendu, texte)

    def test_le_script_parle_aux_routes_de_maj_install(self):
        texte = Path(serveweb.__file__).with_name("maj_banniere.js").read_text(encoding="utf-8")
        for attendu in ("/api/maj", "/api/maj-installer", "libelles", "redemarrage"):
            self.assertIn(attendu, texte)


if __name__ == "__main__":
    unittest.main()
