"""Environnement transmis aux programmes du système lancés par une
application figée par PyInstaller.

Repris à l'identique de lidar2map (_bootstrap_runtime.py), gpxsolar
(_installation.py) et blink2video (runtime.py), qui en avaient chacun une
copie ; watch2notif n'en avait pas.
"""

import os
import sys


def retablir_environnement_systeme(*, fige=None, plateforme=None, environ=None) -> None:
    """Rend aux programmes du système le LD_LIBRARY_PATH d'origine.

    Sous Linux, le bootloader de PyInstaller préfixe cette variable du
    dossier de ses bibliothèques (_internal) et garde l'ancienne valeur dans
    LD_LIBRARY_PATH_ORIG. Tout enfant en hérite : systemctl, xdg-open ou le
    navigateur qu'ouvre webbrowser chargeaient alors les bibliothèques du
    binaire au lieu des leurs, et le systemd de Debian Trixie refuse une
    libcrypto plus ancienne que la sienne (issue #23 de blink2video). C'est
    le rétablissement que recommande PyInstaller pour les programmes
    externes :
    https://pyinstaller.org/en/stable/runtime-information.html#ld-library-path-libpath-considerations

    À appeler dès le démarrage, avant tout lancement de processus. Le
    chargeur d'un processus ne lit la variable qu'à son démarrage : la
    rétablir ne change rien pour le processus courant, ni pour une relance
    du binaire, que son bootloader préfixe de nouveau.
    """
    fige = getattr(sys, "frozen", False) if fige is None else fige
    plateforme = sys.platform if plateforme is None else plateforme
    environ = os.environ if environ is None else environ
    if not fige or plateforme in ("win32", "darwin"):
        return
    origine = environ.get("LD_LIBRARY_PATH_ORIG")
    if origine is not None:
        environ["LD_LIBRARY_PATH"] = origine
    else:
        # Variable absente avant le bootloader : il n'a rien gardé à rétablir.
        environ.pop("LD_LIBRARY_PATH", None)
