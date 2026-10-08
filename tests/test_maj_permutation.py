"""Tests du remplacement élément par élément de maj_install : permutation avec
marqueur de reprise et retour arrière, nettoyage, réservation, et l'enchaînement
finaliser (arrêt, attente, permutation, relance).

Repris des tests de blink2video (test_maj_restauration.py, test_maj_finaliser_arret.py,
test_maj_instances_stockage.py), dont c'est la logique : un échec de copie à mi-chemin
remet tout en l'état, un retour arrière incomplet bloque tout nettoyage et tout
nouvel essai, un arrêt brutal laisse sa marque, deux opérations concurrentes sur la
même installation ne se marchent pas dessus. Installation entièrement factice.

    python -m unittest discover -s tests
"""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nico579_commons import atomique, maj_install as mi  # noqa: E402

ELEMENTS = ("exemple.exe", "exemple", "_internal")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="maj-permutation-")
        self.addCleanup(self._tmp.cleanup)
        racine = Path(self._tmp.name).resolve()
        self.installe, self.neuf = racine / "installe", racine / "neuf"
        for dossier in (self.installe, self.neuf):
            (dossier / "_internal").mkdir(parents=True)
        (self.installe / "exemple.exe").write_bytes(b"original")
        (self.installe / "_internal" / "lib").write_bytes(b"lib-originale")
        (self.neuf / "exemple.exe").write_bytes(b"neuf")
        (self.neuf / "_internal" / "lib").write_bytes(b"lib-neuve")
        (self.installe / "clip.mp4").write_bytes(b"clip conserve")
        self.marqueur = self.installe / mi.MARQUEUR_PERMUTATION
        self.messages = []

    def permuter(self, **options):
        options.setdefault("ecrire", self.messages.append)
        return mi.permuter(self.neuf, self.installe, ELEMENTS, **options)

    def nettoyer(self, **options):
        return mi.nettoyer_restes(self.installe, ELEMENTS, **options)

    def refuser_bibliotheques(self, source, cible):
        if source.name == "_internal":
            raise OSError("copie refusée")
        mi.poser(source, cible)

    def provoquer_retour_incomplet(self):
        remplacer = os.replace

        def refuser_retour(source, cible):
            if Path(source).name == "exemple.exe.ancien":
                raise PermissionError("restauration refusée")
            remplacer(source, cible)

        with mock.patch.object(os, "replace", side_effect=refuser_retour):
            with self.assertRaises(mi.RestaurationIncomplete):
                self.permuter(poser=self.refuser_bibliotheques)


class Permutation(Base):
    def test_remplace_les_elements_et_garde_le_reste(self):
        self.assertTrue(self.permuter())
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"neuf")
        self.assertEqual((self.installe / "_internal" / "lib").read_bytes(), b"lib-neuve")
        self.assertEqual((self.installe / "clip.mp4").read_bytes(), b"clip conserve")
        self.assertEqual((self.installe / "exemple.exe.ancien").read_bytes(), b"original")
        self.assertFalse(self.marqueur.exists())

    def test_echec_simple_restaure_et_autorise_nouvelle_tentative(self):
        self.assertFalse(self.permuter(poser=self.refuser_bibliotheques))
        self.assertFalse(self.marqueur.exists())
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"original")
        self.assertEqual((self.installe / "_internal" / "lib").read_bytes(), b"lib-originale")
        self.assertTrue(self.permuter())
        self.assertEqual((self.installe / "exemple.exe.ancien").read_bytes(), b"original")

    def test_l_echec_est_dit_dans_la_langue_demandee(self):
        self.permuter(poser=self.refuser_bibliotheques, langue="en")
        self.assertTrue(any("Replacement failed" in m for m in self.messages))
        self.messages.clear()
        self.permuter(poser=self.refuser_bibliotheques, langue="fr")
        self.assertTrue(any("Échec du remplacement" in m for m in self.messages))

    def test_restauration_incomplete_preserve_original_et_la_reprise_le_garde(self):
        # Retour arrière impossible : le marqueur reste et la sauvegarde aussi. La mise à jour
        # suivante ne purge rien et reprend la permutation sans toucher à cette sauvegarde.
        self.provoquer_retour_incomplet()
        sauvegarde = self.installe / "exemple.exe.ancien"
        self.assertEqual(sauvegarde.read_bytes(), b"original")
        self.assertTrue(self.marqueur.exists())
        self.assertTrue(self.nettoyer())                      # interrompue : rien de purgé
        self.assertEqual(sauvegarde.read_bytes(), b"original")
        self.assertTrue(self.permuter())
        self.assertEqual(sauvegarde.read_bytes(), b"original")  # jamais écrasée
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"neuf")
        self.assertEqual((self.installe / "_internal" / "lib").read_bytes(), b"lib-neuve")
        self.assertFalse(self.marqueur.exists())
        self.assertEqual((self.installe / "clip.mp4").read_bytes(), b"clip conserve")

    def test_element_neuf_partiellement_copie_est_retire_au_retour(self):
        (self.neuf / "exemple").write_bytes(b"autre executable")

        def poser_puis_echouer(source, cible):
            mi.poser(source, cible)
            if source.name == "exemple":
                raise OSError("copie partielle d'un élément nouveau")

        self.assertFalse(self.permuter(poser=poser_puis_echouer))
        self.assertFalse((self.installe / "exemple").exists())
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"original")
        self.assertFalse(self.marqueur.exists())

    def test_arret_brutal_puis_reprise_par_la_mise_a_jour_suivante(self):
        # Issue 95 de blink2video : l'installation est tuée pendant la copie de _internal.
        # Avant, chaque mise à jour suivante refusait jusqu'à ce qu'on supprime le marqueur à
        # la main. Maintenant elle reprend : tout devient la nouvelle version, l'ancienne
        # reste sauvegardée jusqu'au succès, puis le ménage suivant efface les restes.
        def interrompre_dans_les_bibliotheques(source, cible):
            if source.name == "_internal":
                cible.mkdir()
                (cible / "lib").write_bytes(b"lib-a-moitie")
                raise KeyboardInterrupt
            mi.poser(source, cible)

        with self.assertRaises(KeyboardInterrupt):
            self.permuter(poser=interrompre_dans_les_bibliotheques)
        self.assertTrue(self.marqueur.exists())
        self.assertEqual((self.installe / "exemple.exe.ancien").read_bytes(), b"original")
        self.assertEqual((self.installe / "_internal.ancien" / "lib").read_bytes(), b"lib-originale")

        self.assertTrue(self.nettoyer())                      # rien de purgé
        self.assertTrue((self.installe / "_internal.ancien").exists())
        self.messages.clear()
        self.assertTrue(self.permuter())
        self.assertTrue(any("reprise" in m.lower() or "resum" in m.lower() for m in self.messages))
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"neuf")
        self.assertEqual((self.installe / "_internal" / "lib").read_bytes(), b"lib-neuve")
        self.assertFalse(self.marqueur.exists())
        # Les sauvegardes de la première tentative sont restées intactes jusqu'au bout.
        self.assertEqual((self.installe / "exemple.exe.ancien").read_bytes(), b"original")
        self.assertEqual((self.installe / "_internal.ancien" / "lib").read_bytes(), b"lib-originale")

        self.assertFalse(self.nettoyer())                     # plus rien d'interrompu
        for reste in ("exemple.exe.ancien", "_internal.ancien", "exemple.exe.reprise",
                      "_internal.reprise"):
            self.assertFalse((self.installe / reste).exists(), reste)
        self.assertEqual((self.installe / "clip.mp4").read_bytes(), b"clip conserve")

    def test_reprise_qui_echoue_garde_les_sauvegardes_et_le_marqueur(self):
        def interrompre(source, cible):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self.permuter(poser=interrompre)
        # Seconde tentative : la copie des bibliothèques échoue, retour arrière.
        self.assertFalse(self.permuter(poser=self.refuser_bibliotheques))
        self.assertTrue(self.marqueur.exists())                # toujours interrompue
        self.assertEqual((self.installe / "exemple.exe.ancien").read_bytes(), b"original")
        self.assertTrue(self.nettoyer())
        self.assertEqual((self.installe / "exemple.exe.ancien").read_bytes(), b"original")

    def test_marqueur_refuse_ne_modifie_aucun_fichier(self):
        ouvrir = Path.open

        def refuser_marqueur(chemin, *args, **kwargs):
            if chemin == self.marqueur:
                raise PermissionError("marqueur refusé")
            return ouvrir(chemin, *args, **kwargs)

        with mock.patch.object(Path, "open", autospec=True, side_effect=refuser_marqueur):
            self.assertFalse(self.permuter())
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"original")
        self.assertFalse(self.marqueur.exists())

    def test_les_liens_internes_du_bundle_sont_conserves(self):
        if os.name == "nt":
            self.skipTest("créer un lien exige un privilège sous Windows")
        os.symlink("lib", self.neuf / "_internal" / "lien")
        self.assertTrue(self.permuter())
        lien = self.installe / "_internal" / "lien"
        self.assertTrue(lien.is_symlink())
        self.assertEqual(lien.read_bytes(), b"lib-neuve")

    def test_element_absent_du_neuf_est_laisse_tel_quel(self):
        (self.neuf / "exemple.exe").unlink()
        self.assertTrue(self.permuter())
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"original")
        self.assertFalse((self.installe / "exemple.exe.ancien").exists())


class Nettoyage(Base):
    def test_efface_les_sauvegardes_et_appelle_le_menage_de_l_application(self):
        self.permuter()
        # La sauvegarde d'un dossier est un dossier : elle part aussi.
        self.assertTrue((self.installe / "_internal.ancien").is_dir())
        vus = []
        self.nettoyer(apres=vus.append)
        self.assertFalse((self.installe / "exemple.exe.ancien").exists())
        self.assertFalse((self.installe / "_internal.ancien").exists())
        self.assertEqual(vus, [self.installe])
        self.assertEqual((self.installe / "clip.mp4").read_bytes(), b"clip conserve")
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"neuf")

    def test_ne_purge_rien_tant_qu_une_permutation_est_interrompue(self):
        self.marqueur.write_text("{}", encoding="utf-8")
        (self.installe / "exemple.exe.ancien").write_bytes(b"sauvegarde")
        vus = []
        self.assertTrue(self.nettoyer(apres=vus.append))
        self.assertEqual((self.installe / "exemple.exe.ancien").read_bytes(), b"sauvegarde")
        self.assertEqual(vus, [self.installe])                # le ménage propre à l'appli, oui

    def test_erreur_du_corps_n_est_pas_requalifiee_en_echec_d_acquisition(self):
        erreur = PermissionError("échec du corps réservé")
        with self.assertRaises(PermissionError) as recue:
            self.nettoyer(apres=mock.Mock(side_effect=erreur))
        self.assertIs(recue.exception, erreur)


class Reservation(Base):
    def test_reservation_refusee_ne_modifie_ni_programme_ni_sauvegarde(self):
        sauvegarde = self.installe / "exemple.exe.ancien"
        sauvegarde.write_bytes(b"sauvegarde precedente")
        for erreur in (TimeoutError("mise à jour concurrente"),
                       PermissionError("verrou inaccessible")):
            for operation in (self.permuter, self.nettoyer):
                with self.subTest(erreur=type(erreur).__name__, operation=operation.__name__), \
                        mock.patch.object(atomique, "verrou_inter_processus", side_effect=erreur):
                    with self.assertRaises(mi.RestaurationIncomplete):
                        operation()
                    self.assertFalse(self.marqueur.exists())
                    self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"original")
                    self.assertEqual(sauvegarde.read_bytes(), b"sauvegarde precedente")

    def test_reservation_dans_le_dossier_d_installation_et_exclusive(self):
        with mi.reservation(self.installe, ".exemple-maj"):
            self.assertTrue((self.installe / ".exemple-maj.lock").exists())
            with self.assertRaises(mi.RestaurationIncomplete):
                self.permuter(nom_reservation=".exemple-maj")
            with self.assertRaises(mi.RestaurationIncomplete):
                self.nettoyer(nom_reservation=".exemple-maj")
        # Relâchée : la permutation passe.
        self.assertTrue(self.permuter(nom_reservation=".exemple-maj"))

    def test_nettoyage_en_cours_empeche_le_debut_d_une_permutation(self):
        marqueur_lu = threading.Event()
        continuer = threading.Event()
        erreurs = []
        exists = Path.exists

        def lire_marqueur(chemin):
            resultat = exists(chemin)
            if chemin == self.marqueur and threading.current_thread().name == "nettoyage-maj":
                marqueur_lu.set()
                if not continuer.wait(5):
                    raise RuntimeError("nettoyage simulé non libéré")
            return resultat

        def nettoyer():
            try:
                self.nettoyer()
            except Exception as erreur:
                erreurs.append(erreur)

        with mock.patch.object(Path, "exists", autospec=True, side_effect=lire_marqueur):
            fil = threading.Thread(target=nettoyer, name="nettoyage-maj")
            fil.start()
            try:
                self.assertTrue(marqueur_lu.wait(5))
                with self.assertRaises(mi.RestaurationIncomplete):
                    self.permuter()
                self.assertFalse(self.marqueur.exists())
                self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"original")
            finally:
                continuer.set()
                fil.join(5)
        self.assertFalse(fil.is_alive())
        self.assertEqual(erreurs, [])

    def test_nettoyage_concurrent_ne_purge_pas_la_sauvegarde_d_un_retour_incomplet(self):
        permutation_commencee = threading.Event()
        continuer = threading.Event()
        erreurs = []

        def poser(source, cible):
            if source.name == "exemple.exe":
                permutation_commencee.set()
                if not continuer.wait(5):
                    raise RuntimeError("permutation simulée non libérée")
            if source.name == "_internal":
                raise OSError("copie refusée")
            mi.poser(source, cible)

        remplacer = os.replace

        def refuser_retour(source, cible):
            if Path(source).name == "exemple.exe.ancien":
                raise PermissionError("restauration refusée")
            remplacer(source, cible)

        def permuter():
            try:
                self.permuter(poser=poser)
            except Exception as erreur:
                erreurs.append(erreur)

        sauvegarde = self.installe / "exemple.exe.ancien"
        with mock.patch.object(os, "replace", side_effect=refuser_retour):
            fil = threading.Thread(target=permuter, name="permutation-maj")
            fil.start()
            try:
                self.assertTrue(permutation_commencee.wait(5))
                self.assertEqual(sauvegarde.read_bytes(), b"original")
                with self.assertRaises(mi.RestaurationIncomplete):
                    self.nettoyer()
                self.assertEqual(sauvegarde.read_bytes(), b"original")
            finally:
                continuer.set()
                fil.join(5)
        self.assertFalse(fil.is_alive())
        self.assertEqual(len(erreurs), 1)
        self.assertIsInstance(erreurs[0], mi.RestaurationIncomplete)
        self.assertTrue(self.marqueur.exists())
        self.assertTrue(self.nettoyer())                      # interrompue : rien de purgé
        self.assertEqual(sauvegarde.read_bytes(), b"original")


class Finaliser(Base):
    """Les rappels de l'application remplacés par des doubles : on vérifie l'ordre
    des étapes et ce qui est dit, relancé ou refusé."""

    def setUp(self):
        super().setUp()
        self.journal = []
        self.dits = []
        self.vivants_restants = 0
        self.arret_reussit = True
        self.dormir = mock.Mock()

    def noter(self):
        self.journal.append("noter")
        return ["serve"]

    def arreter(self):
        self.journal.append("arreter")
        return self.arret_reussit

    def vivants(self):
        self.journal.append("vivants")
        if self.vivants_restants > 0:
            self.vivants_restants -= 1
            return True
        return False

    def relancer(self, etat):
        self.journal.append(("relancer", etat))

    def dire(self, cle, echec, **valeurs):
        self.dits.append((cle, echec))

    def finaliser(self, neuf=None, **options):
        return mi.finaliser(
            self.installe, self.neuf if neuf is None else neuf, ELEMENTS, noter=self.noter,
            arreter=self.arreter, vivants=self.vivants, relancer=self.relancer,
            dire=self.dire, dormir=self.dormir, **options)

    def test_enchainement_complet(self):
        self.assertEqual(self.finaliser(), 0)
        self.assertEqual(self.journal, ["noter", "arreter", "vivants", ("relancer", ["serve"])])
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"neuf")
        self.assertEqual([c for c, _ in self.dits], ["arret_version_en_place", "installe_dans"])
        self.dormir.assert_not_called()

    def test_arret_echoue_ne_remplace_et_ne_relance_pas(self):
        self.arret_reussit = False
        self.assertEqual(self.finaliser(), 1)
        self.assertNotIn(("relancer", ["serve"]), self.journal)
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"original")
        self.assertIn(("arret_echoue", True), self.dits)
        self.assertFalse(self.marqueur.exists())

    def test_instance_survivante_refuse_la_mise_a_jour(self):
        self.vivants_restants = 10 ** 6
        self.assertEqual(self.finaliser(tentatives_arret=3), 1)
        self.assertEqual(self.dormir.call_count, 3)
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"original")
        self.assertIn(("instance_encore_active", True), self.dits)
        self.assertNotIn(("relancer", ["serve"]), self.journal)

    def test_arret_confirme_apres_attente_autorise_la_mise_a_jour(self):
        self.vivants_restants = 2
        self.assertEqual(self.finaliser(), 0)
        self.assertEqual(self.dormir.call_count, 2)
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"neuf")

    def test_permutation_qui_ne_demarre_pas_est_retentee_puis_relance_l_ancienne(self):
        ouvrir = Path.open

        def refuser_marqueur(chemin, *args, **kwargs):
            if chemin == self.marqueur:
                raise PermissionError("marqueur refusé")
            return ouvrir(chemin, *args, **kwargs)

        with mock.patch.object(Path, "open", autospec=True, side_effect=refuser_marqueur):
            self.assertEqual(self.finaliser(tentatives_permutation=4), 1)
        self.assertEqual(self.dormir.call_count, 4)
        self.assertIn(("version_precedente_intacte", False), self.dits)
        # L'ancienne version est relancée : rien n'a été remplacé.
        self.assertEqual(self.journal[-1], ("relancer", ["serve"]))
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"original")

    def test_retour_incomplet_ne_reessaie_et_ne_relance_pas(self):
        with mock.patch.object(mi, "permuter",
                               side_effect=mi.RestaurationIncomplete("arrêt sûr")) as permuter:
            self.assertEqual(self.finaliser(), 1)
        permuter.assert_called_once()
        self.dormir.assert_not_called()
        self.assertNotIn(("relancer", ["serve"]), self.journal)
        self.assertIn(("brut", False), self.dits)

    def test_reservation_prise_par_un_autre_ne_reessaie_et_ne_relance_pas(self):
        with mi.reservation(self.installe):
            self.assertEqual(self.finaliser(), 1)
        self.dormir.assert_not_called()
        self.assertNotIn(("relancer", ["serve"]), self.journal)
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"original")

    def test_une_permutation_fournie_remplace_celle_du_module(self):
        appels = []

        def autre(neuf, installe):
            appels.append((neuf, installe))
            return True

        self.assertEqual(self.finaliser(permutation=autre), 0)
        self.assertEqual(appels, [(self.neuf, self.installe)])
        self.assertEqual((self.installe / "exemple.exe").read_bytes(), b"original")   # rien posé

    def test_sans_copie_a_faire_on_ne_fait_que_relancer(self):
        # Installation depuis les sources : git pull a déjà mis les fichiers en place.
        self.assertEqual(self.finaliser(neuf=self.installe), 0)
        self.assertEqual(self.journal[-1], ("relancer", ["serve"]))
        self.assertFalse((self.installe / "exemple.exe.ancien").exists())
        self.assertFalse(self.marqueur.exists())


class Messages(unittest.TestCase):
    def test_les_deux_langues_ont_les_memes_cles_et_les_memes_champs(self):
        import string
        fr, en = mi.LIBELLES_PERMUTATION["fr"], mi.LIBELLES_PERMUTATION["en"]
        self.assertEqual(set(fr), set(en))
        for cle in fr:
            champs = {lg: {f for _, f, _, _ in string.Formatter().parse(d[cle]) if f}
                      for lg, d in (("fr", fr), ("en", en))}
            self.assertEqual(champs["fr"], champs["en"], cle)

    def test_langue_inconnue_en_anglais(self):
        self.assertEqual(mi.texte("arret_echoue", "de"), mi.texte("arret_echoue", "en"))


if __name__ == "__main__":
    unittest.main()
