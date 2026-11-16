"""Téléchargement de tuiles raster (WMTS ou XYZ) vers un MBTiles.

Repris de hike2gpx (fonds.py), la version la plus rapide mesurée, avec trois
apports de lidar2map (_mbtiles_wmts.py) : les redirections suivies, un corps
qui n'est pas une image refusé par sa signature, et l'arrêt après un certain
nombre d'erreurs consécutives seulement.

Ce qui fait le débit, mesuré sur le SCAN 25 de la Géoplateforme :
- une file continue (FENETRE tuiles en vol par connexion) : une tuile lente
  n'arrête que sa connexion, là où des paquets attendaient chacun leur plus
  lente (12,6 tuiles par seconde en paquets de 256, 18 à 42 en file continue) ;
- une connexion HTTP/1.1 gardée ouverte par fil et par hôte, sans nouvelle
  poignée de main TCP et TLS à chaque tuile ;
- des enregistrements SQLite espacés (ENREGISTREMENT tuiles), la file regarnie
  avant d'écrire, pour qu'un commit lent n'arrête pas les connexions.

Ce que le module laisse à l'application : les adresses (fournisseurs, clés),
le réencodage éventuel d'une tuile (le rappel traiter), les métadonnées du
MBTiles, le nom du .part et sa reprise ou sa suppression.

Bibliothèque standard seule.
"""

from __future__ import annotations

import concurrent.futures
import http.client
import io
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEBIT = 0.0                 # tuiles par seconde au plus ; 0 : pas de bride
FILS = 8                    # connexions simultanées (à 24, la Géoplateforme freine)
DELAI = 15                  # secondes au plus pour une tuile, avant un nouvel essai
ESSAIS = 4                  # essais par tuile, espacés de 2, 4 puis 8 s
FENETRE = 32                # tuiles en vol par connexion
ENREGISTREMENT = 500        # tuiles reçues entre deux enregistrements
ERREURS_CONSECUTIVES = 30   # tuiles en échec de suite avant de juger le service en panne
REDIRECTIONS = 3            # sauts suivis par Connexions (http.client ne le fait pas seul)

_REDIRIGE = (301, 302, 303, 307, 308)


class Abandon(Exception):
    """L'appelant a demandé l'arrêt (le rappel continuer a rendu faux)."""


class ErreurTuiles(RuntimeError):
    """Un échec de téléchargement ; code : le statut HTTP en cause, ou None."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def est_image(contenu: bytes) -> bool:
    """Vrai si le contenu commence par la signature d'une image raster (JPEG,
    PNG, GIF, WebP, TIFF). Par signature, ni par taille (un petit PNG uni est
    valide) ni par Content-Type (parfois image/png sur une page d'erreur)."""
    if not contenu or len(contenu) < 4:
        return False
    return (contenu[:3] == b"\xff\xd8\xff"
            or contenu[:8] == b"\x89PNG\r\n\x1a\n"
            or contenu[:6] in (b"GIF87a", b"GIF89a")
            or (contenu[:4] == b"RIFF" and contenu[8:12] == b"WEBP")
            or contenu[:4] in (b"II\x2a\x00", b"MM\x00\x2a", b"II\x2b\x00", b"MM\x00\x2b"))


class Debit:
    """Au plus n requêtes par seconde, tous fils confondus."""

    def __init__(self, par_seconde: float = 0.0):
        self.intervalle = 1.0 / par_seconde if par_seconde else 0.0
        self.suivante = time.monotonic()
        self.verrou = threading.Lock()

    def attendre(self) -> None:
        with self.verrou:
            maintenant = time.monotonic()
            attente = self.suivante - maintenant
            self.suivante = max(self.suivante, maintenant) + self.intervalle
        if attente > 0:
            time.sleep(attente)


class _Reponse(io.BytesIO):
    """Le corps d'une réponse déjà lu, à la manière de celle d'urlopen."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Connexions:
    """Un urlopen qui garde une connexion HTTP/1.1 ouverte par fil et par hôte
    (keep-alive). Une connexion que le serveur a fermée se rouvre une fois ; un
    délai dépassé remonte tel quel, pour le nouvel essai de telecharger_tuile.
    Les redirections sont suivies (REDIRECTIONS sauts). Appelé comme urlopen, un
    statut d'erreur lève HTTPError ; obtenir() rend le statut sans lever.
    fermer() libère les sockets en fin de travail."""

    def __init__(self):
        self.locales = threading.local()
        self.ouvertes = []
        self.verrou = threading.Lock()

    def _connexion(self, schema: str, hote: str, timeout: float):
        cache = getattr(self.locales, "par_hote", None)
        if cache is None:
            cache = self.locales.par_hote = {}
        if hote not in cache:
            classe = http.client.HTTPSConnection if schema == "https" else http.client.HTTPConnection
            cache[hote] = classe(hote, timeout=timeout)
            with self.verrou:
                self.ouvertes.append(cache[hote])
        return cache[hote]

    def _oublier(self, hote: str) -> None:
        connexion = getattr(self.locales, "par_hote", {}).pop(hote, None)
        if connexion is not None:
            connexion.close()

    def _une_fois(self, url: str, entetes: dict, timeout: float):
        parties = urllib.parse.urlsplit(url)
        chemin = parties.path + (f"?{parties.query}" if parties.query else "")
        for essai in range(2):
            connexion = self._connexion(parties.scheme, parties.netloc, timeout)
            try:
                connexion.request("GET", chemin, headers=entetes)
                reponse = connexion.getresponse()
                return reponse, reponse.read()    # lu en entier : la connexion resservira
            except (http.client.HTTPException, OSError) as exc:
                self._oublier(parties.netloc)
                if essai or isinstance(exc, TimeoutError):
                    raise

    def obtenir(self, url: str, entetes: dict | None = None, timeout: float = DELAI):
        """GET d'une adresse, redirections suivies : (statut, type de contenu,
        corps), quel que soit le statut (pas d'exception sur un 404 ou un 500 ;
        les erreurs réseau remontent). Trop de redirections : HTTPError 310."""
        for _ in range(REDIRECTIONS + 1):
            reponse, contenu = self._une_fois(url, entetes or {}, timeout)
            lieu = reponse.headers.get("location")
            if reponse.status in _REDIRIGE and lieu:
                url = urllib.parse.urljoin(url, lieu)
                continue
            return reponse.status, reponse.headers.get("content-type", ""), contenu
        raise urllib.error.HTTPError(url, 310, "Trop de redirections", {}, None)

    def __call__(self, requete, timeout=DELAI):
        statut, _, contenu = self.obtenir(requete.full_url, dict(requete.header_items()), timeout)
        if statut >= 300:
            raise urllib.error.HTTPError(requete.full_url, statut, http.client.responses.get(statut, ""), {}, None)
        return _Reponse(contenu)

    def fermer(self) -> None:
        with self.verrou:
            for connexion in self.ouvertes:
                connexion.close()
            self.ouvertes.clear()


def telecharger_tuile(url: str, ouvrir, debit: Debit, continuer, *, entetes: dict | None = None,
                      absent: tuple = (400, 404), valider=est_image, pause=None) -> bytes | None:
    """Le contenu de la tuile, ou None si elle est absente : un statut de
    absent (404 ; 400 hors de la matrice), un corps vide (204). ESSAIS essais,
    espacés de 2, 4 puis 8 s, sur une coupure, un statut d'erreur ou un corps
    refusé par valider (une page d'erreur servie en 200 : un échec, jamais une
    tuile absente, car un trou donné pour complet est pire). Un refus de clé
    (401, 403) n'est pas toujours définitif : mesuré le 2026-10-28, la
    Géoplateforme a répondu 401 une fois, après 9 000 tuiles, à une clé qu'elle
    acceptait de nouveau l'instant d'après ; d'où les nouveaux essais.
    Lève Abandon si continuer() rend faux, ErreurTuiles après le dernier essai
    (code : le dernier statut HTTP, ou None)."""
    erreur = None
    for essai in range(ESSAIS):
        if not continuer():
            raise Abandon()
        debit.attendre()
        try:
            with ouvrir(urllib.request.Request(url, headers=entetes or {}), timeout=DELAI) as reponse:
                contenu = reponse.read()
            if not contenu:
                return None
            if valider is not None and not valider(contenu):
                raise OSError(f"réponse qui n'est pas une image ({len(contenu)} o) : {contenu[:80]!r}")
            return contenu
        except urllib.error.HTTPError as exc:
            if exc.code in absent:
                return None
            erreur = exc
        except (OSError, http.client.HTTPException) as exc:
            erreur = exc
        if essai < ESSAIS - 1:
            (pause or time.sleep)(2 ** (essai + 1))
    code = erreur.code if isinstance(erreur, urllib.error.HTTPError) else None
    raise ErreurTuiles(f"Tuiles injoignables ({erreur}).", code) from erreur


def ouvrir_base(chemin: Path, ecriture: bool = False) -> sqlite3.Connection:
    """Le MBTiles chemin, créé s'il manque. ecriture : le .part d'un
    téléchargement, en mode WAL (journal en écriture anticipée) et
    synchronous=NORMAL : un enregistrement ajoute au journal au lieu de
    réécrire la base (jusqu'à 12 s dans une base de 830 Mo, d'où des
    connexions à l'arrêt), sans risque pour la reprise si l'application
    s'arrête. À la fin, rendre le fichier seul avec PRAGMA journal_mode=DELETE."""
    base = sqlite3.connect(chemin)
    if ecriture:
        base.execute("PRAGMA journal_mode=WAL")
        base.execute("PRAGMA synchronous=NORMAL")
    base.executescript(
        "CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT);"
        "CREATE TABLE IF NOT EXISTS tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER,"
        " tile_data BLOB);"
        "CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON tiles (zoom_level, tile_column, tile_row);"
        "CREATE UNIQUE INDEX IF NOT EXISTS name ON metadata (name);")
    return base


def deja_rangees(base: sqlite3.Connection) -> set:
    """Les tuiles (z, x, y_tms) déjà dans la base, pour une reprise."""
    return set(base.execute("SELECT zoom_level, tile_column, tile_row FROM tiles"))


def _tuile(url: str, ouvrir, debit: Debit, continuer, entetes, traiter) -> bytes | None:
    """Une tuile téléchargée puis traitée, dans le fil de sa connexion : le
    fil principal n'a plus qu'à la ranger."""
    contenu = telecharger_tuile(url, ouvrir, debit, continuer, entetes=entetes)
    return traiter(contenu) if contenu and traiter else contenu


def telecharger(base: sqlite3.Connection, a_faire, url, *, total: int, faites: int = 0, suivi=None,
                continuer=None, ouvrir=None, entetes: dict | None = None, traiter=None,
                debit: float = DEBIT, fils: int = FILS, fenetre: int = FENETRE,
                enregistrement: int = ENREGISTREMENT,
                erreurs_consecutives: int = ERREURS_CONSECUTIVES) -> int:
    """Range dans base les tuiles a_faire, des (z, x, y) en numérotation XYZ
    (rangée comptée depuis le nord), à l'adresse url(z, x, y). Rend le nombre
    de tuiles reçues ; une tuile absente (voir telecharger_tuile) n'est pas
    rangée.

    total, faites : pour suivi(faites, total), rappelé à chaque enregistrement.
    traiter(contenu) -> octets, au besoin, dans le fil de la connexion
    (réencodage). ouvrir : un urlopen de remplacement (essais) ; par défaut des
    Connexions gardées, fermées en fin de travail.

    Une tuile en échec après ses essais est sautée, la file continue ; après
    erreurs_consecutives échecs de suite, le service est jugé en panne : arrêt
    avec ErreurTuiles. Un refus de clé (401, 403) y met fin dès que chaque
    connexion l'a essuyé. En fin de file, si des tuiles ont échoué, ErreurTuiles
    aussi : l'appelant ne finalise pas un fond troué, et reprend avec ce que
    contient base (deja_rangees). Les tuiles reçues sont enregistrées dans tous
    les cas, y compris sur Abandon ou sur une exception.
    """
    suivi = suivi or (lambda faites, total: None)
    continuer = continuer or (lambda: True)
    connexions = None if ouvrir else Connexions()
    ouvrir = ouvrir or connexions
    limiteur = Debit(debit)
    file = iter(a_faire)
    recues = 0          # tuiles rangées
    depuis = 0          # tuiles traitées depuis le dernier enregistrement
    echecs = 0
    de_suite = 0
    refus = 0
    derniere = None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=fils) as pool:
            en_vol = {}

            def remplir():
                while len(en_vol) < fenetre * fils and (t := next(file, None)) is not None:
                    en_vol[pool.submit(_tuile, url(*t), ouvrir, limiteur, continuer, entetes, traiter)] = t

            remplir()
            try:
                while en_vol:
                    finies, _ = concurrent.futures.wait(en_vol, return_when=concurrent.futures.FIRST_COMPLETED)
                    arrivees = [(en_vol.pop(tache), tache) for tache in finies]
                    remplir()       # avant d'écrire : un enregistrement lent n'arrête pas les connexions
                    for (z, x, y), tache in arrivees:
                        try:
                            contenu = tache.result()
                        except ErreurTuiles as exc:
                            echecs += 1
                            de_suite += 1
                            refus = refus + 1 if exc.code in (401, 403) else 0
                            derniere = exc
                            if de_suite >= erreurs_consecutives or refus >= fils:
                                raise
                        else:
                            de_suite = refus = 0
                            if contenu:
                                base.execute("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)",
                                             (z, x, 2 ** z - 1 - y, contenu))
                                recues += 1
                        faites += 1
                        depuis += 1
                    if depuis >= enregistrement:
                        # Dans le désordre, mais chaque tuile rangée ne se redemande plus.
                        base.commit()
                        depuis = 0
                        suivi(faites, total)
            except BaseException:
                for tache in en_vol:
                    tache.cancel()
                # Arrêté ou en échec : les tuiles déjà reçues restent acquises pour la reprise.
                base.commit()
                raise
        base.commit()
        suivi(faites, total)
    finally:
        if connexions is not None:
            connexions.fermer()
    if echecs:
        raise ErreurTuiles(f"{echecs} tuile(s) en échec, la dernière : {derniere}", derniere.code) from derniere
    return recues
