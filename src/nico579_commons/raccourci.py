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

import os
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
    if plateforme.startswith("linux"):
        return _bureau_linux()
    return Path.home() / "Desktop"


def _bureau_linux() -> Path:
    """Le dossier du Bureau de l'utilisateur, tel que le dit XDG.

    ~/Desktop n'existe que sur un système en anglais : « Bureau » en
    français, « Schreibtisch » en allemand. Même lecture que xdg-user-dir
    (~/.config/user-dirs.dirs, XDG_DESKTOP_DIR), sans lancer de programme ; à
    défaut ~/Desktop."""
    accueil = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME") or accueil / ".config")
    try:
        lignes = (config / "user-dirs.dirs").read_text(encoding="utf-8").splitlines()
    except OSError:
        lignes = []
    for ligne in lignes:
        ligne = ligne.strip()
        if ligne.startswith("XDG_DESKTOP_DIR="):
            valeur = ligne.split("=", 1)[1].strip().strip('"')
            valeur = valeur.replace("$HOME", str(accueil)).replace("${HOME}", str(accueil))
            chemin = Path(valeur)
            # Un chemin relatif ou égal à ~ (Bureau désactivé) n'est pas un Bureau.
            if chemin.is_absolute() and chemin != accueil:
                return chemin
    return accueil / "Desktop"


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


# Caractères qui obligent à entourer un argument de guillemets dans la clé Exec
# (spécification Desktop Entry, « Exec variables »).
_RESERVES_EXEC = frozenset(" \t\n\"'\\><~|&;$*?#()`")


def _argument_exec(argument: str) -> str:
    """Un argument écrit pour la clé Exec d'un fichier .desktop.

    Ce n'est pas la syntaxe d'un shell : shlex.quote() entoure d'apostrophes,
    que la spécification ne connaît pas. Ici, guillemets doubles seulement si
    nécessaire ; à l'intérieur, guillemet, accent grave, dollar et barre
    oblique inverse sont précédés d'une barre oblique inverse ; « % » est
    doublé (sinon c'est un code de champ) ; enfin chaque barre oblique inverse
    est doublée par la règle générale des chaînes, si bien qu'une barre
    oblique inverse littérale s'écrit avec quatre. Un argument vide s'écrit
    ""."""
    if not argument:
        return '""'
    argument = argument.replace("%", "%%")
    if not any(c in _RESERVES_EXEC for c in argument):
        return argument
    for c in ("\\", '"', "`", "$"):
        argument = argument.replace(c, "\\" + c)
    return '"' + argument.replace("\\", "\\\\") + '"'


def contenu_desktop(nom: str, commande: Sequence[str], dossier: Path,
                    icone: Optional[Path] = None, terminal: bool = False) -> str:
    """Le fichier .desktop (Linux), séparé pour les tests."""
    exec_ligne = " ".join(_argument_exec(str(a)) for a in commande)
    chemin_travail = str(dossier).replace("\\", "\\\\")
    lignes = ["[Desktop Entry]", "Type=Application", f"Name={nom}",
              f"Exec={exec_ligne}", f"Path={chemin_travail}"]
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
    try:
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(contenu, encoding="utf-8")
        cible.chmod(0o755)
    except OSError as erreur:
        # Bureau en lecture seule, disque plein : un code de retour, pas une
        # exception qui traverserait l'appelant (menu de l'icône, CLI).
        ecrire(_texte(langue, "echec", detail=str(erreur)))
        return 1
    # GNOME/Nautilus refuse de lancer un .desktop du Bureau tant qu'il n'est
    # pas marqué « de confiance » ; KDE et XFCE ignorent cet attribut. La
    # valeur est la chaîne « true », pas « yes » : Nautilus et DING (les icônes
    # du bureau d'Ubuntu) comparent à 'true' et traitent toute autre valeur
    # comme « lancement non autorisé » (vu dans la VM Ubuntu, 2026-10-06).
    try:
        lancer(["gio", "set", str(cible), "metadata::trusted", "true"])
    except OSError:
        pass
    ecrire(_texte(langue, "cree", cible=cible))
    return 0
