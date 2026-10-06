// Bandeau de mise à jour, le même pour toutes les applications qui servent une
// page web et s'installent seules (nico579_commons.maj_install). Servi par
// nico579_commons.serveweb à l'adresse /nico579-maj.js ; une page n'a qu'à le
// charger : <script src="/nico579-maj.js"></script>.
//
// Il interroge /api/maj (version, état de l'installation, textes dans la
// langue de l'application) et propose « Installer » ; l'installation se
// conduit côté serveur, la page ne fait qu'afficher l'avancement. Quand
// l'application redémarre, la page se recharge d'elle-même.
(function () {
  'use strict';
  var CADENCE_REPOS = 30000;      // ms entre deux questions quand rien ne se passe
  var CADENCE_ACTIVE = 1000;      // ms pendant le téléchargement et le redémarrage
  var barre = null;
  var minuteur = null;
  var attendaitRedemarrage = false;
  var masquee = false;
  try { masquee = sessionStorage.getItem('nico579-maj-masquee') === '1'; } catch (e) {}

  function style(element, regles) {
    for (var cle in regles) { element.style[cle] = regles[cle]; }
  }

  function creer() {
    var div = document.createElement('div');
    div.id = 'nico579-maj';
    div.setAttribute('role', 'status');
    var sombre = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    style(div, {
      position: 'fixed', top: '0', left: '0', right: '0', zIndex: '2147483000',
      display: 'none', alignItems: 'center', justifyContent: 'center', gap: '12px',
      padding: '8px 16px', fontFamily: 'system-ui, sans-serif', fontSize: '14px',
      background: sombre ? '#1f3a5f' : '#e8f1fc', color: sombre ? '#eaf2ff' : '#12355b',
      borderBottom: '1px solid ' + (sombre ? '#3b6aa5' : '#9bbbe4'), boxSizing: 'border-box'
    });
    document.body.appendChild(div);
    return div;
  }

  function bouton(texte, action) {
    var b = document.createElement('button');
    b.type = 'button';
    b.textContent = texte;
    style(b, { font: 'inherit', cursor: 'pointer', padding: '2px 12px' });
    b.addEventListener('click', action);
    return b;
  }

  function afficher(parties) {
    if (!barre) { barre = creer(); }
    barre.textContent = '';
    parties.forEach(function (p) { barre.appendChild(p); });
    barre.style.display = 'flex';
  }

  function cacher() { if (barre) { barre.style.display = 'none'; } }

  function texte(contenu) {
    var s = document.createElement('span');
    s.textContent = contenu;
    return s;
  }

  function fermer(libelles) {
    var b = bouton('×', function () {
      masquee = true;
      try { sessionStorage.setItem('nico579-maj-masquee', '1'); } catch (e) {}
      cacher();
    });
    b.title = libelles.fermer || '';
    b.setAttribute('aria-label', libelles.fermer || 'close');
    return b;
  }

  function lien(d) {
    var a = document.createElement('a');
    a.href = d.page;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = d.libelles.voir;
    a.style.color = 'inherit';
    return a;
  }

  function rendre(d) {
    var e = d.etat, L = d.libelles;
    if (e.etat === 'redemarrage') {
      attendaitRedemarrage = true;
      afficher([texte(L.redemarrage)]);
      return CADENCE_ACTIVE;
    }
    if (e.etat === 'telechargement') {
      var pct = e.total ? ' ' + Math.floor(100 * e.recu / e.total) + ' %' : '';
      afficher([texte(L.telechargement + pct)]);
      return CADENCE_ACTIVE;
    }
    if (e.etat === 'erreur') {
      var message = (e.erreur && e.erreur.message) || '';
      afficher([texte(L.erreur + ' ' + message), bouton(L.reessayer, installer), lien(d), fermer(L)]);
      return CADENCE_REPOS;
    }
    if (d.version && !masquee) {
      var parties = [texte(L.disponible.replace('{version}', d.version))];
      parties.push(d.possible ? bouton(L.installer, installer) : lien(d));
      parties.push(fermer(L));
      afficher(parties);
    } else {
      cacher();
    }
    return CADENCE_REPOS;
  }

  function planifier(delai) {
    clearTimeout(minuteur);
    minuteur = setTimeout(interroger, delai);
  }

  function interroger() {
    fetch('/api/maj', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (attendaitRedemarrage && d.etat.etat !== 'redemarrage') {
          // Le serveur répond de nouveau : c'est la nouvelle version.
          window.location.reload();
          return;
        }
        planifier(rendre(d));
      })
      .catch(function () {
        // Serveur injoignable : normal pendant le redémarrage, on réessaie vite.
        planifier(attendaitRedemarrage ? CADENCE_ACTIVE : CADENCE_REPOS);
      });
  }

  function installer() {
    fetch('/api/maj-installer', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
    }).then(function () { planifier(300); }).catch(function () { planifier(CADENCE_ACTIVE); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', interroger);
  } else {
    interroger();
  }
})();
