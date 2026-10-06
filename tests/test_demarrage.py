"""Tests de demarrage : fichiers du démarrage automatique (raccourci Windows,
service systemd, agent launchd).

Repris des tests de blink2video (échappements, session systemd, agent macOS,
est_installe) et de lidar2map et watch2notif (raccourci, migration du .vbs),
dont ce module est la fusion. Jamais le vrai dossier personnel : tout se passe
dans un dossier temporaire, systemctl et launchctl sont simulés.

    python -m unittest discover -s tests
"""

import plistlib
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nico579_commons import demarrage  # noqa: E402

COMMANDE = ("/home/moi/Blink & Videos/blink2video", "start",
            "--note=50% <test>", "--dossier=$HOME", 'a"b', "c\\d")
DOSSIER = Path("/home/moi/Blink & Videos/100% $donnees")
SESSION = {"XDG_RUNTIME_DIR": "/run/user/1000"}


def entree(**options):
    base = dict(nom="exemple", commande=COMMANDE, dossier=DOSSIER, description="Exemple (test)")
    base.update(options)
    return demarrage.Entree(**base)


class Lanceur:
    """Enregistre les commandes lancées et rend le code voulu pour chacune."""

    def __init__(self, refuse=()):
        self.appels = []
        self.refuse = refuse

    def __call__(self, commande, **options):
        self.appels.append((list(commande), options))
        code = 1 if any(r in commande for r in self.refuse) else 0
        return subprocess.CompletedProcess(commande, code, "", "")

    def commandes(self):
        return [c for c, _ in self.appels]


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.accueil = Path(self._tmp.name).resolve()


class Contenus(unittest.TestCase):
    def test_execstart_garde_chaque_argument_intact(self):
        contenu = demarrage.contenu_unite(entree())
        ligne = next(l for l in contenu.splitlines() if l.startswith("ExecStart="))
        # shlex, en mode POSIX, lit guillemets et antislashs comme systemd ;
        # restent les doublements propres à systemd, %% et $$.
        arguments = [a.replace("%%", "%").replace("$$", "$")
                     for a in shlex.split(ligne[len("ExecStart="):])]
        self.assertEqual(arguments, list(COMMANDE))
        self.assertIn(f"WorkingDirectory={str(DOSSIER).replace('%', '%%')}\n", contenu)

    def test_options_de_l_unite(self):
        simple = demarrage.contenu_unite(entree())
        self.assertNotIn("After=", simple)
        self.assertNotIn("RestartSec", simple)
        self.assertIn("Description=Exemple (test)\n", simple)
        self.assertIn("Restart=on-failure\n", simple)
        self.assertIn("WantedBy=default.target\n", simple)
        graphique = demarrage.contenu_unite(entree(apres_session_graphique=True, attente_relance_s=10))
        self.assertIn("After=graphical-session.target\n", graphique)
        self.assertIn("RestartSec=10\n", graphique)

    def test_agent_relu_a_l_identique(self):
        agent = plistlib.loads(demarrage.contenu_agent(entree()).encode("utf-8"))
        self.assertEqual(agent["Label"], "com.nico.exemple")
        self.assertEqual(agent["ProgramArguments"], list(COMMANDE))
        self.assertEqual(agent["WorkingDirectory"], str(DOSSIER))
        self.assertTrue(agent["RunAtLoad"])
        # Relancé après un échec seulement, pas après un arrêt voulu.
        self.assertEqual(agent["KeepAlive"], {"SuccessfulExit": False})

    def test_label_explicite(self):
        agent = plistlib.loads(demarrage.contenu_agent(entree(label_macos="com.nico579.exemple")).encode())
        self.assertEqual(agent["Label"], "com.nico579.exemple")


class EnvSystemctl(Base):
    def session(self, uid=1001, bus=True) -> Path:
        dossier = self.accueil / str(uid)
        dossier.mkdir()
        if bus:
            (dossier / "bus").write_text("", encoding="utf-8")
        return dossier

    def test_session_deduite_de_run_user(self):
        dossier = self.session()
        env = demarrage.env_systemctl({"PATH": "/usr/bin"}, uid=1001, racine=self.accueil)
        self.assertEqual(env["XDG_RUNTIME_DIR"], str(dossier))
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], f"unix:path={dossier / 'bus'}")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_variables_deja_presentes_conservees(self):
        self.session()
        existant = {"XDG_RUNTIME_DIR": "/run/user/42",
                    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/42/bus"}
        self.assertEqual(demarrage.env_systemctl(existant, uid=1001, racine=self.accueil), existant)

    def test_sans_bus_pas_d_adresse_inventee(self):
        self.session(bus=False)
        env = demarrage.env_systemctl({}, uid=1001, racine=self.accueil)
        self.assertIn("XDG_RUNTIME_DIR", env)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)

    def test_sans_session_systemd_rien_d_invente(self):
        env = demarrage.env_systemctl({}, uid=1001, racine=self.accueil)
        self.assertNotIn("XDG_RUNTIME_DIR", env)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)


class Linux(Base):
    def activer(self, lanceur=None, env=SESSION, e=None):
        lanceur = lanceur or Lanceur()
        notes = demarrage.activer(e or entree(), plateforme="linux", accueil=self.accueil,
                                  lancer=lanceur, env=dict(env))
        return lanceur, notes

    def test_ecrit_l_unite_puis_recharge_et_demarre(self):
        lanceur, notes = self.activer()
        unite = self.accueil / ".config" / "systemd" / "user" / "exemple.service"
        self.assertEqual(unite.read_text(encoding="utf-8"), demarrage.contenu_unite(entree()))
        self.assertEqual(lanceur.commandes(), [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", "exemple"]])
        self.assertEqual(notes, [])
        self.assertTrue(demarrage.est_actif(entree(), plateforme="linux", accueil=self.accueil))

    def test_systemctl_recoit_l_environnement_de_session(self):
        lanceur, _ = self.activer(env={"XDG_RUNTIME_DIR": "/run/user/1001"})
        for _commande, options in lanceur.appels:
            self.assertEqual(options["env"]["XDG_RUNTIME_DIR"], "/run/user/1001")

    def test_refus_de_systemctl_n_est_plus_annonce_comme_reussi(self):
        with self.assertRaises(demarrage.ErreurDemarrage) as c:
            self.activer(Lanceur(refuse=("enable",)))
        self.assertEqual(c.exception.code, "installation_refusee")
        self.assertIn("enable --now", str(c.exception))

    def test_sans_session_systemd_l_avertissement_reste(self):
        _, notes = self.activer(Lanceur(refuse=("enable",)), env={})
        self.assertEqual(notes[0][0], "session_systemd_absente")
        self.assertIn("loginctl enable-linger", demarrage.message(notes[0][0], "fr", **notes[0][1]))

    def test_desactiver_arrete_retire_et_recharge(self):
        self.activer()
        lanceur = Lanceur()
        demarrage.desactiver(entree(), plateforme="linux", accueil=self.accueil,
                             lancer=lanceur, env=dict(SESSION))
        self.assertEqual(lanceur.commandes(), [
            ["systemctl", "--user", "disable", "--now", "exemple"],
            ["systemctl", "--user", "daemon-reload"]])
        self.assertFalse(demarrage.est_actif(entree(), plateforme="linux", accueil=self.accueil))

    def test_desactiver_sans_avoir_active_ne_leve_pas(self):
        demarrage.desactiver(entree(), plateforme="linux", accueil=self.accueil,
                             lancer=Lanceur(), env=dict(SESSION))

    def test_activer_deux_fois_reste_actif(self):
        self.activer()
        self.activer()
        self.assertTrue(demarrage.est_actif(entree(), plateforme="linux", accueil=self.accueil))


class MacOS(Base):
    def test_ecrit_l_agent_puis_le_charge(self):
        lanceur = Lanceur()
        demarrage.activer(entree(), plateforme="darwin", accueil=self.accueil, lancer=lanceur)
        cible = self.accueil / "Library" / "LaunchAgents" / "com.nico.exemple.plist"
        self.assertEqual(cible.read_text(encoding="utf-8"), demarrage.contenu_agent(entree()))
        self.assertEqual(lanceur.commandes(), [["launchctl", "load", str(cible)]])
        self.assertTrue(demarrage.est_actif(entree(), plateforme="darwin", accueil=self.accueil))

    def test_les_anciens_labels_sont_dechargés_avant(self):
        lanceur = Lanceur()
        demarrage.activer(entree(anciens_labels_macos=("com.nico579.exemple",)),
                          plateforme="darwin", accueil=self.accueil, lancer=lanceur)
        self.assertEqual(lanceur.commandes()[0], ["launchctl", "remove", "com.nico579.exemple"])
        self.assertEqual(lanceur.commandes()[1][:2], ["launchctl", "load"])

    def test_desactiver_decharge_et_retire(self):
        demarrage.activer(entree(), plateforme="darwin", accueil=self.accueil, lancer=Lanceur())
        lanceur = Lanceur()
        demarrage.desactiver(entree(), plateforme="darwin", accueil=self.accueil, lancer=lanceur)
        cible = self.accueil / "Library" / "LaunchAgents" / "com.nico.exemple.plist"
        self.assertEqual(lanceur.commandes(), [["launchctl", "unload", str(cible)]])
        self.assertFalse(cible.exists())

    def test_desactiver_sans_avoir_active_ne_fait_rien(self):
        lanceur = Lanceur()
        demarrage.desactiver(entree(), plateforme="darwin", accueil=self.accueil, lancer=lanceur)
        self.assertEqual(lanceur.appels, [])


class WindowsSimule(Base):
    """Le raccourci passe par raccourci.creer (PowerShell) : ici un faux
    PowerShell qui note le script et pose le fichier."""

    def faux(self, dossier, cree=True):
        scripts = []

        def lancer(commande, **options):
            script = commande[-1]
            scripts.append(script)
            if cree:
                (dossier / "exemple.lnk").write_bytes(b"lnk")
            return subprocess.CompletedProcess(commande, 0 if cree else 1, "", "refus de test")
        lancer.scripts = scripts
        return lancer

    def test_le_raccourci_lance_le_programme_reduit(self):
        lancer = self.faux(self.accueil)
        demarrage.activer(entree(commande=("C:/App/exemple.exe", "--serve", "--no-browser"),
                                 dossier=Path("C:/App")), plateforme="win32",
                          dossier=self.accueil, lancer=lancer)
        script = lancer.scripts[0]
        self.assertIn("exemple.exe", script)
        self.assertIn("--serve --no-browser", script)
        self.assertIn("WindowStyle = 7", script)
        self.assertIn("Exemple (test)", script)

    def test_echec_signale_et_ne_perd_pas_l_ancien_vbs(self):
        (self.accueil / "exemple.vbs").write_text("ancien")
        with self.assertRaises(demarrage.ErreurDemarrage) as c:
            demarrage.activer(entree(retire_vbs=True), plateforme="win32", dossier=self.accueil,
                              lancer=self.faux(self.accueil, cree=False))
        self.assertEqual(c.exception.code, "raccourci_non_cree")
        self.assertTrue((self.accueil / "exemple.vbs").exists())

    def test_activer_retire_l_ancien_vbs(self):
        (self.accueil / "exemple.vbs").write_text("ancien")
        demarrage.activer(entree(retire_vbs=True), plateforme="win32", dossier=self.accueil,
                          lancer=self.faux(self.accueil))
        self.assertFalse((self.accueil / "exemple.vbs").exists())

    def test_desactiver_retire_raccourci_et_vbs(self):
        (self.accueil / "exemple.lnk").write_bytes(b"x")
        (self.accueil / "exemple.vbs").write_text("x")
        demarrage.desactiver(entree(), plateforme="win32", dossier=self.accueil)
        self.assertEqual(list(self.accueil.iterdir()), [])
        demarrage.desactiver(entree(), plateforme="win32", dossier=self.accueil)   # sans erreur

    def test_actif_avec_un_vbs_seul_quand_il_faut_le_migrer(self):
        (self.accueil / "exemple.vbs").write_text("x")
        self.assertFalse(demarrage.est_actif(entree(), plateforme="win32", dossier=self.accueil))
        self.assertTrue(demarrage.est_actif(entree(retire_vbs=True), plateforme="win32",
                                            dossier=self.accueil))


@unittest.skipUnless(sys.platform == "win32", "PowerShell et le dossier Démarrage de Windows")
class WindowsReel(Base):
    def test_raccourci_reel_dans_un_dossier_temporaire(self):
        e = entree(commande=(sys.executable, "--serve", "--no-browser"), dossier=Path(sys.executable).parent)
        demarrage.activer(e, dossier=self.accueil)
        self.assertTrue((self.accueil / "exemple.lnk").is_file())
        self.assertTrue(demarrage.est_actif(e, dossier=self.accueil))
        demarrage.desactiver(e, dossier=self.accueil)
        self.assertFalse((self.accueil / "exemple.lnk").exists())

    def test_migration_du_vbs(self):
        e = entree(commande=(sys.executable, "--serve"), dossier=Path(sys.executable).parent, retire_vbs=True)
        (self.accueil / "exemple.vbs").write_text("ancien")
        self.assertTrue(demarrage.migrer_vbs(e, dossier=self.accueil))
        self.assertTrue((self.accueil / "exemple.lnk").is_file())
        self.assertFalse((self.accueil / "exemple.vbs").exists())
        self.assertFalse(demarrage.migrer_vbs(e, dossier=self.accueil))     # plus de .vbs


class Divers(Base):
    def test_installees_par_prefixe(self):
        for nom in ("exemple-start", "exemple-watch", "autre"):
            demarrage.activer(entree(nom=nom, label_macos=f"com.nico579.{nom}"), plateforme="darwin",
                              accueil=self.accueil, lancer=Lanceur())
            demarrage.activer(entree(nom=nom), plateforme="linux", accueil=self.accueil,
                              lancer=Lanceur(), env=dict(SESSION))
        linux = demarrage.installees("exemple", plateforme="linux", accueil=self.accueil)
        self.assertEqual([p.name for p in linux], ["exemple-start.service", "exemple-watch.service"])
        mac = demarrage.installees("exemple", plateforme="darwin", accueil=self.accueil,
                                   prefixe_label="com.nico579.exemple")
        self.assertEqual([p.name for p in mac],
                         ["com.nico579.exemple-start.plist", "com.nico579.exemple-watch.plist"])

    def test_apercu_n_ecrit_rien(self):
        for plateforme in ("linux", "darwin"):
            apercu = demarrage.apercu(entree(), plateforme=plateforme, accueil=self.accueil)
            self.assertTrue(apercu["contenu"])
        self.assertEqual(list(self.accueil.rglob("*")), [])

    def test_plateforme_non_prise_en_charge(self):
        for fonction in (demarrage.activer, demarrage.desactiver):
            with self.assertRaises(demarrage.ErreurDemarrage) as c:
                fonction(entree(), plateforme="freebsd")
            self.assertEqual(c.exception.code, "plateforme_non_prise_en_charge")
        self.assertFalse(demarrage.est_actif(entree(), plateforme="freebsd"))

    def test_messages_dans_les_deux_langues(self):
        import string
        self.assertEqual(set(demarrage.LIBELLES["fr"]), set(demarrage.LIBELLES["en"]))
        for cle in demarrage.LIBELLES["fr"]:
            champs = {lg: {f for _, f, _, _ in string.Formatter().parse(demarrage.LIBELLES[lg][cle]) if f}
                      for lg in ("fr", "en")}
            self.assertEqual(champs["fr"], champs["en"], cle)


if __name__ == "__main__":
    unittest.main()
