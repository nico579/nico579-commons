// Sélecteur de langue FR / EN commun aux quatre applications (nico579_commons.langue).
//
// La page réserve un ou plusieurs emplacements :
//   <span class="…" data-nico579-langue data-classe="btn-lang"></span>
// L'emplacement garde sa propre classe et son style ; les deux boutons qu'on y dessine
// prennent data-classe (la classe des boutons de la page) et portent « active » pour la
// langue courante. Le choix est lu et enregistré par /api/langue ; l'attribut lang de
// <html> suit, et chaque changement est annoncé par l'événement « nico579-langue »
// (event.detail.code) sur le document, pour que l'application applique ses textes.
// L'application peut aussi s'abonner : window.nico579Langue.surChangement(fonction),
// appelée tout de suite si la langue est déjà connue.
(function () {
  'use strict';
  var CODES = ['fr', 'en'];
  var code = null;
  var abonnes = [];

  function detecter() {
    return String(navigator.language || 'en').toLowerCase().indexOf('fr') === 0 ? 'fr' : 'en';
  }

  function emplacements() {
    return Array.prototype.slice.call(document.querySelectorAll('[data-nico579-langue]'));
  }

  function dessiner() {
    emplacements().forEach(function (zone) {
      if (!zone.querySelector('button[data-lang]')) {
        CODES.forEach(function (c) {
          var b = document.createElement('button');
          b.type = 'button';
          b.setAttribute('data-lang', c);
          b.setAttribute('data-lang-btn', c);
          var classe = zone.getAttribute('data-classe');
          if (classe) { b.className = classe; }
          b.textContent = c.toUpperCase();
          b.addEventListener('click', function () { appliquer(c, true, false); });
          zone.appendChild(b);
        });
      }
      Array.prototype.forEach.call(zone.querySelectorAll('button[data-lang]'), function (b) {
        var actif = b.getAttribute('data-lang') === code;
        b.classList.toggle('active', actif);
        b.setAttribute('aria-pressed', actif ? 'true' : 'false');
      });
    });
  }

  function enregistrer(c, detectee) {
    try {
      fetch('/api/langue', { method: 'POST', cache: 'no-store',
                             headers: { 'Content-Type': 'application/json' },
                             body: JSON.stringify({ code: c, detectee: !!detectee }) })
        .catch(function () {});
    } catch (e) { /* page sans réseau : le choix reste affiché */ }
  }

  function appliquer(c, enregistrement, detectee) {
    if (CODES.indexOf(c) < 0) { return; }
    var change = c !== code;
    code = c;
    document.documentElement.setAttribute('lang', c);
    dessiner();
    if (change) {
      abonnes.forEach(function (f) { try { f(c); } catch (e) { /* un abonné ne gêne pas les autres */ } });
      document.dispatchEvent(new CustomEvent('nico579-langue', { detail: { code: c } }));
    }
    if (enregistrement) { enregistrer(c, detectee); }
  }

  window.nico579Langue = {
    code: function () { return code; },
    changer: function (c) { appliquer(c, true, false); },
    surChangement: function (f) { abonnes.push(f); if (code) { f(code); } }
  };

  function demarrer() {
    dessiner();
    fetch('/api/langue', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .catch(function () { return {}; })
      .then(function (d) {
        if (d && CODES.indexOf(d.code) >= 0) { appliquer(d.code, false, false); }
        else { appliquer(detecter(), true, true); }
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', demarrer);
  } else {
    demarrer();
  }
})();
