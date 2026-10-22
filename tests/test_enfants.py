"""Tests du module enfants, sur de vrais processus. Le cas qui compte : un
parent lance un enfant lié, on tue le parent sans lui laisser le temps de
rien ranger (TerminateProcess, SIGKILL), et l'enfant doit mourir aussi. Seule
l'exécution le prouve, sur chacun des systèmes de la CI.

    python -m unittest discover -s tests
"""

import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from nico579_commons import enfants  # noqa: E402

WINDOWS = sys.platform == "win32"
DORMEUR = [sys.executable, "-c", "import time; time.sleep(120)"]
# Un dormeur qui donne son propre PID : sous Windows, quand sys.executable
# est le python.exe d'un venv, ce n'est pas celui que voit Popen, mais celui
# du vrai interpréteur, que le lanceur du venv démarre aussitôt.
BAVARD = [sys.executable, "-c", "import os, time; print(os.getpid(), flush=True); time.sleep(120)"]
# Un enfant qui lance à son tour un dormeur, de la façon la plus ordinaire,
# et donne son PID.
LANCEUR = ("import subprocess, sys, time\n"
           "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
           "print(p.pid, flush=True)\n"
           "time.sleep(120)\n")
PRELUDE = ("import subprocess, sys, time\n"
           "sys.path.insert(0, {src!r})\n"
           "from nico579_commons import enfants\n"
           "DORMEUR = {dormeur!r}\n"
           "BAVARD = {bavard!r}\n"
           "LANCEUR = {lanceur!r}\n")


def vivant(pid):
    """Le processus pid tourne-t-il encore ? Un zombie (mort, que son
    nouveau parent n'a pas encore ramassé) compte pour mort."""
    if WINDOWS:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        poignee = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not poignee:
            return False
        try:
            return kernel32.WaitForSingleObject(poignee, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(poignee)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    etat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True,
                          text=True).stdout.strip()
    return bool(etat) and not etat.startswith("Z")


def attendre(condition, delai=15):
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(0.1)
    return condition()


def tuer(pid):
    """Filet des essais : rien ne reste derrière eux, même si le lien a
    échoué."""
    if vivant(pid):
        try:
            os.kill(pid, signal.SIGTERM if WINDOWS else signal.SIGKILL)
        except OSError:
            pass


class MortDuParent(unittest.TestCase):
    def parent(self, corps):
        """Lance un parent Python qui exécute corps, lequel écrit une ligne,
        puis dort ; rend le parent et les mots de cette ligne."""
        script = (PRELUDE.format(src=str(SRC), dormeur=DORMEUR, bavard=BAVARD, lanceur=LANCEUR)
                  + corps + "\nsys.stdout.flush()\ntime.sleep(120)\n")
        parent = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(parent.stdout.close)
        self.addCleanup(parent.wait)
        self.addCleanup(parent.kill)
        mots = parent.stdout.readline().split()
        self.assertTrue(mots, "le parent n'a rien écrit (son erreur est plus haut)")
        return parent, mots

    def tuer_parent(self, parent):
        parent.kill()
        parent.wait()

    def test_l_enfant_meurt_avec_son_parent_tue(self):
        """Le dormeur meurt, et pas seulement le processus que voit Popen :
        derrière le lanceur d'un venv, le vrai interpréteur aussi, parce que
        le lanceur entre dans le job avant de partir."""
        parent, mots = self.parent(
            "lie = enfants.demarrer(BAVARD, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,\n"
            "                       text=True)\n"
            "print(lie.pid, lie.stdout.readline().strip(), lie.rattache)")
        pids = (int(mots[0]), int(mots[1]))
        for pid in pids:
            self.addCleanup(tuer, pid)
        self.assertEqual(mots[2], "True")
        self.assertTrue(all(vivant(pid) for pid in pids))
        self.tuer_parent(parent)
        self.assertTrue(attendre(lambda: not any(vivant(pid) for pid in pids)),
                        "l'enfant a survécu à son parent")

    def test_sa_descendance_meurt_aussi(self):
        parent, mots = self.parent(
            "lie = enfants.demarrer([sys.executable, '-c', LANCEUR], stdin=subprocess.DEVNULL,\n"
            "                       stdout=subprocess.PIPE, text=True)\n"
            "print(lie.pid, lie.stdout.readline().strip())")
        enfant, petit_enfant = int(mots[0]), int(mots[1])
        for pid in (enfant, petit_enfant):
            self.addCleanup(tuer, pid)
        self.assertTrue(vivant(petit_enfant))
        self.tuer_parent(parent)
        self.assertTrue(attendre(lambda: not vivant(enfant) and not vivant(petit_enfant)),
                        "l'enfant ou son propre enfant a survécu")

    def test_un_enfant_ordinaire_ne_le_retient_pas(self):
        """Lancé ensuite par subprocess, même avec close_fds=False, un enfant
        ordinaire n'hérite ni du job ni de la ligne de vie : il survit au
        parent, comme le doit le processus de « Redémarrer », et ne retient
        pas l'enfant lié."""
        parent, mots = self.parent(
            "lie = enfants.demarrer(BAVARD, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,\n"
            "                       text=True)\n"
            "libre = subprocess.Popen(BAVARD, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,\n"
            "                         text=True, close_fds=False)\n"
            "print(lie.pid, lie.stdout.readline().strip(), libre.pid, libre.stdout.readline().strip())")
        pids = [int(mot) for mot in mots]
        for pid in pids:
            self.addCleanup(tuer, pid)
        lie, libre = pids[:2], pids[3]
        self.tuer_parent(parent)
        self.assertTrue(attendre(lambda: not any(vivant(pid) for pid in lie)),
                        "l'enfant lié a survécu")
        self.assertTrue(vivant(libre), "l'enfant ordinaire est mort avec le parent")


class CommePopenEtRun(unittest.TestCase):
    def test_rien_ne_reste_une_fois_l_enfant_attendu(self):
        enfant = enfants.demarrer(DORMEUR, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        self.addCleanup(tuer, enfant.pid)
        self.assertTrue(enfant.rattache)
        enfant.terminate()
        enfant.wait(timeout=30)
        self.assertIsNone(enfant._lien)
        if not WINDOWS:
            def groupe_vide():
                try:
                    os.killpg(enfant.pid, 0)
                except ProcessLookupError:
                    return True
                return False
            self.assertTrue(attendre(groupe_vide), "le guetteur est resté")

    def test_la_sortie_et_le_pid_sont_ceux_de_la_commande(self):
        """La sortie se ferme avec la commande, pas avec le guetteur (sinon
        communicate() attendrait jusqu'au délai). Sous Linux et macOS, le PID
        que voit Popen est celui de la commande elle-même : le shell du
        guetteur se remplace par elle, et terminate() la touche directement.
        Sous Windows, Popen lance la commande sans intermédiaire, mais le
        python.exe d'un venv n'est qu'un lanceur : le PID écrit serait celui
        de son enfant."""
        with enfants.demarrer([sys.executable, "-c", "import os; print(os.getpid())"],
                              stdout=subprocess.PIPE, text=True) as enfant:
            sortie, _ = enfant.communicate(timeout=30)
        if WINDOWS:
            self.assertTrue(sortie.strip().isdigit())
        else:
            self.assertEqual(int(sortie), enfant.pid)

    def test_tube_au_dela_du_descripteur_9(self):
        """Dans une vraie application (serveur, icône, journaux), le tube du
        guetteur reçoit un descripteur à deux chiffres. Le sh d'Ubuntu (dash)
        ne lit qu'un chiffre devant une redirection : « exec 13<&- » y lançait
        la commande « 13 », et BRouter ne démarrait plus dans hike2gpx 0.2.1
        (VM Ubuntu, 2026-10-22). On occupe tout jusqu'à 12 pour le reproduire."""
        occupes = []
        try:
            while True:
                fd = os.open(os.devnull, os.O_RDONLY)
                occupes.append(fd)
                if fd >= 12:
                    break
            with enfants.demarrer([sys.executable, "-c", "print('vivant')"],
                                  stdout=subprocess.PIPE, text=True) as enfant:
                sortie, _ = enfant.communicate(timeout=30)
        finally:
            for fd in occupes:
                os.close(fd)
        self.assertEqual((enfant.returncode, sortie.strip()), (0, "vivant"))

    def test_lancer_comme_subprocess_run(self):
        commande = [sys.executable, "-c",
                    "import sys; print(sys.stdin.read().upper()); sys.exit(3)"]
        resultat = enfants.lancer(commande, input="abc", capture_output=True, text=True,
                                  timeout=30)
        self.assertEqual((resultat.returncode, resultat.stdout.strip()), (3, "ABC"))
        self.assertEqual(resultat.args, commande)
        with self.assertRaises(subprocess.CalledProcessError):
            enfants.lancer(commande, input="", capture_output=True, check=True, timeout=30)
        with self.assertRaises(subprocess.TimeoutExpired):
            enfants.lancer(DORMEUR, stdin=subprocess.DEVNULL, timeout=1)

    def test_programme_introuvable(self):
        with self.assertRaises(FileNotFoundError):
            enfants.demarrer(["nico579-programme-introuvable"])
        with self.assertRaises(FileNotFoundError):
            enfants.lancer([str(SRC / "introuvable" / "programme")])

    def test_options_refusees(self):
        with self.assertRaises(TypeError):
            enfants.demarrer("python -c pass")
        for option in ("shell", "executable", "start_new_session"):
            with self.subTest(option=option), self.assertRaises(ValueError):
                enfants.demarrer(DORMEUR, **{option: True})


if __name__ == "__main__":
    unittest.main()
