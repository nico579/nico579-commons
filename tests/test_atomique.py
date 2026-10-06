"""Tests du module atomique : lecture JSON tolérante, remplacement qui retente
un refus Windows passager, verrou entre vrais processus, chemins de staging.

    python -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from nico579_commons import atomique  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dossier = Path(self._tmp.name)


class LireJson(Base):
    def test_absent_rend_le_defaut(self):
        self.assertEqual(atomique.lire_json(self.dossier / "absent.json", {"a": 1}), {"a": 1})

    def test_corrompu_rend_le_defaut(self):
        fichier = self.dossier / "x.json"
        fichier.write_text("{pas du json", encoding="utf-8")
        self.assertEqual(atomique.lire_json(fichier, []), [])

    def test_corrompu_leve_si_la_tolerance_est_retiree(self):
        # config.json de watch2notif : un fichier que l'utilisateur peut encore
        # réparer à la main ne doit pas être remplacé en silence par le défaut.
        fichier = self.dossier / "x.json"
        fichier.write_text("{pas du json", encoding="utf-8")
        with self.assertRaises(ValueError):
            atomique.lire_json(fichier, [], tolerer_corrompu=False)
        self.assertEqual(atomique.lire_json(self.dossier / "absent.json", {"a": 1},
                                            tolerer_corrompu=False), {"a": 1})

    def test_bom_utf8_accepte(self):
        # Bloc-notes et PowerShell 5.1 ajoutent un BOM : en utf-8 simple le
        # fichier passait pour corrompu et la réécriture suivante l'appauvrissait.
        fichier = self.dossier / "bom.json"
        fichier.write_bytes(b"\xef\xbb\xbf" + json.dumps({"k": "é"}).encode("utf-8"))
        self.assertEqual(atomique.lire_json(fichier, None), {"k": "é"})

    def test_refus_passager_est_absorbe(self):
        fichier = self.dossier / "x.json"
        fichier.write_text('{"ok": true}', encoding="utf-8")
        vrai = Path.read_text
        appels = {"n": 0}

        def capricieux(self_, *a, **k):
            appels["n"] += 1
            if appels["n"] < 3:
                raise PermissionError("tenu par un autre processus")
            return vrai(self_, *a, **k)

        with mock.patch.object(Path, "read_text", capricieux), \
                mock.patch.object(atomique.time, "sleep"):
            self.assertEqual(atomique.lire_json(fichier, None), {"ok": True})
        self.assertEqual(appels["n"], 3)

    def test_refus_qui_persiste_leve_au_lieu_de_rendre_le_defaut(self):
        # Rendre le défaut ferait réécrire un fichier présent avec presque rien.
        fichier = self.dossier / "x.json"
        fichier.write_text("{}", encoding="utf-8")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("refus")), \
                mock.patch.object(atomique.time, "sleep"):
            with self.assertRaises(PermissionError):
                atomique.lire_json(fichier, {"defaut": True})


class Remplacer(Base):
    def test_remplace_le_fichier(self):
        source, cible = self.dossier / "a", self.dossier / "b"
        source.write_text("neuf")
        cible.write_text("ancien")
        atomique.remplacer(source, cible)
        self.assertEqual(cible.read_text(), "neuf")
        self.assertFalse(source.exists())

    def test_retente_un_refus_passager(self):
        vrai = os.replace
        appels = {"n": 0}

        def capricieux(a, b):
            appels["n"] += 1
            if appels["n"] < 4:
                raise PermissionError("lecteur en cours")
            return vrai(a, b)

        source, cible = self.dossier / "a", self.dossier / "b"
        source.write_text("x")
        with mock.patch.object(atomique.os, "replace", capricieux), \
                mock.patch.object(atomique.time, "sleep"):
            atomique.remplacer(source, cible)
        self.assertEqual(appels["n"], 4)
        self.assertEqual(cible.read_text(), "x")

    def test_refus_qui_persiste_est_propage(self):
        with mock.patch.object(atomique.os, "replace", side_effect=PermissionError("non")), \
                mock.patch.object(atomique.time, "sleep") as pause:
            with self.assertRaises(PermissionError):
                atomique.remplacer("a", "b")
        self.assertEqual(pause.call_count, atomique.TENTATIVES_REFUS - 1)


class CheminPart(Base):
    def test_chemins_uniques_dans_le_meme_dossier(self):
        cible = self.dossier / "donnees.json"
        chemins = {atomique.chemin_part(cible) for _ in range(50)}
        self.assertEqual(len(chemins), 50)
        for chemin in chemins:
            self.assertEqual(chemin.parent, self.dossier)
            self.assertTrue(chemin.name.startswith("donnees.json.") and chemin.name.endswith(".part"))

    def test_ne_touche_pas_au_fichier_final(self):
        cible = self.dossier / "base.db"
        cible.write_text("final")
        atomique.chemin_part(cible)
        self.assertEqual(cible.read_text(), "final")


class EcrireJson(Base):
    def test_ecrit_et_relit_sans_reste(self):
        chemin = self.dossier / "sous" / "dossier" / "x.json"
        atomique.ecrire_json(chemin, {"nom": "élève", "n": [1, 2]})
        self.assertEqual(json.loads(chemin.read_text(encoding="utf-8")), {"nom": "élève", "n": [1, 2]})
        self.assertIn("élève", chemin.read_text(encoding="utf-8"))   # ensure_ascii=False
        self.assertEqual([p.name for p in chemin.parent.iterdir()], ["x.json"])

    def test_indent_none_ecrit_un_json_compact(self):
        chemin = self.dossier / "x.json"
        atomique.ecrire_json(chemin, {"a": [1, 2]}, indent=None)
        self.assertEqual(chemin.read_text(encoding="utf-8"), '{"a": [1, 2]}')
        atomique.ecrire_json(chemin, {"a": 1})
        self.assertIn("\n", chemin.read_text(encoding="utf-8"))   # indent=2 par défaut

    def test_remplace_l_ancien_contenu(self):
        chemin = self.dossier / "x.json"
        atomique.ecrire_json(chemin, {"v": 1})
        atomique.ecrire_json(chemin, {"v": 2})
        self.assertEqual(atomique.lire_json(chemin, None), {"v": 2})

    def test_echec_ne_laisse_ni_temporaire_ni_perte(self):
        chemin = self.dossier / "x.json"
        atomique.ecrire_json(chemin, {"v": 1})
        with self.assertRaises(TypeError):
            atomique.ecrire_json(chemin, {"v": object()})   # non sérialisable
        self.assertEqual(atomique.lire_json(chemin, None), {"v": 1})
        self.assertEqual([p.name for p in self.dossier.iterdir()], ["x.json"])

    def test_ecrivains_simultanes_ne_se_marchent_pas_dessus(self):
        # Un temporaire à nom fixe partagé faisait échouer le second
        # os.replace (FileNotFoundError) : ici chaque appel a le sien.
        chemin = self.dossier / "x.json"
        erreurs = []

        def ecrire(i):
            try:
                for j in range(30):
                    atomique.ecrire_json(chemin, {"i": i, "j": j})
            except Exception as exc:  # pragma: no cover - échec du test
                erreurs.append(exc)

        fils = [threading.Thread(target=ecrire, args=(i,)) for i in range(4)]
        for f in fils:
            f.start()
        for f in fils:
            f.join()
        self.assertEqual(erreurs, [])
        self.assertIsInstance(atomique.lire_json(chemin, None), dict)
        self.assertEqual([p.name for p in self.dossier.iterdir()], ["x.json"])


class VerrouInterProcessus(Base):
    def test_exclusion_entre_fils(self):
        chemin = self.dossier / "donnees"
        en_cours = {"n": 0, "max": 0}

        def travailler():
            with atomique.verrou_inter_processus(chemin):
                en_cours["n"] += 1
                en_cours["max"] = max(en_cours["max"], en_cours["n"])
                time.sleep(0.02)
                en_cours["n"] -= 1

        fils = [threading.Thread(target=travailler) for _ in range(6)]
        for f in fils:
            f.start()
        for f in fils:
            f.join()
        self.assertEqual(en_cours["max"], 1)

    def test_delai_depasse_leve_timeouterror(self):
        chemin = self.dossier / "donnees"
        with atomique.verrou_inter_processus(chemin):
            debut = time.monotonic()
            with self.assertRaises(TimeoutError):
                with atomique.verrou_inter_processus(chemin, delai_s=0.2):
                    pass   # pragma: no cover
            self.assertGreaterEqual(time.monotonic() - debut, 0.2)

    def test_relache_a_la_sortie_meme_sur_erreur(self):
        chemin = self.dossier / "donnees"
        with self.assertRaises(RuntimeError):
            with atomique.verrou_inter_processus(chemin):
                raise RuntimeError("boum")
        with atomique.verrou_inter_processus(chemin, delai_s=0.5):
            pass

    def test_exclusion_entre_vrais_processus(self):
        # Le verrou d'un AUTRE processus refuse celui-ci, et disparaît avec lui.
        chemin = self.dossier / "donnees"
        code = (
            "import sys, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from nico579_commons import atomique\n"
            "with atomique.verrou_inter_processus(sys.argv[2]):\n"
            "    print('pris', flush=True)\n"
            "    time.sleep(30)\n"
        )
        fils = subprocess.Popen([sys.executable, "-c", code, str(SRC), str(chemin)],
                                stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(fils.stdout.readline().strip(), "pris")
            with self.assertRaises(TimeoutError):
                with atomique.verrou_inter_processus(chemin, delai_s=0.3):
                    pass   # pragma: no cover
        finally:
            fils.kill()
            fils.wait()
            fils.stdout.close()
        # Mort du détenteur : l'OS a relâché le verrou, rien à nettoyer.
        with atomique.verrou_inter_processus(chemin, delai_s=5):
            pass


if __name__ == "__main__":
    unittest.main()
