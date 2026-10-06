"""Tests de maj_archive : choix du fichier de release, téléchargement vérifié,
extraction sans risque.

Repris des tests de blink2video (test_maj_deploy_security.py) et de
watch2notif (test_self_update.py), dont maj_archive est la fusion : une
archive qui sort de son dossier ne doit rien écrire dehors, un téléchargement
n'est publié qu'après taille et empreinte, une redirection étrangère est
refusée. Aucun accès au réseau.

    python -m unittest discover -s tests
"""

import hashlib
import io
import os
import ssl
import stat
import sys
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nico579_commons import maj_archive as ma  # noqa: E402

DEPOT = "nico579/exemple"
AGENT = "exemple-test"


class Reponse(io.BytesIO):
    def __init__(self, contenu: bytes, url: str, taille_http="auto"):
        super().__init__(contenu)
        self._url = url
        self.headers = {}
        if taille_http == "auto":
            taille_http = len(contenu)
        if taille_http is not None:
            self.headers["Content-Length"] = str(taille_http)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def url_de(nom: str, version: str = "v9.0.0") -> str:
    return f"https://github.com/{DEPOT}/releases/download/{version}/{nom}"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.racine = Path(self._tmp.name)


class Adresses(unittest.TestCase):
    def test_url_de_release_exacte(self):
        self.assertTrue(ma.url_de_release(url_de("a.zip"), DEPOT, "a.zip"))
        for mauvaise in (
                f"http://github.com/{DEPOT}/releases/download/v9.0.0/a.zip",
                "https://github.com/autre/depot/releases/download/v9.0.0/a.zip",
                f"https://github.com/{DEPOT}/releases/download/latest/a.zip",
                f"https://github.com/{DEPOT}/releases/download/v9.0.0/b.zip",
                f"https://github.com/{DEPOT}/releases/download/v9.0.0/a.zip?x=1",
                f"https://user@github.com/{DEPOT}/releases/download/v9.0.0/a.zip",
                f"https://github.com:8443/{DEPOT}/releases/download/v9.0.0/a.zip",
                f"https://evil.example/{DEPOT}/releases/download/v9.0.0/a.zip"):
            with self.subTest(url=mauvaise):
                self.assertFalse(ma.url_de_release(mauvaise, DEPOT, "a.zip"))

    def test_redirection_reste_chez_github(self):
        for ok in ("https://github.com/x", "https://objects.githubusercontent.com/x",
                   "https://release-assets.githubusercontent.com/x"):
            self.assertTrue(ma.url_de_redirection(ok), ok)
        for non in ("https://evil.example/x", "http://github.com/x",
                    "https://github.com.evil.example/x", "https://u@github.com/x",
                    "https://github.com:444/x", "https://githubusercontent.com.evil/x"):
            self.assertFalse(ma.url_de_redirection(non), non)

    def test_sha256_normalise(self):
        h = "A" * 64
        self.assertEqual(ma.sha256_normalise("sha256:" + h), "a" * 64)
        self.assertEqual(ma.sha256_normalise(h), "a" * 64)
        for impropre in (None, "", "sha256:court", "z" * 64):
            self.assertEqual(ma.sha256_normalise(impropre), "")

    def test_nom_archive_sur(self):
        self.assertEqual(ma.nom_archive_sur("a-linux.tar.gz"), "a-linux.tar.gz")
        for nom in ("", "../a.zip", "d/a.zip", "a.exe", "a.zip\\", "x" * 201 + ".zip"):
            with self.subTest(nom=nom), self.assertRaises(ma.ErreurMiseAJour):
                ma.nom_archive_sur(nom)


class Asset(unittest.TestCase):
    def asset(self, **changements):
        donnees = b"archive"
        base = {"name": "a.zip", "browser_download_url": url_de("a.zip"), "size": len(donnees),
                "digest": "sha256:" + hashlib.sha256(donnees).hexdigest(), "state": "uploaded"}
        base.update(changements)
        return base

    def test_choisit_l_unique_fichier_finalise(self):
        choisi = ma.choisir_asset([self.asset(), {"name": "autre.zip"}], "a.zip", DEPOT)
        self.assertEqual(choisi["name"], "a.zip")
        self.assertEqual(choisi["size"], 7)
        self.assertTrue(choisi["digest"].startswith("sha256:"))

    def test_absent_ou_en_double(self):
        for assets in ([], [self.asset(), self.asset()]):
            with self.assertRaises(ma.ErreurMiseAJour) as c:
                ma.choisir_asset(assets, "a.zip", DEPOT)
            self.assertEqual(c.exception.categorie, "invalid_asset")

    def test_refuse_empreinte_taille_etat_et_url(self):
        for changement in ({"digest": None}, {"digest": "sha256:court"}, {"size": 0},
                           {"size": "x"}, {"state": "new"},
                           {"browser_download_url": "http://example.test/a.zip"},
                           {"browser_download_url": "https://github.com/autre/depot/releases/download/v1.0.0/a.zip"}):
            with self.subTest(changement=changement), self.assertRaises(ma.ErreurMiseAJour):
                ma.choisir_asset([self.asset(**changement)], "a.zip", DEPOT)

    def test_taille_plafonnee(self):
        with self.assertRaises(ma.ErreurMiseAJour):
            ma.choisir_asset([self.asset()], "a.zip", DEPOT, taille_max=3)


class Telechargement(Base):
    def lancer(self, contenu, *, destination=None, reponse=None, taille=None, sha=None, **options):
        destination = destination or self.racine / "a.zip"
        reponse = reponse or Reponse(contenu, url_de("a.zip"))
        ma.telecharger(url_de("a.zip"), destination,
                       len(contenu) if taille is None else taille,
                       sha or hashlib.sha256(contenu).hexdigest(),
                       depot=DEPOT, agent=AGENT, ouvrir=lambda *a, **k: reponse, **options)
        return destination

    def test_publie_seulement_apres_les_controles(self):
        destination = self.lancer(b"octets verifies")
        self.assertEqual(destination.read_bytes(), b"octets verifies")
        self.assertFalse((self.racine / "a.zip.part").exists())

    def test_empreinte_fausse_ou_archive_tronquee_effacees(self):
        for recu, taille_http in ((b"modifie!", None), (b"court", None)):
            with self.subTest(recu=recu):
                destination = self.racine / ("bad-%d.zip" % len(recu))
                with self.assertRaises(ma.ErreurMiseAJour):
                    self.lancer(b"attendu!", destination=destination,
                                reponse=Reponse(recu, url_de("a.zip"), taille_http))
                self.assertFalse(destination.exists())
                self.assertFalse(Path(str(destination) + ".part").exists())

    def test_redirection_etrangere_refusee(self):
        with self.assertRaises(ma.ErreurMiseAJour) as c:
            self.lancer(b"x", reponse=Reponse(b"x", "https://evil.example/a.zip"))
        self.assertEqual(c.exception.code, "archive_redirection")

    def test_taille_http_differente_refusee(self):
        with self.assertRaises(ma.ErreurMiseAJour) as c:
            self.lancer(b"abc", reponse=Reponse(b"abc", url_de("a.zip"), taille_http=99))
        self.assertEqual(c.exception.code, "archive_taille_http_inattendue")

    def test_flux_plus_long_que_la_taille_publiee(self):
        with self.assertRaises(ma.ErreurMiseAJour) as c:
            self.lancer(b"abcdef", taille=3, sha="0" * 64,
                        reponse=Reponse(b"abcdef", url_de("a.zip"), taille_http=None))
        self.assertEqual(c.exception.code, "archive_depasse_taille")

    def test_url_etrangere_refusee_avant_toute_requete(self):
        appels = []
        with self.assertRaises(ma.ErreurMiseAJour):
            ma.telecharger("https://github.com/autre/depot/releases/download/v1.0.0/a.zip",
                           self.racine / "a.zip", 1, "a" * 64, depot=DEPOT, agent=AGENT,
                           ouvrir=lambda *a, **k: appels.append(1))
        self.assertEqual(appels, [])

    def test_erreur_reseau_devient_telechargement_echoue(self):
        def echoue(*_a, **_k):
            raise OSError("hors ligne")
        with self.assertRaises(ma.ErreurMiseAJour) as c:
            ma.telecharger(url_de("a.zip"), self.racine / "a.zip", 1, "a" * 64,
                           depot=DEPOT, agent=AGENT, ouvrir=echoue)
        self.assertEqual(c.exception.categorie, "download_failed")

    def test_contexte_tls_transmis_et_progression_appelee(self):
        contexte = ssl.create_default_context()
        vu = {}

        def ouvrir(requete, **options):
            vu.update(options)
            return Reponse(b"contenu", url_de("a.zip"))

        suivi = []
        with mock.patch.object(ma.time, "time", side_effect=[1000.0, 1001.0, 1002.0, 1003.0]):
            ma.telecharger(url_de("a.zip"), self.racine / "a.zip", 7,
                           hashlib.sha256(b"contenu").hexdigest(), depot=DEPOT, agent=AGENT,
                           ouvrir=ouvrir, contexte=contexte,
                           progression=lambda recu, total: suivi.append((recu, total)))
        self.assertIs(vu["context"], contexte)
        self.assertEqual(suivi, [(7, 7)])

    def test_lire_empreinte(self):
        h = "b" * 64
        corps = f"{h} *a.zip\n".encode("ascii")
        url = url_de("a.zip.sha256")
        lu = ma.lire_empreinte(url, "a.zip", DEPOT, agent=AGENT,
                               ouvrir=lambda *a, **k: Reponse(corps, url))
        self.assertEqual(lu, h)
        for mauvais in (b"", b"zz\n", f"{h} autre.zip\n".encode(), f"{h}\n{h}\n".encode(),
                        b"\xff\xfe"):
            with self.subTest(corps=mauvais), self.assertRaises(ma.ErreurMiseAJour):
                ma.lire_empreinte(url, "a.zip", DEPOT, agent=AGENT,
                                  ouvrir=lambda *a, **k: Reponse(mauvais, url))
        with self.assertRaises(ma.ErreurMiseAJour):   # URL qui n'est pas celle de la release
            ma.lire_empreinte("https://github.com/autre/depot/releases/download/v1.0.0/a.zip.sha256",
                              "a.zip", DEPOT, agent=AGENT, ouvrir=lambda *a, **k: Reponse(corps, url))


def zip_de(chemin: Path, membres: dict) -> Path:
    with zipfile.ZipFile(chemin, "w", zipfile.ZIP_DEFLATED) as sortie:
        for nom, contenu in membres.items():
            sortie.writestr(nom, contenu)
    return chemin


def lien_zip(sortie, nom: str, cible: str) -> None:
    info = zipfile.ZipInfo(nom)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    sortie.writestr(info, cible)


class Extraction(Base):
    def tar_avec_lien(self, cible_lien: str, *, sous_le_lien: bool = False) -> Path:
        archive = self.racine / "bundle.tar.gz"
        with tarfile.open(archive, "w:gz") as sortie:
            for nom in ("bundle", "bundle/_internal", "bundle/_internal/pillow.libs"):
                dossier = tarfile.TarInfo(nom)
                dossier.type = tarfile.DIRTYPE
                sortie.addfile(dossier)
            contenu = b"bibliotheque"
            fichier = tarfile.TarInfo("bundle/_internal/pillow.libs/libwebp.so.7")
            fichier.size = len(contenu)
            sortie.addfile(fichier, io.BytesIO(contenu))
            lien = tarfile.TarInfo("bundle/_internal/libwebp.so.7")
            lien.type = tarfile.SYMTYPE
            lien.linkname = cible_lien
            sortie.addfile(lien)
            if sous_le_lien:
                cache = tarfile.TarInfo("bundle/_internal/libwebp.so.7/cache")
                sortie.addfile(cache, io.BytesIO(b""))
        return archive

    def test_zip_nominal(self):
        archive = zip_de(self.racine / "b.zip", {"bundle/programme": b"programme",
                                                 "bundle/_internal/lib.dat": b"lib"})
        dossier = ma.extraire(archive, self.racine / "out")
        self.assertEqual(dossier.name, "bundle")
        self.assertEqual((dossier / "programme").read_bytes(), b"programme")
        self.assertEqual((dossier / "_internal" / "lib.dat").read_bytes(), b"lib")

    def test_racine_exigee(self):
        archive = zip_de(self.racine / "b.zip", {"bundle/f": b"x"})
        self.assertEqual(ma.extraire(archive, self.racine / "ok", racine="bundle").name, "bundle")
        with self.assertRaises(ma.ErreurMiseAJour):
            ma.extraire(archive, self.racine / "ko", racine="autre")

    def test_un_seul_dossier_de_premier_niveau(self):
        archive = zip_de(self.racine / "b.zip", {"a/f": b"x", "b/f": b"y"})
        with self.assertRaises(ma.ErreurMiseAJour) as c:
            ma.extraire(archive, self.racine / "out")
        self.assertEqual(c.exception.code, "archive_bundle_unique")

    def test_zip_refuse_traversee_absolu_antislash_et_lecteur(self):
        for index, nom in enumerate(("../echappe", "/absolu", "bundle\\..\\echappe",
                                     "C:/windows/x", "bundle/../../echappe")):
            with self.subTest(nom=nom):
                archive = zip_de(self.racine / f"t{index}.zip", {nom: b"attaque"})
                with self.assertRaises(ma.ErreurMiseAJour):
                    ma.extraire(archive, self.racine / f"out{index}")
                self.assertFalse((self.racine / "echappe").exists())

    def test_refuse_noms_non_portables(self):
        for index, nom in enumerate(("bundle/CON", "bundle/nul.txt", "bundle/fin.", "bundle/a b ",
                                     "bundle/a:b", "bundle/" + "x" * 256)):
            with self.subTest(nom=nom):
                archive = zip_de(self.racine / f"n{index}.zip", {nom: b"x"})
                with self.assertRaises(ma.ErreurMiseAJour):
                    ma.extraire(archive, self.racine / f"o{index}")

    def test_refuse_collision_de_casse_et_doublon(self):
        for index, noms in enumerate((("bundle/Lisez", "bundle/lisez"),)):
            archive = self.racine / f"c{index}.zip"
            with zipfile.ZipFile(archive, "w") as sortie:
                for nom in noms:
                    sortie.writestr(nom, b"x")
            with self.assertRaises(ma.ErreurMiseAJour) as c:
                ma.extraire(archive, self.racine / f"oc{index}")
            self.assertEqual(c.exception.code, "archive_collision_chemins")
        doublon = self.racine / "d.zip"
        with warnings.catch_warnings(), zipfile.ZipFile(doublon, "w") as sortie:
            warnings.simplefilter("ignore")
            sortie.writestr("bundle/f", b"1")
            sortie.writestr("bundle/f", b"2")
        with self.assertRaises(ma.ErreurMiseAJour) as c:
            ma.extraire(doublon, self.racine / "od")
        self.assertEqual(c.exception.code, "archive_membre_duplique")

    def test_zip_chiffre_refuse(self):
        archive = zip_de(self.racine / "e.zip", {"bundle/f": b"x"})
        brut = bytearray(archive.read_bytes())
        # Bit 0 des drapeaux : en-tête local (offset 6) et répertoire central (offset 8).
        for signature, decalage in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            i = brut.index(signature)
            brut[i + decalage] |= 1
        archive.write_bytes(bytes(brut))
        with self.assertRaises(ma.ErreurMiseAJour) as c:
            ma.extraire(archive, self.racine / "oe")
        self.assertEqual(c.exception.code, "archive_membre_zip_chiffre")

    def test_zip_refuse_lien_sortant(self):
        archive = self.racine / "l.zip"
        with zipfile.ZipFile(archive, "w") as sortie:
            lien_zip(sortie, "bundle/lien", "../../echappe")
        with self.assertRaises(ma.ErreurMiseAJour):
            ma.extraire(archive, self.racine / "ol")

    def test_tar_refuse_lien_sortant_et_n_ecrit_pas_dehors(self):
        archive = self.racine / "l.tar.gz"
        with tarfile.open(archive, "w:gz") as sortie:
            dossier = tarfile.TarInfo("bundle")
            dossier.type = tarfile.DIRTYPE
            sortie.addfile(dossier)
            lien = tarfile.TarInfo("bundle/lien")
            lien.type = tarfile.SYMTYPE
            lien.linkname = "../../echappe"
            sortie.addfile(lien)
        with self.assertRaises(ma.ErreurMiseAJour):
            ma.extraire(archive, self.racine / "ol")
        self.assertFalse((self.racine / "echappe").exists())

    def test_tar_refuse_traversee_liens_physiques_et_peripheriques(self):
        def traversee(info):
            info.name = "../echappe"

        def lien_physique(info):
            info.type, info.linkname = tarfile.LNKTYPE, "bundle/f"

        def fifo(info):
            info.type = tarfile.FIFOTYPE

        for index, modifier in enumerate((traversee, lien_physique, fifo)):
            archive = self.racine / f"x{index}.tar.gz"
            with tarfile.open(archive, "w:gz") as sortie:
                info = tarfile.TarInfo("bundle/f")
                modifier(info)
                if info.type == tarfile.REGTYPE:
                    info.size = 1
                    sortie.addfile(info, io.BytesIO(b"x"))
                else:
                    sortie.addfile(info)
            with self.subTest(cas=modifier.__name__), self.assertRaises(ma.ErreurMiseAJour):
                ma.extraire(archive, self.racine / f"ox{index}")
            self.assertFalse((self.racine / "echappe").exists())

    @unittest.skipIf(os.name == "nt", "créer un lien exige un privilège sous Windows")
    def test_tar_accepte_les_liens_internes_du_bundle(self):
        dossier = ma.extraire(self.tar_avec_lien("pillow.libs/libwebp.so.7"), self.racine / "o")
        lien = dossier / "_internal" / "libwebp.so.7"
        self.assertTrue(lien.is_symlink())
        self.assertEqual(os.readlink(lien), "pillow.libs/libwebp.so.7")
        self.assertEqual(lien.read_bytes(), b"bibliotheque")

    @unittest.skipIf(os.name == "nt", "créer un lien exige un privilège sous Windows")
    def test_zip_accepte_les_liens_internes_du_bundle(self):
        archive = self.racine / "b.zip"
        with zipfile.ZipFile(archive, "w") as sortie:
            sortie.writestr("bundle/Python.framework/Versions/3.12/Python", b"python")
            lien_zip(sortie, "bundle/Python.framework/Versions/Current", "3.12")
        dossier = ma.extraire(archive, self.racine / "o")
        courant = dossier / "Python.framework" / "Versions" / "Current"
        self.assertTrue(courant.is_symlink())
        self.assertEqual((courant / "Python").read_bytes(), b"python")

    @unittest.skipIf(os.name == "nt", "créer un lien exige un privilège sous Windows")
    def test_lien_vers_une_autre_racine_refuse_quand_la_racine_est_connue(self):
        archive = self.racine / "b.tar.gz"
        with tarfile.open(archive, "w:gz") as sortie:
            for nom in ("bundle", "autre"):
                d = tarfile.TarInfo(nom)
                d.type = tarfile.DIRTYPE
                sortie.addfile(d)
            lien = tarfile.TarInfo("bundle/vers_autre")
            lien.type = tarfile.SYMTYPE
            lien.linkname = "../autre"
            sortie.addfile(lien)
        with self.assertRaises(ma.ErreurMiseAJour):
            ma.extraire(archive, self.racine / "o", racine="bundle")

    def test_lien_absolu_refuse_et_lien_ne_sert_pas_de_dossier(self):
        with self.assertRaises(ma.ErreurMiseAJour):
            ma.extraire(self.tar_avec_lien("/etc/passwd"), self.racine / "o1")
        with self.assertRaises(ma.ErreurMiseAJour):
            ma.extraire(self.tar_avec_lien("pillow.libs", sous_le_lien=True), self.racine / "o2")

    @unittest.skipUnless(os.name == "nt", "politique propre à Windows")
    def test_liens_refuses_sous_windows(self):
        with self.assertRaises(ma.ErreurMiseAJour):
            ma.extraire(self.tar_avec_lien("pillow.libs/libwebp.so.7"), self.racine / "o")

    def test_taille_decompressee_bornee_avant_ecriture(self):
        archive = zip_de(self.racine / "bombe.zip", {"bundle/gros": b"1234"})
        with self.assertRaises(ma.ErreurMiseAJour) as c:
            ma.extraire(archive, self.racine / "o", taille_max=3)
        self.assertEqual(c.exception.code, "archive_zip_trop_volumineux")
        self.assertFalse((self.racine / "o" / "bundle" / "gros").exists())

    def test_nombre_de_membres_borne(self):
        archive = zip_de(self.racine / "m.zip", {f"bundle/f{i}": b"x" for i in range(5)})
        with self.assertRaises(ma.ErreurMiseAJour) as c:
            ma.extraire(archive, self.racine / "o", membres_max=3)
        self.assertEqual(c.exception.code, "archive_trop_de_membres")

    def test_format_inconnu_et_archive_corrompue(self):
        autre = self.racine / "a.rar"
        autre.write_bytes(b"x")
        with self.assertRaises(ma.ErreurMiseAJour) as c:
            ma.extraire(autre, self.racine / "o1")
        self.assertEqual(c.exception.code, "archive_format_inconnu")
        cassee = self.racine / "c.zip"
        cassee.write_bytes(b"pas un zip")
        with self.assertRaises(ma.ErreurMiseAJour) as c:
            ma.extraire(cassee, self.racine / "o2")
        self.assertEqual(c.exception.code, "archive_illisible")

    @unittest.skipIf(os.name == "nt", "modes POSIX")
    def test_seul_le_bit_executable_est_conserve(self):
        archive = self.racine / "b.tar.gz"
        with tarfile.open(archive, "w:gz") as sortie:
            d = tarfile.TarInfo("bundle")
            d.type = tarfile.DIRTYPE
            sortie.addfile(d)
            for nom, mode in (("bundle/prog", 0o4755), ("bundle/donnee", 0o600)):
                f = tarfile.TarInfo(nom)
                f.size, f.mode = 1, mode
                sortie.addfile(f, io.BytesIO(b"x"))
        dossier = ma.extraire(archive, self.racine / "o")
        self.assertEqual(stat.S_IMODE((dossier / "prog").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((dossier / "donnee").stat().st_mode), 0o644)


class Erreurs(unittest.TestCase):
    def test_message_et_categorie(self):
        erreur = ma.ErreurMiseAJour("archive_empreinte_incorrecte", obtenue="a", sha256="b")
        self.assertIn("a", erreur.message("fr"))
        self.assertNotEqual(erreur.message("fr"), erreur.message("en"))
        self.assertEqual(erreur.categorie, "integrity_failed")
        self.assertIsInstance(erreur, OSError)

    def test_les_deux_langues_ont_les_memes_cles_et_les_memes_champs(self):
        import string
        self.assertEqual(set(ma.LIBELLES["fr"]), set(ma.LIBELLES["en"]))
        for cle in ma.LIBELLES["fr"]:
            champs = {lg: {f for _, f, _, _ in string.Formatter().parse(ma.LIBELLES[lg][cle]) if f}
                      for lg in ("fr", "en")}
            self.assertEqual(champs["fr"], champs["en"], cle)

    def test_toute_categorie_connue(self):
        self.assertTrue(set(ma.CATEGORIES) <= set(ma.LIBELLES["fr"]))


if __name__ == "__main__":
    unittest.main()
