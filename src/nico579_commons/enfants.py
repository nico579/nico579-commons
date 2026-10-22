"""Processus enfants liés à la vie de l'application.

Un serveur qui lance des processus de longue durée les arrête en sortant.
S'il meurt sans passer par là (console fermée, processus tué, plantage), ses
enfants lui survivent, orphelins, avec leur mémoire et leur port : trois
moteurs BRouter de hike2gpx, jusqu'à 768 Mo chacun, le 2026-10-20.

demarrer() est un subprocess.Popen dont l'enfant meurt avec ce processus, de
quelque façon que celui-ci meure, et lancer() le subprocess.run
correspondant : mêmes noms que runtime.demarrer et runtime.lancer de
blink2video. Chaque système a son mécanisme :

- Windows : l'enfant entre dans un « job » du noyau marqué
  KILL_ON_JOB_CLOSE, dont seul ce processus tient la poignée. À sa mort, le
  noyau ferme la poignée et tue tout ce que le job contient (le procédé de
  libuv, donc de Node.js). L'enfant naît suspendu et ne part qu'une fois dans
  le job : le python.exe d'un venv, par exemple, n'est qu'un lanceur qui
  démarre aussitôt le vrai interpréteur, lequel échapperait au job s'il
  naissait avant ce rattachement. Sous Windows 7, un processus n'entre que
  dans un seul job : si l'application tourne déjà dans un job, l'enfant, qui
  en hérite, tourne sans ce lien, et son attribut rattache le dit.
- Linux et macOS : une ligne de vie. L'enfant naît dans une nouvelle session,
  donc dans son propre groupe de processus, avec un guetteur sh qui lit un
  tube dont seul ce processus tient l'écriture. À sa mort, le noyau ferme le
  tube, la lecture rend la main et le guetteur tue le groupe entier.
  PR_SET_PDEATHSIG ne convient pas : propre à Linux (macOS n'a rien de tel),
  il part à la mort du fil qui a lancé l'enfant et non du processus (dans un
  serveur, le fil d'une requête), et demande preexec_fn, que la documentation
  de subprocess déconseille dès qu'un programme a plusieurs fils.

Une fois l'enfant attendu (wait, poll, communicate, sortie d'un bloc with),
le lien est défait, et ce que l'enfant aurait laissé derrière lui dans son
job ou son groupe est tué : rien ne s'accumule au fil des lancements. Un
enfant qui ne doit pas mourir avec l'application (« Redémarrer », un
navigateur) se lance comme avant, par subprocess ou par relance.
"""

import errno
import os
import shutil
import subprocess
import sys
import threading

# Le guetteur, en sh POSIX. $1 : le descripteur de lecture de la ligne de
# vie ; la suite : la commande. Le sous-shell quitte le dossier courant et
# lâche les flux hérités (sans quoi un appelant qui lit la sortie de l'enfant
# jusqu'au bout attendrait aussi le guetteur), lit le tube, puis tue son
# groupe (kill 0). Il lit par le descripteur reçu et non par son entrée
# standard : celle d'une commande lancée en arrière-plan est /dev/null
# (POSIX). Le shell principal se remplace par la commande, qui garde ainsi le
# PID que connaît Popen. Il ne ferme pas sa copie du tube : dash, le sh
# d'Ubuntu et de Debian, ne lit qu'un chiffre devant une redirection, et
# « exec 13<&- » y lançait la commande « 13 ». La commande hérite donc d'un
# bout de lecture, sans effet sur le lien : la fin du tube ne dépend que du
# bout d'écriture, que l'application garde seule.
_GUETTEUR = """\
fd=$1; shift
( cd /; exec </dev/null >/dev/null 2>&1; read -r x <&"$fd"; kill -s KILL 0 ) &
exec "$@"
"""

# Options de Popen incompatibles avec le lien : le shell et l'exécutable
# prendraient la place du guetteur, une autre session ou un autre groupe
# sortiraient l'enfant du groupe que le guetteur tue.
_REFUSEES = ("shell", "executable", "start_new_session", "process_group")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _LimitesDeBase(ctypes.Structure):  # JOBOBJECT_BASIC_LIMIT_INFORMATION
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _LimitesEtendues(ctypes.Structure):  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        _fields_ = [("BasicLimitInformation", _LimitesDeBase),
                    ("IoInfo", ctypes.c_uint64 * 6),  # IO_COUNTERS
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _Fil(ctypes.Structure):  # THREADENTRY32
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD),
                    ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", wintypes.LONG),
                    ("tpDeltaPri", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD)]

    _LIMITES_ETENDUES = 9  # JobObjectExtendedLimitInformation
    # KILL_ON_JOB_CLOSE, plus BREAKAWAY_OK : un descendant qui demande
    # expressément à sortir du job (CREATE_BREAKAWAY_FROM_JOB) le peut, au
    # lieu de voir son lancement refusé ; les autres y restent.
    _LIMITES = 0x2000 | 0x0800
    _DROITS = 0x0100 | 0x0001  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    _SUSPENDU = 0x00000004  # CREATE_SUSPENDED
    _RELEVE_DES_FILS = 0x00000004  # TH32CS_SNAPTHREAD
    _REPRENDRE = 0x0002  # THREAD_SUSPEND_RESUME
    _POIGNEE_INVALIDE = ctypes.c_void_p(-1).value

    # Une instance à soi : les argtypes posés ici ne touchent pas
    # ctypes.windll.kernel32, que d'autres modules partagent.
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                  ctypes.c_void_p, wintypes.DWORD)
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(_Fil))
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(_Fil))
    _kernel32.Thread32Next.restype = wintypes.BOOL
    _kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    _kernel32.ResumeThread.restype = wintypes.DWORD


def _reprendre(pid):
    """Fait partir le processus pid, créé suspendu ; vrai si c'est fait.
    Popen ferme la poignée de son fil principal : on le retrouve dans un
    relevé des fils du système (Toolhelp32), où un processus né suspendu n'en
    a qu'un."""
    releve = _kernel32.CreateToolhelp32Snapshot(_RELEVE_DES_FILS, 0)
    if releve in (None, _POIGNEE_INVALIDE):
        return False
    repris = False
    try:
        fil = _Fil()
        fil.dwSize = ctypes.sizeof(fil)
        encore = _kernel32.Thread32First(releve, ctypes.byref(fil))
        while encore:
            if fil.th32OwnerProcessID == pid:
                poignee = _kernel32.OpenThread(_REPRENDRE, False, fil.th32ThreadID)
                if poignee:
                    repris = _kernel32.ResumeThread(poignee) != 0xFFFFFFFF or repris
                    _kernel32.CloseHandle(poignee)
            encore = _kernel32.Thread32Next(releve, ctypes.byref(fil))
    finally:
        _kernel32.CloseHandle(releve)
    return repris


def _job(pid):
    """Poignée d'un nouveau job KILL_ON_JOB_CLOSE qui contient le processus
    pid, ou None si Windows refuse (voir le module). La poignée n'est pas
    héritable : un autre enfant, lancé ensuite par subprocess, ne garde pas
    le job en vie."""
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limites = _LimitesEtendues()
    limites.BasicLimitInformation.LimitFlags = _LIMITES
    processus = _kernel32.OpenProcess(_DROITS, False, pid)
    rattache = bool(processus) and bool(
        _kernel32.SetInformationJobObject(job, _LIMITES_ETENDUES, ctypes.byref(limites),
                                          ctypes.sizeof(limites))
        and _kernel32.AssignProcessToJobObject(job, processus))
    if processus:
        _kernel32.CloseHandle(processus)
    if not rattache:
        _kernel32.CloseHandle(job)
        return None
    return job


def _verifier_programme(programme, cwd, env):
    """Lève, comme Popen, FileNotFoundError ou PermissionError pour un
    programme qui ne peut pas démarrer. Lancé par le guetteur, il ferait
    seulement sortir l'enfant en 127 ou 126, et l'appelant qui attend
    l'exception de Popen (Java absent, par exemple) ne la verrait pas."""
    nom = os.fsdecode(programme)
    if os.sep in nom:
        chemin = os.path.join(os.fsdecode(cwd), nom) if cwd is not None else nom
        if not os.path.exists(chemin):
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), nom)
        if os.path.isdir(chemin) or not os.access(chemin, os.X_OK):
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), nom)
    elif shutil.which(nom, path=os.pathsep.join(os.get_exec_path(env))) is None:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), nom)


class Enfant(subprocess.Popen):
    """subprocess.Popen dont l'enfant meurt avec ce processus (voir le
    module). Mêmes options, sauf celles de _REFUSEES ; la commande est une
    liste. rattache dit si le lien tient : toujours sous Linux et macOS,
    sous Windows sauf refus du système. À attendre comme tout Popen."""

    def __init__(self, commande, **options):
        self.rattache = False
        self._lien = None  # Windows : le job ; ailleurs : l'écriture de la ligne de vie
        self._verrou_lien = threading.Lock()
        if isinstance(commande, (str, bytes)):
            raise TypeError("la commande d'un enfant lié est une liste, pas une chaîne")
        refusees = [nom for nom in _REFUSEES if nom in options]
        if refusees:
            raise ValueError("options incompatibles avec un enfant lié : " + ", ".join(refusees))
        commande = [os.fspath(element) for element in commande]
        if sys.platform == "win32":
            drapeaux = options.pop("creationflags", 0)
            super().__init__(commande, creationflags=drapeaux | _SUSPENDU, **options)
            self._lien = _job(self.pid)
            self.rattache = self._lien is not None
            # Suspendu à la demande de l'appelant : à lui de le faire partir.
            if not drapeaux & _SUSPENDU and not _reprendre(self.pid):
                self.kill()
                self.wait()
                raise OSError("le processus créé suspendu n'a pas pu partir")
            return
        _verifier_programme(commande[0], options.get("cwd"), options.get("env"))
        lecture, ecriture = os.pipe()
        transmis = tuple(options.pop("pass_fds", ())) + (lecture,)
        try:
            super().__init__(["/bin/sh", "-c", _GUETTEUR, "sh", str(lecture), *commande],
                             pass_fds=transmis, start_new_session=True, **options)
        except BaseException:
            os.close(ecriture)
            raise
        finally:
            os.close(lecture)
        self.args = commande
        self._lien = ecriture
        self.rattache = True

    def poll(self):
        code = super().poll()
        if code is not None:
            self._delier()
        return code

    def wait(self, timeout=None):
        code = super().wait(timeout)
        self._delier()
        return code

    def _delier(self):
        """L'enfant est mort et attendu : ferme le job (Windows) ou la ligne
        de vie, ce qui tue ce qu'il aurait laissé derrière lui."""
        with self._verrou_lien:
            lien, self._lien = self._lien, None
        if lien is None:
            return
        if sys.platform == "win32":
            _kernel32.CloseHandle(lien)
        else:
            os.close(lien)


def demarrer(commande, **options):
    """subprocess.Popen(commande, **options), l'enfant lié à la vie de ce
    processus ; rend un Enfant."""
    return Enfant(commande, **options)


def lancer(commande, *, input=None, capture_output=False, timeout=None, check=False,
           **options):
    """subprocess.run(commande, ...), l'enfant lié à la vie de ce processus.
    Même déroulé que subprocess.run : délai dépassé ou interruption tuent
    l'enfant avant de remonter."""
    if input is not None:
        if options.get("stdin") is not None:
            raise ValueError("stdin and input arguments may not both be used.")
        options["stdin"] = subprocess.PIPE
    if capture_output:
        if options.get("stdout") is not None or options.get("stderr") is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output.")
        options["stdout"] = options["stderr"] = subprocess.PIPE
    with Enfant(commande, **options) as processus:
        try:
            sortie, erreurs = processus.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            processus.kill()
            if sys.platform == "win32":
                # Comme subprocess.run : sous Windows, la sortie lue jusque-là
                # n'arrive qu'avec ce second communicate().
                exc.stdout, exc.stderr = processus.communicate()
            else:
                processus.wait()
            raise
        except BaseException:
            processus.kill()
            raise
        code = processus.poll()
        if check and code:
            raise subprocess.CalledProcessError(code, processus.args, output=sortie,
                                                stderr=erreurs)
    return subprocess.CompletedProcess(processus.args, code, sortie, erreurs)
