"""Mise à jour par archive : choix du fichier de la release, téléchargement
vérifié et extraction sans risque. Le même code pour blink2video et
watch2notif, qui en avaient chacun une version (maj.py et self_update.py).

Ce que fait ce module : trouver le bon fichier dans les assets d'une release
GitHub, le télécharger en refusant toute URL ou redirection étrangère et en
vérifiant taille et SHA-256 avant de le publier, puis l'extraire en refusant
tout ce qui sortirait du dossier de destination (traversée, liens vers
l'extérieur, noms non portables, collisions de casse, bombe de décompression).

Ce qu'il ne fait pas : remplacer l'installation en place et relancer
l'application. Cela dépend de la façon dont chaque application est livrée
(dossier échangé, assistant externe, agent macOS) et reste chez elle.

Bibliothèque standard seule : une mise à jour doit pouvoir réparer une
installation dont les dépendances sont abîmées.

Les erreurs sont des ErreurMiseAJour (une OSError) : `code` est une clé de
message stable, `valeurs` ses paramètres, `categorie` le regroupement grossier
(invalid_asset, integrity_failed, download_failed, unsafe_archive) pour les
interfaces qui n'en distinguent pas davantage, et message(langue) rend le
texte en français ou en anglais.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import stat
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

# Plafonds par défaut. Une archive officielle fait quelques dizaines à cent
# vingt Mo : ils ne devinent pas sa taille (celle publiée par GitHub doit
# correspondre exactement), ils refusent avant écriture une métadonnée
# aberrante et bornent une archive hostile. Chaque appel peut resserrer les
# siens.
TAILLE_ARCHIVE_MAX = 2 * 1024 * 1024 * 1024
TAILLE_EXTRAITE_MAX = 4 * 1024 * 1024 * 1024
MEMBRES_MAX = 100_000
EMPREINTE_TAILLE_MAX = 4096
CIBLE_LIEN_MAX = 4096
BLOC = 262144

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_TAG = re.compile(r"^v\d+\.\d+\.\d+$")
_LETTRE_DE_LECTEUR = re.compile(r"^[A-Za-z]:")
HOTES_REDIRECTION = ("github.com", "githubusercontent.com")
_NOMS_WINDOWS_INTERDITS = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$",
    *("COM%d" % i for i in range(1, 10)),
    *("LPT%d" % i for i in range(1, 10)),
}

LIBELLES = {'fr': {'empreinte_url_etrangere': "URL d'empreinte étrangère à la release officielle.",
        'empreinte_redirection': "La redirection de l'empreinte quitte GitHub ou HTTPS.",
        'empreinte_taille_http_invalide': "Taille d'empreinte HTTP invalide.",
        'empreinte_trop_volumineuse': "Fichier d'empreinte anormalement volumineux.",
        'empreinte_non_ascii': "Fichier d'empreinte non ASCII.",
        'empreinte_ambigue': "Fichier d'empreinte ambigu.",
        'empreinte_absente': 'Empreinte SHA-256 absente ou invalide.',
        'empreinte_archive_incorrecte': "L'empreinte ne désigne pas l'archive attendue.",
        'empreinte_release_absente': 'Cette release ne fournit aucune empreinte SHA-256 : mise à '
                                     'jour automatique refusée. Téléchargez-la manuellement depuis '
                                     'GitHub.',
        'nom_archive_impropre': "Nom d'archive impropre : {nom!r}",
        'archive_url_etrangere': "URL d'archive étrangère à la release officielle.",
        'archive_taille_invalide': "Taille d'archive invalide ou excessive : {taille} octets.",
        'archive_empreinte_invalide': "Empreinte SHA-256 d'archive invalide.",
        'archive_redirection': "La redirection de l'archive quitte GitHub ou HTTPS.",
        'archive_taille_http_invalide': "Taille d'archive HTTP invalide.",
        'archive_taille_http_inattendue': 'Taille HTTP inattendue : {annoncee}, attendu {taille}.',
        'archive_depasse_taille': "L'archive dépasse la taille publiée.",
        'archive_tronquee': 'Archive tronquée : {recu} octets, attendu {taille}.',
        'archive_empreinte_incorrecte': 'Empreinte SHA-256 incorrecte : {obtenue}, attendu '
                                        '{sha256}.',
        'archive_chemin_dangereux': "Chemin dangereux dans l'archive : {brut!r}",
        'archive_nom_non_portable': "Nom non portable dans l'archive : {brut!r}",
        'archive_chemin_hors_dossier': "Chemin hors du dossier d'extraction : {brut!r}",
        'archive_collision_chemins': "Collision de chemins dans l'archive : {nom!r}",
        'archive_membre_duplique': "Membre dupliqué dans l'archive : {nom!r}",
        'archive_membre_tronque': "Membre tronqué dans l'archive : {nom}",
        'archive_membre_plus_long': "Membre plus long qu'annoncé : {nom}",
        'archive_trop_de_membres': 'Archive contenant trop de membres.',
        'archive_membre_zip_chiffre': 'Membre ZIP chiffré interdit : {nom!r}',
        'archive_type_zip_dangereux': 'Type ZIP dangereux : {nom!r}',
        'archive_lien_type_zip_dangereux': 'Lien ou type ZIP dangereux : {nom!r}',
        'archive_zip_trop_volumineux': 'Contenu ZIP décompressé trop volumineux.',
        'archive_tar_trop_volumineux': 'Contenu TAR décompressé trop volumineux.',
        'archive_lien_type_tar_dangereux': 'Lien ou type TAR dangereux : {nom!r}',
        'archive_lien_hors_dossier': "Lien de l'archive hors du dossier d'extraction : {nom!r} -> "
                                     '{cible!r}',
        'archive_membre_tar_illisible': 'Membre TAR illisible : {nom!r}',
        'archive_format_inconnu': "Format d'archive inconnu : {nom}",
        'archive_bundle_unique': "L'archive doit contenir un unique dossier de bundle.",
        'asset_absent': 'Fichier de release absent ou en double : {nom}.',
        'asset_etat_invalide': 'Fichier de release non finalisé (état {etat!r}).',
        'asset_empreinte_absente': 'Empreinte SHA-256 absente ou invalide dans la release.',
        'asset_url_inattendue': 'URL de téléchargement inattendue pour la release officielle.',
        'telechargement_echoue': 'Téléchargement impossible : {detail}',
        'archive_racine_inattendue': "Racine inattendue dans l'archive : {nom!r}",
        'archive_racine_invalide': "Racine du bundle invalide dans l'archive.",
        'archive_illisible': 'Archive illisible ou corrompue : {detail}'},
 'en': {'empreinte_url_etrangere': 'Checksum URL foreign to the official release.',
        'empreinte_redirection': 'The checksum redirect leaves GitHub or HTTPS.',
        'empreinte_taille_http_invalide': 'Invalid checksum HTTP size.',
        'empreinte_trop_volumineuse': 'Checksum file abnormally large.',
        'empreinte_non_ascii': 'Checksum file not ASCII.',
        'empreinte_ambigue': 'Ambiguous checksum file.',
        'empreinte_absente': 'Missing or invalid SHA-256 checksum.',
        'empreinte_archive_incorrecte': 'The checksum does not name the expected archive.',
        'empreinte_release_absente': 'This release provides no SHA-256 checksum: automatic update '
                                     'refused. Download it manually from GitHub.',
        'nom_archive_impropre': 'Improper archive name: {nom!r}',
        'archive_url_etrangere': 'Archive URL foreign to the official release.',
        'archive_taille_invalide': 'Invalid or excessive archive size: {taille} bytes.',
        'archive_empreinte_invalide': 'Invalid archive SHA-256 checksum.',
        'archive_redirection': 'The archive redirect leaves GitHub or HTTPS.',
        'archive_taille_http_invalide': 'Invalid archive HTTP size.',
        'archive_taille_http_inattendue': 'Unexpected HTTP size: {annoncee}, expected {taille}.',
        'archive_depasse_taille': 'The archive exceeds its published size.',
        'archive_tronquee': 'Truncated archive: {recu} bytes, expected {taille}.',
        'archive_empreinte_incorrecte': 'Incorrect SHA-256 checksum: {obtenue}, expected {sha256}.',
        'archive_chemin_dangereux': 'Dangerous path in the archive: {brut!r}',
        'archive_nom_non_portable': 'Non-portable name in the archive: {brut!r}',
        'archive_chemin_hors_dossier': 'Path outside the extraction folder: {brut!r}',
        'archive_collision_chemins': 'Path collision in the archive: {nom!r}',
        'archive_membre_duplique': 'Duplicate member in the archive: {nom!r}',
        'archive_membre_tronque': 'Truncated member in the archive: {nom}',
        'archive_membre_plus_long': 'Member longer than announced: {nom}',
        'archive_trop_de_membres': 'Archive contains too many members.',
        'archive_membre_zip_chiffre': 'Encrypted ZIP member forbidden: {nom!r}',
        'archive_type_zip_dangereux': 'Dangerous ZIP type: {nom!r}',
        'archive_lien_type_zip_dangereux': 'Dangerous ZIP link or type: {nom!r}',
        'archive_zip_trop_volumineux': 'Decompressed ZIP content too large.',
        'archive_tar_trop_volumineux': 'Decompressed TAR content too large.',
        'archive_lien_type_tar_dangereux': 'Dangerous TAR link or type: {nom!r}',
        'archive_lien_hors_dossier': 'Archive link outside the extraction folder: {nom!r} -> '
                                     '{cible!r}',
        'archive_membre_tar_illisible': 'Unreadable TAR member: {nom!r}',
        'archive_format_inconnu': 'Unknown archive format: {nom}',
        'archive_bundle_unique': 'The archive must contain a single bundle folder.',
        'asset_absent': 'Release file missing or duplicated: {nom}.',
        'asset_etat_invalide': 'Release file not finalized (state {etat!r}).',
        'asset_empreinte_absente': 'SHA-256 digest missing or invalid in the release.',
        'asset_url_inattendue': 'Unexpected download URL for the official release.',
        'telechargement_echoue': 'Download failed: {detail}',
        'archive_racine_inattendue': 'Unexpected root in the archive: {nom!r}',
        'archive_racine_invalide': 'Invalid bundle root in the archive.',
        'archive_illisible': 'Unreadable or corrupted archive: {detail}'}}

CATEGORIES = {
    "asset_absent": "invalid_asset",
    "asset_etat_invalide": "invalid_asset",
    "asset_empreinte_absente": "invalid_asset",
    "asset_url_inattendue": "invalid_asset",
    "archive_taille_invalide": "invalid_asset",
    "archive_empreinte_invalide": "invalid_asset",
    "archive_url_etrangere": "invalid_asset",
    "archive_redirection": "invalid_asset",
    "nom_archive_impropre": "invalid_asset",
    "archive_taille_http_invalide": "integrity_failed",
    "archive_taille_http_inattendue": "integrity_failed",
    "archive_depasse_taille": "integrity_failed",
    "archive_tronquee": "integrity_failed",
    "archive_empreinte_incorrecte": "integrity_failed",
    "telechargement_echoue": "download_failed",
}


class ErreurMiseAJour(OSError):
    """Refus ou échec d'une étape de mise à jour. Hérite d'OSError : un appelant
    qui attrapait déjà OSError autour du téléchargement continue de fonctionner."""

    def __init__(self, code: str, **valeurs):
        self.code = code
        self.valeurs = valeurs
        self.categorie = CATEGORIES.get(code, "unsafe_archive")
        super().__init__(self.message("fr"))

    def message(self, langue: str = "fr") -> str:
        modele = LIBELLES.get(langue, LIBELLES["en"]).get(self.code) or self.code
        try:
            return modele.format(**self.valeurs)
        except (KeyError, IndexError):
            return modele


def _echec(code: str, **valeurs):
    return ErreurMiseAJour(code, **valeurs)


# ------------------------------------------------------------------ adresses

def sha256_normalise(valeur) -> str:
    """Empreinte SHA-256 canonique (minuscules, sans « sha256: »), ou chaîne
    vide si elle est impropre."""
    texte = str(valeur or "").strip()
    if texte.lower().startswith("sha256:"):
        texte = texte.split(":", 1)[1].strip()
    return texte.lower() if _SHA256.fullmatch(texte) else ""


def url_de_redirection(url: str) -> bool:
    """N'accepte que les hôtes HTTPS qu'atteint GitHub en redirigeant un
    téléchargement de release (github.com, *.githubusercontent.com)."""
    try:
        analyse = urllib.parse.urlparse(str(url))
        port = analyse.port
    except ValueError:
        return False
    hote = (analyse.hostname or "").lower().rstrip(".")
    sur_github = any(hote == suffixe or hote.endswith("." + suffixe)
                     for suffixe in HOTES_REDIRECTION)
    return (analyse.scheme == "https" and sur_github and port in (None, 443)
            and analyse.username is None and analyse.password is None)


def url_de_release(url: str, depot: str, nom: str) -> bool:
    """Lie l'URL initiale au dépôt, au format de tag et au fichier attendus.

    Les métadonnées d'une release peuvent venir d'un cache ou d'une réponse
    qu'on ne maîtrise pas : accepter n'importe quel dépôt GitHub permettrait de
    fournir son propre binaire et sa propre empreinte."""
    try:
        analyse = urllib.parse.urlparse(str(url))
        chemin = urllib.parse.unquote(analyse.path)
        port = analyse.port
    except (UnicodeError, ValueError):
        return False
    morceaux = chemin.split("/")
    attendu = ["", *depot.split("/"), "releases", "download"]
    return (
        analyse.scheme == "https"
        and analyse.hostname == "github.com"
        and analyse.username is None and analyse.password is None
        and port is None and not analyse.params
        and not analyse.query and not analyse.fragment
        and len(morceaux) == len(attendu) + 2
        and morceaux[:len(attendu)] == attendu
        and _TAG.fullmatch(morceaux[-2]) is not None
        and morceaux[-1] == nom
    )


def nom_archive_sur(nom) -> str:
    """Nom de fichier d'archive sans chemin, en .zip ou .tar.gz."""
    nom = str(nom or "")
    if (not nom or len(nom) > 200 or "/" in nom or "\\" in nom
            or Path(nom).name != nom
            or not (nom.lower().endswith(".zip") or nom.lower().endswith(".tar.gz"))):
        raise _echec("nom_archive_impropre", nom=nom)
    return nom


# --------------------------------------------------------------------- asset

def choisir_asset(assets: list, nom: str, depot: str, *,
                  taille_max: int = TAILLE_ARCHIVE_MAX) -> dict:
    """L'unique fichier de release de ce nom, finalisé, avec sa taille, son
    empreinte SHA-256 et une URL du dépôt officiel. Rend une copie normalisée
    (size entier, digest « sha256:… » en minuscules)."""
    trouves = [a for a in assets or [] if isinstance(a, dict) and a.get("name") == nom]
    if len(trouves) != 1:
        raise _echec("asset_absent", nom=nom)
    asset = dict(trouves[0])
    if asset.get("state") != "uploaded":
        raise _echec("asset_etat_invalide", etat=asset.get("state"))
    try:
        taille = int(asset.get("size"))
    except (TypeError, ValueError):
        taille = 0
    if not 0 < taille <= taille_max:
        raise _echec("archive_taille_invalide", taille=taille)
    empreinte = sha256_normalise(asset.get("digest"))
    if not empreinte:
        raise _echec("asset_empreinte_absente")
    url = str(asset.get("browser_download_url") or "")
    if not url_de_release(url, depot, nom):
        raise _echec("asset_url_inattendue")
    asset.update(size=taille, digest="sha256:" + empreinte, browser_download_url=url)
    return asset


def lire_empreinte(url: str, nom_archive: str, depot: str, *, agent: str,
                   ouvrir: Callable = urllib.request.urlopen, contexte=None,
                   delai_s: float = 15) -> str:
    """Lit le petit fichier « <archive>.sha256 » publié avec l'archive, pour les
    réponses de l'API qui ne portent pas encore le champ `digest`."""
    if not url_de_release(url, depot, nom_archive + ".sha256"):
        raise _echec("empreinte_url_etrangere")
    requete = urllib.request.Request(url, headers={"User-Agent": agent})
    options = {"timeout": delai_s}
    if contexte is not None:
        options["context"] = contexte
    with ouvrir(requete, **options) as reponse:
        finale = getattr(reponse, "geturl", lambda: url)()
        if not url_de_redirection(finale):
            raise _echec("empreinte_redirection")
        annoncee = reponse.headers.get("Content-Length")
        if annoncee:
            try:
                annoncee = int(annoncee)
            except ValueError:
                raise _echec("empreinte_taille_http_invalide") from None
            if annoncee < 1 or annoncee > EMPREINTE_TAILLE_MAX:
                raise _echec("empreinte_trop_volumineuse")
        corps = reponse.read(EMPREINTE_TAILLE_MAX + 1)
        if len(corps) > EMPREINTE_TAILLE_MAX:
            raise _echec("empreinte_trop_volumineuse")
    try:
        lignes = [ligne.strip() for ligne in corps.decode("ascii").splitlines()
                  if ligne.strip()]
    except UnicodeDecodeError:
        raise _echec("empreinte_non_ascii") from None
    if len(lignes) != 1:
        raise _echec("empreinte_ambigue")
    champs = lignes[0].split()
    empreinte = sha256_normalise(champs[0] if champs else "")
    if not empreinte:
        raise _echec("empreinte_absente")
    if len(champs) > 2 or (len(champs) == 2 and champs[1].lstrip("*") != nom_archive):
        raise _echec("empreinte_archive_incorrecte")
    return empreinte


# ------------------------------------------------------------- téléchargement

def telecharger(url: str, destination: Path, taille: int, sha256: str, *,
                depot: str, agent: str, nom: Optional[str] = None,
                ouvrir: Callable = urllib.request.urlopen, contexte=None,
                progression: Optional[Callable[[int, int], None]] = None,
                taille_max: int = TAILLE_ARCHIVE_MAX, delai_s: float = 60) -> None:
    """Rapatrie l'archive vers « <destination>.part », puis ne la publie sous
    son nom qu'après avoir vérifié taille exacte et SHA-256 : un fichier à ce
    nom est donc toujours complet et authentique. Toute erreur efface le
    partiel. `nom` : le dernier élément attendu de l'URL (par défaut le nom de
    la destination). `progression(reçu, total)` est appelé deux fois par
    seconde au plus."""
    nom = nom or destination.name
    if not url_de_release(url, depot, nom):
        raise _echec("archive_url_etrangere")
    if not 1 <= taille <= taille_max:
        raise _echec("archive_taille_invalide", taille=taille)
    sha256 = sha256_normalise(sha256)
    if not sha256:
        raise _echec("archive_empreinte_invalide")

    partiel = destination.with_name(destination.name + ".part")
    requete = urllib.request.Request(
        url, headers={"Accept": "application/octet-stream", "User-Agent": agent})
    options = {"timeout": delai_s}
    if contexte is not None:
        options["context"] = contexte
    try:
        with ouvrir(requete, **options) as reponse:
            finale = getattr(reponse, "geturl", lambda: url)()
            if not url_de_redirection(finale):
                raise _echec("archive_redirection")
            annoncee = getattr(reponse, "headers", {}).get("Content-Length")
            if annoncee:
                try:
                    annoncee = int(annoncee)
                except ValueError:
                    raise _echec("archive_taille_http_invalide") from None
                if annoncee != taille:
                    raise _echec("archive_taille_http_inattendue",
                                 annoncee=annoncee, taille=taille)
            hacheur = hashlib.sha256()
            recu = 0
            dernier = 0.0
            with partiel.open("xb") as sortie:
                while True:
                    bloc = reponse.read(BLOC)
                    if not bloc:
                        break
                    recu += len(bloc)
                    if recu > taille or recu > taille_max:
                        raise _echec("archive_depasse_taille")
                    sortie.write(bloc)
                    hacheur.update(bloc)
                    if progression is not None and time.time() - dernier > 0.5:
                        dernier = time.time()
                        progression(recu, taille)
            if recu != taille:
                raise _echec("archive_tronquee", recu=recu, taille=taille)
            obtenue = hacheur.hexdigest()
            if obtenue != sha256:
                raise _echec("archive_empreinte_incorrecte", obtenue=obtenue, sha256=sha256)
        os.replace(partiel, destination)
    except ErreurMiseAJour:
        partiel.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError, ValueError) as erreur:
        partiel.unlink(missing_ok=True)
        raise _echec("telechargement_echoue", detail=str(erreur)) from erreur
    except BaseException:
        partiel.unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------ extraction

def _destination(racine: Path, nom: str) -> tuple:
    """Destination confinée d'un membre, avec une syntaxe portable stricte."""
    brut = str(nom or "")
    if (not brut or len(brut) > 4096 or "\x00" in brut
            or brut.startswith(("/", "\\"))):
        raise _echec("archive_chemin_dangereux", brut=brut)
    portable = brut.replace("\\", "/").rstrip("/")
    morceaux = portable.split("/")
    if (not portable or PurePosixPath(portable).is_absolute()
            or _LETTRE_DE_LECTEUR.match(portable)
            or any(not m or m in (".", "..") for m in morceaux)):
        raise _echec("archive_chemin_dangereux", brut=brut)
    for morceau in morceaux:
        base = morceau.split(".", 1)[0].upper()
        if (len(morceau) > 255 or ":" in morceau
                or morceau.endswith((" ", "."))
                or any(ord(c) < 32 for c in morceau)
                or base in _NOMS_WINDOWS_INTERDITS):
            raise _echec("archive_nom_non_portable", brut=brut)
    cible = racine.joinpath(*morceaux).resolve()
    try:
        cible.relative_to(racine)
    except ValueError:
        raise _echec("archive_chemin_hors_dossier", brut=brut) from None
    return cible, tuple(morceaux)


def _inscrire(registre: dict, morceaux: tuple, genre: str) -> None:
    """Refuse doublons, collisions de casse et fichier utilisé comme parent."""
    for index in range(1, len(morceaux) + 1):
        nom = "/".join(morceaux[:index])
        cle = nom.casefold()
        courant = genre if index == len(morceaux) else "dir"
        precedent = registre.get(cle)
        if precedent is None:
            registre[cle] = (nom, courant)
            continue
        if precedent[0] != nom or precedent[1] != courant:
            raise _echec("archive_collision_chemins", nom=nom)
        if courant != "dir":
            raise _echec("archive_membre_duplique", nom=nom)


def _cible_lien(morceaux: tuple, cible_lien: str, racine_bundle: Optional[str]) -> str:
    """Cible d'un lien symbolique, acceptée seulement si elle est relative et
    reste dans le dossier d'extraction (et dans la racine du bundle quand elle
    est connue) : la règle du filtre « data » de tarfile. Les bundles
    PyInstaller en contiennent (bibliothèques de Pillow sous Linux, framework
    Python sous macOS). Sous Windows aucune archive publiée n'en contient et
    en créer demande un privilège : ils y restent refusés."""
    nom = "/".join(morceaux)
    if os.name == "nt":
        raise _echec("archive_lien_type_zip_dangereux", nom=nom)
    brut = str(cible_lien or "")
    if (not brut or len(brut) > CIBLE_LIEN_MAX or "\x00" in brut or "\\" in brut
            or PurePosixPath(brut).is_absolute() or _LETTRE_DE_LECTEUR.match(brut)):
        raise _echec("archive_lien_hors_dossier", nom=nom, cible=brut)
    resolu = posixpath.normpath(posixpath.join(*morceaux[:-1], brut))
    if resolu in (".", "..") or resolu.startswith("../"):
        raise _echec("archive_lien_hors_dossier", nom=nom, cible=brut)
    if racine_bundle and resolu.split("/")[0] != racine_bundle:
        raise _echec("archive_lien_hors_dossier", nom=nom, cible=brut)
    return brut


def _creer_liens(membres: list) -> None:
    """Liens posés en dernier, une fois dossiers et fichiers écrits : aucun
    chemin validé plus haut n'a pu en traverser un."""
    for _info, cible, genre, lien in membres:
        if genre == "lien":
            cible.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(lien, cible)


def _copier(source, destination: Path, taille: int) -> None:
    restant = taille
    with destination.open("xb") as sortie:
        while restant:
            bloc = source.read(min(BLOC, restant))
            if not bloc:
                raise _echec("archive_membre_tronque", nom=destination.name)
            sortie.write(bloc)
            restant -= len(bloc)
        if source.read(1):
            raise _echec("archive_membre_plus_long", nom=destination.name)


def _verifier_racine(morceaux: tuple, racine_bundle: Optional[str], nom: str) -> None:
    if racine_bundle and morceaux[0] != racine_bundle:
        raise _echec("archive_racine_inattendue", nom=nom)


def _extraire_zip(archive: Path, racine: Path, racine_bundle, taille_max, membres_max) -> None:
    with zipfile.ZipFile(archive) as zip_:
        infos = zip_.infolist()
        if len(infos) > membres_max:
            raise _echec("archive_trop_de_membres")
        registre, membres, total = {}, [], 0
        for info in infos:
            mode = (info.external_attr >> 16) & 0xFFFF
            type_mode = stat.S_IFMT(mode)
            if info.flag_bits & 0x1:
                raise _echec("archive_membre_zip_chiffre", nom=info.filename)
            lien = None
            if info.is_dir():
                if type_mode not in (0, stat.S_IFDIR):
                    raise _echec("archive_type_zip_dangereux", nom=info.filename)
                genre = "dir"
            elif type_mode == stat.S_IFLNK:
                # Un lien ZIP porte sa cible comme contenu du membre.
                if not 0 < info.file_size <= CIBLE_LIEN_MAX:
                    raise _echec("archive_lien_type_zip_dangereux", nom=info.filename)
                genre = "lien"
                try:
                    lien = zip_.read(info).decode("utf-8")
                except UnicodeDecodeError:
                    raise _echec("archive_lien_type_zip_dangereux", nom=info.filename) from None
            else:
                if type_mode not in (0, stat.S_IFREG):
                    raise _echec("archive_lien_type_zip_dangereux", nom=info.filename)
                genre = "file"
                total += info.file_size
                if info.file_size < 0 or total > taille_max:
                    raise _echec("archive_zip_trop_volumineux")
            cible, morceaux = _destination(racine, info.filename)
            _verifier_racine(morceaux, racine_bundle, info.filename)
            if genre == "lien":
                lien = _cible_lien(morceaux, lien, racine_bundle)
            # Un lien est une feuille : rien ne peut être rangé « sous » lui.
            _inscrire(registre, morceaux, "file" if genre == "lien" else genre)
            membres.append((info, cible, genre, lien))

        for _, cible, genre, _lien in membres:
            if genre == "dir":
                cible.mkdir(parents=True, exist_ok=True)
        for info, cible, genre, _lien in membres:
            if genre != "file":
                continue
            cible.parent.mkdir(parents=True, exist_ok=True)
            with zip_.open(info, "r") as source:
                _copier(source, cible, info.file_size)
            # Seul le caractère exécutable utile est conservé : ni propriétaire,
            # ni setuid, ni mode arbitraire venu de l'archive.
            if os.name != "nt":
                mode = (info.external_attr >> 16) & 0o111
                cible.chmod(0o755 if mode else 0o644)
        _creer_liens(membres)


def _extraire_tar(archive: Path, racine: Path, racine_bundle, taille_max, membres_max) -> None:
    with tarfile.open(archive) as tar:
        infos = []
        for info in tar:
            infos.append(info)
            if len(infos) > membres_max:
                raise _echec("archive_trop_de_membres")
        registre, membres, total = {}, [], 0
        for info in infos:
            lien = None
            if info.isdir():
                genre = "dir"
            elif info.isfile() and not getattr(info, "sparse", None):
                genre = "file"
                total += info.size
                if info.size < 0 or total > taille_max:
                    raise _echec("archive_tar_trop_volumineux")
            elif info.issym():
                genre = "lien"
            else:
                # Liens physiques, périphériques, FIFO : jamais dans un bundle.
                raise _echec("archive_lien_type_tar_dangereux", nom=info.name)
            cible, morceaux = _destination(racine, info.name)
            _verifier_racine(morceaux, racine_bundle, info.name)
            if genre == "lien":
                lien = _cible_lien(morceaux, info.linkname, racine_bundle)
            _inscrire(registre, morceaux, "file" if genre == "lien" else genre)
            membres.append((info, cible, genre, lien))

        for _, cible, genre, _lien in membres:
            if genre == "dir":
                cible.mkdir(parents=True, exist_ok=True)
        for info, cible, genre, _lien in membres:
            if genre != "file":
                continue
            cible.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(info)
            if source is None:
                raise _echec("archive_membre_tar_illisible", nom=info.name)
            with source:
                _copier(source, cible, info.size)
            cible.chmod(0o755 if info.mode & 0o111 else 0o644)
        _creer_liens(membres)


def extraire(archive: Path, vers: Path, *, racine: Optional[str] = None,
             taille_max: int = TAILLE_EXTRAITE_MAX, membres_max: int = MEMBRES_MAX) -> Path:
    """Déballe l'archive (.zip ou .tar.gz) dans `vers` (qui ne doit pas exister)
    et rend le dossier du bundle qu'elle contenait. `racine` : nom exigé de ce
    dossier unique (watch2notif, watch2notif.app) ; sans lui, un seul dossier
    de n'importe quel nom."""
    vers.mkdir(parents=True, exist_ok=False)
    base = vers.resolve()
    nom = archive.name.lower()
    try:
        if nom.endswith(".zip"):
            _extraire_zip(archive, base, racine, taille_max, membres_max)
        elif nom.endswith(".tar.gz"):
            _extraire_tar(archive, base, racine, taille_max, membres_max)
        else:
            raise _echec("archive_format_inconnu", nom=archive.name)
    except (zipfile.BadZipFile, tarfile.TarError, UnicodeError) as erreur:
        raise _echec("archive_illisible", detail=str(erreur)) from erreur
    contenu = list(vers.iterdir())
    if (len(contenu) != 1 or not contenu[0].is_dir() or contenu[0].is_symlink()
            or (racine and contenu[0].name != racine)):
        raise _echec("archive_racine_invalide" if racine else "archive_bundle_unique")
    return contenu[0]
