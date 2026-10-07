"""Installation automatique d'une version téléchargée, avec retour arrière.

Tiré de self_update.py de watch2notif (en production sur les trois systèmes),
généralisé pour que toute application livrée en dossier PyInstaller (« onedir »,
ou bundle .app sous macOS) puisse se mettre à jour elle-même. Le même
protocole pour toutes :

1. `preparer` télécharge l'archive (maj_archive), l'extrait à côté de
   l'installation, vérifie que le bundle est complet et lui fait passer son
   auto-test, sans toucher à l'installation en cours.
2. `lancer` démarre un assistant externe (PowerShell sous Windows, sh ailleurs)
   qui écrit « prêt » et attend le feu vert.
3. L'application confirme avec `valider`, puis se ferme ; l'assistant attend sa
   fin, échange les dossiers, recopie les données à conserver, relance la
   nouvelle version et vérifie qu'elle reste en vie. À la moindre défaillance,
   il remet l'ancienne version en place et la relance.

Ce que chaque application décrit : son nom, comment s'appelle l'archive de
chaque système (c'est sa Disposition), les données à conserver et, le cas
échéant, son service systemd ou son agent launchd.

Bibliothèque standard seule (comme maj_archive).
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import atomique, maj_archive
from .maj_archive import ErreurMiseAJour

ARRET_PRET_S = 25        # attente de « prêt » : Windows peut scanner un script inédit (AMSI)
ARRET_ACCUSE_S = 8       # attente de l'accusé de réception après le feu vert


@dataclass(frozen=True)
class Application:
    """Ce que l'installateur doit savoir d'une application."""
    nom: str
    # Noms (dans le dossier de données du bundle) recopiés de l'ancienne
    # installation vers la nouvelle : fichiers ou dossiers. Une application dont
    # les données vivent hors du dossier d'installation n'en met aucun.
    donnees_preservees: tuple = ()
    # Noms tolérés dans le dossier d'installation en plus de l'exécutable et de
    # _internal (verrou d'instance, journal...). Tout autre nom fait refuser
    # le remplacement : le dossier n'est pas, ou plus, le bundle publié.
    noms_toleres: tuple = ()
    # Ce qu'il faut relancer : un processus avec ces arguments (arguments_relance),
    # ou plusieurs, un par entrée de verbes_relance (chacune la liste d'arguments
    # d'un processus : les « verbes » qui tournaient, comme les compositions de
    # blink2video). Ce qui tourne n'est connu qu'au moment de la mise à jour :
    # lancer() et l'Installateur acceptent aussi la liste à ce moment-là.
    arguments_relance: tuple = ()
    verbes_relance: tuple = ()
    unite_systemd: str = ""          # ex. « watch2notif.service » (service utilisateur)
    label_launchd: str = ""          # ex. « com.nico.watch2notif »
    option_auto_test: str = "--self-test-version"
    fenetre: str = "Hidden"          # Windows : Hidden, Minimized ou Normal
    taille_archive_max: int = maj_archive.TAILLE_ARCHIVE_MAX
    taille_extraite_max: int = maj_archive.TAILLE_EXTRAITE_MAX
    membres_max: int = maj_archive.MEMBRES_MAX
    agent: str = ""

    @property
    def agent_http(self) -> str:
        return self.agent or f"{self.nom}-updater"


@dataclass(frozen=True)
class Disposition:
    """Où est installée l'application et ce que contient son archive."""
    system: str
    machine: str
    asset_name: str
    archive_kind: str            # « zip » ou « tar »
    expected_root: str           # dossier unique de l'archive (foo, Foo.app...)
    install_root: Path
    data_relative: Path
    executable_relative: Path


@dataclass(frozen=True)
class Preparation:
    version: str
    token: str
    disposition: Disposition
    staging_root: Path
    payload_root: Path
    backup_root: Path
    failed_root: Path


def _echec(code: str, **valeurs) -> ErreurMiseAJour:
    return ErreurMiseAJour(code, **valeurs)


# --------------------------------------------------------------- disposition

def _racine_app_macos(executable: Path) -> Path:
    for candidat in (executable, *executable.parents):
        if candidat.suffix.lower() == ".app":
            try:
                relatif = executable.relative_to(candidat)
            except ValueError:
                continue
            if len(relatif.parts) >= 3 and relatif.parts[:2] == ("Contents", "MacOS"):
                return candidat
    raise _echec("unsafe_install", detail=f"application .app introuvable depuis {executable}")


def disposition(app: Application, *, asset_name: str, archive_kind: str,
                racine_attendue: str, nom_executable: Optional[str] = None,
                executable: Optional[Path] = None, systeme: Optional[str] = None,
                machine: Optional[str] = None, fige: Optional[bool] = None) -> Disposition:
    """Décrit le bundle courant et le dossier exact qui peut le remplacer.

    `asset_name`, `archive_kind` et `racine_attendue` sont ceux de ce système
    (l'application connaît ses noms d'archive). `nom_executable` : le nom de
    l'exécutable sans extension (par défaut celui de l'application).

    Refuse (unsafe_install) tout ce qui ne ressemble pas au bundle publié : un
    dossier général (Bureau, Téléchargements...) où quelqu'un aurait copié
    l'exécutable et _internal serait détruit par un remplacement."""
    if fige is None:
        fige = bool(getattr(sys, "frozen", False))
    if executable is None and not fige:
        raise _echec("source_mode")
    executable = Path(executable or sys.executable).resolve()
    systeme = systeme or platform.system()
    machine = machine or platform.machine()
    nom = nom_executable or app.nom

    if systeme == "Darwin":
        racine = _racine_app_macos(executable)
        data_relative = Path("Contents") / "MacOS"
        executable_relative = data_relative / nom
    else:
        racine = executable.parent
        data_relative = Path(".")
        executable_relative = Path(nom + ".exe" if systeme == "Windows" else nom)

    racine = racine.resolve()
    racine_systeme = Path(racine.anchor).resolve()
    try:
        accueil = Path.home().resolve()
    except (OSError, RuntimeError):
        accueil = None
    if racine == racine_systeme or (accueil is not None and racine == accueil):
        raise _echec("unsafe_install", detail=str(racine))

    if systeme != "Darwin":
        autorises = {executable_relative.name, "_internal", *app.donnees_preservees,
                     *app.noms_toleres}
        try:
            inconnus = sorted(p.name for p in racine.iterdir() if p.name not in autorises)
        except OSError as erreur:
            raise _echec("unsafe_install", detail=str(erreur)) from erreur
        if inconnus:
            raise _echec("unsafe_install", detail="contenu inconnu : " + ", ".join(inconnus[:5]))
        if not (racine / "_internal").is_dir():
            raise _echec("unsafe_install", detail="dossier _internal courant absent")

    if executable != (racine / executable_relative).resolve():
        raise _echec("unsafe_install", detail=f"exécutable inattendu : {executable}")
    return Disposition(system=systeme, machine=machine, asset_name=asset_name,
                       archive_kind=archive_kind, expected_root=racine_attendue,
                       install_root=racine, data_relative=data_relative,
                       executable_relative=executable_relative)


def possible(construire: Callable[[], Disposition]) -> tuple:
    """(vrai, "") si cette installation peut se remplacer toute seule, sinon
    (faux, code du refus). `construire` : la fonction de l'application qui
    rend sa Disposition."""
    try:
        construire()
    except ErreurMiseAJour as erreur:
        return False, erreur.code
    return True, ""


# ---------------------------------------------------------------- préparation

def _valider_bundle(app: Application, bundle: Path, disp: Disposition) -> Path:
    executable = bundle / disp.executable_relative
    if not executable.is_file() or executable.is_symlink():
        raise _echec("invalid_payload", detail=f"exécutable absent : {disp.executable_relative}")
    donnees = bundle / disp.data_relative
    for nom in app.donnees_preservees:
        if (donnees / nom).exists() or (donnees / nom).is_symlink():
            raise _echec("invalid_payload",
                         detail=f"donnée modifiable présente dans le bundle : {nom}")
    if disp.system in ("Windows", "Linux") and not (bundle / "_internal").is_dir():
        raise _echec("invalid_payload", detail="dossier _internal absent")
    if disp.system != "Windows" and not os.access(executable, os.X_OK):
        raise _echec("invalid_payload", detail="exécutable non exécutable")
    return executable


def _auto_test(app: Application, executable: Path, version: str, systeme: str) -> None:
    drapeaux = getattr(subprocess, "CREATE_NO_WINDOW", 0) if systeme == "Windows" else 0
    try:
        resultat = subprocess.run(
            [str(executable), app.option_auto_test, version], cwd=executable.parent,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False, creationflags=drapeaux)
    except (OSError, subprocess.TimeoutExpired) as erreur:
        raise _echec("invalid_payload", detail=f"auto-test impossible : {erreur}") from erreur
    if resultat.returncode != 0:
        raise _echec("invalid_payload", detail=f"auto-test échoué (code {resultat.returncode})")


def preparer(app: Application, info: dict, depot: str, disp: Disposition, *,
             ouvrir: Callable = urllib.request.urlopen, auto_test: bool = True,
             contexte=None, progression: Optional[Callable[[int, int], None]] = None
             ) -> Preparation:
    """Télécharge, vérifie et extrait le bundle sans toucher à l'installation.
    `info` : {"version", "assets"} tel que le rend maj.Verificateur.disponible()."""
    try:
        asset = maj_archive.choisir_asset(info.get("assets"), disp.asset_name, depot,
                                          taille_max=app.taille_archive_max)
        version = str(info.get("version") or "")
        if not version:
            raise _echec("version_absente")

        jeton = uuid.uuid4().hex
        racine = disp.install_root
        staging = None
        try:
            staging = Path(tempfile.mkdtemp(prefix=f".{racine.name}.update-",
                                            dir=racine.parent)).resolve()
            archive = staging / ("download.tar.gz" if disp.archive_kind == "tar" else "download.zip")
            morceaux = urllib.parse.urlparse(asset["browser_download_url"]).path.split("/")
            maj_archive.telecharger(
                asset["browser_download_url"], archive, asset["size"], asset["digest"],
                depot="/".join(morceaux[1:3]), agent=app.agent_http, nom=asset["name"],
                ouvrir=ouvrir, contexte=contexte, progression=progression,
                taille_max=app.taille_archive_max, delai_s=30)
            bundle = maj_archive.extraire(
                archive, staging / "extracted", racine=disp.expected_root,
                taille_max=app.taille_extraite_max, membres_max=app.membres_max)
            executable = _valider_bundle(app, bundle, disp)
            if auto_test:
                _auto_test(app, executable, version, disp.system)
            sauvegarde = racine.parent / f".{racine.name}.backup-{jeton}"
            echec = racine.parent / f".{racine.name}.failed-{jeton}"
            if sauvegarde.exists() or echec.exists():
                raise _echec("unsafe_install", detail="chemin de transaction déjà présent")
            return Preparation(version=version, token=jeton, disposition=disp,
                               staging_root=staging, payload_root=bundle,
                               backup_root=sauvegarde, failed_root=echec)
        except BaseException:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            raise
    except ErreurMiseAJour:
        raise
    except OSError as erreur:
        raise _echec("prepare_failed", detail=str(erreur)) from erreur


def nettoyer(prep: Preparation) -> None:
    staging = prep.staging_root.resolve()
    parent = prep.disposition.install_root.parent.resolve()
    if staging.parent == parent and staging.name.startswith(
            f".{prep.disposition.install_root.name}.update-"):
        shutil.rmtree(staging, ignore_errors=True)


# --------------------------------------------------------------- assistants

_ASSISTANT_WINDOWS = r'''param(
    [int]$ParentPid,
    [string]$Current,
    [string]$Payload,
    [string]$Staging,
    [string]$Backup,
    [string]$Failed,
    [string]$DataRelative,
    [string]$ExecutableRelative,
    [string]$ReadyFile,
    [string]$GoFile,
    [string]$LogFile,
    [string]$Preserved,
    [string]$RelaunchFile,
    [string]$WindowStyle
)
$ErrorActionPreference = "Stop"
function Write-UpdateLog([string]$Message) {
    try { Add-Content -LiteralPath $LogFile -Value ((Get-Date -Format o) + " " + $Message) -Encoding UTF8 } catch {}
}
function Quote-Argument([string]$Argument) {
    if ($Argument -match '[\s"]') { return '"' + ($Argument -replace '"', '\"') + '"' }
    return $Argument
}
function Start-App([string]$Root) {
    # Un processus par ligne du fichier de relance (arguments séparés par une
    # tabulation) ; sans fichier, un seul processus sans argument. Le premier
    # est celui dont on vérifie qu'il reste en vie.
    $exe = Join-Path $Root $ExecutableRelative
    $lines = @()
    if ($RelaunchFile -and (Test-Path -LiteralPath $RelaunchFile)) {
        $lines = @(Get-Content -LiteralPath $RelaunchFile -Encoding UTF8)
    }
    if ($lines.Count -eq 0) { $lines = @("") }
    $first = $null
    foreach ($line in $lines) {
        $params = @{ FilePath = $exe; WorkingDirectory = (Split-Path -Parent $exe); WindowStyle = $WindowStyle; PassThru = $true }
        $list = @($line -split "`t" | Where-Object { $_ } | ForEach-Object { Quote-Argument $_ })
        if ($list.Count -gt 0) { $params.ArgumentList = $list }
        $process = Start-Process @params
        if ($null -eq $first) { $first = $process }
    }
    return $first
}
function Copy-UserData([string]$OldRoot, [string]$NewRoot) {
    $oldData = Join-Path $OldRoot $DataRelative
    $newData = Join-Path $NewRoot $DataRelative
    New-Item -ItemType Directory -Force -Path $newData | Out-Null
    foreach ($name in @($Preserved -split '\|' | Where-Object { $_ })) {
        $source = Join-Path $oldData $name
        $target = Join-Path $newData $name
        if (Test-Path -LiteralPath $source -PathType Container) {
            if (Test-Path -LiteralPath $target) { throw "candidate unexpectedly contains $name" }
            Copy-Item -LiteralPath $source -Destination $target -Recurse -Force
        } elseif (Test-Path -LiteralPath $source -PathType Leaf) {
            Copy-Item -LiteralPath $source -Destination $target -Force
        }
    }
}
function Restore-OldVersion {
    try {
        if (Test-Path -LiteralPath $Current) {
            if (Test-Path -LiteralPath $Failed) { throw "failed destination already exists" }
            Move-Item -LiteralPath $Current -Destination $Failed
        }
        if (Test-Path -LiteralPath $Backup) {
            if (Test-Path -LiteralPath $Current) { throw "current path still exists during rollback" }
            Move-Item -LiteralPath $Backup -Destination $Current
            Start-App $Current | Out-Null
        }
    } catch { Write-UpdateLog ("rollback failed: " + $_.Exception.Message) }
}

Write-UpdateLog ("helper started (pid " + $PID + ")")
New-Item -ItemType File -Force -Path $ReadyFile | Out-Null
Write-UpdateLog "ready file written, waiting for go"
$readyDeadline = (Get-Date).AddSeconds(60)
while (-not (Test-Path -LiteralPath $GoFile)) {
    if (Test-Path -LiteralPath (Join-Path $Staging "helper.abort")) {
        Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
        exit 8
    }
    if ((Get-Date) -gt $readyDeadline) { Write-UpdateLog "update was not committed"; exit 9 }
    Start-Sleep -Milliseconds 100
}
if (Test-Path -LiteralPath (Join-Path $Staging "helper.abort")) {
    Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
    exit 8
}
New-Item -ItemType File -Force -Path ($GoFile + ".ack") | Out-Null
Write-UpdateLog "go received, ack written, waiting for parent to exit"
Start-Sleep -Milliseconds 500
try {
    $parentDeadline = (Get-Date).AddMinutes(5)
    while (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        if ((Get-Date) -gt $parentDeadline) { Write-UpdateLog "parent did not exit"; exit 10 }
        Start-Sleep -Milliseconds 250
    }
    if ((Test-Path -LiteralPath $Backup) -or (Test-Path -LiteralPath $Failed)) {
        Write-UpdateLog "transaction destination appeared unexpectedly"
        Start-App $Current | Out-Null
        exit 11
    }
    Write-UpdateLog "parent exited, starting swap"
    Move-Item -LiteralPath $Current -Destination $Backup
    try {
        Move-Item -LiteralPath $Payload -Destination $Current
        Copy-UserData $Backup $Current
    } catch {
        Write-UpdateLog ("swap failed: " + $_.Exception.Message)
        Restore-OldVersion
        exit 2
    }

    try {
        $newProcess = Start-App $Current
        Start-Sleep -Seconds 5
        if ($newProcess.HasExited) { throw "new process exited too early" }
    } catch {
        Write-UpdateLog ("restart failed: " + $_.Exception.Message)
        Restore-OldVersion
        exit 3
    }

    Write-UpdateLog "update succeeded, new version running"
    Remove-Item -LiteralPath $Backup -Recurse -Force
    Remove-Item -LiteralPath $Staging -Recurse -Force
    exit 0
} catch {
    Write-UpdateLog ("update failed: " + $_.Exception.Message)
    if ((-not (Test-Path -LiteralPath $Current)) -and (Test-Path -LiteralPath $Backup)) {
        try { Move-Item -LiteralPath $Backup -Destination $Current; Start-App $Current | Out-Null } catch {}
    } elseif ((Test-Path -LiteralPath $Current) -and (-not (Test-Path -LiteralPath $Backup))) {
        try { Start-App $Current | Out-Null } catch {}
    }
    exit 1
}
'''


_ASSISTANT_POSIX = r'''#!/bin/sh
set -f
parent_pid=$1
current=$2
payload=$3
staging=$4
backup=$5
failed=$6
data_relative=$7
executable_relative=$8
system_name=$9
ready_file=${10}
go_file=${11}
log_file=${12}
service_file=${13}
mac_plist=${14}
service_unit=${15}
launchd_label=${16}
preserved=${17}
relaunch_file=${18}

write_log() {
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" >> "$log_file" 2>/dev/null || true
}
copy_user_data() {
    old_data=$backup/$data_relative
    new_data=$current/$data_relative
    mkdir -p "$new_data" || return 1
    old_ifs=$IFS
    IFS='|'
    for name in $preserved; do
        IFS=$old_ifs
        [ -n "$name" ] || continue
        if [ -d "$old_data/$name" ]; then
            [ ! -e "$new_data/$name" ] || return 1
            cp -R -p "$old_data/$name" "$new_data/$name" || return 1
        elif [ -f "$old_data/$name" ]; then
            cp -p "$old_data/$name" "$new_data/$name" || return 1
        fi
    done
    IFS=$old_ifs
    return 0
}
use_launchd() {
    [ "$system_name" = "Darwin" ] && [ -n "$launchd_label" ] && [ -f "$mac_plist" ]
}
use_systemd() {
    [ "$system_name" = "Linux" ] && [ -n "$service_unit" ] && [ -f "$service_file" ]
}
start_version() {
    root=$1
    exe=$root/$executable_relative
    if use_launchd; then
        launchctl load "$mac_plist" >/dev/null 2>&1 || return 1
        sleep 5
        launchctl list "$launchd_label" 2>/dev/null | /usr/bin/grep -Eq '"PID"[[:space:]]*=[[:space:]]*[0-9]+'
        return $?
    fi
    if use_systemd; then
        transition_waited=0
        while :; do
            service_state=$(systemctl --user show "$service_unit" --property=ActiveState --value 2>/dev/null || true)
            [ "$service_state" != "activating" ] && [ "$service_state" != "deactivating" ] && break
            sleep 1
            transition_waited=$((transition_waited + 1))
            [ "$transition_waited" -lt 30 ] || return 1
        done
        systemctl --user reset-failed "$service_unit" >/dev/null 2>&1 || true
        systemctl --user start "$service_unit" >/dev/null 2>&1 || return 1
        sleep 5
        systemctl --user is-active --quiet "$service_unit"
        return $?
    fi
    working_dir=$(dirname "$exe")
    # Un processus par ligne du fichier de relance (arguments séparés par une
    # tabulation) ; sans fichier, un seul processus sans argument. Le premier
    # est celui dont on vérifie qu'il reste en vie.
    first_pid=""
    if [ -n "$relaunch_file" ] && [ -s "$relaunch_file" ]; then
        tab=$(printf '\t')
        while IFS= read -r line || [ -n "$line" ]; do
            old_ifs=$IFS
            IFS=$tab
            set -- $line
            IFS=$old_ifs
            (cd "$working_dir" && exec "$exe" "$@" </dev/null >/dev/null 2>&1) &
            [ -n "$first_pid" ] || first_pid=$!
        done < "$relaunch_file"
    else
        (cd "$working_dir" && exec "$exe" </dev/null >/dev/null 2>&1) &
        first_pid=$!
    fi
    sleep 5
    kill -0 "$first_pid" 2>/dev/null
}
restore_old() {
    if use_launchd; then
        launchctl unload "$mac_plist" >/dev/null 2>&1 || true
    fi
    if use_systemd; then
        systemctl --user stop "$service_unit" >/dev/null 2>&1 || true
    fi
    if [ -e "$current" ]; then
        [ ! -e "$failed" ] || return 1
        mv "$current" "$failed" 2>/dev/null || return 1
    fi
    if [ -e "$backup" ]; then
        [ ! -e "$current" ] || return 1
        mv "$backup" "$current" 2>/dev/null || return 1
        start_version "$current" || true
    fi
}

write_log "helper started (pid $$)"
: > "$ready_file" || exit 10
write_log "ready file written, waiting for go"
waited=0
while [ ! -e "$go_file" ]; do
    if [ -e "$staging/helper.abort" ]; then
        rm -rf "$staging"
        exit 8
    fi
    sleep 1
    waited=$((waited + 1))
    if [ "$waited" -ge 60 ]; then
        write_log "update was not committed"
        exit 9
    fi
done
if [ -e "$staging/helper.abort" ]; then
    rm -rf "$staging"
    exit 8
fi
mac_unloaded=0
if use_launchd; then
    if launchctl list "$launchd_label" >/dev/null 2>&1; then
        if launchctl unload "$mac_plist" >/dev/null 2>&1; then
            mac_unloaded=1
        else
            write_log "could not unload LaunchAgent"
            exit 10
        fi
    fi
fi
: > "$go_file.ack" || exit 10
write_log "go received, ack written, waiting for parent to exit"
sleep 1

waited=0
while kill -0 "$parent_pid" 2>/dev/null; do
    sleep 1
    waited=$((waited + 1))
    if [ "$waited" -ge 300 ]; then
        write_log "parent did not exit"
        if [ "$mac_unloaded" -eq 1 ]; then launchctl load "$mac_plist" >/dev/null 2>&1 || true; fi
        exit 11
    fi
done

if [ -e "$backup" ] || [ -L "$backup" ] || [ -e "$failed" ] || [ -L "$failed" ]; then
    write_log "transaction destination appeared unexpectedly"
    start_version "$current" || true
    exit 12
fi

write_log "parent exited, starting swap"
if ! mv "$current" "$backup"; then
    write_log "could not move current installation"
    start_version "$current" || true
    exit 12
fi
if ! mv "$payload" "$current"; then
    write_log "could not install candidate"
    restore_old
    exit 13
fi
if ! copy_user_data; then
    write_log "could not preserve user data"
    restore_old
    exit 14
fi
if ! start_version "$current"; then
    write_log "new version did not stay running"
    restore_old
    exit 15
fi

write_log "update succeeded, new version running"
rm -rf "$backup"
rm -rf "$staging"
exit 0
'''


def _chemins_valides(prep: Preparation) -> None:
    courant = prep.disposition.install_root.resolve()
    parent = courant.parent
    staging = prep.staging_root.resolve()
    bundle = prep.payload_root.resolve()
    sauvegarde = prep.backup_root.resolve()
    echec = prep.failed_root.resolve()
    if not courant.is_dir() or courant == Path(courant.anchor).resolve():
        raise _echec("unsafe_install", detail=str(courant))
    if staging.parent != parent or not staging.name.startswith(f".{courant.name}.update-"):
        raise _echec("unsafe_install", detail=str(staging))
    if staging not in bundle.parents or not bundle.is_dir():
        raise _echec("unsafe_install", detail=str(bundle))
    if sauvegarde.parent != parent or echec.parent != parent:
        raise _echec("unsafe_install", detail="sauvegarde hors du dossier attendu")
    if sauvegarde.exists() or echec.exists():
        raise _echec("unsafe_install", detail="sauvegarde déjà présente")


def _ecrire_assistant(app: Application, contenu: str, suffixe: str) -> Path:
    descripteur, nom = tempfile.mkstemp(prefix=f"{app.nom}-updater-", suffix=suffixe)
    assistant = Path(nom)
    try:
        with os.fdopen(descripteur, "w", encoding="utf-8", newline="\n") as flux:
            flux.write(contenu)
        if os.name != "nt":
            assistant.chmod(0o700)
    except Exception:
        assistant.unlink(missing_ok=True)
        raise
    return assistant


def _preparer_agent_macos(app: Application, plist: Optional[Path]) -> None:
    """Empêche launchd de relancer l'ancien bundle pendant l'échange. Le job
    chargé garde son ancienne définition jusqu'au `unload` de l'assistant ; le
    `load` qui suit le remplacement prend cette définition corrigée."""
    if not app.label_launchd or plist is None or not plist.is_file():
        return
    try:
        donnees = plistlib.loads(plist.read_bytes())
        if donnees.get("Label") != app.label_launchd:
            raise _echec("helper_failed", detail=f"LaunchAgent {app.nom} invalide")
        donnees["KeepAlive"] = {"SuccessfulExit": False}
        temporaire = plist.with_suffix(plist.suffix + ".update.tmp")
        temporaire.write_bytes(plistlib.dumps(donnees, fmt=plistlib.FMT_XML, sort_keys=False))
        os.replace(temporaire, plist)
    except ErreurMiseAJour:
        raise
    except (OSError, ValueError, TypeError) as erreur:
        raise _echec("helper_failed", detail=f"LaunchAgent : {erreur}") from erreur


def processus_a_relancer(app: Application,
                         verbes: Optional[Sequence[Sequence[str]]] = None) -> list:
    """Les processus à relancer après la mise à jour, chacun la liste de ses
    arguments : `verbes` s'il est donné (ce qui tournait, noté à l'instant de la
    mise à jour), sinon verbes_relance de l'application, sinon un seul processus
    avec arguments_relance."""
    if verbes is not None:
        return [[str(a) for a in v] for v in verbes]
    if app.verbes_relance:
        return [[str(a) for a in v] for v in app.verbes_relance]
    return [[str(a) for a in app.arguments_relance]]


def _ecrire_relance(dossier: Path, processus: Sequence[Sequence[str]]) -> Path:
    """Le fichier de relance lu par l'assistant : un processus par ligne, ses
    arguments séparés par une tabulation, fins de ligne « \n » même sous Windows.
    open() et non Path.write_text(newline=...) : ce paramètre n'existe qu'à partir
    de Python 3.10, et l'édition Windows 7 est en 3.8."""
    fichier = Path(dossier) / "relance.txt"
    with fichier.open("w", encoding="utf-8", newline="\n") as flux:
        flux.write("\n".join("\t".join(ligne) for ligne in processus) + "\n")
    return fichier


def lancer(app: Application, prep: Preparation, *, ecrire: Callable[[str], None] = print,
           verbes: Optional[Sequence[Sequence[str]]] = None) -> None:
    """Démarre l'assistant, vérifie qu'il est prêt, puis rend la main à
    l'appelant, qui doit ensuite appeler valider() et se fermer.

    `verbes` : les processus à relancer, un par entrée, chacune la liste de ses
    arguments (voir processus_a_relancer)."""
    _chemins_valides(prep)
    if any("|" in nom for nom in app.donnees_preservees):
        # « | » sépare les noms de données passés à l'assistant.
        raise _echec("helper_failed", detail="nom de donnée avec « | »")
    processus = processus_a_relancer(app, verbes)
    if any(c in argument for ligne in processus for argument in ligne for c in "\t\r\n"):
        # Tabulation et fin de ligne séparent les arguments et les processus du
        # fichier de relance lu par l'assistant.
        raise _echec("helper_failed", detail="argument de relance avec tabulation ou fin de ligne")
    disp = prep.disposition
    pret = prep.staging_root / "helper.ready"
    feu_vert = prep.staging_root / "helper.go"
    journal = prep.staging_root / "update-helper.log"
    accueil = Path.home()
    fichier_service = str(accueil / ".config" / "systemd" / "user" / app.unite_systemd
                          if app.unite_systemd else "")
    plist = (accueil / "Library" / "LaunchAgents" / f"{app.label_launchd}.plist"
             if app.label_launchd else None)
    if disp.system == "Darwin":
        _preparer_agent_macos(app, plist)

    commun = [str(os.getpid()), str(disp.install_root), str(prep.payload_root),
              str(prep.staging_root), str(prep.backup_root), str(prep.failed_root),
              str(disp.data_relative), str(disp.executable_relative)]
    preserves = "|".join(app.donnees_preservees)
    try:
        relance = str(_ecrire_relance(prep.staging_root, processus))
    except OSError as erreur:
        raise _echec("helper_failed", detail=str(erreur)) from erreur

    try:
        if disp.system == "Windows":
            assistant = _ecrire_assistant(app, _ASSISTANT_WINDOWS, ".ps1")
            commande = ["powershell.exe", "-NoProfile", "-NonInteractive",
                        "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                        "-File", str(assistant),
                        "-ParentPid", commun[0], "-Current", commun[1], "-Payload", commun[2],
                        "-Staging", commun[3], "-Backup", commun[4], "-Failed", commun[5],
                        "-DataRelative", commun[6], "-ExecutableRelative", commun[7],
                        "-ReadyFile", str(pret), "-GoFile", str(feu_vert),
                        "-LogFile", str(journal), "-Preserved", preserves,
                        "-RelaunchFile", relance, "-WindowStyle", app.fenetre]
            # CREATE_NO_WINDOW seul : une console est bien créée, juste invisible,
            # contrairement à DETACHED_PROCESS (aucune console du tout) qui rendait
            # ce lancement de PowerShell erratique (parfois 15 s à démarrer, parfois
            # un retour immédiat sans que le script ait rien exécuté ; constaté en
            # réel le 2026-09-07).
            processus = subprocess.Popen(
                commande, cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)
        else:
            assistant = _ecrire_assistant(app, _ASSISTANT_POSIX, ".sh")
            commande = ["/bin/sh", str(assistant), *commun, disp.system, str(pret),
                        str(feu_vert), str(journal), fichier_service, str(plist or ""),
                        app.unite_systemd, app.label_launchd, preserves, relance]
            if (disp.system == "Linux" and os.environ.get("INVOCATION_ID")
                    and shutil.which("systemd-run")):
                # Sous un service systemd, l'assistant doit sortir du cgroup du
                # service, sinon l'arrêt du service le tue avec son parent.
                unite = f"{app.nom}-update-{prep.token[:12]}"
                resultat = subprocess.run(
                    ["systemd-run", "--user", "--collect", f"--unit={unite}", *commande],
                    cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=15, check=False)
                if resultat.returncode != 0:
                    raise _echec("helper_failed", detail=f"systemd-run : {resultat.returncode}")
                processus = None
            else:
                processus = subprocess.Popen(
                    commande, cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True, close_fds=True)

        # 25 s et non 8 : sous Windows, le premier lancement d'un script PowerShell
        # inédit (nouveau fichier temporaire à chaque mise à jour) déclenche un
        # scan AMSI/Defender qui peut à lui seul dépasser 8 s avant la première
        # ligne du script (constaté en réel le 2026-09-07).
        debut = time.monotonic()
        limite = debut + ARRET_PRET_S
        while time.monotonic() < limite:
            if pret.exists():
                ecrire(f"[maj] assistant prêt après {time.monotonic() - debut:.1f}s")
                return
            if processus is not None and processus.poll() is not None:
                ecrire(f"[maj] l'assistant s'est terminé (code {processus.returncode}) "
                       f"après {time.monotonic() - debut:.1f}s sans écrire helper.ready")
                break
            time.sleep(0.1)
        ecrire(f"[maj] pas de confirmation de l'assistant après {time.monotonic() - debut:.1f}s")
        raise _echec("helper_failed", detail="l'assistant n'a pas confirmé son démarrage")
    except ErreurMiseAJour:
        raise
    except OSError as erreur:
        raise _echec("helper_failed", detail=str(erreur)) from erreur


def valider(prep: Preparation, *, ecrire: Callable[[str], None] = print) -> None:
    """Donne le feu vert à l'assistant prêt : à appeler quand l'application est
    sur le point de se fermer. Sans accusé de réception, la transaction est
    annulée."""
    _chemins_valides(prep)
    pret = prep.staging_root / "helper.ready"
    if not pret.is_file():
        raise _echec("helper_failed", detail="l'assistant n'est plus prêt")
    try:
        feu_vert = prep.staging_root / "helper.go"
        feu_vert.touch(exist_ok=False)
    except OSError as erreur:
        raise _echec("helper_failed", detail=str(erreur)) from erreur
    debut = time.monotonic()
    accuse = Path(str(feu_vert) + ".ack")
    while time.monotonic() < debut + ARRET_ACCUSE_S:
        if accuse.is_file():
            ecrire(f"[maj] transaction confirmée par l'assistant après {time.monotonic() - debut:.1f}s")
            return
        time.sleep(0.05)
    ecrire(f"[maj] pas d'accusé de réception de l'assistant après {time.monotonic() - debut:.1f}s")
    annuler(prep)
    raise _echec("helper_failed", detail="l'assistant n'a pas confirmé la transaction")


def annuler(prep: Preparation) -> None:
    """Demande à un assistant en attente de renoncer et de nettoyer son dossier."""
    try:
        (prep.staging_root / "helper.abort").touch(exist_ok=True)
    except OSError:
        pass


class Installateur:
    """Conduit une installation de bout en bout dans un fil de fond et en tient
    l'état à jour, pour que le menu de l'icône et la page de l'application
    (qui l'interrogent par etat()) n'aient rien d'autre à faire : un bouton
    appelle demarrer(), un sondage lit etat().

    Séquence, la même pour toutes les applications : rafraîchir la release,
    préparer (téléchargement, extraction, contrôles), lancer l'assistant,
    lui donner le feu vert, puis appeler `quitter()` (l'application s'arrête
    proprement : l'assistant échange alors les dossiers et relance la nouvelle
    version). À chaque étape qui échoue, ce qui avait été préparé est nettoyé,
    l'état passe à « erreur » et l'application continue de tourner.

    États : inactif, telechargement, redemarrage, erreur."""

    def __init__(self, app: Application, depot: str, verificateur,
                 construire: Callable[[], Disposition], *, quitter: Callable[[], None],
                 ouvrir: Callable = urllib.request.urlopen, contexte=None,
                 ecrire: Callable[[str], None] = print,
                 verbes_relance: Optional[Callable[[], Sequence[Sequence[str]]]] = None):
        self.app = app
        self.verbes_relance = verbes_relance
        self.depot = depot
        self.verificateur = verificateur
        self.construire = construire
        self.quitter = quitter
        self.ouvrir = ouvrir
        self.contexte = contexte
        self.ecrire = ecrire
        self._verrou = threading.Lock()
        self._etat = {"etat": "inactif", "version": None, "recu": 0, "total": 0, "erreur": None}
        self._fil: Optional[threading.Thread] = None

    def possible(self) -> tuple:
        """(vrai, "") si cette installation peut se remplacer toute seule, sinon
        (faux, code du refus : source_mode, unsafe_install...)."""
        return possible(self.construire)

    def etat(self) -> dict:
        with self._verrou:
            return dict(self._etat)

    def _fixer(self, **valeurs) -> None:
        with self._verrou:
            self._etat.update(valeurs)

    def _echec(self, erreur: BaseException) -> None:
        if isinstance(erreur, ErreurMiseAJour):
            detail = {"code": erreur.code, "categorie": erreur.categorie,
                      "message": {"fr": erreur.message("fr"), "en": erreur.message("en")}}
        else:
            detail = {"code": "prepare_failed", "categorie": "prepare_failed",
                      "message": {"fr": str(erreur), "en": str(erreur)}}
        self.ecrire(f"[maj] échec de l'installation : {detail['message']['fr']}")
        self._fixer(etat="erreur", erreur=detail)

    def demarrer(self) -> bool:
        """Lance l'installation. Faux si elle est déjà en cours."""
        with self._verrou:
            if self._etat["etat"] in ("telechargement", "redemarrage"):
                return False
            self._etat.update(etat="telechargement", version=None, recu=0, total=0, erreur=None)
        self._fil = threading.Thread(target=self._travailler, name="installation-maj",
                                     daemon=True)
        self._fil.start()
        return True

    def attendre(self, delai_s: float = 60) -> None:
        """Pour les tests : attend la fin du fil."""
        fil = self._fil
        if fil is not None:
            fil.join(delai_s)

    def _travailler(self) -> None:
        prep = None
        try:
            self.verificateur.verifier()
            info = self.verificateur.disponible()
            if not info:
                raise _echec("asset_absent", nom="release")
            self._fixer(version=info["version"])
            disp = self.construire()

            def progression(recu: int, total: int) -> None:
                self._fixer(recu=recu, total=total)

            prep = preparer(self.app, info, self.depot, disp, ouvrir=self.ouvrir,
                            contexte=self.contexte, progression=progression)
            # Ce qui tourne est noté à l'instant de la mise à jour, pas avant.
            verbes = self.verbes_relance() if self.verbes_relance is not None else None
            lancer(self.app, prep, ecrire=self.ecrire, verbes=verbes)
            valider(prep, ecrire=self.ecrire)
        except Exception as erreur:
            if prep is not None:
                # Un assistant déjà lancé renonce et nettoie ; sinon on nettoie ici.
                annuler(prep)
                nettoyer(prep)
            self._echec(erreur)
            return
        self._fixer(etat="redemarrage")
        self.ecrire(f"[maj] version {prep.version} installée, arrêt pour laisser la main à l'assistant")
        self.quitter()


# ------------------------------------------------- convention des archives

def archive_standard(nom: str, *, racine_macos: str, systeme: Optional[str] = None,
                     machine: Optional[str] = None) -> tuple:
    """(fichier, genre, racine) de l'archive de ce système selon la convention de
    gpxsolar et lidar2map : « <nom>-<windows|linux|macos>-<x86_64|arm64> » en
    .zip (.tar.gz sous Linux), qui contient un dossier du même nom, ou le
    bundle `racine_macos` (« GPXSOLAR.app ») sous macOS. Windows et Linux
    n'existent qu'en x86_64 ; macOS en arm64 et x86_64."""
    systeme = systeme or platform.system()
    machine = (machine or platform.machine()).lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if systeme == "Windows" and arch == "x86_64":
        base = f"{nom}-windows-x86_64"
        return base + ".zip", "zip", base
    if systeme == "Linux" and arch == "x86_64":
        base = f"{nom}-linux-x86_64"
        return base + ".tar.gz", "tar", base
    if systeme == "Darwin" and arch:
        return f"{nom}-macos-{arch}.zip", "zip", racine_macos
    raise _echec("unsupported_target", cible=f"{systeme}/{machine}")


# ------------------------------------------------ routes pour la page de l'application

# Deux « Vérifier les mises à jour » plus rapprochés que cela ne font qu'une question.
DELAI_ENTRE_VERIFICATIONS_S = 5.0

LIBELLES_BANDEAU = {
    "fr": {
        "disponible": "Version {version} disponible.",
        "installer": "Installer",
        "voir": "Voir la version",
        "telechargement": "Téléchargement de la mise à jour…",
        "redemarrage": "Mise à jour installée, redémarrage…",
        "erreur": "La mise à jour a échoué :",
        "reessayer": "Réessayer",
        "fermer": "Fermer",
        "reglages": "⚙ Réglages…",
        "reglages_titre": "Réglages",
        "version": "Version {version}",
        "verifier": "Vérifier les mises à jour",
        "verification": "Vérification…",
        "a_jour": "Vous avez la dernière version.",
        "echec_verification": "Vérification impossible (réseau ?).",
        "derniere_verification": "Dernière vérification : {heure}",
    },
    "en": {
        "disponible": "Version {version} available.",
        "installer": "Install",
        "voir": "View the release",
        "telechargement": "Downloading the update…",
        "redemarrage": "Update installed, restarting…",
        "erreur": "The update failed:",
        "reessayer": "Retry",
        "fermer": "Close",
        "reglages": "⚙ Settings…",
        "reglages_titre": "Settings",
        "version": "Version {version}",
        "verifier": "Check for updates",
        "verification": "Checking…",
        "a_jour": "You have the latest version.",
        "echec_verification": "Could not check (network?).",
        "derniere_verification": "Last checked: {heure}",
    },
}


def routes(installateur: Installateur, langue: Callable[[], str] = lambda: "fr") -> tuple:
    """(routes GET, routes POST) à ajouter à celles de l'application pour que le
    bandeau (maj_banniere.js, servi par serveweb) fonctionne : /api/maj rend la
    version, l'état de l'installation et les textes dans la langue de
    l'application ; /api/maj-installer la démarre."""
    def lire_langue() -> str:
        valeur = (langue() or "fr")[:2].lower()
        return valeur if valeur in LIBELLES_BANDEAU else "en"

    def etat() -> dict:
        lg = lire_langue()
        verificateur = installateur.verificateur
        info = verificateur.disponible()
        possible_, raison = installateur.possible()
        courant = installateur.etat()
        erreur = courant["erreur"]
        if erreur:
            erreur = dict(erreur, message=erreur["message"].get(lg) or erreur["message"]["fr"])
        return {"version": info["version"] if info else None,
                "version_locale": verificateur.version_locale,
                "verifie_a": verificateur.verifie_a,
                "page": (info or {}).get("page") or verificateur.page_des_releases,
                "possible": possible_, "raison": raison,
                "etat": dict(courant, erreur=erreur), "libelles": LIBELLES_BANDEAU[lg],
                # Toutes les langues : la page change de langue sans recharger, et le bouton
                # Réglages (reglages.js) doit suivre sans interroger le serveur à nouveau.
                "libelles_par_langue": LIBELLES_BANDEAU}

    def installer(_payload) -> dict:
        return {"ok": installateur.demarrer()}

    derniere_question = [0.0]

    def verifier(_payload) -> dict:
        """« Vérifier les mises à jour » du panneau Réglages : interroge GitHub tout
        de suite. Deux clics rapprochés n'en font qu'une question (l'API publique en
        accorde soixante par heure)."""
        maintenant = time.monotonic()
        if maintenant - derniere_question[0] < DELAI_ENTRE_VERIFICATIONS_S:
            ok = installateur.verificateur.verifie_a is not None
        else:
            derniere_question[0] = maintenant
            ok = installateur.verificateur.verifier()
        return dict(etat(), ok=ok)

    return {"maj": etat}, {"maj-installer": installer, "maj-verifier": verifier}


# ======================================================================
# Remplacement élément par élément, avec reprise après coupure
# ======================================================================
#
# L'autre façon de remplacer une installation, celle de blink2video : au lieu
# d'un assistant externe qui échange le dossier entier, la NOUVELLE version
# (lancée depuis son dossier de préparation) arrête l'ancienne, copie ses
# fichiers un à un à la place des anciens et relance. Elle ne demande aucun droit
# sur le dossier parent, elle sait relancer plusieurs processus (chaque
# composition qui tournait), et un marqueur écrit AVANT la première modification
# permet de reprendre, ou de refuser de purger la seule sauvegarde, après un arrêt
# brutal. Les deux stratégies partagent la préparation (preparer) ; l'application
# choisit celle qui convient à la façon dont elle est livrée et s'exécute.

MARQUEUR_PERMUTATION = ".maj_permutation.json"
NOM_RESERVATION = ".maj-installation"

LIBELLES_PERMUTATION = {'fr': {'permutation_non_finalisee': 'Une permutation non finalisée subsiste : {marqueur}. '
                                     'Sauvegardes .ancien conservées ; réparation nécessaire. '
                                     "Si l'application démarre et fonctionne (par exemple après "
                                     'une réinstallation à la main), supprimez ce fichier : la '
                                     'prochaine mise à jour effacera elle-même les « .ancien ».',
        'permutation_preparation_interrompue': 'Préparation de permutation interrompue : '
                                               '{marqueur}. Aucun remplacement autorisé avant '
                                               'vérification.',
        'permutation_non_demarree': 'Permutation non démarrée : {erreur}',
        'echec_remplacement': 'Échec du remplacement ({erreur}). Retour à la version précédente.',
        'restauration_incomplete': 'Restauration incomplète ; aucune relance ni nouvelle '
                                   'tentative. Conserver {marqueur} et les sauvegardes .ancien. '
                                   '{echecs}',
        'maj_precedente_non_finalisee': 'Mise à jour précédente non finalisée : sauvegardes et '
                                        'préparation conservées. Si l\'application démarre et '
                                        'fonctionne (par exemple après une réinstallation à la '
                                        'main), supprimez le fichier {marqueur} : la prochaine '
                                        'mise à jour effacera elle-même les « .ancien ».',
        'arret_version_en_place': 'Arrêt de la version en place…',
        'arret_echoue': "Mise à jour interrompue : la commande d'arrêt a échoué.",
        'instance_encore_active': 'Mise à jour interrompue : une instance est encore active.',
        'version_precedente_intacte': "La version précédente est intacte : rien n'a été remplacé.",
        'installe_dans': 'Installé dans {installe}',
        'installation_non_reservee': 'Installation non réservée ; aucun remplacement ni nettoyage '
                                     'autorisé ({erreur}).',
        'brut': '{texte}'},
 'en': {'permutation_non_finalisee': 'An unfinished swap remains: {marqueur}. .ancien backups kept; '
                                     'repair needed. If the application starts and works (for '
                                     'example after reinstalling it by hand), delete this file: the '
                                     'next update will clear the ".ancien" backups itself.',
        'permutation_preparation_interrompue': 'Swap preparation interrupted: {marqueur}. No '
                                               'replacement allowed before verification.',
        'permutation_non_demarree': 'Swap not started: {erreur}',
        'echec_remplacement': 'Replacement failed ({erreur}). Reverting to the previous version.',
        'restauration_incomplete': 'Incomplete restoration; no relaunch or further attempt. Keep '
                                   '{marqueur} and the .old backups. {echecs}',
        'maj_precedente_non_finalisee': 'Previous update not finalized: backups and preparation '
                                        'kept. If the application starts and works (for example '
                                        'after reinstalling it by hand), delete the file {marqueur}: the '
                                        'next update will clear the ".ancien" backups itself.',
        'arret_version_en_place': 'Stopping the current version…',
        'arret_echoue': 'Update interrupted: the stop command failed.',
        'instance_encore_active': 'Update interrupted: an instance is still active.',
        'version_precedente_intacte': 'The previous version is intact: nothing was replaced.',
        'installe_dans': 'Installed in {installe}',
        'installation_non_reservee': 'Installation not reserved; no replacement or cleanup allowed '
                                     '({erreur}).',
        'brut': '{texte}'}}


def texte(cle: str, langue: str = "fr", **valeurs) -> str:
    """Message de la permutation dans la langue demandée (anglais à défaut)."""
    modele = LIBELLES_PERMUTATION.get(langue, LIBELLES_PERMUTATION["en"]).get(cle) or cle
    try:
        return modele.format(**valeurs)
    except (KeyError, IndexError):
        return modele


class RestaurationIncomplete(RuntimeError):
    """La sauvegarde doit rester intacte jusqu'à une réparation explicite."""


@contextlib.contextmanager
def reservation(installe: Path, nom: str = NOM_RESERVATION, *, langue: str = "fr"):
    """Sérialise nettoyage et permutation d'une même installation, entre processus.

    Le verrou (atomique.verrou_inter_processus, relâché par l'OS à la mort de son
    détenteur) empêche un nettoyage concurrent de franchir le contrôle du marqueur
    avant sa création. Le marqueur, lui, survit à un arrêt brutal. Les erreurs du
    corps ne sont pas des échecs d'acquisition : elles suivent leur propre retour
    arrière, sans être requalifiées."""
    with contextlib.ExitStack() as reservations:
        try:
            reservations.enter_context(atomique.verrou_inter_processus(
                Path(installe) / nom, delai_s=0))
        except (TimeoutError, OSError) as erreur:
            raise RestaurationIncomplete(
                texte("installation_non_reservee", langue, erreur=erreur)) from erreur
        yield


def effacer_element(chemin: Path) -> None:
    if chemin.is_dir() and not chemin.is_symlink():
        shutil.rmtree(chemin)
    else:
        chemin.unlink(missing_ok=True)


def poser(source: Path, cible: Path) -> None:
    """Installe un fichier ou un dossier neuf à sa place définitive.

    Une copie, et non un déplacement : le programme qui exécute cette fonction
    est celui du dossier neuf, ses bibliothèques sont chargées depuis
    `_internal`, et Windows refuse de renommer un dossier dont un fichier est
    mappé en mémoire. Copier ne demande rien d'exclusif sur la source."""
    if source.is_dir():
        # symlinks=True : les liens internes du bundle (validés à l'extraction)
        # restent des liens au lieu d'être dupliqués ; la structure du framework
        # Python sous macOS en dépend.
        shutil.copytree(source, cible, symlinks=True)
    else:
        shutil.copy2(source, cible)
        if os.name != "nt":
            cible.chmod(0o755)


def permuter(neuf: Path, installe: Path, elements: Sequence[str], *,
             marqueur: str = MARQUEUR_PERMUTATION, nom_reservation: str = NOM_RESERVATION,
             poser: Callable = poser, ecrire: Callable[[str], None] = print,
             langue: str = "fr") -> bool:
    """Met les fichiers neufs à la place des anciens, ou remet tout en l'état.

    Les anciens sont écartés (« <nom>.ancien ») avant d'être supprimés : si une
    copie échoue à mi-chemin, on sait revenir en arrière, ce qu'un effacement
    préalable rendrait impossible. Rend vrai si tout est en place, faux si la
    permutation n'a pas pu démarrer (rien n'a changé : on peut réessayer), et
    lève RestaurationIncomplete si le retour arrière lui-même échoue ou si une
    permutation précédente n'est pas finalisée (rien ne doit alors être purgé)."""
    with reservation(installe, nom_reservation, langue=langue):
        return _permuter_reserve(Path(neuf), Path(installe), elements, marqueur, poser,
                                 ecrire, langue)


def _permuter_reserve(neuf, installe, elements, nom_marqueur, poser, ecrire, langue) -> bool:
    marqueur = installe / nom_marqueur
    try:
        # Création exclusive AVANT la première mutation : un arrêt brutal laisse
        # aussi le garde-fou empêchant de purger la seule sauvegarde.
        with marqueur.open("x", encoding="utf-8") as fichier:
            json.dump({"elements": list(elements)}, fichier)
    except FileExistsError as erreur:
        raise RestaurationIncomplete(
            texte("permutation_non_finalisee", langue, marqueur=marqueur)) from erreur
    except OSError as erreur:
        if marqueur.exists():
            raise RestaurationIncomplete(
                texte("permutation_preparation_interrompue", langue, marqueur=marqueur)) from erreur
        ecrire(texte("permutation_non_demarree", langue, erreur=erreur))
        return False

    touches = []
    try:
        for nom in elements:
            source = neuf / nom
            if not source.exists():
                continue
            ancien = installe / nom
            retire = None
            if ancien.exists():
                retire = installe / f"{nom}.ancien"
                effacer_element(retire)
                os.replace(ancien, retire)
            touches.append((retire, ancien))
            poser(source, installe / nom)
        marqueur.unlink()
        return True
    except OSError as erreur:
        ecrire(texte("echec_remplacement", langue, erreur=erreur))
        echecs = []
        for retire, ancien in reversed(touches):
            try:
                # Supprimer aussi un élément neuf qui n'existait pas avant.
                effacer_element(ancien)
                if retire is not None:
                    os.replace(retire, ancien)
            except OSError as restauration:
                echecs.append(f"{ancien.name}: {restauration}")
        if not echecs:
            try:
                marqueur.unlink()
            except OSError as restauration:
                echecs.append(str(restauration))
        if echecs:
            raise RestaurationIncomplete(
                texte("restauration_incomplete", langue, marqueur=marqueur,
                      echecs=" ; ".join(echecs))) from erreur
        return False


def nettoyer_restes(installe: Path, elements: Sequence[str], *,
             marqueur: str = MARQUEUR_PERMUTATION, nom_reservation: str = NOM_RESERVATION,
             apres: Optional[Callable[[Path], None]] = None, langue: str = "fr") -> None:
    """Efface les restes d'une mise à jour précédente (les « <nom>.ancien »), puis
    appelle `apres(installe)` pour ceux que l'application a posés elle-même.

    Ce ménage ne peut pas se faire à la fin de l'opération : le programme qui
    permute tourne depuis son dossier de préparation, et sous Windows un
    exécutable ne peut pas effacer le dossier dont il est issu. On le fait donc au
    début de la suivante, quand plus personne n'y tient. Refuse (lève
    RestaurationIncomplete) si une permutation précédente n'est pas finalisée :
    les sauvegardes sont alors tout ce qui reste."""
    installe = Path(installe)
    with reservation(installe, nom_reservation, langue=langue):
        if (installe / marqueur).exists():
            raise RestaurationIncomplete(
                texte("maj_precedente_non_finalisee", langue, marqueur=installe / marqueur))
        for nom in elements:
            reste = installe / f"{nom}.ancien"
            try:
                shutil.rmtree(reste, ignore_errors=True) if reste.is_dir() \
                    else reste.unlink(missing_ok=True)
            except OSError:
                pass
        if apres is not None:
            apres(installe)


def finaliser(installe: Path, neuf: Path, elements: Sequence[str], *,
              noter: Callable[[], object], arreter: Callable[[], bool],
              vivants: Callable[[], bool], relancer: Callable[[object], None],
              dire: Callable[..., None], marqueur: str = MARQUEUR_PERMUTATION,
              nom_reservation: str = NOM_RESERVATION, poser: Callable = poser,
              permutation: Optional[Callable[[Path, Path], bool]] = None,
              tentatives_arret: int = 20, tentatives_permutation: int = 15,
              dormir: Callable[[float], None] = time.sleep, langue: str = "fr") -> int:
    """Second temps du remplacement, exécuté par la nouvelle version : arrêter
    l'ancienne, remplacer, relancer. Rend le code de sortie du processus.

    L'application fournit ce qui lui est propre :
      noter()        ce qui tourne, noté AVANT l'arrêt (c'est ce qu'il faudra relancer) ;
      arreter()      arrête la version en place, vrai si l'arrêt a réussi ;
      vivants()      vrai tant qu'un processus de l'ancienne version subsiste ;
      relancer(état) relance ce que noter() avait vu ;
      dire(cle, echec, **valeurs)  rapporte une étape ou un échec (cles de
                     LIBELLES_PERMUTATION : texte(cle, langue, **valeurs)) ;
                     `echec` vrai : la mise à jour s'arrête sans relance.
      permutation(neuf, installe)  facultatif : remplace permuter() de ce module (une
                     application qui l'enveloppe, ou un test).

    Sans copie à faire (`neuf == installe`, une installation depuis les sources
    déjà mise à jour par git), on ne fait que relancer."""
    installe, neuf = Path(installe), Path(neuf)
    etat = noter()
    dire("arret_version_en_place", False)
    if not arreter():
        dire("arret_echoue", True)
        return 1
    # Les fichiers restent tenus quelques instants après la mort du processus,
    # le temps que le système referme ses poignées.
    for _ in range(tentatives_arret):
        if not vivants():
            break
        dormir(1)
    else:
        dire("instance_encore_active", True)
        return 1

    if neuf != installe:
        for _ in range(tentatives_permutation):
            try:
                if permutation is not None:
                    reussi = permutation(neuf, installe)
                else:
                    reussi = permuter(
                        neuf, installe, elements, marqueur=marqueur,
                        nom_reservation=nom_reservation, poser=poser,
                        ecrire=lambda message: dire("brut", False, texte=message),
                        langue=langue)
            except RestaurationIncomplete as erreur:
                dire("brut", False, texte=str(erreur))
                return 1
            if reussi:
                break
            dormir(2)
        else:
            dire("version_precedente_intacte", False)
            relancer(etat)
            return 1

    dire("installe_dans", False, installe=installe)
    relancer(etat)
    return 0


def relancer_verbes(executable: Path, verbes: Sequence[Sequence[str]], *,
                    demarrer: Optional[Callable] = None, cwd: Optional[Path] = None,
                    env: Optional[dict] = None, par_defaut: Sequence[str] = ()) -> list:
    """Relance un processus par entrée de `verbes` (chacune la liste d'arguments
    de l'exécutable), détachés de celui-ci, et rend les lignes de commande
    lancées. Sans verbes, un seul processus avec `par_defaut`.

    `demarrer(commande, **options)` : l'application peut fournir le sien (celui de
    blink2video sait détacher proprement sous chaque système) ; à défaut, Popen
    sans console ni entrée, dans une nouvelle session sous POSIX (CREATE_NO_WINDOW
    seul sous Windows, jamais avec DETACHED_PROCESS)."""
    def detacher(commande, **options):
        drapeaux = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        if os.name == "nt":
            options["creationflags"] = drapeaux
        else:
            options["start_new_session"] = True
        return subprocess.Popen(commande, **options)

    lancer_processus = demarrer or detacher
    lignes = [list(v) for v in verbes] or [list(par_defaut)]
    lancees = []
    for arguments in lignes:
        commande = [str(executable), *[str(a) for a in arguments]]
        lancer_processus(commande, cwd=str(cwd) if cwd else None, env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        lancees.append(commande)
    return lancees
