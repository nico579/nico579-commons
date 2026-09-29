"""Tests du module dossiers : l'état dans le dossier standard de l'OS, les
sorties dans un dossier visible, la reprise unique de l'état d'une ancienne
version. Cas repris de test_dossiers.py de gpxsolar et de lidar2map, avec une
application fictive.

Isolation stricte : dans chaque test, le dossier standard et Documents sont
remplacés par des dossiers temporaires. Changer LOCALAPPDATA ne suffirait pas :
sous Windows, platformdirs interroge le shell, qui l'ignore.

    python -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nico579_commons.dossiers import Dossiers  # noqa: E402

PREFERENCES = "demo_prefs.json"
FICHIERS = (PREFERENCES, "demo_config.json", "demo_historique.json")


def fabriquer():
    return Dossiers("demo", preferences=PREFERENCES, fichiers_etat=FICHIERS,
                    dossiers_sorties=("Resultats", "Caches"))


class Isole(unittest.TestCase):
    """Dossier standard, Documents et ancien dossier de travail dans un dossier
    temporaire au nom accentué ; DEMO_HOME retiré."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.racine = Path(tmp.name).resolve() / "Données é"
        self.etat = self.racine / "AppData" / "Local" / "demo-data"
        self.documents = self.racine / "Documents"
        self.ancien = self.racine / "Anciennes" / "demo-windows-x86_64"
        self.ancien.mkdir(parents=True)
        self.d = fabriquer()
        sans_home = {c: v for c, v in os.environ.items() if c != "DEMO_HOME"}
        for correctif in (
                mock.patch.dict(os.environ, sans_home, clear=True),
                mock.patch.object(Dossiers, "dossier_etat_standard", return_value=self.etat),
                mock.patch.object(Dossiers, "documents", return_value=self.documents)):
            correctif.start()
            self.addCleanup(correctif.stop)

    def ecrire_ancien(self, nom, contenu):
        (self.ancien / nom).write_text(contenu, encoding="utf-8")

    def preferences(self):
        return json.loads((self.etat / PREFERENCES).read_text(encoding="utf-8"))


class Noms(unittest.TestCase):
    def test_valeurs_par_defaut_derivees_du_nom(self):
        d = Dossiers("gpxsolar")
        self.assertEqual((d.nom_etat, d.nom_sorties, d.variable_home, d.marqueur),
                         ("gpxsolar-data", "gpxsolar", "GPXSOLAR_HOME", ".gpxsolar_etat_migre.json"))

    def test_chaque_nom_peut_etre_impose(self):
        d = Dossiers("x", nom_etat="etat", nom_sorties="sorties", variable_home="MA_VAR",
                     marqueur=".m.json")
        self.assertEqual((d.nom_etat, d.nom_sorties, d.variable_home, d.marqueur),
                         ("etat", "sorties", "MA_VAR", ".m.json"))


class CalculsPurs(Isole):
    def test_etat_dans_le_dossier_standard(self):
        self.assertEqual(self.d.dossier_etat(), self.etat)

    def test_sorties_dans_documents_par_defaut(self):
        self.assertEqual(self.d.dossier_sorties(), self.documents / "demo")

    def test_simple_calcul_rien_n_est_cree(self):
        self.d.dossier_etat()
        self.d.dossier_sorties()
        self.assertFalse(self.etat.exists())
        self.assertFalse(self.documents.exists())

    def test_la_variable_home_regroupe_etat_et_sorties(self):
        portable = self.racine / "portable"
        with mock.patch.dict(os.environ, {"DEMO_HOME": str(portable)}):
            self.assertEqual(self.d.dossier_etat(), portable)
            self.assertEqual(self.d.dossier_sorties(), portable)

    def test_environnement_passe_en_parametre(self):
        portable = self.racine / "portable"
        self.assertEqual(self.d.dossier_etat({"DEMO_HOME": str(portable)}), portable)
        self.assertEqual(self.d.dossier_etat({}), self.etat)

    def test_reglage_dossier_sorties_prioritaire(self):
        self.etat.mkdir(parents=True)
        randos = self.racine / "D" / "Randos"
        (self.etat / PREFERENCES).write_text(
            json.dumps({"lang": "fr", "dossier_sorties": str(randos)}), encoding="utf-8")
        self.assertEqual(self.d.dossier_sorties(), randos)

    def test_reglage_vide_ou_mal_forme_est_ignore(self):
        self.etat.mkdir(parents=True)
        for valeur in ("", "   ", 3, None, ["x"]):
            with self.subTest(valeur=valeur):
                (self.etat / PREFERENCES).write_text(
                    json.dumps({"dossier_sorties": valeur}), encoding="utf-8")
                self.assertEqual(self.d.dossier_sorties(), self.documents / "demo")
        (self.etat / PREFERENCES).write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(self.d.dossier_sorties(), self.documents / "demo")


class RepliSansPlatformdirs(unittest.TestCase):
    def test_repli_identique_a_platformdirs(self):
        try:
            import platformdirs  # noqa: F401
        except ImportError:
            self.skipTest("platformdirs absent : rien à comparer")
        d = fabriquer()
        reel = d.dossier_etat_standard()
        with mock.patch.dict(sys.modules, {"platformdirs": None}):
            repli = d.dossier_etat_standard()
        self.assertEqual(repli, reel)
        self.assertEqual(reel.name, "demo-data")

    def test_documents_de_repli(self):
        with mock.patch.dict(sys.modules, {"platformdirs": None}):
            self.assertEqual(fabriquer().documents(), Path.home() / "Documents")


class Reprise(Isole):
    def test_etat_copie_et_sorties_laissees_en_place(self):
        self.ecrire_ancien(PREFERENCES, json.dumps({"lang": "fr"}))
        self.ecrire_ancien("demo_config.json", '{"date": "01/07/2026"}')
        self.ecrire_ancien("demo_historique.json", "[]")
        (self.ancien / "Resultats").mkdir()

        repris = self.d.preparer_etat(self.ancien, version="2.0")

        self.assertEqual(repris, list(FICHIERS) + ["dossier_sorties"])
        self.assertEqual(self.preferences(), {"lang": "fr", "dossier_sorties": str(self.ancien)})
        self.assertEqual((self.etat / "demo_config.json").read_text(encoding="utf-8"),
                         '{"date": "01/07/2026"}')
        self.assertEqual(self.d.dossier_sorties(), self.ancien)
        # Copiés, jamais déplacés : revenir à l'ancienne version reste possible.
        for nom in FICHIERS:
            self.assertTrue((self.ancien / nom).is_file(), nom)
        self.assertTrue((self.ancien / "Resultats").is_dir())
        marqueur = json.loads((self.etat / self.d.marqueur).read_text(encoding="utf-8"))
        self.assertEqual((marqueur["depuis"], marqueur["version"], marqueur["dossier_sorties"]),
                         (str(self.ancien), "2.0", str(self.ancien)))

    def test_une_seule_reprise(self):
        self.ecrire_ancien("demo_historique.json", "[]")
        self.assertEqual(self.d.preparer_etat(self.ancien), ["demo_historique.json"])
        self.ecrire_ancien("demo_config.json", "{}")
        self.assertEqual(self.d.preparer_etat(self.ancien), [])
        self.assertFalse((self.etat / "demo_config.json").exists())

    def test_rien_a_reprendre_ni_marqueur_ni_reglage(self):
        self.assertEqual(self.d.preparer_etat(self.ancien), [])
        self.assertFalse((self.etat / self.d.marqueur).exists())
        self.assertFalse((self.etat / PREFERENCES).exists())
        # Sans marqueur, la version installée pourra encore reprendre.
        self.ecrire_ancien("demo_historique.json", "[]")
        self.assertEqual(self.d.preparer_etat(self.ancien), ["demo_historique.json"])

    def test_n_ecrase_jamais_l_etat_ni_le_reglage_existants(self):
        self.etat.mkdir(parents=True)
        (self.etat / "demo_historique.json").write_text("[1]", encoding="utf-8")
        (self.etat / PREFERENCES).write_text(
            json.dumps({"dossier_sorties": "D:/Randos"}), encoding="utf-8")
        self.ecrire_ancien("demo_historique.json", "[]")
        self.ecrire_ancien(PREFERENCES, "{}")
        (self.ancien / "Caches").mkdir()

        self.assertEqual(self.d.preparer_etat(self.ancien), [])
        self.assertEqual((self.etat / "demo_historique.json").read_text(encoding="utf-8"), "[1]")
        self.assertEqual(self.preferences(), {"dossier_sorties": "D:/Randos"})

    def test_sans_effet_avec_la_variable_home(self):
        self.ecrire_ancien("demo_historique.json", "[]")
        with mock.patch.dict(os.environ, {"DEMO_HOME": str(self.racine / "portable")}):
            self.assertEqual(self.d.preparer_etat(self.ancien), [])
        self.assertFalse(self.etat.exists())

    def test_ancien_dossier_deja_dossier_d_etat(self):
        self.etat.mkdir(parents=True)
        (self.etat / "demo_historique.json").write_text("[]", encoding="utf-8")
        self.assertEqual(self.d.preparer_etat(self.etat), [])
        self.assertFalse((self.etat / self.d.marqueur).exists())

    def test_lancements_simultanes_une_seule_reprise(self):
        # Deux lancements au même instant : le verrou exclut aussi deux fils
        # d'un même processus.
        self.ecrire_ancien("demo_historique.json", "[]")
        (self.ancien / "Resultats").mkdir()
        resultats = []
        depart = threading.Barrier(4)

        def lancer():
            depart.wait()
            resultats.append(self.d.preparer_etat(self.ancien))

        fils = [threading.Thread(target=lancer) for _ in range(4)]
        for f in fils:
            f.start()
        for f in fils:
            f.join()
        self.assertEqual(sorted(bool(r) for r in resultats), [False, False, False, True])
        self.assertEqual(self.preferences(), {"dossier_sorties": str(self.ancien)})

    def test_copier_si_absent_n_ecrase_pas_et_ignore_une_source_absente(self):
        source, cible = self.ancien / "a.txt", self.racine / "b.txt"
        self.assertFalse(Dossiers.copier_si_absent(source, cible))
        source.write_text("neuf")
        self.assertTrue(Dossiers.copier_si_absent(source, cible))
        source.write_text("autre")
        self.assertFalse(Dossiers.copier_si_absent(source, cible))
        self.assertEqual(cible.read_text(), "neuf")
        self.assertEqual([p.name for p in self.racine.iterdir() if p.suffix == ".part"], [])


if __name__ == "__main__":
    unittest.main()
