"""Tests des modules environnement et relance. La relance lance un vrai
processus : c'est lui, et non les arguments passés à Popen, qui prouve
qu'elle fonctionne. Le cas du vrai service systemd est éprouvé à part, en
CI sous Linux (tests/essai_systemd.py).

    python -m unittest discover -s tests
"""

import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nico579_commons import environnement, relance  # noqa: E402

BUNDLE = "/opt/app/_internal"


class Environnement(unittest.TestCase):
    def appliquer(self, environ, fige=True, plateforme="linux"):
        environ = dict(environ)
        environnement.retablir_environnement_systeme(fige=fige, plateforme=plateforme,
                                                     environ=environ)
        return environ

    def test_valeur_d_origine_rendue(self):
        env = self.appliquer({"LD_LIBRARY_PATH": f"{BUNDLE}:/usr/local/lib",
                              "LD_LIBRARY_PATH_ORIG": "/usr/local/lib"})
        self.assertEqual(env["LD_LIBRARY_PATH"], "/usr/local/lib")

    def test_variable_absente_avant_le_bootloader_retiree(self):
        env = self.appliquer({"LD_LIBRARY_PATH": BUNDLE, "PATH": "/usr/bin"})
        self.assertNotIn("LD_LIBRARY_PATH", env)
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_sources_intactes(self):
        # Hors binaire figé, la variable est celle de l'utilisateur.
        env = self.appliquer({"LD_LIBRARY_PATH": "/choix/utilisateur"}, fige=False)
        self.assertEqual(env["LD_LIBRARY_PATH"], "/choix/utilisateur")

    def test_windows_et_macos_intacts(self):
        for plateforme in ("win32", "darwin"):
            with self.subTest(plateforme=plateforme):
                env = self.appliquer({"LD_LIBRARY_PATH": BUNDLE,
                                      "LD_LIBRARY_PATH_ORIG": "/usr/lib"},
                                     plateforme=plateforme)
                self.assertEqual(env["LD_LIBRARY_PATH"], BUNDLE)

    def test_deux_appels_meme_resultat(self):
        environ = {"LD_LIBRARY_PATH": BUNDLE, "LD_LIBRARY_PATH_ORIG": "/usr/lib"}
        for _ in range(2):
            environnement.retablir_environnement_systeme(fige=True, plateforme="linux",
                                                         environ=environ)
        self.assertEqual(environ["LD_LIBRARY_PATH"], "/usr/lib")


class Commande(unittest.TestCase):
    def test_fige_relance_l_executable(self):
        self.assertEqual(
            relance.commande(fige=True, executable="/opt/app/app",
                             argv=["/opt/app/_internal/app.py", "--port", "8000"]),
            ["/opt/app/app", "--port", "8000"])

    def test_sources_relance_le_script(self):
        self.assertEqual(
            relance.commande(fige=False, executable="/usr/bin/python3",
                             argv=["app.py", "--port", "8000"]),
            ["/usr/bin/python3", "app.py", "--port", "8000"])


class UniteSystemd(unittest.TestCase):
    def unite(self, texte, nom="watch2notif", plateforme="linux"):
        with tempfile.TemporaryDirectory() as dossier:
            cgroup = Path(dossier) / "cgroup"
            cgroup.write_text(texte, encoding="utf-8")
            return relance.unite_systemd(nom, cgroup=cgroup, plateforme=plateforme)

    def test_cgroup_v2(self):
        self.assertEqual(self.unite(
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/watch2notif.service\n"),
            "watch2notif.service")

    def test_cgroup_v1_et_unite_a_suffixe(self):
        texte = ("12:pids:/user.slice/user-1000.slice/user@1000.service\n"
                 "1:name=systemd:/user.slice/user-1000.slice/user@1000.service/"
                 "app.slice/blink2video-start.service\n")
        self.assertEqual(self.unite(texte, nom="blink2video"), "blink2video-start.service")

    def test_lance_depuis_un_terminal(self):
        # Le terminal de GNOME tourne lui-même en service : seul le nom de
        # l'application compte.
        self.assertEqual(self.unite(
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
            "app-org.gnome.Terminal.slice/vte-spawn-1234.scope\n"), "")

    def test_nom_voisin_ignore(self):
        self.assertEqual(self.unite(
            "0::/user.slice/user@1000.service/app.slice/watch2notifx.service\n"), "")

    def test_hors_linux_et_cgroup_illisible(self):
        self.assertEqual(self.unite("0::/app.slice/watch2notif.service\n",
                                    plateforme="win32"), "")
        self.assertEqual(relance.unite_systemd(
            "watch2notif", cgroup=Path(tempfile.gettempdir()) / "absent-cgroup",
            plateforme="linux"), "")


class Relancer(unittest.TestCase):
    def faux(self, code=0, erreur=None):
        appels = {"lancer": [], "demarrer": []}

        def lancer(commande, **options):
            appels["lancer"].append(commande)
            if erreur is not None:
                raise erreur
            return types.SimpleNamespace(returncode=code)

        def demarrer(commande, **options):
            appels["demarrer"].append((commande, options))
        return appels, lancer, demarrer

    def test_sous_le_service_systemd_relance(self):
        # -15 : systemctl tué par le SIGTERM de l'arrêt qu'il vient de
        # demander, la relance est en cours.
        for code in (0, -15):
            with self.subTest(code=code):
                appels, lancer, demarrer = self.faux(code=code)
                mode = relance.relancer(["/opt/app/app"], nom="app", plateforme="linux",
                                        unite="app.service", lancer=lancer,
                                        demarrer=demarrer)
                self.assertEqual(mode, "systemd")
                self.assertEqual(
                    appels["lancer"],
                    [["systemctl", "--user", "--no-block", "restart", "app.service"]])
                self.assertEqual(appels["demarrer"], [])

    def test_systemctl_en_echec_nouveau_processus(self):
        for options in ({"code": 1}, {"erreur": FileNotFoundError("systemctl")},
                        {"erreur": subprocess.TimeoutExpired("systemctl", 30)}):
            with self.subTest(**{k: str(v) for k, v in options.items()}):
                appels, lancer, demarrer = self.faux(**options)
                mode = relance.relancer(["/opt/app/app"], nom="app", plateforme="linux",
                                        unite="app.service", lancer=lancer,
                                        demarrer=demarrer)
                self.assertEqual(mode, "processus")
                self.assertEqual(len(appels["demarrer"]), 1)

    def test_options_par_systeme(self):
        appels, lancer, demarrer = self.faux()
        relance.relancer(["app.exe"], nom="app", plateforme="win32", unite="",
                         lancer=lancer, demarrer=demarrer, cwd="C:\\app")
        _, options = appels["demarrer"][0]
        self.assertEqual(options["creationflags"], relance.CREATE_NO_WINDOW)
        self.assertNotIn("start_new_session", options)
        self.assertEqual(options["cwd"], "C:\\app")
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        for plateforme in ("darwin", "linux"):
            appels, lancer, demarrer = self.faux()
            relance.relancer(["app"], nom="app", plateforme=plateforme, unite="",
                             lancer=lancer, demarrer=demarrer)
            _, options = appels["demarrer"][0]
            self.assertTrue(options["start_new_session"])
            self.assertNotIn("creationflags", options)

    def test_vrai_processus_relance(self):
        # Le nouveau processus tourne vraiment, et hors Windows dans sa
        # propre session, ce qui le soustrait au nettoyage de launchd.
        with tempfile.TemporaryDirectory() as dossier:
            sortie = Path(dossier) / "session.txt"
            code = ("import os, sys; from pathlib import Path; "
                    "s = os.getsid(0) if hasattr(os, 'getsid') else -1; "
                    "Path(sys.argv[1]).write_text(str(s))")
            mode = relance.relancer([sys.executable, "-c", code, str(sortie)],
                                    nom="essai", unite="")
            self.assertEqual(mode, "processus")
            for _ in range(200):
                if sortie.exists() and sortie.read_text():
                    break
                time.sleep(0.05)
            self.assertTrue(sortie.exists(), "le processus relancé n'a rien écrit")
            if hasattr(os, "getsid"):
                self.assertNotEqual(int(sortie.read_text()), os.getsid(0))


if __name__ == "__main__":
    unittest.main()
