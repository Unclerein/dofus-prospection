'use strict';

// ---------------------------------------------------------------- utilitaires

const $main = document.getElementById('main');
const SVG = 'http://www.w3.org/2000/svg';

/** Crée un élément. Les enfants textuels passent par textContent : aucune donnée n'est interprétée en HTML. */
function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'style') node.style.cssText = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svg(tag, attrs, ...children) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  for (const child of children.flat()) if (child) node.append(child instanceof Node ? child : document.createTextNode(child));
  return node;
}

const fmt = (n) => (n === null || n === undefined ? '—' : Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' '));
const signed = (n) => (n === null || n === undefined ? '—' : (n >= 0 ? '+' : '−') + fmt(Math.abs(n)));

function ago(ts, now) {
  if (!ts) return 'jamais';
  const s = Math.max(0, (now || Date.now() / 1000) - ts);
  if (s < 90) return "à l'instant";
  if (s < 5400) return `il y a ${Math.round(s / 60)} min`;
  if (s < 172800) return `il y a ${Math.round(s / 3600)} h`;
  return `il y a ${Math.round(s / 86400)} j`;
}

function when(ts, withTime = true) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, '0');
  const day = `${p(d.getDate())}/${p(d.getMonth() + 1)}`;
  return withTime ? `${day} ${p(d.getHours())}:${p(d.getMinutes())}` : day;
}

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || `erreur ${response.status}`);
  return response.json();
}

function tile(iconId, big) {
  const box = h('span', { class: big ? 'tile big' : 'tile' });
  if (iconId) {
    const img = h('img', { src: `/icons/${iconId}.png`, alt: '', loading: 'lazy' });
    img.addEventListener('error', () => img.remove());
    box.append(img);
  }
  return box;
}

/** Petite image d'une caractéristique (vitalité, force…), ou rien si elle n'en a pas. */
function statIcon(asset) {
  if (!asset) return null;
  const img = h('img', { class: 'stat-ico', src: `/icons/effects/${asset}.png`, alt: '', loading: 'lazy' });
  img.addEventListener('error', () => img.remove());
  return img;
}

const itemCell = (iconId, name, sub) =>
  h('div', { class: 'item-cell' }, tile(iconId), h('div', {}, h('div', { class: 'name' }, name), sub && h('div', { class: 'sub' }, sub)));

function kpi(label, value, hint, cls) {
  return h('div', { class: 'kpi' }, h('div', { class: 'label' }, label), h('div', { class: 'value ' + (cls || '') }, value), hint && h('div', { class: 'hint' }, hint));
}

function segmented(label, options, current, onPick) {
  return h('div', { class: 'seg', role: 'group', 'aria-label': label },
    options.map(([value, text]) => h('button', { 'aria-pressed': String(value === current), onclick: () => onPick(value) }, text)));
}

/** Libellé court de la source d'un prix. */
function shortSource(source) {
  if (!source) return 'inconnu';
  if (source.startsWith('HDV')) return source.includes('sans exo') ? 'HDV de base' : 'HDV';
  if (source.startsWith('dernier')) return 'dernière vente';
  if (source.startsWith('prix médian')) return 'médiane 24 h';
  return 'prix moyen';
}

/** Un prix observé (HDV, vente) est plus fiable que le prix moyen du jeu. */
const observed = (source) => !!source && source !== 'prix moyen';
const sourceLine = (source, ageHours) =>
  h('div', { class: 'source' }, h('span', { class: 'dot ' + (observed(source) ? 'on' : '') }), `${shortSource(source)} · ${ageLabel(ageHours)}`);
function ageLabel(hours) {
  if (hours === null || hours === undefined) return '';
  if (hours < 1.5) return `${Math.max(1, Math.round(hours * 60))} min`;
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} j`;
}

// ---------------------------------------------------------------- état et navigation

const S = { cache: {}, ui: { crafts: null, forge: {}, ranking: { criterion: 'exo', exo: null, effect: null, amount: 1, start: 'base', type: '' } }, status: null, stamp: null };

const NAV = [
  ['crafts', 'Crafts', 'M3 15l7-7M11 3l4 4-3 3-4-4z'],
  ['stock', 'Mon stock', 'M2.5 6.5L9 3l6.5 3.5v6L9 16l-6.5-3.5zM2.5 6.5L9 10l6.5-3.5M9 10v6'],
  ['jobs', 'Métiers', 'M3 15.5h12M5 15.5V8.5l4-5.5 4 5.5v7M7.5 15.5v-3.500h3v3.500'],
  ['forge', 'Forgemagie', 'M9 2l2 4.5 5 .6-3.7 3.3 1 4.9L9 12.8 4.7 15.3l1-4.9L2 7.1l5-.6z'],
  ['trends', 'Tendances', 'M2 13l4.5-5 3 3L16 4M12 4h4v4'],
  ['item', 'Fiche objet', 'M5 2.5h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2v-9a2 2 0 012-2zM6 6.5h6M6 9.5h6M6 12.5h3'],
  ['ignored', 'Ignorés', 'M3 3l12 12M7.4 7.5a2.2 2.2 0 003.1 3.1M5 5.3C3.4 6.4 2.3 7.9 1.8 9c1 2.3 3.7 5 7.2 5 1.1 0 2.1-.3 3-.7M8 4.1c.3 0 .7-.1 1-.1 3.5 0 6.200 2.700 7.200 5-.3.7-.8 1.500-1.500 2.300'],
  ['status', 'État', 'M9 15.5a6.5 6.5 0 100-13 6.5 6.5 0 000 13zM9 5.5V9l2.5 1.5'],
];

function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  return { page: parts[0] || 'crafts', sub: parts[1], id: parts[2] ? Number(parts[2]) : (parts[1] && /^\d+$/.test(parts[1]) ? Number(parts[1]) : null) };
}

function renderNav() {
  const current = route().page;
  const nav = document.getElementById('nav');
  nav.replaceChildren(...NAV.map(([page, label, path]) =>
    h('a', { href: `#/${page}`, 'aria-current': page === current ? 'page' : null },
      svg('svg', { width: 18, height: 18, viewBox: '0 0 18 18', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' }, svg('path', { d: path })),
      label)));
}

function renderCapture() {
  const s = S.status;
  const box = document.getElementById('capture');
  if (!s) return box.replaceChildren(h('div', { class: 'sub' }, 'Chargement…'));
  box.replaceChildren(
    h('div', { class: 'state' }, h('span', { class: 'dot ' + (s.decode_alert ? 'alert' : s.running ? 'on' : '') }), s.running ? 'Capture en cours' : 'Capture arrêtée'),
    h('div', { class: 'sub' }, `Dernier relevé ${ago(s.last_snapshot_ts, Date.now() / 1000)}`),
    s.server_name && h('div', { class: 'sub' }, `Serveur ${s.server_name}`));
}

async function cached(key, path) {
  if (!S.cache[key]) S.cache[key] = api(path).catch((error) => { delete S.cache[key]; throw error; });
  return S.cache[key];
}

let renderToken = 0;
async function render() {
  const token = ++renderToken;
  renderNav();
  const r = route();
  const pages = { crafts: pageCrafts, stock: pageStock, ignored: pageIgnored, jobs: pageJobs, forge: pageForge, trends: pageTrends, item: pageItem, status: pageStatus };
  try {
    const nodes = await (pages[r.page] || pageCrafts)(r);
    if (token !== renderToken) return; // une navigation plus récente a pris le relais
    const alert = S.status && S.status.decode_alert && r.page !== 'status'
      ? h('div', { class: 'banner', role: 'alert' }, 'Alerte de décodage : les prix ne sont plus lus correctement. ', h('a', { href: '#/status', style: 'text-decoration: underline' }, "Voir la page État."))
      : null;
    $main.replaceChildren(...[alert, ...nodes].filter(Boolean));
  } catch (error) {
    if (token === renderToken) $main.replaceChildren(h('div', { class: 'banner', role: 'alert' }, `Impossible de charger la page : ${error.message}`));
  }
}

/** Redessine la page courante sans perdre le défilement ni le champ en cours de saisie. */
function refresh() {
  const y = window.scrollY;
  const active = document.activeElement && document.activeElement.id;
  return render().then(() => {
    window.scrollTo(0, y);
    if (active) {
      const el = document.getElementById(active);
      if (el) {
        el.focus();
        // Champ de recherche redessiné pendant la frappe : remettre le curseur en fin de texte.
        if (el.type === 'search' || el.type === 'text') el.setSelectionRange(el.value.length, el.value.length);
      }
    }
  });
}

async function poll() {
  try {
    const { stamp } = await api('/api/version');
    const text = JSON.stringify(stamp);
    if (S.stamp !== null && text !== S.stamp) { S.cache = {}; S.status = await api('/api/status'); renderCapture(); refresh(); }
    S.stamp = text;
  } catch (error) { /* serveur momentanément injoignable : on réessaiera */ }
}

window.addEventListener('hashchange', () => { $tip.hidden = true; window.scrollTo(0, 0); render(); });

// ---------------------------------------------------------------- objets ignorés

const EYE_OFF = 'M3 3l12 12M7.4 7.5a2.2 2.2 0 003.1 3.1M5 5.3C3.4 6.4 2.3 7.9 1.8 9c1 2.3 3.7 5 7.2 5 1.1 0 2.1-.3 3-.7M8 4.1c.3 0 .7-.1 1-.1 3.5 0 6.2 2.7 7.2 5-.3.7-.8 1.5-1.5 2.3';
const $toast = h('div', { class: 'toast', hidden: true, role: 'status', 'aria-live': 'polite' });
document.body.append($toast);
let toastTimer = null;

async function setIgnored(itemId, name, ignored) {
  try {
    await api('/api/ignore', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ item_id: itemId, ignored }) });
  } catch (error) {
    $toast.replaceChildren(`Impossible de modifier la liste : ${error.message}`); $toast.hidden = false; return;
  }
  S.cache = {}; // les classements dépendent de la liste des objets ignorés
  await refresh();
  clearTimeout(toastTimer);
  $toast.replaceChildren(
    h('span', {}, ignored ? `${name} est ignoré.` : `${name} n'est plus ignoré.`),
    h('button', { class: 'btn', onclick: () => setIgnored(itemId, name, !ignored) }, 'Annuler'),
    ignored && h('a', { class: 'btn', href: '#/ignored', onclick: () => { $toast.hidden = true; } }, 'Voir la liste'));
  $toast.hidden = false;
  toastTimer = setTimeout(() => { $toast.hidden = true; }, 7000);
}

/** Bouton d'une ligne de classement : un clic et l'objet disparaît des classements. */
function ignoreButton(itemId, name) {
  return h('button', { class: 'icon-btn', title: `Ignorer ${name}`, 'aria-label': `Ignorer ${name}`,
    onclick: (event) => { event.stopPropagation(); $tip.hidden = true; setIgnored(itemId, name, true); } },
    svg('svg', { width: 18, height: 18, viewBox: '0 0 18 18', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' }, svg('path', { d: EYE_OFF })));
}

async function setTypeIgnored(type, ignored) {
  try {
    await api('/api/ignore-type', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type, ignored }) });
  } catch (error) {
    $toast.replaceChildren(`Impossible de modifier la liste : ${error.message}`); $toast.hidden = false; return;
  }
  S.cache = {};
  await refresh();
  clearTimeout(toastTimer);
  $toast.replaceChildren(h('span', {}, ignored ? `Tous les objets de type « ${type} » sont ignorés.` : `Le type « ${type} » n'est plus ignoré.`),
    h('button', { class: 'btn', onclick: () => setTypeIgnored(type, !ignored) }, 'Annuler'));
  $toast.hidden = false;
  toastTimer = setTimeout(() => { $toast.hidden = true; }, 7000);
}

async function pageIgnored() {
  const data = await cached('ignored', '/api/ignored');
  const head = h('header', { class: 'head' }, h('div', {}, h('h1', {}, 'Ignorés'),
    h('div', { class: 'lead' }, 'Masqués de Crafts, Tendances et Mon stock.')));

  // Ignorer tout un type : on tape quelques lettres, on clique le type.
  const ui = S.ui.ignored || (S.ui.ignored = { q: '' });
  const hiddenTypes = data.types.filter((t) => t.ignored);
  const query = norm(ui.q.trim());
  const matches = query.length < 2 ? [] : data.types.filter((t) => !t.ignored && norm(t.name).includes(query)).slice(0, 30);
  let typing = null;
  const typePanel = h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Ignorer tout un type d\'objets'), h('span', { class: 'muted small' }, `${hiddenTypes.length} type${hiddenTypes.length > 1 ? 's' : ''} ignoré${hiddenTypes.length > 1 ? 's' : ''}`)),
    h('div', { style: 'padding: 16px 18px; display: flex; flex-direction: column; gap: 14px' },
      h('div', { class: 'field', style: 'max-width: 380px' }, h('label', { for: 'ig-q' }, 'Type à ignorer'),
        h('input', { id: 'ig-q', type: 'search', value: ui.q, placeholder: 'Aile, Rune de forgemagie, Bouclier…', autocomplete: 'off',
          oninput: (e) => { clearTimeout(typing); const value = e.target.value; typing = setTimeout(() => { ui.q = value; refresh(); }, 200); } })),
      query.length >= 2 && h('div', { class: 'tags' }, matches.length
        ? matches.map((t) => h('button', { class: 'chip', title: `Ignorer les ${t.count} objets de ce type`, onclick: () => { ui.q = ''; setTypeIgnored(t.name, true); } }, t.name, h('span', { class: 'muted' }, ` · ${t.count} objets · ${t.category}`)))
        : h('span', { class: 'muted' }, 'Aucun type ne correspond.')),
      hiddenTypes.length
        ? h('div', { class: 'tags' }, hiddenTypes.map((t) => h('span', { class: 'chip on' }, t.name, h('span', { class: 'muted' }, ` · ${t.count} objets`),
          h('button', { class: 'chip-x', 'aria-label': `Ne plus ignorer le type ${t.name}`, title: 'Rétablir', onclick: () => setTypeIgnored(t.name, false) }, '×'))))
        : h('div', { class: 'muted small' }, 'Aucun type ignoré pour l\'instant.')));

  if (!data.rows.length) {
    return [head, typePanel, h('div', { class: 'panel empty' }, "Aucun objet ignoré.")];
  }
  return [head, typePanel, h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, `${data.rows.length} objet${data.rows.length > 1 ? 's' : ''} ignoré${data.rows.length > 1 ? 's' : ''}`)),
    h('div', { class: 'scroll' }, h('table', { style: 'min-width: 520px' },
      h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Objet'), h('th', {}, 'Ignoré'), h('th', {}, ''))),
      h('tbody', {}, data.rows.map((row) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${row.item_id}`; } },
        h('td', { class: 'l' }, itemCell(row.icon, row.name, [row.type, row.level ? `niv. ${row.level}` : null].filter(Boolean).join(' · '))),
        h('td', { class: 'muted' }, ago(row.added_at, data.now)),
        h('td', {}, h('button', { class: 'btn', onclick: (event) => { event.stopPropagation(); setIgnored(row.item_id, row.name, false); } }, 'Rétablir'))))))))];
}

// ---------------------------------------------------------------- page Crafts

async function pageCrafts() {
  const data = await cached('crafts', '/api/crafts');
  const status = S.status || {};
  const ui = S.ui.crafts || (S.ui.crafts = { doable: false, q: '', job: '', min: 1, max: 200, capital: '', sold: status.min_liquidity || 0, own: false, incomplete: false, sort: 'margin', limit: 100 });
  const set = (patch) => { Object.assign(ui, patch); refresh(); };

  const query = norm((ui.q || '').trim());
  let rows = data.rows.filter((r) =>
    (!query || norm(r['Objet']).includes(query)) &&
    r['Niveau'] >= ui.min && r['Niveau'] <= ui.max &&
    (!ui.job || r['Métier'] === ui.job) &&
    (!ui.own || r['Mon métier'] === true) &&
    (!ui.doable || r.craftable > 0) &&
    (ui.incomplete || r['Marge'] !== null) &&
    (!ui.capital || r['Coût'] === null || r['Coût'] <= Number(ui.capital)) &&
    (!(ui.sold > 0) || r['Vendus 7 j'] === null || r['Vendus 7 j'] >= ui.sold));
  const key = ui.sort === 'margin' ? 'Marge pondérée' : 'Marge %';
  rows = rows.slice().sort((a, b) => (b[key] ?? -Infinity) - (a[key] ?? -Infinity));
  const computable = data.rows.filter((r) => r['Marge'] !== null).length;

  const number = (id, label, value, onchange, extra) => h('div', { class: 'field', style: extra || 'flex: 0 1 150px' },
    h('label', { for: id }, label), h('input', { id, type: 'number', value, onchange: (e) => onchange(e.target.value) }));

  let typing = null;
  const filters = h('section', { class: 'panel pad filters', 'aria-label': 'Filtres' },
    h('div', { class: 'field', style: 'flex: 1 1 220px' }, h('label', { for: 'f-q' }, 'Objet'),
      h('input', { id: 'f-q', type: 'search', value: ui.q, placeholder: 'Chercher une recette…', autocomplete: 'off',
        oninput: (e) => { clearTimeout(typing); const value = e.target.value; typing = setTimeout(() => set({ q: value, limit: 100 }), 220); } })),
    h('div', { class: 'field', style: 'flex: 0 1 190px' }, h('label', { for: 'f-job' }, 'Métier'),
      h('select', { id: 'f-job', onchange: (e) => set({ job: e.target.value, limit: 100 }) },
        h('option', { value: '' }, 'Tous les métiers'), data.jobs.map((j) => h('option', { value: j, selected: j === ui.job }, j)))),
    number('f-min', 'Niveau min.', ui.min, (v) => set({ min: Number(v) || 1 }), 'flex: 0 1 110px'),
    number('f-max', 'Niveau max.', ui.max, (v) => set({ max: Number(v) || 200 }), 'flex: 0 1 110px'),
    h('div', { class: 'field', style: 'flex: 0 1 170px' }, h('label', { for: 'f-cap' }, 'Capital maximum'),
      h('input', { id: 'f-cap', type: 'number', value: ui.capital, placeholder: 'Sans limite', onchange: (e) => set({ capital: e.target.value }) })),
    number('f-sold', 'Vendus sur 7 j, min.', ui.sold, (v) => set({ sold: Number(v) || 0 }), 'flex: 0 1 160px'),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: ui.own, disabled: !status.has_jobs, onchange: (e) => set({ own: e.target.checked }) }), 'Mes métiers seulement'),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: ui.incomplete, onchange: (e) => set({ incomplete: e.target.checked }) }), 'Recettes incalculables'),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: ui.doable, disabled: !data.stock_known, onchange: (e) => set({ doable: e.target.checked }) }), 'Faisables avec mon stock'));

  const body = rows.slice(0, ui.limit).map((r) => {
    const tags = (r['Remarques'] || '').split(', ').filter(Boolean);
    return h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${r.item_id}`; } },
      h('td', { class: 'l' }, itemCell(r.icon, r['Objet'])),
      h('td', { class: 'l soft' }, r['Métier'], h('span', { class: 'muted' }, ` · ${r['Niveau']}`)),
      h('td', {}, h('div', { style: 'font-weight: 500' }, fmt(r['Prix de vente'])), r['Prix de vente'] !== null && sourceLine(r['Source du prix'], r['Âge du prix (h)'])),
      h('td', { class: 'soft' }, fmt(r['Coût'])),
      h('td', { class: 'strong ' + (r['Marge'] === null ? 'muted' : r['Marge'] >= 0 ? 'gain' : 'warn') }, signed(r['Marge'])),
      h('td', { class: 'soft' }, r['Marge %'] === null ? '—' : `${fmt(r['Marge %'])} %`),
      h('td', { class: r['Vendus 7 j'] === null ? 'muted' : 'soft' }, fmt(r['Vendus 7 j'])),
      h('td', {}, r.craftable > 0 ? h('span', { class: 'tag good' }, `× ${fmt(r.craftable)}`) : h('span', { class: 'muted' }, '—')),
      h('td', { class: 'l wrap' }, h('div', { class: 'tags' }, tags.map((t) => h('span', { class: 'tag ' + (t.includes('sous-craft') ? 'info' : t.includes('manquant') || t.includes('non échangeable') ? 'bad' : '') }, t)))),
      h('td', { class: 'act' }, ignoreButton(r.item_id, r['Objet'])));
  });

  return [
    h('header', { class: 'head' },
      h('div', {}, h('h1', {}, 'Crafts'), h('div', { class: 'lead' }, `${fmt(computable)} recettes · taxe ${Math.round((status.hdv_tax || 0) * 100)} %`)),
      segmented('Classer par', [['margin', 'Marge'], ['pct', 'Marge %']], ui.sort, (sort) => set({ sort }))),
    filters,
    h('section', { class: 'panel', 'aria-label': 'Classement' },
      rows.length === 0
        ? h('div', { class: 'empty' }, data.rows.length ? 'Aucune recette ne correspond à ces filtres.' : "Aucune recette : importe les données statiques, puis lance une capture.")
        : h('div', { class: 'scroll' }, h('table', { style: 'min-width: 940px' },
          h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Objet'), h('th', { class: 'l' }, 'Métier'), h('th', {}, 'Prix de vente'), h('th', {}, 'Coût'),
            h('th', { class: ui.sort === 'margin' ? 'sorted' : '' }, 'Marge' + (ui.sort === 'margin' ? ' ↓' : '')),
            h('th', { class: ui.sort === 'pct' ? 'sorted' : '' }, 'Marge %' + (ui.sort === 'pct' ? ' ↓' : '')), h('th', {}, 'Vendus 7 j'), h('th', { title: 'Nombre faisable avec ton inventaire et ta banque' }, 'En stock'), h('th', { class: 'l' }, 'À savoir'), h('th', {}, ''))),
          h('tbody', {}, body))),
      h('div', { class: 'panel-foot' },
        h('span', { class: 'legend' }, h('span', {}, h('span', { class: 'dot on' }), 'prix observé'), h('span', {}, h('span', { class: 'dot' }), 'prix moyen')),
        h('span', {}, `${fmt(Math.min(ui.limit, rows.length))} sur ${fmt(rows.length)} `, rows.length > ui.limit && h('button', { class: 'btn', onclick: () => set({ limit: ui.limit + 200 }) }, 'Afficher plus')))),
  ];
}

// ---------------------------------------------------------------- page Mon stock

/** « possédé / nécessaire », en vert si le compte y est, en orange sinon. */
function haveTag(have, need) {
  const cls = have >= need ? 'good' : have > 0 ? 'bad' : '';
  return h('span', { class: 'tag ' + cls, title: have >= need ? 'En stock' : `Il en manque ${fmt(need - have)}` }, `${fmt(have)} / ${fmt(need)}`);
}

function stockFreshness(meta, bankInferred, now) {
  const part = (key, label) => (meta[key] ? `${label} ${ago(meta[key].captured_at, now)}` : `${label} : jamais lu`);
  return `${part('inventory', 'Inventaire')} · ${bankInferred ? 'banque déduite' : part('bank', 'banque')}`;
}

async function pageStock(r) {
  const items = r.sub === 'items';
  const tabs = h('div', { class: 'seg', role: 'tablist', 'aria-label': 'Vue' },
    h('a', { href: '#/stock', role: 'tab', 'aria-selected': String(!items) }, 'Crafts faisables'),
    h('a', { href: '#/stock/items', role: 'tab', 'aria-selected': String(items) }, 'Inventaire et banque'));
  const data = items ? await cached('stock', '/api/stock') : await cached('stockCrafts', '/api/stock/crafts');
  const head = h('header', { class: 'head' },
    h('div', {}, h('h1', {}, 'Mon stock'), h('div', { class: 'lead' }, Object.keys(data.meta).length ? stockFreshness(data.meta, data.bank_inferred, data.now) : 'Aucun inventaire lu pour l\'instant.')),
    tabs);
  if (!Object.keys(data.meta).length) {
    return [head, h('div', { class: 'panel empty' }, "Ton inventaire est lu à chaque connexion pendant une capture, ta banque quand tu l'ouvres en jeu. Lance le jeu par le raccourci, ouvre ta banque une fois, et cette page se remplira.")];
  }
  return [head, ...(items ? stockItems(data) : stockCrafts(data))];
}

/** Infobulle d'un ingrédient : prix moyen du jeu, prix à l'HDV, et ce qu'il en faut. */
function ingredientTooltip(i, data) {
  const [avg, hdv] = data.prices[i.id] || [null, null];
  const unit = (v) => (v === null ? '—' : v < 100 && !Number.isInteger(v) ? v.toFixed(2).replace('.', ',') : fmt(v));
  const pct = avg && hdv !== null ? (hdv - avg) / avg * 100 : null;
  const missing = Math.max(0, i.need - i.have);
  const price = hdv !== null ? hdv : avg;
  return [
    h('div', { class: 'tip-head' }, tile(i.icon), h('div', {}, h('div', { class: 'tip-name' }, i.name), h('div', { class: 'muted small' }, `${fmt(i.have)} / ${fmt(i.need)}`))),
    h('div', { class: 'tip-lines' },
      h('div', { class: 'tip-price' }, h('span', { class: 'muted' }, 'Prix moyen'), h('b', {}, unit(avg))),
      h('div', { class: 'tip-price' }, h('span', { class: 'muted' }, 'Prix HDV'), h('b', {}, unit(hdv)),
        pct !== null && Math.abs(pct) >= 0.5 && h('span', { class: 'small ' + (pct < 0 ? 'gain' : 'warn') }, `${pct < 0 ? '−' : '+'}${fmt(Math.abs(pct))} %`))),
    missing > 0 && h('div', { class: 'tip-foot' }, h('span', { class: 'muted' }, `Manque ${fmt(missing)}`), h('b', {}, price === null ? '—' : `≈ ${fmt(missing * price)}`)),
  ];
}

function stockCrafts(data) {
  const ui = S.ui.stockCrafts || (S.ui.stockCrafts = { view: 'ready', q: '', job: '', own: false, limit: 100 });
  const set = (patch) => { Object.assign(ui, patch); refresh(); };
  const query = norm(ui.q.trim());
  const base = data.rows.filter((row) =>
    (!query || norm(row.name).includes(query) || row.ingredients.some((i) => norm(i.name).includes(query))) &&
    (!ui.job || row.job === ui.job) && (!ui.own || row.own_job === true));
  const ready = base.filter((row) => row.craftable > 0);
  const close = base.filter((row) => row.craftable === 0 && row.lines - row.covered === 1);
  let rows = ui.view === 'ready' ? ready : ui.view === 'close' ? close : base;
  rows = rows.slice().sort(ui.view === 'ready'
    ? (a, b) => (b.total_margin ?? -Infinity) - (a.total_margin ?? -Infinity)
    : (a, b) => (b.margin ?? -Infinity) - (a.margin ?? -Infinity));
  const gain = ready.reduce((sum, row) => sum + Math.max(0, row.total_margin || 0), 0);

  let typing = null;
  const filters = h('section', { class: 'panel pad filters', 'aria-label': 'Filtres' },
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Recettes'),
      segmented('Recettes', [['ready', `Faisables maintenant · ${ready.length}`], ['close', `Il manque un ingrédient · ${close.length}`], ['all', `Toutes · ${base.length}`]], ui.view, (view) => set({ view, limit: 100 }))),
    h('div', { class: 'field', style: 'flex: 1 1 220px' }, h('label', { for: 's-q' }, 'Objet ou ingrédient'),
      h('input', { id: 's-q', type: 'search', value: ui.q, placeholder: 'Chercher…', autocomplete: 'off',
        oninput: (e) => { clearTimeout(typing); const value = e.target.value; typing = setTimeout(() => set({ q: value, limit: 100 }), 220); } })),
    h('div', { class: 'field', style: 'flex: 0 1 190px' }, h('label', { for: 's-job' }, 'Métier'),
      h('select', { id: 's-job', onchange: (e) => set({ job: e.target.value, limit: 100 }) },
        h('option', { value: '' }, 'Tous les métiers'), data.jobs.map((j) => h('option', { value: j, selected: j === ui.job }, j)))),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: ui.own, disabled: !data.has_jobs, onchange: (e) => set({ own: e.target.checked }) }), 'Mes métiers seulement'));

  const body = rows.slice(0, ui.limit).map((row) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${row.item_id}`; } },
    h('td', { class: 'l' }, itemCell(row.icon, row.name, `${row.job} · ${row.level}`)),
    h('td', {}, row.sell === null ? h('span', { class: 'muted' }, '—')
      : [h('div', { style: 'font-weight: 500' }, fmt(row.sell)), h('div', { class: 'source' }, h('span', { class: 'dot ' + (observed(row.source) ? 'on' : '') }), shortSource(row.source))]),
    h('td', {}, row.craftable > 0 ? h('span', { class: 'tag good', style: 'font-size: 14px; font-weight: 700' }, `× ${fmt(row.craftable)}`) : h('span', { class: 'muted' }, `${row.covered} / ${row.lines} ingr.`)),
    h('td', { class: 'l wrap' }, h('div', { class: 'ingredients' }, row.ingredients.map((i) =>
      h('a', { class: 'ingredient ' + (i.have >= i.need ? 'ok' : i.have > 0 ? 'part' : 'none'), href: `#/item/${i.id}`,
        'aria-label': `${i.name}, ${fmt(i.have)} en stock pour ${fmt(i.need)} par craft, ouvrir la fiche`,
        onclick: (event) => event.stopPropagation(), // ne pas ouvrir la fiche de la recette
        onmouseenter: (event) => showTip(event, ingredientTooltip(i, data)), onmousemove: placeTip, onmouseleave: () => { $tip.hidden = true; } },
        tile(i.icon), h('span', { class: 'ingredient-name' }, i.name), h('span', { class: 'ingredient-qty' }, `${fmt(i.have)} / ${fmt(i.need)}`))))),
    h('td', { class: 'soft' }, row.craftable > 0 ? '—' : row.missing_cost === null ? h('span', { class: 'warn' }, 'prix manquant') : fmt(row.missing_cost)),
    h('td', { class: row.margin === null ? 'muted' : row.margin >= 0 ? 'soft' : 'warn' }, signed(row.margin)),
    h('td', { class: 'strong ' + (row.total_margin === null || !row.craftable ? 'muted' : row.total_margin >= 0 ? 'gain' : 'warn') }, row.craftable ? signed(row.total_margin) : '—'),
    h('td', { class: 'act' }, ignoreButton(row.item_id, row.name))));

  return [
    h('section', { class: 'kpis' },
      kpi('Faisables maintenant', fmt(ready.length)),
      kpi('Gain possible', signed(gain), 'recettes comptées séparément', 'gain'),
      kpi('Il manque un ingrédient', fmt(close.length))),
    filters,
    h('section', { class: 'panel', 'aria-label': 'Recettes' },
      rows.length === 0 ? h('div', { class: 'empty' }, 'Aucune recette ne correspond.')
        : h('div', { class: 'scroll' }, h('table', { style: 'min-width: 980px' },
          h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Objet'), h('th', {}, 'Prix'), h('th', {}, 'Faisables'), h('th', { class: 'l' }, 'Ingrédients'), h('th', {}, 'Manquant'), h('th', {}, 'Marge'), h('th', { class: ui.view === 'ready' ? 'sorted' : '' }, 'Gain total' + (ui.view === 'ready' ? ' ↓' : '')), h('th', {}, ''))),
          h('tbody', {}, body))),
      h('div', { class: 'panel-foot' },
        h('span', { class: 'legend' }, h('span', {}, h('span', { class: 'swatch', style: 'background: var(--gain)' }), 'en stock'), h('span', {}, h('span', { class: 'swatch', style: 'background: var(--warn)' }), 'pas assez'), h('span', {}, h('span', { class: 'swatch', style: 'background: var(--line-strong)' }), 'aucun')),
        h('span', {}, `${fmt(Math.min(ui.limit, rows.length))} sur ${fmt(rows.length)} `, rows.length > ui.limit && h('button', { class: 'btn', onclick: () => set({ limit: ui.limit + 200 }) }, 'Afficher plus')))),
  ];
}

function stockItems(data) {
  const ui = S.ui.stockItems || (S.ui.stockItems = { q: '', category: '', where: '', usedOnly: false, sort: 'value', limit: 150 });
  const set = (patch) => { Object.assign(ui, patch); refresh(); };
  const query = norm(ui.q.trim());
  const counts = new Map();
  for (const row of data.rows) counts.set(row.category, (counts.get(row.category) || 0) + 1);
  let rows = data.rows.filter((row) =>
    (!query || norm(row.name).includes(query)) && (!ui.category || row.category === ui.category) &&
    (!ui.where || row[ui.where] > 0) && (!ui.usedOnly || row.recipes > 0));
  rows = rows.slice().sort(ui.sort === 'value' ? (a, b) => (b.value ?? -1) - (a.value ?? -1) : ui.sort === 'total' ? (a, b) => b.total - a.total : (a, b) => a.name.localeCompare(b.name, 'fr'));
  const sum = (list, key) => list.reduce((total, row) => total + (row[key] || 0), 0);
  const kamas = (key) => (data.meta[key] ? data.meta[key].kamas : null);

  let typing = null;
  const filters = h('section', { class: 'panel pad filters', 'aria-label': 'Filtres' },
    h('div', { class: 'field', style: 'flex: 1 1 220px' }, h('label', { for: 'i-q' }, 'Objet'),
      h('input', { id: 'i-q', type: 'search', value: ui.q, placeholder: 'Chercher…', autocomplete: 'off',
        oninput: (e) => { clearTimeout(typing); const value = e.target.value; typing = setTimeout(() => set({ q: value, limit: 150 }), 220); } })),
    h('div', { class: 'field', style: 'flex: 0 1 220px' }, h('label', { for: 'i-cat' }, 'Famille'),
      h('select', { id: 'i-cat', class: ui.category ? 'set' : '', onchange: (e) => set({ category: e.target.value, limit: 150 }) },
        h('option', { value: '' }, 'Toutes'), [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([name, n]) => h('option', { value: name, selected: name === ui.category }, `${name} · ${n}`)))),
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Où'), segmented('Où', [['', 'Partout'], ['inventory', 'Inventaire'], ['bank', 'Banque']], ui.where, (where) => set({ where }))),
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Trier par'), segmented('Trier par', [['value', 'Valeur'], ['total', 'Quantité'], ['name', 'Nom']], ui.sort, (sort) => set({ sort }))),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: ui.usedOnly, onchange: (e) => set({ usedOnly: e.target.checked }) }), 'Sert dans une recette'));

  return [
    h('section', { class: 'kpis' },
      kpi('Valeur du stock', fmt(sum(data.rows, 'value')), `${fmt(data.rows.length)} objets`),
      kpi('Kamas sur le personnage', fmt(kamas('inventory'))),
      kpi('Kamas en banque', fmt(kamas('bank')), data.meta.bank ? ago(data.meta.bank.captured_at, data.now) : 'jamais lue'),
      kpi('Sélection', fmt(sum(rows, 'value')), `${fmt(rows.length)} objets`)),
    filters,
    h('section', { class: 'panel', 'aria-label': 'Objets possédés' },
      rows.length === 0 ? h('div', { class: 'empty' }, 'Aucun objet ne correspond.')
        : h('div', { class: 'scroll' }, h('table', { style: 'min-width: 860px' },
          h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Objet'), h('th', {}, 'Inventaire'), h('th', {}, 'Banque'), h('th', { class: ui.sort === 'total' ? 'sorted' : '' }, 'Total'), h('th', {}, 'Prix unitaire'), h('th', { class: ui.sort === 'value' ? 'sorted' : '' }, 'Valeur'), h('th', {}, 'Recettes'))),
          h('tbody', {}, rows.slice(0, ui.limit).map((row) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${row.item_id}`; } },
            h('td', { class: 'l' }, itemCell(row.icon, row.name, [row.type, row.level ? `niv. ${row.level}` : null].filter(Boolean).join(' · '))),
            h('td', { class: row.inventory ? 'soft' : 'muted' }, row.inventory ? fmt(row.inventory) : '—'),
            h('td', { class: row.bank ? 'soft' : 'muted' }, row.bank ? fmt(row.bank) : '—'),
            h('td', { style: 'font-weight: 600' }, fmt(row.total)),
            h('td', {}, row.price === null ? h('span', { class: 'muted' }, '—') : [h('div', { class: 'soft' }, row.price < 100 && !Number.isInteger(row.price) ? row.price.toFixed(2).replace('.', ',') : fmt(row.price)), h('div', { class: 'source' }, h('span', { class: 'dot ' + (observed(row.source) ? 'on' : '') }), row.source)]),
            h('td', { class: 'strong ' + (row.value === null ? 'muted' : '') }, fmt(row.value)),
            h('td', { class: row.recipes ? 'soft' : 'muted' }, row.recipes || '—')))))),
      h('div', { class: 'panel-foot' }, h('span', {}, 'Objets portés exclus.'),
        h('span', {}, `${fmt(Math.min(ui.limit, rows.length))} sur ${fmt(rows.length)} `, rows.length > ui.limit && h('button', { class: 'btn', onclick: () => set({ limit: ui.limit + 300 }) }, 'Afficher plus')))),
  ];
}

// ---------------------------------------------------------------- page Métiers

const levelToXp = (level) => level * (level - 1) * 10;
const xpToLevel = (xp) => Math.max(1, Math.min(200, Math.floor((Math.sqrt(1 + 0.4 * xp) + 1) / 2)));

function loadJobState() {
  try { return JSON.parse(localStorage.getItem('dofustool.jobs') || '{}'); } catch (error) { return {}; }
}

async function pageJobs() {
  const jobs = (await cached('jobs', '/api/jobs')).jobs;
  const head = h('header', { class: 'head' }, h('div', {}, h('h1', {}, 'Métiers'), h('div', { class: 'lead' }, 'Chemin le moins coûteux vers un niveau')));
  if (!jobs.length) return [head, h('div', { class: 'panel empty' }, 'Aucune recette connue.')];

  const saved = S.ui.jobs || (S.ui.jobs = Object.assign({ job: jobs[0].id, bonus: 100, resale: false, perJob: {} }, loadJobState()));
  if (!jobs.some((j) => j.id === saved.job)) saved.job = jobs[0].id;
  const job = jobs.find((j) => j.id === saved.job);
  const mine = saved.perJob[job.id] || (saved.perJob[job.id] = { xp: job.level ? levelToXp(job.level) : 0, target: Math.min(200, (job.level || 1) + 20), exclude: [] });
  const persist = () => { try { localStorage.setItem('dofustool.jobs', JSON.stringify(saved)); } catch (error) { /* stockage indisponible */ } };
  const change = (mutate) => { mutate(); persist(); refresh(); };

  const level = xpToLevel(mine.xp);
  const query = `job=${job.id}&xp=${mine.xp}&target=${mine.target}&bonus=${saved.bonus}&resale=${saved.resale ? 1 : 0}&exclude=${mine.exclude.join(',')}`;
  const plan = await cached(`plan-${query}`, `/api/jobs/plan?${query}`);

  const controls = h('section', { class: 'panel pad filters', 'aria-label': 'Réglages' },
    h('div', { class: 'field', style: 'flex: 0 1 200px' }, h('label', { for: 'j-job' }, 'Métier'),
      h('select', { id: 'j-job', onchange: (e) => change(() => { saved.job = Number(e.target.value); }) },
        jobs.map((j) => h('option', { value: j.id, selected: j.id === job.id }, j.name)))),
    h('div', { class: 'field', style: 'flex: 0 1 170px' }, h('label', { for: 'j-xp' }, 'XP actuelle'),
      h('input', { id: 'j-xp', type: 'number', min: 0, value: mine.xp, onchange: (e) => change(() => { mine.xp = Math.max(0, Math.floor(Number(e.target.value) || 0)); }) })),
    h('div', { class: 'field', style: 'flex: 0 1 120px' }, h('label', { for: 'j-level' }, 'ou niveau actuel'),
      h('input', { id: 'j-level', type: 'number', min: 1, max: 200, value: level, onchange: (e) => change(() => { mine.xp = levelToXp(Math.max(1, Math.min(200, Math.floor(Number(e.target.value) || 1)))); }) })),
    h('div', { class: 'field', style: 'flex: 0 1 130px' }, h('label', { for: 'j-target' }, 'Niveau souhaité'),
      h('input', { id: 'j-target', type: 'number', min: 1, max: 200, value: mine.target, class: 'set', onchange: (e) => change(() => { mine.target = Math.max(1, Math.min(200, Math.floor(Number(e.target.value) || 1))); }) })),
    h('div', { class: 'field', style: 'flex: 0 1 120px' }, h('label', { for: 'j-bonus' }, 'Bonus XP %'),
      h('input', { id: 'j-bonus', type: 'number', min: 0, value: saved.bonus, onchange: (e) => change(() => { saved.bonus = Math.max(0, Number(e.target.value) || 100); }) })),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: saved.resale, onchange: (e) => change(() => { saved.resale = e.target.checked; }) }), 'Déduire la revente'));

  const excluded = plan.excluded.length ? h('div', { class: 'tags', style: 'align-items: center' }, h('span', { class: 'muted small' }, 'Retirés :'),
    plan.excluded.map((x) => h('span', { class: 'chip on' }, x.name,
      h('button', { class: 'chip-x', 'aria-label': `Remettre ${x.name}`, title: 'Remettre', onclick: () => change(() => { mine.exclude = mine.exclude.filter((i) => i !== x.item_id); }) }, '×'))),
    h('button', { class: 'btn quiet', onclick: () => change(() => { mine.exclude = []; }) }, 'Tout remettre')) : null;

  if (mine.target <= level) {
    return [head, controls, excluded, h('div', { class: 'panel empty' }, `Tu es déjà niveau ${level}.`)];
  }

  const gained = plan.end_xp - plan.start_xp;
  const kpis = h('section', { class: 'kpis' },
    kpi(saved.resale ? 'Coût net' : 'Coût des ingrédients', plan.cost < 0 ? signed(-plan.cost) : fmt(plan.cost), plan.cost < 0 ? 'bénéfice' : null, plan.cost < 0 ? 'gain' : ''),
    kpi('Niveaux', `${plan.start_level} → ${plan.end_level}`, `${fmt(gained)} XP`),
    kpi('Crafts', fmt(plan.crafts), `${plan.steps.length} étape${plan.steps.length > 1 ? 's' : ''}`),
    kpi('Kamas par XP', gained > 0 ? (plan.cost / gained).toFixed(1).replace('.', ',') : '—'));

  const blocked = plan.blocked_at !== null
    ? h('div', { class: 'banner' }, `Bloqué au niveau ${plan.blocked_at} : aucune recette chiffrable ne rapporte d'XP. ${plan.unpriced} recette${plan.unpriced > 1 ? 's' : ''} sans prix.`)
    : null;

  const steps = h('section', { class: 'panel', 'aria-label': 'Étapes' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Étapes'), h('span', { class: 'muted small' }, `${plan.candidates} recettes chiffrées`)),
    plan.steps.length === 0 ? h('div', { class: 'empty' }, 'Aucune étape.')
      : h('div', { class: 'scroll' }, h('table', { style: 'min-width: 900px' },
        h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Niveaux'), h('th', { class: 'l' }, 'Objet'), h('th', {}, 'Crafts'), h('th', {}, 'XP'), h('th', {}, saved.resale ? 'Coût net / craft' : 'Coût / craft'), h('th', {}, 'Coût'), h('th', {}, 'Kamas / XP'), h('th', {}, ''))),
        h('tbody', {}, plan.steps.map((step) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${step.item_id}`; } },
          h('td', { class: 'l', style: 'font-weight: 600' }, `${step.from_level} → ${step.to_level}`),
          h('td', { class: 'l' }, itemCell(step.icon, step.name, `niv. ${step.level} · ${step.ingredients} ingr.` + (step.ratio_pct !== 100 ? ` · XP ×${step.ratio_pct / 100}` : ''))),
          h('td', { style: 'font-weight: 600' }, `× ${fmt(step.crafts)}`),
          h('td', { class: 'soft' }, fmt(step.xp)),
          h('td', { class: 'soft' }, fmt(step.unit_cost)),
          h('td', { class: 'strong ' + (step.cost < 0 ? 'gain' : '') }, step.cost < 0 ? signed(-step.cost) : fmt(step.cost)),
          h('td', { class: 'soft' }, (step.cost / step.xp).toFixed(1).replace('.', ',')),
          h('td', { class: 'act', style: 'width: 96px' }, h('button', { class: 'btn', onclick: (event) => { event.stopPropagation(); change(() => { mine.exclude = [...new Set([...mine.exclude, step.item_id])]; }); } }, 'Retirer'))))))));

  const total = plan.shopping.reduce((sum, row) => sum + (row.cost || 0), 0);
  const shopping = h('section', { class: 'panel', 'aria-label': 'Liste de courses' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Liste de courses'), h('span', { class: 'muted small' }, `${plan.shopping.length} ingrédients · ${fmt(total)} à acheter`)),
    plan.shopping.length === 0 ? h('div', { class: 'empty' }, 'Rien à acheter.')
      : h('div', { class: 'scroll' }, h('table', { style: 'min-width: 760px' },
        h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Ingrédient'), h('th', {}, 'Besoin'), h('th', {}, 'En stock'), h('th', {}, 'À acheter'), h('th', {}, 'Prix'), h('th', {}, 'Coût'))),
        h('tbody', {}, plan.shopping.map((row) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${row.item_id}`; } },
          h('td', { class: 'l' }, itemCell(row.icon, row.name)),
          h('td', { class: 'soft' }, fmt(row.quantity)),
          h('td', {}, !plan.stock_known ? h('span', { class: 'muted' }, '—') : haveTag(row.have, row.quantity)),
          h('td', { style: 'font-weight: 600' }, row.to_buy ? fmt(row.to_buy) : h('span', { class: 'gain' }, '0')),
          h('td', {}, row.price === null ? h('span', { class: 'warn' }, '—') : [h('div', { class: 'soft' }, row.price < 100 && !Number.isInteger(row.price) ? row.price.toFixed(2).replace('.', ',') : fmt(row.price)), h('div', { class: 'source' }, h('span', { class: 'dot ' + (observed(row.source) ? 'on' : '') }), shortSource(row.source))]),
          h('td', { class: 'strong' }, fmt(row.cost))))))));

  return [head, controls, excluded, blocked, kpis, steps, shopping];
}

// ---------------------------------------------------------------- page Forgemagie

async function pageForge(r) {
  const options = (await cached('forgeOptions', '/api/forge/options')).items;
  const ranking = r.sub === 'ranking';
  const tabs = h('div', { class: 'seg', role: 'tablist', 'aria-label': 'Vue' },
    h('a', { href: '#/forge', role: 'tab', 'aria-selected': String(!ranking) }, 'Par objet'),
    h('a', { href: '#/forge/ranking', role: 'tab', 'aria-selected': String(ranking) }, 'Classement général'));
  const head = h('header', { class: 'head' },
    h('div', {}, h('h1', {}, 'Forgemagie')),
    tabs);
  if (!options.length) {
    return [head, h('div', { class: 'panel empty' }, "Aucune annonce d'équipement connue. Ouvre la fiche d'achat d'un équipement à l'HDV pendant une capture.")];
  }
  if (ranking) return [head, ...(await forgeRanking())];
  const id = r.sub === 'item' && r.id ? r.id : (S.ui.forgeItem && options.some((o) => o.id === S.ui.forgeItem) ? S.ui.forgeItem : options[0].id);
  S.ui.forgeItem = id;
  return [head, ...(await forgeItem(id, options))];
}

function matches(listing, f) {
  for (const [effect, minimum] of Object.entries(f.minimums)) if ((listing.values[effect] || 0) < minimum) return false;
  if (f.transcended === true && !listing.transcended) return false;
  if (f.transcended === false && listing.transcended) return false;
  if (f.exo === 0) return listing.exo.length === 0;
  if (f.exo) return listing.exo.includes(f.exo) && (listing.values[f.exo] || 0) >= f.exo_min;
  return true;
}

function typeTag(listing) {
  const cls = listing.missing.length ? 'bad' : listing.exo.length ? 'exo' : listing.over.length ? 'over' : listing.label === 'jets parfaits' ? 'good' : '';
  const tag = h('span', { class: 'tag ' + cls }, listing.label);
  return listing.transcended ? h('span', { class: 'tags', style: 'flex-wrap: nowrap' }, tag, h('span', { class: 'tag trans', title: 'Porte « Empêche les futures forgemagies », la marque laissée par une rune de transcendance' }, 'transcendé')) : tag;
}

let saveTimer = null;
function saveFilter(id, f) {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    api(`/api/forge/item/${id}/filter`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(f) }).catch(() => {});
  }, 500);
}

async function forgeItem(id, options) {
  const d = await cached(`forge-${id}`, `/api/forge/item/${id}`);
  const picker = itemPicker(options.map((o) => [o.id, o.name, o.level, o.icon, `${o.type ? o.type + ' · ' : ''}${o.count} annonce${o.count > 1 ? 's' : ''}`]),
    { id: 'forge-search', placeholder: 'Chercher un équipement…', href: (itemId) => `#/forge/item/${itemId}`, browse: true });
  const header = h('section', { class: 'panel item-head', 'aria-label': 'Objet' },
    tile(d.icon, true),
    h('div', { class: 'info' }, h('div', { class: 'title' }, d.name),
      h('div', { class: 'muted' }, `${(options.find((o) => o.id === id) || {}).type || 'Équipement'} · niveau ${d.level}` + (d.template_known ? ` · ${d.listings.length} annonces relevées ${ago(d.captured_at, d.now)}` : ''))),
    picker,
    h('a', { class: 'btn', href: `#/item/${id}`, style: 'display: inline-flex; align-items: center' }, 'Fiche objet'));
  if (!d.template_known) {
    return [header, h('div', { class: 'note' }, "Les caractéristiques de base de cet objet n'ont pas pu être récupérées sur DofusDB (service injoignable ?) : exos, overs et jets ne peuvent pas être lus pour l'instant. "),
      h('button', { class: 'btn', onclick: () => { delete S.cache[`forge-${id}`]; refresh(); } }, 'Réessayer')];
  }

  const saved = d.filter || {};
  const f = S.ui.forge[id] || (S.ui.forge[id] = {
    minimums: Object.fromEntries(Object.entries(saved.minimums || {}).map(([k, v]) => [k, Number(v)])),
    exo: saved.exo === undefined ? null : saved.exo, exo_min: saved.exo_min || 1, showAll: false,
    transcended: typeof saved.transcended === 'boolean' ? saved.transcended : null,
  });
  const change = (mutate) => { mutate(f); saveFilter(id, { minimums: f.minimums, exo: f.exo, exo_min: f.exo_min, transcended: f.transcended });
    for (const key of Object.keys(S.cache)) if (key.startsWith('ranking')) delete S.cache[key]; // le classement dépend des critères
    refresh();
  };

  const tax = d.tax;
  const net = (price) => price * (1 - tax);
  const cheapest = d.listings[0] || null;
  const plain = d.listings.find((l) => l.plain) || null;
  const matching = d.listings.filter((l) => matches(l, f));
  const best = matching[0] || null;
  const median = matching.length ? matching[Math.floor((matching.length - 1) / 2)].price : null;
  const hasCriteria = Object.keys(f.minimums).length > 0 || f.exo !== null || f.transcended !== null;

  const base = h('section', { class: 'kpis', 'aria-label': 'Craft contre hôtel de vente' },
    kpi('Coût de craft', fmt(d.craft_cost), d.craft_cost === null ? 'prix manquant' : null),
    kpi('Moins cher en vente', fmt(cheapest && cheapest.price), cheapest ? cheapest.label : null),
    kpi('Moins cher de base', fmt(plain && plain.price), ((n) => `${n} annonce${n > 1 ? 's' : ''}`)(d.listings.filter((l) => l.plain).length)),
    kpi('Craft puis revente', plain && d.craft_cost !== null ? signed(net(plain.price) - d.craft_cost) : '—', 'net de taxe', plain && d.craft_cost !== null && net(plain.price) - d.craft_cost >= 0 ? 'gain' : 'warn'));

  const lostWarning = cheapest && cheapest.missing.length
    ? h('div', { class: 'banner' }, `L'exemplaire le moins cher a perdu une ligne de base : ${cheapest.missing.map((i) => d.names[i]).join(', ')}. Il n'est pas comparable à un craft.`)
    : null;

  const lineRows = d.lines.map((line) => {
    const value = f.minimums[line.id];
    return h('div', { class: 'line-row' },
      h('label', { for: `line-${line.id}`, class: 'with-ico' }, statIcon(d.assets[line.id]), line.name),
      h('span', { class: 'range' }, line.min === line.max ? `${line.min}` : `${line.min} à ${line.max}`),
      h('input', { id: `line-${line.id}`, type: 'number', min: 0, value: value || '', placeholder: '—', class: value ? 'set' : '',
        onchange: (e) => change((x) => { const n = Number(e.target.value); if (n > 0) x.minimums[line.id] = n; else delete x.minimums[line.id]; }) }));
  });
  const criteria = h('section', { class: 'panel pad criteria', 'aria-label': 'Critères' },
    h('div', { style: 'display: flex; align-items: center; justify-content: space-between; gap: 12px' }, h('h2', {}, 'Critères'),
      h('div', { style: 'display: flex; gap: 8px' },
        h('button', { class: 'btn', onclick: () => change((x) => { x.minimums = Object.fromEntries(d.lines.map((l) => [l.id, l.max])); x.exo = 0; }) }, 'Jets parfaits'),
        h('button', { class: 'btn quiet', onclick: () => change((x) => { x.minimums = {}; x.exo = null; x.exo_min = 1; x.transcended = null; }) }, 'Réinitialiser'))),
    h('div', {},
      h('div', { class: 'line-row head cap' }, h('span', {}, 'Ligne de base'), h('span', { style: 'text-align: right' }, 'De base'), h('span', { style: 'text-align: right' }, 'Minimum')),
      lineRows.length ? lineRows : h('div', { class: 'muted small' }, "Cet objet n'a pas de caractéristique de base à régler.")),
    h('div', { class: 'exo-row' },
      h('div', { class: 'field' }, h('label', { for: 'exo-pick' }, 'Exo'),
        h('select', { id: 'exo-pick', class: f.exo ? 'exo-set' : '', onchange: (e) => change((x) => { x.exo = e.target.value === '' ? null : Number(e.target.value); }) },
          h('option', { value: '', selected: f.exo === null }, 'Peu importe'), h('option', { value: 0, selected: f.exo === 0 }, 'Aucun exo'),
          d.exos.map((x) => h('option', { value: x.id, selected: f.exo === x.id }, `${x.name} · ${x.count} annonce${x.count > 1 ? 's' : ''}`)))),
      h('div', { class: 'field' }, h('label', { for: 'exo-min', style: 'text-align: right' }, 'Minimum'),
        h('input', { id: 'exo-min', type: 'number', min: 1, value: f.exo_min, disabled: !f.exo, class: f.exo ? 'exo-set' : '', onchange: (e) => change((x) => { x.exo_min = Math.max(1, Number(e.target.value) || 1); }) }))),
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Rune de transcendance'),
      segmented('Rune de transcendance', [[null, 'Peu importe'], [false, 'Sans'], [true, 'Seulement']], f.transcended, (value) => change((x) => { x.transcended = value; }))),
    null);

  const result = h('div', { class: 'kpis strong' },
    kpi('Correspondent', [String(matching.length), h('small', {}, ` sur ${d.listings.length}`)]),
    kpi('Le moins cher', fmt(best && best.price), median !== null ? `médiane ${fmt(median)}` : null),
    kpi('Prime sur la base', best && plain ? signed(best.price - plain.price) : '—', 'avant runes', 'accent'),
    kpi('Gain sur un craft', best && d.craft_cost !== null ? signed(net(best.price) - d.craft_cost) : '—', 'net de taxe, avant runes', 'gain'));

  const shown = f.showAll ? d.listings : matching;
  const table = h('div', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, f.showAll ? 'Toutes les annonces' : hasCriteria ? 'Annonces qui correspondent' : 'Annonces'),
      h('div', { class: 'legend' }, h('span', {}, h('span', { class: 'swatch', style: 'background: var(--accent)' }), 'jet au maximum'), h('span', {}, h('span', { class: 'swatch', style: 'background: var(--warn)' }), 'sous le minimum de base'))),
    shown.length === 0 ? h('div', { class: 'empty' }, 'Aucune annonce ne correspond à ces critères.') : listingsTable(shown, d, f),
    h('div', { class: 'panel-foot' }, h('span', {}, 'Prix demandés.'),
      hasCriteria && h('button', { class: 'btn', onclick: () => { f.showAll = !f.showAll; refresh(); } }, f.showAll ? 'Seulement celles qui correspondent' : `Voir les ${d.listings.length} annonces`)));

  const count = (test) => d.listings.filter(test).length;
  const breakdown = h('div', { class: 'tags', style: 'align-items: center' }, 
    h('span', { class: 'tag' }, `${count((l) => l.plain)} de base`),
    h('span', { class: 'tag exo' }, `${count((l) => l.exo.length && !l.missing.length)} exo`),
    h('span', { class: 'tag over' }, `${count((l) => l.over.length && !l.exo.length && !l.missing.length)} over`),
    h('span', { class: 'tag bad' }, `${count((l) => l.missing.length)} avec une ligne perdue`),
    h('span', { class: 'tag trans' }, `${count((l) => l.transcended)} transcendés`));

  const gone = h('details', { class: 'panel' },
    h('summary', {}, `Annonces disparues depuis une visite précédente · ${d.gone.length || "aucune pour l'instant"}`),
    d.gone.length ? listingsTable(d.gone, d, f, true)
      : h('div', { class: 'empty' }, 'Aucune pour l\'instant.'));

  return [header, base, lostWarning, h('div', { class: 'split' }, criteria, h('section', { class: 'results', 'aria-label': 'Résultat' }, result, table, breakdown, gone))];
}

function sortValue(l, key, d) {
  if (key === 'price') return l.price;
  if (key === 'type') return l.label;
  if (key === 'quality') return l.quality === null ? -1 : l.quality;
  if (key === 'exo') return l.exo.length ? `${d.names[l.exo[0]]} ${String(l.values[l.exo[0]] || 0).padStart(6, '0')}` : '';
  if (key === 'seen') return l.last_seen || 0;
  return l.values[key] || 0;
}

/** Infobulle d'un exemplaire, à la manière de celle du jeu : toutes ses lignes, lues par rapport à l'objet de base. */
const $tip = h('div', { class: 'item-tip', hidden: true, role: 'tooltip' });
document.body.append($tip);

function itemTooltip(l, d) {
  // Ordre du jeu : les lignes de dégâts d'une arme, puis les exos, puis les caractéristiques de base.
  const fixed = (d.fixed || []).map((line) => h('div', { class: 'tip-line fixed' },
    h('span', { class: 'v' }, line.min === line.max ? `${line.min}` : `${line.min} à ${line.max}`), h('span', { class: 'n' }, line.name), h('span', { class: 'r' }, '')));
  const lines = [];
  for (const line of d.lines) {
    const v = l.values[line.id] || 0;
    const range = line.min === line.max ? `${line.min}` : `${line.min} à ${line.max}`;
    const state = v <= 0 ? ['low', 'perdue'] : v > line.max ? ['over', `+${v - line.max}`] : v >= line.max && line.max > line.min ? ['max', 'max'] : v < line.min ? ['low', 'bas'] : ['', ''];
    lines.push([line.id, h('div', { class: 'tip-line ' + state[0] }, h('span', { class: 'v' }, v), h('span', { class: 'n with-ico' }, statIcon(d.assets[line.id]), line.name), h('span', { class: 'r' }, `${range}${state[1] ? ' · ' + state[1] : ''}`))]);
  }
  const exos = l.exo.map((effect) => [effect, h('div', { class: 'tip-line exo' }, h('span', { class: 'v' }, l.values[effect]), h('span', { class: 'n with-ico' }, statIcon(d.assets[effect]), d.names[effect]), h('span', { class: 'r' }, 'exo'))]);
  for (const [effect, bounds] of Object.entries(d.template || {})) {
    if (bounds[1] > 0) continue; // malus de base
    const v = l.values[effect];
    if (v !== undefined) lines.push([Number(effect), h('div', { class: 'tip-line malus' }, h('span', { class: 'v' }, `−${Math.abs(v)}`), h('span', { class: 'n with-ico' }, statIcon(d.assets[effect]), d.names[effect]), h('span', { class: 'r' }, 'malus'))]);
  }
  // Même ordre que l'infobulle du jeu : chaque ligne à sa place, exos compris.
  const rank = new Map((d.order || []).map((effect, index) => [Number(effect), index]));
  const byRank = (a, b) => (rank.get(a[0]) ?? 1e9) - (rank.get(b[0]) ?? 1e9);
  lines.sort(byRank);
  exos.sort(byRank);
  const all = [...fixed, ...exos.map((entry) => entry[1]), ...lines.map((entry) => entry[1])];
  return [
    h('div', { class: 'tip-head' }, tile(d.icon), h('div', {}, h('div', { class: 'tip-name' }, d.name), h('div', { class: 'muted small' }, `Niv. ${d.level}${l.quality !== null ? ` · jets ${l.quality} %` : ''}`)), typeTag(l)),
    h('div', { class: 'tip-lines' }, all.length ? all : h('div', { class: 'muted' }, 'Aucune caractéristique transmise.')),
    h('div', { class: 'tip-foot' }, h('span', { class: 'muted' }, 'Prix'), h('b', {}, fmt(l.price))),
  ];
}

function showTip(event, nodes) {
  $tip.replaceChildren(...nodes.filter(Boolean));
  $tip.hidden = false;
  placeTip(event);
}

function placeTip(event) {
  const pad = 18, box = $tip.getBoundingClientRect();
  let x = event.clientX + pad, y = event.clientY + pad;
  if (x + box.width > window.innerWidth - 8) x = event.clientX - box.width - pad;
  if (y + box.height > window.innerHeight - 8) y = Math.max(8, window.innerHeight - box.height - 8);
  $tip.style.left = `${Math.max(8, x)}px`;
  $tip.style.top = `${y}px`;
}

function listingsTable(listings, d, f, withDates) {
  const sort = f.sort || (f.sort = { key: 'price', dir: 1 });
  listings = listings.slice().sort((a, b) => {
    const x = sortValue(a, sort.key, d), y = sortValue(b, sort.key, d);
    return (typeof x === 'string' ? x.localeCompare(y, 'fr') : x - y) * sort.dir || a.price - b.price;
  });
  const head = (key, label, left, title) => h('th', { class: (left ? 'l ' : '') + (sort.key === String(key) ? 'sorted' : ''), title, 'aria-sort': sort.key === String(key) ? (sort.dir > 0 ? 'ascending' : 'descending') : null },
    h('button', { class: 'th-sort', onclick: () => {
      if (sort.key === String(key)) sort.dir = -sort.dir;
      else { sort.key = String(key); sort.dir = key === 'price' || key === 'type' || key === 'exo' ? 1 : -1; } // une stat : la plus haute d'abord
      refresh();
    } }, label, sort.key === String(key) ? (sort.dir > 0 ? ' ↑' : ' ↓') : ''));
  const short = (name) => name.replace('% Résistance', 'Ré').replace('Dommages', 'Do').replace('Dommage', 'Do').replace('Intelligence', 'Int').replace('Vitalité', 'Vita').replace('Agilité', 'Agi').replace('Sagesse', 'Sag').replace('Initiative', 'Ini').replace('Prospection', 'PP').replace('% Critique', 'Crit');
  return h('div', { class: 'scroll' }, h('table', { style: `min-width: ${420 + d.lines.length * 62}px` },
    h('thead', {}, h('tr', {}, head('price', 'Prix'), head('type', 'Type', true), head('quality', 'Jets', true), head('exo', 'Exo', true),
      d.lines.map((line) => head(line.id, [statIcon(d.assets[line.id]), short(line.name)], false, line.name)), withDates && head('seen', 'Vue pour la dernière fois'))),
    h('tbody', {}, listings.map((l) => h('tr', {
      onmouseenter: (event) => showTip(event, itemTooltip(l, d)),
      onmousemove: placeTip,
      onmouseleave: () => { $tip.hidden = true; },
    },
      h('td', { style: 'font-weight: 600' }, fmt(l.price)),
      h('td', { class: 'l' }, typeTag(l)),
      h('td', { class: 'l' }, l.quality === null ? h('span', { class: 'muted' }, '—')
        : [h('span', { class: 'bar thin' }, h('span', { style: `width: ${l.quality}%` })), h('span', { class: 'muted' }, ` ${l.quality} %`)]),
      h('td', { class: 'l' }, l.exo.length ? h('div', { class: 'tags' }, l.exo.map((i) => h('span', { class: 'tag exo with-ico' }, statIcon(d.assets[i]), `${d.names[i]} ${l.values[i]}`))) : h('span', { class: 'muted' }, '—')),
      d.lines.map((line) => { const v = l.values[line.id] || 0; return h('td', { class: v >= line.max && line.max > line.min ? 'max' : v > line.max ? 'max' : v < line.min ? 'low' : 'soft' }, v); }),
      withDates && h('td', { class: 'muted' }, when(l.last_seen)))))));
}

async function forgeRanking() {
  const ui = S.ui.ranking;
  const first = await cached('rankingExos', '/api/forge/ranking?criterion=saved');
  if (ui.exo === null && first.exos.length) {
    const preferred = first.exos.find((x) => x.name === 'PA') || first.exos[0];
    ui.exo = preferred.id;
  }
  if (ui.effect === null && first.lines.length) ui.effect = (first.lines.find((x) => x.name === '% Critique') || first.lines[0]).id;
  const query = ui.criterion === 'exo' ? `criterion=exo&exo=${ui.exo}`
    : ui.criterion === 'over' ? `criterion=over&effect=${ui.effect}&amount=${ui.amount}` : 'criterion=saved';
  const data = await cached(`ranking-${query}`, `/api/forge/ranking?${query}`);
  const set = (patch) => { Object.assign(ui, patch); refresh(); };
  const exoName = (first.exos.find((x) => x.id === ui.exo) || {}).name || 'exo';
  const lineName = (first.lines.find((x) => x.id === ui.effect) || {}).name || 'caractéristique';
  const label = ui.criterion === 'exo' ? `Avec exo ${exoName}` : ui.criterion === 'over' ? `${lineName} à +${ui.amount} ou plus` : 'Selon mes critères';
  const key = ui.start === 'base' ? 'Prime sur la base' : 'Gain sur le craft';

  const typeCounts = new Map();
  for (const r of data.rows) if (r['Moins cher selon critère'] !== null) typeCounts.set(r.type || 'Autres', (typeCounts.get(r.type || 'Autres') || 0) + 1);
  if (ui.type && !typeCounts.has(ui.type)) ui.type = '';
  const rows = data.rows.filter((r) => r['Moins cher selon critère'] !== null && (!ui.type || (r.type || 'Autres') === ui.type))
    .sort((a, b) => (b[key] ?? -Infinity) - (a[key] ?? -Infinity));
  const top = Math.max(1, ...rows.map((r) => r[key] || 0));

  const controls = h('section', { class: 'panel pad filters', 'aria-label': 'Comparaison' },
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Amélioration comparée'),
      segmented('Amélioration comparée', [['exo', 'Un exo'], ['over', 'Un over'], ['saved', 'Mes critères']], ui.criterion, (criterion) => set({ criterion }))),
    ui.criterion === 'exo' && h('div', { class: 'field', style: 'flex: 0 1 280px' }, h('label', { for: 'rank-exo' }, 'Exo'),
      h('select', { id: 'rank-exo', class: 'exo-set', onchange: (e) => set({ exo: Number(e.target.value) }) },
        first.exos.map((x) => h('option', { value: x.id, selected: x.id === ui.exo }, `${x.name} · ${x.count} annonce${x.count > 1 ? 's' : ''}`)))),
    ui.criterion === 'over' && h('div', { class: 'field', style: 'flex: 0 1 240px' }, h('label', { for: 'rank-line' }, 'Caractéristique'),
      h('select', { id: 'rank-line', class: 'set', onchange: (e) => set({ effect: Number(e.target.value) }) },
        first.lines.map((x) => h('option', { value: x.id, selected: x.id === ui.effect }, `${x.name} · ${x.count} objet${x.count > 1 ? 's' : ''}`)))),
    ui.criterion === 'over' && h('div', { class: 'field', style: 'flex: 0 1 170px' }, h('label', { for: 'rank-amount' }, 'Au-dessus du jet parfait'),
      h('input', { id: 'rank-amount', type: 'number', min: 1, value: ui.amount, class: 'set', onchange: (e) => set({ amount: Math.max(1, Number(e.target.value) || 1) }) })),
    h('div', { class: 'field', style: 'flex: 0 1 200px' }, h('label', { for: 'rank-type' }, "Type d'objet"),
      h('select', { id: 'rank-type', class: ui.type ? 'set' : '', onchange: (e) => set({ type: e.target.value }) },
        h('option', { value: '' }, 'Tous les types'),
        [...typeCounts.entries()].sort((a, b) => a[0].localeCompare(b[0], 'fr')).map(([name, n]) => h('option', { value: name, selected: name === ui.type }, `${name} · ${n}`)))),
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Point de départ'),
      segmented('Point de départ', [['base', 'Acheter de base'], ['craft', 'Fabriquer']], ui.start, (start) => set({ start }))));

  const body = rows.map((r, index) => {
    const value = r[key];
    return h('tr', { class: 'link', onclick: () => { location.hash = `#/forge/item/${r.item_id}`; } },
      h('td', { class: 'muted' }, index + 1),
      h('td', { class: 'l' }, itemCell(r.icon, r['Objet'], `${r.type ? r.type + ' · ' : ''}${r['Annonces']} annonces` + (r['Attention'] ? ` · ${r['Attention']}` : ''))),
      h('td', { class: r['Moins cher de base'] === null ? 'muted' : 'soft' }, fmt(r['Moins cher de base'])),
      h('td', {}, h('div', { style: 'font-weight: 500' }, fmt(r['Moins cher selon critère'])),
        h('div', { class: 'small ' + (r['Correspondent'] === 1 ? 'warn' : 'muted'), style: r['Correspondent'] === 1 ? 'font-weight: 600' : '' }, r['Correspondent'] === 1 ? '1 seule annonce' : `${r['Correspondent']} annonces`)),
      h('td', { class: 'l' }, value === null
        ? h('span', { class: 'tag' }, ui.start === 'base' ? 'aucun exemplaire de base en vente' : 'coût de craft inconnu')
        : h('div', { class: 'bar-cell' }, h('span', { class: 'bar' }, h('span', { style: `width: ${Math.max(0, Math.round(value / top * 100))}%` })), h('span', { class: 'num', style: 'text-align: right' }, signed(value)))),
      h('td', { class: 'soft' }, fmt(r['Coût de craft'])),
      h('td', { class: 'gain', style: 'font-weight: 600' }, signed(ui.start === 'base' ? r['Gain sur le craft'] : r['Prime sur la base'])));
  });

  return [controls, h('section', { class: 'panel', 'aria-label': 'Classement' },
    rows.length === 0
      ? h('div', { class: 'empty' }, ui.criterion === 'saved' ? "Aucun critère enregistré : règle-les dans l'onglet « Par objet »."
        : ui.criterion === 'over' ? `Aucune annonce connue avec ${lineName} à ${ui.amount} ou plus au-dessus de son jet parfait, parmi les ${data.rows.length} objets qui ont cette ligne de base.`
        : 'Aucun objet connu ne répond à ce critère.')
      : h('div', { class: 'scroll' }, h('table', { style: 'min-width: 940px' },
        h('thead', {}, h('tr', {}, h('th', {}, '#'), h('th', { class: 'l' }, 'Objet'), h('th', {}, 'De base'), h('th', {}, label),
          h('th', { class: 'l sorted' }, (ui.start === 'base' ? 'Marge sur la base' : 'Marge sur un craft') + ' ↓'), h('th', {}, 'Coût de craft'),
          h('th', {}, ui.start === 'base' ? 'Marge sur un craft' : 'Marge sur la base'))),
        h('tbody', {}, body))),
    h('div', { class: 'panel-foot' },
      h('span', {}, ui.criterion === 'over'
        ? `${rows.length} objets sur ${data.rows.length} avec ${lineName} · avant runes`
        : `${rows.length} objets sur ${data.known} · avant runes`),
      h('span', { class: 'warn' }, '1 seule annonce = fragile')))];
}

// ---------------------------------------------------------------- page Tendances

async function pageTrends() {
  const data = await cached('trends', '/api/trends');
  const status = S.status || {};
  const head = h('header', { class: 'head' }, h('div', {}, h('h1', {}, 'Tendances'),
    h('div', { class: 'lead' }, 'Écarts par unité')));
  if (!data.rows.length) {
    return [head, h('div', { class: 'panel empty' }, `Données insuffisantes. Une tendance demande au moins ${status.min_snapshots_for_trend || 5} relevés de prix moyens (${status.snapshots || 0} pour l'instant), ou l'historique du cours du marché de l'objet.`)];
  }
  const price = (v) => (v !== null && v < 100 && !Number.isInteger(v) ? v.toFixed(2).replace('.', ',') : fmt(v));
  const reference = (r) => (r['Prix moyen'] !== null && r['Prix moyen'] !== undefined ? [r['Prix moyen'], 'prix moyen du jeu']
    : r['Moyenne 30 j'] !== null ? [r['Moyenne 30 j'], 'moyenne 30 j'] : [r['Moyenne 7 j'], 'moyenne 7 j']);
  const table = (title, rows, positive) => h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, title), h('span', { class: 'muted small' }, `${rows.length} objet${rows.length > 1 ? 's' : ''}`)),
    rows.length === 0 ? h('div', { class: 'empty' }, 'Aucun pour le moment.')
      : h('div', { class: 'scroll' }, h('table', { style: 'min-width: 640px' },
        h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Objet'), h('th', {}, 'Prix'), h('th', {}, 'Comparé à'), h('th', {}, 'Écart en kamas'), h('th', { class: 'sorted' }, 'Écart'), h('th', {}, 'Recettes'), h('th', {}, ''))),
        h('tbody', {}, rows.slice(0, 150).map((r) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${r.item_id}`; } },
          h('td', { class: 'l' }, itemCell(r.icon, r['Objet'], [r.type, r['Base']].filter(Boolean).join(' · '))),
          h('td', { style: 'font-weight: 500' }, price(r['Prix'])),
          h('td', {}, h('div', { class: 'soft' }, fmt(reference(r)[0])), h('div', { class: 'muted small' }, reference(r)[1])),
          h('td', { class: 'soft' }, `${positive ? '+' : '−'}${price(kamasGap(r))}`),
          h('td', { class: 'strong ' + (positive ? 'warn' : 'gain') }, `${r['Écart %'] >= 0 ? '+' : '−'}${fmt(Math.abs(r['Écart %']))} %`),
          h('td', { class: r.recipes ? 'soft' : 'muted' }, r.recipes || '—'),
          h('td', { class: 'act' }, ignoreButton(r.item_id, r['Objet']))))))),
    rows.length > 150 ? h('div', { class: 'panel-foot' }, h('span', {}, `150 sur ${fmt(rows.length)}`)) : null);
  const ui = S.ui.trends || (S.ui.trends = { category: '', type: '', usedOnly: false, pct: Math.round((status.trend_threshold || 0.15) * 100), kamas: 0 });
  const refValue = (r) => (r['Prix moyen'] !== null && r['Prix moyen'] !== undefined ? r['Prix moyen'] : r['Moyenne 30 j'] !== null ? r['Moyenne 30 j'] : r['Moyenne 7 j']);
  const kamasGap = (r) => Math.abs(r['Prix'] - refValue(r));
  // L'écart minimum remplace le seuil de signal fixe : c'est toi qui le règles.
  const signalled = data.rows.filter((r) =>
    Math.abs(r['Écart %']) >= Math.max(ui.pct, 0.5) &&
    (!(ui.kamas > 0) || kamasGap(r) >= ui.kamas) &&
    (!ui.usedOnly || r.recipes > 0));
  const count = (list, key) => { const m = new Map(); for (const r of list) m.set(r[key] || 'Autres', (m.get(r[key] || 'Autres') || 0) + 1); return [...m.entries()].sort((a, b) => b[1] - a[1]); };
  const categories = count(signalled, 'category');
  const inCategory = signalled.filter((r) => !ui.category || r.category === ui.category);
  const types = count(inCategory, 'type');
  if (ui.type && !types.some(([name]) => name === ui.type)) ui.type = '';
  const kept = inCategory.filter((r) => !ui.type || (r.type || 'Autres') === ui.type);
  const filters = h('section', { class: 'panel pad filters', 'aria-label': 'Filtres' },
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Famille'),
      segmented('Famille', [['', `Tout · ${signalled.length}`], ...categories.map(([name, n]) => [name, `${name} · ${n}`])], ui.category, (category) => { Object.assign(ui, { category, type: '' }); refresh(); })),
    h('div', { class: 'field', style: 'flex: 0 1 240px' }, h('label', { for: 't-type' }, 'Type'),
      h('select', { id: 't-type', class: ui.type ? 'set' : '', onchange: (e) => { ui.type = e.target.value; refresh(); } },
        h('option', { value: '' }, 'Tous les types'), types.map(([name, n]) => h('option', { value: name, selected: name === ui.type }, `${name} · ${n}`)))),
    h('div', { class: 'field', style: 'flex: 0 1 130px' }, h('label', { for: 't-pct' }, 'Écart min. en %'),
      h('input', { id: 't-pct', type: 'number', min: 0, value: ui.pct, class: 'set', onchange: (e) => { ui.pct = Math.max(0, Number(e.target.value) || 0); refresh(); } })),
    h('div', { class: 'field', style: 'flex: 0 1 170px' }, h('label', { for: 't-kamas' }, 'Écart min. en kamas'),
      h('input', { id: 't-kamas', type: 'number', min: 0, value: ui.kamas || '', placeholder: 'Sans minimum', class: ui.kamas > 0 ? 'set' : '', onchange: (e) => { ui.kamas = Math.max(0, Number(e.target.value) || 0); refresh(); } })),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: ui.usedOnly, onchange: (e) => { ui.usedOnly = e.target.checked; refresh(); } }), 'Masquer ce qui ne sert dans aucune recette'));
  const under = kept.filter((r) => r['Écart %'] < 0).sort((a, b) => a['Écart %'] - b['Écart %']);
  const over = kept.filter((r) => r['Écart %'] > 0).sort((a, b) => b['Écart %'] - a['Écart %']);
  return [head, filters, h('div', { class: 'grid2' }, table('Sous-cotés, à acheter', under, false), table('Sur-cotés, à vendre', over, true))];
}

// ---------------------------------------------------------------- graphiques

/** Courbe ou barres à une seule série, avec survol. points : [[ts, valeur]]. */
function chart(points, { bars = false, height = 220, unit = '', detail } = {}) {
  const W = 720, H = height, L = 64, R = 12, T = 10, B = 26;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), yMax = Math.max(...ys) || 1;
  const yMin = bars ? 0 : Math.min(...ys);
  const lo = bars ? 0 : Math.max(0, yMin - (yMax - yMin) * 0.15);
  let hi = yMax + (yMax - lo) * 0.1;
  if (!(hi > lo)) hi = lo + Math.max(1, lo * 0.1); // série plate : garder une échelle non nulle
  const X = (v) => L + (x1 === x0 ? 0.5 : (v - x0) / (x1 - x0)) * (W - L - R);
  const Y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Graphique' });
  for (let i = 0; i <= 3; i++) {
    const v = lo + (hi - lo) * i / 3;
    root.append(svg('line', { class: 'grid', x1: L, x2: W - R, y1: Y(v), y2: Y(v) }), svg('text', { class: 'axis', x: L - 8, y: Y(v) + 4, 'text-anchor': 'end' }, fmt(v)));
  }
  const span = x1 - x0;
  const ticks = Math.min(6, points.length);
  let lastLabel = -Infinity;
  for (let i = 0; i < ticks; i++) {
    const p = points[Math.round(i * (points.length - 1) / Math.max(1, ticks - 1))];
    if (X(p[0]) - lastLabel < 70) continue; // pas d'étiquettes qui se chevauchent
    lastLabel = X(p[0]);
    root.append(svg('text', { class: 'axis', x: X(p[0]), y: H - 6, 'text-anchor': 'middle' }, span > 2 * 86400 ? when(p[0], false) : when(p[0]).slice(6)));
  }
  if (bars) {
    const w = Math.max(3, Math.min(18, (W - L - R) / points.length - 4));
    root.append(svg('g', { class: 'bars' }, points.map((p) => svg('rect', { x: X(p[0]) - w / 2, y: Y(p[1]), width: w, height: Math.max(0, Y(lo) - Y(p[1])), rx: 2 }))));
  } else {
    const path = points.map((p, i) => `${i ? 'L' : 'M'}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join(' ');
    root.append(svg('path', { class: 'area', d: `${path} L${X(x1)} ${Y(lo)} L${X(x0)} ${Y(lo)} Z` }), svg('path', { class: 'series', d: path }));
  }
  const cross = svg('line', { class: 'cross', y1: T, y2: H - B, visibility: 'hidden' });
  const point = svg('circle', { class: 'point', r: 4.5, visibility: 'hidden' });
  root.append(cross, point);
  const tip = h('div', { class: 'tip', hidden: true });
  const wrap = h('div', { class: 'chart-wrap' }, root, tip);
  root.addEventListener('mousemove', (event) => {
    const box = root.getBoundingClientRect();
    const sx = (event.clientX - box.left) / box.width * W;
    let best = 0;
    points.forEach((p, i) => { if (Math.abs(X(p[0]) - sx) < Math.abs(X(points[best][0]) - sx)) best = i; });
    const p = points[best];
    cross.setAttribute('x1', X(p[0])); cross.setAttribute('x2', X(p[0])); cross.setAttribute('visibility', 'visible');
    if (!bars) { point.setAttribute('cx', X(p[0])); point.setAttribute('cy', Y(p[1])); point.setAttribute('visibility', 'visible'); }
    tip.hidden = false;
    tip.replaceChildren(h('div', { class: 'muted' }, when(p[0])), h('b', {}, `${fmt(p[1])}${unit}`), detail ? h('div', { class: 'muted' }, detail(p)) : '');
    tip.style.left = `${X(p[0]) / W * 100}%`;
    tip.style.top = `${(bars ? T : Y(p[1])) / H * 100 - 3}%`;
  });
  root.addEventListener('mouseleave', () => { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); point.setAttribute('visibility', 'hidden'); });
  return wrap;
}

function chartPanel(title, note, points, options, empty) {
  return h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, title), note && h('span', { class: 'muted small' }, note)),
    points.length >= 2 ? h('div', { class: 'chart' }, chart(points, options))
      : h('div', { class: 'empty' }, points.length === 1 ? `Un seul point pour l'instant : ${fmt(points[0][1])} le ${when(points[0][0])}.` : empty));
}

// ---------------------------------------------------------------- page Fiche objet

const norm = (text) => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();

/**
 * Barre de recherche d'objet. items : [id, nom, niveau, icône, précision facultative].
 * browse : la liste est courte, on la montre entière dès que le champ a le focus.
 */
function itemPicker(items, { id = 'item-search', placeholder = 'Chercher un objet…', href = (itemId) => `#/item/${itemId}`, browse = false } = {}) {
  const list = h('ul', { hidden: true, role: 'listbox' });
  const input = h('input', { id, type: 'search', placeholder, autocomplete: 'off', 'aria-label': placeholder });
  const show = () => {
    const q = norm(input.value.trim());
    if (q.length < 2 && !browse) { list.hidden = true; return; }
    const found = [];
    for (const it of items) { if (norm(it[1]).includes(q) || (it[4] && norm(it[4]).includes(q))) { found.push(it); if (found.length >= (browse ? 60 : 12)) break; } }
    list.replaceChildren(...found.map((it) => h('li', {}, h('button', { onmousedown: (e) => e.preventDefault(), onclick: () => { list.hidden = true; input.value = ''; location.hash = href(it[0]); } },
      tile(it[3]), h('span', {}, it[1], h('span', { class: 'muted' }, ` · niv. ${it[2]}${it[4] ? ' · ' + it[4] : ''}`))))));
    list.hidden = found.length === 0;
  };
  input.addEventListener('input', show);
  input.addEventListener('focus', show);
  input.addEventListener('blur', () => { list.hidden = true; });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') list.hidden = true;
    if (event.key === 'Enter') { const first = list.querySelector('button'); if (first && !list.hidden) first.click(); }
  });
  return h('div', { class: 'picker' }, input, list);
}

async function pageItem(r) {
  const items = (await cached('items', '/api/items')).items;
  const head = h('header', { class: 'head' }, h('div', {}, h('h1', {}, 'Fiche objet')), itemPicker(items));
  const id = r.id || S.ui.item;
  if (!id) return [head, h('div', { class: 'panel empty' }, items.length ? 'Cherche un objet.' : "Aucun objet connu : importe les données statiques et lance une capture.")];
  S.ui.item = id;
  const d = await cached(`item-${id}`, `/api/item/${id}`);
  const now = d.now;

  const header = h('section', { class: 'panel item-head' }, tile(d.icon, true),
    h('div', { class: 'info' }, h('div', { class: 'title' }, d.name), h('div', { class: 'muted' }, `${d.type || 'Objet'} · niveau ${d.level} · ${d.exchangeable ? 'échangeable' : 'non échangeable'}`),
      d.owned.known && h('div', { class: 'tags', style: 'margin-top: 4px' },
        d.owned.inventory + d.owned.bank > 0
          ? [h('span', { class: 'tag good' }, `Tu en as ${fmt(d.owned.inventory + d.owned.bank)}`), h('span', { class: 'tag' }, `inventaire ${fmt(d.owned.inventory)}`), h('span', { class: 'tag' }, `banque ${fmt(d.owned.bank)}`)]
          : h('span', { class: 'tag' }, "Tu n'en as pas"))),
    d.equipment && d.hdv && h('a', { class: 'btn', href: `#/forge/item/${id}`, style: 'display: inline-flex; align-items: center' }, 'Voir en forgemagie'),
    h('button', { class: 'btn ' + (d.ignored ? '' : 'quiet'), onclick: () => setIgnored(id, d.name, !d.ignored) }, d.ignored ? 'Ne plus ignorer' : 'Ignorer cet objet'));

  const ignoredNote = d.ignored ? h('div', { class: 'note' }, 'Cet objet est ignoré : il n\'apparaît plus dans Crafts, Tendances et les recettes de Mon stock.')
    : d.type_ignored ? h('div', { class: 'note' }, `Le type « ${d.type} » est ignoré : cet objet n'apparaît plus dans Crafts, Tendances et les recettes de Mon stock. `, h('a', { href: '#/ignored', style: 'text-decoration: underline' }, 'Gérer les types ignorés')) : null;
  const kpis = h('section', { class: 'kpis' },
    kpi('Prix de référence', fmt(d.ref && d.ref.price), d.ref ? h('span', { class: 'source' }, h('span', { class: 'dot ' + (observed(d.ref.source) ? 'on' : '') }), `${shortSource(d.ref.source)} · ${ago(d.ref.ts, now)}`) : 'Aucun prix connu'),
    kpi('Coût le plus bas', fmt(d.unit_cost.cost), d.unit_cost.mode || 'prix manquant'),
    kpi('Vendus sur 24 h', fmt(d.qty_24h), d.market_seen_at ? ago(d.market_seen_at, now) : 'cours non consulté'),
    kpi('Vendus sur 7 j', fmt(d.qty_7d)));

  // Écart entre le prix réellement demandé à l'HDV et le prix moyen annoncé par le jeu.
  let gap = null;
  if (d.hdv_unit !== null && d.hdv_unit !== undefined && d.avg_price) {
    const diff = d.hdv_unit - d.avg_price.price, pct = d.avg_price.price ? diff / d.avg_price.price * 100 : 0;
    const cheaper = diff < 0, flat = Math.abs(pct) < 0.5;
    const unit = (v) => (Number.isInteger(v) ? fmt(v) : v.toFixed(2).replace('.', ','));
    gap = h('section', { class: 'panel gap ' + (flat ? '' : cheaper ? 'under' : 'above'), 'aria-label': 'HDV contre prix moyen' },
      h('div', { class: 'gap-side' }, h('div', { class: 'label' }, "Prix à l'HDV"), h('div', { class: 'value' }, unit(d.hdv_unit)), h('div', { class: 'hint' }, ago(d.hdv.captured_at, now))),
      h('div', { class: 'gap-mid' },
        h('div', { class: 'gap-pct' }, flat ? '=' : `${cheaper ? '−' : '+'}${fmt(Math.abs(pct))} %`),
        h('div', { class: 'gap-text' }, flat ? 'égal' : `${cheaper ? '−' : '+'}${unit(Math.abs(diff))}`)),
      h('div', { class: 'gap-side right' }, h('div', { class: 'label' }, 'Prix moyen du jeu'), h('div', { class: 'value' }, fmt(d.avg_price.price)), h('div', { class: 'hint' }, ago(d.avg_price.ts, now))));
  }

  const noMarket = "Cours du marché non consulté.";
  const hourlyQty = new Map(d.hourly.map((p) => [p[0], p[2]]));
  const dailyQty = new Map(d.daily.map((p) => [p[0], p[2]]));
  const charts = h('div', { class: 'grid2' },
    chartPanel('Prix par heure', '24 dernières heures', d.hourly.map((p) => [p[0], p[1]]), { detail: (p) => `${fmt(hourlyQty.get(p[0]))} vendus` }, noMarket),
    chartPanel('Quantités vendues par heure', null, d.hourly.filter((p) => p[2] !== null).map((p) => [p[0], p[2]]), { bars: true, height: 220 }, noMarket),
    chartPanel('Prix par jour', `${d.daily.length} jours`, d.daily.map((p) => [p[0], p[1]]), { detail: (p) => `${fmt(dailyQty.get(p[0]))} vendus` }, noMarket),
    chartPanel('Prix moyen au fil des relevés', `${d.snapshots.length} relevé${d.snapshots.length > 1 ? 's' : ''}`, d.snapshots, {}, "Aucun relevé."));

  let hdv;
  if (!d.hdv) {
    hdv = h('div', { class: 'empty' }, "Fiche HDV non consultée.");
  } else if (d.hdv.kind === 'lots') {
    hdv = h('div', { class: 'scroll' }, h('table', { style: 'min-width: 360px' },
      h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Lot'), h('th', {}, 'Prix du lot'), h('th', {}, 'Prix unitaire'))),
      h('tbody', {}, d.hdv.frame.map((row) => h('tr', {}, h('td', { class: 'l', style: 'font-weight: 600' }, row['Lot']), h('td', {}, fmt(row['Prix du lot'])), h('td', { class: 'soft' }, row['Prix unitaire'].toFixed(2).replace('.', ',')))))));
  } else {
    hdv = h('div', { style: 'padding: 16px 18px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center' },
      h('span', { class: 'muted' }, `${d.hdv.frame.length} exemplaires en vente :`),
      Object.entries(d.hdv.counts || {}).map(([label, n]) => h('span', { class: 'tag' }, `${n} ${label}`)),
      h('a', { class: 'btn', href: `#/forge/item/${id}`, style: 'display: inline-flex; align-items: center; margin-left: auto' }, 'Détail en forgemagie'));
  }
  const hdvPanel = h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Hôtel de vente'), d.hdv && h('span', { class: 'muted small' }, `Relevé ${ago(d.hdv.captured_at, now)}`)), hdv);

  let recipe;
  if (!d.craft) {
    recipe = h('div', { class: 'empty' }, 'Pas de recette.');
  } else {
    const c = d.craft;
    recipe = [
      h('div', { class: 'kpis', style: 'border-radius: 0; border-left: 0; border-right: 0' },
        kpi('Vente nette de taxe', fmt(c.revenue)), kpi('Coût de fabrication', fmt(c.cost)),
        kpi('Marge', signed(c.margin), c.margin_pct === null ? null : `${fmt(c.margin_pct * 100)} %`, c.margin === null ? '' : c.margin >= 0 ? 'gain' : 'warn'),
        d.owned.known && kpi('Faisable en stock', c.craftable > 0 ? `× ${fmt(c.craftable)}` : '0', null, c.craftable > 0 ? 'gain' : '')),
      h('div', { class: 'scroll' }, h('table', { style: 'min-width: 720px' },
        h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Ingrédient'), h('th', {}, 'Quantité'), h('th', {}, 'En stock'), h('th', {}, 'Prix unitaire'), h('th', { class: 'l' }, 'Obtenu par'), h('th', {}, 'Sous-total'))),
        h('tbody', {}, c.ingredients.map((row) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${row.item_id}`; } },
          h('td', { class: 'l' }, itemCell(row.icon, row['Ingrédient'], row['Source'] ? `${row['Source']} · ${ageLabel(row['Âge (h)'])}` : null)),
          h('td', { class: 'soft' }, fmt(row['Quantité'])),
          h('td', {}, !d.owned.known ? h('span', { class: 'muted' }, '—') : haveTag(row.have, row['Quantité'])),
          h('td', {}, fmt(row['Coût retenu'])),
          h('td', { class: 'l' }, h('span', { class: 'tag ' + (row['Mode'] === 'craft' ? 'info' : row['Mode'] === 'achat' ? '' : 'bad') }, row['Mode'])),
          h('td', { style: 'font-weight: 600' }, fmt(row['Sous-total'])))))))];
  }
  const recipePanel = h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Recette'), d.craft && h('span', { class: 'muted small' }, `${d.craft.job} niveau ${d.craft.level}` + (d.craft.own_job === true ? ' · dans tes métiers' : d.craft.own_job === false ? ' · hors de tes métiers' : ''))),
    d.craft && d.craft.flags.length ? h('div', { style: 'padding: 12px 18px 0' }, h('div', { class: 'tags' }, d.craft.flags.map((flag) => h('span', { class: 'tag ' + (flag.includes('manquant') || flag.includes('non échangeable') ? 'bad' : '') }, flag)))) : null,
    recipe);

  const usedPanel = h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Utilisé dans'), h('span', { class: 'muted small' }, `${d.used_in.length} recette${d.used_in.length > 1 ? 's' : ''}`)),
    d.used_in.length === 0 ? h('div', { class: 'empty' }, "Aucune recette.")
      : h('div', { class: 'scroll' }, h('table', { style: 'min-width: 620px' },
        h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Objet'), h('th', { class: 'l' }, 'Métier'), h('th', {}, 'Quantité utilisée'), h('th', {}, 'Prix de vente'), h('th', {}, 'Marge'))),
        h('tbody', {}, d.used_in.slice(0, 80).map((row) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${row.item_id}`; } },
          h('td', { class: 'l' }, itemCell(row.icon, row['Objet'])), h('td', { class: 'l soft' }, row['Métier'], h('span', { class: 'muted' }, ` · ${row['Niveau']}`)),
          h('td', { class: 'soft' }, fmt(row['Quantité utilisée'])), h('td', {}, fmt(row['Prix de vente'])),
          h('td', { class: 'strong ' + (row['Marge'] === null ? 'muted' : row['Marge'] >= 0 ? 'gain' : 'warn') }, signed(row['Marge']))))))));

  return [head, header, ignoredNote, kpis, gap, charts, hdvPanel, recipePanel, usedPanel];
}

// ---------------------------------------------------------------- page État

async function pageStatus() {
  S.status = await api('/api/status');
  renderCapture();
  const s = S.status, now = s.now;
  const rows = [
    ['Dernière connexion au jeu captée', when(s.last_connection_ts)], ['Derniers prix moyens décodés', when(s.last_avg_prices_ts)],
    ['Relevés de prix moyens en base', fmt(s.snapshots)], ['Objets avec un prix moyen', fmt(s.priced_items)],
    ['Objets avec un dernier prix de vente', fmt(s.last_sales)], ['Objets avec un historique du cours du marché', fmt(s.history_items)],
    ['Objets avec des annonces HDV', fmt(s.hdv_items)], ['Version des données statiques', s.static_version || 'non importées'],
    ['Import des données statiques', when(s.static_imported_at)], ['Objets connus', fmt(s.items)], ['Recettes connues', fmt(s.recipes)],
  ];
  return [
    h('header', { class: 'head' }, h('div', {}, h('h1', {}, 'État'))),
    s.decode_alert && h('div', { class: 'banner', role: 'alert' }, `${s.decode_alert} (alerte du ${when(s.decode_alert_ts)})`),
    s.unknown_jobs && s.unknown_jobs.length ? h('div', { class: 'note' }, `Métiers inconnus dans config.toml : ${s.unknown_jobs.join(', ')}`) : null,
    h('section', { class: 'kpis' },
      kpi('Capture', s.running ? 'En cours' : 'Arrêtée', s.running ? ago(s.started_ts, now) : when(s.stopped_ts), s.running ? 'gain' : ''),
      kpi('Dernier relevé de prix', ago(s.last_snapshot_ts, now), when(s.last_snapshot_ts)),
      kpi('Décodage', s.decode_alert ? 'Alerte' : 'Normal', s.decode_alert ? 'voir MAINTENANCE.md' : null, s.decode_alert ? 'warn' : 'gain')),
    h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Données')),
      h('table', {}, h('tbody', {}, rows.map(([label, value]) => h('tr', {}, h('td', { class: 'l soft' }, label), h('td', { style: 'font-weight: 500' }, value)))))),
    h('section', { class: 'panel pad' }, h('h2', { style: 'margin-bottom: 12px' }, 'Limites à garder en tête'),
      h('ul', { class: 'list' },
        h('li', {}, 'Les prix moyens sont théoriques : lissés, en retard sur le marché, sans distinction de lot ni de forgemagie.'),
        h('li', {}, "Les annonces HDV sont des prix demandés, pas des ventes ; elles ne couvrent que les objets que tu ouvres."),
        h('li', {}, "Les marges ignorent le temps passé, le coût des runes et l'XP de métier."),
        h('li', {}, 'Une mise à jour du jeu peut casser le décodage jusqu\'à ré-identification.'))),
  ];
}

// ---------------------------------------------------------------- démarrage

(async function start() {
  renderNav();
  renderCapture();
  try { S.status = await api('/api/status'); } catch (error) { /* affiché par la page */ }
  renderCapture();
  await render();
  await poll();
  setInterval(poll, 5000);
  setInterval(renderCapture, 30000);
})();
