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

/** Quantité au format du jeu : 9 999 en entier, puis 12k, 1,2M. */
function compactQty(n) {
  if (n < 10000) return String(n);
  if (n < 1e6) return `${Math.floor(n / 1000)}k`;
  return `${(Math.floor(n / 1e5) / 10).toString().replace('.', ',')}M`;
}

/** Icône d'un objet, avec en bas à droite la quantité possédée (inventaire + banque), comme en jeu. */
function tile(iconId, big, itemId) {
  const box = h('span', { class: big ? 'tile big' : 'tile' });
  if (iconId) {
    const img = h('img', { src: `/icons/${iconId}.png`, alt: '', loading: 'lazy' });
    img.addEventListener('error', () => img.remove());
    box.append(img);
  }
  const owned = itemId !== undefined && S.status && S.status.owned ? S.status.owned[itemId] : null;
  const total = owned ? owned[0] + owned[1] + (owned[2] || 0) : 0;
  if (total > 0) {
    box.append(h('span', { class: 'qty', title: `Possédé : ${fmt(total)} (inventaire ${fmt(owned[0])}, banque ${fmt(owned[1])}` + (owned[2] ? `, havre-sac ${fmt(owned[2])})` : ')') }, compactQty(total)));
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

const itemCell = (iconId, name, sub, itemId) =>
  h('div', { class: 'item-cell', ...(itemId ? hoverTip(itemId) : {}) }, tile(iconId, false, itemId), h('div', {}, h('div', { class: 'name' }, name), sub && h('div', { class: 'sub' }, sub)));

// Infobulle d'un objet au survol : ses caractéristiques de base, comme en jeu, et ses prix connus.
const tipData = {};
let tipWanted = null, tipTimer = null;

function hoverTip(itemId) {
  return {
    onmouseenter: (event) => {
      tipWanted = itemId;
      clearTimeout(tipTimer);
      // Petit délai : parcourir une liste ne déclenche pas une infobulle par ligne.
      tipTimer = setTimeout(async () => {
        if (!tipData[itemId]) tipData[itemId] = api(`/api/item/${itemId}/tip`).catch(() => { delete tipData[itemId]; return null; });
        const data = await tipData[itemId];
        if (data && tipWanted === itemId) showTip(event, baseTooltip(data));
      }, 180);
    },
    onmousemove: (event) => { if (!$tip.hidden && tipWanted === itemId) placeTip(event); },
    onmouseleave: () => { tipWanted = null; clearTimeout(tipTimer); $tip.hidden = true; },
  };
}

/** Survol d'un prix d'équipement tiré d'une annonce HDV : les jets de l'exemplaire retenu. */
const listingData = {};
function hoverListing(itemId, source, price) {
  if (!source || !(source.includes('sans exo') || source.includes('compris'))) return {}; // sources propres aux équipements
  const cheapest = source.includes('compris'); // sinon : jet de base
  return {
    onmouseenter: (event) => {
      tipWanted = `listing-${itemId}`;
      clearTimeout(tipTimer);
      tipTimer = setTimeout(async () => {
        if (!listingData[itemId]) listingData[itemId] = api(`/api/forge/item/${itemId}`).catch(() => { delete listingData[itemId]; return null; });
        const d = await listingData[itemId];
        if (!d || !d.template_known || tipWanted !== `listing-${itemId}`) return;
        const pool = d.listings.filter((l) => (cheapest ? l.missing.length <= 2 : l.plain)).sort((a, b) => a.price - b.price);
        const chosen = pool.find((l) => l.price === price) || pool[0];
        if (!chosen) return;
        showTip(event, [h('div', { class: 'tip-note' }, cheapest ? 'Exemplaire le moins cher en vente' : 'Jet de base le moins cher en vente'), ...itemTooltip(chosen, d)]);
      }, 180);
    },
    onmousemove: (event) => { if (!$tip.hidden && tipWanted === `listing-${itemId}`) placeTip(event); },
    onmouseleave: () => { tipWanted = null; clearTimeout(tipTimer); $tip.hidden = true; },
  };
}

/** Survol d'un de mes lots d'équipement en vente : les jets de mon exemplaire, comme une annonce de l'HDV. */
function hoverLot(r, key) {
  return r.rolls ? hoverRolls(r.item_id, r.rolls, `lot-${key}`, 'Ton exemplaire en vente') : hoverTip(r.item_id);
}

/** Survol qui montre un exemplaire précis d'un équipement (jets, exo, over), comme une annonce de l'HDV. */
function hoverRolls(itemId, rolls, wanted, note) {
  return {
    onmouseenter: (event) => {
      tipWanted = wanted;
      clearTimeout(tipTimer);
      tipTimer = setTimeout(async () => {
        if (!listingData[itemId]) listingData[itemId] = api(`/api/forge/item/${itemId}`).catch(() => { delete listingData[itemId]; return null; });
        const d = await listingData[itemId];
        if (!d || !d.template_known || tipWanted !== wanted) return;
        showTip(event, [h('div', { class: 'tip-note' }, note), ...itemTooltip(rolls, d)]);
      }, 180);
    },
    onmousemove: (event) => { if (!$tip.hidden && tipWanted === wanted) placeTip(event); },
    onmouseleave: () => { tipWanted = null; clearTimeout(tipTimer); $tip.hidden = true; },
  };
}

const similarTitle = (x) => `${x.count} annonce${x.count > 1 ? 's' : ''} similaire${x.count > 1 ? 's' : ''} sur ${x.listings} du même modèle : mêmes exos, mêmes overs`
  + (x.confidence === 'fiable' ? ', jets au moins aussi bons à 10 % près.' : x.confidence === 'approximative' ? ', jets au moins aussi bons à 20 % près.' : ', sans comparer les autres jets.');

function baseTooltip(d) {
  const range = (line) => (line.min === line.max ? `${line.min}` : `${line.min} à ${line.max}`);
  const row = (line, cls) => h('div', { class: 'tip-line base ' + cls }, h('span', { class: 'v' }, range(line)), h('span', { class: 'n with-ico' }, statIcon(line.asset), line.name), h('span', { class: 'r' }, ''));
  const stats = [...d.fixed.map((line) => row(line, 'fixed')), ...d.lines.map((line) => row(line, line.max <= 0 ? 'malus' : ''))];
  const price = (label, value, note) => (value === null || value === undefined ? null
    : h('div', { class: 'tip-price' }, h('span', { class: 'muted' }, label), h('b', {}, fmt(value)), h('span', { class: 'muted small' }, note || '')));
  const prices = [
    price('Prix moyen', d.avg),
    d.hdv && price(d.equipment ? 'HDV jet de base' : 'HDV', d.hdv.price, [lotLabel(d.hdv.lot), ago(d.hdv.ts, d.now)].filter(Boolean).join(' · ')),
    d.hdv_any && (!d.hdv || d.hdv_any.price < d.hdv.price) && price('HDV le moins cher', d.hdv_any.price, 'exo ou over compris'),
    d.ref && d.ref.source === 'prix estimé' && price('Prix estimé', d.ref.price),
    price('Coût de craft', d.craft_cost),
  ].filter(Boolean);
  return [
    h('div', { class: 'tip-head' }, tile(d.icon, false, d.id), h('div', {}, h('div', { class: 'tip-name' }, d.name), h('div', { class: 'muted small' }, [d.type, `niv. ${d.level}`].filter(Boolean).join(' · ')))),
    d.equipment && h('div', { class: 'tip-lines' }, stats.length ? stats : h('div', { class: 'muted' }, d.template_known ? 'Aucune caractéristique.' : 'Caractéristiques de base pas encore récupérées.')),
    prices.length ? h('div', { class: 'tip-prices' }, prices) : null,
  ];
}

function kpi(label, value, hint, cls) {
  return h('div', { class: 'kpi' }, h('div', { class: 'label' }, label), h('div', { class: 'value ' + (cls || '') }, value), hint && h('div', { class: 'hint' }, hint));
}

function segmented(label, options, current, onPick) {
  return h('div', { class: 'seg', role: 'group', 'aria-label': label },
    options.map(([value, text]) => h('button', { 'aria-pressed': String(value === current), onclick: () => onPick(value) }, text)));
}

/** « x100 » quand le prix HDV vient d'un lot de plus d'un objet, sinon rien. */
const lotLabel = (lot) => (lot > 1 ? `x${lot}` : '');

/** Infobulle du prix d'un lot : le prix unitaire affiché n'est pas ce qu'on paie en une fois. */
const lotTitle = (lot, unitPrice) =>
  lot > 1 && unitPrice !== null && unitPrice !== undefined ? `Prix HDV d'un lot de ${lot} : ${fmt(unitPrice * lot)} kamas, ramené à l'unité` : null;

/** En-tête de colonne cliquable : un clic trie dans le sens naturel de la colonne, un second l'inverse. */
function sortTh(sort, key, label, { left = false, title = null, first = -1, onchange = refresh } = {}) {
  const active = sort.key === key;
  return h('th', { class: (left ? 'l ' : '') + (active ? 'sorted' : ''), title, 'aria-sort': active ? (sort.dir > 0 ? 'ascending' : 'descending') : null },
    h('button', { class: 'th-sort', onclick: () => { if (active) sort.dir = -sort.dir; else { sort.key = key; sort.dir = first; } onchange(); } },
      label, active ? (sort.dir > 0 ? ' ↑' : ' ↓') : ''));
}

/** Copie triée selon l'en-tête choisi. Les lignes sans valeur restent en bas, quel que soit le sens. */
function sortedBy(rows, sort, getters) {
  const get = getters[sort.key];
  if (!get) return rows;
  return rows.slice().sort((a, b) => {
    const x = get(a), y = get(b);
    const noX = x === null || x === undefined, noY = y === null || y === undefined;
    if (noX || noY) return noX - noY;
    return (typeof x === 'string' ? x.localeCompare(y, 'fr') : x - y) * sort.dir;
  });
}

/** Libellé court de la source d'un prix, avec la taille du lot HDV d'où vient le prix unitaire. */
function shortSource(source, lot) {
  if (!source) return 'inconnu';
  if (source.startsWith('HDV')) return [source.includes('sans exo') ? 'HDV de base' : source.includes('compris') ? 'HDV exo compris' : 'HDV', lotLabel(lot)].filter(Boolean).join(' ');
  if (source.startsWith('dernier')) return 'dernière vente';
  if (source.startsWith('prix médian')) return 'médiane 24 h';
  if (source === 'prix estimé') return 'estimé';
  return 'prix moyen';
}

/** Un prix observé (HDV, vente) est plus fiable que le prix moyen du jeu. */
const observed = (source) => !!source && source !== 'prix moyen' && source !== 'prix estimé';
const dotClass = (source) => (observed(source) ? 'on' : source === 'prix estimé' ? 'est' : '');
const pct = (ratio) => `${Math.round(ratio * 100)} %`;
const sourceLine = (source, ageHours, lot, unitPrice) =>
  h('div', { class: 'source', title: lotTitle(lot, unitPrice) }, h('span', { class: 'dot ' + dotClass(source) }), `${shortSource(source, lot)} · ${ageLabel(ageHours)}`);
function ageLabel(hours) {
  if (hours === null || hours === undefined) return '';
  if (hours < 1.5) return `${Math.max(1, Math.round(hours * 60))} min`;
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} j`;
}

// ---------------------------------------------------------------- état et navigation

const S = { cache: {}, ui: { crafts: null, forge: {}, ranking: { criterion: 'exo', exo: null, effect: null, amount: 1, start: 'base', type: '' } }, status: null, stamp: null };

// Page, libellé, tracé de l'icône, groupe du menu (« pied » : les trois liens discrets du bas).
const NAV = [
  ['today', 'Aujourd\'hui', 'M9 5.500a3.500 3.500 0 100 7 3.500 3.500 0 000-7zM9 1.500v1.500M9 15v1.500M1.500 9H3M15 9h1.500M3.700 3.700l1 1M13.300 13.300l1 1M3.700 14.300l1-1M13.300 4.700l1-1', null],
  ['crafts', 'Crafts', 'M3 15l7-7M11 3l4 4-3 3-4-4z', 'Gagner des kamas'],
  ['forge', 'Forgemagie', 'M9 2l2 4.5 5 .6-3.7 3.3 1 4.9L9 12.8 4.7 15.3l1-4.9L2 7.1l5-.6z', 'Gagner des kamas'],
  ['trends', 'Tendances', 'M2 13l4.5-5 3 3L16 4M12 4h4v4', 'Gagner des kamas'],
  ['item', 'Fiche objet', 'M5 2.5h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2v-9a2 2 0 012-2zM6 6.5h6M6 9.5h6M6 12.5h3', 'Gagner des kamas'],
  ['stock', 'Mon stock', 'M2.5 6.5L9 3l6.5 3.5v6L9 16l-6.5-3.5zM2.5 6.5L9 10l6.5-3.5M9 10v6', 'Mon compte'],
  ['sales', 'Mes ventes', 'M2.5 5.5h13l-1.2 8a1.5 1.5 0 01-1.500 1.300H5.2a1.5 1.5 0 01-1.500-1.300zM6 5.5V4.500a3 3 0 016 0v1', 'Mon compte'],
  ['jobs', 'Métiers', 'M3 15.5h12M5 15.5V8.5l4-5.5 4 5.5v7M7.5 15.5v-3.500h3v3.500', 'Outils'],
  ['fight', 'Combat', 'M9 2.5l6.5 6.5L9 15.5 2.5 9zM9 6.5v5M6.5 9h5', 'Outils'],
  ['ignored', 'Ignorés', 'M3 3l12 12M7.4 7.5a2.2 2.2 0 003.1 3.1M5 5.3C3.4 6.4 2.3 7.9 1.8 9c1 2.3 3.7 5 7.2 5 1.1 0 2.1-.3 3-.7M8 4.1c.3 0 .7-.1 1-.1 3.5 0 6.200 2.700 7.200 5-.3.7-.8 1.500-1.500 2.300', 'Outils'],
  ['status', 'État', 'M9 15.5a6.5 6.5 0 100-13 6.5 6.5 0 000 13zM9 5.5V9l2.5 1.5', 'pied'],
  ['welcome', 'Aide', 'M9 15.5a6.5 6.5 0 100-13 6.5 6.5 0 000 13zM7.2 7.200a1.900 1.900 0 113 1.500c-.700.500-1.200.900-1.200 1.800M9 12.700v.100', 'pied'],
  ['config', 'Config', 'M3 5h4M11 5h4M3 9h8M15 9h0M3 13h2M9 13h6M9 3.5v3M13 7.5v3M7 11.5v3', 'pied'],
];

function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  return { page: parts[0] || 'today', sub: parts[1], id: parts[2] ? Number(parts[2]) : (parts[1] && /^\d+$/.test(parts[1]) ? Number(parts[1]) : null) };
}

function renderNav() {
  const current = route().page;
  const nav = document.getElementById('nav');
  const link = ([page, label, path]) => h('a', { href: `#/${page}`, 'aria-current': page === current ? 'page' : null },
    svg('svg', { width: 18, height: 18, viewBox: '0 0 18 18', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' }, svg('path', { d: path })),
    label);
  const nodes = [];
  let group = null;
  for (const entry of NAV.filter((e) => e[3] !== 'pied')) {
    if (entry[3] && entry[3] !== group) nodes.push(h('div', { class: 'nav-group' }, entry[3]));
    group = entry[3];
    nodes.push(link(entry));
  }
  nodes.push(h('div', { class: 'nav-foot' }, NAV.filter((e) => e[3] === 'pied').map(link)));
  nav.replaceChildren(...nodes);
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
  const pages = { today: pageToday, crafts: pageCrafts, stock: pageStock, sales: pageSales, ignored: pageIgnored, jobs: pageJobs, forge: pageForge, trends: pageTrends, fight: pageFight, item: pageItem, status: pageStatus, config: pageConfig, welcome: pageWelcome };
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
    markSeen();
    const { stamp } = await api('/api/version');
    const text = JSON.stringify(stamp);
    if (S.stamp !== null && text !== S.stamp) { S.cache = {}; S.status = await api('/api/status'); renderCapture(); refresh(); }
    S.stamp = text;
  } catch (error) { /* serveur momentanément injoignable : on réessaiera */ }
}

window.addEventListener('hashchange', () => { $tip.hidden = true; window.scrollTo(0, 0); render(); });

// ---------------------------------------------------------------- page Aujourd'hui

/** Début de la période « depuis ta dernière visite » : la fin de la visite précédente, si elle date de plus d'une demi-heure. */
function visitSince() {
  const now = Date.now() / 1000;
  try {
    const seen = Number(localStorage.getItem('dofustool.seen')) || 0;
    let since = Number(localStorage.getItem('dofustool.since')) || 0;
    if (!seen || !since) since = now - 86400;
    else if (now - seen > 1800) since = seen;
    localStorage.setItem('dofustool.since', String(since));
    localStorage.setItem('dofustool.seen', String(now));
    return since;
  } catch (error) { return now - 86400; }
}
const markSeen = () => { try { localStorage.setItem('dofustool.seen', String(Date.now() / 1000)); } catch (error) { /* stockage indisponible */ } };

const dayLabel = (iso, today) => {
  const diff = Math.round((new Date(iso) - new Date(today)) / 86400000);
  if (diff === 0) return "Aujourd'hui";
  if (diff === 1) return 'Demain';
  if (diff === -1) return 'Hier';
  return new Date(`${iso}T12:00:00`).toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' });
};
const listRow = (attrs, ...children) => h(attrs.href ? 'a' : 'div', { class: 'list-row' + (attrs.href ? ' link' : ''), ...attrs }, children);
const plural = (n, one, many) => `${fmt(n)} ${n > 1 ? many : one}`;

async function almanaxPanel() {
  const ui = S.ui.today || (S.ui.today = { day: null });
  const d = await cached(`almanax-${ui.day || 'today'}`, `/api/almanax${ui.day ? `?day=${ui.day}` : ''}`);
  const go = (day) => { ui.day = day === d.today ? null : day; refresh(); };
  const head = h('div', { class: 'panel-head' }, h('h2', {}, 'Almanax'),
    h('div', { class: 'day-nav' },
      h('button', { class: 'btn quiet', 'aria-label': 'Jour précédent', title: 'Jour précédent', onclick: () => go(d.previous) }, '‹'),
      h('span', { class: 'day-label' }, dayLabel(d.day, d.today)),
      h('button', { class: 'btn quiet', 'aria-label': 'Jour suivant', title: 'Jour suivant', onclick: () => go(d.next) }, '›'),
      d.day !== d.today && h('button', { class: 'btn', onclick: () => go(d.today) }, "Aujourd'hui")));
  if (!d.available) {
    return h('section', { class: 'panel', 'aria-label': 'Almanax' }, head, h('div', { class: 'empty' }, 'Almanax indisponible : DofusDB ne répond pas. ',
      h('button', { class: 'btn', onclick: () => { delete S.cache[`almanax-${ui.day || 'today'}`]; refresh(); } }, 'Réessayer')));
  }
  return h('section', { class: 'panel', 'aria-label': 'Almanax' }, head,
    h('div', { class: 'list-row', style: 'display: block' }, h('div', { style: 'font-weight: 600; font-size: 16px' }, d.name || 'Bonus du jour'), h('div', { class: 'muted', style: 'margin-top: 2px' }, d.desc)),
    d.items.map((it) => listRow({ href: `#/item/${it.item_id}` },
      itemCell(it.icon, it.name, `offrande · × ${fmt(it.quantity)}` + (it.unit !== null ? ` · ${unitLabel(it.unit)} l'unité` : ''), it.item_id),
      h('div', { class: 'end' }, h('div', { class: 'strong' }, it.cost === null ? h('span', { class: 'muted' }, 'prix inconnu') : fmt(it.cost)),
        it.owned !== null && h('div', {}, haveTag(it.owned, it.quantity))))));
}

async function pageToday() {
  if (!S.since) S.since = visitSince();
  const [d, almanax] = await Promise.all([cached('today', `/api/today?since=${Math.floor(S.since)}`), almanaxPanel()]);
  const head = h('header', { class: 'head' }, h('div', {}, h('h1', {}, "Aujourd'hui"),
    h('div', { class: 'lead' }, new Date().toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' }) + ` · dernière visite ${ago(d.since, d.now)}`)));

  // À faire maintenant : seulement ce qui demande un geste.
  const t = d.todo;
  const filter = (view) => () => { (S.ui.sales || (S.ui.sales = { tab: 'lots', view: 'all', q: '', kind: '', family: '' })).view = view; S.ui.sales.tab = 'lots'; };
  const todo = [];
  if (t.capture.alert) todo.push(listRow({ href: '#/status' }, h('span', { class: 'warn', style: 'font-weight: 600' }, 'Alerte de décodage'), h('span', { class: 'end muted' }, 'voir la page État')));
  else if (!t.capture.running) todo.push(listRow({ href: '#/status' }, h('span', { style: 'font-weight: 600' }, 'Capture arrêtée'), h('span', { class: 'end muted' }, 'rien de nouveau n\u2019est relevé tant qu\u2019elle ne tourne pas')));
  else if (t.capture.awaiting) todo.push(listRow({ href: '#/status' }, h('span', { style: 'font-weight: 600' }, 'Prix moyens en attente'), h('span', { class: 'end muted' }, t.capture.late ? 'capture lancée après le jeu : dans l\u2019heure' : 'ils arrivent au choix du personnage')));
  if (t.undercut.count) {
    todo.push(listRow({ href: '#/sales', onclick: filter('undercut') }, h('span', { style: 'font-weight: 600' }, plural(t.undercut.count, 'lot sous-enchéri', 'lots sous-enchéris')), h('span', { class: 'end warn', style: 'font-weight: 600' }, `${fmt(t.undercut.amount)} kamas concernés`)));
    for (const r of t.undercut.rows) todo.push(listRow({ href: `#/item/${r.item_id}`, class: 'list-row link sub' }, itemCell(r.icon, r.name, `lot de ${r.lot} à ${fmt(r.price)}`, r.item_id), h('span', { class: 'end warn' }, `−${fmt(r.undercut)}`)));
  }
  if (t.expiring.count) {
    todo.push(listRow({ href: '#/sales', onclick: filter('soon') }, h('span', { style: 'font-weight: 600' }, plural(t.expiring.count, 'lot expire sous 3 jours', 'lots expirent sous 3 jours'))));
    for (const r of t.expiring.rows) todo.push(listRow({ href: `#/item/${r.item_id}`, class: 'list-row link sub' }, itemCell(r.icon, r.name, `lot de ${r.lot} à ${fmt(r.price)}`, r.item_id), h('span', { class: 'end warn' }, remainingLabel(r.remaining_s))));
  }
  const todoPanel = h('section', { class: 'panel' + (todo.length ? ' attention' : ''), 'aria-label': 'À faire maintenant' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'À faire maintenant')),
    todo.length ? todo : h('div', { class: 'empty' }, d.quality.sales_known ? 'Rien à faire : aucun lot sous-enchéri ni proche de l\u2019expiration.' : 'Ouvre l\u2019onglet Vendre d\u2019un HDV en jeu pour suivre tes lots ici.'));

  // Depuis la dernière visite.
  const r = d.recent;
  const mini = (label, top) => (top ? h('span', { class: 'mini-item' }, label, tile(top.icon, false), h('span', {}, top.name)) : label);
  const recent = h('section', { class: 'kpis', 'aria-label': 'Depuis ta dernière visite' },
    h('a', { class: 'kpi', href: '#/sales', onclick: () => { filter('all')(); S.ui.sales.tab = 'journal'; S.ui.sales.kind = 'sale'; } }, h('div', { class: 'label' }, 'Vendu depuis ta dernière visite'), h('div', { class: 'value ' + (r.sold.amount ? 'gain' : '') }, r.sold.amount ? `+${fmt(r.sold.amount)}` : '—'),
      h('div', { class: 'hint' }, r.sold.count ? mini(`${plural(r.sold.count, 'lot', 'lots')}` + (r.sold.offline ? `, dont ${r.sold.offline} hors ligne · surtout ` : ' · surtout '), r.sold.top) : 'aucune vente')),
    h('a', { class: 'kpi', href: '#/sales', onclick: () => { filter('all')(); S.ui.sales.tab = 'journal'; S.ui.sales.kind = 'purchase'; } }, h('div', { class: 'label' }, 'Acheté'), h('div', { class: 'value' }, r.bought.amount ? `−${fmt(r.bought.amount)}` : '—'),
      h('div', { class: 'hint' }, r.bought.count ? mini(`${plural(r.bought.count, 'lot', 'lots')} · surtout `, r.bought.top) : 'aucun achat')),
    h('a', { class: 'kpi', href: '#/forge/journal' }, h('div', { class: 'label' }, 'Forgemagie'), h('div', { class: 'value' }, r.forged.passes ? plural(r.forged.passes, 'rune', 'runes') : '—'),
      h('div', { class: 'hint' }, r.forged.passes ? mini(`${fmt(r.forged.cost)} kamas sur ${plural(r.forged.objects, 'objet', 'objets')} · dernier `, r.forged.top) : 'aucune rune passée')));

  // Pistes : ce que le stock permet de fabriquer, et ce qui s'écarte de sa moyenne.
  // Le bouton « ignorer » vit dans une ligne cliquable : il ne doit pas ouvrir la fiche de l'objet.
  const ignore = (x) => { const button = ignoreButton(x.item_id, x.name); button.addEventListener('click', (event) => event.preventDefault()); return button; };
  const crafts = h('section', { class: 'panel', 'aria-label': 'Faisable avec ton stock' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Faisable avec ton stock'), h('a', { class: 'muted small', href: '#/stock' }, 'tout voir')),
    d.crafts.length ? d.crafts.map((c) => listRow({ href: `#/item/${c.item_id}` }, itemCell(c.icon, c.name, `× ${fmt(c.craftable)} avec ce que tu possèdes`, c.item_id), h('span', { class: 'end gain', style: 'font-weight: 600' }, signed(c.margin)), ignore(c)))
      : h('div', { class: 'empty' }, d.stock_known ? 'Aucune recette rentable n\u2019est faisable avec ton stock seul.' : 'Stock inconnu : connecte-toi en jeu, capture active.'));
  const signals = h('section', { class: 'panel', 'aria-label': 'Signaux du marché' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Signaux du marché'), h('a', { class: 'muted small', href: '#/trends' }, 'tout voir')),
    d.signals.length ? d.signals.map((x) => listRow({ href: `#/item/${x.item_id}` }, itemCell(x.icon, x.name, `${fmt(x.price)} kamas · ${fmt(x.sold_7d)} vendus sur 7 j`, x.item_id),
      h('span', { class: 'end' }, h('span', { class: 'tag ' + (x.deviation < 0 ? 'good' : 'bad'), title: x.deviation < 0 ? 'Sous sa moyenne : peut-être à acheter' : 'Au-dessus de sa moyenne : peut-être à vendre' }, `${x.deviation > 0 ? '+' : '−'}${Math.abs(Math.round(x.deviation))} % ${x.deviation < 0 ? 'sous' : 'au-dessus de'} sa moyenne`)), ignore(x)))
      : h('div', { class: 'empty' }, 'Aucun écart marqué parmi les objets dont les ventes sont connues.'));

  // Pour de meilleures données : repliée, ce n'est pas une urgence.
  const q = d.quality;
  const hints = [
    q.unpriced_lots > 0 && h('li', {}, `${plural(q.unpriced_lots, 'lot en vente', 'lots en vente')} sans prix HDV relevé : ouvre la fiche de ces objets à l\u2019HDV pour savoir si tu es le moins cher.`),
    q.snapshot_age_s !== null && q.snapshot_age_s > 2 * 3600 && h('li', {}, `Prix moyens relevés ${ago(d.now - q.snapshot_age_s, d.now)} : ils se rafraîchissent en jeu, capture active.`),
    !q.sales_known && h('li', {}, 'Tes lots en vente ne sont pas connus : ouvre l\u2019onglet Vendre d\u2019un HDV.'),
  ].filter(Boolean);
  const quality = hints.length ? h('details', { class: 'panel pad quiet-panel' }, h('summary', {}, `Pour de meilleures données · ${hints.length}`), h('ul', { class: 'tour-points', style: 'margin-top: 10px' }, hints)) : null;

  return [head, todoPanel, h('div', { class: 'today-grid' }, almanax, h('div', { class: 'today-stack' }, recent)), h('div', { class: 'today-grid' }, crafts, signals), quality];
}

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
        h('td', { class: 'l' }, itemCell(row.icon, row.name, [row.type, row.level ? `niv. ${row.level}` : null].filter(Boolean).join(' · '), row.item_id)),
        h('td', { class: 'muted' }, ago(row.added_at, data.now)),
        h('td', {}, h('button', { class: 'btn', onclick: (event) => { event.stopPropagation(); setIgnored(row.item_id, row.name, false); } }, 'Rétablir'))))))))];
}

// ---------------------------------------------------------------- page Crafts

const SOLD_TITLE = 'Quantité vendue, lue dans le cours du marché de l\u2019objet. Connue seulement si toi ou un ami du groupe avez ouvert ce cours en jeu.';

/** Quantités vendues sur 7 jours, et sur 30 jours en dessous. */
function soldCell(r) {
  if (r['Vendus 7 j'] === null || r['Vendus 7 j'] === undefined) return h('td', { class: 'muted small', title: SOLD_TITLE }, 'non consulté');
  return h('td', { title: SOLD_TITLE }, h('div', { class: 'soft' }, fmt(r['Vendus 7 j'])), r['Vendus 30 j'] !== null && r['Vendus 30 j'] !== undefined && h('div', { class: 'source' }, `${fmt(r['Vendus 30 j'])} sur 30 j`));
}

async function pageCrafts() {
  const data = await cached('crafts', '/api/crafts');
  const status = S.status || {};
  const ui = S.ui.crafts || (S.ui.crafts = { doable: false, q: '', job: '', min: 1, max: 200, capital: '', sold: status.min_liquidity || 0, own: false, incomplete: false, sort: { key: 'margin', dir: -1 }, limit: 100 });
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
  rows = sortedBy(rows, ui.sort, {
    name: (r) => r['Objet'], job: (r) => `${r['Métier']} ${String(r['Niveau']).padStart(3, '0')}`,
    sell: (r) => r['Prix de vente'], cost: (r) => r['Coût'], margin: (r) => r['Marge pondérée'], pct: (r) => r['Marge %'],
    sold: (r) => r['Vendus 7 j'], sold30: (r) => r['Vendus 30 j'], stock: (r) => r.craftable || null,
  });
  const th = (key, label, options = {}) => sortTh(ui.sort, key, label, { ...options, onchange: () => set({ limit: 100 }) });
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
      h('td', { class: 'l' }, itemCell(r.icon, r['Objet'], null, r.item_id)),
      h('td', { class: 'l soft' }, r['Métier'], h('span', { class: 'muted' }, ` · ${r['Niveau']}`)),
      h('td', r.equipment ? hoverListing(r.item_id, r['Source du prix'], r['Prix de vente']) : {}, h('div', { style: 'font-weight: 500' }, fmt(r['Prix de vente'])), r['Prix de vente'] !== null && sourceLine(r['Source du prix'], r['Âge du prix (h)'], r['Lot du prix'], r['Prix de vente']),
        // Équipement vendu au prix d'un jet de base : le prix moyen du jeu reste visible à côté.
        r.equipment && r.avg && observed(r['Source du prix']) && h('div', { class: 'muted small' }, `moyen ${fmt(r.avg)}`)),
      h('td', { class: 'soft' }, fmt(r['Coût'])),
      h('td', { class: 'strong ' + (r['Marge'] === null ? 'muted' : r['Marge'] >= 0 ? 'gain' : 'warn') }, signed(r['Marge'])),
      h('td', { class: 'soft' }, r['Marge %'] === null ? '—' : `${fmt(r['Marge %'])} %`),
      soldCell(r),
      h('td', {}, r.craftable > 0 ? h('span', { class: 'tag good' }, `× ${fmt(r.craftable)}`) : h('span', { class: 'muted' }, '—')),
      h('td', { class: 'l wrap' }, h('div', { class: 'tags' }, tags.map((t) => h('span', { class: 'tag ' + (t.includes('sous-craft') ? 'info' : t.includes('manquant') || t.includes('non échangeable') ? 'bad' : '') }, t)))),
      h('td', { class: 'act' }, ignoreButton(r.item_id, r['Objet'])));
  });

  return [
    h('header', { class: 'head' },
      h('div', {}, h('h1', {}, 'Crafts'), h('div', { class: 'lead' }, `${fmt(computable)} recettes · taxe ${Math.round((status.hdv_tax || 0) * 100)} % · clique un en-tête pour trier`)),
      h('div', { class: 'field' }, h('span', { class: 'label', title: 'Jet de base : annonce HDV sans exo, ni over, ni ligne perdue. Le moins cher : exo, over et transcendance admis, deux lignes perdues au plus.' }, 'Prix de vente des équipements'),
        segmented('Prix de vente des équipements', [['both', 'Jet de base, sinon prix moyen'], ['base', 'Jet de base'], ['any', 'Le moins cher, exo et over compris'], ['avg', 'Prix moyen']], status.equipment_price || 'both', async (mode) => {
          try {
            await api('/api/equipment-price', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }) });
          } catch (error) { return notify(`Non enregistré. ${error.message}`); }
          S.cache = {}; S.status = await api('/api/status'); refresh();
        }))),
    filters,
    h('section', { class: 'panel', 'aria-label': 'Classement' },
      rows.length === 0
        ? h('div', { class: 'empty' }, data.rows.length ? 'Aucune recette ne correspond à ces filtres.' : "Aucune recette : importe les données statiques, puis lance une capture.")
        : h('div', { class: 'scroll' }, h('table', { style: 'min-width: 940px' },
          h('thead', {}, h('tr', {}, th('name', 'Objet', { left: true, first: 1 }), th('job', 'Métier', { left: true, first: 1 }), th('sell', 'Prix de vente'), th('cost', 'Coût'),
            th('margin', 'Marge'), th('pct', 'Marge %'), th('sold', 'Vendus 7 j', { title: SOLD_TITLE }), th('stock', 'En stock', { title: 'Nombre faisable avec ton inventaire et ta banque' }), h('th', { class: 'l' }, 'À savoir'), h('th', {}, ''))),
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
  return `${part('inventory', 'Inventaire')} · ${bankInferred ? 'banque déduite' : part('bank', 'banque')}` + (meta.havre ? ` · ${part('havre', 'havre-sac')}` : '');
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
  const [avg, hdv, , lot] = data.prices[i.id] || [null, null, null, null];
  const unit = (v) => (v === null ? '—' : v < 100 && !Number.isInteger(v) ? v.toFixed(2).replace('.', ',') : fmt(v));
  const pct = avg && hdv !== null ? (hdv - avg) / avg * 100 : null;
  const missing = Math.max(0, i.need - i.have);
  const price = hdv !== null ? hdv : avg;
  return [
    h('div', { class: 'tip-head' }, tile(i.icon, false, i.id), h('div', {}, h('div', { class: 'tip-name' }, i.name), h('div', { class: 'muted small' }, `${fmt(i.have)} / ${fmt(i.need)}`))),
    h('div', { class: 'tip-lines' },
      h('div', { class: 'tip-price' }, h('span', { class: 'muted' }, 'Prix moyen'), h('b', {}, unit(avg))),
      h('div', { class: 'tip-price' }, h('span', { class: 'muted', title: lotTitle(lot, hdv) }, lot > 1 ? `Prix HDV (lot ${lotLabel(lot)})` : 'Prix HDV'), h('b', {}, unit(hdv)),
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
  // Ingrédients qu'il faut encore se procurer pour un craft (une ligne en quantité insuffisante compte).
  const missing = (row) => (row.craftable > 0 ? 0 : row.lines - row.covered);
  const lacking = (n) => base.filter((row) => (n === 4 ? missing(row) >= 4 : missing(row) === n));
  const close = lacking(1);
  const views = { ready, m1: close, m2: lacking(2), m3: lacking(3), m4: lacking(4), all: base };
  let rows = views[ui.view] || base;
  rows = rows.slice().sort(ui.view === 'ready'
    ? (a, b) => (b.total_margin ?? -Infinity) - (a.total_margin ?? -Infinity)
    : (a, b) => (b.margin ?? -Infinity) - (a.margin ?? -Infinity));
  const gain = ready.reduce((sum, row) => sum + Math.max(0, row.total_margin || 0), 0);

  let typing = null;
  const filters = h('section', { class: 'panel pad filters', 'aria-label': 'Filtres' },
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Recettes'),
      segmented('Recettes', [['ready', `Tout en stock · ${ready.length}`], ['m1', `1 manquant · ${views.m1.length}`], ['m2', `2 · ${views.m2.length}`], ['m3', `3 · ${views.m3.length}`], ['m4', `4 et plus · ${views.m4.length}`], ['all', `Toutes · ${base.length}`]], ui.view, (view) => set({ view, limit: 100 }))),
    h('div', { class: 'field', style: 'flex: 1 1 220px' }, h('label', { for: 's-q' }, 'Objet ou ingrédient'),
      h('input', { id: 's-q', type: 'search', value: ui.q, placeholder: 'Chercher…', autocomplete: 'off',
        oninput: (e) => { clearTimeout(typing); const value = e.target.value; typing = setTimeout(() => set({ q: value, limit: 100 }), 220); } })),
    h('div', { class: 'field', style: 'flex: 0 1 190px' }, h('label', { for: 's-job' }, 'Métier'),
      h('select', { id: 's-job', onchange: (e) => set({ job: e.target.value, limit: 100 }) },
        h('option', { value: '' }, 'Tous les métiers'), data.jobs.map((j) => h('option', { value: j, selected: j === ui.job }, j)))),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: ui.own, disabled: !data.has_jobs, onchange: (e) => set({ own: e.target.checked }) }), 'Mes métiers seulement'));

  const body = rows.slice(0, ui.limit).map((row) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${row.item_id}`; } },
    h('td', { class: 'l' }, itemCell(row.icon, row.name, `${row.job} · ${row.level}`, row.item_id)),
    h('td', hoverListing(row.item_id, row.source, row.sell), row.sell === null ? h('span', { class: 'muted' }, '—')
      : [h('div', { style: 'font-weight: 500' }, fmt(row.sell)), h('div', { class: 'source', title: lotTitle(row.lot, row.sell) }, h('span', { class: 'dot ' + dotClass(row.source) }), shortSource(row.source, row.lot))]),
    h('td', {}, row.craftable > 0 ? h('span', { class: 'tag good', style: 'font-size: 14px; font-weight: 700' }, `× ${fmt(row.craftable)}`) : h('span', { class: 'muted' }, `${row.covered} / ${row.lines} ingr.`)),
    h('td', { class: 'l wrap' }, h('div', { class: 'ingredients' }, row.ingredients.map((i) =>
      h('a', { class: 'ingredient ' + (i.have >= i.need ? 'ok' : i.have > 0 ? 'part' : 'none'), href: `#/item/${i.id}`,
        'aria-label': `${i.name}, ${fmt(i.have)} en stock pour ${fmt(i.need)} par craft, ouvrir la fiche`,
        onclick: (event) => event.stopPropagation(), // ne pas ouvrir la fiche de la recette
        onmouseenter: (event) => showTip(event, ingredientTooltip(i, data)), onmousemove: placeTip, onmouseleave: () => { $tip.hidden = true; } },
        tile(i.icon, false, i.id), h('span', { class: 'ingredient-name' }, i.name), h('span', { class: 'ingredient-qty' }, `${fmt(i.have)} / ${fmt(i.need)}`))))),
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
          h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Objet'), h('th', {}, 'Inventaire'), h('th', {}, 'Banque'), h('th', {}, 'Havre-sac'), h('th', { class: ui.sort === 'total' ? 'sorted' : '' }, 'Total'), h('th', {}, 'Prix unitaire'), h('th', { class: ui.sort === 'value' ? 'sorted' : '' }, 'Valeur'), h('th', {}, 'Recettes'))),
          h('tbody', {}, rows.slice(0, ui.limit).map((row) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${row.item_id}`; } },
            h('td', { class: 'l' }, itemCell(row.icon, row.name, [row.type, row.level ? `niv. ${row.level}` : null].filter(Boolean).join(' · '), row.item_id)),
            h('td', { class: row.inventory ? 'soft' : 'muted' }, row.inventory ? fmt(row.inventory) : '—'),
            h('td', { class: row.bank ? 'soft' : 'muted' }, row.bank ? fmt(row.bank) : '—'),
            h('td', { class: row.havre ? 'soft' : 'muted' }, row.havre ? fmt(row.havre) : '—'),
            h('td', { style: 'font-weight: 600' }, fmt(row.total)),
            h('td', {}, row.price === null ? h('span', { class: 'muted' }, '—') : [h('div', { class: 'soft' }, row.price < 100 && !Number.isInteger(row.price) ? row.price.toFixed(2).replace('.', ',') : fmt(row.price)), h('div', { class: 'source', title: lotTitle(row.lot, row.price) }, h('span', { class: 'dot ' + dotClass(row.source) }), [row.source, lotLabel(row.lot)].filter(Boolean).join(' · '))]),
            h('td', { class: 'strong ' + (row.value === null ? 'muted' : '') }, fmt(row.value)),
            h('td', { class: row.recipes ? 'soft' : 'muted' }, row.recipes || '—')))))),
      h('div', { class: 'panel-foot' }, h('span', {}, 'Objets portés exclus.'),
        h('span', {}, `${fmt(Math.min(ui.limit, rows.length))} sur ${fmt(rows.length)} `, rows.length > ui.limit && h('button', { class: 'btn', onclick: () => set({ limit: ui.limit + 300 }) }, 'Afficher plus')))),
  ];
}

// ---------------------------------------------------------------- page Mes ventes

function remainingLabel(seconds) {
  if (seconds <= 0) return 'expiré';
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  if (seconds < 2 * 86400) return `${Math.round(seconds / 3600)} h`;
  return `${Math.round(seconds / 86400)} j`;
}

async function pageSales() {
  const data = await cached('sales', '/api/sales');
  const head = h('header', { class: 'head' }, h('div', {}, h('h1', {}, 'Mes ventes'),
    h('div', { class: 'lead' }, data.latest ? `Relevé ${ago(data.latest, data.now)}` + (data.markets > 1 ? ` · ${data.markets} HDV` : '') : 'Lots que tu as mis en vente')));
  const ui = S.ui.sales || (S.ui.sales = { tab: 'lots', view: 'all', q: '', kind: '', family: '' });
  const tabs = segmented('Vue', [['lots', `Lots en vente · ${data.rows.length}`], ['journal', `Journal · ${data.trades.length}`]], ui.tab, (tab) => { ui.tab = tab; refresh(); });
  head.append(tabs);
  if (ui.tab === 'journal') return [head, ...salesJournal(data, ui)];
  if (!data.markets) {
    return [head, h('div', { class: 'panel empty' }, 'Ouvre l\'onglet Vendre d\'un HDV en jeu, capture active : tes lots apparaîtront ici.')];
  }
  const undercut = data.rows.filter((r) => r.undercut > 0);
  const unknown = data.rows.filter((r) => (r.equipment ? !r.similar : r.hdv === null));
  const soon = data.rows.filter((r) => r.remaining_s < 3 * 86400);
  const total = data.rows.reduce((sum, r) => sum + r.price, 0);
  const kpis = h('section', { class: 'kpis' },
    kpi('Lots en vente', fmt(data.rows.length), data.markets > 1 ? `${data.markets} HDV relevés` : null),
    kpi('Valeur affichée', fmt(total), 'somme des prix demandés'),
    kpi('Sous-enchéris', fmt(undercut.length), undercut.length ? `${fmt(undercut.reduce((sum, r) => sum + r.price, 0))} kamas concernés` : 'aucun parmi les prix relevés', undercut.length ? 'warn' : 'gain'),
    kpi('Expirent sous 3 jours', fmt(soon.length)));

  const query = norm(ui.q.trim());
  const pool = { all: data.rows, undercut, unknown, soon }[ui.view] || data.rows;
  const gear = data.rows.filter((r) => r.equipment).length;
  const shown = pool.filter((r) => (!ui.family || (ui.family === 'gear') === r.equipment) && (!query || norm(r.name).includes(query)));
  let typing = null;
  const filters = h('section', { class: 'panel pad filters', 'aria-label': 'Filtres' },
    segmented('Lots affichés', [['all', `Tous (${data.rows.length})`], ['undercut', `Sous-enchéris (${undercut.length})`], ['unknown', `Prix HDV non relevé (${unknown.length})`], ['soon', `Expirent bientôt (${soon.length})`]], ui.view, (view) => { ui.view = view; refresh(); }),
    segmented('Famille', [['', 'Tout'], ['gear', `Équipements (${gear})`], ['other', `Ressources et autres (${data.rows.length - gear})`]], ui.family, (family) => { ui.family = family; refresh(); }),
    h('div', { class: 'field', style: 'flex: 0 1 260px' }, h('label', { for: 's-q' }, 'Objet'),
      h('input', { id: 's-q', type: 'search', value: ui.q, placeholder: 'Chercher…', autocomplete: 'off',
        oninput: (e) => { clearTimeout(typing); const value = e.target.value; typing = setTimeout(() => { ui.q = value; refresh(); }, 200); } })));

  const state = (r) => {
    if (r.equipment) {
      if (!r.rolls) return h('span', { class: 'muted', title: 'Rouvre l\u2019onglet Vendre de cet HDV en jeu pour relever ses jets' }, 'jets non relevés');
      const x = r.similar;
      const versus = !x ? h('div', { class: 'source', title: 'Aucune annonce comparable relevée : ouvre la fiche de cet objet à l\u2019HDV en jeu' }, 'aucune annonce similaire')
        : h('div', { class: 'source', title: similarTitle(x), ...hoverRolls(r.item_id, x.listing, `sim-${r.item_id}-${r.price}`, 'Annonce similaire la moins chère') },
          x.confidence === 'grossière' ? null : x.price < r.price ? h('span', { class: 'warn', style: 'font-weight: 600' }, `−${fmt(r.price - x.price)} · `) : h('span', { class: 'gain', style: 'font-weight: 600' }, 'le moins cher · '),
          `similaire à ${fmt(x.price)} · ${x.confidence}`);
      return [h('span', { class: 'tags', style: 'justify-content: flex-end' }, typeTag(r.rolls), r.rolls.quality !== null && h('span', { class: 'muted small' }, `jets ${r.rolls.quality} %`)), versus];
    }
    if (r.hdv === null) return h('span', { class: 'muted' }, 'non relevé');
    if (r.undercut > 0) return [h('div', { class: 'warn', style: 'font-weight: 600' }, `−${fmt(r.undercut)}`), h('div', { class: 'source' }, `HDV à ${fmt(r.hdv)} · ${ago(r.hdv_ts, data.now)}`)];
    return [h('div', { class: 'gain', style: 'font-weight: 600' }, 'le moins cher'), h('div', { class: 'source' }, ago(r.hdv_ts, data.now))];
  };
  const table = h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, `${shown.length} lot${shown.length > 1 ? 's' : ''}`), h('span', { class: 'muted small' }, 'Comparés au prix le plus bas relevé pour la même taille de lot · survole un équipement pour voir ses jets')),
    shown.length === 0 ? h('div', { class: 'empty' }, 'Aucun lot ne correspond.')
      : h('div', { class: 'scroll' }, h('table', { style: 'min-width: 860px' },
        h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Objet'), h('th', {}, 'Lot'), h('th', {}, 'Mon prix'), h('th', {}, 'À l\'unité'), h('th', {}, 'Face à l\'HDV'), h('th', {}, 'Prix moyen du lot'), h('th', {}, 'Expire dans'))),
        h('tbody', {}, shown.slice(0, 400).map((r, index) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${r.item_id}`; } },
          h('td', { class: 'l', ...hoverLot(r, index) }, itemCell(r.icon, r.name, r.type)),
          h('td', { class: 'soft' }, `x${r.lot}`),
          h('td', { class: 'strong' }, fmt(r.price)),
          h('td', { class: 'soft' }, r.lot > 1 ? (r.unit < 100 && !Number.isInteger(r.unit) ? r.unit.toFixed(2).replace('.', ',') : fmt(r.unit)) : '—'),
          h('td', {}, state(r)),
          h('td', { class: 'soft' }, fmt(r.avg)),
          h('td', { class: r.remaining_s < 3 * 86400 ? 'warn' : 'soft' }, remainingLabel(r.remaining_s))))))));
  return [head, kpis, filters, table];
}

/** Journal des ventes conclues et des achats, tels que le jeu les annonce. */
function salesJournal(data, ui) {
  const t = data.totals;
  const kpis = h('section', { class: 'kpis' },
    kpi('Vendu sur 24 h', fmt(t.sale_24h[1]), `${t.sale_24h[0]} lot${t.sale_24h[0] > 1 ? 's' : ''}`, t.sale_24h[1] ? 'gain' : ''),
    kpi('Vendu sur 7 j', fmt(t.sale_7d[1]), `${t.sale_7d[0]} lot${t.sale_7d[0] > 1 ? 's' : ''}`),
    kpi('Acheté sur 24 h', fmt(t.purchase_24h[1]), `${t.purchase_24h[0]} lot${t.purchase_24h[0] > 1 ? 's' : ''}`),
    kpi('Acheté sur 7 j', fmt(t.purchase_7d[1]), `${t.purchase_7d[0]} lot${t.purchase_7d[0] > 1 ? 's' : ''}`));
  const waiting = data.offline_pending && data.offline_pending.amount > 0
    ? h('div', { class: 'banner' }, `${fmt(data.offline_pending.amount)} kamas gagnés hors ligne (annoncés le ${when(data.offline_pending.latest)}) ne sont pas encore détaillés. Ouvre l'onglet Vendre de tes HDV en jeu : les lots partis seront retrouvés.`)
    : null;
  if (!data.trades.length) {
    return [kpis, waiting, h('div', { class: 'panel empty' }, 'Aucune vente ni achat capté pour l\'instant. Ils s\'ajoutent tout seuls quand le jeu les annonce, capture active.')];
  }
  const query = norm(ui.q.trim());
  const shown = data.trades.filter((r) => (!ui.kind || r.kind === ui.kind) && (!ui.fm || r.fm) && (!query || norm(r.name).includes(query)));
  let typing = null;
  const filters = h('section', { class: 'panel pad filters', 'aria-label': 'Filtres' },
    segmented('Mouvements', [['', 'Tout'], ['sale', 'Ventes'], ['purchase', 'Achats']], ui.kind, (kind) => { ui.kind = kind; refresh(); }),
    h('label', { class: 'check', title: 'Runes achetées ou vendues, et objets que tu as forgemagés' }, h('input', { type: 'checkbox', checked: !!ui.fm, onchange: (e) => { ui.fm = e.target.checked; refresh(); } }), `Forgemagie seulement (${data.trades.filter((r) => r.fm).length})`),
    h('div', { class: 'field', style: 'flex: 0 1 260px' }, h('label', { for: 'j-q' }, 'Objet'),
      h('input', { id: 'j-q', type: 'search', value: ui.q, placeholder: 'Chercher…', autocomplete: 'off',
        oninput: (e) => { clearTimeout(typing); const value = e.target.value; typing = setTimeout(() => { ui.q = value; refresh(); }, 200); } })));
  const unit = (r) => r.price / r.quantity;
  const table = h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, `${shown.length} mouvement${shown.length > 1 ? 's' : ''}`),
      h('span', { class: 'muted small' }, `Depuis le ${when(data.first_trade, false)} · en jeu et hors ligne`)),
    shown.length === 0 ? h('div', { class: 'empty' }, 'Aucun mouvement ne correspond.')
      : h('div', { class: 'scroll' }, h('table', { style: 'min-width: 760px' },
        h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Quand'), h('th', { class: 'l' }, 'Objet'), h('th', { class: 'l' }, ''), h('th', {}, 'Lot'), h('th', {}, 'Prix du lot'), h('th', {}, 'À l\'unité'), h('th', {}, 'Prix moyen du lot'))),
        h('tbody', {}, shown.slice(0, 300).map((r) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${r.item_id}`; } },
          h('td', { class: 'l soft' }, when(r.ts)),
          h('td', { class: 'l' }, itemCell(r.icon, r.name, null, r.item_id)),
          h('td', { class: 'l' }, h('span', { class: 'tag ' + (r.kind === 'sale' ? 'good' : 'info'), title: r.offline ? 'Vente conclue pendant ton absence : déduite du lot disparu et des kamas annoncés à la connexion. Heure = celle de la connexion.' : null }, r.kind === 'sale' ? (r.offline ? 'vente hors ligne' : 'vente') : 'achat')),
          h('td', { class: 'soft' }, `x${r.quantity}`),
          h('td', { class: 'strong ' + (r.kind === 'sale' ? 'gain' : '') }, `${r.kind === 'sale' ? '+' : '−'}${fmt(r.price)}`),
          h('td', { class: 'soft' }, r.quantity > 1 ? (unit(r) < 100 && !Number.isInteger(unit(r)) ? unit(r).toFixed(2).replace('.', ',') : fmt(unit(r))) : '—'),
          h('td', { class: 'soft' }, fmt(r.avg))))))));
  return [kpis, waiting, filters, table];
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
  const mine = saved.perJob[job.id] || (saved.perJob[job.id] = { xp: job.xp ?? (job.level ? levelToXp(job.level) : 0), target: Math.min(200, (job.level || 1) + 20), exclude: [] });
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
          h('td', { class: 'l' }, itemCell(step.icon, step.name, `niv. ${step.level} · ${step.ingredients} ingr.` + (step.ratio_pct !== 100 ? ` · XP ×${step.ratio_pct / 100}` : ''), step.item_id)),
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
          h('td', { class: 'l' }, itemCell(row.icon, row.name, null, row.item_id)),
          h('td', { class: 'soft' }, fmt(row.quantity)),
          h('td', {}, !plan.stock_known ? h('span', { class: 'muted' }, '—') : haveTag(row.have, row.quantity)),
          h('td', { style: 'font-weight: 600' }, row.to_buy ? fmt(row.to_buy) : h('span', { class: 'gain' }, '0')),
          h('td', {}, row.price === null ? h('span', { class: 'warn' }, '—') : [h('div', { class: 'soft' }, row.price < 100 && !Number.isInteger(row.price) ? row.price.toFixed(2).replace('.', ',') : fmt(row.price)), h('div', { class: 'source', title: lotTitle(row.lot, row.price) }, h('span', { class: 'dot ' + dotClass(row.source) }), shortSource(row.source, row.lot))]),
          h('td', { class: 'strong' }, fmt(row.cost))))))));

  return [head, controls, excluded, blocked, kpis, steps, shopping];
}

// ---------------------------------------------------------------- page Forgemagie

async function pageForge(r) {
  const options = (await cached('forgeOptions', '/api/forge/options')).items;
  const ranking = r.sub === 'ranking';
  const journal = r.sub === 'journal';
  const tabs = h('div', { class: 'seg', role: 'tablist', 'aria-label': 'Vue' },
    h('a', { href: '#/forge', role: 'tab', 'aria-selected': String(!ranking && !journal) }, 'Par objet'),
    h('a', { href: '#/forge/ranking', role: 'tab', 'aria-selected': String(ranking) }, 'Classement général'),
    h('a', { href: '#/forge/journal', role: 'tab', 'aria-selected': String(journal) }, 'Mon journal'));
  const head = h('header', { class: 'head' },
    h('div', {}, h('h1', {}, 'Forgemagie')),
    tabs);
  if (journal) return [head, ...(await forgeJournal(r.id))];
  if (!options.length) {
    return [head, h('div', { class: 'panel empty' }, "Aucune annonce d'équipement connue. Ouvre la fiche d'achat d'un équipement à l'HDV pendant une capture.")];
  }
  if (ranking) return [head, ...(await forgeRanking())];
  const id = r.sub === 'item' && r.id ? r.id : (S.ui.forgeItem && options.some((o) => o.id === S.ui.forgeItem) ? S.ui.forgeItem : options[0].id);
  S.ui.forgeItem = id;
  return [head, ...(await forgeItem(id, options))];
}

// ---------------------------------------------------------------- journal de forgemagie

const durationLabel = (seconds) => (seconds < 90 ? `${Math.round(seconds)} s` : seconds < 5400 ? `${Math.round(seconds / 60)} min` : `${(seconds / 3600).toFixed(1).replace('.', ',')} h`);
const unitLabel = (price) => (price === null || price === undefined ? '—' : price < 100 && !Number.isInteger(price) ? price.toFixed(1).replace('.', ',') : fmt(price));
const BASE_SOURCES = { manual: 'saisi à la main', purchase: 'prix de ton achat', model: 'ton dernier achat de ce modèle', craft: 'coût de craft du jour', market: 'prix du marché' };

/** Répartition des passages : succès critiques, succès neutres, échecs. */
function outcomeCell(row) {
  const passed = row.sc + row.sn;
  return [h('div', { style: 'font-weight: 500' }, `${pct(row.count ? passed / row.count : 0)} passées`),
    h('div', { class: 'source', title: 'Succès critiques · succès neutres · échecs' }, `${row.sc} SC · ${row.sn} SN · ${row.ec} EC`)];
}

function runeTable(runes, title, note) {
  return h('section', { class: 'panel', 'aria-label': title },
    h('div', { class: 'panel-head' }, h('h2', {}, title), note && h('span', { class: 'muted small' }, note)),
    h('div', { class: 'scroll' }, h('table', { class: 'dense', style: 'min-width: 760px' },
      h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Rune'), h('th', {}, 'Passages'), h('th', {}, 'Résultat'), h('th', { title: 'Prix du marché retenu pour chaque rune passée' }, 'Prix unitaire'), h('th', {}, 'Coût'), h('th', { title: 'Prix moyen de tes achats de cette rune, pondéré par les quantités' }, "Mon prix d'achat"))),
      h('tbody', {}, runes.map((rune) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${rune.id}`; } },
        h('td', { class: 'l' }, itemCell(rune.icon, rune.name, null, rune.id)),
        h('td', { style: 'font-weight: 600' }, fmt(rune.count)),
        h('td', {}, outcomeCell(rune)),
        h('td', { class: 'soft' }, unitLabel(rune.unit)),
        h('td', { class: 'strong' }, fmt(rune.cost)),
        h('td', { class: 'soft' }, unitLabel(rune.bought))))))));
}

async function forgeJournal(uid) {
  const data = await cached('forgeJournal', '/api/forge/journal');
  if (!data.dossiers.length) {
    return [h('div', { class: 'panel empty' }, 'Aucun passage de rune capté pour l\'instant. Forgemage un objet, capture active : son dossier apparaîtra ici, avec chaque rune passée et son coût.')];
  }
  const ui = S.ui.fmJournal || (S.ui.fmJournal = { sort: { key: 'last', dir: -1 } });
  const sold = data.dossiers.filter((d) => d.status === 'sold');
  const passes = data.dossiers.reduce((sum, d) => sum + d.passes, 0);
  const runeCost = data.dossiers.reduce((sum, d) => sum + d.rune_cost, 0);
  const realized = sold.reduce((sum, d) => sum + (d.margin || 0), 0);
  const time = data.dossiers.reduce((sum, d) => sum + d.duration_s, 0);
  const kpis = h('section', { class: 'kpis' },
    kpi('Objets forgemagés', fmt(data.dossiers.length), `${sold.length} vendu${sold.length > 1 ? 's' : ''}`),
    kpi('Runes passées', fmt(passes), time ? `en ${durationLabel(time)}` : null),
    kpi('Coût des runes', fmt(runeCost), 'au prix du marché'),
    kpi('Marge réalisée', sold.length ? signed(realized) : '—', sold.length ? 'sur les objets vendus' : 'aucun objet vendu pour l\'instant', sold.length ? (realized >= 0 ? 'gain' : 'warn') : ''));

  const statusTag = (d) => (d.status === 'sold' ? h('span', { class: 'tag good' }, 'vendu')
    : d.status === 'listed' ? h('span', { class: 'tag info' }, 'en vente')
      : d.similar ? h('span', { class: 'tag ' + (d.similar.confidence === 'fiable' ? 'good' : ''), title: similarTitle(d.similar) }, `estimé · ${d.similar.confidence}`)
        : d.special ? h('span', { class: 'tag bad', title: 'Objet exo ou over sans annonce similaire relevée : c\u2019est le prix d\u2019un exemplaire ordinaire, sans doute trop bas. Ouvre sa fiche à l\u2019HDV en jeu.' }, 'estimé · peu fiable')
          : h('span', { class: 'tag', title: 'Aucune annonce similaire relevée : prix de référence du modèle, selon le réglage des équipements' }, 'estimé · prix du modèle'));
  const rows = sortedBy(data.dossiers, ui.sort, {
    name: (d) => d.name, last: (d) => d.last_ts, passes: (d) => d.passes, runes: (d) => d.rune_cost, base: (d) => d.base_cost, sale: (d) => d.sale, margin: (d) => d.margin,
  });
  const current = data.dossiers.find((d) => d.uid === uid) || null;
  const table = h('section', { class: 'panel', 'aria-label': 'Dossiers' },
    h('div', { class: 'panel-head' }, h('h2', {}, `${rows.length} objet${rows.length > 1 ? 's' : ''}`), h('span', { class: 'muted small' }, 'Clique un objet pour le détail de ses runes')),
    h('div', { class: 'scroll' }, h('table', { style: 'min-width: 940px' },
      h('thead', {}, h('tr', {}, sortTh(ui.sort, 'name', 'Objet', { left: true, first: 1 }), sortTh(ui.sort, 'last', 'Dernier passage'), sortTh(ui.sort, 'passes', 'Passages'),
        sortTh(ui.sort, 'runes', 'Runes'), sortTh(ui.sort, 'base', 'Objet de base'), sortTh(ui.sort, 'sale', 'Prix de vente'), sortTh(ui.sort, 'margin', 'Marge', { title: 'Prix de vente, moins la taxe de mise en vente, l\'objet de base et les runes' }))),
      h('tbody', {}, rows.map((d) => h('tr', { class: 'link' + (current && current.uid === d.uid ? ' on' : ''), onclick: () => { location.hash = `#/forge/journal/${d.uid}`; } },
        h('td', { class: 'l', ...(d.rolls ? hoverRolls(d.item_id, d.rolls, `fm-${d.uid}`, 'Ton exemplaire, après forgemagie') : hoverTip(d.item_id)) }, itemCell(d.icon, d.name, d.type)),
        h('td', { class: 'soft' }, [h('div', {}, when(d.last_ts)), d.duration_s > 0 && h('div', { class: 'source' }, durationLabel(d.duration_s))]),
        h('td', {}, [h('div', { style: 'font-weight: 600' }, fmt(d.passes)), h('div', { class: 'source', title: 'Succès critiques · succès neutres · échecs' }, `${d.sc} SC · ${d.sn} SN · ${d.ec} EC`)]),
        h('td', {}, [h('div', { class: 'strong' }, fmt(d.rune_cost)), d.unpriced > 0 && h('div', { class: 'source warn' }, `${d.unpriced} sans prix`)]),
        h('td', {}, d.base_cost === null ? h('span', { class: 'warn' }, '—') : [h('div', { class: 'soft' }, fmt(d.base_cost)), h('div', { class: 'source' }, BASE_SOURCES[d.base_source] || '')]),
        h('td', {}, [h('div', { class: 'soft' }, fmt(d.sale)), h('div', {}, statusTag(d))]),
        h('td', { class: 'strong ' + (d.margin === null ? '' : d.margin >= 0 ? 'gain' : 'warn') }, d.margin === null ? '—' : [signed(d.margin), d.status !== 'sold' && h('div', { class: 'source' }, d.status === 'listed' ? 'si vendu à ce prix' : 'estimée')])))))));

  const out = [kpis, table];
  if (current) out.push(...forgeDossier(current, data));
  out.push(runeTable(data.runes, 'Toutes mes runes', `${fmt(passes)} passages · ton taux de réussite réel par rune`));
  return out;
}

function forgeDossier(d, data) {
  let draft = d.base_source === 'manual' ? d.base_cost : null;
  const save = async (cost) => {
    try {
      await api('/api/forge/journal/base', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid: d.uid, cost }) });
      delete S.cache.forgeJournal;
      refresh();
    } catch (error) { notify(`Prix non enregistré : ${error.message}`); }
  };
  const real = d.real_cost !== null && d.real_passes === d.passes ? d.real_cost : null;
  const money = (label, value, hint, cls) => h('div', { class: 'kpi' }, h('div', { class: 'label' }, label), h('div', { class: 'value ' + (cls || '') }, value), hint && h('div', { class: 'hint' }, hint));
  const summary = h('section', { class: 'panel pad', 'aria-label': 'Bilan' },
    h('div', { class: 'panel-head', style: 'padding: 0 0 12px' }, h('h2', {}, d.name),
      h('span', { class: 'muted small' }, `du ${when(d.first_ts)} au ${when(d.last_ts)} · ${fmt(d.passes)} passages` + (d.duration_s ? ` en ${durationLabel(d.duration_s)}` : '') + (d.pool !== null ? ` · puits ${String(Math.round(d.pool * 10) / 10).replace('.', ',')}` : ''))),
    h('div', { class: 'kpis' },
      money('Objet de base', fmt(d.base_cost), BASE_SOURCES[d.base_source] || 'prix inconnu'),
      money('Runes', fmt(d.rune_cost), real !== null ? `${fmt(real)} à ton prix d'achat` : d.real_cost !== null ? `${d.real_passes} passages sur ${d.passes} couverts par tes achats` : 'au prix du marché'),
      money(d.status === 'sold' ? 'Vendu' : d.status === 'listed' ? 'En vente à' : 'Valeur estimée', fmt(d.sale), d.sale !== null ? `taxe de mise en vente ${fmt(d.tax)}` + (d.status === 'sold' && d.sale_ts ? ` · ${when(d.sale_ts)}` : '') : 'aucun prix de référence'),
      d.status !== 'sold' && h('div', { class: 'kpi', ...(d.similar ? hoverRolls(d.item_id, d.similar.listing, `sim-${d.uid}`, 'Annonce similaire la moins chère') : {}) },
        h('div', { class: 'label' }, 'Annonces similaires'),
        h('div', { class: 'value' }, d.similar ? fmt(d.similar.price) : '—'),
        h('div', { class: 'hint', title: d.similar ? similarTitle(d.similar) : null }, d.similar
          ? `${d.similar.confidence} · ${d.similar.count} sur ${d.similar.listings} · relevé ${ago(d.similar.captured_at, data.now)}` + (d.similar.floor ? ` · moins bons dès ${fmt(d.similar.floor)}` : '')
          : d.special ? 'aucune : exo ou over, valeur sans doute sous-estimée' : 'aucune relevée : prix du modèle')),
      money(d.status === 'sold' ? 'Marge' : 'Marge estimée', d.margin === null ? '—' : signed(d.margin), d.margin !== null && d.duration_s > 600 ? `${signed(d.margin / (d.duration_s / 3600))} par heure de forgemagie` : null, d.margin === null ? '' : d.margin >= 0 ? 'gain' : 'warn')),
    h('div', { class: 'filters', style: 'margin-top: 14px; align-items: flex-end' },
      h('div', { class: 'field', style: 'flex: 0 1 220px' }, h('label', { for: 'fm-base' }, "Prix payé pour l'objet de base"),
        h('input', { id: 'fm-base', type: 'number', min: 0, step: 1, value: draft === null ? '' : draft, placeholder: d.base_cost === null ? 'inconnu' : fmt(d.base_cost), class: draft === null ? '' : 'set',
          oninput: (e) => { draft = e.target.value === '' ? null : Math.max(0, Math.round(Number(e.target.value) || 0)); } })),
      h('button', { class: 'btn', onclick: () => save(draft) }, 'Enregistrer'),
      d.base_source === 'manual' && h('button', { class: 'btn quiet', onclick: () => save(null) }, 'Revenir au prix calculé')));

  const changed = d.lines.filter((l) => l.before === null || l.before !== l.after);
  const delta = (l) => (l.before === null ? null : l.after - l.before);
  const lines = h('section', { class: 'panel', 'aria-label': 'Jets' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Jets'), h('span', { class: 'muted small' }, d.lines.some((l) => l.before !== null) ? `${changed.length} ligne${changed.length > 1 ? 's' : ''} modifiée${changed.length > 1 ? 's' : ''}` : "état d'avant non capté")),
    h('div', { class: 'scroll' }, h('table', { class: 'dense', style: 'min-width: 420px' },
      h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Caractéristique'), h('th', {}, 'Avant'), h('th', {}, 'Après'), h('th', {}, 'Écart'))),
      h('tbody', {}, d.lines.map((l) => h('tr', {},
        h('td', { class: 'l' }, statIcon(l.asset), ' ', l.name),
        h('td', { class: 'soft' }, l.before === null ? '—' : fmt(l.before)),
        h('td', { style: 'font-weight: 600' }, fmt(l.after)),
        h('td', { class: delta(l) > 0 ? 'gain' : delta(l) < 0 ? 'warn' : 'muted' }, delta(l) === null || delta(l) === 0 ? '—' : signed(delta(l)))))))));

  return [summary, h('div', { class: 'split' }, h('div', { style: 'flex: 2 1 520px; min-width: 0' }, runeTable(d.runes, 'Runes passées sur cet objet', null)), h('div', { style: 'flex: 1 1 320px; min-width: 0' }, lines))];
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
    tile(d.icon, true, d.id),
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
    h('div', { class: 'tip-head' }, tile(d.icon, false, d.id), h('div', {}, h('div', { class: 'tip-name' }, d.name), h('div', { class: 'muted small' }, `Niv. ${d.level}${l.quality !== null ? ` · jets ${l.quality} %` : ''}`)), typeTag(l)),
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
  return h('div', { class: 'scroll' }, h('table', { class: 'dense', style: `min-width: ${420 + d.lines.length * 44}px` },
    h('thead', {}, h('tr', {}, head('price', 'Prix'), head('type', 'Type', true), head('quality', 'Jets', true), head('exo', 'Exo', true),
      d.lines.map((line) => head(line.id, d.assets[line.id] ? statIcon(d.assets[line.id]) : short(line.name), false, line.name)), withDates && head('seen', 'Vue pour la dernière fois'))),
    h('tbody', {}, listings.map((l) => h('tr', {
      class: l.mine ? 'mine' : '',
      onmouseenter: (event) => showTip(event, itemTooltip(l, d)),
      onmousemove: placeTip,
      onmouseleave: () => { $tip.hidden = true; },
    },
      h('td', { style: 'font-weight: 600' }, fmt(l.price)),
      h('td', { class: 'l' }, typeTag(l), l.mine && h('span', { class: 'tag mine' }, 'à toi')),
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
  const other = ui.start === 'base' ? 'Gain sur le craft' : 'Prime sur la base';
  const sort = ui.sort || (ui.sort = { key: 'main', dir: -1 });
  const mages = data.mages || {};
  const known = Object.values(mages).some((level) => level);
  if (ui.mage && !(ui.mage in mages)) ui.mage = '';
  // Un mage travaille les objets de son métier jusqu'à son propre niveau.
  const mine = (r) => !!r.mage && (mages[r.mage] || 0) >= r['Niveau'];
  const matching = data.rows.filter((r) => r['Moins cher selon critère'] !== null);
  const rows = sortedBy(matching.filter((r) => (!ui.type || (r.type || 'Autres') === ui.type) && (!ui.mage || r.mage === ui.mage) && (!ui.mine || mine(r))), sort, {
    name: (r) => r['Objet'], base: (r) => r['Moins cher de base'], with: (r) => r['Moins cher selon critère'],
    main: (r) => r[key], craft: (r) => r['Coût de craft'], other: (r) => r[other], sold: (r) => r['Vendus 7 j'],
  });
  const saved = data.rows.filter((r) => r['Correspondent'] !== null).length;
  const top = Math.max(1, ...rows.map((r) => r[key] || 0));

  const controls = h('section', { class: 'panel pad filters', 'aria-label': 'Comparaison' },
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Amélioration comparée'),
      segmented('Amélioration comparée', [['exo', 'Un exo'], ['over', 'Un over'], ['saved', 'Mes critères par objet']], ui.criterion, (criterion) => set({ criterion }))),
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
    h('div', { class: 'field', style: 'flex: 0 1 200px' }, h('label', { for: 'rank-mage' }, 'Métier de forgemagie'),
      h('select', { id: 'rank-mage', class: ui.mage ? 'set' : '', onchange: (e) => set({ mage: e.target.value }) },
        h('option', { value: '' }, 'Tous les métiers'),
        Object.entries(mages).map(([name, level]) => h('option', { value: name, selected: name === ui.mage }, `${name} · ${matching.filter((r) => r.mage === name).length}` + (level ? ` · niv. ${level}` : ''))))),
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Point de départ'),
      segmented('Point de départ', [['base', 'Acheter de base'], ['craft', 'Fabriquer']], ui.start, (start) => set({ start }))),
    h('label', { class: 'check', title: known ? 'Objets de tes métiers de forgemagie, jusqu\u2019à ton niveau dans chacun' : 'Renseigne tes métiers dans Config (ou choisis ton personnage) pour activer ce filtre' },
      h('input', { type: 'checkbox', checked: !!ui.mine && known, disabled: !known, onchange: (e) => set({ mine: e.target.checked }) }), 'Ce que je peux forgemager'),
    ui.criterion === 'saved' && h('div', { class: 'muted small', style: 'flex: 1 1 100%' },
      'Tes critères se règlent objet par objet, dans l\u2019onglet ', h('a', { href: '#/forge', style: 'text-decoration: underline' }, 'Par objet'),
      ` : un minimum par caractéristique et un exo voulu, dans le panneau de gauche. Ils s\u2019enregistrent tout seuls. ${saved ? `${saved} objet${saved > 1 ? 's en ont' : ' en a'}.` : 'Aucun objet n\u2019en a pour l\u2019instant.'}`));

  const body = rows.map((r, index) => {
    const value = r[key];
    return h('tr', { class: 'link', onclick: () => { location.hash = `#/forge/item/${r.item_id}`; } },
      h('td', { class: 'muted' }, index + 1),
      h('td', { class: 'l' }, itemCell(r.icon, r['Objet'], `${r.type ? r.type + ' · ' : ''}niv. ${r['Niveau']}${r.mage ? ' · ' + r.mage : ''} · ${r['Annonces']} annonces` + (r['Attention'] ? ` · ${r['Attention']}` : ''), r.item_id)),
      h('td', { class: r['Moins cher de base'] === null ? 'muted' : 'soft' }, fmt(r['Moins cher de base'])),
      h('td', {}, h('div', { style: 'font-weight: 500' }, fmt(r['Moins cher selon critère'])),
        h('div', { class: 'small ' + (r['Correspondent'] === 1 ? 'warn' : 'muted'), style: r['Correspondent'] === 1 ? 'font-weight: 600' : '' }, r['Correspondent'] === 1 ? '1 seule annonce' : `${r['Correspondent']} annonces`)),
      h('td', { class: 'l' }, value === null
        ? h('span', { class: 'tag' }, ui.start === 'base' ? 'aucun exemplaire de base en vente' : 'coût de craft inconnu')
        : h('div', { class: 'bar-cell' }, h('span', { class: 'bar' }, h('span', { style: `width: ${Math.max(0, Math.round(value / top * 100))}%` })), h('span', { class: 'num', style: 'text-align: right' }, signed(value)))),
      h('td', { class: 'soft' }, fmt(r['Coût de craft'])),
      h('td', { class: 'gain', style: 'font-weight: 600' }, signed(ui.start === 'base' ? r['Gain sur le craft'] : r['Prime sur la base'])),
      soldCell(r));
  });

  return [controls, h('section', { class: 'panel', 'aria-label': 'Classement' },
    rows.length === 0
      ? h('div', { class: 'empty' }, ui.criterion === 'saved' ? "Aucun critère enregistré : règle-les dans l'onglet « Par objet »."
        : ui.criterion === 'over' ? `Aucune annonce connue avec ${lineName} à ${ui.amount} ou plus au-dessus de son jet parfait, parmi les ${data.rows.length} objets qui ont cette ligne de base.`
        : matching.length ? 'Aucun objet ne correspond à ces filtres (type, métier, « ce que je peux forgemager »).' : 'Aucun objet connu ne répond à ce critère.')
      : h('div', { class: 'scroll' }, h('table', { class: 'dense', style: 'min-width: 1040px' },
        h('thead', {}, h('tr', {}, h('th', {}, '#'), sortTh(sort, 'name', 'Objet', { left: true, first: 1 }), sortTh(sort, 'base', 'De base'), sortTh(sort, 'with', label),
          sortTh(sort, 'main', ui.start === 'base' ? 'Marge sur la base' : 'Marge sur un craft', { left: true }), sortTh(sort, 'craft', 'Coût de craft'),
          sortTh(sort, 'other', ui.start === 'base' ? 'Marge sur un craft' : 'Marge sur la base'), sortTh(sort, 'sold', 'Vendus 7 j', { title: SOLD_TITLE }))),
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
      : h('div', { class: 'scroll' }, h('table', { class: 'dense', style: 'min-width: 600px' },
        h('thead', {}, h('tr', {}, sortTh(ui.sort, 'name', 'Objet', { left: true, first: 1 }), sortTh(ui.sort, 'price', 'Prix'), sortTh(ui.sort, 'ref', 'Comparé à'),
          sortTh(ui.sort, 'kamas', 'Écart en kamas'), sortTh(ui.sort, 'gap', 'Écart'), sortTh(ui.sort, 'recipes', 'Recettes'), h('th', {}, ''))),
        h('tbody', {}, rows.slice(0, 150).map((r) => h('tr', { class: 'link', onclick: () => { location.hash = `#/item/${r.item_id}`; } },
          h('td', { class: 'l' }, itemCell(r.icon, r['Objet'], [r.type, r['Base']].filter(Boolean).join(' · '), r.item_id)),
          h('td', { style: 'font-weight: 500' }, price(r['Prix'])),
          h('td', {}, h('div', { class: 'soft' }, fmt(reference(r)[0])), h('div', { class: 'muted small' }, reference(r)[1])),
          h('td', { class: 'soft' }, `${positive ? '+' : '−'}${price(kamasGap(r))}`),
          h('td', { class: 'strong ' + (positive ? 'warn' : 'gain') }, `${r['Écart %'] >= 0 ? '+' : '−'}${fmt(Math.abs(r['Écart %']))} %`),
          h('td', { class: r.recipes ? 'soft' : 'muted' }, r.recipes || '—'),
          h('td', { class: 'act' }, ignoreButton(r.item_id, r['Objet']))))))),
    rows.length > 150 ? h('div', { class: 'panel-foot' }, h('span', {}, `150 sur ${fmt(rows.length)}`)) : null);
  const ui = S.ui.trends || (S.ui.trends = { category: '', type: '', usedOnly: false, pct: Math.round((status.trend_threshold || 0.15) * 100), kamas: 0, sort: { key: 'gap', dir: -1 } });
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
  // Même tri pour les deux tableaux ; « Écart » classe par ampleur, le plus fort d'abord.
  const getters = { name: (r) => r['Objet'], price: (r) => r['Prix'], ref: refValue, kamas: kamasGap, gap: (r) => Math.abs(r['Écart %']), recipes: (r) => r.recipes || 0 };
  const under = sortedBy(kept.filter((r) => r['Écart %'] < 0), ui.sort, getters);
  const over = sortedBy(kept.filter((r) => r['Écart %'] > 0), ui.sort, getters);
  return [head, filters, h('div', { class: 'grid2' }, table('Sous-cotés, à acheter', under, false), table('Sur-cotés, à vendre', over, true))];
}

// ---------------------------------------------------------------- graphiques

/**
 * Courbe ou barres à une seule série, avec survol. points : [[ts, valeur, marque facultative]].
 * La marque est une classe CSS : « rebuilt » pour un point reconstitué, « rebuilt unsure » s'il est incertain.
 */
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
    root.append(svg('g', { class: 'bars' }, points.map((p) => svg('rect', { class: p[2] || '', x: X(p[0]) - w / 2, y: Y(p[1]), width: w, height: Math.max(0, Y(lo) - Y(p[1])), rx: 2 }))));
  } else {
    const path = points.map((p, i) => `${i ? 'L' : 'M'}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join(' ');
    root.append(svg('path', { class: 'area', d: `${path} L${X(x1)} ${Y(lo)} L${X(x0)} ${Y(lo)} Z` }), svg('path', { class: 'series', d: path }));
    root.append(...points.filter((p) => p[2]).map((p) => svg('circle', { class: 'mark ' + p[2], r: 3.5, cx: X(p[0]), cy: Y(p[1]) })));
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
      tile(it[3], false, it[0]), h('span', {}, it[1], h('span', { class: 'muted' }, ` · niv. ${it[2]}${it[4] ? ' · ' + it[4] : ''}`))))));
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

  const header = h('section', { class: 'panel item-head' }, tile(d.icon, true, d.id),
    h('div', { class: 'info' }, h('div', { class: 'title' }, d.name), h('div', { class: 'muted' }, `${d.type || 'Objet'} · niveau ${d.level} · ${d.exchangeable ? 'échangeable' : 'non échangeable'}`),
      d.owned.known && h('div', { class: 'tags', style: 'margin-top: 4px' },
        d.owned.inventory + d.owned.bank + (d.owned.havre || 0) > 0
          ? [h('span', { class: 'tag good' }, `Tu en as ${fmt(d.owned.inventory + d.owned.bank + (d.owned.havre || 0))}`), h('span', { class: 'tag' }, `inventaire ${fmt(d.owned.inventory)}`), h('span', { class: 'tag' }, `banque ${fmt(d.owned.bank)}`), d.owned.havre > 0 && h('span', { class: 'tag' }, `havre-sac ${fmt(d.owned.havre)}`)]
          : h('span', { class: 'tag' }, "Tu n'en as pas"))),
    d.equipment && d.hdv && h('a', { class: 'btn', href: `#/forge/item/${id}`, style: 'display: inline-flex; align-items: center' }, 'Voir en forgemagie'),
    h('button', { class: 'btn ' + (d.ignored ? '' : 'quiet'), onclick: () => setIgnored(id, d.name, !d.ignored) }, d.ignored ? 'Ne plus ignorer' : 'Ignorer cet objet'));

  const ignoredNote = d.ignored ? h('div', { class: 'note' }, 'Cet objet est ignoré : il n\'apparaît plus dans Crafts, Tendances et les recettes de Mon stock.')
    : d.type_ignored ? h('div', { class: 'note' }, `Le type « ${d.type} » est ignoré : cet objet n'apparaît plus dans Crafts, Tendances et les recettes de Mon stock. `, h('a', { href: '#/ignored', style: 'text-decoration: underline' }, 'Gérer les types ignorés')) : null;
  const kpis = h('section', { class: 'kpis' },
    kpi('Prix de référence', fmt(d.ref && d.ref.price), d.ref ? h('span', { class: 'source', title: lotTitle(d.ref.lot, d.ref.price) }, h('span', { class: 'dot ' + dotClass(d.ref.source) }), `${shortSource(d.ref.source, d.ref.lot)}${d.ref.spread ? ` ± ${pct(d.ref.spread)}` : ''} · ${ago(d.ref.ts, now)}`) : 'Aucun prix connu'),
    d.estimate && kpi('Prix estimé', fmt(d.estimate.price),
      h('span', { class: 'source', title: `Prix moyen ${fmt(d.estimate.avg)}, ${d.estimate.delta > 0 ? '+' : '−'}${fmt(Math.abs(d.estimate.delta))} en ${Math.round(d.estimate.hours)} h de relevés : les ventes récentes se font ${d.estimate.delta > 0 ? 'au-dessus' : 'en dessous'}.` },
        h('span', { class: 'dot est' }), `${d.estimate.confidence} · ${fmt(d.estimate.price * (1 - d.estimate.spread))} à ${fmt(d.estimate.price * (1 + d.estimate.spread))}`),
      d.estimate.delta > 0 ? 'warn' : 'gain'),
    kpi('Coût le plus bas', fmt(d.unit_cost.cost), d.unit_cost.mode || 'prix manquant'),
    kpi('Vendus sur 24 h', fmt(d.qty_24h), d.market_seen_at ? `${ago(d.market_seen_at, now)}${d.rebuilt && !d.rebuilt.blind ? ' + reconstitué' : ''}` : 'cours non consulté'),
    kpi('Vendus sur 7 j', fmt(d.qty_7d)),
    d.relative && d.relative.pace !== null && !d.market_seen_at && kpi('Rythme des ventes', `×${(Math.round(d.relative.pace * 10) / 10).toString().replace('.', ',')}`, 'estimé · 1 = moyenne du mois'));

  // Écart entre le prix réellement demandé à l'HDV et le prix moyen annoncé par le jeu.
  let gap = null;
  if (d.hdv_unit !== null && d.hdv_unit !== undefined && d.avg_price) {
    const diff = d.hdv_unit - d.avg_price.price, pct = d.avg_price.price ? diff / d.avg_price.price * 100 : 0;
    const cheaper = diff < 0, flat = Math.abs(pct) < 0.5;
    const unit = (v) => (Number.isInteger(v) ? fmt(v) : v.toFixed(2).replace('.', ','));
    gap = h('section', { class: 'panel gap ' + (flat ? '' : cheaper ? 'under' : 'above'), 'aria-label': 'HDV contre prix moyen' },
      h('div', { class: 'gap-side' }, h('div', { class: 'label' }, "Prix à l'HDV"), h('div', { class: 'value', title: lotTitle(d.hdv_lot, d.hdv_unit) }, unit(d.hdv_unit)), h('div', { class: 'hint' }, [d.hdv_lot > 1 && `lot ${lotLabel(d.hdv_lot)}`, ago(d.hdv.captured_at, now)].filter(Boolean).join(' · '))),
      h('div', { class: 'gap-mid' },
        h('div', { class: 'gap-pct' }, flat ? '=' : `${cheaper ? '−' : '+'}${fmt(Math.abs(pct))} %`),
        h('div', { class: 'gap-text' }, flat ? 'égal' : `${cheaper ? '−' : '+'}${unit(Math.abs(diff))}`)),
      h('div', { class: 'gap-side right' }, h('div', { class: 'label' }, 'Prix moyen du jeu'), h('div', { class: 'value' }, fmt(d.avg_price.price)), h('div', { class: 'hint' }, ago(d.avg_price.ts, now))));
  }

  const noMarket = "Cours du marché non consulté.";
  // Cours relevé, prolongé par les points reconstitués d'après les prix moyens (creux, pointillés si incertains).
  const rb = d.rebuilt && !d.rebuilt.blind ? d.rebuilt : null;
  const hourly = d.hourly.map((p) => [p[0], p[1], '', p[2]])
    .concat(rb ? rb.hourly.map((p) => [p[0], p[1], p[3] ? 'rebuilt' : 'rebuilt unsure', p[2], p[4]]) : []);
  const rebuiltDays = new Set(rb ? rb.daily.map((p) => p[0]) : []);
  const daily = d.daily.filter((p) => !rebuiltDays.has(p[0])).map((p) => [p[0], p[1], '', p[2]])
    .concat(rb ? rb.daily.map((p) => [p[0], p[1], 'rebuilt', p[2]]) : []).sort((a, b) => a[0] - b[0]);
  const sold = (p) => `${fmt(p[3])} vendus${p[2] ? ` · reconstitué${p[2].includes('unsure') ? ', incertain' : ''}${p[4] > 1.5 ? ` sur ${Math.round(p[4])} h` : ''}` : ''}`;
  const hourNote = rb ? '24 h du relevé + reconstitué' : '24 dernières heures';
  let qtyPanel = chartPanel('Quantités vendues par heure', null, hourly.filter((p) => p[3] !== null).map((p) => [p[0], p[3], p[2], p[3], p[4]]), { bars: true, height: 220, detail: sold }, noMarket);
  const rel = !d.market_seen_at && d.relative && d.relative.points.length ? d.relative : null;
  if (rel) {
    qtyPanel = chartPanel('Ventes relatives par relevé', 'estimé · % du volume sur 30 j', rel.points.map((p) => [p[0], p[2], '', null, p[1]]),
      { bars: true, height: 220, unit: ' %', detail: (p) => `sur ${Math.round(p[4] * 10) / 10} h` }, noMarket);
  }
  const charts = h('div', { class: 'grid2' },
    chartPanel('Prix par heure', hourNote, hourly, { detail: sold }, noMarket),
    qtyPanel,
    chartPanel('Prix par jour', `${daily.length} jours`, daily, { detail: sold }, noMarket),
    chartPanel('Prix moyen au fil des relevés', `${d.snapshots.length} relevé${d.snapshots.length > 1 ? 's' : ''}`, d.snapshots, {}, "Aucun relevé."));
  const minQty = d.rebuilt && d.rebuilt.min_qty >= 1.5 ? ` Ventes de moins de ${fmt(d.rebuilt.min_qty)} invisibles.` : '';
  const coursNote = d.rebuilt && d.rebuilt.blind
    ? h('div', { class: 'note' }, `Prix moyen trop peu sensible : ventes non reconstituées depuis le relevé.${minQty}`)
    : rb ? h('div', { class: 'note' }, `Points creux : ventes reconstituées d'après les prix moyens, jusqu'au ${when(rb.processed_ts)} (pointillés : incertaines).${minQty}`)
    : rel ? h('div', { class: 'note' }, `Sans relevé du cours : valeurs relatives et estimées, d'après ${Math.round(rel.hours)} h de prix moyens.${rel.observed ? '' : " Rythme inconnu sans prix relevé à l'HDV."}`)
    : null;

  let hdv;
  if (!d.hdv) {
    hdv = h('div', { class: 'empty' }, "Fiche HDV non consultée.");
  } else if (d.hdv.kind === 'lots') {
    // Mon lot de cette taille : c'est l'annonce affichée s'il est au même prix (à égalité, on ne peut pas savoir laquelle).
    const mineFor = (row) => (d.hdv.mine || {})[row['Lot'].slice(1)];
    hdv = h('div', { class: 'scroll' }, h('table', { style: 'min-width: 360px' },
      h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Lot'), h('th', {}, 'Prix du lot'), h('th', {}, 'Prix unitaire'))),
      h('tbody', {}, d.hdv.frame.map((row) => {
        const mine = mineFor(row), own = mine !== undefined && mine <= row['Prix du lot'];
        return h('tr', { class: own ? 'mine' : '' },
          h('td', { class: 'l', style: 'font-weight: 600' }, row['Lot'], own ? h('span', { class: 'tag mine' }, 'à toi')
            : mine !== undefined && h('span', { class: 'tag', style: 'margin-left: 6px', title: 'Ton lot de cette taille est plus cher que l\u2019annonce la moins chère.' }, `le tien : ${fmt(mine)}`)),
          h('td', {}, fmt(row['Prix du lot'])), h('td', { class: 'soft' }, row['Prix unitaire'].toFixed(2).replace('.', ',')));
      }))));
  } else {
    hdv = h('div', { style: 'padding: 16px 18px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center' },
      h('span', { class: 'muted' }, `${d.hdv.frame.length} exemplaires en vente :`),
      (d.hdv.mine || {})[1] !== undefined && h('span', { class: 'tag mine' }, 'dont les tiens'),
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
          h('td', { class: 'l' }, itemCell(row.icon, row['Ingrédient'], row['Source'] ? [row['Source'], lotLabel(row['Lot']), ageLabel(row['Âge (h)'])].filter(Boolean).join(' · ') : null, row.item_id)),
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
          h('td', { class: 'l' }, itemCell(row.icon, row['Objet'], null, row.item_id)), h('td', { class: 'l soft' }, row['Métier'], h('span', { class: 'muted' }, ` · ${row['Niveau']}`)),
          h('td', { class: 'soft' }, fmt(row['Quantité utilisée'])), h('td', {}, fmt(row['Prix de vente'])),
          h('td', { class: 'strong ' + (row['Marge'] === null ? 'muted' : row['Marge'] >= 0 ? 'gain' : 'warn') }, signed(row['Marge']))))))));

  return [head, header, ignoredNote, kpis, gap, charts, coursNote, hdvPanel, recipePanel, usedPanel];
}

// ---------------------------------------------------------------- page Combat (Comte Harebourg)

const FIGHT_CELL = { w: 34, h: 17 };
const VERDICT_TEXT = {
  SAFE: 'sans danger',
  OCCUPIED: 'case occupée (effet à confirmer)',
  RISKY: "destination impossible pour toi (effet à confirmer)",
  WIPE: 'Air du Temps : toute l’équipe meurt',
};

async function pageFight() {
  const ui = S.ui.fight || (S.ui.fight = { me: null, target: null, comte: null, allies: [], rotation: 90, round: 1, mode: 'me', heat: true, life: '' });
  const d = await api('/api/fight/harebourg', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ me: ui.me, target: ui.target, comte: ui.comte, allies: ui.allies, rotation: ui.rotation, round: ui.round }),
  });
  const set = (patch) => { Object.assign(ui, patch); refresh(); };
  const same = (a, b) => !!a && !!b && a[0] === b[0] && a[1] === b[1];
  const label = (c) => `(${c[0]}, ${c[1]})`;

  function place(cell) {
    const occupied = (c) => same(c, ui.me) || same(c, ui.target) || same(c, ui.comte) || ui.allies.some((a) => same(a, c));
    const clear = { me: same(cell, ui.me) ? null : ui.me, target: same(cell, ui.target) ? null : ui.target, comte: same(cell, ui.comte) ? null : ui.comte, allies: ui.allies.filter((a) => !same(a, cell)) };
    if (ui.mode === 'erase') return set(clear);
    if (ui.mode === 'ally') return set(occupied(cell) ? clear : { allies: [...ui.allies, cell] });
    set({ ...clear, [ui.mode]: cell });
  }

  // Grille isométrique : x = (col - rang) * w/2, y = (col + rang) * h/2.
  const rows = d.layout, W = FIGHT_CELL.w, H = FIGHT_CELL.h;
  const used = [];
  rows.forEach((line, r) => [...line].forEach((k, c) => { if (k !== '-') used.push([c, r]); }));
  const px = (c, r) => [(c - r) * W / 2, (c + r) * H / 2];
  const xs = used.map(([c, r]) => px(c, r)[0]), ys = used.map(([c, r]) => px(c, r)[1]);
  const minX = Math.min(...xs) - W / 2 - 4, minY = Math.min(...ys) - H / 2 - 4;
  const width = Math.max(...xs) - minX + W / 2 + 4, height = Math.max(...ys) - minY + H / 2 + 4;
  const diamond = (c, r, inset = 0) => {
    const [x, y] = px(c, r), w = W / 2 - inset, hh = H / 2 - inset / 2;
    return `${x},${y - hh} ${x + w},${y} ${x},${y + hh} ${x - w},${y}`;
  };
  const kind = (c, r) => (rows[r] && rows[r][c]) || '-';

  const verdicts = {};
  if (d.swap_map && ui.heat) for (const [c, r, v] of d.swap_map) verdicts[`${c},${r}`] = v;
  const miTemps = new Set(d.mi_temps.map(([c, r]) => `${c},${r}`));

  const board = svg('svg', { viewBox: `${minX} ${minY} ${width} ${height}`, class: 'fight-board', role: 'img', 'aria-label': 'Salle du Comte Harebourg' });
  const cells = svg('g', {});
  for (const [c, r] of used) {
    const k = kind(c, r);
    const v = verdicts[`${c},${r}`];
    const cls = ['cell', k === '#' ? 'wall' : 'floor', v && `v-${v.toLowerCase()}`, miTemps.has(`${c},${r}`) && 'mitemps'].filter(Boolean).join(' ');
    const poly = svg('polygon', { points: diamond(c, r, 1), class: cls });
    if (k !== '#') {
      poly.addEventListener('click', () => place([c, r]));
      poly.addEventListener('mouseenter', () => hover(c, r));
    }
    cells.append(poly);
  }
  const marks = svg('g', { class: 'marks' });
  const hoverLayer = svg('g', { class: 'hover' });
  board.append(cells, marks, hoverLayer);
  board.addEventListener('mouseleave', () => hoverLayer.replaceChildren());

  const piece = (cell, cls, text) => {
    if (!cell) return;
    const [x, y] = px(...cell);
    marks.append(svg('g', { class: `piece ${cls}` }, svg('ellipse', { cx: x, cy: y - 2, rx: 9, ry: 9 }), svg('text', { x, y: y + 2, 'text-anchor': 'middle' }, text)));
  };
  if (d.shot) {
    marks.append(svg('polygon', { points: diamond(...d.shot.aim, 1), class: 'aim' + (d.shot.clickable ? '' : ' bad') }));
    const [x, y] = px(...d.shot.aim);
    marks.append(svg('text', { x, y: y + 4, 'text-anchor': 'middle', class: 'aim-label' }, 'Vise'));
  }
  if (d.swap) {
    const [x1, y1] = px(...(d.swap.mover === 'comte' ? ui.comte : ui.me)), [x2, y2] = px(...d.swap.destination);
    marks.append(svg('line', { x1, y1, x2, y2, class: `swap-line v-${d.swap.verdict.toLowerCase()}` }));
    marks.append(svg('polygon', { points: diamond(...d.swap.destination, 3), class: `swap-dest v-${d.swap.verdict.toLowerCase()}` }));
  }
  ui.allies.forEach((a) => piece(a, 'ally', 'A'));
  piece(ui.comte, 'comte', 'H');
  piece(ui.target, 'target', 'C');
  piece(ui.me, 'me', 'M');

  function hover(c, r) {
    hoverLayer.replaceChildren(svg('polygon', { points: diamond(c, r, 1), class: 'cursor' }));
    if (!d.landing) return;
    const [lc, lr, critical] = d.landing[r][c];
    hoverLayer.append(svg('polygon', { points: diamond(lc, lr, 2), class: 'landing' + (critical ? ' bad' : '') }));
    if (critical) { const [x, y] = px(lc, lr); hoverLayer.append(svg('text', { x, y: y + 4, 'text-anchor': 'middle', class: 'aim-label bad' }, '×')); }
  }

  // Panneau de commandes
  const lifeHint = () => {
    const pct = Number(ui.life);
    if (ui.life === '' || !(pct >= 0 && pct <= 100)) return null;
    const band = d.life_bands.find(([floor]) => pct >= floor) || d.life_bands[d.life_bands.length - 1];
    return h('button', { class: 'btn', onclick: () => set({ rotation: band[1] }) }, `${pct} % de PV : ${band[2]} (indicatif)`);
  };
  const controls = h('section', { class: 'panel pad fight-controls', 'aria-label': 'Réglages' },
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Placer'),
      segmented('Placer', [['me', 'Moi'], ['target', 'Cible'], ['comte', 'Comte'], ['ally', 'Allié'], ['erase', 'Effacer']], ui.mode, (mode) => set({ mode }))),
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Ma confusion'),
      h('div', { class: 'row-gap' },
        segmented('Confusion', d.rotations.filter(([v]) => v !== 0).concat(d.rotations.filter(([v]) => v === 0)), ui.rotation, (rotation) => set({ rotation })),
        h('button', { class: 'btn', title: 'Chaque ligne de dégâts au contact ajoute 90° horaire', onclick: () => set({ rotation: d.bumped }) }, '+1 coup au contact'))),
    h('div', { class: 'field', style: 'flex: 0 1 120px' }, h('label', { for: 'f-life' }, 'Mes PV (%)'),
      h('input', { id: 'f-life', type: 'number', min: 0, max: 100, value: ui.life, oninput: (e) => { ui.life = e.target.value; refresh(); } })),
    lifeHint(),
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Tour'),
      h('div', { class: 'row-gap' },
        h('button', { class: 'btn', 'aria-label': 'Tour précédent', onclick: () => set({ round: Math.max(1, ui.round - 1) }) }, '−'),
        h('b', { class: 'round' }, String(ui.round)),
        h('button', { class: 'btn', 'aria-label': 'Tour suivant', onclick: () => set({ round: ui.round + 1 }) }, '+'),
        h('span', { class: 'muted small' }, ui.round % 2 ? 'impair : frapper le Comte te déplace' : 'pair : frapper le Comte le déplace'))),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: ui.heat, onchange: (e) => set({ heat: e.target.checked }) }), "Danger de frapper le Comte depuis chaque case"));

  // Ce qu'il faut retenir
  const facts = [];
  if (!ui.me) facts.push(h('p', { class: 'muted' }, 'Place ton personnage, ta cible et le Comte sur la grille.'));
  if (d.shot) {
    facts.push(h('p', {}, h('b', {}, 'Pour toucher la cible : '), d.shot.clickable ? `clique la case jaune ${label(d.shot.aim)}.` : `il faudrait cliquer ${label(d.shot.aim)}, hors de l'arène : change de place.`));
    if (d.shot.critical_failure) facts.push(h('p', { class: 'warn' }, 'La cible est sur un obstacle : échec critique.'));
    if (d.shot.melee) facts.push(h('p', { class: 'muted' }, `Au contact : après chaque ligne de dégâts, ta confusion passe à ${d.rotations.find(([v]) => v === d.bumped)[1]}.`));
  } else if (ui.me) facts.push(h('p', { class: 'muted' }, 'Survole une case : le contour montre où tombera ton sort.'));
  if (d.swap) {
    const who = d.swap.mover === 'comte' ? `le Comte part en ${label(d.swap.destination)}` : `tu pars en ${label(d.swap.destination)}`;
    facts.push(h('p', { class: d.swap.verdict === 'WIPE' ? 'warn strong' : d.swap.verdict === 'SAFE' ? '' : 'warn' }, h('b', {}, 'Si tu frappes le Comte : '), `${who} — ${VERDICT_TEXT[d.swap.verdict]}.`));
  }
  if (d.mi_temps.length && ui.me && miTemps.has(`${ui.me[0]},${ui.me[1]}`)) facts.push(h('p', { class: 'warn strong' }, 'Tu es dans la croix Mi-temps : ne commence pas ton tour ici.'));

  const legend = h('div', { class: 'legend' },
    h('span', {}, h('i', { class: 'sw me' }), 'Moi'), h('span', {}, h('i', { class: 'sw target' }), 'Cible'), h('span', {}, h('i', { class: 'sw comte' }), 'Comte'), h('span', {}, h('i', { class: 'sw ally' }), 'Allié'),
    h('span', {}, h('i', { class: 'sw aim' }), 'Case à cliquer'), h('span', {}, h('i', { class: 'sw mitemps' }), 'Mi-temps'),
    ui.heat && h('span', {}, h('i', { class: 'sw safe' }), 'Frapper le Comte d’ici : sans danger'),
    ui.heat && h('span', {}, h('i', { class: 'sw wipe' }), 'Air du Temps si tu frappes d’ici'),
    ui.heat && h('span', {}, h('i', { class: 'sw risky' }), 'À confirmer'));

  return [
    h('header', { class: 'head' }, h('div', {}, h('h1', {}, 'Comte Harebourg'),
      h('div', { class: 'muted' }, 'Simulation à la main. Les positions et la confusion viendront de la capture du combat.'))),
    controls,
    h('section', { class: 'panel fight' }, h('div', { class: 'fight-wrap' }, board), h('div', { class: 'fight-facts' }, ...facts, legend)),
  ];
}

// ---------------------------------------------------------------- page État

/** Où en est le partage avec les amis. */
function sharePanelStatus(s) {
  const share = s.share;
  if (!share || (!share.enabled && !share.hosting)) return null;
  const state = !share.enabled ? 'Ce PC héberge le hub mais n\'y est pas inscrit lui-même'
    : share.last_error ? `Dernière tentative échouée : ${share.last_error}`
      : share.last_ok ? `Synchronisé ${ago(share.last_ok, s.now)}` : 'Pas encore synchronisé';
  return h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Partage'), h('span', { class: share.last_error ? 'warn small' : 'muted small' }, state)),
    h('table', {}, h('tbody', {},
      h('tr', {}, h('td', { class: 'l soft' }, 'Relevés envoyés'), h('td', { style: 'font-weight: 500' }, fmt(share.sent))),
      h('tr', {}, h('td', { class: 'l soft' }, 'Relevés reçus des amis'), h('td', { style: 'font-weight: 500' }, fmt(share.received))),
      share.members.map((m) => h('tr', {}, h('td', { class: 'l soft' }, m.pseudo + (m.pseudo === share.pseudo ? ' (moi)' : '')),
        h('td', {}, `vu ${ago(m.last_seen, s.now)} · ${fmt(m.pushed)} relevés`))))));
}

/** Ce que vaut le prix estimé : comparé aux annonces HDV relevées sur la même période. */
function estimatePanel(s) {
  const e = s.estimates;
  if (!e) return null;
  const row = (level) => {
    const c = e.check[level];
    return h('tr', {},
      h('td', { class: 'l', style: 'font-weight: 500' }, level),
      h('td', {}, fmt(c.points)),
      h('td', { class: c.error !== null && c.error < c.avg_error ? 'gain' : '' }, c.error === null ? '—' : pct(c.error)),
      h('td', { class: 'soft' }, c.avg_error === null ? '—' : pct(c.avg_error)),
      h('td', { class: 'soft' }, c.within_20 === null ? '—' : pct(c.within_20)),
      h('td', {}, `± ${pct(c.spread)}`, c.measured ? null : h('span', { class: 'muted' }, ' par défaut')));
  };
  return h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Prix estimé'),
      h('span', { class: 'muted small' }, `${fmt(e.count)} objets estimés, dont ${fmt(e.reliable)} fiables · ${s.use_estimated_prices ? 'utilisé comme prix de référence' : 'affiché seulement'}`)),
    e.count === 0 ? h('div', { class: 'empty' }, 'Pas encore d\'estimation : il faut au moins deux relevés de prix moyens espacés de 3 heures dans la même journée.')
      : h('div', { class: 'scroll' }, h('table', { style: 'min-width: 640px' },
        h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Confiance'), h('th', {}, 'Objets contrôlés à l\'HDV'), h('th', {}, 'Erreur médiane'), h('th', {}, 'Erreur du prix moyen'), h('th', {}, 'À ± 20 %'), h('th', {}, 'Fourchette affichée'))),
        h('tbody', {}, row('fiable'), row('indicatif')))));
}

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
    s.unknown_jobs && s.unknown_jobs.length ? h('div', { class: 'note' }, `Métiers inconnus dans la configuration : ${s.unknown_jobs.join(', ')}`) : null,
    h('section', { class: 'kpis' },
      kpi('Capture', s.running ? 'En cours' : 'Arrêtée', s.running ? ago(s.started_ts, now) : when(s.stopped_ts), s.running ? 'gain' : ''),
      kpi('Dernier relevé de prix', ago(s.last_snapshot_ts, now), when(s.last_snapshot_ts)),
      s.decode_alert ? kpi('Décodage', 'Alerte', 'voir MAINTENANCE.md', 'warn')
        : s.running && s.awaiting_prices_since ? kpi('Décodage', 'En attente', s.awaiting_prices_late ? 'capture lancée après la connexion au jeu : prix moyens dans l\u2019heure' : 'prix moyens : ils arrivent au choix du personnage')
        : kpi('Décodage', 'Normal', null, 'gain')),
    h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Données')),
      h('table', {}, h('tbody', {}, rows.map(([label, value]) => h('tr', {}, h('td', { class: 'l soft' }, label), h('td', { style: 'font-weight: 500' }, value)))))),
    sharePanelStatus(s),
    estimatePanel(s),
    h('section', { class: 'panel pad' }, h('h2', { style: 'margin-bottom: 12px' }, 'Limites à garder en tête'),
      h('ul', { class: 'list' },
        h('li', {}, 'Les prix moyens sont théoriques : lissés, en retard sur le marché, sans distinction de lot ni de forgemagie.'),
        h('li', {}, "Les annonces HDV sont des prix demandés, pas des ventes ; elles ne couvrent que les objets que tu ouvres."),
        h('li', {}, "Les marges ignorent le temps passé, le coût des runes et l'XP de métier."),
        h('li', {}, 'Une mise à jour du jeu peut casser le décodage jusqu\'à ré-identification.'))),
  ];
}

// ---------------------------------------------------------------- visite guidée (premier démarrage)

let tourOpen = false;

/** Visite guidée en surimpression : quelques réglages, puis un tour des onglets, mis en lumière un par un. */
async function startTour() {
  if (tourOpen) return;
  tourOpen = true;
  let data;
  try { data = await api('/api/config'); } catch (error) { tourOpen = false; return; }
  const v = data.values;
  const form = { server: v.server_name, pseudo: v.share_pseudo, hub: v.share_hub_url, token: v.share_token };
  const cameFrom = location.hash;
  let index = 0, poller = null, live = S.status, message = '';

  const $hole = h('div', { class: 'tour-hole', 'aria-hidden': 'true' });
  const $card = h('div', { class: 'tour-card' });
  const $tour = h('div', { class: 'tour', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Visite guidée de Prospection' }, $hole, $card);

  const field = (id, label, key, attrs = {}) => h('div', { class: 'field' }, h('label', { for: id }, label),
    h('input', { id, type: 'text', value: form[key], autocomplete: 'off', spellcheck: 'false', ...attrs, oninput: (e) => { form[key] = e.target.value.trim(); } }));
  const point = (title, text) => h('li', {}, h('strong', {}, title), text ? h('span', {}, ` ${text}`) : null);
  const check = (ok, title, detail) => h('li', { class: 'step ' + (ok ? 'done' : '') },
    h('span', { class: 'step-mark', 'aria-hidden': 'true' }, ok ? '✓' : ''), h('div', {}, h('div', { class: 'step-title' }, title), h('div', { class: 'muted small' }, detail)));
  const recent = (ts) => !!ts && live && live.now - ts < 900;
  const tab = (page, title, text) => ({ target: page, title, body: () => [h('p', {}, text)] });

  const steps = [
    { title: 'Bienvenue dans Prospection', body: () => [
      h('p', {}, 'L\'outil lit ce que le jeu reçoit du serveur et en tire des prix, des marges et l\'état de ton stock.'),
      h('ul', { class: 'tour-points' },
        point('Il écoute, c\'est tout.', 'Rien n\'est envoyé au jeu, aucun clic à ta place.'),
        point('Tes données restent chez toi.', 'Stock, ventes et personnages ne quittent pas ce PC.'),
        point('Deux minutes de réglages,', 'puis un tour des onglets.'))] },
    { title: 'Ton serveur et ton pseudo', body: () => [
      h('p', {}, 'Le serveur sert à ne pas mélanger des prix. Le pseudo est le nom que verront tes amis si vous partagez vos relevés.'),
      field('t-server', 'Serveur de jeu', 'server', { placeholder: 'Kourial' }),
      field('t-pseudo', 'Pseudo', 'pseudo')],
      check: () => (form.server ? '' : 'Indique ton serveur de jeu.') },
    { title: 'Partager avec tes amis', optional: true, body: () => [
      h('p', {}, 'À plusieurs, chacun profite des prix relevés par les autres. Si un ami t\'a donné une adresse et un jeton, colle-les ici. Sinon passe à la suite : ça se règle plus tard dans Config.'),
      field('t-hub', 'Adresse du hub', 'hub', { placeholder: 'https://…' }),
      field('t-token', 'Ton jeton', 'token', { type: 'password' })],
      check: () => (form.hub && !/^https?:\/\//.test(form.hub) ? 'L\'adresse du hub commence par https://' : form.hub && !form.token ? 'Il manque ton jeton.' : '') },
    { title: 'Lance toujours le jeu par le raccourci', live: true, body: () => [
      h('p', {}, 'Le raccourci « Dofus + Prospection » du bureau démarre l\'écoute avant le jeu : c\'est indispensable, elle doit voir la connexion dès le début.'),
      h('ul', { class: 'steps' },
        check(live && live.running, 'Écoute démarrée', live && live.running ? 'La capture tourne.' : 'Lance le raccourci du bureau.'),
        check(recent(live && live.last_connection_ts), 'Personnage connecté', recent(live && live.last_connection_ts) ? 'Le jeu est vu.' : 'Choisis ton personnage en jeu.'),
        check(recent(live && live.last_snapshot_ts), 'Prix moyens reçus', recent(live && live.last_snapshot_ts) ? `${fmt(live.priced_items)} objets.` : 'Ils arrivent quelques secondes après.')),
      h('p', { class: 'muted small' }, 'Les étapes se cochent toutes seules. Tu peux continuer sans attendre.')] },
    { title: 'Ce que tu fais en jeu nourrit l\'outil', body: () => [
      h('p', {}, 'Les prix moyens de tous les objets arrivent seuls, toutes les heures. Le reste se relève quand tu l\'affiches en jeu :'),
      h('ul', { class: 'tour-points' },
        point('La fiche d\'un objet à l\'HDV', '→ son prix réel, lot par lot.'),
        point('Ta banque et ton inventaire', '→ ton stock.'),
        point('L\'onglet Vendre d\'un HDV', '→ tes lots en vente.'),
        point('Le cours du marché d\'un objet', '→ ses ventes passées.'))] },
    tab('crafts', 'Crafts', 'Toutes les recettes, classées par marge : prix de vente, coût des ingrédients et taxe compris. Un clic ouvre la fiche de l\'objet.'),
    tab('stock', 'Mon stock', 'Ce que tu possèdes, sa valeur, et les recettes que tu peux déjà lancer avec ce que tu as.'),
    tab('sales', 'Mes ventes', 'Tes lots en vente, et ceux qui ne sont plus les moins chers de l\'HDV.'),
    { target: 'forge', hash: '#/forge', title: 'Forgemagie : un équipement à la loupe', body: () => [
      h('p', {}, 'Cherche un équipement que tu as ouvert à l\'HDV : chaque exemplaire en vente apparaît avec ses jets.'),
      h('ul', { class: 'tour-points' },
        point('Exo, over, ligne perdue :', 'chaque exemplaire est étiqueté, avec la qualité de ses jets en %.'),
        point('Tes critères, à gauche :', 'un minimum par caractéristique, un exo voulu. Seuls les exemplaires qui les respectent restent.'),
        point('En haut, le calcul :', 'coût de craft, moins cher de base, et ce que tu gagnes en l\'améliorant toi-même.'),
        point('Survole une ligne', 'pour voir l\'objet comme en jeu ; clique un en-tête pour trier par ce jet.'))] },
    { target: 'forge', hash: '#/forge/ranking', title: 'Forgemagie : le classement général', body: () => [
      h('p', {}, 'Quels équipements rapportent le plus à forgemager ? Choisis une amélioration, l\'outil compare tous ceux que tu as relevés.'),
      h('ul', { class: 'tour-points' },
        point('Un exo :', 'par exemple PA. Il compare le moins cher de base au moins cher avec cet exo.'),
        point('Un over :', 'une caractéristique poussée au-dessus de son jet parfait, du montant que tu choisis.'),
        point('Mes critères :', 'ceux que tu as enregistrés objet par objet.'),
        point('Point de départ :', 'acheter l\'objet de base, ou le fabriquer.')),
      h('p', { class: 'muted small' }, 'Les marges sont avant runes. « 1 seule annonce » signale un prix fragile : un seul vendeur le demande.')] },
    { target: 'forge', hash: '#/forge/journal', title: 'Forgemagie : ton journal', body: () => [
      h('p', {}, 'Chaque rune que tu passes en jeu est notée toute seule, capture active. Un dossier par objet travaillé.'),
      h('ul', { class: 'tour-points' },
        point('Runes :', 'combien de chaque, leur coût au prix du marché, et ton taux de réussite réel.'),
        point('Objet de base :', 'ton prix d\'achat s\'il a été capté, sinon le coût de craft. Tu peux le saisir.'),
        point('Marge :', 'calculée dès que l\'objet est en vente, définitive quand il est vendu, même hors ligne.'))] },
    tab('trends', 'Tendances', 'Les objets vendus nettement sous ou au-dessus de leur prix habituel.'),
    tab('status', 'État', 'La capture tourne-t-elle, que vaut le prix estimé, où en est le partage avec tes amis.'),
    { title: 'C\'est parti', body: () => [
      h('p', {}, 'Plus tu ouvres de fiches à l\'HDV, plus les prix sont justes. Cette visite reste disponible dans l\'onglet Aide, et tous les réglages dans Config.')] },
  ];

  const close = () => {
    clearInterval(poller);
    document.removeEventListener('keydown', onKey);
    window.removeEventListener('resize', place);
    $tour.remove();
    tourOpen = false;
  };
  const save = async (withForm) => {
    const values = { ...configDraft(v), onboarded: true };
    if (withForm) Object.assign(values, { server_name: form.server, share_pseudo: form.pseudo, share_hub_url: form.hub, share_token: form.token });
    await api('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) });
    S.cache = {}; S.ui.config = null; S.ui.welcome = null;
    try { S.status = await api('/api/status'); renderCapture(); } catch (error) { /* au prochain rafraîchissement */ }
  };
  const finish = async (withForm) => {
    try { await save(withForm); } catch (error) { message = `Non enregistré. ${error.message}`; index = withForm ? 1 : index; return draw(); }
    close();
    if (withForm) location.hash = '#/crafts'; else if (location.hash !== cameFrom) location.hash = cameFrom || '#/crafts';
    render();
  };
  const go = (delta) => {
    const step = steps[index];
    if (delta > 0 && step.check) { message = step.check(); if (message) return draw(); }
    message = '';
    index = Math.max(0, Math.min(steps.length - 1, index + delta));
    draw();
  };
  function onKey(event) {
    if (event.key === 'Escape') finish(false);
    else if (event.key === 'ArrowRight' && event.target.tagName !== 'INPUT') go(1);
    else if (event.key === 'ArrowLeft' && event.target.tagName !== 'INPUT') go(-1);
    else if (event.key === 'Enter' && event.target.tagName === 'INPUT') go(1);
  }

  /** Place le halo sur l'onglet présenté et la carte à côté ; sans cible, la carte est au centre. */
  function place() {
    const step = steps[index];
    const target = step.target && document.querySelector(`#nav a[href="#/${step.target}"]`);
    if (!target) {
      $hole.style.cssText = 'left: 50%; top: 50%; width: 0; height: 0;';
      $card.style.cssText = 'left: 50%; top: 50%; transform: translate(-50%, -50%);';
      return;
    }
    const r = target.getBoundingClientRect();
    $hole.style.cssText = `left: ${r.left - 4}px; top: ${r.top - 4}px; width: ${r.width + 8}px; height: ${r.height + 8}px;`;
    const width = Math.min(420, innerWidth - 32);
    const beside = r.right + 24 + width <= innerWidth;
    const left = beside ? r.right + 24 : Math.max(16, Math.min(innerWidth - width - 16, r.left));
    const top = beside ? Math.max(16, Math.min(innerHeight - $card.offsetHeight - 16, r.top - 24)) : Math.min(innerHeight - $card.offsetHeight - 16, r.bottom + 16);
    $card.style.cssText = `left: ${left}px; top: ${Math.max(16, top)}px; transform: none;`;
  }

  function draw() {
    const step = steps[index];
    const last = index === steps.length - 1;
    clearInterval(poller);
    if (step.live) poller = setInterval(async () => { try { live = await api('/api/status'); if (steps[index].live) draw(); } catch (error) { /* réessai */ } }, 3000);
    const hash = step.hash || (step.target && `#/${step.target}`);
    if (hash && location.hash !== hash) location.hash = hash;
    const focused = document.activeElement && document.activeElement.id;
    $card.replaceChildren(
      h('div', { class: 'tour-progress', 'aria-hidden': 'true' }, steps.map((_, i) => h('span', { class: i <= index ? 'on' : '' }))),
      h('div', { class: 'tour-count' }, `${index + 1} sur ${steps.length}` + (step.optional ? ' · facultatif' : '')),
      h('h2', { id: 'tour-title' }, step.title),
      h('div', { class: 'tour-body' }, step.body()),
      message && h('div', { class: 'warn small', role: 'alert' }, message),
      h('div', { class: 'tour-actions' },
        !last && h('button', { class: 'btn quiet', onclick: () => finish(false) }, 'Passer la visite'),
        h('span', { style: 'flex: 1' }),
        index > 0 && h('button', { class: 'btn', onclick: () => go(-1) }, 'Retour'),
        h('button', { id: 'tour-next', class: 'btn primary', onclick: () => (last ? finish(true) : go(1)) }, last ? 'Terminer' : step.optional && !form.hub ? 'Plus tard' : 'Suivant')));
    place();
    const again = focused && document.getElementById(focused);
    (again && $card.contains(again) ? again : $card.querySelector('input') || document.getElementById('tour-next')).focus();
  }

  document.body.append($tour);
  document.addEventListener('keydown', onKey);
  window.addEventListener('resize', place);
  draw();
}

// ---------------------------------------------------------------- page Aide

async function pageWelcome() {
  S.status = await api('/api/status');
  renderCapture();
  const s = S.status;
  const data = await cached('config', '/api/config');
  const ui = S.ui.welcome || (S.ui.welcome = { server: data.values.server_name, pseudo: data.values.share_pseudo, hub: data.values.share_hub_url, token: data.values.share_token });
  const first = !s.onboarded;

  const check = (ok, title, detail) => h('li', { class: 'step ' + (ok ? 'done' : '') }, h('span', { class: 'step-mark', 'aria-hidden': 'true' }, ok ? '✓' : ''), h('div', {}, h('div', { class: 'step-title' }, title), h('div', { class: 'muted small' }, detail)));
  const checklist = h('section', { class: 'panel pad' }, h('h2', { style: 'margin-bottom: 14px' }, 'Où tu en es'),
    h('ul', { class: 'steps' },
      check(s.items > 0, 'Données du jeu importées', s.items > 0 ? `${fmt(s.items)} objets, ${fmt(s.recipes)} recettes` : 'Lance : .venv\\Scripts\\python.exe -m dofustool.staticdata.update'),
      check(!!s.last_connection_ts, 'Jeu vu par la capture', s.last_connection_ts ? `Dernière connexion ${ago(s.last_connection_ts, s.now)}` : 'Lance le jeu par le raccourci « Dofus + Prospection » et choisis ton personnage.'),
      check(s.snapshots > 0, 'Prix moyens reçus', s.snapshots > 0 ? `${fmt(s.snapshots)} relevés, ${fmt(s.priced_items)} objets` : 'Ils arrivent quelques secondes après le choix du personnage, puis toutes les heures.'),
      check(s.hdv_items > 0, 'Premières annonces HDV relevées', s.hdv_items > 0 ? `${fmt(s.hdv_items)} objets` : 'Ouvre la fiche d\'un objet à l\'HDV : son prix est relevé.'),
      check(s.share.enabled && !!s.share.last_ok && !s.share.last_error, 'Partage avec tes amis', !s.share.enabled ? 'Facultatif : renseigne l\'adresse du hub et ton jeton ci-dessous.' : s.share.last_error ? s.share.last_error : s.share.last_ok ? `Synchronisé ${ago(s.share.last_ok, s.now)}` : 'Première synchronisation dans la minute.')));

  const input = (id, label, key, width, attrs = {}) => h('div', { class: 'field', style: `flex: 1 1 ${width}px` }, h('label', { for: id }, label),
    h('input', { id, type: 'text', value: ui[key], autocomplete: 'off', spellcheck: 'false', ...attrs, oninput: (e) => { ui[key] = e.target.value.trim(); } }));
  const save = async () => {
    const values = { ...configDraft(data.values), server_name: ui.server, share_pseudo: ui.pseudo, share_hub_url: ui.hub, share_token: ui.token, onboarded: true };
    try {
      await api('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) });
    } catch (error) { return notify(`Non enregistré. ${error.message}`); }
    S.cache = {}; S.ui.welcome = null; S.ui.config = null;
    notify('Réglages enregistrés.');
    if (first) location.hash = '#/crafts'; else refresh();
  };
  const settings = h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Tes réglages'), h('span', { class: 'muted small' }, 'Modifiables ensuite dans Config')),
    h('div', { class: 'form-body' },
      h('div', { class: 'filters' }, input('w-server', 'Ton serveur de jeu', 'server', 180, { placeholder: 'Kourial' }), input('w-pseudo', 'Ton pseudo (vu par tes amis)', 'pseudo', 180)),
      h('div', { class: 'filters' }, input('w-hub', 'Adresse du hub (si on t\'en a donné une)', 'hub', 300, { placeholder: 'http://100.x.y.z:8610' }), input('w-token', 'Ton jeton', 'token', 260, { type: 'password' })),
      h('div', {}, h('button', { class: 'btn primary', onclick: save }, first ? 'Enregistrer et commencer' : 'Enregistrer'))));

  const how = h('section', { class: 'panel pad' }, h('h2', { style: 'margin-bottom: 12px' }, 'Comment ça marche'),
    h('ul', { class: 'list' },
      h('li', {}, 'Prospection écoute ce que le jeu reçoit du serveur. Il n\'envoie rien, ne clique pas, ne modifie pas le jeu.'),
      h('li', {}, 'Lance toujours le jeu par le raccourci « Dofus + Prospection » : c\'est lui qui démarre la capture, et elle doit commencer avant la connexion.'),
      h('li', {}, 'Les prix moyens de tous les objets arrivent seuls. Le prix réel d\'un objet est relevé quand tu ouvres sa fiche à l\'HDV.'),
      h('li', {}, 'Ta banque est lue quand tu l\'ouvres, tes ventes quand tu ouvres l\'onglet Vendre, le cours d\'un objet quand tu l\'affiches.'),
      h('li', {}, 'Avec le partage, les relevés de marché de tes amis s\'ajoutent aux tiens. Ton stock, tes ventes et tes personnages ne quittent jamais ton PC.')));

  const tabs = [
    ['crafts', 'Crafts', 'Les recettes classées par marge, coût des ingrédients et taxe compris.'],
    ['stock', 'Mon stock', 'Ce que tu possèdes, et ce que tu peux fabriquer avec.'],
    ['sales', 'Mes ventes', 'Tes lots en vente, et ceux qui ne sont plus les moins chers.'],
    ['jobs', 'Métiers', 'Le chemin le moins coûteux pour monter un métier.'],
    ['forge', 'Forgemagie', 'Les équipements en vente, leurs jets, exos et overs.'],
    ['trends', 'Tendances', 'Les objets sous-cotés ou sur-cotés par rapport à leur prix habituel.'],
    ['item', 'Fiche objet', 'Tout ce qu\'on sait d\'un objet : prix, cours, recette, annonces.'],
    ['status', 'État', 'La capture fonctionne-t-elle, que vaut le prix estimé, où en est le partage.'],
  ];
  const tour = h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Les onglets')),
    h('table', {}, h('tbody', {}, tabs.map(([page, name, text]) => h('tr', { class: 'link', onclick: () => { location.hash = `#/${page}`; } },
      h('td', { class: 'l', style: 'font-weight: 600; width: 150px' }, name), h('td', { class: 'l soft wrap' }, text))))));

  return [
    h('header', { class: 'head' }, h('div', {}, h('h1', {}, 'Aide'), h('div', { class: 'lead' }, 'Où tu en es, et à quoi sert chaque onglet.')),
      h('button', { class: 'btn primary', onclick: () => startTour() }, 'Lancer la visite guidée'),
      h('button', { class: 'btn', onclick: () => showNotes(NOTES.slice(0, 3)) }, 'Nouveautés')),
    checklist, settings, how, tour,
  ];
}

// ---------------------------------------------------------------- page Config

/** Valeurs de config.toml telles que le formulaire les manipule. */
const configDraft = (values) => ({ ...values, iface: values.iface || '', character_id: values.character_id || 0, jobs: { ...values.jobs }, share_members: { ...values.share_members } });

function notify(text) {
  clearTimeout(toastTimer);
  $toast.replaceChildren(h('span', {}, text));
  $toast.hidden = false;
  toastTimer = setTimeout(() => { $toast.hidden = true; }, 5000);
}

async function pageConfig() {
  const data = await cached('config', '/api/config');
  const ui = S.ui.config || (S.ui.config = { draft: null });
  const saved = configDraft(data.values);
  const d = ui.draft || (ui.draft = configDraft(data.values));
  const dirty = () => JSON.stringify(d) !== JSON.stringify(saved);

  const save = h('button', { class: 'btn primary', onclick: async () => {
    try {
      await api('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) });
    } catch (error) { return notify(`Non enregistré. ${error.message}`); }
    S.cache = {}; ui.draft = null;
    try { S.status = await api('/api/status'); renderCapture(); } catch (error) { /* affiché au prochain rafraîchissement */ }
    await refresh();
    notify('Configuration enregistrée.');
  } }, 'Enregistrer');
  const cancel = h('button', { class: 'btn quiet', onclick: () => { ui.draft = null; refresh(); } }, 'Annuler');
  const sync = () => { save.disabled = cancel.disabled = !dirty(); };
  sync();

  const number = (id, label, value, onValue, attrs = {}) => h('div', { class: 'field', style: 'flex: 0 1 200px' }, h('label', { for: id }, label),
    h('input', { id, type: 'number', value, ...attrs, oninput: (e) => { onValue(e.target.value === '' ? null : Number(e.target.value)); sync(); } }));
  const text = (id, label, key, width, placeholder) => h('div', { class: 'field', style: `flex: 1 1 ${width}px` }, h('label', { for: id }, label),
    h('input', { id, type: 'text', value: d[key], placeholder, autocomplete: 'off', spellcheck: 'false', oninput: (e) => { d[key] = e.target.value; sync(); } }));
  const percent = (x) => Math.round(x * 1e6) / 1e4;

  // Personnage : le choisir reprend les niveaux de métier relevés à sa dernière connexion.
  const character = data.characters.find((c) => c.id === d.character_id);
  const known = (c) => Object.keys(c.jobs).length > 0;
  const stale = character && known(character) && !dirty() && Object.entries(character.jobs).some(([name, level]) => d.jobs[name] !== level);
  const pick = (id) => {
    d.character_id = id;
    const chosen = data.characters.find((c) => c.id === id);
    if (chosen && known(chosen)) d.jobs = { ...chosen.jobs };
    refresh();
  };
  const set = Object.keys(d.jobs).length;
  const jobsPanel = h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Personnage et métiers'), h('span', { class: 'muted small' }, `${set} métier${set > 1 ? 's' : ''} renseigné${set > 1 ? 's' : ''}`)),
    h('div', { class: 'form-body' },
      h('div', { class: 'filters' },
        h('div', { class: 'field', style: 'flex: 0 1 320px' }, h('label', { for: 'c-char' }, 'Personnage'),
          h('select', { id: 'c-char', onchange: (e) => pick(Number(e.target.value)) },
            h('option', { value: 0, selected: !d.character_id }, 'Aucun'),
            d.character_id && !character ? h('option', { value: d.character_id, selected: true }, 'Personnage inconnu') : null,
            data.characters.map((c) => h('option', { value: c.id, selected: c.id === d.character_id }, `${c.name} · niv. ${c.level}${known(c) ? '' : ' · métiers non relevés'}`)))),
        character && known(character) && h('div', { class: 'muted small', style: 'padding-bottom: 12px' }, `Métiers relevés ${ago(character.jobs_at, data.now)}`)),
      data.characters.length === 0 && h('div', { class: 'muted small' }, 'Aucun personnage vu pour l\'instant : connecte-toi en jeu avec la capture active.'),
      character && !known(character) && h('div', { class: 'muted small' }, 'Métiers non relevés : connecte ce personnage avec la capture active.'),
      stale && h('div', { class: 'note', style: 'display: flex; align-items: center; justify-content: space-between; gap: 16px' },
        'Les niveaux relevés en jeu ont changé depuis l\'enregistrement.',
        h('button', { class: 'btn', onclick: () => { d.jobs = { ...d.jobs, ...character.jobs }; refresh(); } }, 'Reprendre les niveaux relevés')),
      h('div', { class: 'form-grid' }, data.jobs.map((name, index) => h('div', { class: 'field' }, h('label', { for: `c-job-${index}` }, name),
        h('input', { id: `c-job-${index}`, type: 'number', min: 1, max: 200, value: d.jobs[name] ?? '', placeholder: '—', class: d.jobs[name] ? 'set' : null,
          oninput: (e) => { if (e.target.value === '') delete d.jobs[name]; else d.jobs[name] = Number(e.target.value); sync(); } }))))));

  const market = h('section', { class: 'panel' }, h('div', { class: 'panel-head' }, h('h2', {}, 'Marché')),
    h('div', { class: 'form-body' }, h('div', { class: 'filters' },
      number('c-tax', 'Taxe HDV %', percent(d.hdv_tax), (v) => { d.hdv_tax = v === null ? null : v / 100; }, { min: 0, max: 50, step: 0.1 }),
      number('c-age', 'Dernière vente valable (h)', d.last_sale_max_age_hours, (v) => { d.last_sale_max_age_hours = v; }, { min: 1, step: 1 }),
      number('c-snap', 'Relevés pour une tendance', d.min_snapshots_for_trend, (v) => { d.min_snapshots_for_trend = v; }, { min: 1, step: 1 }),
      number('c-liq', 'Vendus sur 7 j, minimum', d.min_liquidity, (v) => { d.min_liquidity = v; }, { min: 0, step: 10 }),
      number('c-thr', 'Seuil de signal %', percent(d.trend_threshold), (v) => { d.trend_threshold = v === null ? null : v / 100; }, { min: 1, max: 500, step: 1 }),
      h('label', { class: 'check' }, h('input', { id: 'c-est', type: 'checkbox', checked: d.use_estimated_prices, onchange: (e) => { d.use_estimated_prices = e.target.checked; sync(); } }), 'Utiliser le prix estimé à défaut de relevé HDV'))));

  const system = h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Capture et lanceur'), h('span', { class: 'muted small' }, 'Pris en compte au prochain lancement')),
    h('div', { class: 'form-body' },
      h('div', { class: 'filters' },
        text('c-server', 'Serveur', 'server_name', 160),
        text('c-iface', 'Interface réseau', 'iface', 220, 'par défaut'),
        number('c-timeout', 'Recherche des clés après (s)', d.avg_prices_timeout_s, (v) => { d.avg_prices_timeout_s = v; }, { min: 5, max: 3600, step: 5 }),
        text('c-process', 'Processus du jeu', 'dofus_process', 140)),
      h('div', { class: 'filters' },
        text('c-ankama', 'Launcher Ankama', 'ankama_path', 520),
        h('label', { class: 'check' }, h('input', { id: 'c-dash', type: 'checkbox', checked: d.start_dashboard, onchange: (e) => { d.start_dashboard = e.target.checked; sync(); } }), 'Ouvrir Prospection avec le jeu'))));

  return [
    h('header', { class: 'head' }, h('div', {}, h('h1', {}, 'Config'), h('div', { class: 'lead' }, data.path)), h('div', { class: 'filters' }, cancel, save)),
    jobsPanel, market, sharePanel(d, data, sync), system,
  ];
}

/** Jeton aléatoire, fabriqué par le navigateur : 24 caractères sans ambiguïté. */
function newToken() {
  const bytes = crypto.getRandomValues(new Uint8Array(18));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_');
}

function copyText(text, label) {
  navigator.clipboard.writeText(text).then(() => notify(`${label} copié.`), () => notify('Copie impossible : sélectionne le texte à la main.'));
}

function sharePanel(d, data, sync) {
  const field = (id, label, key, width, attrs = {}) => h('div', { class: 'field', style: `flex: 1 1 ${width}px` }, h('label', { for: id }, label),
    h('input', { id, type: 'text', value: d[key], autocomplete: 'off', spellcheck: 'false', ...attrs, oninput: (e) => { d[key] = e.target.value.trim(); sync(); } }));
  const members = Object.entries(d.share_members);
  const tailscale = data.addresses.find((a) => a.tailscale);
  const address = tailscale || data.addresses[0];
  const hubUrl = address ? `http://${address.ip}:${d.share_port}` : null;
  let pseudoInput = null;
  const add = () => {
    const pseudo = pseudoInput.value.trim();
    if (!pseudo || d.share_members[pseudo]) return notify(pseudo ? 'Ce pseudo existe déjà.' : 'Donne un pseudo à cet ami.');
    d.share_members[pseudo] = newToken();
    refresh();
  };
  pseudoInput = h('input', { id: 'c-friend', type: 'text', placeholder: 'Pseudo de l\'ami', autocomplete: 'off', onkeydown: (e) => { if (e.key === 'Enter') add(); } });

  const hosting = !d.share_host ? null : h('div', { class: 'form-body', style: 'padding: 0' },
    h('div', { class: 'note' }, hubUrl
      ? [`Adresse à donner à tes amis : `, h('strong', {}, hubUrl), ' ', h('button', { class: 'btn', style: 'margin-left: 10px', onclick: () => copyText(hubUrl, 'Adresse') }, 'Copier'),
        !tailscale && h('div', { class: 'muted small', style: 'margin-top: 6px' }, 'Adresse de ton réseau local : elle ne marche que chez toi. Pour des amis à distance, installe Tailscale (voir INSTALL.md).')]
      : 'Aucune adresse réseau trouvée sur ce PC.'),
    h('div', { class: 'muted small' }, 'Un jeton par ami. Pour que ton propre PC partage aussi, ajoute-toi dans la liste et colle ton jeton plus haut, avec l\'adresse http://127.0.0.1:' + d.share_port + '.'),
    members.length > 0 && h('div', { class: 'scroll' }, h('table', { style: 'min-width: 560px' },
      h('thead', {}, h('tr', {}, h('th', { class: 'l' }, 'Ami'), h('th', { class: 'l' }, 'Jeton (secret)'), h('th', {}, ''))),
      h('tbody', {}, members.map(([pseudo, token]) => h('tr', {},
        h('td', { class: 'l', style: 'font-weight: 600' }, pseudo),
        h('td', { class: 'l soft' }, `${token.slice(0, 4)}…${token.slice(-3)}`),
        h('td', {}, h('button', { class: 'btn', onclick: () => copyText(token, `Jeton de ${pseudo}`) }, 'Copier le jeton'), ' ',
          h('button', { class: 'btn quiet', onclick: () => { delete d.share_members[pseudo]; refresh(); } }, 'Retirer'))))))),
    h('div', { class: 'filters' }, h('div', { class: 'field', style: 'flex: 0 1 260px' }, h('label', { for: 'c-friend' }, 'Ajouter un ami'), pseudoInput),
      h('button', { class: 'btn', onclick: add }, 'Créer son jeton')));

  return h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Partage avec des amis'), h('span', { class: 'muted small' }, 'Seuls les relevés de marché sont partagés')),
    h('div', { class: 'form-body' },
      h('div', { class: 'filters' },
        field('c-pseudo', 'Mon pseudo', 'share_pseudo', 160),
        field('c-hub', 'Adresse du hub', 'share_hub_url', 300, { placeholder: 'http://100.x.y.z:8610' }),
        field('c-token', 'Mon jeton', 'share_token', 260, { type: 'password', placeholder: 'reçu de celui qui héberge' })),
      h('div', { class: 'filters' },
        h('label', { class: 'check' }, h('input', { id: 'c-host', type: 'checkbox', checked: d.share_host, onchange: (e) => { d.share_host = e.target.checked; refresh(); } }), 'Ce PC héberge le hub'),
        d.share_host && h('div', { class: 'field', style: 'flex: 0 1 120px' }, h('label', { for: 'c-port' }, 'Port'),
          h('input', { id: 'c-port', type: 'number', min: 1024, max: 65535, value: d.share_port, oninput: (e) => { d.share_port = Number(e.target.value); sync(); } })),
        d.share_host && h('span', { class: 'muted small', style: 'padding-bottom: 12px' }, 'Pris en compte au prochain lancement de Prospection')),
      hosting));
}

// ---------------------------------------------------------------- recherche globale

// Pages et onglets atteignables par leur nom, avec quelques mots pour les retrouver autrement.
const DESTINATIONS = [
  ['#/today', 'Aujourd\'hui', 'accueil almanax offrande resume dernière visite'],
  ['#/crafts', 'Crafts', 'recettes marge fabriquer rentable'],
  ['#/stock', 'Mon stock › Crafts faisables', 'recettes ingredients manquants'],
  ['#/stock/items', 'Mon stock › Inventaire et banque', 'possede objets valeur'],
  ['#/sales', 'Mes ventes', 'lots hdv vendre sous-encheri'],
  ['#/jobs', 'Métiers', 'xp niveau monter paysan'],
  ['#/forge', 'Forgemagie › Par objet', 'fm exo over jets criteres annonces'],
  ['#/forge/ranking', 'Forgemagie › Classement général', 'fm exo over metier rentable'],
  ['#/forge/journal', 'Forgemagie › Mon journal', 'fm runes passages cout marge puits'],
  ['#/trends', 'Tendances', 'prix hausse baisse signal'],
  ['#/fight', 'Combat', 'harebourg'],
  ['#/item', 'Fiche objet', 'cours du marche prix historique'],
  ['#/ignored', 'Ignorés', 'masquer objets types'],
  ['#/status', 'État', 'capture partage hub alerte'],
  ['#/config', 'Config', 'reglages personnage metiers taxe partage'],
  ['#/welcome', 'Aide', 'visite guidee nouveautes tutoriel'],
];
let $palette = null;

/** Fenêtre de recherche : une page ou un objet, d'où qu'on soit. Flèches pour choisir, Entrée pour y aller. */
async function openSearch() {
  if ($palette) return;
  let items = [];
  let results = [];
  let active = 0;
  const input = h('input', { type: 'search', placeholder: 'Chercher un objet ou une page…', autocomplete: 'off', 'aria-label': 'Recherche globale' });
  const list = h('ul', { role: 'listbox' });
  const close = () => { if ($palette) { $palette.remove(); $palette = null; document.removeEventListener('keydown', onKey, true); } };
  const go = (hash) => { close(); if (location.hash === hash) render(); else location.hash = hash; };
  const icon = (page) => {
    const entry = NAV.find(([name]) => name === page);
    return h('span', { class: 'page-ico' }, entry && svg('svg', { width: 18, height: 18, viewBox: '0 0 18 18', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' }, svg('path', { d: entry[2] })));
  };
  const draw = () => {
    const q = norm(input.value.trim());
    const pages = DESTINATIONS.filter(([, label, words]) => !q || norm(label).includes(q) || words.includes(q)).slice(0, q ? 5 : DESTINATIONS.length);
    const found = [];
    if (q.length >= 2) {
      // D'abord les noms qui commencent par la recherche, puis ceux qui la contiennent.
      for (const pass of [(n) => n.startsWith(q), (n) => !n.startsWith(q) && n.includes(q)]) {
        for (const it of items) { if (found.length >= 12) break; if (pass(it[it.length - 1])) found.push(it); }
      }
    }
    results = [
      ...pages.map(([hash, label]) => ({ hash, node: [icon(hash.split('/')[1]), h('span', {}, label), h('span', { class: 'kind' }, 'page')] })),
      ...found.map((it) => ({ hash: `#/item/${it[0]}`, node: [tile(it[3], false, it[0]), h('span', {}, it[1], h('span', { class: 'muted' }, ` · niv. ${it[2]}`)), h('span', { class: 'kind' }, 'fiche objet')] })),
    ];
    active = Math.min(active, Math.max(0, results.length - 1));
    list.replaceChildren(...(results.length ? results.map((r, index) => h('li', {}, h('button', { class: index === active ? 'on' : '', role: 'option', 'aria-selected': String(index === active), onmousedown: (e) => e.preventDefault(), onclick: () => go(r.hash) }, r.node)))
      : [h('li', { class: 'muted', style: 'padding: 14px 12px' }, 'Aucun objet ni page ne correspond.')]));
    const on = list.querySelector('.on');
    if (on) on.scrollIntoView({ block: 'nearest' });
  };
  const onKey = (event) => {
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); active = (active + (event.key === 'ArrowDown' ? 1 : -1) + results.length) % Math.max(1, results.length); draw(); }
    else if (event.key === 'Enter') { event.preventDefault(); if (results[active]) go(results[active].hash); }
  };
  input.addEventListener('input', () => { active = 0; draw(); });
  $palette = h('div', { class: 'palette-veil', onclick: (event) => { if (event.target === $palette) close(); } },
    h('div', { class: 'palette', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Recherche globale' }, input, list,
      h('div', { class: 'palette-foot' }, h('span', {}, h('kbd', {}, '↑'), ' ', h('kbd', {}, '↓'), ' choisir'), h('span', {}, h('kbd', {}, 'Entrée'), ' ouvrir'), h('span', {}, h('kbd', {}, 'Échap'), ' fermer'))));
  document.addEventListener('keydown', onKey, true);
  document.body.append($palette);
  input.focus();
  draw();
  try {
    items = (await cached('items', '/api/items')).items.map((it) => [...it, norm(it[1])]);
    if ($palette) draw();
  } catch (error) { /* les pages restent proposées */ }
}

document.addEventListener('keydown', (event) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || '');
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); openSearch(); }
  else if (event.key === '/' && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); openSearch(); }
});
document.getElementById('search-open').addEventListener('click', () => openSearch());

// ---------------------------------------------------------------- nouveautés

// Une entrée par mise à jour qui change quelque chose à l'écran, la plus récente d'abord. Le numéro ne fait que monter.
const NOTES = [
  { id: 7, date: '8 octobre 2026', title: 'Page « Aujourd\u2019hui » et menu rangé', hash: '#/today', go: 'Voir la page', points: [
    ['Aujourd\u2019hui :', 'la nouvelle page d\u2019accueil. Ce qui demande un geste (lots sous-enchéris, lots qui expirent), ce qui s\u2019est passé depuis ta dernière visite, les crafts faisables avec ton stock et les signaux du marché.'],
    ['Almanax :', 'le bonus du jour et son offrande, avec son coût et ce que tu en possèdes. Les flèches font défiler les jours.'],
    ['Menu :', 'les pages sont rangées par usage : gagner des kamas, mon compte, outils. État, Aide et Config sont en bas.'],
  ] },
  { id: 6, date: '8 octobre 2026', title: 'Recherche globale, forgemagie et annonces similaires', hash: '#/forge/ranking', go: 'Voir le classement', points: [
    ['Recherche globale, Ctrl+K ou la touche / :', 'une fenêtre de recherche s\u2019ouvre d\u2019où que tu sois. Tape le nom d\u2019un objet pour ouvrir sa fiche, ou celui d\u2019une page ou d\u2019un onglet pour y aller.'],
    ['Aussi dans le menu :', 'le bouton « Rechercher », en haut à gauche.'],
    ['Forgemagie › Classement :', 'un choix du métier de forgemagie, et une case « Ce que je peux forgemager » d’après tes métiers et leur niveau.'],
    ['Mes critères par objet :', 'ils se règlent dans l’onglet « Par objet », panneau de gauche. Le classement le rappelle désormais.'],
    ['Mes ventes :', 'un filtre Équipements / Ressources, et les jets de tes équipements en vente au survol (après avoir rouvert l’onglet Vendre en jeu).'],
    ['Annonces similaires :', 'tes équipements sont comparés aux annonces HDV du même modèle qui ont les mêmes exos, les mêmes overs et des jets au moins aussi bons, à 10 % près. Mes ventes dit si tu es le moins cher ; le journal de forgemagie s’en sert pour estimer un objet pas encore en vente, avec un niveau de confiance.'],
    ['Forgemagie › Mon journal :', 'au survol d’un objet, ses jets réels après forgemagie, et plus ceux du modèle.'],
    ['Quantités vendues :', 'sur 7 et 30 jours, dans Crafts et dans le classement de forgemagie. « Non consulté » : ouvre le cours du marché de l’objet en jeu.'],
    ['Optimisation :', 'l’app prend moins de mémoire et de place sur le disque, et le classement de forgemagie s’ouvre plus vite.'],
  ] },
  { id: 1, date: '7 octobre 2026', title: 'Journal de forgemagie et ventes hors ligne', hash: '#/forge/journal', go: 'Voir mon journal', points: [
    ['Forgemagie › Mon journal :', 'chaque rune que tu passes en jeu est notée toute seule. Un dossier par objet : runes passées, coût, taux de réussite, jets avant et après.'],
    ['Marge par objet :', 'calculée dès que l’objet forgemagé est mis en vente, définitive quand il est vendu. Le prix de l’objet de base se saisit dans le dossier.'],
    ['Ventes hors ligne :', 'les lots vendus pendant ton absence entrent dans Mes ventes › Journal, dès que tu rouvres l’onglet Vendre de l’HDV.'],
    ['Journal des ventes et achats :', 'tes lots en vente se mettent à jour en direct, et une case « Forgemagie seulement » isole runes et objets forgemagés.'],
    ['Prix de référence :', 'une vente du cours du marché plus récente que le relevé HDV prime désormais sur l’annonce.'],
  ] },
];
const NOTES_KEY = 'dofustool.notes';

/** Affiche les nouveautés. Sans argument : toutes celles parues depuis la dernière fois, ou rien. */
function showNotes(notes) {
  const latest = NOTES[0].id;
  let seen = null;
  try { seen = localStorage.getItem(NOTES_KEY); } catch (error) { return; }
  const remember = () => { try { localStorage.setItem(NOTES_KEY, String(latest)); } catch (error) { /* stockage indisponible */ } };
  if (!notes) {
    // Jamais rien vu : sur une installation neuve la visite guidée suffit ; sinon c'est la première mise à jour annoncée.
    notes = seen === null ? (S.status && S.status.onboarded ? NOTES.slice(0, 1) : []) : NOTES.filter((n) => n.id > Number(seen)).slice(0, 3);
    remember();
    if (!notes.length) return;
  }
  const close = () => { $box.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  const first = notes[0];
  const $box = h('div', { class: 'notes', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'notes-title', onclick: (event) => { if (event.target === $box) close(); } },
    h('div', { class: 'tour-card' },
      h('div', { class: 'tour-count' }, 'Nouveautés'),
      notes.flatMap((note, index) => [
        h('h2', { id: index === 0 ? 'notes-title' : null }, note.title),
        h('div', { class: 'muted small' }, note.date),
        h('ul', { class: 'tour-points' }, note.points.map(([strong, text]) => h('li', {}, h('strong', {}, strong), ' ', text)))]),
      h('div', { class: 'tour-actions' },
        h('button', { class: 'btn quiet', style: 'margin-left: auto', onclick: close }, 'Fermer'),
        first.hash && h('button', { id: 'notes-go', class: 'btn primary', onclick: () => { close(); location.hash = first.hash; } }, first.go))));
  document.addEventListener('keydown', onKey);
  document.body.append($box);
  ($box.querySelector('#notes-go') || $box.querySelector('button')).focus();
}

// ---------------------------------------------------------------- démarrage

(async function start() {
  renderNav();
  renderCapture();
  try { S.status = await api('/api/status'); } catch (error) { /* affiché par la page */ }
  renderCapture();
  await render();
  const fresh = S.status && !S.status.onboarded;
  showNotes(); // avant la visite : une installation neuve retient qu'elle n'a rien à rattraper
  if (fresh) startTour(); // premier démarrage : la visite guidée
  await poll();
  setInterval(poll, 5000);
  setInterval(renderCapture, 30000);
})();
