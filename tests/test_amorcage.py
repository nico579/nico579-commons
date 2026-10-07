"""Tests de amorcage : l'amorçage des dépendances en mode sources.

Reprend ceux de lidar2map (résolution du mode, moteur venv, stratégies pip,
--installer-deps) et de gpxsolar (venv remis au verrou, mode force), dont ce
module est la fusion, plus ce que les deux n'avaient pas : l'installation sans
verrou (blink2video, Python 3.8), la réutilisation de l'environnement (image
Docker), la langue, et un essai réel, avec un vrai venv et un verrou vide.
Jamais le vrai dossier personnel : tout se passe dans un dossier temporaire.

    python -m unittest discover -s tests
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nico579_commons import amorcage  # noqa: E402

VARIABLE = "EXEMPLE_BOOTSTRAP"


class Base(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(prefix="amorcage-")
        self.addCleanup(temporaire.cleanup)
        self.racine = Path(temporaire.name) / "sources"
        self.home = Path(temporaire.name) / "home"
        self.racine.mkdir()
        self.home.mkdir()
        (self.racine / "requirements.in").write_text("alpha>=1\nbeta\n", encoding="utf-8")
        (self.racine / "requirements.txt").write_text("alpha==1.0 \\\n    --hash=sha256:00\n",
                                                      encoding="utf-8")
        patch = mock.patch.object(Path, "home", return_value=self.home)
        patch.start()
        self.addCleanup(patch.stop)
        # L'environnement du test : ni conda ni venv actif, aucune variable du mode.
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for nom in ("CONDA_PREFIX", "VIRTUAL_ENV", VARIABLE):
            os.environ.pop(nom, None)

    def moteur(self, **options):
        return amorcage.Amorcage("exemple", self.racine, **options)

    def python_du_venv(self, moteur=None):
        moteur = moteur or self.moteur()
        dossier = "Scripts" if amorcage.platform.system() == "Windows" else "bin"
        nom = "python.exe" if amorcage.platform.system() == "Windows" else "python"
        return moteur.venv / dossier / nom

    def faux_venv(self, *, installation=0, creation_echoue=False):
        """subprocess.run simulé : `-m venv` crée le Python du venv, pip rend
        ``installation``. Remplit ``self.appels``."""
        self.appels = []
        python = self.python_du_venv()

        def run(commande, *args, **kwargs):
            self.appels.append(list(commande))
            if commande[1:3] == ["-m", "venv"] and "--help" not in commande:
                if creation_echoue:
                    raise subprocess.CalledProcessError(1, commande)
                python.parent.mkdir(parents=True)
                python.write_text("", encoding="utf-8")
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=installation, stdout="", stderr="boum" if installation else "")
        return mock.patch.object(amorcage.subprocess, "run", side_effect=run)

    def sortie(self, fonction, *args, **kwargs):
        """Exécute, rend (code de sortie ou None, stdout)."""
        tampon = io.StringIO()
        code = None
        with contextlib.redirect_stdout(tampon):
            try:
                fonction(*args, **kwargs)
            except SystemExit as sortie:
                code = sortie.code
        return code, tampon.getvalue()


class LectureDuVerrou(Base):
    def test_nom_normalise(self):
        self.assertEqual(amorcage.nom_normalise("Pillow"), "pillow")
        self.assertEqual(amorcage.nom_normalise("srtm.py"), amorcage.nom_normalise("srtm-py"))
        self.assertEqual(amorcage.nom_normalise("a_b__c"), "a-b-c")

    def test_dependances_directes_ignore_commentaires_options_et_marqueurs(self):
        fichier = self.racine / "autre.in"
        fichier.write_text("# commentaire\n\n-c contraintes.txt\nrequests>=2  # fin\n"
                           "nico579-commons[tray]>=0.4,<0.5\nnumba; sys_platform == 'win32'\n"
                           "tzdata==2024.1\n", encoding="utf-8")
        self.assertEqual(amorcage.dependances_directes(fichier),
                         ["requests", "nico579-commons", "tzdata"])
        self.assertEqual(amorcage.dependances_directes(fichier, conditionnelles=True),
                         ["requests", "nico579-commons", "numba", "tzdata"])

    def test_dependances_absentes_lit_les_metadonnees(self):
        class Distribution:
            def __init__(self, nom):
                self.metadata = {"Name": nom}

        installes = [Distribution("Alpha"), Distribution("pillow")]
        self.assertEqual(amorcage.dependances_absentes(["alpha", "Pillow", "beta"], installes),
                         ["beta"])

    def test_empreinte_et_commande(self):
        verrou = self.racine / "requirements.txt"
        empreinte = amorcage.empreinte_verrou(verrou)
        self.assertEqual(len(empreinte), 64)
        verrou.write_text("autre\n", encoding="utf-8")
        self.assertNotEqual(amorcage.empreinte_verrou(verrou), empreinte)
        self.assertEqual(
            amorcage.commande_installation("py", "--user", verrou=verrou),
            ["py", "-m", "pip", "install", "-q", "--disable-pip-version-check",
             "--require-hashes", "-r", str(verrou), "--user"])
        self.assertNotIn("--require-hashes",
                         amorcage.commande_installation("py", verrou=verrou,
                                                        avec_empreintes=False))


class ResolutionDuMode(Base):
    def resoudre(self, argv, env=None):
        with mock.patch.object(sys, "argv", list(argv)), \
                mock.patch.dict(os.environ, env or {}):
            moteur = self.moteur()
            mode = moteur.mode()
            return mode, list(sys.argv)

    def test_defaut_et_variable(self):
        self.assertEqual(self.resoudre(["x.py"]), ("auto", ["x.py"]))
        self.assertEqual(self.resoudre(["x.py"], {VARIABLE: " PIP "}), ("pip", ["x.py"]))
        self.assertEqual(self.resoudre(["x.py"], {VARIABLE: "invalide"}), ("auto", ["x.py"]))

    def test_nom_de_la_variable(self):
        self.assertEqual(amorcage.Amorcage("blink2video", self.racine).variable,
                         "BLINK2VIDEO_BOOTSTRAP")
        self.assertEqual(self.moteur(variable="BLINK_BOOTSTRAP").variable, "BLINK_BOOTSTRAP")

    def test_la_ligne_de_commande_l_emporte_et_est_consommee(self):
        mode, argv = self.resoudre(
            ["x.py", "--bootstrap=auto", "--zone", "z", "--bootstrap", "none"], {VARIABLE: "pip"})
        self.assertEqual(mode, "none")
        self.assertEqual(argv, ["x.py", "--zone", "z"])

    def test_force_est_un_mode(self):
        self.assertEqual(self.resoudre(["x.py", "--bootstrap=force"]), ("force", ["x.py"]))

    def test_nettoyage_en_place(self):
        argv = ["x.py", "--bootstrap=pip", "--zone", "z", "--no-venv", "--lidar"]
        with mock.patch.object(sys, "argv", argv):
            identite = id(sys.argv)
            mode = self.moteur().mode()
            self.assertEqual(id(sys.argv), identite)
        self.assertEqual(mode, "pip")
        self.assertEqual(argv, ["x.py", "--zone", "z", "--lidar"])

    def test_valeurs_invalides_sortent_en_2_sans_toucher_argv(self):
        cas = (["x.py", "--bootstrap=invalide", "--lidar"], ["x.py", "--bootstrap=", "--lidar"],
               ["x.py", "--bootstrap", "invalide", "--lidar"], ["x.py", "--bootstrap"],
               ["x.py", "--bootstrap", "--lidar"],
               ["x.py", "--bootstrap=none", "--lidar", "--bootstrap", "invalide"])
        for initial in cas:
            with self.subTest(argv=initial):
                argv = list(initial)
                erreur = io.StringIO()
                with mock.patch.object(sys, "argv", argv), \
                        mock.patch.dict(os.environ, {VARIABLE: "none"}), \
                        contextlib.redirect_stderr(erreur):
                    with self.assertRaises(SystemExit) as sortie:
                        self.moteur().mode()
                self.assertEqual(sortie.exception.code, 2)
                self.assertEqual(argv, initial)
                self.assertIn("--bootstrap", erreur.getvalue())

    def test_anciens_raccourcis(self):
        for drapeau, attendu in (("--no-bootstrap", "none"), ("--venv", "auto"),
                                 ("--no-venv", "pip")):
            with self.subTest(drapeau=drapeau):
                self.assertEqual(self.resoudre(["x.py", drapeau, "--lidar"]),
                                 (attendu, ["x.py", "--lidar"]))

    def test_priorite_fixe_des_raccourcis(self):
        for raccourcis in (["--no-venv", "--venv", "--no-bootstrap"],
                           ["--no-bootstrap", "--venv", "--no-venv"]):
            with self.subTest(raccourcis=raccourcis):
                self.assertEqual(self.resoudre(["x.py", "--bootstrap=none", *raccourcis]),
                                 ("pip", ["x.py"]))

    def test_aide_sort_en_0_et_laisse_argv(self):
        argv = ["x.py", "--bootstrap=invalide", "--help-bootstrap"]
        with mock.patch.object(sys, "argv", argv):
            code, texte = self.sortie(self.moteur().mode)
        self.assertEqual(code, 0)
        self.assertEqual(argv, ["x.py", "--bootstrap=invalide", "--help-bootstrap"])
        for attendu in ("--bootstrap=auto", "--bootstrap=force", "EXEMPLE_BOOTSTRAP",
                        "~/.exemple/venv"):
            self.assertIn(attendu, texte)


class ModeNone(Base):
    def test_tout_est_la(self):
        moteur = self.moteur()
        with mock.patch.object(amorcage, "dependances_absentes", return_value=[]):
            self.assertEqual(self.sortie(moteur.verifier)[0], None)

    def test_paquet_manquant_sort_en_1_avec_le_verrou(self):
        moteur = self.moteur()
        with mock.patch.object(amorcage, "dependances_absentes", return_value=["beta"]):
            code, texte = self.sortie(moteur.verifier)
        self.assertEqual(code, 1)
        self.assertIn("beta", texte)
        self.assertIn(str(self.racine / "requirements.txt"), texte)


class ModePip(Base):
    def test_rien_ne_manque_rien_n_est_lance(self):
        moteur = self.moteur()
        with mock.patch.object(moteur, "absentes", return_value=[]), \
                mock.patch.object(amorcage.subprocess, "run") as run:
            moteur.installer_si_besoin()
        run.assert_not_called()

    def lancer_pip(self, retours):
        moteur = self.moteur()
        appels = []

        def run(commande, **kwargs):
            appels.append(commande)
            retour = retours[len(appels) - 1]
            if isinstance(retour, Exception):
                raise retour
            return mock.Mock(returncode=retour, stdout="", stderr="trois\nlignes\nd'erreur")
        with mock.patch.object(moteur, "absentes", return_value=["beta"]), \
                mock.patch.object(amorcage.subprocess, "run", side_effect=run), \
                mock.patch.object(sys, "base_prefix", sys.prefix):
            code, texte = self.sortie(moteur.installer_si_besoin)
        return code, texte, appels

    def test_trois_strategies_la_premiere_qui_reussit_arrete(self):
        code, texte, appels = self.lancer_pip([1, 0])
        self.assertIsNone(code)
        self.assertEqual(len(appels), 2)
        self.assertIn("--break-system-packages", appels[1])
        self.assertIn("PEP 668", texte)

    def test_toutes_en_echec_sortie_1_avec_le_dernier_message(self):
        code, texte, appels = self.lancer_pip([1, subprocess.TimeoutExpired("pip", 1), 1])
        self.assertEqual(code, 1)
        self.assertEqual(len(appels), 3)
        self.assertIn("--user", appels[2])
        self.assertIn("d'erreur", texte)
        self.assertIn("--bootstrap=auto", texte)

    def test_dans_un_venv_seule_la_strategie_standard(self):
        moteur = self.moteur()
        with mock.patch.object(moteur, "absentes", return_value=["beta"]), \
                mock.patch.object(amorcage.subprocess, "run",
                                  return_value=mock.Mock(returncode=1, stdout="", stderr="x")) as run, \
                mock.patch.object(sys, "base_prefix", sys.prefix + "-autre"):
            self.assertEqual(self.sortie(moteur.installer_si_besoin)[0], 1)
        self.assertEqual(run.call_count, 1)

    def test_pip_absent_est_amorce_par_ensurepip(self):
        moteur = self.moteur()
        with mock.patch.object(amorcage.subprocess, "run",
                               return_value=mock.Mock(returncode=1)), \
                mock.patch("ensurepip.bootstrap") as bootstrap:
            self.sortie(moteur.assurer_pip)
        bootstrap.assert_called_once_with(upgrade=True)

    def test_ensurepip_en_echec_sort_en_1(self):
        moteur = self.moteur()
        with mock.patch.object(amorcage.subprocess, "run",
                               return_value=mock.Mock(returncode=1)), \
                mock.patch("ensurepip.bootstrap", side_effect=OSError("non")):
            self.assertEqual(self.sortie(moteur.assurer_pip)[0], 1)


class ModeAuto(Base):
    def test_deja_dans_le_venv_rien_a_faire(self):
        moteur = self.moteur()
        moteur.venv.mkdir(parents=True)
        with mock.patch.object(sys, "prefix", str(moteur.venv)), \
                mock.patch.object(moteur, "relancer") as relancer, \
                mock.patch.object(amorcage.subprocess, "run") as run:
            self.assertIsNone(self.sortie(moteur.venv_et_relance)[0])
        relancer.assert_not_called()
        run.assert_not_called()

    def test_venv_manquant_cree_installe_note_puis_relance(self):
        moteur = self.moteur()
        with self.faux_venv(), mock.patch.object(moteur, "relancer") as relancer, \
                mock.patch.object(moteur, "verifier_venv_linux"):
            self.assertIsNone(self.sortie(moteur.venv_et_relance)[0])
        self.assertEqual(self.appels[0][1:3], ["-m", "venv"])
        installation = self.appels[1]
        self.assertEqual(installation[0], str(self.python_du_venv()))
        self.assertIn("--require-hashes", installation)
        marque = moteur.venv / "exemple-verrou.sha256"
        self.assertEqual(marque.read_text().strip(), moteur.empreinte())
        relancer.assert_called_once()
        self.assertEqual(relancer.call_args[0][0], self.python_du_venv())

    def test_venv_installe_depuis_le_meme_verrou_relance_sans_pip(self):
        moteur = self.moteur()
        python = self.python_du_venv()
        python.parent.mkdir(parents=True)
        python.write_text("")
        (moteur.venv / "exemple-verrou.sha256").write_text(moteur.empreinte() + "\n")
        with mock.patch.object(moteur, "relancer") as relancer, \
                mock.patch.object(amorcage.subprocess, "run") as run:
            self.sortie(moteur.venv_et_relance)
        run.assert_not_called()
        relancer.assert_called_once()

    def test_venv_d_un_ancien_verrou_est_reinstalle_sans_etre_recree(self):
        moteur = self.moteur()
        python = self.python_du_venv()
        python.parent.mkdir(parents=True)
        python.write_text("")
        (moteur.venv / "exemple-verrou.sha256").write_text("ancienne\n")
        with self.faux_venv(), mock.patch.object(moteur, "relancer"):
            self.sortie(moteur.venv_et_relance)
        self.assertEqual(len(self.appels), 1)
        self.assertIn("--require-hashes", self.appels[0])
        self.assertEqual((moteur.venv / "exemple-verrou.sha256").read_text().strip(),
                         moteur.empreinte())

    def test_installation_en_echec_sortie_1_sans_marque_ni_relance(self):
        moteur = self.moteur()
        with self.faux_venv(installation=1), mock.patch.object(moteur, "relancer") as relancer, \
                mock.patch.object(moteur, "verifier_venv_linux"):
            code, texte = self.sortie(moteur.venv_et_relance)
        self.assertEqual(code, 1)
        self.assertIn("boum", texte)
        self.assertFalse((moteur.venv / "exemple-verrou.sha256").exists())
        relancer.assert_not_called()

    def test_delai_de_pip_depasse(self):
        moteur = self.moteur()
        python = self.python_du_venv()
        python.parent.mkdir(parents=True)
        python.write_text("")
        with mock.patch.object(amorcage.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("pip", 1800)):
            code, texte = self.sortie(moteur.venv_et_relance)
        self.assertEqual(code, 1)
        self.assertIn("timeout", texte)

    def test_creation_du_venv_impossible_sortie_1(self):
        moteur = self.moteur()
        with self.faux_venv(creation_echoue=True), mock.patch.object(moteur, "verifier_venv_linux"):
            code, texte = self.sortie(moteur.venv_et_relance)
        self.assertEqual(code, 1)
        self.assertIn("venv", texte)

    def test_windows_utilise_scripts(self):
        moteur = self.moteur()
        with mock.patch.object(amorcage.platform, "system", return_value="Windows"):
            python = self.python_du_venv(moteur)
            python.parent.mkdir(parents=True)
            python.write_text("")
            (moteur.venv / "exemple-verrou.sha256").write_text(moteur.empreinte() + "\n")
            with mock.patch.object(moteur, "relancer") as relancer:
                self.sortie(moteur.venv_et_relance)
        self.assertEqual(relancer.call_args[0], (python, True))
        self.assertEqual(python.parent.name, "Scripts")

    def test_environnement_actif_arrete_et_oriente(self):
        moteur = self.moteur()
        with mock.patch.dict(os.environ, {"CONDA_PREFIX": "/opt/conda"}), \
                mock.patch.object(amorcage.subprocess, "run") as run:
            code, texte = self.sortie(moteur.venv_et_relance)
        self.assertEqual(code, 1)
        self.assertIn("/opt/conda", texte)
        self.assertIn("--bootstrap=pip", texte)
        self.assertIn("--bootstrap=force", texte)
        run.assert_not_called()

    def test_force_passe_outre_l_environnement_actif(self):
        moteur = self.moteur()
        with mock.patch.dict(os.environ, {"VIRTUAL_ENV": "/mon/venv"}), self.faux_venv(), \
                mock.patch.object(moteur, "relancer") as relancer, \
                mock.patch.object(moteur, "verifier_venv_linux"):
            self.assertIsNone(self.sortie(moteur.venv_et_relance, force=True)[0])
        relancer.assert_called_once()

    def test_sans_reutilisation_le_venv_est_toujours_cree(self):
        moteur = self.moteur()
        with mock.patch.object(moteur, "absentes", return_value=[]), self.faux_venv(), \
                mock.patch.object(moteur, "relancer") as relancer, \
                mock.patch.object(moteur, "verifier_venv_linux"):
            self.sortie(moteur.venv_et_relance)
        relancer.assert_called_once()

    def test_reutiliser_l_environnement_sans_venv_existant_ne_fait_rien(self):
        # Une image Docker : les dépendances sont déjà là, aucun venv à créer.
        moteur = self.moteur(reutiliser_environnement=True)
        with mock.patch.object(moteur, "absentes", return_value=[]), \
                mock.patch.object(amorcage.subprocess, "run") as run:
            self.assertIsNone(self.sortie(moteur.venv_et_relance)[0])
        run.assert_not_called()

    def test_reutiliser_l_environnement_mais_un_venv_existe_on_s_y_met(self):
        moteur = self.moteur(reutiliser_environnement=True)
        python = self.python_du_venv()
        python.parent.mkdir(parents=True)
        python.write_text("")
        (moteur.venv / "exemple-verrou.sha256").write_text(moteur.empreinte() + "\n")
        with mock.patch.object(moteur, "absentes", return_value=[]), \
                mock.patch.object(moteur, "relancer") as relancer:
            self.sortie(moteur.venv_et_relance)
        relancer.assert_called_once()

    def test_reutiliser_l_environnement_force_cree_quand_meme(self):
        moteur = self.moteur(reutiliser_environnement=True)
        with mock.patch.object(moteur, "absentes", return_value=[]), self.faux_venv(), \
                mock.patch.object(moteur, "relancer") as relancer, \
                mock.patch.object(moteur, "verifier_venv_linux"):
            self.sortie(moteur.venv_et_relance, force=True)
        relancer.assert_called_once()

    def test_sans_verrou_installation_depuis_requirements_in(self):
        moteur = self.moteur(verrou=None)
        self.assertEqual(moteur.empreinte(), amorcage.empreinte_verrou(self.racine / "requirements.in"))
        commande = moteur.commande("py")
        self.assertNotIn("--require-hashes", commande)
        self.assertEqual(commande[-2:], ["-r", str(self.racine / "requirements.in")])


class VenvDeLinux(Base):
    def test_sans_effet_hors_linux(self):
        with mock.patch.object(amorcage.platform, "system", return_value="Windows"), \
                mock.patch.object(amorcage.subprocess, "run") as run:
            self.moteur().verifier_venv_linux()
        run.assert_not_called()

    def test_module_absent_message_apt_et_sortie_1(self):
        moteur = self.moteur()
        with mock.patch.object(amorcage.platform, "system", return_value="Linux"), \
                mock.patch.dict(sys.modules, {"venv": None}), \
                mock.patch.object(amorcage.subprocess, "run",
                                  return_value=mock.Mock(returncode=1)):
            code, texte = self.sortie(moteur.verifier_venv_linux)
        self.assertEqual(code, 1)
        self.assertIn("sudo apt install python3-venv", texte)


class Relance(Base):
    def test_unix_remplace_le_processus(self):
        moteur = self.moteur()
        with mock.patch.object(sys, "argv", ["x.py", "--a"]), \
                mock.patch.object(amorcage.os, "execv") as execv:
            moteur.relancer(Path("/v/bin/python"), False)
        execv.assert_called_once_with(str(Path("/v/bin/python")),
                                      [str(Path("/v/bin/python")), "x.py", "--a"])

    def test_windows_attend_le_fils_et_propage_son_code(self):
        moteur = self.moteur()
        with mock.patch.object(sys, "argv", ["x.py"]), \
                mock.patch.object(amorcage.subprocess, "run",
                                  return_value=mock.Mock(returncode=7)) as run:
            code, _ = self.sortie(moteur.relancer, Path("C:/v/Scripts/python.exe"), True)
        self.assertEqual(code, 7)
        # Les flux du parent sont passés tels quels (pas de tube, que la page ne lirait pas).
        self.assertIsNot(run.call_args[1]["stdout"], subprocess.PIPE)
        self.assertIsNotNone(run.call_args[1]["stdout"])

    def test_windows_ctrl_c_sort_en_130(self):
        moteur = self.moteur()
        with mock.patch.object(amorcage.subprocess, "run", side_effect=KeyboardInterrupt):
            self.assertEqual(self.sortie(moteur.relancer, Path("p"), True)[0], 130)


class Orchestration(Base):
    def lancer(self, mode, *, frozen=False, argv=("x.py",), **options):
        moteur = self.moteur(**options)
        appels = []
        for nom in ("verifier", "assurer_pip", "venv_et_relance", "installer_si_besoin"):
            patch = mock.patch.object(moteur, nom, side_effect=lambda *a, _n=nom, **k:
                                      appels.append((_n, a, k)))
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch.object(sys, "argv", list(argv)), \
                mock.patch.dict(os.environ, {VARIABLE: mode}), \
                mock.patch.object(sys, "frozen", frozen, create=True):
            code, _ = self.sortie(moteur.lancer)
            restant = list(sys.argv)
        return code, appels, restant

    def test_none_verifie_seulement(self):
        _, appels, _ = self.lancer("none")
        self.assertEqual([a[0] for a in appels], ["verifier"])

    def test_pip_assure_pip_puis_installe(self):
        _, appels, _ = self.lancer("pip")
        self.assertEqual([a[0] for a in appels], ["assurer_pip", "installer_si_besoin"])

    def test_auto_et_force_passent_par_le_venv(self):
        _, appels, _ = self.lancer("auto")
        self.assertEqual(appels[0], ("venv_et_relance", (), {"force": False}))
        _, appels, _ = self.lancer("force")
        self.assertEqual(appels[0], ("venv_et_relance", (), {"force": True}))
        self.assertEqual(appels[1][0], "installer_si_besoin")

    def test_binaire_fige_pas_d_amorcage_mais_les_options_sont_consommees(self):
        _, appels, restant = self.lancer(
            "auto", frozen=True, argv=("x.py", "--bootstrap=pip", "--installer-deps", "--a"))
        self.assertEqual(appels, [])
        self.assertEqual(restant, ["x.py", "--a"])

    def test_apres_installation_est_appele_sauf_en_mode_none(self):
        crochet = mock.Mock()
        self.lancer("auto", apres_installation=crochet)
        crochet.assert_called_once_with()
        crochet.reset_mock()
        self.lancer("none", apres_installation=crochet)
        crochet.assert_not_called()

    def test_installer_deps_installe_le_verrou_complet_et_quitte(self):
        moteur = self.moteur()
        with mock.patch.object(moteur, "venv_et_relance"), \
                mock.patch.object(moteur, "installer_si_besoin") as si_besoin, \
                mock.patch.object(moteur, "installer_verrou_complet", return_value=True), \
                mock.patch.object(sys, "argv", ["x.py", "--installer-deps"]):
            code, _ = self.sortie(moteur.lancer)
            self.assertEqual(sys.argv, ["x.py"])
        self.assertEqual(code, 0)
        si_besoin.assert_not_called()
        with mock.patch.object(moteur, "venv_et_relance"), \
                mock.patch.object(moteur, "installer_verrou_complet", return_value=False), \
                mock.patch.object(sys, "argv", ["x.py", "--installer-deps"]):
            self.assertEqual(self.sortie(moteur.lancer)[0], 1)

    def test_installer_verrou_complet(self):
        moteur = self.moteur()
        lancer = mock.Mock(return_value=mock.Mock(returncode=0, stdout="", stderr=""))
        messages = []
        self.assertTrue(moteur.installer_verrou_complet(lancer=lancer, executable="py",
                                                        ecrire=messages.append))
        self.assertEqual(lancer.call_args[0][0], moteur.commande("py"))
        lancer = mock.Mock(return_value=mock.Mock(returncode=1, stdout="", stderr="rate"))
        self.assertFalse(moteur.installer_verrou_complet(lancer=lancer, executable="py",
                                                         ecrire=messages.append))
        self.assertTrue(any("rate" in m for m in messages))


class Langue(Base):
    def test_francais_et_repli_sur_l_anglais(self):
        self.assertIn("Création", self.moteur(langue=lambda: "fr").texte("creation_venv", venv="v"))
        self.assertIn("Creating", self.moteur().texte("creation_venv", venv="v"))
        self.assertIn("Creating", self.moteur(langue=lambda: "de").texte("creation_venv", venv="v"))

    def test_memes_cles_dans_les_deux_langues(self):
        self.assertEqual(set(amorcage.LIBELLES["fr"]), set(amorcage.LIBELLES["en"]))

    def test_chaque_message_se_formate(self):
        for langue in ("fr", "en"):
            moteur = self.moteur(langue=lambda langue=langue: langue)
            for cle, modele in amorcage.LIBELLES[langue].items():
                with self.subTest(langue=langue, cle=cle):
                    champs = {nom: "x" for nom in
                              amorcage.re.findall(r"{(\w+)[^}]*}", modele)}
                    self.assertTrue(moteur.texte(cle, **champs))


class Console(Base):
    def test_une_console_qui_ne_sait_pas_afficher_les_cadres_n_est_pas_un_plantage(self):
        flux = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        with contextlib.redirect_stdout(flux):
            amorcage._ecrire("  ╔══╗ ✓ é")
            flux.flush()
        flux.detach()


class EssaiReel(Base):
    """Un vrai venv, avec un verrou vide : le moteur crée l'environnement, y
    installe (rien), note l'empreinte, et le programme s'y relance."""

    def test_premier_lancement_puis_relance_dans_le_venv(self):
        (self.racine / "requirements.in").write_text("", encoding="utf-8")
        (self.racine / "requirements.txt").write_text("", encoding="utf-8")
        script = self.racine / "exemple.py"
        script.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(Path(amorcage.__file__).parent)!r})\n"
            "import amorcage\n"
            f"amorcage.Amorcage('exemple', {str(self.racine)!r}).lancer()\n"
            "print('PRET', sys.prefix)\n", encoding="utf-8")
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        for nom in ("CONDA_PREFIX", "VIRTUAL_ENV", VARIABLE, "PYTHONPATH"):
            env.pop(nom, None)
        premier = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                                 env=env, timeout=300)
        self.assertEqual(premier.returncode, 0, premier.stdout + premier.stderr)
        venv = self.home / ".exemple" / "venv"
        # Sous macOS, /var est un lien vers /private/var : on compare les chemins résolus.
        prefixe = [l for l in premier.stdout.splitlines() if l.startswith("PRET ")][-1][5:]
        self.assertEqual(Path(prefixe).resolve(), venv.resolve())
        self.assertTrue((venv / "exemple-verrou.sha256").is_file())
        # Deuxième lancement : le venv est à jour, on s'y relance sans réinstaller.
        second = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                                env=env, timeout=120)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertNotIn("Installing dependencies", second.stdout)
        self.assertIn("PRET", second.stdout)


if __name__ == "__main__":
    unittest.main()
