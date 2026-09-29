"""Tests de outils/inventaire_communs.py sur de petits dépôts fabriqués : ce qui
est identique, ce qui se ressemble, ce qui est ignoré, et que rien n'est écrit
dans les dépôts relevés.

    python -m unittest discover -s tests
"""

import contextlib
import hashlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("inventaire_communs", RACINE / "outils" / "inventaire_communs.py")
inventaire = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(inventaire)

FONCTION = '''\
def calculer(a, b):
    """Docstring qui ne compte pas."""
    # un commentaire non plus
    total = 0
    for i in range(a):
        total += i * b
    if total > 100:
        total -= 1
    return total
'''

VOISINE = '''\
def voisine(x):
    resultat = []
    for i in range(x):
        resultat.append(i)
    resultat.reverse()
    resultat.sort()
    return resultat
'''


class DepotsFabriques(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.racine = Path(tmp.name)
        self.a, self.b = self.racine / "depot-a", self.racine / "depot-b"
        for depot in (self.a, self.b):
            depot.mkdir()

    def ecrire(self, depot, nom, texte):
        chemin = depot / nom
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(texte, encoding="utf-8")

    def rapport(self, **options):
        return inventaire.analyser({"aaa": self.a, "bbb": self.b}, **options)


class Comparaison(DepotsFabriques):
    def test_definition_identique_malgre_docstring_et_commentaires(self):
        self.ecrire(self.a, "outil.py", FONCTION)
        self.ecrire(self.b, "autre.py", FONCTION.replace("Docstring qui ne compte pas.", "Autre texte.")
                    .replace("# un commentaire non plus", "# et un autre"))
        rapport = self.rapport()
        self.assertIn("[2 dépôts] calculer", rapport)
        self.assertIn("aaa~bbb:IDENTIQUE", rapport)
        self.assertIn("aaa:outil.py,bbb:autre.py", rapport)

    def test_definition_voisine_donne_un_pourcentage(self):
        self.ecrire(self.a, "outil.py", VOISINE)
        self.ecrire(self.b, "outil.py", VOISINE.replace("resultat.sort()", "resultat.sort(reverse=True)"))
        rapport = self.rapport()
        ligne = [ligne for ligne in rapport.splitlines() if "aaa~bbb:" in ligne][0]
        self.assertNotIn("IDENTIQUE", ligne)
        self.assertRegex(ligne, r"aaa~bbb:\d+%")

    def test_meme_corps_sous_un_autre_nom(self):
        self.ecrire(self.a, "outil.py", FONCTION)
        self.ecrire(self.b, "outil.py", FONCTION.replace("def calculer", "def renomme"))
        section = inventaire.analyser({"aaa": self.a, "bbb": self.b}).split("## B.")[1].split("## C.")[0]
        self.assertIn("aaa:outil.py::calculer", section)
        self.assertIn("bbb:outil.py::renomme", section)

    def test_fichiers_de_meme_nom_meme_avec_tiret_bas_initial(self):
        self.ecrire(self.a, "_serve.py", FONCTION)
        self.ecrire(self.b, "serve.py", FONCTION)
        section = self.rapport().split("## C.")[1].split("## D.")[0]
        self.assertIn("serve.py", section)
        self.assertIn("aaa~bbb:100%", section)

    def test_doublons_stricts_comptent_les_copies_en_trop(self):
        self.ecrire(self.a, "outil.py", FONCTION)
        self.ecrire(self.b, "outil.py", FONCTION)
        section = self.rapport().split("## D.")[1]
        self.assertIn(f"1 groupes de définitions identiques entre dépôts, {len(FONCTION.splitlines())} lignes", section)
        self.assertIn("aaa:outil.py = bbb:outil.py", section)

    def test_seuil_de_taille_minimale(self):
        self.ecrire(self.a, "outil.py", "def petit():\n    return 1\n")
        self.ecrire(self.b, "outil.py", "def petit():\n    return 1\n")
        self.assertNotIn("petit", self.rapport())
        self.assertIn("petit", self.rapport(min_lignes=2))


class Exclusions(DepotsFabriques):
    def test_tests_et_dossiers_de_construction_ignores(self):
        self.ecrire(self.a, "test_outil.py", FONCTION)
        self.ecrire(self.b, "test_outil.py", FONCTION)
        self.ecrire(self.a, "build/outil.py", FONCTION)
        self.ecrire(self.b, "dist/outil.py", FONCTION)
        self.ecrire(self.a, "tests/aide.py", FONCTION)
        self.ecrire(self.b, "tests/aide.py", FONCTION)
        rapport = self.rapport()
        self.assertNotIn("calculer", rapport)
        self.assertIn("0 groupes de définitions identiques", rapport)

    def test_fichier_python_illisible_est_saute(self):
        self.ecrire(self.a, "casse.py", "def pas fini(:\n")
        self.ecrire(self.a, "outil.py", FONCTION)
        self.ecrire(self.b, "outil.py", FONCTION)
        self.assertIn("[2 dépôts] calculer", self.rapport())


class LectureSeule(DepotsFabriques):
    def empreinte(self):
        h = hashlib.sha1()
        for depot in (self.a, self.b):
            for chemin in sorted(depot.rglob("*")):
                h.update(str(chemin).encode())
                if chemin.is_file():
                    h.update(chemin.read_bytes())
        return h.hexdigest()

    def test_aucun_fichier_modifie_ni_cree(self):
        self.ecrire(self.a, "outil.py", FONCTION)
        self.ecrire(self.b, "outil.py", FONCTION)
        avant = self.empreinte()
        self.rapport()
        self.assertEqual(self.empreinte(), avant)


class LigneDeCommande(DepotsFabriques):
    def lancer(self, *arguments):
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            code = inventaire.main(list(arguments))
        return code, sortie.getvalue()

    def test_depots_par_defaut_sous_la_racine_et_surcharge(self):
        for nom in inventaire.DEPOTS_PAR_DEFAUT.values():
            (self.racine / nom).mkdir()
        self.ecrire(self.racine / "blink-commons", "outil.py", FONCTION)
        self.ecrire(self.a, "outil.py", FONCTION)
        code, texte = self.lancer("--racine", str(self.racine), "--depot", f"zzz={self.a}")
        self.assertEqual(code, 0)
        self.assertIn("# b2v: 1 fichiers .py", texte)
        self.assertIn("# zzz: 1 fichiers .py", texte)
        self.assertIn("b2v~zzz:IDENTIQUE", texte)

    def test_depot_introuvable_ou_mal_forme(self):
        with self.assertRaises(SystemExit) as erreur:
            inventaire.lire_depots(self.racine, [])
        self.assertIn("introuvable", str(erreur.exception))
        with self.assertRaises(SystemExit) as erreur:
            inventaire.lire_depots(self.racine, ["sans-egal"])
        self.assertIn("sigle=chemin", str(erreur.exception))


if __name__ == "__main__":
    unittest.main()
