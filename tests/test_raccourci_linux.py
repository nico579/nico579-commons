"""Raccourci de bureau sous Linux, vu dans la VM Ubuntu de blink2video le
2026-10-06 : « Créer un raccourci sur le Bureau » ne faisait rien.

1. Le Bureau n'est pas toujours ~/Desktop : ~/Bureau en français.
2. La clé Exec d'un .desktop n'est pas du shell : « sh -c '...' » n'est pas la
   syntaxe de la spécification Desktop Entry.
3. GNOME (DING, Nautilus) ne tient un lanceur pour autorisé que si
   metadata::trusted vaut exactement « true » : « yes » donnait « Untrusted
   Desktop File ».

Un analyseur de référence relit la ligne Exec comme le ferait un bureau.

    python -m unittest discover -s tests
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nico579_commons import raccourci  # noqa: E402


def relire_exec(valeur: str) -> list:
    """Exec relu selon la spécification : règle des chaînes (\\\\ vaut \\),
    puis découpe par espaces, guillemets doubles et barres d'échappement, et
    « %% » vaut « % »."""
    chaine = valeur.replace("\\\\", "\\")
    arguments, courant, dans, actif, i = [], [], False, False, 0
    while i < len(chaine):
        c = chaine[i]
        if dans:
            if c == "\\" and i + 1 < len(chaine):
                i += 1
                courant.append(chaine[i])
            elif c == '"':
                dans = False
            else:
                courant.append(c)
        elif c == '"':
            dans, actif = True, True
        elif c == " ":
            if actif or courant:
                arguments.append("".join(courant))
            courant, actif = [], False
        else:
            courant.append(c)
        i += 1
    if actif or courant:
        arguments.append("".join(courant))
    return [a.replace("%%", "%") for a in arguments]


class ExecConforme(unittest.TestCase):
    def aller_retour(self, arguments):
        ecrit = " ".join(raccourci.argument_exec(a) for a in arguments)
        self.assertEqual(relire_exec(ecrit), arguments, ecrit)
        self.assertNotIn("sh -c", ecrit)
        return ecrit

    def test_chemin_simple_reste_nu(self):
        self.assertEqual(self.aller_retour(["/usr/bin/python3", "/app/x.py", "start"]),
                         "/usr/bin/python3 /app/x.py start")

    def test_chemins_difficiles(self):
        for argument in ("/home/a b/app", "/home/o'brien/app", 'dos"guillemet', "prix$1",
                         "100%", "100%d", "back\\slash", "a`b", "(x)&y;z|w", "~/x",
                         "tab\tici", "é-ü/日本", "a  b", "#x", "*?"):
            with self.subTest(argument=argument):
                self.aller_retour(["/opt/app", argument, "start"])

    def test_pourcent_litteral_est_double(self):
        self.assertEqual(raccourci.argument_exec("a%b"), "a%%b")

    def test_barre_inverse_litterale_s_ecrit_avec_quatre(self):
        self.assertEqual(raccourci.argument_exec("a\\b"), '"a\\\\\\\\b"')

    def test_argument_vide_reste_un_argument(self):
        self.assertEqual(raccourci.argument_exec(""), '""')
        self.aller_retour(["/opt/app", "", "start"])

    def test_le_contenu_n_utilise_plus_sh_c(self):
        contenu = raccourci.contenu_desktop("app", ["/opt/mon app/run", "start"], Path("/opt/mon app"))
        exec_ligne = next(l for l in contenu.splitlines() if l.startswith("Exec="))
        self.assertEqual(relire_exec(exec_ligne[5:]), ["/opt/mon app/run", "start"])
        self.assertNotIn("sh -c", exec_ligne)


class BureauLinux(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="commons-bureau-")
        self.addCleanup(self.tmp.cleanup)
        self.accueil = Path(self.tmp.name)
        for p in (mock.patch.object(Path, "home", return_value=self.accueil),
                  mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": ""})):
            p.start()
            self.addCleanup(p.stop)

    def dirs(self, texte):
        (self.accueil / ".config").mkdir(exist_ok=True)
        (self.accueil / ".config" / "user-dirs.dirs").write_text(texte, encoding="utf-8")

    def test_bureau_francais(self):
        self.dirs('XDG_DOCUMENTS_DIR="$HOME/Documents"\nXDG_DESKTOP_DIR="$HOME/Bureau"\n')
        self.assertEqual(raccourci.bureau("linux"), self.accueil / "Bureau")

    def test_accolades_et_chemin_absolu(self):
        self.dirs('XDG_DESKTOP_DIR="${HOME}/Schreibtisch"\n')
        self.assertEqual(raccourci.bureau("linux"), self.accueil / "Schreibtisch")
        ailleurs = self.accueil / "ailleurs" / "bureau"
        self.dirs(f'XDG_DESKTOP_DIR="{ailleurs}"\n')
        self.assertEqual(raccourci.bureau("linux"), ailleurs)

    def test_bureau_desactive_ou_relatif_retombe_sur_desktop(self):
        for ligne in ('XDG_DESKTOP_DIR="$HOME/"', 'XDG_DESKTOP_DIR="$HOME"', 'XDG_DESKTOP_DIR="bureau"'):
            with self.subTest(ligne=ligne):
                self.dirs(ligne + "\n")
                self.assertEqual(raccourci.bureau("linux"), self.accueil / "Desktop")

    def test_sans_fichier_de_configuration(self):
        self.assertEqual(raccourci.bureau("linux"), self.accueil / "Desktop")

    def test_xdg_config_home_est_respecte(self):
        autre = self.accueil / "cfg"
        autre.mkdir()
        (autre / "user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/Escritorio"\n', encoding="utf-8")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(autre)}):
            self.assertEqual(raccourci.bureau("linux"), self.accueil / "Escritorio")

    def test_les_autres_plateformes_gardent_desktop(self):
        self.dirs('XDG_DESKTOP_DIR="$HOME/Bureau"\n')
        self.assertEqual(raccourci.bureau("darwin"), self.accueil / "Desktop")


class CreationLinux(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="commons-raccourci-")
        self.addCleanup(self.tmp.cleanup)
        self.accueil = Path(self.tmp.name)
        for p in (mock.patch.object(Path, "home", return_value=self.accueil),
                  mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": ""})):
            p.start()
            self.addCleanup(p.stop)
        self.commandes = []
        self.messages = []

    def lancer(self, commande):
        self.commandes.append(list(commande))
        return subprocess.CompletedProcess(commande, 0, "", "")

    def creer(self, **options):
        return raccourci.creer("monapp", ["/opt/mon app/monapp", "start"], Path("/opt/mon app"),
                               icone=Path("/opt/mon app/i.png"), plateforme="linux",
                               lancer=self.lancer, ecrire=self.messages.append, **options)

    def test_ecrit_dans_le_bureau_francais_cree_au_besoin(self):
        (self.accueil / ".config").mkdir()
        (self.accueil / ".config" / "user-dirs.dirs").write_text(
            'XDG_DESKTOP_DIR="$HOME/Bureau"\n', encoding="utf-8")
        self.assertEqual(self.creer(), 0)
        fichier = self.accueil / "Bureau" / "monapp.desktop"
        self.assertTrue(fichier.is_file())
        self.assertFalse((self.accueil / "Desktop").exists())
        lignes = dict(l.split("=", 1) for l in fichier.read_text(encoding="utf-8").splitlines()[1:])
        self.assertEqual(relire_exec(lignes["Exec"]), ["/opt/mon app/monapp", "start"])

    def test_le_lanceur_est_marque_de_confiance_avec_la_valeur_true(self):
        self.assertEqual(self.creer(), 0)
        fichier = self.accueil / "Desktop" / "monapp.desktop"
        self.assertEqual(self.commandes, [["gio", "set", str(fichier), "metadata::trusted", "true"]])

    def test_gio_absent_ne_fait_pas_echouer_la_creation(self):
        def sans_gio(commande):
            raise FileNotFoundError("gio")
        with mock.patch.object(self, "lancer", sans_gio):
            self.assertEqual(self.creer(), 0)
        self.assertTrue((self.accueil / "Desktop" / "monapp.desktop").is_file())


if __name__ == "__main__":
    unittest.main()
