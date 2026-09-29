"""Relance de l'application, pour « Redémarrer » dans le menu de l'icône.

Référence : lidar2map (_commande_relance, _relancer_process), jumeau de
gpxsolar, complétée de deux cas qu'aucune des deux ne traitait :

- sous le service systemd de son démarrage automatique (Linux), un
  processus lancé par l'application reste dans le cgroup du service, quel
  que soit son parent. systemd le tue dès que le processus principal sort,
  et comme cette sortie se fait avec le code 0, Restart=on-failure ne
  relance rien : l'application s'arrêtait au lieu de redémarrer (issue #35
  de blink2video). C'est donc systemd qui relance, par
  « systemctl --user --no-block restart » ;
- sous macOS, launchd tue de même ce qui reste du groupe de processus d'un
  agent dont le processus principal sort (clé AbandonProcessGroup de
  launchd.plist) : le nouveau processus part dans sa propre session
  (start_new_session), comme le helper de mise à jour de watch2notif.

Sous Windows, CREATE_NO_WINDOW seul, jamais combiné à DETACHED_PROCESS : la
combinaison rendait le lancement erratique (parfois quinze secondes pour
démarrer, parfois rien), constaté sur watch2notif le 2026-09-07.

hors_du_service() traite le cas voisin d'un second processus qui doit
survivre à celui-ci (« Nouvelle instance » de lidar2map) : sous le service,
il naîtrait lui aussi dans son cgroup, et mourrait avec lui.
"""

import signal
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

# Constante de subprocess sous Windows seulement : recopiée pour que le
# module se charge (et se teste) partout.
CREATE_NO_WINDOW = 0x08000000

# Unité « scope » transitoire, son propre cgroup, que la fin du service
# n'atteint pas : la façon documentée de systemd de lancer un programme à
# part (c'est aussi ainsi que les bureaux GNOME et KDE rangent les
# applications qu'ils lancent). Même préfixe que blink2video (issue #35).
PORTEE_SYSTEMD = ["systemd-run", "--user", "--scope", "--quiet", "--"]


def commande(*, fige=None, executable=None, argv=None) -> list:
    """Commande qui relance ce même programme avec les mêmes arguments.

    Figé, ``argv[0]`` ne désigne pas forcément l'exécutable (le chargeur de
    lidar2map le remplace par le chemin d'un script, ni exécutable ni
    lançable sous Windows) : on relance l'exécutable courant."""
    fige = getattr(sys, "frozen", False) if fige is None else fige
    executable = sys.executable if executable is None else executable
    argv = sys.argv if argv is None else argv
    if fige:
        return [executable] + list(argv[1:])
    return [executable] + list(argv)


def unite_systemd(nom: str, cgroup: Path = Path("/proc/self/cgroup"),
                  plateforme=None) -> str:
    """Service systemd de l'application (« nom.service » ou
    « nom-….service ») dont ce processus fait partie, ou "" : hors Linux,
    hors d'un tel service (lancé depuis un terminal ou le bureau), ou cgroup
    illisible. Lit le cgroup v2 (« 0::/… ») comme les lignes du v1. Repris
    de blink2video (autostart.unite_systemd)."""
    plateforme = sys.platform if plateforme is None else plateforme
    if not plateforme.startswith("linux"):
        return ""
    try:
        texte = cgroup.read_text(encoding="utf-8")
    except OSError:
        return ""
    for ligne in texte.splitlines():
        for segment in reversed(ligne.rsplit(":", 1)[-1].split("/")):
            if segment == f"{nom}.service" or (
                    segment.startswith(f"{nom}-") and segment.endswith(".service")):
                return segment
    return ""


def hors_du_service(commande: Sequence[str], *, nom: str, plateforme=None, unite=None,
                    lancer: Callable = subprocess.run) -> list:
    """``commande`` à lancer pour qu'elle survive à ce processus.

    Sous le service systemd de l'application, préfixée de PORTEE_SYSTEMD :
    lancée telle quelle, elle naîtrait dans le cgroup du service, quel que
    soit son parent, et systemd la tuerait à l'arrêt ou au redémarrage du
    service. Un essai à vide vérifie d'abord que systemd-run répond ; sinon,
    ou hors d'un tel service, la commande telle quelle. ``unite`` : "" force
    la commande telle quelle (essais)."""
    plateforme = sys.platform if plateforme is None else plateforme
    unite = unite_systemd(nom, plateforme=plateforme) if unite is None else unite
    if not unite:
        return list(commande)
    try:
        essai = lancer([*PORTEE_SYSTEMD, "true"], stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return list(commande)
    if essai.returncode != 0:
        return list(commande)
    return [*PORTEE_SYSTEMD, *commande]


def relancer(commande: Sequence[str], *, nom: str, plateforme=None, unite=None,
             lancer: Callable = subprocess.run, demarrer: Callable = subprocess.Popen,
             **options) -> str:
    """Relance l'application ; rend "systemd" ou "processus".

    Sous son service systemd, demande à systemd de relancer le service : il
    arrête ce processus (SIGTERM), puis le démarre de nouveau. Ailleurs, ou
    si systemctl refuse, lance ``commande`` dans un nouveau processus
    détaché ; le processus courant doit alors avoir libéré ce que le nouveau
    va prendre (port d'écoute, verrou d'instance unique) avant l'appel, puis
    sortir. ``unite`` : "" force le nouveau processus (essais). ``options``
    va à subprocess.Popen (cwd, stdout...)."""
    plateforme = sys.platform if plateforme is None else plateforme
    unite = unite_systemd(nom, plateforme=plateforme) if unite is None else unite
    if unite:
        try:
            resultat = lancer(["systemctl", "--user", "--no-block", "restart", unite],
                              stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=30, check=False)
            # Tué par SIGTERM : systemctl, du même cgroup, a reçu le signal
            # de l'arrêt qu'il venait de demander avant d'avoir fini de
            # sortir. La relance est donc en cours.
            if resultat.returncode in (0, -signal.SIGTERM):
                return "systemd"
        except (OSError, subprocess.SubprocessError):
            pass
    options.setdefault("stdin", subprocess.DEVNULL)
    options.setdefault("close_fds", True)
    if plateforme == "win32":
        options.setdefault("creationflags", CREATE_NO_WINDOW)
    else:
        options.setdefault("start_new_session", True)
    demarrer(list(commande), **options)
    return "processus"
