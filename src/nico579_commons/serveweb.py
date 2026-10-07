"""Serveur web local d'une application : sert sa page (index.html, app.js,
style.css, web_bridge.js) et ses routes /api/* en JSON, sur la boucle locale.

Repris de _serve_web.py de lidar2map, dont ceux de gpxsolar et de watch2notif
étaient des copies : gpxsolar à trois écarts près (routes à paramètres, accès
distant, nom d'une variable d'environnement), watch2notif une copie plus
ancienne, qui n'avait pas reçu les durcissements faits depuis (en-tête
Sec-Fetch-Site, réponse 500 en JSON quand une route lève, Content-Length
négatif, chemin invalide). Une seule version : ce qu'on corrige ici l'est
pour les quatre.

Même modèle de sécurité que blink2video (serve.py, hote_autorise()) : pas
d'authentification de compte, seule la provenance de la requête (Host,
adresse TCP du client, Origin, Sec-Fetch-Site) est vérifiée. Un outil
personnel, pas un service multi-utilisateur.

Dispatch : une route ``/api/<clef>`` appelle ``api_routes["<clef>"]()`` en GET
ou ``post_routes["<clef>"](payload_json)`` en POST. Une route GET qui prend des
paramètres de requête déclare comment les lire dans ``arguments_get`` : clef
-> fonction qui reçoit le dict de parse_qs() et rend le tuple d'arguments de la
route.

Pas de framework : http.server.ThreadingHTTPServer, bibliothèque standard
seule. Rien de propre à une application n'est écrit ici : elle en fait une
sous-classe de Handler (nom de sa variable d'environnement de proxy local,
routes à paramètres) et la passe à demarrer().
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

PREFIXE_API = "/api/"
ROUTE_BANDEAU_MAJ = "/nico579-maj.js"
ROUTE_REGLAGES = "/nico579-reglages.js"
# Fichiers JavaScript communs, servis depuis le paquet : route -> fichier.
FICHIERS_COMMUNS = {
    ROUTE_BANDEAU_MAJ: "maj_banniere.js",
    ROUTE_REGLAGES: "reglages.js",
}


def fichiers_manquants() -> list:
    """Ceux des fichiers communs qui manquent à ce paquet. Vide en sources ; dans un
    exécutable PyInstaller, les données du paquet ne sont embarquées que si le
    .spec les demande (collect_data_files("nico579_commons")) : sans cela, la
    page réclame un fichier qui n'existe pas (constaté sur les exécutables
    installés le 2026-10-07). Les auto-tests des applications l'appellent."""
    return [nom for nom in FICHIERS_COMMUNS.values()
            if not Path(__file__).with_name(nom).is_file()]

# Route -> (fichier dans gui_dir, type de contenu). Les fichiers absents
# répondent 404 : une application sans web_bridge.js n'a rien à retirer.
FICHIERS_STATIQUES = {
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/web_bridge.js": ("web_bridge.js", "text/javascript; charset=utf-8"),
}


def entrees_confiance(valeur: str) -> list:
    """Entrées de trusted_host : une liste séparée par des virgules, chacune un
    nom d'hôte exact, une IP ou un sous-réseau CIDR. Une valeur unique reste
    une liste d'un."""
    return [entree.strip() for entree in (valeur or "").split(",") if entree.strip()]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Posés par demarrer() sur la CLASSE (une seule instance de serveur par
    # processus) avant de démarrer : une application qui veut changer l'hôte
    # de confiance à chaud modifie l'attribut de sa propre sous-classe.
    trusted_host: str = ""
    gui_dir: Optional[Path] = None
    favicon: Optional[Path] = None
    api_routes: dict = {}
    post_routes: dict = {}
    arguments_get: dict = {}
    fichiers_statiques: dict = FICHIERS_STATIQUES
    # Nom de la variable d'environnement (« LIDAR2MAP_TRUSTED_LOOPBACK_PROXY »)
    # qui admet un proxy inverse local devant le serveur ; "" : jamais.
    variable_proxy_local: str = ""

    _HOTES_LOCAUX = ("127.0.0.1", "localhost", "::1")

    def log_message(self, fmt, *args):
        pass  # pas de journal d'accès, rien d'utile ici

    # ------------------------------------------------------------ sécurité

    def _journaliser_acces_refuse(self, raison: str) -> None:
        """Appelé à chaque refus de hote_autorise() avec la raison, pour
        qu'une application garde une trace (un trusted_host mal renseigné ne
        laisse sinon qu'un 403 muet). Ne fait rien par défaut."""

    @staticmethod
    def _hote_correspond(hote: str, hote_confiance: str) -> bool:
        """Vrai si `hote` (Host ou Origin, déjà réduit au hostname) est couvert
        par l'une des entrées de `hote_confiance`.

        Chaque entrée (liste séparée par des virgules : un seul nom ou un seul
        sous-réseau ne suffit pas pour mêler accès direct et iframe) est soit
        une IP ou un nom d'hôte exact (comparaison de chaîne), soit un
        sous-réseau CIDR (192.168.1.0/24 : un client Windows en DHCP n'a pas
        d'adresse fixe). ip_network(..., strict=False) tolère qu'on y colle
        l'adresse d'une machine du réseau (192.168.1.5/24), erreur de saisie
        probable et sans ambiguïté. Jamais de ValueError : un hôte ou un CIDR
        mal formé se traite comme « pas de correspondance »."""
        for entree in entrees_confiance(hote_confiance):
            if "/" in entree:
                try:
                    if ipaddress.ip_address(hote) in ipaddress.ip_network(entree, strict=False):
                        return True
                except ValueError:
                    continue
            elif hote == entree:
                return True
        return False

    def hote_autorise(self) -> bool:
        """Faux si Host (déclaré par le client) ne désigne pas cette machine,
        si l'adresse TCP réelle du client n'est ni la boucle locale, ni un
        proxy local admis, ni couverte par trusted_host, ou si Origin (quand
        le navigateur l'envoie) diffère. Aucune authentification de compte :
        seule la provenance compte, et le seul rempart contre une page tierce
        qui actionnerait l'API à l'insu de qui la visite. Un client HTTP
        quelconque (tests, curl local) n'envoie pas Origin : seul Host,
        toujours présent, est alors regardé.

        trusted_host : une liste d'entrées séparées par des virgules, chacune
        un nom d'hôte, une IP ou un sous-réseau CIDR (cf. _hote_correspond).
        Ce réglage relâche Host lui-même : la garantie ne vient plus de la
        boucle locale mais du réseau auquel on se lie (tunnel privé, LAN) ;
        un sous-réseau est un choix à assumer pour un LAN de confiance, jamais
        pour un tunnel qui doit rester aussi étroit qu'une seule machine. N'a
        de sens qu'avec une liaison sur cette adresse précise, jamais
        0.0.0.0. Chaque refus est signalé à _journaliser_acces_refuse()."""
        hote_confiance = (self.trusted_host or "").strip()
        hote = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        hote_est_local = hote in self._HOTES_LOCAUX
        hote_est_confiance = self._hote_correspond(hote, hote_confiance)
        if not hote_est_local and not hote_est_confiance:
            self._journaliser_acces_refuse(
                f"Host {hote!r} ni local, ni couvert par trusted_host {hote_confiance!r}")
            return False
        # Host est fourni par le client et se forge avec curl : il ne constitue
        # pas une frontière réseau à lui seul. Hors proxy local ou tunnel de
        # confiance, seule une vraie adresse cliente de boucle locale est admise.
        client = str(getattr(self, "client_address", ("127.0.0.1", 0))[0])
        try:
            boucle_locale = ipaddress.ip_address(client).is_loopback
        except ValueError:
            boucle_locale = False
        proxy_local = bool(self.variable_proxy_local) and             os.environ.get(self.variable_proxy_local) == "1"
        if not boucle_locale and not proxy_local and not hote_est_confiance:
            self._journaliser_acces_refuse(
                f"ni boucle locale (IP cliente {client!r}), ni "
                f"{self.variable_proxy_local or 'proxy local'}, ni trusted_host "
                f"(Host {hote!r}, trusted_host {hote_confiance!r})")
            return False
        origine = self.headers.get("Origin")
        if origine:
            # ValueError sur une Origin manifestement invalide (IPv6 mal
            # fermée, ex. « http://[abc ») : refuser plutôt que laisser
            # urlparse planter la requête.
            try:
                origine_hote = urlparse(origine).hostname
            except ValueError:
                origine_hote = None
            origine_ok = origine_hote is not None and (
                origine_hote in self._HOTES_LOCAUX
                or self._hote_correspond(origine_hote, hote_confiance))
            if not origine_ok:
                self._journaliser_acces_refuse(
                    f"Origin {origine!r} (hostname {origine_hote!r}) "
                    f"ni local, ni couvert par trusted_host {hote_confiance!r}")
                return False
        return True

    # Fetch Metadata (Chrome 76+, Firefox 90+, Safari 16.4+) : un GET simple
    # venu d'un autre site (<img src>, fetch no-cors) n'envoie pas Origin, mais
    # déclenchait les effets de bord des routes /api/*. Les clients hors
    # navigateur n'envoient pas cet en-tête et restent acceptés.
    _SITES_ADMIS = ("same-origin", "none")

    def requete_inter_sites(self) -> bool:
        site = self.headers.get("Sec-Fetch-Site")
        return site is not None and site.strip().lower() not in self._SITES_ADMIS

    # ------------------------------------------------------------- réponses

    def send_json(self, data, status: int = 200) -> None:
        """Réponse JSON en UTF-8 lisible (« é », pas « \u00e9 ») : la page décode
        l'un comme l'autre, mais un journal ou un test lit le premier. Le tampon est
        vidé tout de suite : une application qui s'arrête juste après avoir répondu
        (redémarrage) ne doit pas laisser la réponse dans le tampon."""
        corps = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)
        self.wfile.flush()

    def send_static(self, path: Optional[Path], content_type: str,
                    cache: Optional[str] = None) -> None:
        if path is None:
            self.send_error(404)
            return
        try:
            corps = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if cache:
            self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def send_index(self) -> None:
        # Servi tel quel : index.html référence lui-même ses feuilles de style
        # et ses scripts.
        self.send_static(self.gui_dir / "index.html", "text/html; charset=utf-8")

    # --------------------------------------------------------------- GET

    def do_GET(self) -> None:
        if not self.hote_autorise():
            self.send_error(403)
            return
        # urlparse lève ValueError sur certaines formes manifestement
        # invalides (IPv6 mal fermé, ex. « //[abc »).
        try:
            parsed = urlparse(self.path)
        except ValueError:
            self.send_error(400)
            return
        route = parsed.path

        if route == "/":
            self.send_index()
            return
        if route in FICHIERS_COMMUNS:
            # Bandeau de mise à jour et bouton Réglages communs (maj_install.routes
            # en fournit l'API).
            self.send_static(Path(__file__).with_name(FICHIERS_COMMUNS[route]),
                             "text/javascript; charset=utf-8")
            return
        statique = self.fichiers_statiques.get(route)
        if statique is not None:
            self.send_static(self.gui_dir / statique[0], statique[1])
            return
        if route == "/favicon.ico":
            # Icône de l'onglet (celle de la zone de notification), statique :
            # le navigateur peut la garder une semaine.
            self.send_static(self.favicon, "image/x-icon",
                             cache="public, max-age=604800")
            return

        if not route.startswith(PREFIXE_API):
            self.send_error(404)
            return
        if self.requete_inter_sites():
            self.send_error(403)
            return
        clef = route[len(PREFIXE_API):]
        gestionnaire = self.api_routes.get(clef)
        if gestionnaire is None:
            self.send_error(404)
            return
        lire_arguments = self.arguments_get.get(clef)
        arguments = lire_arguments(parse_qs(parsed.query)) if lire_arguments else ()
        self._repondre(gestionnaire, *arguments)

    # --------------------------------------------------------------- POST

    # Au-delà, le corps d'une requête refusée n'est pas lu : la connexion est
    # fermée sans lui, un envoi abusif ne doit pas occuper le serveur.
    _CORPS_REFUSE_MAX = 1 << 20

    def _refuser(self, code: int) -> None:
        """Répond une erreur à un POST après avoir lu son corps. Fermée avec
        des octets non lus, la connexion part en RST sous Windows et le client
        perd la réponse (WinError 10053) : sur 300 POST vers une route
        inconnue, 14 réponses 404 perdues."""
        try:
            longueur = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            longueur = 0
        if 0 < longueur <= self._CORPS_REFUSE_MAX:
            self.rfile.read(longueur)
        self.send_error(code)

    def do_POST(self) -> None:
        if not self.hote_autorise():
            self._refuser(403)
            return
        try:
            route = urlparse(self.path).path
        except ValueError:
            self._refuser(400)
            return
        if not route.startswith(PREFIXE_API):
            self._refuser(404)
            return
        if self.requete_inter_sites():
            self._refuser(403)
            return
        gestionnaire = self.post_routes.get(route[len(PREFIXE_API):])
        if gestionnaire is None:
            self._refuser(404)
            return
        try:
            longueur = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.send_error(400)
            return
        if longueur < 0:
            # rfile.read(-1) lirait jusqu'à la fermeture de la connexion :
            # en keep-alive HTTP/1.1, le fil du serveur resterait bloqué.
            self.send_error(400)
            return
        try:
            payload = json.loads(self.rfile.read(longueur) or b"{}")
        except ValueError:
            # JSONDecodeError, mais aussi UnicodeDecodeError (corps non UTF-8),
            # toutes deux sous-classes de ValueError.
            self.send_json({"error": "corps JSON illisible"}, 400)
            return
        self._repondre(gestionnaire, payload)

    def _repondre(self, gestionnaire: Callable, *args) -> None:
        """Appelle la route et renvoie son résultat en JSON. Une exception de
        la route devient une réponse 500 JSON : sans ça, socketserver coupait
        la connexion sans réponse et le fetch() de la page échouait en erreur
        réseau opaque."""
        try:
            resultat = gestionnaire(*args)
        except Exception as exc:
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            return
        self.send_json(resultat)


class Server(ThreadingHTTPServer):
    # Sous Windows, SO_REUSEADDR laisserait deux processus écouter le même
    # port : c'est ce qui permet de détecter une instance déjà lancée.
    allow_reuse_address = os.name != "nt"


class Server6(Server):
    address_family = socket.AF_INET6


def classe_serveur(adresse: str):
    """Serveur IPv4 ou IPv6 selon l'adresse d'écoute (``fd7a::1``...)."""
    return Server6 if ":" in adresse else Server


def est_boucle_locale(adresse: str) -> bool:
    try:
        return ipaddress.ip_address(adresse).is_loopback
    except ValueError:
        return adresse == "localhost"


class EcouteHoteConfiance:
    """Écoute supplémentaire sur l'adresse de l'hôte de confiance (VPN maillé
    type Tailscale/WireGuard), au même port et avec le même Handler que le
    serveur principal, qui reste sur la boucle locale.

    Sans elle, l'accès distant exigeait --bind <adresse VPN> : la page locale
    (127.0.0.1) cessait alors de répondre, et un serveur démarré à
    l'ouverture de session (sans --bind) n'était jamais joignable à distance.
    L'adresse n'existe pas toujours au démarrage (VPN pas encore connecté) :
    nouvel essai toutes les ``delai_s`` secondes, jusqu'au succès ou jusqu'à
    un changement d'hôte. Hôte = nom (MagicDNS) ou adresse : ses adresses
    résolues sont écoutées, sauf celles de la boucle locale (déjà servies).

    ``handler`` : la classe du serveur principal (attribut de classe ou
    argument), pour que les deux écoutes partagent le même état."""

    handler = Handler

    def __init__(self, port: int, delai_s: float = 30.0, handler=None):
        self.port = port
        self.delai_s = delai_s
        if handler is not None:
            self.handler = handler
        self.etat = "inactif"          # inactif | actif | en attente
        self._verrou = threading.Lock()
        self._hote = ""
        self._serveurs = []
        self._arret = threading.Event()

    def definir(self, hote: str) -> str:
        """(Re)configure l'écoute pour ``hote`` ('' : aucune) ; rend l'état."""
        hote = (hote or "").strip()
        with self._verrou:
            self._stopper()
            self._hote = hote
            self._arret = threading.Event()
            if not hote:
                self.etat = "inactif"
            elif self._essayer(hote):
                self.etat = "actif"
            else:
                self.etat = "en attente"
                threading.Thread(target=self._reessayer, args=(hote, self._arret),
                                 daemon=True).start()
            return self.etat

    def arreter(self) -> None:
        with self._verrou:
            self._stopper()
            self._hote = ""
            self.etat = "inactif"

    def _adresses(self, hote):
        try:
            infos = socket.getaddrinfo(hote, self.port, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError, OSError):
            return []
        adresses = []
        for info in infos:
            adresse = info[4][0]
            if adresse not in adresses:
                adresses.append(adresse)
        return adresses

    def _essayer(self, hote) -> bool:
        adresses = self._adresses(hote)
        if adresses and all(est_boucle_locale(a) for a in adresses):
            return True                # déjà servi par le serveur principal
        for adresse in adresses:
            if est_boucle_locale(adresse):
                continue
            try:
                serveur = classe_serveur(adresse)((adresse, self.port), self.handler)
            except OSError:
                continue               # adresse absente de cette machine (VPN arrêté)
            threading.Thread(target=serveur.serve_forever, daemon=True).start()
            self._serveurs.append(serveur)
        return bool(self._serveurs)

    def _reessayer(self, hote, arret) -> None:
        while not arret.wait(self.delai_s):
            with self._verrou:
                if arret.is_set() or self._hote != hote:
                    return
                if self._essayer(hote):
                    self.etat = "actif"
                    return

    def _stopper(self) -> None:
        self._arret.set()
        for serveur in self._serveurs:
            serveur.shutdown()
            serveur.server_close()
        self._serveurs = []


def demarrer(*, bind: str, port: int, trusted_host: str, gui_dir: Path,
             api_routes: dict, post_routes: Optional[dict] = None,
             favicon: Optional[Path] = None, handler=None) -> Server:
    """Crée et démarre le serveur (thread daemon, s'éteint avec le processus).
    Retourne l'instance pour permettre server.shutdown()/server_close() par
    l'appelant. Lève OSError si le port est déjà occupé (laissé à l'appelant :
    message adapté à son propre contexte).

    ``handler`` : la sous-classe de Handler de l'application. Sans elle, une
    sous-classe neuve : les réglages ne touchent jamais la classe de base."""
    if handler is None:
        handler = type("Handler", (Handler,), {})
    handler.trusted_host = trusted_host
    handler.gui_dir = gui_dir
    handler.favicon = favicon
    handler.api_routes = api_routes
    handler.post_routes = post_routes or {}
    serveur = classe_serveur(bind)((bind, port), handler)
    serveur.handler = handler
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    return serveur


def port_libre(bind: str, port: int) -> bool:
    """Vrai si ``port`` peut être écouté sur ``bind`` à cet instant."""
    famille = socket.AF_INET6 if ":" in bind else socket.AF_INET
    with socket.socket(famille, socket.SOCK_STREAM) as sonde:
        if os.name != "nt":
            # Même règle que Server.allow_reuse_address.
            sonde.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sonde.bind((bind, port))
        except OSError:
            return False
    return True


def premier_port_libre(bind: str, port_depart: int, taille: int, **parametres):
    """Essaie port_depart puis les suivants sur ``taille`` ports et rend
    (serveur, port) sur le premier qui accepte, ou (None, None) si toute la
    plage est prise. ``parametres`` va à demarrer() (sans bind ni port)."""
    for port in range(port_depart, port_depart + taille):
        try:
            return demarrer(bind=bind, port=port, **parametres), port
        except OSError:
            continue
    return None, None


def instance_existante(application: str, bind: str, port: int, timeout: float = 1.0) -> bool:
    """Vrai si un serveur de ``application`` (et pas un service tiers qui
    occuperait ce port par coïncidence) répond déjà sur bind:port : sa route
    /api/init annonce {"app": application}."""
    hote = f"[{bind}]" if ":" in bind else bind
    try:
        with urllib.request.urlopen(f"http://{hote}:{port}/api/init",
                                    timeout=timeout) as reponse:
            return json.loads(reponse.read()).get("app") == application
    except Exception:
        return False
