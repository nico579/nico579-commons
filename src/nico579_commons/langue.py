"""Choix de la langue de l'interface, FR ou EN, le même dans les quatre applications.

Le sélecteur FR / EN existait en quatre exemplaires, chacun avec sa façon de garder le
choix (configuration, préférences, fichier) et aucun ne prévenait les éléments communs de
la page (bouton Réglages, bandeau de mise à jour) quand la langue changeait. Ici :

  - ce module donne la route /api/langue, qui lit et enregistre le choix par les deux
    fonctions que l'application fournit ;
  - /nico579-langue.js (langue.js) dessine les boutons dans les emplacements
    ``data-nico579-langue`` de la page, pose l'attribut ``lang`` de <html> et annonce
    chaque changement par l'événement ``nico579-langue`` (``detail.code``).

Les textes de l'application, eux, restent à l'application : elle écoute l'événement et
applique son propre dictionnaire.

Bibliothèque standard seule.
"""

from __future__ import annotations

from typing import Callable, Optional

CODES = ("fr", "en")


def routes(lire: Callable[[], Optional[str]], ecrire: Callable[[str], object],
           enregistrer_detection: bool = False) -> tuple:
    """(routes GET, routes POST) à ajouter à celles de l'application.

    `lire()` rend le code enregistré ("fr" ou "en"), ou autre chose (None) tant que
    l'utilisateur n'a rien choisi : la page prend alors la langue de son navigateur.
    `ecrire(code)` l'enregistre. Quand la page n'a fait que DÉTECTER sa langue (rien
    d'enregistré encore), la détection n'est gardée que si `enregistrer_detection` :
    blink2video en a besoin pour que le menu de son icône suive la langue de la page dès
    le premier lancement ; les autres laissent le navigateur décider à chaque fois."""
    def etat() -> dict:
        code = lire()
        return {"code": code if code in CODES else None, "codes": list(CODES)}

    def changer(payload) -> dict:
        payload = payload or {}
        code = payload.get("code")
        if code not in CODES:
            return {"ok": False, "error": "code de langue invalide"}
        if payload.get("detectee") and not enregistrer_detection:
            return {"ok": True, "code": code, "enregistre": False}
        ecrire(code)
        return {"ok": True, "code": code, "enregistre": True}

    return {"langue": etat}, {"langue": changer}
