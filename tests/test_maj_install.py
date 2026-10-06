"""Tests de maj_install : disposition de l'installation, préparation d'une
mise à jour, et l'assistant pour de vrai.

Les deux essais de bout en bout lancent l'assistant réel du système
(PowerShell sous Windows, sh ailleurs) sur une fausse installation faite dans un
dossier temporaire : une mise à jour qui réussit (dossiers échangés, données
conservées, nouvelle version relancée) et une qui échoue (la nouvelle version
meurt aussitôt : l'ancienne doit revenir et être relancée). Ils durent une
dizaine de secondes chacun.

    python -m unittest discover -s tests
"""

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from nico579_commons import maj_archive, maj_install as mi  # noqa: E402

DEPOT = "nico579/exemple"
APP = mi.Application("exemple", donnees_preservees=("config.json", "state"),
                     noms_toleres=(".exemple.lock",))
WINDOWS = os.name == "nt"


class Reponse(io.BytesIO):
    def __init__(self, contenu: bytes, url: str):
        super().__init__(contenu)
        self.headers = {"Content-Length": str(len(contenu))}
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        self.close()


def supprimer(chemin: Path, delai_s: float = 30) -> None:
    """rmtree qui patiente : sous Windows, un dossier est verrouillé tant qu'un
    processus lancé dedans tourne encore."""
    fin = time.monotonic() + delai_s
    while chemin.exists():
        try:
            shutil.rmtree(chemin)
        except OSError:
            if time.monotonic() > fin:
                shutil.rmtree(chemin, ignore_errors=True)
                return
            time.sleep(0.5)


class Base(unittest.TestCase):
    def setUp(self):
        self.racine = Path(tempfile.mkdtemp(prefix="maj-install-")).resolve()
        self.addCleanup(supprimer, self.racine)

    def installation(self, systeme="Linux", nom="exemple", contenu=None):
        dossier = self.racine / nom
        dossier.mkdir()
        exe = "exemple.exe" if systeme == "Windows" else "exemple"
        (dossier / exe).write_bytes(b"x")
        (dossier / "_internal").mkdir()
        for autre in contenu or ():
            (dossier / autre).write_text("x")
        return dossier, dossier / exe

    def disposition(self, dossier, exe, systeme="Linux"):
        return mi.disposition(
            APP, asset_name="exemple-linux-x86_64.tar.gz", archive_kind="tar",
            racine_attendue="exemple", executable=exe, systeme=systeme, machine="x86_64",
            fige=True)


class Disposition(Base):
    def test_bundle_dedie_accepte(self):
        dossier, exe = self.installation(contenu=("config.json", ".exemple.lock"))
        disp = self.disposition(dossier, exe)
        self.assertEqual(disp.install_root, dossier)
        self.assertEqual(disp.executable_relative, Path("exemple"))
        self.assertEqual(disp.expected_root, "exemple")

    def test_windows_nomme_l_executable_avec_exe(self):
        dossier, exe = self.installation("Windows")
        disp = self.disposition(dossier, exe, "Windows")
        self.assertEqual(disp.executable_relative, Path("exemple.exe"))

    def test_refus_depuis_les_sources(self):
        with self.assertRaises(maj_archive.ErreurMiseAJour) as c:
            mi.disposition(APP, asset_name="a.zip", archive_kind="zip", racine_attendue="a",
                           fige=False)
        self.assertEqual(c.exception.code, "source_mode")

    def test_refus_d_un_dossier_general(self):
        dossier, exe = self.installation(contenu=("photo.jpg", "notes.txt"))
        with self.assertRaises(maj_archive.ErreurMiseAJour) as c:
            self.disposition(dossier, exe)
        self.assertEqual(c.exception.code, "unsafe_install")
        self.assertIn("photo.jpg", c.exception.valeurs["detail"])

    def test_refus_sans_internal(self):
        dossier, exe = self.installation()
        (dossier / "_internal").rmdir()
        with self.assertRaises(maj_archive.ErreurMiseAJour):
            self.disposition(dossier, exe)

    def test_refus_de_la_racine_du_disque_et_du_dossier_personnel(self):
        for dossier in (Path(self.racine.anchor), Path.home()):
            with self.subTest(dossier=str(dossier)), self.assertRaises(maj_archive.ErreurMiseAJour):
                mi.disposition(APP, asset_name="a.zip", archive_kind="zip", racine_attendue="a",
                               executable=dossier / "exemple", systeme="Linux", fige=True)

    def test_macos_cherche_le_bundle_app(self):
        app = self.racine / "Exemple.app"
        exe = app / "Contents" / "MacOS" / "exemple"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"x")
        disp = mi.disposition(APP, asset_name="exemple-macos-arm64.zip", archive_kind="zip",
                              racine_attendue="Exemple.app", executable=exe, systeme="Darwin",
                              machine="arm64", fige=True)
        self.assertEqual(disp.install_root, app.resolve())
        self.assertEqual(disp.data_relative, Path("Contents") / "MacOS")
        with self.assertRaises(maj_archive.ErreurMiseAJour):     # pas dans un .app
            mi.disposition(APP, asset_name="a", archive_kind="zip", racine_attendue="a",
                           executable=self.racine / "exemple", systeme="Darwin", fige=True)

    def test_possible(self):
        dossier, exe = self.installation()
        self.assertEqual(mi.possible(lambda: self.disposition(dossier, exe)), (True, ""))
        self.assertEqual(mi.possible(lambda: mi.disposition(
            APP, asset_name="a", archive_kind="zip", racine_attendue="a", fige=False)),
            (False, "source_mode"))


def zip_bundle(version: str, *, avec_donnee=False, sans_internal=False) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as z:
        programme = zipfile.ZipInfo("exemple/exemple")
        programme.create_system = 3
        programme.external_attr = (stat.S_IFREG | 0o755) << 16   # exécutable sous Linux et macOS
        z.writestr(programme, f"{version}".encode())
        if not sans_internal:
            z.writestr("exemple/_internal/lib.dat", b"lib")
        if avec_donnee:
            z.writestr("exemple/config.json", b"{}")
    return tampon.getvalue()


class Preparation(Base):
    def info(self, donnees: bytes, version="9.0.0", nom="exemple-linux-x86_64.zip"):
        return {"version": version, "assets": [{
            "name": nom, "state": "uploaded", "size": len(donnees),
            "digest": "sha256:" + hashlib.sha256(donnees).hexdigest(),
            "browser_download_url": f"https://github.com/{DEPOT}/releases/download/v{version}/{nom}"}]}

    def preparer(self, donnees, **options):
        dossier, exe = self.installation()
        disp = mi.disposition(APP, asset_name="exemple-linux-x86_64.zip", archive_kind="zip",
                              racine_attendue="exemple", executable=exe, systeme="Linux",
                              fige=True)
        info = self.info(donnees)
        url = info["assets"][0]["browser_download_url"]
        self.dossier = dossier
        return mi.preparer(APP, info, DEPOT, disp, auto_test=False,
                           ouvrir=lambda *a, **k: Reponse(donnees, url), **options)

    def test_prepare_sans_toucher_a_l_installation(self):
        prep = self.preparer(zip_bundle("9.0.0"))
        try:
            self.assertEqual((self.dossier / "exemple").read_bytes(), b"x")
            self.assertEqual((prep.payload_root / "exemple").read_bytes(), b"9.0.0")
            self.assertEqual(prep.version, "9.0.0")
            self.assertTrue(prep.staging_root.name.startswith(".exemple.update-"))
            self.assertFalse(prep.backup_root.exists() or prep.failed_root.exists())
        finally:
            mi.nettoyer(prep)
        self.assertFalse(prep.staging_root.exists())

    def test_donnee_modifiable_dans_le_bundle_refusee_et_staging_nettoye(self):
        with self.assertRaises(maj_archive.ErreurMiseAJour) as c:
            self.preparer(zip_bundle("9.0.0", avec_donnee=True))
        self.assertEqual(c.exception.code, "invalid_payload")
        self.assertEqual([n for n in os.listdir(self.racine) if n.startswith(".exemple.update-")], [])

    def test_internal_absent_refuse(self):
        with self.assertRaises(maj_archive.ErreurMiseAJour) as c:
            self.preparer(zip_bundle("9.0.0", sans_internal=True))
        self.assertEqual(c.exception.code, "invalid_payload")

    def test_archive_corrompue_ou_empreinte_fausse(self):
        donnees = zip_bundle("9.0.0")
        dossier, exe = self.installation()
        disp = self.disposition(dossier, exe)
        disp = mi.Disposition(**{**disp.__dict__, "asset_name": "exemple-linux-x86_64.zip",
                                 "archive_kind": "zip"})
        info = self.info(donnees)
        info["assets"][0]["digest"] = "sha256:" + "0" * 64
        url = info["assets"][0]["browser_download_url"]
        with self.assertRaises(maj_archive.ErreurMiseAJour) as c:
            mi.preparer(APP, info, DEPOT, disp, auto_test=False,
                        ouvrir=lambda *a, **k: Reponse(donnees, url))
        self.assertEqual(c.exception.categorie, "integrity_failed")
        self.assertEqual([n for n in os.listdir(self.racine) if n.startswith(".exemple.update-")], [])

    def test_fichier_de_release_absent(self):
        dossier, exe = self.installation()
        disp = self.disposition(dossier, exe)
        with self.assertRaises(maj_archive.ErreurMiseAJour) as c:
            mi.preparer(APP, {"version": "9", "assets": []}, DEPOT, disp, auto_test=False)
        self.assertEqual(c.exception.code, "asset_absent")

    def test_version_absente(self):
        dossier, exe = self.installation()
        disp = mi.Disposition(**{**self.disposition(dossier, exe).__dict__,
                                 "asset_name": "exemple-linux-x86_64.zip", "archive_kind": "zip"})
        info = self.info(zip_bundle("9"))
        info["version"] = ""
        with self.assertRaises(maj_archive.ErreurMiseAJour) as c:
            mi.preparer(APP, info, DEPOT, disp, auto_test=False)
        self.assertEqual(c.exception.code, "version_absente")

    def test_valider_exige_un_assistant_pret(self):
        prep = self.preparer(zip_bundle("9.0.0"))
        try:
            with self.assertRaises(maj_archive.ErreurMiseAJour) as c:
                mi.valider(prep, ecrire=lambda *_: None)
            self.assertEqual(c.exception.code, "helper_failed")
        finally:
            mi.nettoyer(prep)

    def test_chemins_de_transaction_controles(self):
        prep = self.preparer(zip_bundle("9.0.0"))
        try:
            ailleurs = mi.Preparation(**{**prep.__dict__, "staging_root": self.racine / "ailleurs"})
            with self.assertRaises(maj_archive.ErreurMiseAJour):
                mi.lancer(APP, ailleurs, ecrire=lambda *_: None)
            deja = mi.Preparation(**{**prep.__dict__, "backup_root": self.dossier})
            with self.assertRaises(maj_archive.ErreurMiseAJour):
                mi.lancer(APP, deja, ecrire=lambda *_: None)
        finally:
            mi.nettoyer(prep)

    def test_annuler_pose_le_marqueur(self):
        prep = self.preparer(zip_bundle("9.0.0"))
        try:
            mi.annuler(prep)
            self.assertTrue((prep.staging_root / "helper.abort").exists())
        finally:
            mi.nettoyer(prep)


# ----------------------------------------------------- l'assistant, pour de vrai

SCRIPT_PARENT = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from nico579_commons import maj_install as mi
d = json.loads(sys.argv[2])
disp = mi.Disposition(system=d["system"], machine="x86_64", asset_name="a", archive_kind="zip",
                      expected_root="exemple", install_root=Path(d["install"]),
                      data_relative=Path("."), executable_relative=Path(d["exe"]))
prep = mi.Preparation(version="9.9.9", token="t0k3n", disposition=disp,
                      staging_root=Path(d["staging"]), payload_root=Path(d["payload"]),
                      backup_root=Path(d["backup"]), failed_root=Path(d["failed"]))
app = mi.Application("exemple", donnees_preservees=("config.json", "state"),
                     arguments_relance=("--ouvrir",), fenetre="Minimized")
mi.lancer(app, prep)
mi.valider(prep)
'''


def faux_programme(dossier: Path, version: str, *, meurt_aussitot: bool) -> str:
    """Écrit un faux exécutable (script) qui note son démarrage, et rend son
    nom relatif. Il tourne ~10 s s'il ne « meurt aussitôt »."""
    if WINDOWS:
        nom = "app.cmd"
        corps = f"@echo off\r\necho {version} %* > \"%~dp0demarre-{version}.txt\"\r\n"
        if not meurt_aussitot:
            corps += "ping -n 10 127.0.0.1 >nul\r\n"
        (dossier / nom).write_text(corps)
    else:
        nom = "app"
        corps = f"#!/bin/sh\necho \"{version} $*\" > \"$(dirname \"$0\")/demarre-{version}.txt\"\n"
        if not meurt_aussitot:
            corps += "sleep 10\n"
        (dossier / nom).write_text(corps)
        (dossier / nom).chmod((dossier / nom).stat().st_mode | stat.S_IXUSR)
    return nom


class AssistantReel(Base):
    def lancer_scenario(self, *, nouvelle_meurt: bool):
        install = self.racine / "exemple"
        install.mkdir()
        exe = faux_programme(install, "ancienne", meurt_aussitot=False)
        (install / "_internal").mkdir()
        (install / "config.json").write_text('{"reglage": 1}')
        (install / "state").mkdir()
        (install / "state" / "etat.json").write_text("[1]")
        staging = self.racine / ".exemple.update-abc"
        payload = staging / "extracted" / "exemple"
        payload.mkdir(parents=True)
        faux_programme(payload, "nouvelle", meurt_aussitot=nouvelle_meurt)
        donnees = {"system": "Windows" if WINDOWS else "Linux", "install": str(install),
                   "exe": exe, "staging": str(staging), "payload": str(payload),
                   "backup": str(self.racine / ".exemple.backup-t0k3n"),
                   "failed": str(self.racine / ".exemple.failed-t0k3n")}
        parent = subprocess.run([sys.executable, "-c", SCRIPT_PARENT, str(SRC), json.dumps(donnees)],
                                capture_output=True, text=True, timeout=90)
        self.assertEqual(parent.returncode, 0, parent.stdout + parent.stderr)
        return install, staging

    def attendre(self, condition, delai_s=90):
        fin = time.monotonic() + delai_s
        while time.monotonic() < fin:
            if condition():
                return True
            time.sleep(0.5)
        return False

    def test_mise_a_jour_reussie(self):
        install, staging = self.lancer_scenario(nouvelle_meurt=False)
        # Le faux programme de la nouvelle version a le même nom que l'ancien :
        # le contenu de « exemple/ » est donc celui du bundle de la nouvelle.
        self.assertTrue(self.attendre(lambda: (install / "demarre-nouvelle.txt").exists()),
                        "la nouvelle version n'a pas été relancée")
        self.assertTrue(self.attendre(lambda: not staging.exists()),
                        "l'assistant n'a pas nettoyé son dossier")
        self.assertFalse((self.racine / ".exemple.backup-t0k3n").exists())
        # Les données à conserver ont suivi, copiées de l'ancienne installation.
        self.assertEqual((install / "config.json").read_text(), '{"reglage": 1}')
        self.assertEqual((install / "state" / "etat.json").read_text(), "[1]")
        # Relancée avec ses arguments.
        self.assertIn("--ouvrir", (install / "demarre-nouvelle.txt").read_text())
        self.assertFalse((install / "demarre-ancienne.txt").exists())

    def test_nouvelle_version_qui_meurt_remet_l_ancienne(self):
        install, staging = self.lancer_scenario(nouvelle_meurt=True)
        self.assertTrue(self.attendre(lambda: (install / "demarre-ancienne.txt").exists()),
                        "l'ancienne version n'a pas été relancée après l'échec")
        self.assertTrue(self.attendre(lambda: any(
            p.name.startswith(".exemple.failed-") for p in self.racine.iterdir())))
        # L'ancienne installation est intacte, avec ses données.
        self.assertEqual((install / "config.json").read_text(), '{"reglage": 1}')
        self.assertTrue((install / "_internal").is_dir())
        journal = (staging / "update-helper.log")
        self.assertTrue(self.attendre(journal.exists))
        self.assertIn("restart failed" if WINDOWS else "new version did not stay running",
                      journal.read_text(encoding="utf-8", errors="replace"))


class FauxVerificateur:
    def __init__(self, info):
        self.info = info
        self.verifications = 0

    def verifier(self):
        self.verifications += 1
        return True

    def disponible(self):
        return self.info


class InstallateurTests(Base):
    """L'orchestration, sans réseau ni assistant : preparer, lancer et valider
    sont remplacés, on vérifie l'enchaînement, l'état et le nettoyage."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        self.mock = mock
        self.dossier, self.exe = self.installation()
        self.staging = self.racine / ".exemple.update-x"
        self.staging.mkdir()
        self.prep = mi.Preparation(
            version="9.0.0", token="t", disposition=self.disposition(self.dossier, self.exe),
            staging_root=self.staging, payload_root=self.staging / "p",
            backup_root=self.racine / ".exemple.backup-t", failed_root=self.racine / ".exemple.failed-t")
        self.appels = []
        self.messages = []
        self.quitte = []

    def installateur(self, info="defaut", construire=None):
        if info == "defaut":
            info = {"version": "9.0.0", "assets": []}
        return mi.Installateur(
            APP, DEPOT, FauxVerificateur(info),
            construire or (lambda: self.disposition(self.dossier, self.exe)),
            quitter=lambda: self.quitte.append(True), ecrire=self.messages.append)

    def patches(self, **remplacements):
        defaut = {
            "preparer": lambda *a, **k: (self.appels.append("preparer"), self.prep)[1],
            "lancer": lambda *a, **k: self.appels.append("lancer"),
            "valider": lambda *a, **k: self.appels.append("valider"),
            "annuler": lambda *a, **k: self.appels.append("annuler"),
            "nettoyer": lambda *a, **k: self.appels.append("nettoyer"),
        }
        defaut.update(remplacements)
        pile = [self.mock.patch.object(mi, nom, fonction) for nom, fonction in defaut.items()]
        for patch in pile:
            patch.start()
            self.addCleanup(patch.stop)

    def lancer_et_attendre(self, installateur):
        self.assertTrue(installateur.demarrer())
        installateur.attendre(10)
        return installateur.etat()

    def test_enchainement_complet_puis_arret(self):
        self.patches()
        installateur = self.installateur()
        etat = self.lancer_et_attendre(installateur)
        self.assertEqual(self.appels, ["preparer", "lancer", "valider"])
        self.assertEqual(self.quitte, [True])
        self.assertEqual(etat["etat"], "redemarrage")
        self.assertEqual(etat["version"], "9.0.0")
        self.assertEqual(installateur.verificateur.verifications, 1)

    def test_deja_en_cours_refuse_un_second_demarrage(self):
        import threading
        porte = threading.Event()

        def bloque(*a, **k):
            porte.wait(5)
            return self.prep

        self.patches(preparer=bloque)
        installateur = self.installateur()
        self.assertTrue(installateur.demarrer())
        self.assertFalse(installateur.demarrer())
        self.assertEqual(installateur.etat()["etat"], "telechargement")
        porte.set()
        installateur.attendre(10)
        self.assertEqual(installateur.etat()["etat"], "redemarrage")

    def test_la_progression_est_visible_dans_l_etat(self):
        def prepare(app, info, depot, disp, **options):
            options["progression"](5, 10)
            self.vu = installateur.etat()
            return self.prep

        self.patches(preparer=prepare)
        installateur = self.installateur()
        self.lancer_et_attendre(installateur)
        self.assertEqual((self.vu["etat"], self.vu["recu"], self.vu["total"]),
                         ("telechargement", 5, 10))

    def test_rien_a_installer(self):
        self.patches()
        etat = self.lancer_et_attendre(self.installateur(info=None))
        self.assertEqual(etat["etat"], "erreur")
        self.assertEqual(etat["erreur"]["code"], "asset_absent")
        self.assertEqual(self.appels, [])
        self.assertEqual(self.quitte, [])

    def test_installation_non_remplacable(self):
        self.patches()

        def refuse():
            raise maj_archive.ErreurMiseAJour("source_mode")

        etat = self.lancer_et_attendre(self.installateur(construire=refuse))
        self.assertEqual((etat["etat"], etat["erreur"]["code"]), ("erreur", "source_mode"))
        self.assertIn("sources", etat["erreur"]["message"]["fr"])
        self.assertIn("sources", etat["erreur"]["message"]["en"])

    def test_echec_de_preparation_ne_quitte_pas(self):
        def echoue(*a, **k):
            raise maj_archive.ErreurMiseAJour("archive_empreinte_incorrecte", obtenue="a", sha256="b")

        self.patches(preparer=echoue)
        etat = self.lancer_et_attendre(self.installateur())
        self.assertEqual(etat["erreur"]["categorie"], "integrity_failed")
        self.assertEqual(self.quitte, [])
        self.assertEqual(self.appels, [])        # rien n'avait été préparé : rien à nettoyer

    def test_echec_de_l_assistant_nettoie_et_ne_quitte_pas(self):
        def echoue(*a, **k):
            raise maj_archive.ErreurMiseAJour("helper_failed", detail="pas prêt")

        self.patches(lancer=echoue)
        etat = self.lancer_et_attendre(self.installateur())
        self.assertEqual(etat["erreur"]["code"], "helper_failed")
        self.assertEqual(self.appels, ["preparer", "annuler", "nettoyer"])
        self.assertEqual(self.quitte, [])

    def test_echec_du_feu_vert_annule(self):
        def echoue(*a, **k):
            raise maj_archive.ErreurMiseAJour("helper_failed", detail="pas d'accusé")

        self.patches(valider=echoue)
        etat = self.lancer_et_attendre(self.installateur())
        self.assertEqual(etat["etat"], "erreur")
        self.assertEqual(self.appels, ["preparer", "lancer", "annuler", "nettoyer"])
        self.assertEqual(self.quitte, [])

    def test_exception_inattendue_devient_prepare_failed(self):
        def casse(*a, **k):
            raise RuntimeError("boum")

        self.patches(preparer=casse)
        etat = self.lancer_et_attendre(self.installateur())
        self.assertEqual(etat["erreur"]["code"], "prepare_failed")
        self.assertEqual(etat["erreur"]["message"]["fr"], "boum")

    def test_on_peut_reessayer_apres_une_erreur(self):
        self.patches()
        installateur = self.installateur(info=None)
        self.assertEqual(self.lancer_et_attendre(installateur)["etat"], "erreur")
        installateur.verificateur.info = {"version": "9.0.0", "assets": []}
        etat = self.lancer_et_attendre(installateur)
        self.assertEqual(etat["etat"], "redemarrage")
        self.assertIsNone(etat["erreur"])

    def test_possible(self):
        self.patches()
        self.assertEqual(self.installateur().possible(), (True, ""))
        self.assertEqual(self.installateur(construire=lambda: mi.disposition(
            APP, asset_name="a", archive_kind="zip", racine_attendue="a", fige=False)).possible(),
            (False, "source_mode"))


if __name__ == "__main__":
    unittest.main()
