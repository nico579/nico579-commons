"""Relevé de ce qui est commun aux dépôts des applications : fonctions et
classes de même nom ou de même corps, fichiers de même nom, doublons stricts,
avec leur degré de ressemblance. C'est l'outil derrière ANALYSE-MUTUALISATION-
2026-09-29.md : le refaire donne les mêmes mesures, ou dit ce qui a bougé.

    python outils/inventaire_communs.py
    python outils/inventaire_communs.py --racine D:/dev
    python outils/inventaire_communs.py --depot b2v=D:/dev/blink2video --min-lignes 8

Par défaut, les dépôts sont cherchés à côté de celui de la bibliothèque
(blink-commons, lidar2map-commons, gpxsolar-commons, watch2notif-commons, les
noms des copies de travail de Nico). Lecture seule : rien n'est modifié dans
aucun dépôt, seul un rapport est écrit sur la sortie standard.

Ce qui est mesuré :
  A. les définitions de même nom présentes dans au moins deux dépôts, et si
     elles sont identiques (même arbre syntaxique, sans docstring ni numéros de
     ligne) ou à quel point elles se ressemblent ;
  B. les définitions de même corps sous des noms différents ;
  C. les fichiers de même nom (Python, JavaScript, spécifications, scripts,
     workflows) et leur ressemblance ligne à ligne ;
  D. les doublons stricts : les lignes qu'on supprimerait en ne gardant qu'un
     exemplaire de chaque définition identique.
Les tests et les dossiers de construction sont ignorés. Une ressemblance mesure
la forme, pas l'usage : lire les copies avant de conclure.

Bibliothèque standard seule.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import itertools
import sys
from collections import defaultdict
from pathlib import Path

DEPOTS_PAR_DEFAUT = {
    "b2v": "blink-commons",
    "l2m": "lidar2map-commons",
    "gpx": "gpxsolar-commons",
    "w2n": "watch2notif-commons",
}
EXCLUS = {".git", "build", "dist", "dist-win7", "build-win7", "build_venv", "build_venv_win7",
          "__pycache__", "tests", "Screenshots", "node_modules", ".github", "venv", "docs"}
SUFFIXES_FICHIERS = (".py", ".js", ".spec", ".ps1", ".sh", ".yml")
MIN_LIGNES = 6


def fichiers(racine: Path, suffixe: str):
    """Les fichiers de ``suffixe`` d'un dépôt, hors tests et dossiers exclus."""
    for chemin in sorted(racine.rglob("*" + suffixe)):
        rel = chemin.relative_to(racine)
        if any(p in EXCLUS for p in rel.parts):
            continue
        if suffixe == ".py" and chemin.name.startswith(("test_", "_test_")):
            continue
        yield chemin


def _sans_doc(noeud):
    corps = list(noeud.body)
    if (corps and isinstance(corps[0], ast.Expr)
            and isinstance(getattr(corps[0], "value", None), ast.Constant)
            and isinstance(corps[0].value.value, str)):
        corps = corps[1:]
    return corps


def empreinte(noeud) -> str:
    """Structure du corps, sans docstring, sans numéro de ligne, sans nom."""
    bloc = ast.Module(body=_sans_doc(noeud), type_ignores=[])
    return hashlib.sha1(ast.dump(bloc, annotate_fields=False).encode()).hexdigest()[:12]


def definitions(chemin: Path) -> list:
    """Fonctions, classes et méthodes d'un fichier Python (liste vide si le
    fichier ne se lit pas)."""
    try:
        source = chemin.read_text(encoding="utf-8")
        arbre = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return []
    sortie = []

    def visiter(noeuds, prefixe=""):
        for n in noeuds:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                nom = prefixe + n.name
                lignes = (n.end_lineno or n.lineno) - n.lineno + 1
                texte = "\n".join(
                    ligne.rstrip() for ligne in (ast.get_source_segment(source, n) or "").splitlines()
                    if ligne.strip() and not ligne.strip().startswith("#"))
                sortie.append({"nom": nom, "lignes": lignes, "hash": empreinte(n), "texte": texte,
                               "fichier": chemin.name, "debut": n.lineno})
                if isinstance(n, ast.ClassDef):
                    visiter(n.body, nom + ".")

    visiter(arbre.body)
    return sortie


def ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.splitlines(), b.splitlines(), autojunk=False).ratio()


def analyser(depots: dict, min_lignes: int = MIN_LIGNES) -> str:
    """Le rapport, en texte, pour ``depots`` (sigle -> dossier)."""
    sortie = []
    ecrire = sortie.append

    defs = {s: [d for f in fichiers(r, ".py") for d in definitions(f)] for s, r in depots.items()}
    for sigle, racine in depots.items():
        chemins = list(fichiers(racine, ".py"))
        lignes = sum(len(f.read_text(encoding="utf-8", errors="replace").splitlines()) for f in chemins)
        ecrire(f"# {sigle}: {len(chemins)} fichiers .py, {len(defs[sigle])} définitions, "
               f"{lignes} lignes (hors tests)")

    # A. Même nom dans au moins deux dépôts
    par_nom = defaultdict(list)
    for sigle, ds in defs.items():
        for d in ds:
            if d["lignes"] >= min_lignes:
                par_nom[d["nom"]].append((sigle, d))
    ecrire(f"\n## A. MÊME NOM, >= {min_lignes} lignes, dans au moins deux dépôts "
           "(identique / ressemblance)")
    lignes_a = []
    for nom, occ in par_nom.items():
        if len({s for s, _ in occ}) < 2:
            continue
        par_depot = {}
        for s, d in occ:                      # une occurrence par dépôt, la plus longue
            if s not in par_depot or d["lignes"] > par_depot[s]["lignes"]:
                par_depot[s] = d
        paires = []
        for (s1, d1), (s2, d2) in itertools.combinations(sorted(par_depot.items()), 2):
            etat = "IDENTIQUE" if d1["hash"] == d2["hash"] else "%.0f%%" % (100 * ratio(d1["texte"], d2["texte"]))
            paires.append(f"{s1}~{s2}:{etat}")
        taille = "/".join(f"{s}{par_depot[s]['lignes']}" for s in sorted(par_depot))
        fichiers_ = ",".join(f"{s}:{par_depot[s]['fichier']}" for s in sorted(par_depot))
        lignes_a.append((len(par_depot), max(d["lignes"] for d in par_depot.values()),
                         nom, taille, fichiers_, " ".join(paires)))
    for n, _mx, nom, taille, fich, paires in sorted(lignes_a, key=lambda x: (-x[0], -x[1], x[2])):
        ecrire(f"[{n} dépôts] {nom}  ({taille})\n     {fich}\n     {paires}")

    # B. Même corps sous des noms différents
    par_hash = defaultdict(list)
    for sigle, ds in defs.items():
        for d in ds:
            if d["lignes"] >= min_lignes:
                par_hash[d["hash"]].append((sigle, d))
    ecrire("\n## B. MÊME CORPS (arbre syntaxique identique) sous des noms différents")
    for occ in par_hash.values():
        if len({s for s, _ in occ}) >= 2 and len({d["nom"] for _, d in occ}) > 1:
            ecrire(" / ".join(f"{s}:{d['fichier']}::{d['nom']}({d['lignes']})" for s, d in occ))

    # C. Fichiers de même nom
    ecrire("\n## C. FICHIERS DE MÊME NOM (hors tests), ressemblance ligne à ligne")
    par_nomf = defaultdict(dict)
    for sigle, racine in depots.items():
        for suffixe in SUFFIXES_FICHIERS:
            for f in fichiers(racine, suffixe):
                par_nomf[f.name.lstrip("_")][sigle] = f
    for cle, occ in sorted(par_nomf.items()):
        if len(occ) < 2:
            continue
        textes = {s: f.read_text(encoding="utf-8", errors="replace") for s, f in occ.items()}
        paires = [f"{s1}~{s2}:{100 * ratio(t1, t2):.0f}%"
                  for (s1, t1), (s2, t2) in itertools.combinations(sorted(textes.items()), 2)]
        taille = "/".join(f"{s}{len(t.splitlines())}" for s, t in sorted(textes.items()))
        ecrire(f"{cle}  ({taille})  " + " ".join(paires))

    # D. Doublons stricts
    ecrire("\n## D. DOUBLONS STRICTS : lignes qu'on supprimerait en gardant un seul exemplaire "
           "de chaque définition identique")
    total, groupes = 0, 0
    par_fichiers = defaultdict(int)
    for occ in par_hash.values():
        if len({s for s, _ in occ}) < 2:
            continue
        groupes += 1
        un_par_depot = {}
        for s, d in occ:
            un_par_depot.setdefault(s, d)
        extra = sum(d["lignes"] for d in list(un_par_depot.values())[1:])
        total += extra
        par_fichiers[tuple(sorted((s, d["fichier"]) for s, d in un_par_depot.items()))] += extra
    ecrire(f"{groupes} groupes de définitions identiques entre dépôts, {total} lignes en double")
    for cle, n in sorted(par_fichiers.items(), key=lambda x: (-x[1], x[0]))[:15]:
        ecrire(f"  {n:4} lignes  " + " = ".join(f"{s}:{f}" for s, f in cle))
    return "\n".join(sortie)


def lire_depots(racine: Path, surcharges: list) -> dict:
    """Sigle -> dossier : les noms par défaut sous ``racine``, puis les
    surcharges « sigle=chemin »."""
    depots = {sigle: racine / nom for sigle, nom in DEPOTS_PAR_DEFAUT.items()}
    for surcharge in surcharges:
        sigle, separateur, chemin = surcharge.partition("=")
        if not separateur or not sigle or not chemin:
            raise SystemExit(f"--depot attend sigle=chemin, reçu : {surcharge!r}")
        depots[sigle] = Path(chemin)
    manquants = [f"{s} ({d})" for s, d in depots.items() if not d.is_dir()]
    if manquants:
        raise SystemExit("dépôt introuvable : " + ", ".join(manquants))
    return depots


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--racine", type=Path, default=Path(__file__).resolve().parents[2],
                        help="dossier qui contient les dépôts (défaut : celui qui contient "
                             "le dépôt de la bibliothèque)")
    parser.add_argument("--depot", action="append", default=[], metavar="SIGLE=CHEMIN",
                        help="un dépôt ailleurs, ou un dépôt de plus (répétable)")
    parser.add_argument("--min-lignes", type=int, default=MIN_LIGNES,
                        help="taille minimale d'une définition comparée (défaut : %(default)s)")
    args = parser.parse_args(argv)
    depots = lire_depots(args.racine, args.depot)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(analyser(depots, args.min_lignes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
