// Bouton « ⚙ Réglages… » commun aux applications qui servent une page web, avec
// ce que toutes ont en commun : la version, et la vérification des mises à jour.
// Servi par nico579_commons.serveweb à l'adresse /nico579-reglages.js ; une page
// n'a qu'à marquer sa place et le charger :
//
//   <span id="nico579-reglages"></span>   (où mettre le bouton, près de FR/EN)
//   <script src="/nico579-reglages.js"></script>
//
// Sans emplacement, le bouton se place en haut à droite de la page. Le panneau
// s'appuie sur /api/maj et /api/maj-verifier (nico579_commons.maj_install.routes),
// dont il tire aussi ses textes, dans la langue de l'application. Une application
// peut y ajouter ses propres lignes : window.nico579Reglages.ajouter(titre, element).
//
// Le bouton prend le style des boutons de la page si l'emplacement porte
// data-classe="…" ; sinon il emprunte la couleur et la police de la page.
(function () {
  'use strict';
  var donnees = null;          // dernière réponse de /api/maj
  var panneau = null;
  var corps = null;
  var statut = null;
  var sections = [];           // [{titre, element}] ajoutées par l'application
  var bouton = null;

  function el(nom, attributs, enfants) {
    var e = document.createElement(nom);
    for (var cle in (attributs || {})) { e.setAttribute(cle, attributs[cle]); }
    (enfants || []).forEach(function (enfant) {
      e.appendChild(typeof enfant === 'string' ? document.createTextNode(enfant) : enfant);
    });
    return e;
  }

  function couleurs() {
    var style = window.getComputedStyle(document.body);
    var fond = style.backgroundColor;
    if (!fond || fond === 'rgba(0, 0, 0, 0)' || fond === 'transparent') { fond = '#ffffff'; }
    return { fond: fond, texte: style.color, police: style.fontFamily };
  }

  function textes() {
    return (donnees && donnees.libelles) || {};
  }

  function dire(message) {
    if (statut) { statut.textContent = message; }
  }

  // ----------------------------------------------------------------- le panneau

  function remplir() {
    if (!corps) { return; }
    var L = textes();
    corps.textContent = '';
    var d = donnees || {};
    corps.appendChild(el('p', { style: 'margin:0 0 12px' }, [
      (L.version || 'Version {version}').replace('{version}', d.version_locale || '?')
    ]));
    var verifier = el('button', { type: 'button', style: styleBouton() },
                      [L.verifier || 'Check for updates']);
    verifier.addEventListener('click', verifierMaintenant);
    corps.appendChild(verifier);
    statut = el('p', { role: 'status', style: 'margin:10px 0 0;min-height:1.4em' });
    corps.appendChild(statut);
    afficherResultat(false);
    sections.forEach(ajouterSection);
  }

  function styleBouton() {
    return 'font:inherit;cursor:pointer;padding:4px 12px;border-radius:4px;' +
           'border:1px solid currentColor;background:transparent;color:inherit';
  }

  function afficherResultat(apresVerification) {
    if (!statut) { return; }
    var L = textes();
    var d = donnees || {};
    statut.textContent = '';
    if (d.version) {
      statut.appendChild(document.createTextNode(
        (L.disponible || 'Version {version} available.').replace('{version}', d.version) + ' '));
      if (d.possible) {
        var installer = el('button', { type: 'button', style: styleBouton() }, [L.installer || 'Install']);
        installer.addEventListener('click', function () {
          fetch('/api/maj-installer', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                        body: '{}' });
          fermer();   // le bandeau commun montre l'avancement
        });
        statut.appendChild(installer);
      } else {
        statut.appendChild(el('a', { href: d.page, target: '_blank', rel: 'noopener noreferrer',
                                     style: 'color:inherit' }, [L.voir || 'View the release']));
      }
    } else if (apresVerification) {
      statut.textContent = L.a_jour || 'You have the latest version.';
    }
    if (d.verifie_a) {
      var heure = new Date(d.verifie_a * 1000).toLocaleTimeString();
      statut.appendChild(el('div', { style: 'opacity:.7;font-size:.9em;margin-top:4px' }, [
        (L.derniere_verification || 'Last checked: {heure}').replace('{heure}', heure)]));
    }
  }

  function verifierMaintenant() {
    var L = textes();
    dire(L.verification || 'Checking…');
    fetch('/api/maj-verifier', { method: 'POST', cache: 'no-store',
                                 headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok === false) {
          dire(L.echec_verification || 'Could not check (network?).');
          return;
        }
        donnees = d;
        afficherResultat(true);
      })
      .catch(function () { dire(L.echec_verification || 'Could not check (network?).'); });
  }

  function ajouterSection(section) {
    if (!corps) { return; }
    var bloc = el('div', { style: 'margin-top:14px;padding-top:12px;border-top:1px solid rgba(128,128,128,.4)' });
    if (section.titre) { bloc.appendChild(el('div', { style: 'font-weight:600;margin-bottom:6px' }, [section.titre])); }
    bloc.appendChild(section.element);
    corps.appendChild(bloc);
  }

  function creerPanneau() {
    var c = couleurs();
    var voile = el('div', { id: 'nico579-reglages-voile', role: 'presentation',
      style: 'position:fixed;inset:0;z-index:2147483100;background:rgba(0,0,0,.45);display:flex;' +
             'align-items:flex-start;justify-content:center;padding-top:8vh' });
    var boite = el('div', { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'nico579-reglages-titre',
      style: 'background:' + c.fond + ';color:' + c.texte + ';font-family:' + c.police + ';' +
             'min-width:300px;max-width:92%;padding:16px 20px;border-radius:8px;' +
             'border:1px solid rgba(128,128,128,.6);box-shadow:0 6px 24px rgba(0,0,0,.5);box-sizing:border-box' });
    var entete = el('div', { style: 'display:flex;justify-content:space-between;align-items:center;margin-bottom:12px' });
    entete.appendChild(el('strong', { id: 'nico579-reglages-titre' }, [textes().reglages_titre || 'Settings']));
    var croix = el('button', { type: 'button', 'aria-label': textes().fermer || 'Close',
                               style: styleBouton() + ';border:none;font-size:1.3em;line-height:1' }, ['×']);
    croix.addEventListener('click', fermer);
    entete.appendChild(croix);
    boite.appendChild(entete);
    corps = el('div');
    boite.appendChild(corps);
    voile.appendChild(boite);
    voile.addEventListener('mousedown', function (e) { if (e.target === voile) { fermer(); } });
    return voile;
  }

  function ouvrir() {
    var demande = fetch('/api/maj', { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (d) { donnees = d; }).catch(function () {});
    if (panneau) { fermer(); }
    panneau = creerPanneau();
    document.body.appendChild(panneau);
    remplir();
    demande.then(function () {
      // Les textes arrivent avec la réponse : le panneau se redessine dans la bonne langue.
      if (panneau) {
        var titre = document.getElementById('nico579-reglages-titre');
        if (titre) { titre.textContent = textes().reglages_titre || 'Settings'; }
        remplir();
      }
    });
    document.addEventListener('keydown', surEchap);
  }

  function fermer() {
    if (panneau && panneau.parentNode) { panneau.parentNode.removeChild(panneau); }
    panneau = null; corps = null; statut = null;
    document.removeEventListener('keydown', surEchap);
  }

  function surEchap(e) { if (e.key === 'Escape') { fermer(); } }

  // ------------------------------------------------------------------- le bouton

  function placer() {
    var ancre = document.getElementById('nico579-reglages');
    bouton = el('button', { type: 'button', id: 'nico579-reglages-bouton', title: 'Settings' }, ['⚙']);
    if (ancre) {
      var classe = ancre.getAttribute('data-classe');
      if (classe) { bouton.className = classe; }
      else { bouton.setAttribute('style', styleBouton()); }
      ancre.appendChild(bouton);
    } else {
      bouton.setAttribute('style', styleBouton() +
        ';position:fixed;top:8px;right:8px;z-index:2147483000;background:' + couleurs().fond);
      document.body.appendChild(bouton);
    }
    bouton.addEventListener('click', ouvrir);
    // Le texte « Réglages… » vient du serveur, dans la langue de l'application.
    fetch('/api/maj', { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (d) {
        donnees = d;
        bouton.textContent = (d.libelles && d.libelles.reglages) || '⚙';
        bouton.title = (d.libelles && d.libelles.reglages_titre) || 'Settings';
      }).catch(function () {});
  }

  window.nico579Reglages = {
    ajouter: function (titre, element) {
      var section = { titre: titre, element: element };
      sections.push(section);
      ajouterSection(section);
    },
    ouvrir: ouvrir,
    fermer: fermer
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', placer);
  } else {
    placer();
  }
})();
