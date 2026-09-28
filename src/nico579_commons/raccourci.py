"""Raccourci sur le Bureau : ouvrir ou lancer une application en un
double-clic, sous Windows, macOS et Linux.

Repris de raccourci_bureau.py de blink2video. N'installe rien de permanent :
un seul fichier posé sur le Bureau, que l'utilisateur retire lui-même le jour
où il n'en veut plus. L'application fournit sa ligne de commande complète
(exécutable figé, ou pythonw.exe et son script depuis les sources) : elle
seule sait comment elle se lance.

- Windows : un .lnk, par l'interface COM de l'explorateur (WScript.Shell)
  que PowerShell expose sans rien installer. Emplacement du Bureau demandé
  au système (CSIDL_DESKTOPDIRECTORY), qui suit un Bureau redirigé.
- macOS : un .app minuscule fabriqué par osacompile autour de « do shell
  script », qui lance sans jamais montrer de fenêtre (un .command ouvrirait
  toujours Terminal.app).
- Linux : un .desktop, marqué « de confiance » pour GNOME (gio), sans effet
  ailleurs.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

LIBELLES = {
    "fr": {
        "non_pris_en_charge": "Raccourci sur le Bureau non pris en charge sur {plateforme}.",
        "creerait": "Créerait {cible} : {detail}",
        "echec": "Échec du raccourci : {detail}",
        "non_cree": "raccourci non créé",
        "cree": "Raccourci créé : {cible}",
    },
    "en": {
        "non_pris_en_charge": "Desktop shortcut not supported on {plateforme}.",
        "creerait": "Would create {cible}: {detail}",
        "echec": "Shortcut failed: {detail}",
        "non_cree": "shortcut not created",
        "cree": "Shortcut created: {cible}",
    },
}


def _texte(langue: str, cle: str, **valeurs) -> str:
    return LIBELLES.get(langue, LIBELLES["en"])[cle].format(**valeurs)


def _lancer(commande: Sequence[str]):
    """subprocess.run sans fenêtre de console qui clignote sous Windows
    (CREATE_NO_WINDOW seul, jamais avec DETACHED_PROCESS)."""
    options = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(list(commande), stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          text=True, errors="replace", check=False, **options)


def bureau(plateforme: Optional[str] = None) -> Path:
    """Dossier du Bureau de la session courante."""
    plateforme = plateforme or sys.platform
    if plateforme == "win32":
        import ctypes
        tampon = ctypes.create_unicode_buffer(1024)
        # CSIDL_DESKTOPDIRECTORY = 0x10 : dossier physique du Bureau (distinct
        # de CSIDL_DESKTOP, racine virtuelle du Shell, qui n'est pas un chemin).
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, tampon)
        return Path(tampon.value)
    return Path.home() / "Desktop"


def _chaine_ps(valeur: str) -> str:
    return "'" + valeur.replace("'", "''") + "'"


def _applescript(texte: str) -> str:
    return '"' + texte.replace("\\", "\\\\").replace('"', '\\"') + '"'


def creer(nom: str, commande: Sequence[str], dossier: Path, icone: Optional[Path] = None,
          description: str = "", *, terminal: bool = False, reduit: bool = False,
          simulation: bool = False, langue: str = "fr", plateforme: Optional[str] = None,
          dossier_bureau: Optional[Path] = None,
          lancer: Callable = _lancer, ecrire: Callable[[str], None] = print) -> int:
    """Pose le raccourci `nom` sur le Bureau ; 0 si c'est fait (ou simulé).

    `commande` : ligne complète, exécutable en tête. `dossier` : dossier de
    travail. `icone` : .ico sous Windows, .png ou .ico sous Linux (ignorée
    sous macOS). `terminal` : Linux, ouvrir dans un terminal (application en
    mode console). `reduit` : Windows, fenêtre réduite d'emblée (exécutable en
    mode console, qui ne peut pas se lancer sans fenêtre)."""
    plateforme = plateforme or sys.platform
    cible_dossier = dossier_bureau or bureau(plateforme)
    if plateforme == "win32":
        return _windows(nom, commande, dossier, icone, description, reduit,
                        simulation, langue, cible_dossier, lancer, ecrire)
    if plateforme == "darwin":
        return _macos(nom, commande, dossier, simulation, langue, cible_dossier, lancer, ecrire)
    if plateforme.startswith("linux"):
        return _linux(nom, commande, dossier, icone, terminal, simulation, langue,
                      cible_dossier, lancer, ecrire)
    ecrire(_texte(langue, "non_pris_en_charge", plateforme=plateforme))
    return 1


def _windows(nom, commande, dossier, icone, description, reduit, simulation, langue,
             dossier_bureau, lancer, ecrire) -> int:
    cible = dossier_bureau / f"{nom}.lnk"
    executable, arguments = commande[0], subprocess.list2cmdline(list(commande[1:]))
    # WindowStyle : 7 = réduite, 1 = normale.
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut({cible});"
        "$s.TargetPath = {executable}; $s.Arguments = {arguments};"
        "$s.WorkingDirectory = {dossier};{icone}"
        "$s.WindowStyle = {style}; $s.Description = {description}; $s.Save()"
    ).format(
        cible=_chaine_ps(str(cible)), executable=_chaine_ps(str(executable)),
        arguments=_chaine_ps(arguments), dossier=_chaine_ps(str(dossier)),
        icone=f" $s.IconLocation = {_chaine_ps(str(icone))};" if icone else "",
        style=7 if reduit else 1, description=_chaine_ps(description or nom),
    )
    if simulation:
        ecrire(_texte(langue, "creerait", cible=cible, detail=f"{executable} {arguments}"))
        return 0
    resultat = lancer(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    if resultat.returncode != 0 or not cible.exists():
        ecrire(_texte(langue, "echec",
                      detail=(resultat.stderr or "").strip() or _texte(langue, "non_cree")))
        return 1
    ecrire(_texte(langue, "cree", cible=cible))
    return 0


def _macos(nom, commande, dossier, simulation, langue, dossier_bureau, lancer, ecrire) -> int:
    cible = dossier_bureau / f"{nom}.app"
    commande_shell = "cd {} && {} > /dev/null 2>&1 &".format(
        shlex.quote(str(dossier)), " ".join(shlex.quote(str(a)) for a in commande))
    script = "do shell script " + _applescript(commande_shell)
    if simulation:
        ecrire(_texte(langue, "creerait", cible=cible, detail=script))
        return 0
    if cible.exists():
        shutil.rmtree(cible)
    resultat = lancer(["osacompile", "-o", str(cible), "-e", script])
    if resultat.returncode != 0 or not cible.exists():
        ecrire(_texte(langue, "echec",
                      detail=(resultat.stderr or "").strip() or _texte(langue, "non_cree")))
        return 1
    ecrire(_texte(langue, "cree", cible=cible))
    return 0


def contenu_desktop(nom: str, commande: Sequence[str], dossier: Path,
                    icone: Optional[Path] = None, terminal: bool = False) -> str:
    """Le fichier .desktop (Linux), séparé pour les tests."""
    exec_ligne = "sh -c {}".format(
        shlex.quote(" ".join(shlex.quote(str(a)) for a in commande)))
    lignes = ["[Desktop Entry]", "Type=Application", f"Name={nom}",
              f"Exec={exec_ligne}", f"Path={dossier}"]
    if icone:
        lignes.append(f"Icon={icone}")
    lignes.append(f"Terminal={'true' if terminal else 'false'}")
    return "\n".join(lignes) + "\n"


def _linux(nom, commande, dossier, icone, terminal, simulation, langue, dossier_bureau,
           lancer, ecrire) -> int:
    cible = dossier_bureau / f"{nom}.desktop"
    contenu = contenu_desktop(nom, commande, dossier, icone, terminal)
    if simulation:
        ecrire(_texte(langue, "creerait", cible=cible, detail="\n" + contenu))
        return 0
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(contenu, encoding="utf-8")
    cible.chmod(0o755)
    # GNOME/Nautilus refuse de lancer un .desktop du Bureau tant qu'il n'est
    # pas marqué « de confiance » ; KDE et XFCE ignorent cet attribut.
    try:
        lancer(["gio", "set", str(cible), "metadata::trusted", "yes"])
    except OSError:
        pass
    ecrire(_texte(langue, "cree", cible=cible))
    return 0
