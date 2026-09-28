"""Essai de relance.relancer() sous un vrai service systemd utilisateur.

Lancé en CI sous Linux seulement (job « systemd » de ci.yml), jamais sur un
poste : il pose puis retire l'unité essai-relance.service dans
~/.config/systemd/user.

Le service imite une application lancée par son démarrage automatique :
son processus principal, au premier démarrage, demande sa relance comme le
ferait « Redémarrer », puis sort avec le code 0 ; relancé, il tourne.
Trois scénarios :

- systemd, sortie immédiate : le pire cas, le processus sort avant que
  systemd ait exécuté la relance demandée ;
- systemd, sortie après deux secondes : systemd l'arrête lui-même (SIGTERM) ;
- témoin, simple nouveau processus (unite="") : systemd doit le tuer à la
  sortie du premier, c'est l'issue #35 de blink2video. Sans ce témoin,
  l'essai pourrait réussir pour une autre raison que celle qu'il prétend
  prouver.

    python tests/essai_systemd.py
"""

import os
import subprocess
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
NOM = "essai-relance"
UNITE = f"{NOM}.service"
FICHIER_UNITE = Path.home() / ".config" / "systemd" / "user" / UNITE


def enfant(journal: Path, mode: str, delai_s: float) -> None:
    """Le processus principal du service."""
    sys.path.insert(0, str(RACINE / "src"))
    from nico579_commons import relance

    deja = journal.read_text().split() if journal.exists() else []
    with journal.open("a") as f:
        f.write(f"{os.getpid()}\n")
    if deja:
        time.sleep(3600)        # relancé : l'application tourne
        return
    commande = [sys.executable, __file__, "--enfant", str(journal), mode, str(delai_s)]
    unite = None if mode == "systemd" else ""
    obtenu = relance.relancer(commande, nom=NOM, unite=unite)
    # Sous systemd, son SIGTERM peut arriver dès ici : seul le témoin note
    # ce que relancer() a rendu.
    with journal.with_suffix(".mode").open("w") as f:
        f.write(obtenu)
    time.sleep(delai_s)
    sys.exit(0)


def systemctl(*args, check=True):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True,
                          text=True, check=check)


def propriete(nom: str) -> str:
    return systemctl("show", UNITE, f"--property={nom}", "--value").stdout.strip()


def vivant(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def pids(journal: Path) -> list:
    return [int(p) for p in journal.read_text().split()] if journal.exists() else []


def scenario(mode: str, delai_s: float, dossier: Path) -> str:
    """Rend "" si le scénario se passe comme prévu, sinon ce qui cloche."""
    journal = dossier / f"{mode}-{delai_s}.txt"
    FICHIER_UNITE.parent.mkdir(parents=True, exist_ok=True)
    FICHIER_UNITE.write_text(
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={sys.executable} {__file__} --enfant {journal} {mode} {delai_s}\n"
        "Restart=on-failure\n"
        "RestartSec=10\n", encoding="utf-8")
    systemctl("daemon-reload")
    systemctl("start", UNITE)
    try:
        fin = time.monotonic() + 30
        while time.monotonic() < fin and len(pids(journal)) < 2:
            time.sleep(0.2)
        lances = pids(journal)
        if len(lances) < 2:
            return f"jamais relancé (processus : {lances})"
        time.sleep(delai_s + 5)     # le premier est sorti, systemd a fait son ménage
        second = lances[1]
        if mode == "systemd":
            if propriete("ActiveState") != "active":
                return f"service {propriete('ActiveState')} après la relance"
            if propriete("MainPID") != str(second) or not vivant(second):
                return (f"processus principal {propriete('MainPID')}, "
                        f"attendu {second} vivant")
            return ""
        # Témoin : le nouveau processus, resté dans le cgroup du service,
        # meurt avec lui.
        mode_obtenu = journal.with_suffix(".mode").read_text()
        if mode_obtenu != "processus":
            return f"relancer() a rendu {mode_obtenu!r}, pas 'processus'"
        if vivant(second):
            return f"témoin : le processus {second} a survécu, l'essai ne prouve rien"
        return ""
    finally:
        systemctl("stop", UNITE, check=False)
        FICHIER_UNITE.unlink()
        systemctl("daemon-reload", check=False)


def main() -> int:
    import tempfile

    for _ in range(60):
        if systemctl("show-environment", check=False).returncode == 0:
            break
        time.sleep(0.5)
    else:
        print("gestionnaire systemd de l'utilisateur injoignable")
        return 1
    echecs = 0
    with tempfile.TemporaryDirectory() as dossier:
        for mode, delai_s in (("systemd", 0.0), ("systemd", 2.0), ("processus", 1.0)):
            probleme = scenario(mode, delai_s, Path(dossier))
            print(f"{mode}, sortie après {delai_s:g} s : {probleme or 'comme prévu'}")
            echecs += bool(probleme)
    return 1 if echecs else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--enfant"]:
        enfant(Path(sys.argv[2]), sys.argv[3], float(sys.argv[4]))
    else:
        sys.exit(main())
