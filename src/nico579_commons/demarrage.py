"""Démarrage automatique d'une application avec la session de l'utilisateur.

Chaque système a son mécanisme, et aucun n'exige de droits d'administrateur :

  Windows : un raccourci dans le dossier Démarrage (créé par nico579_commons.
            raccourci, par l'interface COM de l'explorateur) ;
  Linux   : un service systemd utilisateur ;
  macOS   : un agent launchd.

Chacun se défait en supprimant un fichier, ce qui est délibéré : un mécanisme de
démarrage qu'on ne sait plus retirer est une nuisance. Les trois applications
(blink2video, lidar2map, watch2notif) en avaient chacune une version, d'où ce
module : le meilleur de chacune, c'est-à-dire des arguments systemd entre
guillemets avec % et $ doublés, l'environnement de session de « systemctl
--user » déduit de /run/user/<uid>, un agent launchd écrit par plistlib (donc
toujours valide, quel que soit le chemin) et la migration du vieux script .vbs.

Une application décrit son entrée (nom, commande, dossier, description, label
macOS) et appelle activer, desactiver et est_actif. Les refus sont des
ErreurDemarrage (une RuntimeError) avec une clé de message stable.

Bibliothèque standard seule.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import raccourci

LIBELLES = {
    "fr": {
        "raccourci_non_cree": "Raccourci de démarrage non créé : {detail}",
        "installation_refusee": "Refus de systemd : « {commande} » a rendu {retour} (service écrit dans {cible}).",
        "plateforme_non_prise_en_charge": "Démarrage automatique non pris en charge sur {plateforme}.",
        "session_systemd_absente": "Aucune session systemd pour cet utilisateur : le service démarrera à la prochaine connexion (ou sans connexion avec « loginctl enable-linger {utilisateur} »).",
    },
    "en": {
        "raccourci_non_cree": "Startup shortcut not created: {detail}",
        "installation_refusee": "systemd refused: \"{commande}\" returned {retour} (service written to {cible}).",
        "plateforme_non_prise_en_charge": "Automatic startup not supported on {plateforme}.",
        "session_systemd_absente": "No systemd session for this user: the service will start at the next login (or without login with \"loginctl enable-linger {utilisateur}\").",
    },
}

RELANCE_MACOS = {"SuccessfulExit": False}


class ErreurDemarrage(RuntimeError):
    """Refus ou échec du démarrage automatique : `code` est une clé de message
    stable, `valeurs` ses paramètres, message(langue) le texte."""

    def __init__(self, code: str, **valeurs):
        self.code = code
        self.valeurs = valeurs
        super().__init__(self.message("fr"))

    def message(self, langue: str = "fr") -> str:
        return message(self.code, langue, **self.valeurs)


def message(code: str, langue: str = "fr", **valeurs) -> str:
    modele = LIBELLES.get(langue, LIBELLES["en"]).get(code) or code
    try:
        return modele.format(**valeurs)
    except (KeyError, IndexError):
        return modele


@dataclass(frozen=True)
class Entree:
    """Une entrée de démarrage : un fichier par système, nommé d'après `nom`."""
    nom: str                           # « lidar2map », « blink2video-start »...
    commande: tuple                    # exécutable puis arguments
    dossier: Path                      # dossier de travail
    description: str = ""
    label_macos: str = ""              # défaut : com.nico.<nom>
    # Linux : après la session graphique (une application à icône), et attente
    # entre deux relances après un échec (secondes ; 0 : celle de systemd).
    apres_session_graphique: bool = False
    attente_relance_s: int = 0
    # macOS : anciens labels à décharger avant de charger le nouveau.
    anciens_labels_macos: tuple = field(default_factory=tuple)
    # Windows : un ancien script <nom>.vbs à retirer (VBScript quitte Windows).
    retire_vbs: bool = False

    @property
    def label(self) -> str:
        return self.label_macos or f"com.nico.{self.nom}"

    @property
    def texte(self) -> str:
        return self.description or self.nom


# ------------------------------------------------------------------ chemins

def dossier_demarrage(plateforme: Optional[str] = None) -> Path:
    """Dossier Démarrage de l'utilisateur (Windows), tel que le donne le shell,
    donc aussi quand il est redirigé ; %APPDATA% sinon."""
    plateforme = plateforme or sys.platform
    if plateforme == "win32":
        try:
            import ctypes
            tampon = ctypes.create_unicode_buffer(260)
            # CSIDL_STARTUP = 7 : dossier de démarrage de l'utilisateur courant.
            if ctypes.windll.shell32.SHGetFolderPathW(None, 7, None, 0, tampon) == 0 and tampon.value:
                return Path(tampon.value)
        except (AttributeError, OSError):
            pass
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    raise ErreurDemarrage("plateforme_non_prise_en_charge", plateforme=plateforme)


def _accueil(accueil: Optional[Path]) -> Path:
    return Path(accueil) if accueil is not None else Path.home()


def chemin_unite(entree: Entree, accueil: Optional[Path] = None) -> Path:
    return _accueil(accueil) / ".config" / "systemd" / "user" / f"{entree.nom}.service"


def chemin_agent(entree: Entree, accueil: Optional[Path] = None) -> Path:
    return _accueil(accueil) / "Library" / "LaunchAgents" / f"{entree.label}.plist"


def chemin_raccourci(entree: Entree, dossier: Optional[Path] = None) -> Path:
    return (dossier or dossier_demarrage("win32")) / f"{entree.nom}.lnk"


def _chemin_vbs(entree: Entree, dossier: Optional[Path] = None) -> Path:
    return (dossier or dossier_demarrage("win32")) / f"{entree.nom}.vbs"


# --------------------------------------------------------- contenus (purs)

def argument_systemd(valeur: str) -> str:
    """Un argument d'ExecStart= entre guillemets, selon les règles de systemd
    (man systemd.service, « Command lines ») : antislash et guillemet échappés,
    % doublé (spécificateurs), $ doublé (substitution de variables). Une simple
    jointure par espaces coupait en deux un chemin comme « /home/moi/Mes
    Videos/programme »."""
    echappe = (valeur.replace("\\", "\\\\").replace('"', '\\"')
               .replace("%", "%%").replace("$", "$$"))
    return f'"{echappe}"'


def contenu_unite(entree: Entree) -> str:
    lignes = ["[Unit]", f"Description={entree.texte}"]
    if entree.apres_session_graphique:
        lignes.append("After=graphical-session.target")
    lignes += ["", "[Service]", "Type=simple",
               # Une seule valeur, que systemd ne découpe pas ; seuls ses
               # spécificateurs % y sont interprétés.
               f"WorkingDirectory={str(entree.dossier).replace('%', '%%')}",
               "ExecStart=" + " ".join(argument_systemd(str(a)) for a in entree.commande),
               "Restart=on-failure"]
    if entree.attente_relance_s:
        lignes.append(f"RestartSec={entree.attente_relance_s}")
    lignes += ["", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lignes)


def contenu_agent(entree: Entree) -> str:
    """Agent launchd. Relancé seulement après un échec, pas après un arrêt voulu
    depuis l'icône ou une mise à jour : KeepAlive=true serait combattu par
    launchd, qui relancerait aussitôt l'ancienne instance. plistlib échappe
    tout (un dossier « Blink & Videos » écrivait un & brut, et launchd refusait
    le fichier)."""
    donnees = {
        "Label": entree.label,
        "ProgramArguments": [str(a) for a in entree.commande],
        "WorkingDirectory": str(entree.dossier),
        "RunAtLoad": True,
        "KeepAlive": dict(RELANCE_MACOS),
    }
    return plistlib.dumps(donnees, sort_keys=False).decode("utf-8")


def env_systemctl(environ=None, uid=None, racine: Path = Path("/run/user")) -> dict:
    """Environnement de « systemctl --user », session systemd comprise.

    Un utilisateur dédié ouvert par su ou sudo n'a ni XDG_RUNTIME_DIR ni
    DBUS_SESSION_BUS_ADDRESS : systemctl --user ne trouve alors pas son
    gestionnaire de services. Les deux se déduisent de /run/user/<uid>, que
    systemd crée pour tout utilisateur qui a une session ou le « linger »
    activé."""
    env = dict(os.environ if environ is None else environ)
    if not env.get("XDG_RUNTIME_DIR"):
        dossier = racine / str(os.getuid() if uid is None else uid)
        if dossier.is_dir():
            env["XDG_RUNTIME_DIR"] = str(dossier)
    if not env.get("DBUS_SESSION_BUS_ADDRESS") and env.get("XDG_RUNTIME_DIR"):
        bus = Path(env["XDG_RUNTIME_DIR"]) / "bus"
        if bus.exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    return env


# -------------------------------------------------------------- lancement

def _lancer_par_defaut(commande, **options):
    drapeaux = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    if drapeaux:
        options.setdefault("creationflags", drapeaux)
    return subprocess.run(list(commande), **options)


# ----------------------------------------------------------------- actions

def est_actif(entree: Entree, *, plateforme: Optional[str] = None,
              accueil: Optional[Path] = None, dossier: Optional[Path] = None) -> bool:
    """Vrai si l'entrée est posée (son fichier existe)."""
    plateforme = plateforme or sys.platform
    if plateforme == "win32":
        return (chemin_raccourci(entree, dossier).exists()
                or (entree.retire_vbs and _chemin_vbs(entree, dossier).exists()))
    if plateforme == "darwin":
        return chemin_agent(entree, accueil).exists()
    if plateforme.startswith("linux"):
        return chemin_unite(entree, accueil).exists()
    return False


def activer(entree: Entree, *, plateforme: Optional[str] = None,
            accueil: Optional[Path] = None, dossier: Optional[Path] = None,
            lancer: Callable = _lancer_par_defaut, env: Optional[dict] = None) -> list:
    """Pose l'entrée et, sous Linux et macOS, la démarre. Rend la liste des
    avertissements (clés de message : « session_systemd_absente »), vide quand
    tout est net. Lève ErreurDemarrage si le système refuse.

    `lancer(commande, **options)` : subprocess.run, ou un équivalent ;
    `env` : l'environnement de systemctl (par défaut, celui de la session)."""
    plateforme = plateforme or sys.platform
    if plateforme == "win32":
        _activer_windows(entree, dossier, lancer)
        return []
    if plateforme == "darwin":
        _activer_macos(entree, accueil, lancer)
        return []
    if plateforme.startswith("linux"):
        return _activer_linux(entree, accueil, lancer, env)
    raise ErreurDemarrage("plateforme_non_prise_en_charge", plateforme=plateforme)


def desactiver(entree: Entree, *, plateforme: Optional[str] = None,
               accueil: Optional[Path] = None, dossier: Optional[Path] = None,
               lancer: Callable = _lancer_par_defaut, env: Optional[dict] = None) -> None:
    """Arrête et retire l'entrée ; ne lève rien si elle n'était pas posée."""
    plateforme = plateforme or sys.platform
    if plateforme == "win32":
        chemin_raccourci(entree, dossier).unlink(missing_ok=True)
        # Le .vbs d'une ancienne version aussi : sinon il relancerait l'application.
        _chemin_vbs(entree, dossier).unlink(missing_ok=True)
    elif plateforme == "darwin":
        cible = chemin_agent(entree, accueil)
        if cible.exists():
            _quitter_labels_macos(entree, lancer)
            lancer(["launchctl", "unload", str(cible)], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            cible.unlink(missing_ok=True)
    elif plateforme.startswith("linux"):
        env = env_systemctl() if env is None else env
        lancer(["systemctl", "--user", "disable", "--now", entree.nom], check=False,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        existait = chemin_unite(entree, accueil).exists()
        chemin_unite(entree, accueil).unlink(missing_ok=True)
        if existait:
            lancer(["systemctl", "--user", "daemon-reload"], check=False, env=env)
    else:
        raise ErreurDemarrage("plateforme_non_prise_en_charge", plateforme=plateforme)


def migrer_vbs(entree: Entree, *, dossier: Optional[Path] = None,
               lancer: Callable = _lancer_par_defaut) -> bool:
    """Remplace le .vbs d'une ancienne version par le raccourci, sans toucher au
    choix de l'utilisateur : rien si le démarrage automatique n'était pas actif.
    Vrai si un remplacement a eu lieu (Windows seulement)."""
    if sys.platform != "win32" or not _chemin_vbs(entree, dossier).exists():
        return False
    _activer_windows(entree, dossier, lancer)
    return True


def installees(prefixe: str, *, plateforme: Optional[str] = None,
               accueil: Optional[Path] = None, dossier: Optional[Path] = None,
               prefixe_label: str = "") -> list:
    """Fichiers d'entrées dont le nom commence par `prefixe` (« blink2video » :
    toutes les entrées « blink2video-... »), pour qui en tient plusieurs.
    `prefixe_label` : préfixe des labels macOS (par défaut com.nico.<prefixe>)."""
    plateforme = plateforme or sys.platform
    if plateforme == "win32":
        return sorted((dossier or dossier_demarrage("win32")).glob(f"{prefixe}*.lnk"))
    if plateforme == "darwin":
        base = _accueil(accueil) / "Library" / "LaunchAgents"
        return sorted(base.glob(f"{prefixe_label or 'com.nico.' + prefixe}*.plist"))
    if plateforme.startswith("linux"):
        return sorted((_accueil(accueil) / ".config" / "systemd" / "user").glob(f"{prefixe}*.service"))
    return []


# ----------------------------------------------------------- par système

def _activer_windows(entree: Entree, dossier: Optional[Path], lancer: Callable) -> None:
    """Raccourci .lnk dans le dossier Démarrage. WindowStyle réduit : un
    programme console qui cache sa fenêtre dès son départ naît ainsi sans éclair
    à l'écran, et une console qui reviendrait ne s'ouvrirait pas en plein écran."""
    cible_dossier = dossier or dossier_demarrage("win32")
    cible_dossier.mkdir(parents=True, exist_ok=True)
    messages = []

    def appeler(commande):
        resultat = lancer(list(commande), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, text=True, errors="replace", check=False)
        return resultat

    code = raccourci.creer(
        entree.nom, list(entree.commande), entree.dossier, description=entree.texte,
        reduit=True, plateforme="win32", dossier_bureau=cible_dossier,
        lancer=appeler, ecrire=messages.append)
    if code != 0 or not chemin_raccourci(entree, cible_dossier).exists():
        raise ErreurDemarrage("raccourci_non_cree",
                              detail=(messages[-1] if messages else str(cible_dossier)))
    if entree.retire_vbs:
        # Deux entrées lanceraient deux fois l'application à l'ouverture de session.
        _chemin_vbs(entree, cible_dossier).unlink(missing_ok=True)


def _activer_linux(entree: Entree, accueil: Optional[Path], lancer: Callable,
                   env: Optional[dict]) -> list:
    env = env_systemctl() if env is None else env
    cible = chemin_unite(entree, accueil)
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(contenu_unite(entree), encoding="utf-8")
    lancer(["systemctl", "--user", "daemon-reload"], check=False, env=env)
    activation = ["systemctl", "--user", "enable", "--now", entree.nom]
    resultat = lancer(activation, check=False, env=env)
    if not env.get("XDG_RUNTIME_DIR"):
        # Pas de session systemd (su, sudo...) : le fichier est posé, il servira
        # à la prochaine connexion ; ce n'est pas un échec.
        import getpass
        try:
            utilisateur = getpass.getuser()
        except Exception:
            utilisateur = "<utilisateur>"
        return [("session_systemd_absente", {"utilisateur": utilisateur})]
    # Avec une session systemd, un refus est un vrai échec : ne plus l'annoncer
    # comme une installation réussie.
    if resultat is not None and getattr(resultat, "returncode", 0) != 0:
        raise ErreurDemarrage("installation_refusee", commande=" ".join(activation),
                              retour=resultat.returncode, cible=cible)
    return []


def _quitter_labels_macos(entree: Entree, lancer: Callable) -> None:
    """Décharge les agents encore chargés sous un ancien nom : launchd garde la
    définition lue à l'ouverture de session, et un « unload » du fichier ne les
    retrouverait pas."""
    for ancien in entree.anciens_labels_macos:
        lancer(["launchctl", "remove", ancien], check=False,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _activer_macos(entree: Entree, accueil: Optional[Path], lancer: Callable) -> None:
    contenu = contenu_agent(entree)
    plistlib.loads(contenu.encode("utf-8"))      # relu : jamais d'agent invalide
    cible = chemin_agent(entree, accueil)
    _quitter_labels_macos(entree, lancer)
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(contenu, encoding="utf-8")
    lancer(["launchctl", "load", str(cible)], check=False)


def apercu(entree: Entree, *, plateforme: Optional[str] = None,
           accueil: Optional[Path] = None, dossier: Optional[Path] = None) -> dict:
    """Ce qui serait écrit, sans rien écrire (--dry-run) : {"cible", "contenu"}
    (contenu vide pour le raccourci Windows, fichier binaire créé par le shell)."""
    plateforme = plateforme or sys.platform
    if plateforme == "win32":
        return {"cible": chemin_raccourci(entree, dossier), "contenu": ""}
    if plateforme == "darwin":
        return {"cible": chemin_agent(entree, accueil), "contenu": contenu_agent(entree)}
    if plateforme.startswith("linux"):
        return {"cible": chemin_unite(entree, accueil), "contenu": contenu_unite(entree)}
    raise ErreurDemarrage("plateforme_non_prise_en_charge", plateforme=plateforme)


def commande_ligne(commande: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(commande))
