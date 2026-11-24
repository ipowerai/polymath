/* Polymath dashboard. Renders api/state (or the data embedded in a snapshot file).
   No dependencies; everything is built with DOM calls, so no data is ever parsed as HTML. */
'use strict';
// Everything below looks up the page's elements, so it runs once they exist: the server's page loads this file
// deferred (it runs after parsing), but a snapshot file has it inline in <head>, where it would run before <body>.
((main) => {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', main, {once: true});
  else main();
})(() => {
  // --- DOM helpers ------------------------------------------------------------------
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const DAY = 86400000;

  function add(el, kids) {
    for (const kid of kids) {
      if (kid == null || kid === false || kid === true) continue;
      if (Array.isArray(kid)) add(el, kid);
      else el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'style') Object.assign(el.style, v);
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (k === 'text') el.textContent = v;
      else if (k in el && typeof v !== 'string') el[k] = v;
      else el.setAttribute(k, v === true ? '' : String(v));
    }
    return add(el, kids);
  }
  function s(tag, attrs, ...kids) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'text') el.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, String(v));
    }
    return add(el, kids);
  }
  const $ = (id) => document.getElementById(id);
  const view = $('view'), navEl = $('nav'), liveEl = $('live'), themeBtn = $('theme');
  const tipEl = $('tooltip'), toastEl = $('toast');

  const ICONS = {
    check: '<path d="M4.5 12.5l4.5 4.5L19.5 7"/>',
    x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    half: '<path d="M5 12h14"/>',
    alert: '<path d="M12 3.5l9.5 16.5h-19z"/><path d="M12 10v4.5M12 17.6v.4"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    pause: '<path d="M9 6.5v11M15 6.5v11"/>',
    dot: '<circle cx="12" cy="12" r="4" fill="currentColor"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    auto: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17" /><path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="currentColor"/>',
    out: '<path d="M14 5h5v5M19 5l-8 8M17 14v5H5V7h5"/>',
    up: '<path d="M12 19V5M5.5 11.5L12 5l6.5 6.5"/>',
    down: '<path d="M12 5v14M5.5 12.5L12 19l6.5-6.5"/>',
    flat: '<path d="M5 12h14M13.5 6.5L19 12l-5.5 5.5"/>',
  };
  function icon(name, cls, label) {
    const el = s('svg', {viewBox: '0 0 24 24', class: `ic ${cls || ''}`, fill: 'none', stroke: 'currentColor',
      'stroke-width': '2.2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round'});
    el.innerHTML = ICONS[name] || ICONS.dot; // fixed, trusted markup from the table above
    if (label) { el.setAttribute('role', 'img'); el.setAttribute('aria-label', label); }
    else el.setAttribute('aria-hidden', 'true');
    return el;
  }

  // --- formatting -----------------------------------------------------------------------
  const isNum = (x) => typeof x === 'number' && isFinite(x);
  const pct = (x, d = 0) => (isNum(x) ? `${(x * 100).toFixed(d)}%` : '–');
  const fixed = (x, d = 1) => (isNum(x) ? Number(x).toFixed(d) : '–');
  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;
  const clamp01 = (x) => Math.max(0, Math.min(1, x || 0));
  function localDay(iso) {
    const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number);
    return new Date(y, m - 1, d);
  }
  const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  const dayKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const today = () => localDay((app.state && app.state.app.today) || dayKey(new Date()));
  const daysFrom = (a, b) => Math.round((b - a) / DAY);
  const fmtDay = (d) => d.toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'});
  const fmtShort = (d) => d.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
  const fmtLong = (d) => d.toLocaleDateString(undefined, {weekday: 'long', month: 'long', day: 'numeric'});
  const fmtTime = (iso) => new Date(iso).toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'});
  const fmtWhen = (iso) => new Date(iso).toLocaleString(undefined,
    {weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
  function relTime(iso) {
    if (!iso) return '–';
    const t = Date.parse(iso);
    if (!isFinite(t)) return '–';
    const diff = t - Date.now(), abs = Math.abs(diff), past = diff < 0;
    if (abs < 45e3) return 'just now';
    if (abs < 45 * 60e3) { const m = Math.round(abs / 60e3); return past ? `${m} min ago` : `in ${m} min`; }
    if (abs < 18 * 3600e3) { const hr = Math.round(abs / 3600e3); return past ? `${hr} h ago` : `in ${hr} h`; }
    const days = daysFrom(localDay(dayKey(new Date())), localDay(dayKey(new Date(t))));
    if (days === 0) return past ? 'earlier today' : 'later today';
    if (days === -1) return 'yesterday';
    if (days === 1) return 'tomorrow';
    return days < 0 ? `${-days} days ago` : `in ${days} days`;
  }
  function untilText(iso) {
    const n = daysFrom(today(), localDay(iso));
    if (n === 0) return 'today';
    if (n === 1) return 'tomorrow';
    return n > 0 ? `${n} days left` : `${-n} days ago`;
  }

  // --- app state --------------------------------------------------------------------------
  const app = {
    state: null, snapshot: false, fetchedAt: 0, failing: false, pending: false, route: null, refocus: null,
    drafts: {}, cache: new Map(),
    ui: {tables: new Set(), sort: {key: 'area', dir: 1}, filter: {q: '', area: '', status: ''},
      mistakes: 'unresolved', mistakesTrack: '', answersOffset: 0},
  };
  const writable = () => !!(app.state && app.state.app.mode === 'live' && !app.state.app.read_only);

  // The sign-in key. Signing in ends on this page with a key in the address (#k=…), which no server ever sees:
  // it moves into this page's own storage, which pages on other ports of this host can't read (they are sent its
  // cookies), and goes with every API call. A sign-in cookie seen by another program on this machine opens
  // nothing without it. Read from storage at each call, so a sign-in in another tab is taken up here too.
  const KEY_NAME = 'polymath-key';
  let memoryKey = '';
  function pageKey() {
    try { return localStorage.getItem(KEY_NAME) || memoryKey; } catch (err) { return memoryKey; }
  }
  // A key that arrives in the address is kept only once the server accepts it: any page can send this tab to
  // /#k=<anything>, and a planted key must never replace the real one (that would sign every tab out).
  const keyReady = (async function takeKey() {
    const m = /^#k=([0-9a-f]{64})$/.exec(location.hash);
    if (!m) return;
    history.replaceState(null, '', location.pathname + location.search);
    try {
      const res = await fetch('api/version', {credentials: 'same-origin', cache: 'no-store',
        headers: {Accept: 'application/json', 'X-Polymath-Key': m[1]}});
      if (!res.ok) return;
    } catch (err) { return; }
    memoryKey = m[1];
    try { localStorage.setItem(KEY_NAME, m[1]); } catch (err) { /* storage off: this tab keeps it */ }
  })();
  const TOO_MANY_COOKIES = 'This browser sent more cookie data than a request may carry: something else on this machine '
    + 'has set cookies for this address. Clear this site’s cookies in the browser, then sign in again.';

  // One API request, with the key. A 401 can come in the moment another tab signs in again (this tab already has the
  // new cookie, but not yet the key that tab is storing): read the key again, and try once more before leaving.
  async function call(url, options) {
    await keyReady;
    const send = (key) => fetch(url, {...options, credentials: 'same-origin', cache: 'no-store',
      headers: {...options.headers, 'X-Polymath-Key': key}});
    let key = pageKey();
    let res = await send(key);
    if (res.status === 401) {
      if (pageKey() === key) await new Promise((resolve) => setTimeout(resolve, 400));
      key = pageKey();
      res = await send(key);
    }
    if (res.status === 401) { location.href = 'login'; throw new Error('Sign in first.'); }
    if (res.status === 431) throw Object.assign(new Error(TOO_MANY_COOKIES), {status: 431});
    return res;
  }
  async function getJSON(url) {
    const res = await call(url, {headers: {Accept: 'application/json'}});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.error || `The server answered ${res.status}.`), {code: data.code, status: res.status});
    return data;
  }
  async function post(url, body) {
    const res = await call(url, {method: 'POST', body: JSON.stringify(body),
      headers: {'Content-Type': 'application/json', 'X-Polymath': '1'}});
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.error || `The server answered ${res.status}.`), {code: data.code, status: res.status});
    return data;
  }
  async function refresh() {
    try {
      app.state = await getJSON('api/state');
      app.fetchedAt = Date.now();
      app.failing = false;
      app.cache.clear();
      renderSoon(true);
      if (onSession()) reloadSession(); // the data changed under an open session: a void elsewhere, an undo
    } catch (err) {
      app.failing = true;
      updateLive();
      if (!app.state) view.replaceChildren(h('div', {class: 'empty'}, h('b', null, 'Can’t reach the dashboard server.'),
        h('p', null, String(err.message || err))));
    }
  }
  async function poll() {
    if (app.snapshot || document.hidden || !app.state) return;
    try {
      const v = await getJSON('api/version');
      app.failing = false;
      if (v.v !== app.state.app.data_version || Date.now() - app.fetchedAt > 5 * 60e3) await refresh();
      else updateLive();
    } catch (err) {
      app.failing = true;
      updateLive();
      if (err.status === 431 && !app.toldCookies) { app.toldCookies = true; toast(err.message, true); } // say what to do, once
    }
  }
  function editing() {
    const a = document.activeElement;
    return !!(a && view.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName));
  }
  // A redraw replaces the page, so one that arrives on its own (a poll, a reply) waits while the learner
  // types, and while a pointer is down: a click whose press and release land on different elements (the
  // old button and the one that replaced it) never happens.
  const pointer = {down: false, at: 0};
  const pointerBusy = () => pointer.down && Date.now() - pointer.at < 10000;
  let flushTimer = 0;
  function holdRender() {
    app.pending = true;
    clearTimeout(flushTimer);
    flushTimer = setTimeout(flushRender, 10100); // in case the release never reaches the page
  }
  function flushRender() {
    if (!app.pending || editing()) return; // still typing: the focusout that ends it flushes
    if (pointerBusy()) holdRender();
    else render();
  }
  function redraw() { if (pointerBusy()) holdRender(); else render(); }
  function renderSoon(fromData) {
    if (fromData && app.route && app.route.name === 'study' && app.route.id) { updateLive(); return; }
    if (editing()) { app.pending = true; updateLive(); } else redraw();
  }
  document.addEventListener('pointerdown', (e) => {
    app.refocus = null; // a pointer user: don't steer focus back to a control
    if (e.isPrimary && e.button === 0) { pointer.down = true; pointer.at = Date.now(); }
  }, true);
  const released = () => { pointer.down = false; setTimeout(flushRender, 0); }; // runs after the click it ends
  ['pointerup', 'pointercancel', 'click', 'contextmenu', 'dragstart'].forEach((type) => document.addEventListener(type, released, true));
  window.addEventListener('blur', released);
  document.addEventListener('pointermove', (e) => { if (pointer.down && !e.buttons) released(); }, {passive: true}); // a release we missed
  view.addEventListener('focusout', () => setTimeout(flushRender, 60));
  document.addEventListener('focusin', (e) => {
    if (app.refocus && !(e.target instanceof Element && e.target.hasAttribute('data-focus-scope'))) app.refocus = null;
  });

  // --- routing --------------------------------------------------------------------------
  function parseRoute() {
    let parts = [];
    try {
      parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
    } catch (err) { parts = []; }
    const [name, a, b] = parts;
    if (name === 'track' && a) return {name: 'track', slug: a, tab: b || 'progress'};
    if (name === 'concept' && a) return {name: 'concept', id: a};
    if (name === 'mistakes' || name === 'activity' || name === 'you' || name === 'coach') return {name};
    if (name === 'study') return {name: 'study', id: a || null};
    if (name === 'goals') return {name: 'goals', id: a || null};
    if (name === 'models') return {name: 'models', tab: a || null, id: b || null};
    return {name: 'overview'};
  }
  const link = (...parts) => '#/' + parts.map(encodeURIComponent).join('/');

  // --- theme ------------------------------------------------------------------------------
  const THEMES = ['auto', 'light', 'dark'];
  function storedTheme() {
    try { return THEMES.includes(localStorage.getItem('polymath-theme')) ? localStorage.getItem('polymath-theme') : 'auto'; }
    catch (err) { return 'auto'; }
  }
  function applyTheme(theme) {
    if (theme === 'auto') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    themeBtn.replaceChildren(icon(theme === 'dark' ? 'moon' : theme === 'light' ? 'sun' : 'auto'));
    const name = {auto: 'match the system', light: 'light', dark: 'dark'}[theme];
    themeBtn.setAttribute('aria-label', `Colour theme: ${name}. Change it.`);
    themeBtn.title = `Theme: ${name}`;
  }
  themeBtn.addEventListener('click', () => {
    const next = THEMES[(THEMES.indexOf(storedTheme()) + 1) % THEMES.length];
    try { localStorage.setItem('polymath-theme', next); } catch (err) { /* private mode: not remembered */ }
    applyTheme(next);
  });

  // --- tooltip, toast ------------------------------------------------------------------
  function tipContent({title, rows}) {
    return [title ? h('div', {class: 'tt-title'}, title) : null,
      ...(rows || []).map((r) => h('div', {class: 'tt-row'},
        r.key ? h('span', {class: `tt-key is-${r.key}`}) : null, h('b', null, r.value),
        r.label ? h('span', {class: 'muted'}, r.label) : null))];
  }
  function showTip(content, x, y) {
    tipEl.replaceChildren(...tipContent(content));
    tipEl.hidden = false;
    const w = tipEl.offsetWidth, ht = tipEl.offsetHeight;
    let left = x + 14, top = y + 14;
    if (left + w > window.innerWidth - 8) left = Math.max(8, x - w - 14);
    if (top + ht > window.innerHeight - 8) top = Math.max(8, y - ht - 14);
    tipEl.style.left = `${left}px`;
    tipEl.style.top = `${top}px`;
  }
  function hideTip() { tipEl.hidden = true; }
  function hoverTip(el, content) {
    const show = (e) => showTip(content(), e.clientX, e.clientY);
    el.addEventListener('pointermove', show);
    el.addEventListener('pointerdown', show);
    el.addEventListener('pointerleave', hideTip);
    el.addEventListener('focus', () => { const r = el.getBoundingClientRect(); showTip(content(), r.left + r.width / 2, r.bottom); });
    el.addEventListener('blur', hideTip);
    return el;
  }
  window.addEventListener('scroll', hideTip, {passive: true});
  let toastTimer = 0;
  function toast(message, error) {
    toastEl.textContent = message;
    toastEl.className = `toast${error ? ' is-error' : ''}`;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, error ? 6000 : 3000);
  }

  // --- shared pieces ------------------------------------------------------------------------
  function card({title, sub, actions, body, cls}) {
    const head = (title || actions) ? h('div', {class: 'card-head'},
      h('div', null, title ? h('h2', null, title) : null, sub ? h('div', {class: 'card-sub'}, sub) : null),
      actions ? h('div', {class: 'card-actions'}, actions) : null) : null;
    return h('section', {class: `card ${cls || ''}`}, head, body);
  }
  function stat(label, value, sub, extra) {
    return h('div', {class: 'stat'}, h('div', {class: 'stat-label'}, label), h('div', {class: 'stat-value'}, value),
      sub ? h('div', {class: 'stat-sub'}, sub) : null, extra);
  }
  function badge(b) {
    return b ? h('span', {class: `badge ${b.cls}`}, icon(b.icon), b.text) : null;
  }
  function empty(title, ...lines) {
    return h('div', {class: 'empty'}, h('b', null, title), ...lines.map((l) => h('p', null, l)));
  }
  function cmdBox(text) {
    const code = h('code', null, text);
    const btn = h('button', {class: 'btn btn-small', type: 'button', onclick: async () => {
      try { await navigator.clipboard.writeText(text); toast('Copied. Paste it into Claude Code.'); }
      catch (err) {
        const range = document.createRange(); range.selectNodeContents(code);
        const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
        toast('Selected: press Ctrl+C (or ⌘C) to copy.');
      }
    }}, 'Copy');
    return h('div', {class: 'cmd'}, code, btn);
  }
  function meter(value, ticks, label) {
    const bar = h('div', {class: 'meter', role: 'img', 'aria-label': label},
      h('div', {class: 'meter-fill', style: {width: `${clamp01(value) * 100}%`}}),
      ...(ticks || []).map((t) => h('div', {class: 'meter-tick', style: {left: `${clamp01(t.at) * 100}%`}})));
    const labels = (ticks || []).length ? h('div', {class: 'meter-labels', 'aria-hidden': 'true'},
      ticks.map((t) => h('span', {class: `meter-label is-${t.align || 'end'}`, style: {left: `${clamp01(t.at) * 100}%`}}, t.label))) : null;
    return h('div', {class: 'meter-wrap'}, bar, labels);
  }
  function barH(value, label) {
    return h('div', {class: 'bar-h', role: 'img', 'aria-label': label}, h('span', {style: {width: `${clamp01(value) * 100}%`}}));
  }
  const STATUS = {
    mastered: {label: 'Mastered', order: 4}, developing: {label: 'Developing', order: 3},
    learning: {label: 'Learning', order: 2}, due: {label: 'Due for review', order: 1}, new: {label: 'New', order: 0},
  };
  const STATUS_ORDER = ['mastered', 'developing', 'learning', 'due', 'new'];
  function pill(status) {
    return h('span', {class: 'pill'}, h('i', {class: `lvl-${status}`}), (STATUS[status] || {label: status}).label);
  }
  function statusLegend() {
    return h('div', {class: 'legend'}, STATUS_ORDER.map((k) => h('span', {class: 'key'},
      h('i', {class: `key-swatch lvl-${k}`}), STATUS[k].label)));
  }
  function segBar(parts, total) {
    const bar = h('div', {class: 'seg', role: 'img',
      'aria-label': parts.filter((p) => p.value).map((p) => `${p.label} ${p.value}`).join(', ')});
    for (const p of parts) {
      if (!p.value) continue;
      bar.append(hoverTip(h('span', {class: `lvl-${p.key}`, style: {flexGrow: String(p.value), flexBasis: '0px'}}),
        () => ({title: p.label, rows: [{value: `${p.value} of ${total}`, label: 'concepts'}]})));
    }
    return bar;
  }
  const RESULT = {
    correct: {icon: 'check', cls: 'is-good', label: 'Right'},
    partial: {icon: 'half', cls: 'is-warning', label: 'Partly right'},
    incorrect: {icon: 'x', cls: 'is-critical', label: 'Wrong'},
  };
  const MISTAKE = {
    open: {icon: 'x', cls: 'is-critical', label: 'Open'},
    fixing: {icon: 'clock', cls: 'is-warning', label: 'Fixing'},
    resolved: {icon: 'check', cls: 'is-good', label: 'Resolved'},
  };
  const ERROR = {misconception: 'Wrong belief', gap: 'Gap', confusion: 'Mixed up', detail: 'Detail', slip: 'Slip', trap: 'Trap'};
  const KIND = {mistake: 'Fix', review: 'Review', probe: 'New', practice: 'Practice'};
  const QTYPE = {mcq: 'Multiple choice', scenario: 'Scenario', multi: 'Choose several', tf: 'True/false',
    recall: 'Recall', explain: 'Explain', code: 'Code'};
  function mistakeStatus(m) {
    const meta = MISTAKE[m.status] || MISTAKE.open;
    return h('span', {class: `pill ${meta.cls}`}, icon(meta.icon), meta.label);
  }
  function safeHref(url) { return /^(https?:|mailto:)/i.test(String(url || '')); }
  function extLink(url, label) {
    return safeHref(url) ? h('a', {href: url, target: '_blank', rel: 'noopener noreferrer'}, label || url) : (label || url);
  }

  function markdown(text) {
    // First gather blocks (a paragraph or list item may span lines), then format each one.
    const blocks = [];
    let open = null;
    for (const raw of String(text || '').replace(/\r\n?/g, '\n').split('\n')) {
      const line = raw.replace(/\s+$/, '');
      let m;
      if (!line.trim()) { open = null; continue; }
      if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
        blocks.push({kind: `h${Math.min(4, m[1].length + 1)}`, text: m[2]});
        open = null;
      } else if ((m = line.match(/^\s*[-*+]\s+(.*)$/)) || (m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
        open = {kind: 'li', list: /^\s*\d/.test(line) ? 'ol' : 'ul', text: m[1]};
        blocks.push(open);
      } else if (open && (open.kind === 'p' || /^\s/.test(raw))) {
        open.text += ` ${line.trim()}`;
      } else {
        open = {kind: 'p', text: line.trim()};
        blocks.push(open);
      }
    }
    const root = h('div', {class: 'md'});
    let list = null;
    for (const b of blocks) {
      if (b.kind === 'li') {
        if (!list || list.tagName.toLowerCase() !== b.list) { list = h(b.list); root.append(list); }
        list.append(h('li', null, inline(b.text)));
      } else {
        list = null;
        root.append(h(b.kind, null, inline(b.text)));
      }
    }
    return root;
  }
  function inline(text) {
    const out = [];
    const re = /`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)|(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])|(?<![\w_])_([^_\s][^_]*)_(?![\w_])/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      if (m[1] != null) out.push(h('code', null, m[1]));
      else if (m[2] != null) out.push(h('strong', null, m[2]));
      else if (m[3] != null) out.push(extLink(m[4], m[3]));
      else out.push(h('em', null, m[5] != null ? m[5] : m[6]));
      last = re.lastIndex;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }

  // --- forms ------------------------------------------------------------------------------------
  function field(form, name, label, input) {
    const saved = (app.drafts[form] || {})[name];
    if (saved != null && 'value' in input) input.value = saved;
    input.name = name;
    input.id = `${form}-${name}`;
    input.addEventListener('input', () => { (app.drafts[form] = app.drafts[form] || {})[name] = input.value; });
    return h('label', {class: 'field', for: input.id}, label, input);
  }
  function submitForm(formId, handler, success) {
    return async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector('button[type=submit]');
      if (button) button.disabled = true;
      try {
        await handler(new FormData(form));
        delete app.drafts[formId];
        toast(success);
        form.reset();
        await refresh();
      } catch (err) {
        toast(err.message || String(err), true);
      } finally {
        if (button) button.disabled = false;
      }
    };
  }
  const numberOrNull = (v) => (v === '' || v == null ? null : Number(v));

  // --- charts --------------------------------------------------------------------------------
  const charts = new Map();
  const pendingCharts = [];
  const resizer = 'ResizeObserver' in window ? new ResizeObserver((entries) => entries.forEach((e) => drawChart(e.target))) : null;
  function chartBox(draw) {
    const box = h('div', {class: 'chart-box'});
    pendingCharts.push([box, draw]);
    return box;
  }
  function mountCharts() {
    for (const box of charts.keys()) if (!box.isConnected) { charts.delete(box); if (resizer) resizer.unobserve(box); }
    for (const [box, draw] of pendingCharts.splice(0)) {
      charts.set(box, {draw, width: 0});
      drawChart(box);
      if (resizer) resizer.observe(box);
    }
  }
  function drawChart(box) {
    const c = charts.get(box);
    if (!c || !box.isConnected) return;
    const width = Math.floor(box.clientWidth);
    if (!width || width === c.width) return;
    c.width = width;
    box.replaceChildren(c.draw(width));
  }
  window.addEventListener('resize', () => { for (const box of charts.keys()) drawChart(box); });

  function niceStep(span, count) {
    const raw = span / Math.max(1, count);
    const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    const norm = raw / mag;
    return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  }
  function niceTicks(lo, hi, count) {
    const step = niceStep(hi - lo || 1, count);
    const ticks = [];
    for (let v = Math.floor(lo / step) * step; v <= hi + step * 0.001; v += step) ticks.push(Number(v.toFixed(6)));
    if (ticks[ticks.length - 1] < hi) ticks.push(Number((ticks[ticks.length - 1] + step).toFixed(6)));
    return ticks;
  }
  function dateTicks(x0, x1, width) {
    const days = Math.max(1, Math.round((x1 - x0) / DAY));
    const room = Math.max(1, Math.floor(width / 70));  // gaps between labels that fit
    const step = [1, 2, 3, 7, 14, 28, 56, 91].find((d) => days / d <= room) || Math.ceil(days / room);
    const ticks = [];
    for (let i = 0; i <= days; i += step) ticks.push(addDays(new Date(x0), i));
    return ticks;
  }
  function spreadLabels(items, low, high, gap) {
    items.sort((a, b) => a.y - b.y);
    for (let i = 1; i < items.length; i++) if (items[i].y - items[i - 1].y < gap) items[i].y = items[i - 1].y + gap;
    const over = items.length ? items[items.length - 1].y - high : 0;
    if (over > 0) items.forEach((it) => { it.y -= over; });
    for (let i = items.length - 2; i >= 0; i--) if (items[i + 1].y - items[i].y < gap) items[i].y = items[i + 1].y - gap;
    items.forEach((it) => { it.y = Math.max(low, it.y); });
    return items;
  }

  /* A line chart over dates: one or two series, reference lines, a crosshair tooltip. */
  function lineChart(width, cfg) {
    const H = cfg.height || 230;
    const m = {l: 42, r: 76, t: 22, b: 26};
    const pw = Math.max(60, width - m.l - m.r), ph = H - m.t - m.b;
    const x0 = +cfg.x0, x1 = Math.max(+cfg.x1, x0 + DAY);
    const X = (t) => m.l + ((+t - x0) / (x1 - x0)) * pw;
    const Y = (v) => m.t + (1 - (v - cfg.yMin) / (cfg.yMax - cfg.yMin)) * ph;
    const svg = s('svg', {width, height: H, viewBox: `0 0 ${width} ${H}`, role: 'img', 'aria-label': cfg.summary, tabindex: 0});
    for (const v of cfg.yTicks) {
      svg.append(s('line', {class: 'grid-line', x1: m.l, x2: m.l + pw, y1: Y(v), y2: Y(v)}),
        s('text', {class: 'ax', x: m.l - 8, y: Y(v) + 4, 'text-anchor': 'end', text: cfg.yFmt(v)}));
    }
    svg.append(s('line', {class: 'axis-line', x1: m.l, x2: m.l + pw, y1: Y(cfg.yMin), y2: Y(cfg.yMin)}));
    const ticks = dateTicks(x0, x1, pw);
    ticks.forEach((t, i) => {
      const x = X(t);
      const anchor = i === 0 ? 'start' : x > m.l + pw - 28 ? 'end' : 'middle';
      svg.append(s('text', {class: 'ax', x, y: H - 6, 'text-anchor': anchor, text: fmtShort(t)}));
    });
    for (const v of cfg.vlines || []) {
      if (+v.t < x0 || +v.t > x1) continue;
      const x = X(v.t);
      svg.append(s('line', {class: 'vline', x1: x, x2: x, y1: m.t - 6, y2: m.t + ph}),
        s('text', {class: 'vline-label', x, y: m.t - 10, 'text-anchor': x > m.l + pw - 30 ? 'end' : x < m.l + 30 ? 'start' : 'middle', text: v.label}));
    }
    const labels = [];
    for (const r of cfg.refs || []) {
      if (r.y < cfg.yMin || r.y > cfg.yMax) continue;
      svg.append(s('line', {class: 'ref-line', x1: m.l, x2: m.l + pw, y1: Y(r.y), y2: Y(r.y)}));
      labels.push({y: Y(r.y) + 4, text: r.label, cls: 'ref-label'});
    }
    const times = new Map();
    for (const ser of cfg.series) {
      const pts = ser.points.filter((p) => isNum(p.y));
      if (!pts.length) continue;
      const d = pts.map((p, i) => `${i ? 'L' : 'M'}${X(p.t).toFixed(1)},${Y(p.y).toFixed(1)}`).join('');
      if (ser.area && pts.length > 1) {
        svg.append(s('path', {class: 's-area',
          d: `${d}L${X(pts[pts.length - 1].t).toFixed(1)},${Y(cfg.yMin)}L${X(pts[0].t).toFixed(1)},${Y(cfg.yMin)}Z`}));
      }
      if (pts.length > 1) svg.append(s('path', {class: `s-line ${ser.cls}`, d}));
      const last = pts[pts.length - 1];
      if (ser.dot) svg.append(s('circle', {class: `dot ${ser.dot}`, cx: X(last.t), cy: Y(last.y), r: 4}));
      if (ser.endLabel && ser.endLabel(last)) {
        const x = X(last.t), y = Y(last.y), text = ser.endLabel(last), cls = ser.labelCls || 'end-label';
        if (x >= m.l + pw - 2) labels.push({y: y + 4, text, cls});
        else if (ser.labelPos === 'below') svg.append(s('text', {class: cls, x, y: y + 19, 'text-anchor': x < m.l + 24 ? 'start' : 'middle', text}));
        else svg.append(s('text', {class: cls, x, y: y - 10, 'text-anchor': x < m.l + 24 ? 'start' : 'middle', text}));
      }
      if (!ser.noTooltip) for (const p of pts) times.set(+p.t, true);
    }
    for (const dot of cfg.dots || []) {
      if (+dot.t < x0 || +dot.t > x1) continue;
      svg.append(s('circle', {class: `dot ${dot.cls}`, cx: X(dot.t), cy: Y(dot.y), r: dot.r || 4.5}));
      times.set(+dot.t, true);
    }
    for (const lab of spreadLabels(labels, m.t, m.t + ph + 4, 13)) {
      svg.append(s('text', {class: lab.cls, x: m.l + pw + 8, y: lab.y, text: lab.text}));
    }
    // crosshair: snaps to the nearest day that has data, on hover, touch or arrow keys
    const xs = [...times.keys()].sort((a, b) => a - b);
    if (!xs.length) return svg;
    const cross = s('line', {class: 'crosshair', y1: m.t, y2: m.t + ph, visibility: 'hidden'});
    const marks = s('g', {'pointer-events': 'none'});
    const hit = s('rect', {class: 'hit hit-x', x: m.l - 6, y: m.t - 6, width: pw + 12, height: ph + 12});
    svg.append(cross, marks, hit);
    let index = xs.length - 1;
    function show(i, cx, cy) {
      index = Math.max(0, Math.min(xs.length - 1, i));
      const t = xs[index], x = X(t);
      cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('visibility', 'visible');
      const rows = [];
      marks.replaceChildren();
      for (const ser of cfg.series) {
        if (ser.noTooltip) continue;
        const p = ser.points.find((q) => +q.t === t && isNum(q.y));
        if (!p || (ser.hideWhenCovered && rows.length)) continue;
        if (ser.dot) marks.append(s('circle', {class: `dot ${ser.dot}`, cx: x, cy: Y(p.y), r: 4.5}));
        rows.push({key: ser.key, value: ser.fmt(p.y), label: ser.label});
      }
      for (const dot of cfg.dots || []) {
        if (+dot.t === t) rows.push({key: dot.key || 'mock', value: cfg.yFmt(dot.y), label: dot.label});
      }
      for (const extra of (cfg.extraRows ? cfg.extraRows(t) : [])) rows.push(extra);
      showTip({title: fmtDay(new Date(t)), rows}, cx, cy);
    }
    function nearest(clientX) {
      const r = svg.getBoundingClientRect();
      const t = x0 + ((clientX - r.left - m.l) / pw) * (x1 - x0);
      let best = 0;
      xs.forEach((v, i) => { if (Math.abs(v - t) < Math.abs(xs[best] - t)) best = i; });
      return best;
    }
    function hide() { cross.setAttribute('visibility', 'hidden'); marks.replaceChildren(); hideTip(); }
    hit.addEventListener('pointermove', (e) => show(nearest(e.clientX), e.clientX, e.clientY));
    hit.addEventListener('pointerdown', (e) => show(nearest(e.clientX), e.clientX, e.clientY));
    hit.addEventListener('pointerleave', hide);
    const anchor = () => { const r = svg.getBoundingClientRect(); return [r.left + X(xs[index]), r.top + m.t]; };
    svg.addEventListener('focus', () => show(index, ...anchor()));
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      index += e.key === 'ArrowLeft' ? -1 : 1;
      show(index, ...anchor());
    });
    return svg;
  }

  /* Columns, one per day: values on the first and the tallest, the rest on hover. */
  function columnChart(width, cfg) {
    const H = cfg.height || 170;
    const m = {l: 30, r: 6, t: 20, b: 24};
    const n = cfg.bars.length, pw = Math.max(40, width - m.l - m.r), ph = H - m.t - m.b;
    const max = Math.max(1, ...cfg.bars.map((b) => b.value));
    const ticks = niceTicks(0, Math.max(3, max), 3).filter((v) => Number.isInteger(v));
    const top = ticks[ticks.length - 1];
    const Y = (v) => m.t + (1 - v / top) * ph;
    const svg = s('svg', {width, height: H, viewBox: `0 0 ${width} ${H}`, role: 'img', 'aria-label': cfg.summary});
    for (const v of ticks) {
      svg.append(s('line', {class: 'grid-line', x1: m.l, x2: m.l + pw, y1: Y(v), y2: Y(v)}),
        s('text', {class: 'ax', x: m.l - 8, y: Y(v) + 4, 'text-anchor': 'end', text: String(v)}));
    }
    svg.append(s('line', {class: 'axis-line', x1: m.l, x2: m.l + pw, y1: Y(0), y2: Y(0)}));
    const band = pw / n, bw = Math.min(24, Math.max(4, band - 6));
    const every = band < 30 ? 2 : 1;
    const peak = cfg.bars.reduce((best, b, i) => (b.value > cfg.bars[best].value ? i : best), 0);
    cfg.bars.forEach((b, i) => {
      const cx = m.l + band * (i + 0.5), x = cx - bw / 2, y = Y(b.value), hgt = Y(0) - y;
      let bar = null;
      if (b.value > 0) {
        const r = Math.min(4, bw / 2, hgt);
        bar = s('path', {class: 'bar', d: `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + bw - r}Q${x + bw},${y} ${x + bw},${y + r}V${y + hgt}Z`});
        svg.append(bar);
        if (i === 0 || i === peak) svg.append(s('text', {class: 'cap-label', x: cx, y: y - 6, text: String(b.value)}));
      }
      if (i % every === 0) svg.append(s('text', {class: 'ax', x: cx, y: H - 6, 'text-anchor': 'middle', text: b.label}));
      const hit = s('rect', {class: 'hit', x: m.l + band * i, y: m.t - 8, width: band, height: ph + 8});
      hit.addEventListener('pointermove', (e) => { if (bar) bar.classList.add('is-active'); showTip(b.tip, e.clientX, e.clientY); });
      hit.addEventListener('pointerdown', (e) => showTip(b.tip, e.clientX, e.clientY));
      hit.addEventListener('pointerleave', () => { if (bar) bar.classList.remove('is-active'); hideTip(); });
      svg.append(hit);
    });
    return svg;
  }

  /* A year of study days: one cell per day, darker for more answers. */
  function heatmap(width, cfg) {
    const left = 30, top = 18, gap = 2, wanted = cfg.maxWeeks || 53;
    const pitch = Math.max(12, Math.min(18, Math.floor((width - left) / wanted)));
    const cell = pitch - gap;
    const weeks = Math.max(4, Math.min(wanted, Math.floor((width - left) / pitch)));
    const end = cfg.end;
    const start = addDays(addDays(end, -end.getDay()), -(weeks - 1) * 7);
    const counts = [...cfg.days.values()].map((d) => d.answers).filter((v) => v > 0).sort((a, b) => a - b);
    const q = (p) => counts[Math.min(counts.length - 1, Math.floor(p * counts.length))];
    const cut = counts.length >= 4 ? [q(0.25), q(0.5), q(0.75)] : [5, 15, 30];
    const level = (d) => (!d || (!d.answers && !d.minutes) ? 0 : !d.answers ? 1
      : d.answers <= cut[0] ? 1 : d.answers <= cut[1] ? 2 : d.answers <= cut[2] ? 3 : 4);
    const W = left + weeks * pitch, H = top + 7 * pitch;
    const svg = s('svg', {width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': cfg.summary});
    ['Mon', 'Wed', 'Fri'].forEach((name, i) => svg.append(s('text', {class: 'ax', x: 0, y: top + (2 * i + 1) * pitch + cell - 2, text: name})));
    let lastMonth = -1, lastLabel = -9;
    for (let w = 0; w < weeks; w++) {
      const first = addDays(start, w * 7);
      if (first.getMonth() !== lastMonth && w - lastLabel >= 3 && first.getDate() <= 7 || w === 0) {
        if (w === 0 && first.getDate() > 21) { lastMonth = first.getMonth(); continue; }
        svg.append(s('text', {class: 'ax', x: left + w * pitch, y: 11, text: first.toLocaleDateString(undefined, {month: 'short'})}));
        lastMonth = first.getMonth();
        lastLabel = w;
      }
      for (let d = 0; d < 7; d++) {
        const day = addDays(first, d);
        if (day > end) break;
        const data = cfg.days.get(dayKey(day));
        const rect = s('rect', {class: `cell h${level(data)}`, x: left + w * pitch, y: top + d * pitch, width: cell, height: cell, rx: 2});
        const tip = () => ({title: fmtDay(day), rows: data ? [
          {value: plural(data.answers, 'answer'), label: data.minutes ? `${data.minutes} min active` : ''}]
          : [{value: 'No study', label: ''}]});
        rect.addEventListener('pointermove', (e) => { rect.classList.add('is-active'); showTip(tip(), e.clientX, e.clientY); });
        rect.addEventListener('pointerdown', (e) => showTip(tip(), e.clientX, e.clientY));
        rect.addEventListener('pointerleave', () => { rect.classList.remove('is-active'); hideTip(); });
        svg.append(rect);
      }
    }
    return svg;
  }
  function heatLegend() {
    return h('div', {class: 'legend'}, h('span', {class: 'key'}, 'Less', h('span', {class: 'heat-keys', 'aria-hidden': 'true'},
      [0, 1, 2, 3, 4].map((k) => h('i', {class: `k${k}`}))), 'More answers'));
  }

  /* A chart card with a "Table" toggle: every chart has a table view of the same numbers. */
  function chartCard({id, title, sub, draw, table, legend, foot}) {
    const asTable = app.ui.tables.has(id);
    const toggle = h('button', {class: 'btn btn-small', type: 'button', 'aria-pressed': String(asTable),
      onclick: () => { if (asTable) app.ui.tables.delete(id); else app.ui.tables.add(id); render(); }}, asTable ? 'Chart' : 'Table');
    const body = asTable ? dataTable(table.columns, table.rows, `${title}: table view`) : chartBox(draw);
    return card({title, sub, actions: [toggle], body: [body, !asTable && legend ? h('div', {class: 'chart-foot'}, legend) : null, foot]});
  }
  function dataTable(columns, rows, caption) {
    return h('div', {class: 'table-wrap'}, h('table', {class: 'table'},
      caption ? h('caption', {class: 'sr-only'}, caption) : null,
      h('thead', null, h('tr', null, columns.map((c) => h('th', {scope: 'col', class: c.num ? 'num' : null}, c.label)))),
      h('tbody', null, rows.map((r) => h('tr', {class: r._cls || null}, columns.map((c) => h('td', {class: c.num ? 'num' : c.cls || null}, c.cell(r))))))));
  }
  function sortableTable(id, columns, rows, caption) {
    const sort = app.ui.sort[id] || {key: columns.find((c) => c.sort).key, dir: 1};
    const col = columns.find((c) => c.key === sort.key) || columns[0];
    const sorted = rows.slice().sort((a, b) => {
      const va = col.sort(a), vb = col.sort(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return (va < vb ? -1 : va > vb ? 1 : 0) * sort.dir;
    });
    const head = columns.map((c) => {
      const active = c.key === sort.key;
      const label = c.sort ? h('button', {class: 'sort-btn', type: 'button', onclick: () => {
        app.ui.sort[id] = {key: c.key, dir: active ? -sort.dir : (c.desc ? -1 : 1)};
        render();
      }}, c.label, h('span', {class: 'arrow', 'aria-hidden': 'true'}, active ? (sort.dir > 0 ? '▲' : '▼') : '')) : c.label;
      return h('th', {scope: 'col', class: c.num ? 'num' : null,
        'aria-sort': active ? (sort.dir > 0 ? 'ascending' : 'descending') : null}, label);
    });
    return h('div', {class: 'table-wrap'}, h('table', {class: 'table'}, h('caption', {class: 'sr-only'}, caption),
      h('thead', null, h('tr', null, head)),
      h('tbody', null, sorted.map((r) => h('tr', {class: r._cls || null},
        columns.map((c) => h('td', {class: c.num ? 'num' : c.cls || null}, c.cell(r))))))));
  }

  // --- data helpers ------------------------------------------------------------------------------
  function tracks() {
    const order = {active: 0, paused: 1, done: 2};
    return (app.state.tracks || []).slice().sort((a, b) =>
      (order[a.status] ?? 3) - (order[b.status] ?? 3) || String(a.target_date || '9999').localeCompare(String(b.target_date || '9999')));
  }
  function track(slug) { return (app.state.tracks || []).find((t) => t.slug === slug); }
  function shortTitle(t) {
    if (t.short) return t.short;
    const title = String(t.title || '');
    const paren = title.match(/\(([^)]{2,14})\)\s*$/);
    if (paren) return paren[1];
    if (title.length <= 24) return title;
    const tail = String(t.slug || '').split('-').pop();
    if (tail && tail.length <= 6 && /\d/.test(tail)) return tail.toUpperCase();
    return `${title.slice(0, 22).replace(/\s+\S*$/, '')}\u2026`;
  }
  function readiness(t) {
    if (t.status === 'done') return {cls: 'is-good', icon: 'check', text: 'Done'};
    if (t.status === 'paused') return {cls: 'is-neutral', icon: 'pause', text: 'Paused'};
    if (t.gate && t.gate.met) return {cls: 'is-good', icon: 'check', text: 'Ready to book'};
    if (t.verdict === 'ready') return {cls: 'is-good', icon: 'check', text: 'Ready: take mocks'};
    if (t.verdict === 'close') return {cls: 'is-warning', icon: 'alert', text: 'Close to ready'};
    if (t.verdict === 'not yet') return {cls: 'is-critical', icon: 'x', text: 'Not yet'};
    return null;
  }
  function pace(t) {
    const p = t.projection || {};
    const bits = [];
    if (isNum(p.needed_per_day)) bits.push(`needs ${fixed(p.needed_per_day)} mastered a day`);
    if (isNum(p.velocity_per_day)) bits.push(`doing ${fixed(p.velocity_per_day)} a day`);
    const tone = {ahead: 'is-good', 'on track': 'is-good', complete: 'is-good', behind: 'is-warning', stalled: 'is-warning'}[p.status] || 'is-neutral';
    const ic = {ahead: 'check', 'on track': 'check', complete: 'check', behind: 'alert', stalled: 'alert'}[p.status] || 'clock';
    const label = p.status === 'too early to tell' ? 'Pace: too early to tell' : `Pace: ${p.status}`;
    return {tone, icon: ic, label, detail: bits.join(', ')};
  }
  function countsByStatus(rows) {
    const c = {mastered: 0, developing: 0, learning: 0, due: 0, new: 0};
    rows.forEach((r) => { c[r.status] = (c[r.status] || 0) + 1; });
    return c;
  }
  function liveSession() { return (app.state.sessions || []).find((x) => x.status === 'live'); }
  function examName(t) { return (t.exam && t.exam.name) || t.title; }

  // --- trends: which way each goal is heading ---------------------------------------------------
  function sparkline(values, label) {
    const width = 96, height = 30;
    const pts = values.map((v, i) => [i, v]).filter(([, v]) => isNum(v));
    if (pts.length < 2) return null;
    let lo = Math.min(...pts.map((q) => q[1])), hi = Math.max(...pts.map((q) => q[1]));
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    const X = (i) => 3 + (i / Math.max(1, values.length - 1)) * (width - 8);
    const Y = (v) => 4 + (1 - (v - lo) / (hi - lo)) * (height - 8);
    const d = pts.map(([i, v], k) => `${k ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join('');
    const [li, lv] = pts[pts.length - 1];
    return s('svg', {class: 'spark', width, height, viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': label},
      s('path', {class: 'spark-line', d}), s('circle', {class: 'spark-dot', cx: X(li), cy: Y(lv), r: 3}));
  }
  /* A signed change with an arrow: green when it helps the goal, red when it hurts. */
  function delta(value, {scale = 100, unit = ' points', period = 'this week', upGood = true} = {}) {
    if (!isNum(value)) return null;
    const v = Math.round(value * scale);
    if (v === 0) return h('span', {class: 'delta is-flat'}, icon('flat'), `no change ${period}`);
    const good = (v > 0) === upGood;
    const word = Math.abs(v) === 1 ? unit.replace(/s$/, '') : unit;
    return h('span', {class: `delta ${good ? 'is-better' : 'is-worse'}`}, icon(v > 0 ? 'up' : 'down'),
      `${v > 0 ? '+' : '−'}${Math.abs(v)}${word} ${period}`);
  }
  function outlook(t) {
    if (t.status !== 'active') return null;
    const tr = t.trend || {};
    const passing = isNum(t.passing_score) ? t.passing_score : null;
    const book = passing != null ? passing + 0.07 : null;
    const exam = t.target_date ? localDay(t.target_date) : null;
    const d7 = tr.predicted_delta;
    const week = !isNum(d7) ? null : Math.round(d7 * 100) === 0 ? `Predicted score steady over ${plural(tr.span_days, 'day')}`
      : `Predicted score ${d7 > 0 ? 'up' : 'down'} ${plural(Math.abs(Math.round(d7 * 100)), 'point')} in ${plural(tr.span_days, 'day')}`;
    const due = t.due ? ` Clearing the ${plural(t.due, 'review')} due wins back what has faded.` : '';
    if (!tr.enough) {
      return {cls: 'is-neutral', icon: 'clock', title: 'Your trend shows after a few study days',
        text: 'Answer questions on two or more days and this shows whether you are heading toward your goal or away from it.'};
    }
    if (tr.outlook === 'on track') {
      const when = isNum(tr.book_in_days) && tr.book_in_days > 0 ? addDays(today(), tr.book_in_days) : null;
      const reach = tr.book_in_days === 0 ? `You are above the booking line (${pct(book)}); keep it there.`
        : when ? `At this pace you reach the booking line (${pct(book)}) around ${fmtShort(when)}${exam && when < exam
          ? `, ${plural(daysFrom(when, exam), 'day')} before the exam` : ''}.` : '';
      return {cls: 'is-good', icon: tr.direction === 'down' ? 'alert' : 'up',
        title: tr.direction === 'down' ? 'Ahead, but slipping this week' : 'Trending toward your goal',
        text: `${week ? `${week}. ` : ''}${reach}${tr.direction === 'down' ? due : ''}`};
    }
    if (tr.outlook === 'at risk') {
      return {cls: 'is-warning', icon: 'alert', title: 'On course to pass, short of the booking line',
        text: `${week ? `${week}. ` : ''}At this pace you reach about ${pct(tr.projected_at_exam)} by ${fmtShort(exam)}: over the ${pct(passing)} pass mark, under the ${pct(book)} line.${due}`};
    }
    if (tr.outlook === 'off track') {
      return {cls: 'is-critical', icon: 'down', title: tr.direction === 'down' ? 'Trending away from your goal' : 'Not rising fast enough yet',
        text: `${week ? `${week}. ` : ''}At this pace you reach about ${pct(tr.projected_at_exam)} by ${fmtShort(exam)}, under the ${pct(passing)} pass mark.${due || ' More study days, or a later exam date, closes the gap.'}`};
    }
    const dir = {up: {cls: 'is-good', icon: 'up', title: 'Trending up'}, down: {cls: 'is-critical', icon: 'down', title: 'Trending down'},
      flat: {cls: 'is-neutral', icon: 'flat', title: 'Holding steady'}}[tr.direction];
    return dir ? {...dir, text: `${week ? `${week}.` : ''}${tr.direction === 'down' ? due : ''}`} : null;
  }
  function outlookNote(t) {
    const o = outlook(t);
    if (!o) return null;
    return h('div', {class: `callout outlook ${o.cls}`}, icon(o.icon, o.cls),
      h('div', null, h('b', null, o.title), o.text ? h('div', {class: 'muted small'}, o.text) : null));
  }
  /* Time used against progress made: are you ahead of the clock? */
  function race(t) {
    const tr = t.trend || {};
    if (!isNum(tr.time_used) || !isNum(tr.goal_progress) || !isNum(t.passing_score)) return null;
    const ahead = tr.goal_progress >= tr.time_used;
    const book = t.passing_score + 0.07;
    const row = (label, value, cls, aria) => h('div', {class: 'race-row'}, h('span', {class: 'race-label'}, label),
      h('div', {class: `race-bar ${cls}`, role: 'img', 'aria-label': aria}, h('span', {style: {width: `${clamp01(value) * 100}%`}})),
      h('b', {class: 'num'}, pct(value)));
    return h('div', {class: 'race'},
      row('Time used', tr.time_used, 'is-time', `${pct(tr.time_used)} of the time from your start to the exam`),
      row(`Climb to ${pct(book)}`, tr.goal_progress, 'is-goal', `${pct(tr.goal_progress)} of the way from ${pct(tr.start_predicted)} to ${pct(book)}`),
      h('p', {class: 'race-note'}, icon(ahead ? 'check' : 'alert', ahead ? 'is-good' : 'is-warning'),
        ahead ? `Ahead of the clock: you have climbed ${pct(tr.goal_progress)} of the way from ${pct(tr.start_predicted)} to the booking line, with ${pct(tr.time_used)} of your time used.`
          : `Behind the clock: ${pct(tr.time_used)} of your time is used, and you have climbed ${pct(tr.goal_progress)} of the way to the booking line.`));
  }
  function trendTile(label, value, change, spark, note) {
    return h('div', {class: 'stat trend-tile'}, h('div', {class: 'stat-label'}, label),
      h('div', {class: 'trend-main'}, h('div', {class: 'stat-value'}, value), spark),
      change ? h('div', {class: 'trend-delta'}, change) : null, note ? h('div', {class: 'stat-sub'}, note) : null);
  }
  function momentumCard(t) {
    const tr = t.trend || {};
    const recent = (t.history || []).slice(-14);
    const passing = isNum(t.passing_score) ? t.passing_score : null;
    const days = `in ${plural(tr.span_days || 7, 'day')}`;
    const active = (tr.answers_7d || 0) + (tr.answers_prev || 0) > 0;
    const accChange = isNum(tr.accuracy_7d) && isNum(tr.accuracy_prev) ? tr.accuracy_7d - tr.accuracy_prev : null;
    const spark = (values, label) => (active ? sparkline(values, label) : null);
    const change = (value, opts) => (active ? delta(value, opts) : null);
    const answersChange = (tr.answers_prev || 0) + (tr.answers_7d || 0) > 0 && tr.answers_prev ? (tr.answers_7d || 0) - tr.answers_prev : null;
    const tiles = h('div', {class: 'kpis trend-kpis'},
      passing != null ? trendTile('Predicted score', pct(t.predicted_score), change(tr.predicted_delta, {period: days}),
        spark(recent.map((p) => p.predicted), 'Predicted score, last 14 days'), `pass mark ${pct(passing)}`) : null,
      trendTile('Right answers, last 7 days', pct(tr.accuracy_7d), change(accChange, {period: 'vs the week before'}),
        spark(recent.map((p) => (p.answers ? p.points / p.answers : null)), 'Share of answers right each day, last 14 days'),
        tr.answers_7d ? `of ${plural(tr.answers_7d, 'answer')}` : 'no answers this week'),
      trendTile('Concepts mastered', `${t.mastered}/${t.concepts}`, change(tr.mastered_delta, {scale: 1, unit: ' more', period: days}),
        spark(recent.map((p) => p.mastered), 'Concepts mastered, last 14 days'), `${t.started} started`),
      trendTile('Answers, last 7 days', String(tr.answers_7d || 0), change(answersChange, {scale: 1, unit: '', period: 'vs the week before'}),
        spark(recent.map((p) => p.answers || 0), 'Answers each day, last 14 days'),
        `${plural(recent.filter((p) => p.answers).length, 'study day')} in the last 14`));
    return card({title: 'How it’s trending', sub: 'This week against the week before. The small lines show the last 14 days.',
      body: [outlookNote(t), h('div', {style: {marginTop: '14px'}}, tiles), race(t)]});
  }

  // --- pages: overview -------------------------------------------------------------------------
  function pageHead(title, sub, right, crumbs) {
    return h('div', {class: 'page-head'}, h('div', null, crumbs ? h('div', {class: 'crumbs'}, crumbs) : null,
      h('h1', null, title), sub ? h('div', {class: 'sub'}, sub) : null), right);
  }
  function greeting() {
    const hr = new Date().getHours();
    const name = (app.state.learner.name || '').split(' ')[0];
    const part = hr < 5 ? 'Hello' : hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening';
    return name ? `${part}, ${name}` : part;
  }
  function sessionStrip() {
    const live = liveSession();
    if (!live) return null;
    const t = live.track ? track(live.track) : null;
    const minutes = Math.round(live.minutes || 0);
    return h('div', {class: 'callout'}, h('span', {class: 'live is-live'}, h('span', {class: 'live-dot'})),
      h('div', null, h('b', null, 'Studying now'), t ? ` · ${shortTitle(t)}` : '', ` · started ${fmtTime(live.start)}`,
        h('div', {class: 'muted small'}, `${minutes} min active · ${plural(live.answers, 'answer')}`,
          isNum(live.accuracy) ? ` · ${pct(live.accuracy)} right` : '', ' · this page updates as answers are recorded')));
  }
  function nextExam() {
    const now = today();
    return tracks().filter((t) => t.status === 'active' && t.target_date && localDay(t.target_date) >= now)
      .sort((a, b) => localDay(a.target_date) - localDay(b.target_date))[0];
  }
  function heroCard() {
    const t = nextExam();
    if (!t) return null;
    const days = daysFrom(today(), localDay(t.target_date));
    const g = t.gate;
    return card({body: h('div', {class: 'hero'},
      h('div', {class: 'hero-figure'}, days === 0 ? 'Today' : plural(days, 'day')),
      h('div', {class: 'hero-label'}, days === 0 ? `is your ${examName(t)} exam` : `until your ${examName(t)} exam, ${fmtDay(localDay(t.target_date))}`,
        h('div', {class: 'small'}, isNum(t.predicted_score) ? `Predicted ${pct(t.predicted_score)}` : '',
          isNum(t.passing_score) ? ` against a ${pct(t.passing_score)} pass mark` : '',
          g ? ` · booking checks ${g.passed} of 3 met` : ''),
        (() => { const o = outlook(t); return o && t.trend && t.trend.enough ? h('div', {class: 'hero-trend'}, icon(o.icon, o.cls), o.title) : null; })()))});
  }
  function trackCard(t) {
    const p = pace(t);
    const nxt = t.next || {};
    const sub = t.target_date ? `Exam ${fmtDay(localDay(t.target_date))} · ${untilText(t.target_date)}` : 'No exam date set';
    const passing = isNum(t.passing_score) ? t.passing_score : null;
    const tr = t.trend || {};
    const top = passing != null ? [
      h('div', {class: 'hero hero-row'}, h('div', {class: 'big'}, pct(t.predicted_score)),
        h('div', {class: 'hero-label small'}, 'predicted exam score',
          (tr.answers_7d || tr.answers_prev) ? h('div', null, delta(tr.predicted_delta, {period: 'this week'})) : null),
        (tr.answers_7d || tr.answers_prev) ? sparkline((t.history || []).slice(-14).map((p) => p.predicted), 'Predicted score, last 14 days') : null),
      meter(t.predicted_score, [{at: passing, label: `Pass ${pct(passing)}`, align: 'end'},
        {at: passing + 0.07, label: `Book ${pct(passing + 0.07)}`, align: 'start'}],
        `Predicted ${pct(t.predicted_score)}, pass mark ${pct(passing)}`),
    ] : [h('div', {class: 'hero'}, h('div', {class: 'big'}, `${t.mastered}/${t.concepts}`),
      h('div', {class: 'hero-label small'}, 'concepts mastered'))];
    const counts = countsByStatus(t.concept_rows || []);
    return card({
      title: h('a', {href: link('track', t.slug)}, t.title), sub, actions: [badge(readiness(t))],
      body: [
        ...top,
        h('div', {class: 'kpis', style: {marginTop: '14px'}},
          miniStat('Coverage', `${t.started}/${t.concepts}`, pct(t.coverage)),
          miniStat('Mastered', `${t.mastered}/${t.concepts}`, `${counts.developing} developing`),
          miniStat('Due now', String(t.due), 'reviews'),
          miniStat('Mistakes', String(t.mistakes_open), 'to fix'),
          h('div', null, h('div', {class: 'stat-label'}, 'Active time'),
            h('div', {class: 'stat-value', style: {fontSize: '18px'}, 'data-active': `total:${t.slug}`}, durationText(((app.state.time || {}).tracks || {})[t.slug]?.total || 0)),
            h('div', {class: 'stat-sub'}, h('span', {'data-active': `today:${t.slug}`}, durationText(((app.state.time || {}).tracks || {})[t.slug]?.today || 0)), ' today'))),
        h('div', {style: {marginTop: '14px'}}, outlookNote(t) || h('div', {class: 'callout'}, icon(p.icon, p.tone),
          h('div', null, h('b', null, p.label), p.detail ? h('div', {class: 'muted small'}, p.detail) : null))),
        nxt.items ? h('div', {class: 'small muted', style: {marginTop: '12px'}}, 'Up next: ',
          [nxt.mistakes_due ? plural(nxt.mistakes_due, 'mistake') + ' to fix' : null,
            nxt.due_total ? plural(nxt.due_total, 'review') : null,
            nxt.new_available ? `${nxt.new_available} new to probe` : null].filter(Boolean).join(' · ') || 'nothing due') : null,
        studyButtons(t) || (t.status === 'active' ? cmdBox(`/polymath:learn ${t.slug}`) : null),
      ],
    });
  }
  function miniStat(label, value, sub) {
    return h('div', null, h('div', {class: 'stat-label'}, label), h('div', {class: 'stat-value', style: {fontSize: '18px'}}, value),
      sub ? h('div', {class: 'stat-sub'}, sub) : null);
  }
  function answerFeed(rows, limit) {
    if (!rows.length) return empty('No answers yet.', 'Start a session on the Study page and they appear here as you go.');
    return h('ul', {class: 'feed'}, rows.slice(0, limit).map((a) => {
      const r = RESULT[a.result] || RESULT.incorrect;
      const meta = [a.question, QTYPE[a.qtype] || a.qtype, a.difficulty ? `level ${a.difficulty}` : null,
        a.confidence ? `sure ${a.confidence}/5` : null].filter(Boolean).join(' · ');
      const tags = [a.targets ? `re-ask ${a.targets}` : null, a.mistake && !a.targets ? `mistake ${a.mistake}` : null,
        a.error && ERROR[a.error] ? ERROR[a.error] : null].filter(Boolean);
      return h('li', null, icon(r.icon, r.cls, r.label),
        h('div', null, h('a', {class: 'feed-title', href: link('concept', a.concept)}, a.title),
          h('div', {class: 'feed-meta'}, meta, ...tags.map((tg) => [' ', h('span', {class: 'tag'}, tg)]))),
        h('div', {class: 'feed-when', title: fmtWhen(a.ts)}, relTime(a.ts)));
    }));
  }
  function activityMap() {
    return new Map((app.state.activity || []).map((d) => [d.date, d]));
  }
  function heatCard(maxWeeks, title) {
    const L = app.state.learner;
    const days = activityMap();
    const rows = (app.state.activity || []).slice().reverse();
    return chartCard({
      id: `heat-${maxWeeks}`, title: title || 'Study days',
      sub: `${plural(L.streak, 'day')} in a row · ${plural(L.active_days, 'active day')} · ${plural(L.attempts, 'answer')}`,
      draw: (w) => heatmap(w, {days, end: today(), maxWeeks,
        summary: `Answers per day over the last ${maxWeeks} weeks; ${L.active_days} active days.`}),
      legend: heatLegend(),
      table: {columns: [{label: 'Day', cell: (r) => fmtDay(localDay(r.date))}, {label: 'Answers', num: true, cell: (r) => r.answers},
        {label: 'Active minutes', num: true, cell: (r) => r.minutes || '–'}], rows},
    });
  }
  function pageOverview() {
    const st = app.state;
    const list = tracks();
    const persona = (st.learner.persona || {}).name;
    return h('div', {class: 'stack'},
      pageHead(greeting(), [fmtLong(today()), persona ? ` · your tutor is ${persona}` : '']),
      sessionStrip(),
      heroCard(),
      timeCard(),
      (st.goals || []).length ? h('div', {class: 'grid-cards'}, st.goals.slice(0, 4).map(goalCard)) : null,
      list.length ? h('div', {class: 'grid-cards'}, list.filter((t) => t.status === 'active').map(trackCard))
        : card({body: [empty('No goals yet.', 'Say what you want to achieve, and Polymath works out how to get there.'),
          canStudy() ? h('div', {class: 'form-actions'}, h('a', {class: 'btn btn-primary', href: link('goals', 'new')}, 'Add a goal')) : null]}),
      h('div', {class: 'grid-2'},
        card({title: 'Latest answers', sub: 'Newest first', actions: [h('a', {class: 'btn btn-small', href: '#/activity'}, 'All')],
          body: answerFeed(st.recent || [], 8)}),
        heatCard(22, 'Study days')));
  }

  // --- pages: track --------------------------------------------------------------------------------
  const TABS = [['progress', 'Progress'], ['concepts', 'Concepts'], ['mistakes', 'Mistakes'], ['plan', 'Plan & notes'], ['settings', 'Settings']];
  function pageTrack(route) {
    const t = track(route.slug);
    if (!t) return card({body: empty('No such goal.', `There is no track called “${route.slug}”.`)});
    const e = t.exam || {};
    const facts = [e.questions ? `${e.questions} questions` : null, e.minutes ? `${e.minutes} minutes` : null,
      isNum(e.passing_score) ? `pass mark ${pct(e.passing_score)}` : null].filter(Boolean).join(' · ');
    const sub = h('div', null, t.goal || '',
      h('div', {class: 'small muted', style: {marginTop: '4px'}},
        t.target_date ? `Exam ${fmtDay(localDay(t.target_date))} (${untilText(t.target_date)})` : 'No exam date set',
        facts ? ` · ${facts}` : ''),
      e.verified === false ? h('details', {class: 'more small', style: {marginTop: '4px'}},
        h('summary', null, 'Exam facts not verified yet'), h('p', {class: 'muted', style: {marginTop: '6px', maxWidth: '72ch'}},
          e.verify_note || 'Check them against the official exam guide.')) : null);
    const tabs = h('nav', {class: 'tabs', 'aria-label': `${shortTitle(t)} sections`},
      TABS.map(([key, label]) => h('a', {href: link('track', t.slug, key), 'aria-current': route.tab === key ? 'page' : null}, label)));
    const body = {progress: trackProgress, concepts: trackConcepts, mistakes: trackMistakes, plan: trackPlan, settings: trackSettings}[route.tab] || trackProgress;
    return h('div', null, pageHead(t.title, sub, h('div', {class: 'head-actions'}, badge(readiness(t)), trackStudyButton(t)),
      h('a', {href: '#/'}, 'Overview')), tabs, body(t));
  }
  function trackProgress(t) {
    const passing = isNum(t.passing_score) ? t.passing_score : null;
    const p = pace(t);
    const counts = countsByStatus(t.concept_rows || []);
    const heroBody = passing != null ? [
      h('div', {class: 'hero'}, h('div', {class: 'hero-figure'}, pct(t.predicted_score)),
        h('div', {class: 'hero-label'}, 'predicted exam score', h('div', {class: 'small'}, `pass mark ${pct(passing)} · book at ${pct(passing + 0.07)}`))),
      h('div', {style: {marginTop: '14px'}}, meter(t.predicted_score, [{at: passing, label: `Pass ${pct(passing)}`, align: 'end'},
        {at: passing + 0.07, label: `Book ${pct(passing + 0.07)}`, align: 'start'}], `Predicted ${pct(t.predicted_score)}`)),
      h('p', {class: 'small muted', style: {marginTop: '6px'}}, 'An estimate from your mastery of every concept, weighted like the exam. Mock exams make it more reliable.'),
    ] : [h('div', {class: 'hero'}, h('div', {class: 'hero-figure'}, `${t.mastered}/${t.concepts}`),
      h('div', {class: 'hero-label'}, 'concepts mastered'))];
    return h('div', {class: 'stack'},
      card({body: heroBody}),
      h('div', {class: 'kpis'},
        stat('Coverage', pct(t.coverage), `${t.started} of ${t.concepts} concepts started`),
        stat('Mastered', h('span', null, String(t.mastered), h('small', null, ` / ${t.concepts}`)), `${counts.developing} developing, ${counts.learning} learning`),
        stat('Due for review', String(t.due), t.due ? 'start with these' : 'nothing due'),
        stat('Mistakes to fix', String(t.mistakes_open), 'open or fixing'),
        stat('Pace', (t.projection || {}).status === 'too early to tell' ? 'Too early' : capital((t.projection || {}).status || '–'), p.detail || 'needs a few days of answers')),
      momentumCard(t),
      h('div', {class: 'grid-2'}, gateCard(t), upNextCard(t)),
      readinessChart(t),
      accuracyChart(t),
      burnupChart(t),
      areaCard(t),
      h('div', {class: 'grid-2'}, forecastCard(t), mocksCard(t)));
  }
  const capital = (x) => String(x).charAt(0).toUpperCase() + String(x).slice(1);
  function gateCard(t) {
    const g = t.gate;
    if (!g) return card({title: 'Ready to book?', body: empty('This goal has no pass mark.', 'Add one to the syllabus to get a readiness check.')});
    return card({title: 'Ready to book?', sub: gateSub(g), body: [gateChecks(g, t), gateAction(t, g)]});
  }
  const gateSub = (g) => (g.met ? 'All three checks pass: book the exam.' : `${g.passed} of 3 checks pass. Book when all three do.`);
  function gateChecks(g, t) {
    const lines = g.checks.map((c) => {
      let value;
      if (c.id === 'mocks') {
        value = c.value.length ? c.value.map((v) => pct(v)).join(', ') : 'none yet';
        if (c.value.length === 1) value += ' (1 of 2)';
      } else value = pct(c.value);
      const label = {predicted: `Predicted score at least ${pct(c.target)}`, coverage: `Coverage at least ${pct(c.target)}`,
        mocks: `Last two full mock exams at least ${pct(c.target)}`}[c.id] || c.label;
      return h('li', null, icon(c.met ? 'check' : 'x', c.met ? 'is-good' : 'is-critical', c.met ? 'Met' : 'Not met'),
        h('span', null, label, h('span', {class: 'check-left'}, gateAdvice(c, t))), h('span', {class: 'check-value'}, value));
    });
    return h('ul', {class: 'checks'}, lines);
  }
  /* What is left before a booking check passes, in plain words. */
  function gateAdvice(c, t) {
    if (c.met) return 'Done.';
    if (c.id === 'predicted') return `${plural(Math.max(1, Math.round((c.target - c.value) * 100)), 'point')} to go.`;
    if (c.id === 'coverage') return `${plural(Math.max(1, Math.ceil(c.target * t.concepts - t.started - 1e-9)), 'more concept')} to start.`;
    const scores = c.value || [];
    const goal = `${pct(c.target)} or better`;
    if (!scores.length) return `Take two full mock exams; both need ${goal}.`;
    if (scores[scores.length - 1] >= c.target) return `One more mock at ${goal}.`;
    return `Your last mock was ${pct(scores[scores.length - 1])}: the next two need ${goal}.`;
  }
  function upNextCard(t) {
    const nxt = t.next;
    if (!nxt) return card({title: 'Up next', body: empty('Nothing planned.', '')});
    const items = (nxt.items || []).slice(0, 6);
    const mode = typeof nxt.mode === 'string' ? nxt.mode : '';
    return card({title: 'Up next', sub: `${capital(mode || 'mixed')} placement · ${pct(nxt.placement)} right on first tries`,
      body: [items.length ? h('ul', {class: 'feed is-tagged'}, items.map((it) => h('li', null,
        h('span', {class: 'tag'}, KIND[it.kind] || it.kind),
        h('div', null, h('a', {class: 'feed-title', href: link('concept', it.concept)}, it.title),
          h('div', {class: 'feed-meta'}, it.mistake ? `${it.mistake.id}: ${it.mistake.text || ERROR[it.mistake.error] || ''}` : (it.domain || ''))),
        h('div', {class: 'feed-when'}, `level ${it.difficulty}`)))) : empty('Nothing due right now.', 'Every concept is mastered or waiting for its review date.'),
      h('div', {class: 'small muted', style: {marginTop: '10px'}},
        `${plural(nxt.mistakes_due || 0, 'mistake')} to fix · ${plural(nxt.due_total || 0, 'review')} due · ${nxt.new_available || 0} new to probe (${nxt.new_quota || 0} a session)`),
      studyButtons(t) || (t.status === 'active' ? cmdBox(`/polymath:learn ${t.slug}`) : null)]});
  }
  function readinessChart(t) {
    let hist = (t.history || []).filter((p) => isNum(p.predicted)).map((p) => ({t: localDay(p.date), y: p.predicted, raw: p}));
    const passing = isNum(t.passing_score) ? t.passing_score : null;
    if (passing == null) return null;
    const start = t.start && hist.length && t.start.date === dayKey(hist[0].t) && isNum(t.start.predicted)
      ? {t: addDays(hist[0].t, -1), y: t.start.predicted, raw: {...t.start, due: 0}, start: true} : null;
    if (start) hist = [start, ...hist];
    const tr = t.trend || {};
    const projection = (tr.projection || []).map((p) => ({t: localDay(p.date), y: p.predicted}));
    const now = today();
    const exam = t.target_date ? localDay(t.target_date) : null;
    const x0 = hist.length ? hist[0].t : addDays(now, -1);
    const x1 = exam && exam > now ? exam : addDays(now, 1);
    const mocks = (t.mocks || []).map((m) => ({t: localDay(m.date), y: m.score, cls: 'd-mock', label: 'Mock exam'}));
    const values = hist.map((p) => p.y).concat(mocks.map((m) => m.y), projection.map((p) => p.y), [passing]);
    const yMin = Math.max(0, Math.floor((Math.min(...values) - 0.08) * 10) / 10);
    const yTicks = niceTicks(yMin, 1, 4).filter((v) => v >= yMin && v <= 1);
    const last = hist[hist.length - 1];
    const legend = (mocks.length || projection.length) ? h('div', {class: 'legend'},
      h('span', {class: 'key'}, h('i', {class: 'key-line'}), 'Predicted score'),
      projection.length ? h('span', {class: 'key'}, h('i', {class: 'key-line is-proj'}), 'Where this week\u2019s pace leads') : null,
      mocks.length ? h('span', {class: 'key'}, h('i', {class: 'key-dot'}), 'Mock exam') : null) : null;
    return chartCard({
      id: `ready-${t.slug}`, title: 'Readiness over time',
      sub: projection.length ? `Predicted score each day since you started. The dotted line follows your last week\u2019s pace to the exam on ${fmtShort(exam)}.`
        : exam ? `Predicted score each day, out to the exam on ${fmtShort(exam)}` : 'Predicted score each day',
      draw: (w) => lineChart(w, {
        x0, x1, yMin, yMax: 1, yTicks, yFmt: (v) => pct(v), height: 240,
        summary: last ? `Predicted score ${pct(last.y)} today against a ${pct(passing)} pass mark.` : 'No history yet.',
        series: [{key: 'accent', label: 'Predicted', cls: 's-accent', dot: 'd-accent', area: true, points: hist,
          fmt: (v) => pct(v), endLabel: (p) => pct(p.y), labelPos: 'above'},
        {key: 'proj', label: 'at this week\u2019s pace', cls: 's-proj', points: projection, fmt: (v) => pct(v),
          hideWhenCovered: true, endLabel: (p) => `\u2248${pct(p.y)}`, labelCls: 'proj-label'}],
        refs: [{y: passing, label: `Pass ${pct(passing)}`}, {y: passing + 0.07, label: `Book ${pct(passing + 0.07)}`}],
        dots: mocks,
        vlines: exam && exam > now ? [{t: now, label: 'Today'}, {t: exam, label: 'Exam'}] : [],
        extraRows: (time) => {
          const p = hist.find((q) => +q.t === time);
          if (p && p.start) return [{value: 'Your starting point', label: 'before the first answer'}];
          return p ? [{value: `${p.raw.mastered}/${t.concepts}`, label: 'mastered'}, {value: pct(p.raw.coverage), label: 'coverage'}] : [];
        },
      }),
      legend,
      foot: hist.length < 3 ? h('p', {class: 'small muted', style: {marginTop: '8px'}}, 'The line grows by a point a day as you answer questions.') : null,
      table: {columns: [{label: 'Day', cell: (r) => (r.start ? `${fmtDay(r.t)} (start)` : r.proj ? `${fmtDay(r.t)} (projected)` : fmtDay(r.t))},
        {label: 'Predicted', num: true, cell: (r) => pct(r.y)},
        {label: 'Coverage', num: true, cell: (r) => (r.proj ? '\u2013' : pct(r.raw.coverage))},
        {label: 'Mastered', num: true, cell: (r) => (r.proj ? '\u2013' : r.raw.mastered)},
        {label: 'Due', num: true, cell: (r) => (r.proj ? '\u2013' : r.raw.due)}],
      rows: [...projection.slice(1).filter((p, i, all) => i === all.length - 1 || i % 7 === 6).map((p) => ({...p, proj: true})).reverse(),
        ...hist.slice().reverse()]},
    });
  }
  /* Share of answers right: each study day as a dot, the 7-day average as the line. */
  function accuracyChart(t) {
    const hist = t.history || [];
    const total = hist.reduce((n, p) => n + (p.answers || 0), 0);
    if (!total) return null;
    const days = hist.map((p) => ({t: localDay(p.date), n: p.answers || 0, pts: p.points || 0}));
    const rolling = [];
    days.forEach((d, i) => {
      const win = days.slice(Math.max(0, i - 6), i + 1);
      const n = win.reduce((a, w) => a + w.n, 0);
      if (n) rolling.push({t: d.t, y: win.reduce((a, w) => a + w.pts, 0) / n, n});
    });
    const daily = days.filter((d) => d.n).map((d) => ({t: d.t, y: d.pts / d.n, cls: 'd-day', r: 3, key: 'day',
      label: `right that day (${plural(d.n, 'answer')})`}));
    const passing = isNum(t.passing_score) ? t.passing_score : null;
    const x0 = days[0].t, x1 = days.length > 1 ? days[days.length - 1].t : addDays(days[0].t, 1);
    const lowest = Math.min(...daily.map((d) => d.y), ...rolling.map((r) => r.y), passing != null ? passing : 1);
    const yMin = Math.max(0, Math.floor((lowest - 0.1) * 5) / 5);
    const tr = t.trend || {};
    return chartCard({
      id: `accuracy-${t.slug}`, title: 'Right answers',
      sub: passing != null ? `Share of your answers that were right: each study day, and the 7-day average. The exam needs ${pct(passing)}.`
        : 'Share of your answers that were right: each study day, and the 7-day average.',
      draw: (w) => lineChart(w, {
        x0, x1, yMin, yMax: 1, yTicks: niceTicks(yMin, 1, 4).filter((v) => v >= yMin && v <= 1), yFmt: (v) => pct(v), height: 210,
        summary: `${pct(tr.accuracy_7d)} of answers right over the last 7 days.`,
        series: [{key: 'accent', label: '7-day average', cls: 's-accent', dot: 'd-accent', points: rolling, fmt: (v) => pct(v),
          endLabel: (p) => pct(p.y)}],
        dots: daily,
        refs: passing != null ? [{y: passing, label: `Pass ${pct(passing)}`}] : [],
      }),
      legend: h('div', {class: 'legend'}, h('span', {class: 'key'}, h('i', {class: 'key-line'}), '7-day average'),
        h('span', {class: 'key'}, h('i', {class: 'key-dot is-day'}), 'One study day')),
      table: {columns: [{label: 'Day', cell: (r) => fmtDay(r.t)}, {label: 'Answers', num: true, cell: (r) => r.n},
        {label: 'Right', num: true, cell: (r) => pct(r.pts / r.n)}], rows: days.filter((d) => d.n).reverse()},
    });
  }
  function burnupChart(t) {
    const hist = (t.history || []).map((p) => ({t: localDay(p.date), mastered: p.mastered, started: p.started, raw: p}));
    const now = today();
    const exam = t.target_date ? localDay(t.target_date) : null;
    const total = t.concepts;
    const x0 = hist.length ? hist[0].t : addDays(now, -1);
    const x1 = exam && exam > now ? exam : addDays(now, 1);
    const target = [];
    if (exam && exam > now && t.mastered < total) {
      const left = daysFrom(now, exam);
      const reserve = Math.min(7, Math.max(0, Math.floor(left * 0.15)));
      const finish = addDays(now, Math.max(1, left - reserve));
      target.push({t: now, y: t.mastered}, {t: finish, y: total});
      if (+finish < +exam) target.push({t: exam, y: total});
    }
    const velocity = (t.projection || {}).velocity_per_day;
    const current = [];
    if (exam && exam > now && isNum(velocity) && velocity > 0 && t.mastered < total) {
      const span = Math.min(daysFrom(now, exam), Math.ceil((total - t.mastered) / velocity));
      current.push({t: now, y: t.mastered}, {t: addDays(now, span), y: Math.min(total, t.mastered + velocity * span)});
    }
    const yTicks = niceTicks(0, total, 4).filter((v) => v <= total);
    const legend = h('div', {class: 'legend'},
      h('span', {class: 'key'}, h('i', {class: 'key-line'}), 'Mastered'),
      h('span', {class: 'key'}, h('i', {class: 'key-line is-gray'}), 'Started'),
      current.length ? h('span', {class: 'key'}, h('i', {class: 'key-line is-proj'}), `Your pace (${fixed(velocity)} a day)`) : null,
      target.length ? h('span', {class: 'key'}, h('i', {class: 'key-line is-dash'}), 'Pace needed for the exam date') : null);
    return chartCard({
      id: `burnup-${t.slug}`, title: 'Concepts mastered',
      sub: `Out of ${total}. Mastered means known well and recalled a day or more later.`,
      draw: (w) => lineChart(w, {
        x0, x1, yMin: 0, yMax: total, yTicks, yFmt: (v) => String(v), height: 220,
        summary: `${t.mastered} of ${total} concepts mastered, ${t.started} started.`,
        series: [
          {key: 'gray', label: 'Started', cls: 's-gray', dot: 'd-gray', points: hist.map((p) => ({t: p.t, y: p.started})),
            fmt: (v) => String(v), endLabel: (p) => (p.y ? `${p.y} started` : ''), labelPos: 'above'},
          {key: 'accent', label: 'Mastered', cls: 's-accent', dot: 'd-accent', area: true,
            points: hist.map((p) => ({t: p.t, y: p.mastered})), fmt: (v) => String(v), endLabel: (p) => (p.y ? `${p.y} mastered` : ''), labelPos: 'below'},
          {key: 'proj', label: 'at your pace', cls: 's-proj', points: current, fmt: (v) => fixed(v, 0), hideWhenCovered: true},
          {key: 'ref', label: 'Needed', cls: 's-target', points: target, noTooltip: true},
        ],
        refs: [{y: total, label: `All ${total}`}],
        vlines: exam && exam > now ? [{t: exam, label: 'Exam'}] : [],
      }),
      legend,
      table: {columns: [{label: 'Day', cell: (r) => fmtDay(r.t)}, {label: 'Mastered', num: true, cell: (r) => r.mastered},
        {label: 'Started', num: true, cell: (r) => r.started}], rows: hist.slice().reverse()},
    });
  }
  function areaCard(t) {
    const rows = t.concept_rows || [];
    return card({title: 'Mastery by area', sub: 'Each bar is one exam area, split by where its concepts stand. Weight is the share of the exam.',
      body: [h('div', {class: 'rows'}, (t.domains || []).map((d) => {
        const c = countsByStatus(rows.filter((r) => r.domain === d.id));
        const parts = STATUS_ORDER.map((k) => ({key: k, label: STATUS[k].label, value: c[k]}));
        return h('div', {class: 'row'},
          h('div', {class: 'row-name'}, h('b', null, d.name), h('span', {class: 'small muted'}, `${pct(d.weight)} of the exam · ${plural(d.concepts, 'concept')}`)),
          h('div', {class: 'row-bar'}, segBar(parts, d.concepts)),
          h('div', {class: 'row-figure'}, h('b', null, `${d.mastered}/${d.concepts}`), ' mastered',
            h('div', {class: 'small muted'}, `mastery ${pct(d.mastery)}${d.due ? ` · ${d.due} due` : ''}`)));
      })), h('div', {style: {marginTop: '16px'}}, statusLegend())]});
  }
  function forecastCard(t) {
    const now = today();
    const bars = (t.forecast || []).map((f, i) => {
      const d = localDay(f.date);
      const label = i === 0 ? 'Today' : d.toLocaleDateString(undefined, {weekday: 'narrow'});
      return {value: f.due, label, tip: {title: i === 0 ? `Today, ${fmtShort(d)}` : fmtDay(d),
        rows: [{value: plural(f.due, 'review'), label: i === 0 ? 'due (includes overdue)' : 'coming due'}]}, date: d};
    });
    const total = bars.reduce((sum, b) => sum + b.value, 0);
    if (!total) {
      return card({title: 'Reviews coming due', body: empty('Nothing scheduled yet.',
        'Every concept you answer gets a review date; they show here, day by day.')});
    }
    return chartCard({
      id: `forecast-${t.slug}`, title: 'Reviews coming due', sub: `${plural(total, 'review')} in the next two weeks; today includes anything overdue`,
      draw: (w) => columnChart(w, {bars, summary: `${bars[0] ? bars[0].value : 0} reviews due today, ${total} over two weeks.`}),
      table: {columns: [{label: 'Day', cell: (r) => (+r.date === +now ? 'Today' : fmtDay(r.date))}, {label: 'Reviews', num: true, cell: (r) => r.value}], rows: bars},
    });
  }
  function mocksCard(t) {
    const mocks = (t.mocks || []).slice().reverse();
    const passing = isNum(t.passing_score) ? t.passing_score : null;
    const table = mocks.length ? dataTable([
      {label: 'Day', cell: (m) => fmtDay(localDay(m.date))},
      {label: 'Score', num: true, cell: (m) => pct(m.score)},
      {label: 'Against pass', num: true, cell: (m) => (passing == null ? '–' : `${m.score >= passing ? '+' : ''}${Math.round((m.score - passing) * 100)} pts`)},
    ], mocks, 'Mock exams') : empty('No mock exams yet.', 'Take one with /polymath:mock-exam, or log one you took elsewhere.');
    return card({title: 'Mock exams', sub: 'The last three, newest first', body: [table, writable() ? mockForm(t) : null]});
  }
  function mockForm(t) {
    const id = `mock-${t.slug}`;
    const areaInputs = (t.domains || []).map((d) => field(id, `area-${d.id}`, `${d.name} (%)`,
      h('input', {class: 'input', type: 'number', min: '0', max: '100', step: '1', inputmode: 'numeric'})));
    return h('details', {class: 'more', style: {marginTop: '12px'}, open: !!app.drafts[id]},
      h('summary', null, 'Log a mock exam'),
      h('form', {onsubmit: submitForm(id, async (data) => {
        const byDomain = {};
        for (const d of t.domains || []) { const v = data.get(`area-${d.id}`); if (v !== '' && v != null) byDomain[d.id] = Number(v); }
        await post(`api/tracks/${encodeURIComponent(t.slug)}/mocks`, {
          score: Number(data.get('score')), questions: numberOrNull(data.get('questions')),
          minutes: numberOrNull(data.get('minutes')), by_domain: byDomain});
      }, 'Mock exam logged.')},
      h('div', {class: 'form-grid'},
        field(id, 'score', 'Score (%)', h('input', {class: 'input', type: 'number', min: '0', max: '100', step: '1', required: true, inputmode: 'numeric'})),
        field(id, 'questions', 'Questions', h('input', {class: 'input', type: 'number', min: '1', step: '1', inputmode: 'numeric'})),
        field(id, 'minutes', 'Minutes', h('input', {class: 'input', type: 'number', min: '1', step: '1', inputmode: 'numeric'}))),
      areaInputs.length ? h('p', {class: 'form-note', style: {margin: '12px 0 8px'}}, 'By area (optional)') : null,
      h('div', {class: 'form-grid'}, areaInputs),
      h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-primary', type: 'submit'}, 'Log it'))));
  }
  function conceptColumns(withArea) {
    return [
      {key: 'title', label: 'Concept', sort: (r) => r.title.toLowerCase(), cls: 'wrap',
        cell: (r) => [h('a', {href: link('concept', r.concept)}, r.title), h('div', {class: 'mono muted'}, r.concept)]},
      withArea ? {key: 'area', label: 'Area', sort: (r) => r.domain_name, cell: (r) => r.domain_name} : null,
      {key: 'status', label: 'Status', sort: (r) => STATUS[r.status] ? STATUS[r.status].order : -1, desc: true, cell: (r) => pill(r.status)},
      {key: 'mastery', label: 'Mastery', sort: (r) => r.mastery, desc: true, cell: (r) => hoverTip(
        h('div', {class: 'mini-meter', tabindex: '0'}, barH(r.mastery, `Mastery ${pct(r.mastery)}`), h('span', {class: 'num'}, pct(r.mastery))),
        () => ({title: r.title, rows: [{value: pct(r.mastery), label: 'mastery now'}, {value: pct(r.knowledge), label: 'knowledge'},
          {value: pct(r.memory), label: 'memory today'}]}))},
      {key: 'attempts', label: 'Answers', num: true, sort: (r) => r.attempts, desc: true, cell: (r) => r.attempts || '–'},
      {key: 'accuracy', label: 'Right', num: true, sort: (r) => r.accuracy, desc: true, cell: (r) => pct(r.accuracy)},
      {key: 'next', label: 'Next review', sort: (r) => r.next_review, cell: (r) => (r.status === 'due' ? 'now' : r.next_review ? relTime(r.next_review) : '–')},
      {key: 'mistakes', label: 'Mistakes', num: true, sort: (r) => r.open_mistakes, desc: true, cell: (r) => r.open_mistakes || '–'},
    ].filter(Boolean);
  }
  function trackConcepts(t) {
    const f = app.ui.filter;
    const rows = (t.concept_rows || []).filter((r) => (!f.area || r.domain === f.area) && (!f.status || r.status === f.status)
      && (!f.q || `${r.title} ${r.concept}`.toLowerCase().includes(f.q.toLowerCase())));
    const search = h('input', {class: 'input', type: 'search', placeholder: 'Find a concept', value: f.q, 'aria-label': 'Find a concept',
      oninput: (e) => { f.q = e.target.value; rerenderTable(); }});
    const area = h('select', {class: 'input', 'aria-label': 'Area', onchange: (e) => { f.area = e.target.value; render(); }},
      h('option', {value: ''}, 'All areas'), (t.domains || []).map((d) => h('option', {value: d.id, selected: f.area === d.id}, d.name)));
    const counts = countsByStatus(t.concept_rows || []);
    const chips = h('div', {class: 'chips', role: 'group', 'aria-label': 'Status'},
      [['', `All ${t.concepts}`], ...STATUS_ORDER.map((k) => [k, `${STATUS[k].label} ${counts[k]}`])].map(([k, label]) =>
        h('button', {class: 'chip', type: 'button', 'aria-pressed': String(f.status === k), onclick: () => { f.status = k; render(); }}, label)));
    const holder = h('div', null, rows.length ? sortableTable('concepts', conceptColumns(true), rows, `${t.title} concepts`)
      : empty('No concepts match.', ''));
    function rerenderTable() {
      const again = (t.concept_rows || []).filter((r) => (!f.area || r.domain === f.area) && (!f.status || r.status === f.status)
        && (!f.q || `${r.title} ${r.concept}`.toLowerCase().includes(f.q.toLowerCase())));
      holder.replaceChildren(again.length ? sortableTable('concepts', conceptColumns(true), again, `${t.title} concepts`) : empty('No concepts match.', ''));
    }
    return card({body: [h('div', {class: 'filters'}, search, area), h('div', {class: 'filters'}, chips), holder]});
  }
  function mistakeTable(rows) {
    if (!rows.length) return empty('No mistakes in this view.', 'Every real miss lands here, and leaves once you get it right a day later.');
    return sortableTable('mistakes', [
      {key: 'id', label: 'ID', sort: (m) => Number(String(m.id).slice(1)), desc: true, cell: (m) => h('span', {class: 'mono'}, m.id)},
      {key: 'status', label: 'Status', sort: (m) => ({open: 0, fixing: 1, resolved: 2}[m.status]), cell: (m) => mistakeStatus(m)},
      {key: 'concept', label: 'Concept', sort: (m) => m.title.toLowerCase(), cls: 'wrap', cell: (m) => h('a', {href: link('concept', m.concept)}, m.title)},
      {key: 'what', label: 'What went wrong', cls: 'wrap', cell: (m) => [m.belief ? [h('span', {class: 'muted'}, 'Believed: '), m.belief]
        : m.question ? [h('span', {class: 'muted'}, 'Missed: '), m.question] : (m.text || '\u2013'),
        m.confused_with ? h('div', {class: 'small muted'}, `confused with ${m.confused_with}`) : null]},
      {key: 'type', label: 'Type', sort: (m) => m.error || '', cell: (m) => ERROR[m.error] || m.error || '–'},
      {key: 'misses', label: 'Misses', num: true, sort: (m) => m.misses, desc: true, cell: (m) => m.misses},
      {key: 'last', label: 'Last missed', sort: (m) => m.last_miss, desc: true, cell: (m) => relTime(m.last_miss)},
      {key: 'next', label: 'Check again', sort: (m) => m.next_check || '9', cell: (m) => (m.status === 'resolved' ? '–' : m.due ? 'now' : relTime(m.next_check))},
    ], rows, 'Mistakes');
  }
  function inTrack(m, t) { return (m.tracks || []).includes(t.slug); }
  function trackMistakes(t) {
    const all = (app.state.mistakes || []).filter((m) => inTrack(m, t));
    const open = all.filter((m) => m.status !== 'resolved');
    return h('div', {class: 'stack'},
      card({title: 'Mistakes to fix', sub: 'Open and fixing. Fixing means right once; it is checked again a day after the miss.', body: mistakeTable(open)}),
      card({title: 'Resolved', sub: 'Got right a day or more after the miss', body: mistakeTable(all.filter((m) => m.status === 'resolved'))}));
  }
  function noteForm(id, url, label, extra) {
    return h('form', {class: 'stack', style: {gap: '10px', marginTop: '14px'}, onsubmit: submitForm(id, async (data) => {
      const body = {text: data.get('text')};
      if (data.get('section')) body.section = data.get('section');
      await post(url, body);
    }, 'Note added.')},
    extra || null,
    field(id, 'text', label, h('textarea', {class: 'input', rows: 3, required: true, maxlength: 2000})),
    h('div', {class: 'form-actions', style: {marginTop: 0}}, h('button', {class: 'btn btn-primary', type: 'submit'}, 'Add note')));
  }
  function trackPlan(t) {
    return h('div', {class: 'grid-2'},
      card({title: 'Plan', sub: 'plan.md: what the tutor planned for this goal', body: t.plan_md ? markdown(t.plan_md)
        : empty('No plan yet.', 'The tutor writes one when you set a date.')}),
      card({title: 'Notes', sub: 'notes.md: your and the tutor’s running notes', body: [
        t.notes_md ? markdown(t.notes_md) : empty('No notes yet.', ''),
        writable() ? noteForm(`note-${t.slug}`, `api/tracks/${encodeURIComponent(t.slug)}/notes`, 'Add a dated note') : null]}));
  }
  function trackSettings(t) {
    const id = `settings-${t.slug}`;
    const info = dataTable([{label: 'Setting', cell: (r) => r[0]}, {label: 'Value', cell: (r) => r[1]}], [
      ['Track id', h('span', {class: 'mono'}, t.slug)],
      ['Kind', t.kind || '–'],
      ['Status', capital(t.status)],
      ['Exam date', t.target_date ? fmtDay(localDay(t.target_date)) : 'not set'],
      ['Daily minutes', t.daily_minutes || 'not set'],
      ['Created', t.created ? fmtWhen(t.created) : '–'],
      ['Sources', (t.sources || []).length ? h('div', null, t.sources.map((u) => h('div', null, extLink(u)))) : '–'],
    ]);
    const form = writable() ? h('form', {onsubmit: submitForm(id, async (data) => {
      await post(`api/tracks/${encodeURIComponent(t.slug)}/settings`, {
        target_date: data.get('target_date') || null, status: data.get('status'), daily_minutes: numberOrNull(data.get('daily_minutes'))});
    }, 'Settings saved.')},
    h('div', {class: 'form-grid'},
      field(id, 'target_date', 'Exam date', h('input', {class: 'input', type: 'date', value: t.target_date || ''})),
      field(id, 'status', 'Status', h('select', {class: 'input'}, ['active', 'paused', 'done'].map((v) =>
        h('option', {value: v, selected: t.status === v}, capital(v))))),
      // Any positive number of minutes, as the server takes it: a stored 7.5 (set from the command line) must
      // never make the whole form invalid. The server refuses 0 itself.
      field(id, 'daily_minutes', 'Minutes a day', h('input', {class: 'input', type: 'number', min: '0', max: '1440', step: 'any',
        inputmode: 'decimal', value: t.daily_minutes || ''}))),
    h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-primary', type: 'submit'}, 'Save'),
      h('span', {class: 'form-note'}, 'The syllabus itself changes through Claude Code (edit it and re-import).')))
      : h('p', {class: 'muted'}, app.snapshot ? 'This is a snapshot, so it can’t change anything.' : 'This dashboard was started read-only.');
    return h('div', {class: 'grid-2'}, card({title: 'Change', body: form}), card({title: 'About this goal', body: info}));
  }

  // --- pages: concept ------------------------------------------------------------------------------
  function loadDetail(key, url, fallback) {
    if (app.snapshot) return {data: fallback()};
    const cacheKey = `${key}|${app.state.app.data_version}`;
    const hit = app.cache.get(cacheKey);
    if (hit) return hit;
    const entry = {loading: true};
    app.cache.set(cacheKey, entry);
    getJSON(url).then((data) => { entry.loading = false; entry.data = data; renderSoon(); })
      .catch((err) => { entry.loading = false; entry.error = err.message || String(err); renderSoon(); });
    return entry;
  }
  function pageConcept(route) {
    const entry = loadDetail(`concept:${route.id}`, `api/concept/${encodeURIComponent(route.id)}`,
      () => (app.state.concept_details || {})[route.id]);
    if (entry.loading) return h('p', {class: 'loading'}, 'Loading…');
    const c = entry.data;
    if (!c) return card({body: empty('No such concept.', entry.error || `Nothing is known about “${route.id}”.`)});
    const t = (c.tracks || []).map(track).find(Boolean);
    const crumbs = t ? [h('a', {href: '#/'}, 'Overview'), ' / ', h('a', {href: link('track', t.slug, 'concepts')}, shortTitle(t))] : h('a', {href: '#/'}, 'Overview');
    const docs = (c.docs || []).filter(safeHref);
    const miniList = (rows, none) => (rows.length ? h('ul', {class: 'feed'}, rows.map((r) => h('li', null, h('i', {class: `key-swatch lvl-${r.status}`, style: {marginTop: '6px'}}),
      h('div', null, h('a', {class: 'feed-title', href: link('concept', r.concept)}, r.title), h('div', {class: 'feed-meta'}, STATUS[r.status] ? STATUS[r.status].label : r.status)),
      h('div', {class: 'feed-when num'}, pct(r.mastery))))) : h('p', {class: 'muted small'}, none));
    const canVoid = writable();
    const attempts = c.answers || [];
    const columns = [
      {label: 'When', cell: (a) => h('span', {title: fmtWhen(a.ts)}, relTime(a.ts))},
      {label: 'Result', cell: (a) => { const r = RESULT[a.result] || RESULT.incorrect; return h('span', {class: `pill ${r.cls}`}, icon(r.icon), r.label); }},
      {label: 'Question', cls: 'wrap', cell: (a) => [a.question || '–', a.misconception ? h('div', {class: 'small muted'}, `Belief: ${a.misconception}`) : null]},
      {label: 'Type', cell: (a) => QTYPE[a.qtype] || a.qtype || '–'},
      {label: 'Level', num: true, cell: (a) => a.difficulty || '–'},
      {label: 'Sure', num: true, cell: (a) => (a.confidence ? `${a.confidence}/5` : '–')},
      {label: 'ID', cell: (a) => [h('span', {class: 'mono'}, a.id || '–'), a.targets ? h('div', {class: 'small muted'}, `re-asks ${a.targets}`) : null,
        a.mistake && !a.targets ? h('div', {class: 'small muted'}, `opened ${a.mistake}`) : null]},
    ];
    if (canVoid) columns.push({label: '', cell: (a) => (a.voided ? h('span', {class: 'small muted'}, 'voided') : a.id ? h('button', {
      class: 'btn btn-small', type: 'button', title: 'Remove this answer if its question or answer key was wrong',
      onclick: async () => {
        const reason = window.prompt(`Void ${a.id}? It stops counting toward your mastery. Why (optional)?`, 'answer key was wrong');
        if (reason === null) return;
        try { await post('api/void', {attempt: a.id, reason}); toast(`${a.id} voided.`); await refresh(); }
        catch (err) { toast(err.message || String(err), true); }
      }}, 'Void') : null)});
    return h('div', {class: 'stack'},
      pageHead(c.title, [h('span', {class: 'mono'}, c.concept), (c.tracks || []).length
        ? ` \u00b7 in ${c.tracks.map((s) => (track(s) ? shortTitle(track(s)) : s)).join(', ')}` : ''],
      h('div', {class: 'head-actions'}, pill(c.status), conceptQuiz(c, t)), crumbs),
      canStudy() ? attachLearn(h('section', {class: 'card'}), `concept:${c.concept}`, [], {track: t && t.slug, concept: c.concept}, c.title) : null,
      h('div', {class: 'kpis'},
        stat('Mastery', pct(c.mastery), 'knowledge × memory'),
        stat('Knowledge', pct(c.knowledge), c.inferred ? 'credited from harder answers' : 'how likely you know it'),
        stat('Memory today', pct(c.memory), 'fades until the next review'),
        stat('Answers', String(c.attempts), isNum(c.accuracy) ? `${pct(c.accuracy)} right` : 'none yet'),
        stat('Next review', c.status === 'due' ? 'Now' : c.next_review ? relTime(c.next_review) : '–', c.next_review ? fmtDay(new Date(c.next_review)) : ''),
        stat('Next question', `Level ${c.difficulty}`, 'of 5')),
      card({title: 'What to know', body: [c.objective ? h('p', null, c.objective) : h('p', {class: 'muted'}, 'No objective recorded.'),
        docs.length ? h('div', {class: 'small', style: {marginTop: '10px'}}, 'Official docs: ', docs.map((u, i) => [i ? ' · ' : '',
          h('a', {href: u, target: '_blank', rel: 'noopener noreferrer'}, new URL(u).hostname.replace(/^www\./, ''), ' ', icon('out'))])) : null,
        (c.misconceptions || []).length ? h('div', {style: {marginTop: '12px'}}, h('b', null, 'Wrong beliefs seen'),
          h('ul', {class: 'md'}, c.misconceptions.map((x) => h('li', null, x)))) : null]}),
      h('div', {class: 'grid-2'},
        card({title: 'Builds on', sub: 'Prerequisites', body: miniList(c.prereqs || [], 'Nothing: this is a starting point.')}),
        card({title: 'Leads to', sub: 'Concepts that build on this one', body: miniList(c.dependents || [], 'Nothing yet in your goals.')})),
      (c.mistakes || []).length ? card({title: 'Mistakes on this concept', body: mistakeTable(c.mistakes)}) : null,
      card({title: 'Answers', sub: `${plural(c.answer_count || attempts.length, 'answer')}, newest first${canVoid ? '. Void one if its answer key was wrong.' : ''}`,
        body: attempts.length ? dataTable(columns, attempts.map((a) => ({...a, _cls: a.voided ? 'is-voided' : ''})), 'Answers on this concept')
          : empty('Not asked yet.', 'It will be probed in a coming session.')}));
  }

  // --- pages: mistakes, activity, you -------------------------------------------------------------
  function pageMistakes() {
    const all = app.state.mistakes || [];
    const ui = app.ui;
    const counts = {open: 0, fixing: 0, resolved: 0};
    all.forEach((m) => { counts[m.status] = (counts[m.status] || 0) + 1; });
    const rows = all.filter((m) => (ui.mistakes === 'all' || (ui.mistakes === 'unresolved' ? m.status !== 'resolved' : m.status === ui.mistakes))
      && (!ui.mistakesTrack || (m.tracks || []).includes(ui.mistakesTrack)));
    const filters = [['unresolved', `To fix ${counts.open + counts.fixing}`], ['open', `Open ${counts.open}`], ['fixing', `Fixing ${counts.fixing}`],
      ['resolved', `Resolved ${counts.resolved}`], ['all', `All ${all.length}`]];
    const list = tracks();
    return h('div', {class: 'stack'},
      pageHead('Mistakes', 'Every real miss, until you get it right a day or more later. Slips and traps are counted as exam technique instead.',
        mistakesAction()),
      h('div', {class: 'kpis'}, stat('Open', String(counts.open), 'missed, not yet re-asked'), stat('Fixing', String(counts.fixing), 'right once; checked again tomorrow'),
        stat('Resolved', String(counts.resolved), 'right a day or more later')),
      card({body: [h('div', {class: 'filters'},
        h('div', {class: 'chips', role: 'group', 'aria-label': 'Status'}, filters.map(([k, label]) => h('button', {class: 'chip', type: 'button',
          'aria-pressed': String(ui.mistakes === k), onclick: () => { ui.mistakes = k; render(); }}, label))),
        list.length > 1 ? h('select', {class: 'input', 'aria-label': 'Goal', onchange: (e) => { ui.mistakesTrack = e.target.value; render(); }},
          h('option', {value: ''}, 'All goals'), list.map((t) => h('option', {value: t.slug, selected: ui.mistakesTrack === t.slug}, shortTitle(t)))) : null),
      mistakeTable(rows)]}));
  }
  function sessionsCard() {
    const rows = app.state.sessions || [];
    const body = rows.length ? dataTable([
      {label: 'Started', cell: (x) => (x.start ? h('span', {title: fmtWhen(x.start)}, fmtWhen(x.start)) : '–')},
      {label: 'Goal', cell: (x) => { const t = x.track && track(x.track); return t ? h('a', {href: link('track', t.slug)}, shortTitle(t)) : (x.track || 'mixed'); }},
      {label: 'Where', cell: (x) => (x.where === 'dashboard' ? 'Dashboard' : 'Claude Code')},
      {label: 'Minutes', num: true, cell: (x) => (x.status === 'live' ? (isNum(x.minutes) ? `${Math.round(x.minutes)} so far` : '–') : isNum(x.minutes) ? Math.round(x.minutes) : '–')},
      {label: 'Answers', num: true, cell: (x) => x.answers},
      {label: 'Right', num: true, cell: (x) => pct(x.accuracy)},
      {label: 'Summary', cls: 'wrap', cell: (x) => (x.status === 'live' ? h('span', {class: 'pill is-good'}, icon('dot'), 'In progress')
        : x.status === 'unclosed' ? h('span', {class: 'muted'}, 'Not closed') : x.summary || '–')},
    ], rows, 'Sessions') : empty('No sessions yet.', '');
    return card({title: 'Sessions', sub: 'Newest first', body});
  }
  function answersCard() {
    if (app.snapshot) {
      return card({title: 'Answers', sub: `The latest ${plural((app.state.recent || []).length, 'answer')} in this snapshot`, body: answerFeed(app.state.recent || [], 400)});
    }
    const offset = app.ui.answersOffset;
    const entry = loadDetail(`answers:${offset}`, `api/attempts?offset=${offset}&limit=25`, () => null);
    if (entry.loading) return card({title: 'Answers', body: h('p', {class: 'loading'}, 'Loading…')});
    if (!entry.data) return card({title: 'Answers', body: empty('Couldn’t load answers.', entry.error || '')});
    const d = entry.data;
    const pager = h('div', {class: 'form-actions'},
      h('button', {class: 'btn btn-small', type: 'button', disabled: offset === 0, onclick: () => { app.ui.answersOffset = Math.max(0, offset - d.limit); render(); }}, 'Newer'),
      h('span', {class: 'form-note'}, d.total ? `${offset + 1}–${Math.min(d.total, offset + d.items.length)} of ${d.total}` : ''),
      h('button', {class: 'btn btn-small', type: 'button', disabled: offset + d.limit >= d.total, onclick: () => { app.ui.answersOffset = offset + d.limit; render(); }}, 'Older'));
    return card({title: 'Answers', sub: 'Every recorded answer, newest first', body: [answerFeed(d.items.filter((a) => !a.voided), 25), pager]});
  }
  function pageActivity() {
    const L = app.state.learner;
    return h('div', {class: 'stack'},
      pageHead('Activity', 'Study days, sessions and every answer'),
      h('div', {class: 'kpis'}, stat('Streak', plural(L.streak, 'day')), stat('Active days', String(L.active_days)),
        stat('Answers', String(L.attempts), isNum(L.accuracy) ? `${pct(L.accuracy)} right` : ''),
        stat('Hours in sessions', fixed(L.session_hours, 1))),
      heatCard(53, 'Study days, last year'),
      sessionsCard(),
      answersCard());
  }
  function pageYou() {
    const L = app.state.learner;
    const prefs = L.preferences || {};
    const persona = L.persona || {};
    const enough = (n) => n >= 5;
    const levels = (L.calibration_levels || []).filter((r) => r.n);
    const calRows = levels.length ? h('div', {class: 'rows'}, levels.map((r) => h('div', {class: 'row'},
      h('div', {class: 'row-name'}, h('b', null, `Sure ${r.confidence}/5`), h('span', {class: 'small muted'}, plural(r.n, 'answer'))),
      h('div', {class: 'row-bar'}, hoverTip(h('div', {class: 'dotrow', tabindex: '0', role: 'img', 'aria-label': `When ${r.confidence}/5 sure: ${pct(r.accuracy)} right, ${pct(r.expected)} expected`},
        h('div', {class: 'dotrow-track'}), h('div', {class: 'dotrow-expect', style: {left: `${r.expected * 100}%`}}),
        h('div', {class: 'dotrow-dot', style: {left: `${clamp01(r.accuracy) * 100}%`}})),
      () => ({title: `When you said ${r.confidence}/5`, rows: [{key: 'accent', value: pct(r.accuracy), label: 'right'}, {key: 'ref', value: pct(r.expected), label: 'what that confidence implies'}]}))),
      h('div', {class: 'row-figure'}, h('b', null, pct(r.accuracy)), h('div', {class: 'small muted'}, `expected ${pct(r.expected)}`))))) : empty('No confidence ratings yet.', '');
    const cal = L.calibration;
    const calNote = cal ? (cal.gap > 0.1 ? `Overconfident: you say ${pct(cal.stated)} sure on average and score ${pct(cal.actual)}.`
      : cal.gap < -0.1 ? `Underconfident: you score ${pct(cal.actual)} while saying ${pct(cal.stated)} sure. Trust yourself more.`
        : `Well calibrated: ${pct(cal.stated)} sure on average, ${pct(cal.actual)} right.`) : 'Needs about 8 rated answers.';
    const accuracyRows = (entries, name) => {
      const list = Object.entries(entries || {});
      return list.length ? h('div', {class: 'rows'}, list.map(([k, v]) => h('div', {class: 'row'},
        h('div', {class: 'row-name'}, h('b', null, name(k)), h('span', {class: 'small muted'}, plural(v.n, 'answer'))),
        h('div', {class: 'row-bar'}, barH(v.accuracy, `${name(k)}: ${pct(v.accuracy)} right`)),
        h('div', {class: 'row-figure'}, h('b', null, pct(v.accuracy)), ' right')))) : empty('No answers yet.', '');
    };
    const tech = L.technique || {};
    const mk = L.mistakes || {};
    const conceptList = (rows, none) => (rows && rows.length ? h('ul', {class: 'feed'}, rows.map((r) => h('li', null, h('i', {class: `key-swatch lvl-${r.status}`, style: {marginTop: '6px'}}),
      h('div', null, h('a', {class: 'feed-title', href: link('concept', r.concept)}, r.title), h('div', {class: 'feed-meta'}, (STATUS[r.status] || {}).label || r.status)),
      h('div', {class: 'feed-when num'}, pct(r.mastery))))) : h('p', {class: 'muted small'}, none));
    const headings = (L.notes_md || '').split('\n').filter((l) => /^##\s+/.test(l)).map((l) => l.replace(/^##\s+/, '').trim());
    return h('div', {class: 'stack'},
      pageHead(L.name || 'You', [L.since ? `Learning since ${fmtDay(localDay(L.since))}` : 'Just getting started',
        persona.name ? ` · tutor ${persona.name}${persona.tone ? ` (${persona.tone})` : ''}` : '']),
      h('div', {class: 'kpis'},
        stat('Answers', String(L.attempts), isNum(L.accuracy) ? `${pct(L.accuracy)} right` : ''),
        stat('Learning pace', `${fixed(L.pace, 2)}×`, enough(L.pace_samples) ? `from ${plural(L.pace_samples, 'concept')}` : 'baseline until 5 concepts'),
        stat('Memory', `${fixed(L.memory_factor, 2)}×`, enough(L.memory_samples) ? `from ${plural(L.memory_samples, 'spaced review')}` : 'default until 5 reviews'),
        stat('First tries', pct(L.placement), `${capital(L.mode || '')} placement`),
        stat('Mistakes', String((mk.open || 0) + (mk.fixing || 0)), `${mk.resolved || 0} resolved`),
        stat('Due now', String(L.due_now), 'reviews')),
      h('div', {class: 'grid-2'},
        card({title: 'Confidence', sub: 'How often you were right at each level of sureness. The tick is what that level implies.', body: [calRows,
          h('p', {class: 'small muted', style: {marginTop: '12px'}}, calNote)]}),
        card({title: 'By difficulty', sub: 'Level 1 is a warm-up; level 5 is harder than the exam', body: accuracyRows(L.by_difficulty, (k) => `Level ${k}`)})),
      h('div', {class: 'grid-2'},
        card({title: 'By question type', body: accuracyRows(L.by_format, (k) => QTYPE[k] || k)}),
        card({title: 'Exam technique', sub: 'Points lost without a knowledge gap', body: h('div', {class: 'kpis'},
          stat('Slips', String(tech.slips || 0), 'misread the question'), stat('Traps', String(tech.traps || 0), 'fell for the wording'),
          stat('Time per question', isNum(tech.median_seconds) ? `${Math.round(tech.median_seconds)} s` : '–',
            isNum(tech.budget_seconds) ? `exam allows ${Math.round(tech.budget_seconds)} s` : 'not timed yet'))})),
      h('div', {class: 'grid-2'},
        card({title: 'Weakest', sub: 'Lowest mastery among concepts answered twice or more', body: conceptList(L.weakest, 'Nothing weak yet.')}),
        card({title: 'Strongest', body: conceptList(L.strongest, 'Nothing strong yet: keep going.')})),
      h('div', {class: 'grid-2'},
        card({title: 'Preferences', sub: 'Change them by asking the tutor, or with polymath.py profile set', body: dataTable(
          [{label: 'Setting', cell: (r) => r[0]}, {label: 'Value', cell: (r) => r[1]}], [
            ['Session length', prefs.session_minutes ? `${prefs.session_minutes} minutes` : '–'],
            ['Questions at a time', prefs.block_size || 1],
            ['Starting level', prefs.start_level || '–'],
            ['Memory target', isNum(prefs.desired_retention) ? pct(prefs.desired_retention) : '–'],
            ['Home folder', h('span', {class: 'mono break'}, app.state.app.home || '\u2013')]])}),
        card({title: 'How you learn', sub: 'learner.md: the tutor reads this before every session', body: [
          L.notes_md ? markdown(L.notes_md.replace(/^#\s.*$/m, '').replace(/^_[^]*?_[ \t]*$/m, '')) : empty('Nothing yet.', ''),
          writable() ? noteForm('note-learner', 'api/notes', 'Add a note', headings.length ? field('note-learner', 'section', 'Under',
            h('select', {class: 'input'}, headings.map((x) => h('option', {value: x, selected: x === 'Coaching notes'}, x)))) : null) : null]})));
  }
  function pageSetup() {
    return h('div', {class: 'stack'}, pageHead('Polymath', 'Nothing to show yet'),
      card({body: [empty('No learning data in this folder yet.', `Polymath reads ${app.state.app.home}.`),
        h('p', {class: 'muted', style: {textAlign: 'center'}}, 'In Claude Code, run /polymath:learn and tell the tutor what you want to learn. This page fills in as you go.')]}));
  }

  // --- study: question-first sessions in the browser ------------------------------------------
  const study = {home: null, homeAt: 0, homeLoading: false, view: null, sid: null, loading: null, picks: {},
    keys: new Set(), poll: 0, retry: 0, loadError: null, offset: 0, lastDone: 0, open: new Set(), forms: {}, breaks: {},
    mock: freshMock(), chat: null, chatAt: 0, chatLoading: false, chatPoll: 0, chatSent: 0, applied: new Set(),
    closingAt: {}, // session id -> when this page first showed its closing re-check
    welcome: null, restore: null, awayFrom: null};  // coming back to a session: see saveScreen
  function freshMock() {
    return {at: 0, saveTimer: 0, review: false, autoSubmitted: false, retryAt: 0, toldTimeUp: false, overShown: false, viewing: null};
  }
  const CONF = [[1, 'Guessing'], [2, 'Unsure'], [3, 'Think so'], [4, 'Fairly sure'], [5, 'Certain']];
  const VERDICT = {correct: {icon: 'check', cls: 'is-good', text: 'Right'}, incorrect: {icon: 'x', cls: 'is-critical', text: 'Not quite'}};
  const MODE_NAME = {learn: 'Learn', review: 'Review', quiz: 'Quiz', mock: 'Mock exam'};
  const FAILED = {
    daily_cap: 'Today’s study limit is reached.',
    usage_limit: 'Your AI usage limit is reached for now.',
    signed_out: 'Claude Code isn’t signed in. Run claude in a terminal, log in, then try again.',
    timeout: 'Writing this question took too long.',
    busy: 'Too much was being written at once.',
    invalid: 'The question didn’t pass the checks.',
    model_offline: 'Your AI model isn’t responding.',
    model_missing: 'That AI model isn’t available.',
    model_memory: 'This computer doesn’t have enough free memory for that model.',
    key_rejected: 'The API key was rejected.',
    no_model: 'No AI model is chosen yet.',
    model: 'This Claude Code version can’t use the chosen model. Run claude update.',
    unsafe: 'Claude Code started with tools it shouldn’t have, so the question was refused.',
    api_key: 'Claude Code would have billed an API key instead of your subscription, so it was stopped. Log in with your Claude subscription.',
    not_subscription: 'Claude Code isn’t logged in with a Claude subscription, so nothing was written.',
    writer_missing: 'Claude Code couldn’t be started.',
    stopped: 'The dashboard stopped while this was being written.',
  };
  const studyInfo = () => (app.state && app.state.app && app.state.app.study) || {available: false};
  const canStudy = () => !app.snapshot && writable() && !!studyInfo().available;
  const studyPath = (...parts) => `api/study/${parts.map(encodeURIComponent).join('/')}`;
  const onSession = () => !!(app.route && app.route.name === 'study' && app.route.id);
  const busy = (key = 'main') => study.keys.has(key);
  const serverNow = () => Date.now() + study.offset;
  const clockText = (sec) => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;

  // Time on a question counts only while the page is visible.
  const vis = {total: 0, since: document.hidden ? null : performance.now()};
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { if (vis.since != null) { vis.total += performance.now() - vis.since; vis.since = null; } }
    else vis.since = performance.now();
  });
  const visibleMs = () => vis.total + (vis.since != null ? performance.now() - vis.since : 0);

  // -- the screen you left: coming back to Study shows it as it was, even after a reload --
  // Kept in this browser: what you had chosen but not yet checked, what you had open, and where you were on the page.
  const SCREEN_KEY = 'polymath-study-screen';
  function readScreens() {
    try {
      const v = JSON.parse(localStorage.getItem(SCREEN_KEY) || '{}');
      return {last: typeof v.last === 'string' ? v.last : null, sessions: v.sessions && typeof v.sessions === 'object' ? v.sessions : {}};
    } catch (err) { return {last: null, sessions: {}}; }
  }
  function writeScreens(all) {
    const ids = Object.keys(all.sessions).sort((a, b) => (all.sessions[b].at || 0) - (all.sessions[a].at || 0));
    ids.slice(6).forEach((id) => delete all.sessions[id]);
    try { localStorage.setItem(SCREEN_KEY, JSON.stringify(all)); } catch (err) { /* storage off: this tab still remembers */ }
  }
  const spentOn = (p) => (p.started == null ? (p.carried || 0)
    : Math.min(1800, (p.carried || 0) + ((p.done != null ? p.done : visibleMs()) - p.started) / 1000));
  function saveScreen(y) {
    const sid = study.sid, v = study.view;
    if (!sid || !v || v.id !== sid || !v.questions) return;
    const all = readScreens();
    if (v.error || v.status === 'done') {
      delete all.sessions[sid];
      if (all.last === sid) all.last = null;
      writeScreens(all);
      return;
    }
    const keys = [];
    if (v.mode !== 'mock') { // a mock's answers are saved on the server as you go
      for (const q of v.questions) {
        if (q.state === 'ready' && !q.result) keys.push(q.id);
        if (q.followup && q.followup.state === 'offered') keys.push(`${q.id}:t`);
      }
    }
    const picks = {};
    for (const key of keys) {
      const p = study.picks[key];
      if (p && (p.choice.length || p.confidence != null)) {
        picks[key] = {c: p.choice, n: p.confidence, d: p.done != null, t: p.started == null ? 0 : Math.max(0.1, Math.round(spentOn(p) * 10) / 10)};
      }
    }
    const here = onSession() && app.route.id === sid;
    all.sessions[sid] = {block: v.block, y: Number.isFinite(y) ? y : here ? window.scrollY : ((all.sessions[sid] || {}).y || 0),
      picks, open: [...study.open].slice(-60), forms: study.forms, at: Date.now()};
    all.last = sid;
    writeScreens(all);
  }
  function restoreScreen(sid) {
    const saved = readScreens().sessions[sid];
    if (!saved) return;
    const now = visibleMs();
    for (const [key, s] of Object.entries(saved.picks || {})) {
      if (!s || !Array.isArray(s.c)) continue;
      study.picks[key] = {choice: s.c.filter((c) => typeof c === 'string' && /^[A-F]$/.test(c)),
        confidence: [1, 2, 3, 4, 5].includes(s.n) ? s.n : null, shown: now, started: s.t ? now : null, done: s.d ? now : null,
        carried: Math.min(1800, Number(s.t) || 0), flag: false, full: false};
    }
    study.open = new Set(Array.isArray(saved.open) ? saved.open.filter((x) => typeof x === 'string') : []);
    study.forms = saved.forms && typeof saved.forms === 'object' ? saved.forms : {};
    study.restore = {sid, block: saved.block, y: Number(saved.y) || 0};
    study.welcome = sid;
  }
  function leaveSession(sid, y) {
    if (study.sid !== sid) return;
    saveScreen(y);
    study.awayFrom = visibleMs(); // time away from the question isn't time spent on it
    study.restore = {sid, block: study.view ? study.view.block : null, y};
  }
  function arriveAtSession(sid) {
    if (study.sid !== sid || study.awayFrom == null) return;
    const gap = Math.max(0, visibleMs() - study.awayFrom);
    study.awayFrom = null;
    for (const p of Object.values(study.picks)) {
      p.shown += gap;
      if (p.started != null) p.started += gap;
      if (p.done != null) p.done += gap;
    }
    if (study.lastDone) study.lastDone += gap;
    study.welcome = sid;
  }
  function applyRestore(sid) {
    const r = study.restore;
    if (!r || r.sid !== sid || !study.view || study.view.id !== sid || !study.view.questions) return;
    study.restore = null;
    if (r.block === study.view.block && r.y > 0) window.scrollTo(0, r.y);
  }
  // The Study tab returns to the session you left. From inside Study it goes to Study home, where everything else is.
  async function resumeTarget() {
    const last = readScreens().last;
    let info = null;
    try { info = await Promise.race([getJSON('api/study'), new Promise((done) => setTimeout(() => done(null), 1500))]); }
    catch (err) { info = null; }
    if (!info || !Array.isArray(info.active)) {
      const v = study.view;
      return v && v.id === last && ['active', 'closing'].includes(v.status) ? last : null;
    }
    study.home = info;
    study.homeAt = Date.now();
    if (info.active.some((a) => a.id === last)) return last;
    return !last && info.active.length === 1 ? info.active[0].id : null;
  }
  async function goToStudy(e) {
    if (e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (app.route && app.route.name === 'study') return;
    e.preventDefault();
    const sid = await resumeTarget();
    location.hash = sid ? link('study', sid) : '#/study';
  }
  function reviewAnswers() {
    study.welcome = null;
    render();
    const first = view.querySelector('.qcard');
    if (first) window.scrollTo({top: Math.max(0, first.getBoundingClientRect().top + window.scrollY - 72)});
  }
  function welcomeBack(v) {
    if (study.welcome !== v.id || v.status !== 'active') return null;
    const done = v.questions.filter((q) => q.result && !q.result.pending);
    const wait = waitingOn(v);
    if (!done.length || openQuestions(v).length || wait.reasons || wait.writing) return null;
    const nx = v.next || {};
    const more = nx.exists || nx.more;
    const below = ((document.querySelector('.topbar') || {}).offsetHeight || 54) + 8; // the header is taller on a phone
    return h('div', {class: 'callout back-banner', role: 'status', style: {top: `${below}px`}}, icon('check', 'is-good'), h('div', null,
      h('b', null, done.length > 1 ? `You answered all ${done.length} questions.` : 'You answered this question.'),
      ' Review them for as long as you like, or move on when you’re ready.',
      h('div', {class: 'study-actions', style: {marginTop: '8px'}},
        h('button', {class: 'btn btn-small', type: 'button', onclick: reviewAnswers}, 'Keep reviewing'),
        h('button', {class: 'btn btn-small btn-primary', type: 'button', disabled: busy(),
          onclick: () => (more ? nextBlock(v) : endSession(v))}, more ? 'Next questions' : 'See the summary'))));
  }

  async function loadStudyHome(force) {
    if (study.homeLoading || (!force && study.home && Date.now() - study.homeAt < 15000)) return;
    study.homeLoading = true;
    try { study.home = await getJSON('api/study'); }
    catch (err) { study.home = {available: false, error: err.message || String(err), active: []}; }
    finally { study.homeLoading = false; study.homeAt = Date.now(); }
    if (app.route && app.route.name === 'study' && !app.route.id) renderSoon();
  }

  // -- the session in memory: one view at a time, newest wins --
  const timeUp = (m) => !!(m && m.deadline && Date.parse(m.deadline) - serverNow() <= 0);
  function needsPoll(v) {
    if (!v || !v.questions || v.error || v.status === 'done') return false;
    const checking = (x) => !!(x && x.dispute && x.dispute.state === 'checking');
    if (v.questions.some((q) => q.state === 'writing' || q.key_check === 'running' || checking(q) || checking(q.followup)
      || (q.followup && q.followup.state === 'checking'))) return true;
    const nx = v.next;
    if (nx && nx.exists && nx.ready + (nx.failed || 0) < nx.total) return true;
    const m = v.mock;
    if (!m || m.submitted) return false;
    // While the exam is written, and once its time is up: the server's sweep submits it if the page can't.
    return m.ready + m.failed < m.total || (!!m.started && timeUp(m));
  }
  function accept(fresh, polled) {
    // Only the open session's views count, and a slow reply (a dispute) can arrive after a quicker one:
    // keep whichever the server saved last.
    if (!fresh || !fresh.id || fresh.id !== study.sid) return false;
    if (fresh.now) study.offset = Date.parse(fresh.now) - Date.now();
    const cur = study.view;
    if (cur && cur.id === fresh.id && isNum(cur.rev) && isNum(fresh.rev)) {
      if (fresh.rev < cur.rev) return false;
      if (polled && fresh.rev === cur.rev) { tick(cur, fresh); return false; } // nothing changed but the clocks
    }
    study.view = fresh;
    return true;
  }
  function tick(cur, fresh) {
    // The writing clocks move without a new rev: update them in place rather than redraw the page.
    const seen = new Map((fresh.questions || []).map((q) => [q.id, q]));
    for (const q of cur.questions || []) {
      const f = seen.get(q.id);
      if (!f || q.state !== 'writing') continue;
      q.writing = f.writing;
      const el = view.querySelector(`[data-writing="${CSS.escape(q.id)}"]`);
      if (el) el.textContent = writingText(q);
      const bar = view.querySelector(`[data-writing-bar="${CSS.escape(q.id)}"]`);
      if (bar) bar.style.width = `${writingPct(q)}%`;
    }
  }
  function pollSession() {
    const v = study.view;
    if (study.poll || !onSession() || !v || app.route.id !== v.id || !needsPoll(v)) return;
    const sid = v.id;
    const timer = setTimeout(async () => {
      let fresh = null;
      try { fresh = await getJSON(studyPath(sid)); } catch (err) { /* keep the last view and try again */ }
      if (study.poll !== timer || study.sid !== sid) return; // another session took over meanwhile: its own poll runs
      study.poll = 0;
      if (fresh && accept(fresh, true)) renderSoon();
      pollSession();
    }, document.hidden ? 5000 : 1500);
    study.poll = timer;
  }
  async function reloadSession() {
    // A change in the data (a void on a concept page, an undo in Claude Code) can change what the open session
    // shows without a poll running: fetch its view once.
    const sid = study.sid;
    if (!sid || study.loading === sid || !study.view) return;
    let fresh = null;
    try { fresh = await getJSON(studyPath(sid)); } catch (err) { return; }
    if (study.sid === sid && accept(fresh, true)) renderSoon();
  }
  function switchTo(sid) {
    if (study.sid === sid) return;
    clearTimeout(study.poll);
    clearTimeout(study.retry);
    clearTimeout(study.mock.saveTimer);
    saveScreen();
    Object.assign(study, {sid, view: null, poll: 0, retry: 0, loadError: null, picks: {}, open: new Set(), forms: {},
      mock: freshMock(), lastDone: visibleMs(), welcome: null, restore: null, awayFrom: null});
    if (sid) restoreScreen(sid);
  }
  async function openSession(sid) {
    switchTo(sid);
    if (study.loading === sid) return;
    study.loading = sid;
    let failed = null;
    try { accept(await getJSON(studyPath(sid))); } catch (err) { failed = err; } finally { study.loading = null; }
    if (study.sid !== sid) return;
    if (!failed) study.loadError = null;
    else if (failed.code === 'not_found' || failed.status === 404) {
      study.view = {id: sid, error: failed.message || String(failed), code: failed.code};
    } else if (!study.view) {
      // A blip (the server restarting, the network): no view yet, so look again shortly.
      study.loadError = failed.message || String(failed);
      clearTimeout(study.retry);
      study.retry = setTimeout(() => {
        study.retry = 0;
        if (study.sid === sid && !study.view && onSession() && app.route.id === sid) openSession(sid);
      }, 2500);
    }
    renderSoon();
    pollSession();
  }
  async function act(path, body, opts = {}) {
    const key = opts.key || 'main';
    if (study.keys.has(key)) return null;
    study.keys.add(key);
    redraw();
    try {
      const out = await post(path, body || {});
      if (out && out.id && out.questions) accept(out);
      if (opts.done) opts.done(out);
      return out;
    } catch (err) {
      studyError(err);
      return null;
    } finally {
      study.keys.delete(key);
      redraw();
      pollSession();
    }
  }
  function studyError(err) {
    toast(err.message || String(err), true);
    // The view on screen is out of date: load it again. After "nothing" (no next block after all), that is
    // what stops the page offering Next questions, and the N key, again.
    if (study.sid && (['time_up', 'stale', 'closed', 'not_ready'].includes(err.code) || (err.code === 'nothing' && onSession()))) {
      openSession(study.sid);
    }
    if (['daily_cap', 'not_owner', 'writer_missing', 'signed_out', 'api_key', 'not_subscription'].includes(err.code) || MODEL_DOWN.has(err.code)) loadStudyHome(true);
    if (MODEL_DOWN.has(err.code)) checkModel(true);
  }
  const sessionAct = (action, body, opts) => act(studyPath(study.sid, ...action.split('/')), body, opts);
  async function startStudy(mode, slug, extra) {
    if (!canStudy()) { location.hash = '#/study'; return; }
    const out = await act('api/study/start', {mode, track: slug, ...(extra || {})}, {key: 'start'});
    if (out && out.id) {
      study.home = null;
      switchTo(out.id);
      accept(out);
      location.hash = link('study', out.id);
    }
  }

  // -- picks: the learner's choice and confidence for one question, kept across redraws --
  function pick(key) {
    if (!study.picks[key]) {
      study.picks[key] = {choice: [], confidence: null, shown: visibleMs(), started: null, done: null, flag: false, full: false};
    }
    return study.picks[key];
  }
  function begin(p) { if (p.started == null) p.started = Math.max(p.shown, study.lastDone || 0); }
  const complete = (p, select) => p.choice.length === select && p.confidence != null;
  function settle(p, select) { if (complete(p, select)) { p.done = visibleMs(); study.lastDone = p.done; } }
  function choose(key, q, letter) {
    const p = pick(key);
    begin(p);
    p.full = false;
    if (q.select > 1) {
      if (p.choice.includes(letter)) p.choice = p.choice.filter((c) => c !== letter);
      else if (p.choice.length >= q.select) p.full = true; // exactly N: unpick one to change the answer
      else p.choice = [...p.choice, letter].sort();
    } else p.choice = [letter];
    settle(p, q.select);
    saveScreen();
    render();
  }
  function setConfidence(key, q, n) {
    const p = pick(key);
    begin(p);
    p.confidence = n;
    settle(p, q.select);
    saveScreen();
    render();
  }
  function seconds(p) {
    // From when the question became the one to answer (the previous one done) to when it was answered.
    const end = p.done != null ? p.done : visibleMs();
    return Math.max(1, Math.min(1800, Math.round((p.carried || 0) + (end - (p.started != null ? p.started : p.shown)) / 1000)));
  }

  // -- pieces of a question --
  function richText(text) {
    // `code` spans only; everything else stays plain text.
    const out = [];
    String(text || '').split(/(`[^`\n]+`)/).forEach((part) => {
      if (/^`[^`\n]+`$/.test(part)) out.push(h('code', null, part.slice(1, -1)));
      else if (part) out.push(part);
    });
    return out;
  }
  function chooseHint(q, key) {
    const p = key ? study.picks[key] : null;
    const n = p ? p.choice.length : 0;
    return h('div', {class: `qhint${p && p.full ? ' is-warning' : ''}`, 'aria-live': 'polite'}, `Choose ${q.select}`,
      key ? ` · ${n} of ${q.select} chosen` : '', p && p.full ? ' · unpick one to change your answer' : '');
  }
  function stemBlock(q, key) {
    return [q.primer ? h('div', {class: 'primer'}, h('b', null, 'Before you start'), h('p', null, richText(q.primer))) : null,
      h('div', {class: 'qstem'}, richText(q.stem)), q.code ? h('pre', {class: 'qcode'}, q.code) : null,
      q.select > 1 ? chooseHint(q, key) : null];
  }
  function optionButtons(key, q) {
    const p = pick(key);
    const multi = q.select > 1;
    return h('ul', {class: 'opts', role: multi ? 'group' : 'radiogroup', 'aria-label': multi ? `Options: choose ${q.select}` : 'Options'},
      q.options.map((o) => h('li', null, h('button', {class: 'opt', type: 'button', role: multi ? 'checkbox' : 'radio',
        'aria-checked': String(p.choice.includes(o.key)), 'data-focus': `${key}:${o.key}`, onclick: () => choose(key, q, o.key)},
      h('span', {class: 'opt-key'}, o.key), h('span', {class: 'opt-text'}, richText(o.text))))));
  }
  function confidencePicker(key, q) {
    const p = pick(key);
    return h('div', {class: 'conf', role: 'radiogroup', 'aria-label': 'How sure are you?'},
      h('span', {class: 'conf-label'}, 'How sure?'),
      CONF.map(([n, label]) => h('button', {type: 'button', role: 'radio', 'aria-checked': String(p.confidence === n),
        'data-focus': `${key}:c${n}`, title: `${n}: ${label}`, onclick: () => setConfidence(key, q, n)}, h('b', null, n), label)));
  }
  function folded(id, summary, body) {
    const el = h('details', {class: 'more'}, h('summary', null, summary), body);
    if (study.open.has(id)) el.open = true;
    el.addEventListener('toggle', () => { if (el.open) study.open.add(id); else study.open.delete(id); });
    return el;
  }
  function resultOptions(r, id) {
    const rows = r.options.map((o) => {
      const picked = r.choice.includes(o.key);
      const note = o.correct ? (picked ? 'Right: ' : 'The answer: ') : 'Why not: ';
      return {show: o.correct || picked, node: h('li', null, h('div', {class: `opt ${o.correct ? 'is-right' : picked ? 'is-wrong' : ''}`},
        h('span', {class: 'opt-key'}, o.key), h('span', {class: 'opt-text'}, richText(o.text),
          h('span', {class: 'sr-only'}, o.correct ? ' (a right answer)' : '', picked ? ' (your answer)' : ''),
          o.why ? h('span', {class: 'why'}, note, richText(o.why)) : null)))};
    });
    const rest = rows.filter((x) => !x.show).map((x) => x.node);
    return [h('ul', {class: 'opts'}, rows.filter((x) => x.show).map((x) => x.node)),
      rest.length ? folded(`others:${id}`, 'Why not the others', h('ul', {class: 'opts'}, rest)) : null];
  }
  function pickedOptions(qv) {
    // Before the explanation: the options with the learner's own choice marked, and nothing else.
    const chosen = qv.result.choice || [];
    return h('ul', {class: 'opts'}, qv.question.options.map((o) => h('li', null,
      h('div', {class: `opt${chosen.includes(o.key) ? ' is-picked' : ''}`}, h('span', {class: 'opt-key'}, o.key),
        h('span', {class: 'opt-text'}, richText(o.text), chosen.includes(o.key) ? h('span', {class: 'sr-only'}, ' (your answer)') : null)))));
  }
  function verdictLine(r) {
    if (r.voided) return h('div', {class: 'verdict is-neutral'}, icon('half', 'is-neutral'), 'Voided: it doesn’t count');
    const v = VERDICT[r.result] || VERDICT.incorrect;
    const right = (r.answer || []).join(' and ');
    const yours = r.blank ? 'nothing' : (r.choice || []).join(' and ');
    return h('div', {class: `verdict ${v.cls}`}, icon(v.icon, v.cls), v.text,
      r.result !== 'correct' && right ? h('span', {class: 'verdict-sub'}, `You chose ${yours}; the answer is ${right}.`) : null);
  }
  function theyBelieve(belief) {
    const b = String(belief || '').trim();
    const m = b.match(/^(thinks|believes|assumes|expects|confuses)\s+(.*)$/i);
    if (m) return `${{thinks: 'think', believes: 'believe', assumes: 'assume', expects: 'expect', confuses: 'confuse'}[m[1].toLowerCase()]} ${m[2]}`;
    return `believe this: ${b}`;
  }
  function teachPanel(r) {
    const t = r.teach || {};
    // A miss filed as a gap has no belief to refute: the learner said they weren't really sure. The engine
    // still answers "refute" for any confidence of 4 or 5, so teach it as the gap it was logged as.
    const action = t.action === 'refute' && r.error === 'gap' ? 'teach' : t.action;
    const wrong = (t.picked || []).filter((o) => !o.correct);
    // The wrong option the miss was filed under (on a choose-two, the more serious of two wrong picks).
    const primary = wrong.find((o) => o.key === t.primary) || wrong[0];
    const rule = r.rule ? h('p', null, h('span', {class: 'rule'}, 'The rule: '), richText(r.rule)) : null;
    const explain = r.explanation ? h('p', null, richText(r.explanation)) : null;
    const credited = (t.credited || []).length ? h('p', {class: 'small muted'}, 'Hard and right, so this also credits ',
      t.credited.map((c, i) => [i ? ', ' : '', h('a', {href: link('concept', c.concept)}, c.title)]),
      '. They still get a spaced check later.') : null;
    if (r.result === 'correct') {
      if (action === 'confirm') return h('div', {class: 'teach'}, h('h3', null, 'Right, though you weren’t sure'), rule, explain, credited);
      return rule || explain || credited ? h('div', {class: 'teach is-quiet'}, rule || explain, credited) : null;
    }
    if (action === 'technique') {
      const trap = r.error === 'trap';
      return h('div', {class: 'teach is-technique'}, h('h3', null, trap ? 'The wording caught you, not the content' : 'A misread, not a gap'),
        primary && primary.why ? h('p', null, richText(primary.why)) : null, rule,
        h('p', null, trap ? 'Habit: read the last line of the question first, and mark NOT, EXCEPT and words such as “most efficient” before you look at the options.'
          : 'Habit: before you answer, say in a few words what the question asks for.'));
    }
    if (action === 'refute') {
      const p = primary;
      const mixed = t.confused_with && t.confused_with.concept ? t.confused_with : null;
      return h('div', {class: 'teach is-refute'},
        h('h3', null, t.repeat ? 'You’ve missed this before: another angle' : 'The belief behind your answer'),
        p && p.belief ? h('p', null, `You picked ${p.key}. People pick it when they ${theyBelieve(p.belief)}.`) : null,
        mixed ? h('p', null, 'You mixed it up with ', h('a', {href: link('concept', mixed.concept)}, mixed.title || mixed.concept), '.') : null,
        p && p.why ? h('p', null, h('b', null, 'Why it fails: '), richText(p.why)) : null,
        mixed ? h('p', null, h('b', null, 'Contrast the two: '),
          'say in a sentence what each one is for, and what in a question tells you which one it wants.') : null,
        t.repeat && (t.right || [])[0] ? h('p', null, h('b', null, 'Compare the right answer: '), richText(t.right[0].why)) : null,
        rule, explain);
    }
    if (action === 'check-prerequisites') {
      // The server says which foundations the next questions really check (checked_next): none in a quiz, which
      // keeps to its own questions, or when they were asked earlier in the session. Older answers don't say.
      const pre = t.prereqs || [];
      const next = new Set(Array.isArray(t.checked_next) ? t.checked_next : pre.map((x) => x.concept));
      const named = (list) => list.map((x, i) => [i ? ', ' : '', h('a', {href: link('concept', x.concept)}, x.title)]);
      const checked = pre.filter((x) => next.has(x.concept));
      return h('div', {class: 'teach'}, h('h3', null, 'This builds on something shaky'), rule, explain,
        pre.length ? h('p', null, 'It rests on ', named(pre), '. ',
          checked.length === pre.length ? 'The next questions check that first.'
            : checked.length ? ['The next questions check ', named(checked), ' first.']
              : 'Nothing later in this session checks it: practise it from its page when you can.') : null);
    }
    return h('div', {class: 'teach'}, h('h3', null, 'What to know'), rule, explain);
  }
  function effects(r) {
    const e = r.engine;
    if (!e || r.voided || e.duplicate) return null;
    const bits = [];
    // After a miss the number can still rise (seeing the answer teaches a little), which reads wrongly
    // next to "Not quite": a miss shows what happens next instead.
    if (r.result === 'correct' && isNum(e.knowledge_before) && isNum(e.knowledge_after)) {
      bits.push(['Knowledge ', h('b', null, `${pct(e.knowledge_before)} → ${pct(e.knowledge_after)}`)]);
    }
    if (e.mistake) {
      bits.push(e.repeat ? [h('b', null, e.mistake), ' again: it comes back until it sticks']
        : ['Logged as ', h('b', null, e.mistake), ': asked again until you get it right on another day']);
    }
    (e.fixing || []).forEach((m) => bits.push([h('b', null, m), ' fixed for now: checked again tomorrow']));
    (e.resolved || []).forEach((m) => bits.push([h('b', null, m), ' resolved']));
    if (r.result === 'correct' && isNum(e.review_in_days)) {
      bits.push(['Next review in ', h('b', null, plural(Math.max(1, Math.round(e.review_in_days)), 'day'))]);
    }
    return bits.length ? h('div', {class: 'effects'}, bits.map((b) => h('span', null, b))) : null;
  }
  function focusLater(key) {
    requestAnimationFrame(() => {
      const el = view.querySelector(`[data-focus="${CSS.escape(key)}"]`);
      const box = el ? null : view.querySelector(`[data-focus-scope="${CSS.escape(key)}"]`);
      if (el) el.focus();
      else if (box) focusable(box).focus();
    });
  }

  // -- after a confident miss: what made you pick it? --
  function reasonStep(qv) {
    const key = `other:${qv.id}`;
    const other = study.forms[key];
    const input = other ? h('input', {class: 'input', type: 'text', maxlength: 160, 'data-focus': key,
      'aria-label': 'What made you pick it', placeholder: 'In a few words: what did you think was true?'}) : null;
    if (input) {
      input.value = other.text || '';
      input.addEventListener('input', () => { study.forms[key] = {text: input.value}; });
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); sendReason(qv.id, 'other', input.value); } });
    }
    return h('div', {class: 'teach is-refute'},
      h('h3', null, `You were sure, so first: what made you pick ${(qv.result.choice || []).join(' and ')}?`),
      h('p', {class: 'small muted'}, 'Your answer decides what is taught and what is logged. The explanation comes next.'),
      h('div', {class: 'reasons'}, (qv.ask_reason || []).map((o) => h('button', {class: `chip${o.id === 'skip' ? ' is-quiet' : ''}`,
        type: 'button', disabled: busy(), 'aria-pressed': o.id === 'other' ? String(!!other) : null, 'data-focus': `reason:${qv.id}:${o.id}`,
        onclick: () => {
          if (o.id !== 'other') return sendReason(qv.id, o.id);
          study.forms[key] = {text: ''};
          render();
          focusLater(key);
          return null;
        }}, o.label))),
      input ? h('div', {class: 'form-actions'}, input, h('button', {class: 'btn btn-small', type: 'button', disabled: busy(),
        'data-focus': `${key}:send`, onclick: () => sendReason(qv.id, 'other', input.value)}, 'Send')) : null);
  }
  function sendReason(qid, reason, other) {
    if (reason === 'other' && !String(other || '').trim()) { toast('Say in a few words what made you pick it.', true); return; }
    sessionAct('reason', {qid, reason, other: other || ''}, {done: () => { delete study.forms[`other:${qid}`]; }});
  }

  // -- the follow-up: the same rule in a new case, once a separate check has passed it --
  function followupBlock(qv) {
    const f = qv.followup;
    // A voided question hides a follow-up still to come, but not one already answered: that one counts on its own
    // (a void on the concept page covers only the question), with its own result and dispute.
    if (!f || (qv.result.voided && f.state !== 'answered')) return null;
    const action = (qv.result.teach || {}).action;
    const label = f.untargeted ? (action === 'technique' ? 'Same kind of wording, new question' : 'One more on the same rule')
      : 'Same rule, new case';
    if (f.state === 'checking') {
      return h('div', {class: 'followup'}, h('div', {class: 'qwriting'}, h('span', {class: 'spinner', 'aria-hidden': 'true'}),
        h('span', null, 'Checking a follow-up question before you see it…')));
    }
    if (f.state === 'dropped') {
      return h('p', {class: 'followup small muted'}, f.dropped_by === 'void'
        ? 'No follow-up: the mistake it was for was voided.' : 'No follow-up this time: the separate check wasn’t sure of it.');
    }
    if (f.state === 'skipped') return h('p', {class: 'followup small muted'}, 'Follow-up skipped.');
    const q = f.question;
    if (!q) return null;
    const key = `${qv.id}:t`;
    if (f.state === 'answered' && f.result) {
      return h('div', {class: 'followup'}, h('div', {class: 'twin-label'}, label), stemBlock(q), verdictLine(f.result),
        resultOptions(f.result, key), f.result.result === 'correct' || f.result.voided ? null : teachPanel(f.result), effects(f.result),
        disputeBlock(qv, true));
    }
    const p = pick(key);
    return h('div', {class: 'followup is-open'}, h('div', {class: 'twin-label'}, label), stemBlock(q, key), optionButtons(key, q),
      confidencePicker(key, q), h('div', {class: 'study-actions', style: {marginTop: '12px'}},
        h('button', {class: 'btn btn-primary', type: 'button', 'data-focus': `${key}:check`, disabled: !complete(p, q.select) || busy(),
          onclick: () => answerFollowup(qv)}, 'Check'),
        h('button', {class: 'btn', type: 'button', 'data-focus': `${key}:skip`, disabled: busy(),
          onclick: () => sessionAct('followup', {qid: qv.id, skip: true})}, 'Skip')));
  }
  function answerFollowup(qv) {
    const key = `${qv.id}:t`;
    const p = pick(key);
    if (!qv.followup || !qv.followup.question || !complete(p, qv.followup.question.select)) return;
    sessionAct('followup', {qid: qv.id, answer: {choice: p.choice, confidence: p.confidence, seconds: seconds(p)}});
  }

  // -- when the answer key itself is in doubt --
  function keyDoubt(qv) {
    const d = qv.key_doubt;
    if (qv.result.voided || (qv.dispute && qv.dispute.state)) return null;
    if (!d) { // the check runs while the learner reads; a doubt shows up here once it is done
      const followupWaits = qv.followup && qv.followup.state === 'checking'; // that line already says a check runs
      return qv.key_check === 'running' && !followupWaits
        ? h('p', {class: 'small muted', style: {marginTop: '10px'}}, 'A separate check of this answer key is still running.') : null;
    }
    const blind = (d.blind || []).join(' and ');
    const keyText = (qv.result.answer || []).join(' and ');
    return h('div', {class: 'callout is-warning', style: {marginTop: '12px'}}, icon('alert', 'is-warning'), h('div', null,
      h('b', null, 'A separate check isn’t sure this answer key is right.'),
      blind && blind !== keyText ? h('p', null, `Asked without the key, it answered ${blind}; the key says ${keyText}.`) : null,
      d.doubt ? h('p', null, 'What it doubted: ', richText(d.doubt)) : null,
      d.kept ? h('p', {class: 'small muted'}, 'You kept it as a miss.') : writable() ? h('div', {class: 'study-actions', style: {marginTop: '8px'}},
        h('button', {class: 'btn btn-small', type: 'button', 'data-focus': `keep:${qv.id}`, disabled: busy(),
          onclick: () => sessionAct('keep', {qid: qv.id})}, 'Keep it as a miss'),
        qv.can_dispute ? h('button', {class: 'btn btn-small', type: 'button', onclick: () => openDispute(`dispute:${qv.id}`, blind && blind !== keyText
          ? `A separate check answered ${blind}.` : (d.doubt || ''))}, 'I was right: dispute it') : null) : null));
  }
  function openDispute(key, text) {
    study.forms[key] = {text: text || ''};
    render();
    focusLater(key);
  }
  function disputeIt(qid, reason, force, twin) {
    const key = `dispute:${qid}${twin ? ':t' : ''}`;
    study.forms[key] = {text: reason};
    return sessionAct('dispute', {qid, reason, force: !!force, which: twin ? 'twin' : 'question'}, {key, done: (out) => {
      const q = ((out && out.questions) || []).find((x) => x.id === qid);
      const d = q && (twin ? (q.followup || {}).dispute : q.dispute);
      // When the check sides with the key, what the learner wrote stays in the form: "Void it anyway" sends it,
      // so the void and its note carry their reason. The server keeps no reason for an upheld dispute.
      if (d && d.state === 'upheld' && !force) study.forms[key] = {text: reason};
      else delete study.forms[key];
      if (d && d.state === 'voided') toast(twin ? 'Voided: that follow-up no longer counts.' : 'Voided: that answer no longer counts.');
    }});
  }
  /* The dispute of a question's answer key, or (twin) of its follow-up's. Voiding a question voids its
     follow-up with it; a follow-up is judged, and voided, on its own. */
  function disputeBlock(qv, twin) {
    const f = qv.followup || {};
    const d = (twin ? f.dispute : qv.dispute) || {};
    const id = twin ? `${qv.id}:t` : qv.id;
    const key = `dispute:${id}`;
    const what = twin ? 'this follow-up' : 'this answer';
    if (d.state === 'voided') {
      return h('div', {class: 'callout', style: {marginTop: '12px'}}, icon('check', 'is-neutral'), h('div', null,
        h('b', null, d.forced ? `Voided at your request: ${what} no longer counts.` : `The check agreed: ${what} is voided and no longer counts.`),
        !twin && f.state === 'answered' && (f.dispute || {}).state !== 'voided' ? h('p', {class: 'small'}, 'Its follow-up was voided with it.') : null,
        d.explanation ? h('p', {class: 'small'}, richText(d.explanation)) : null));
    }
    if (busy(key) || d.state === 'checking') {
      return h('div', {class: 'qwriting', style: {marginTop: '12px'}}, h('span', {class: 'spinner', 'aria-hidden': 'true'}),
        h('span', null, 'Checking your dispute against the documentation…'));
    }
    const stands = h('p', {class: 'small muted', style: {marginTop: '12px'}}, 'The check sided with the key, so the answer stands.');
    if (!writable() || !(twin ? f.can_dispute : qv.can_dispute)) return d.state === 'upheld' ? stands : null;
    const form = study.forms[key];
    if (d.state === 'upheld' && study.forms[`kept:${id}`]) return stands;
    const upheld = d.state === 'upheld';
    const alsoTwin = !twin && ['answered', 'offered', 'checking'].includes(f.state) && !(f.result && f.result.voided);
    if (!form && !upheld) {
      return h('div', {class: 'dispute-link'},
        d.state === 'failed' ? h('p', {class: 'small muted'}, d.explanation || 'The last dispute couldn’t void anything.') : null,
        h('button', {class: 'link-btn', type: 'button', 'data-focus': `${key}:open`, onclick: () => openDispute(key, '')},
          twin ? 'Think the follow-up’s answer key is wrong? Dispute the follow-up'
            : alsoTwin ? 'Think the question’s answer key is wrong? Dispute it, with its follow-up' : 'Think the answer key is wrong? Dispute it'));
    }
    // The learner's reason: typed for the check, and once the check sides with the key, still here (what they sent;
    // after a reload, what they type now), so a void they insist on records why.
    const input = h('textarea', {class: 'input', rows: 2, 'data-focus': key,
      'aria-label': upheld ? 'Why you are still sure' : twin ? 'What is wrong with this follow-up?' : 'What is wrong with this question?',
      placeholder: upheld ? 'Why you are still sure. It is kept with the void.'
        : 'What is wrong with the question or its key? A separate check reads this.'});
    input.value = (form && form.text) || '';
    input.addEventListener('input', () => { study.forms[key] = {text: input.value}; });
    return h('div', {class: 'dispute'},
      upheld ? h('div', {class: 'callout'}, icon('alert', 'is-warning'), h('div', null, h('b', null, 'The check sides with the key.'),
        d.explanation ? h('p', {class: 'small'}, richText(d.explanation)) : null,
        h('p', {class: 'small muted'}, 'If you are still sure, you can void it anyway: it then stops counting either way.'))) : null,
      input,
      alsoTwin ? h('p', {class: 'small muted'}, f.state === 'answered'
        ? `Voiding this question voids its follow-up too.${f.can_dispute ? ' To dispute only the follow-up, use its own link above.' : ''}`
        : 'Voiding this question drops its follow-up too.') : null,
      h('div', {class: 'study-actions'},
        upheld ? h('button', {class: 'btn btn-small', type: 'button', 'data-focus': `${key}:force`,
          onclick: () => disputeIt(qv.id, input.value.trim() || 'the learner is sure the key is wrong', true, twin)}, 'Void it anyway')
          : h('button', {class: 'btn btn-small', type: 'button', 'data-focus': `${key}:check`,
            onclick: () => disputeIt(qv.id, input.value, false, twin)}, 'Check my dispute'),
        upheld ? h('button', {class: 'btn btn-small', type: 'button', 'data-focus': `${key}:keep`,
          onclick: () => { study.forms[`kept:${id}`] = true; delete study.forms[key]; render(); }}, 'Keep the answer')
          : h('button', {class: 'btn btn-small', type: 'button', 'data-focus': `${key}:cancel`,
            onclick: () => { delete study.forms[key]; render(); }}, 'Cancel')));
  }

  // -- a question card --
  const writingText = (qv) => {
    const seconds = qv.writing || 0;
    const local = models.status && models.status.kind === 'local';
    return seconds < 45 ? `Writing this question… ${seconds} s (${local ? 'a local model can take a minute or more' : 'usually about 30 s'})`
      : `Still writing (${seconds} s). A draft that fails a check is rewritten, which can take ${local ? 'a few minutes' : 'up to 2 minutes'}.`;
  };
  // How long is left isn't known, so the bar eases toward 92% and never claims to be finished.
  const writingPct = (qv) => Math.min(92, Math.round(100 * (1 - Math.exp(-(qv.writing || 0) / 30))));
  function writingBody(qv) {
    return [h('div', {class: 'qwriting'}, h('span', {class: 'spinner', 'aria-hidden': 'true'}),
      h('span', {'data-writing': qv.id}, writingText(qv))),
    h('div', {class: 'writing-bar', 'aria-hidden': 'true'},
      h('span', {'data-writing-bar': qv.id, style: {width: `${writingPct(qv)}%`}})),
    h('div', {class: 'skeleton w80'}), h('div', {class: 'skeleton w60'}), h('div', {class: 'skeleton w40'})];
  }
  function retryButton(qv) {
    return h('button', {class: 'btn', type: 'button', 'data-focus': `retry:${qv.id}`, disabled: busy(`retry:${qv.id}`) || busy('retry-all'),
      onclick: () => sessionAct('retry', {qid: qv.id}, {key: `retry:${qv.id}`})}, 'Try again');
  }
  function failedBody(qv) {
    const cap = qv.code === 'daily_cap';
    return [h('div', {class: 'callout'}, icon('alert', 'is-warning'), h('div', null,
      h('b', null, FAILED[qv.code] || 'This question couldn’t be written.'),
      qv.error && (!FAILED[qv.code] || qv.code === 'invalid' || MODEL_DOWN.has(qv.code)) ? h('div', {class: 'small muted'}, qv.error) : null,
      cap ? h('div', {class: 'small muted'}, 'Raise the cap in Settings on the Study page.') : null)),
    cap ? null : h('div', {class: 'study-actions', style: {marginTop: '10px'}}, retryButton(qv),
      MODEL_DOWN.has(qv.code) ? h('a', {class: 'btn', href: '#/models'}, 'Change AI model') : null)];
  }
  function conceptLinks(qv) {
    if (!qv.concept) return null;
    const docs = (qv.docs || []).filter(safeHref);
    return h('div', {class: 'small muted qlinks'}, h('a', {href: link('concept', qv.concept)}, `${qv.title || 'This concept'}: your progress`),
      docs.map((u) => [' · ', h('a', {href: u, target: '_blank', rel: 'noopener noreferrer'}, 'Official docs ', icon('out'))]));
  }
  function questionCard(qv, active) {
    const r = qv.result;
    const head = h('div', {class: 'qhead'}, h('span', {class: 'qnum'}, `${qv.n}.`),
      qv.kind_label ? h('span', {class: `tag kind-${qv.kind}`}, qv.mistake ? `${qv.kind_label} ${qv.mistake}` : qv.kind_label) : null,
      qv.area ? h('span', null, qv.area) : null,
      qv.difficulty ? h('span', {title: 'How hard the question is, 1 to 5'}, `level ${qv.difficulty}`) : null);
    let body;
    if (qv.state === 'writing') body = writingBody(qv);
    else if (qv.state === 'failed') body = failedBody(qv);
    else if (r && r.pending) body = [stemBlock(qv.question), pickedOptions(qv), reasonStep(qv), disputeBlock(qv)];
    else if (r) {
      body = [stemBlock(qv.question), verdictLine(r), resultOptions(r, qv.id), r.voided ? null : teachPanel(r), effects(r),
        keyDoubt(qv), followupBlock(qv), disputeBlock(qv), conceptLinks(qv)];
    } else body = [stemBlock(qv.question, qv.id), optionButtons(qv.id, qv.question), confidencePicker(qv.id, qv.question)];
    const box = h('section', {class: `qcard${active ? ' is-active' : ''}${r && !r.pending ? ' is-done' : ''}`,
      'aria-label': `Question ${qv.n}`, 'data-qid': qv.id, 'data-focus-scope': `q:${qv.id}`}, head, body);
    // Only once it's answered: a lesson opened before answering would give the answer away.
    if (r && !r.pending) attachLearn(box, `q:${study.view ? study.view.id : ''}:${qv.id}`, termsOf(qv), {track: study.view && study.view.track, concept: qv.concept}, qv.title);
    return box;
  }
  function termsOf(qv) {
    return [...((qv.result || {}).terms || []), ...(((qv.followup || {}).result || {}).terms || [])];
  }

  // -- the block --
  function openQuestions(v) { return v.questions.filter((q) => q.state === 'ready' && !q.result); }
  function activeTarget(v) {
    // Where keys go: the first question not yet answered in full, else an offered follow-up.
    for (const q of openQuestions(v)) {
      if (!complete(pick(q.id), q.question.select)) return {key: q.id, q: q.question, qid: q.id};
    }
    for (const q of v.questions) {
      const f = q.followup;
      if (f && f.state === 'offered' && f.question && !q.result.voided) return {key: `${q.id}:t`, q: f.question, qid: q.id, twin: true};
    }
    return null;
  }
  function readyToCheck(v) {
    const open = openQuestions(v);
    return open.length && open.every((q) => complete(pick(q.id), q.question.select)) ? open : null;
  }
  function checkBlock(v) {
    const open = readyToCheck(v);
    if (!open || busy()) return;
    study.welcome = null;
    sessionAct('answer', {block: v.block, answers: open.map((q) => {
      const p = pick(q.id);
      return {qid: q.id, choice: p.choice, confidence: p.confidence, seconds: seconds(p)};
    })});
  }
  function waitingOn(v) {
    return {
      reasons: v.questions.filter((q) => q.result && q.result.pending).length,
      followups: v.questions.filter((q) => q.followup && ['offered', 'checking'].includes(q.followup.state) && !q.result.voided).length,
      writing: v.questions.filter((q) => q.state === 'writing').length,
    };
  }
  function nextBlock(v) {
    study.picks = {};
    study.welcome = null;
    study.restore = null;
    study.lastDone = visibleMs();
    sessionAct('next', {block: v.block}, {done: () => window.scrollTo(0, 0)});
  }
  function endSession(v) {
    if (v.status === 'closing') {
      // Mid re-check this skips the rest of it. The second click of a double click on End lands on the redrawn
      // button within moments of the re-check appearing: that one is ignored. A later click asks first.
      if (Date.now() - (study.closingAt[v.id] || 0) < 800) return;
      if (openQuestions(v).length && !window.confirm('Skip the rest of the re-check and end the session now?')) return;
      sessionAct('end', {skip_close: true});
      return;
    }
    const wait = waitingOn(v);
    if ((wait.reasons || wait.followups || openQuestions(v).length)
      && !window.confirm('End now? Open follow-ups are dropped, and misses you didn’t explain are recorded as they are.')) return;
    sessionAct('end', {});
  }

  function pageSession(sid) {
    if (app.snapshot) return pageStudyHome(); // it says why there is no studying here, without asking a server
    if (study.sid !== sid) switchTo(sid);
    const v = study.view;
    if (!v || v.id !== sid) {
      if (!study.retry) openSession(sid); // after a failed load, its retry is already on the way
      return study.loadError ? h('p', {class: 'loading'}, 'Couldn’t load your session; trying again… ',
        h('span', {class: 'small muted'}, study.loadError)) : h('p', {class: 'loading'}, 'Loading your session…');
    }
    if (v.error) {
      return h('div', {class: 'stack'}, pageHead('Study', '', null, [h('a', {href: '#/study'}, 'Study')]),
        card({body: empty('This session isn’t available.', v.error)}));
    }
    if (v.mode === 'mock') return pageMock(v);
    const t = track(v.track);
    const title = `${MODE_NAME[v.mode] || 'Study'}${t ? ` · ${shortTitle(t)}` : ''}`;
    if (v.status === 'done') return sessionSummary(v, title);
    if (v.status === 'closing' && !study.closingAt[v.id]) study.closingAt[v.id] = Date.now();
    const tot = v.totals || {};
    const bar = h('div', {class: 'study-bar'}, h('div', {class: 'study-meta'},
      h('span', null, v.status === 'closing' ? h('b', null, 'Closing re-check')
        : [h('b', null, `Block ${v.block}`), v.mode === 'quiz' && v.count ? ` · a quiz of ${v.count}` : '']),
      h('span', null, h('b', null, tot.answered || 0), ' answered'), h('span', null, h('b', null, tot.right || 0), ' right'),
      (tot.opened || []).length ? h('span', null, h('b', null, tot.opened.length), ' logged to fix') : null,
      h('span', null, h('b', null, Math.round(v.minutes || 0)), v.session_minutes ? ` of ${v.session_minutes} min` : ' min')),
    // The bar's button is the session's way out in every state: in the closing re-check it ends the session without
    // the rest of the re-check, as "Skip the re-check" below it does, and once that is answered, as "Finish the session".
    h('div', {class: 'study-actions'}, h('a', {class: 'btn', href: '#/study'}, 'Study home'),
      h('button', {class: 'btn', type: 'button', 'data-focus': 'end', disabled: busy(),
        onclick: () => endSession(v)}, 'End session')));
    const target = activeTarget(v);
    return h('div', {class: 'stack'}, pageHead(title, sessionSub(v), null, [h('a', {href: '#/study'}, 'Study')]),
      welcomeBack(v), card({body: bar}), breakPrompt(v),
      ...v.questions.map((q) => questionCard(q, target && target.qid === q.id && !target.twin)),
      sessionFooter(v), keysHelp(v));
  }
  function sessionSub(v) {
    const scope = v.scope || {};
    const t = track(v.track);
    if (v.status === 'closing') return 'Before you stop: one more look at what you fixed today, so it holds.';
    if (v.mode === 'review') return 'Mistakes first, then what you are about to forget.';
    if (v.mode === 'quiz') {
      const area = scope.area && t ? ((t.domains || []).find((d) => d.id === scope.area) || {}).name : null;
      return scope.mistakes ? 'Your open mistakes.' : area ? `On ${area}.` : scope.concept ? 'One concept, level by level.' : 'Your weakest concepts first.';
    }
    return 'Question first: a miss is taught from the belief behind it, asked again in a new form, then checked on later days.';
  }
  function breakPrompt(v) {
    const due = Math.floor((v.minutes || 0) / 50);
    if (!due || (study.breaks[v.id] || 0) >= due) return null;
    return h('div', {class: 'callout is-break', role: 'status'}, icon('clock', 'is-neutral'), h('div', null,
      h('b', null, `${Math.round(v.minutes)} minutes in.`), ' A five-minute break away from the screen helps what you just learned settle. The session waits for you.',
      h('div', {class: 'study-actions', style: {marginTop: '8px'}}, h('button', {class: 'btn btn-small', type: 'button',
        onclick: () => { study.breaks[v.id] = due; render(); }}, 'Carry on'))));
  }
  function sessionFooter(v) {
    const open = openQuestions(v);
    const can = readyToCheck(v);
    const wait = waitingOn(v);
    const nxt = v.next || {};
    if (open.length) {
      return h('div', {class: 'study-actions'}, h('button', {class: 'btn btn-primary', type: 'button', 'data-focus': 'check',
        disabled: !can || busy(), onclick: () => checkBlock(v)}, busy() ? 'Checking…' : `Check ${open.length > 1 ? `${open.length} answers` : 'the answer'}`),
      v.status === 'closing' ? h('button', {class: 'btn', type: 'button', 'data-focus': 'skip-close', disabled: busy(),
        onclick: () => sessionAct('end', {skip_close: true})}, 'Skip the re-check') : null,
      h('span', {class: 'form-note'}, can ? 'or press Enter' : 'Choose an answer and say how sure you are, for each question.'));
    }
    if (wait.reasons) return h('p', {class: 'small muted'}, 'Say what made you pick that answer, above, or choose “Just show me the answer”.');
    if (v.status === 'closing') {
      return h('div', {class: 'study-actions'}, h('button', {class: 'btn btn-primary', type: 'button', 'data-focus': 'finish', disabled: busy(),
        onclick: () => sessionAct('end', {skip_close: true})}, 'Finish the session'));
    }
    if (wait.writing) return h('p', {class: 'small muted'}, 'The rest of this block is still being written.');
    const note = wait.followups ? h('span', {class: 'form-note'}, 'A follow-up above is waiting; moving on skips it.')
      : h('span', {class: 'form-note'}, 'or press N');
    if (nxt.exists || nxt.more) {
      const failed = nxt.exists ? nxt.failed || 0 : 0;
      const writing = nxt.exists ? Math.max(0, nxt.total - nxt.ready - failed) : 0;
      const label = writing ? `Next questions (${nxt.ready} of ${nxt.total} written${failed ? `, ${failed} couldn’t be` : ''})`
        : failed ? `Next questions (${failed} couldn’t be written)` : 'Next questions';
      return h('div', {class: 'study-actions'}, h('button', {class: 'btn btn-primary', type: 'button', 'data-focus': 'next', disabled: busy(),
        onclick: () => nextBlock(v)}, label), h('button', {class: 'btn', type: 'button', disabled: busy(), onclick: () => endSession(v)}, 'End session'), note);
    }
    return h('div', {class: 'study-actions'}, h('button', {class: 'btn btn-primary', type: 'button', 'data-focus': 'next', disabled: busy(),
      onclick: () => endSession(v)}, v.closers ? 'Re-check and finish' : 'See the summary'),
    h('span', {class: 'form-note'}, 'That’s everything planned for now.'));
  }
  function keysHelp(v) {
    // N is named only where it can move on: not in the closing re-check, nor once nothing more is planned.
    const nx = v.next || {};
    return h('p', {class: 'small muted keys-help'}, 'Keys: ', h('span', {class: 'kbd'}, 'A'), '–', h('span', {class: 'kbd'}, 'F'),
      ' choose · ', h('span', {class: 'kbd'}, '1'), '–', h('span', {class: 'kbd'}, '5'), ' how sure · ', h('span', {class: 'kbd'}, 'Enter'),
      ' check', v.status === 'active' && (nx.exists || nx.more) ? [' · ', h('span', {class: 'kbd'}, 'N'), ' next questions'] : null);
  }
  function sessionSummary(v, title) {
    const s = v.summary || {};
    const moved = s.moved || [];
    const t = track(v.track);
    return h('div', {class: 'stack'}, pageHead(`${title}: done`, s.text || '', null, [h('a', {href: '#/study'}, 'Study')]),
      h('div', {class: 'kpis'},
        stat('Answers', String(s.answers || 0), isNum(s.accuracy) ? `${pct(s.accuracy)} right` : ''),
        stat('Time', `${Math.round(s.minutes || 0)} min`, 'of active study'),
        stat('New mistakes', String((s.opened || []).length), (s.opened || []).join(', ') || 'none'),
        (s.again || []).length ? stat('Missed again', String(s.again.length), s.again.join(', ')) : null,
        stat('Fixed for now', String((s.fixing || []).length), (s.fixing || []).length
          ? (s.next_check && Date.parse(s.next_check) > Date.now() ? `checked again ${relTime(s.next_check)}` : 'checked again next session')
          : 'none'),
        stat('Resolved', String((s.resolved || []).length), (s.resolved || []).join(', ') || 'none'),
        isNum(s.predicted) ? stat('Predicted score', pct(s.predicted), isNum(s.passing) ? `pass mark ${pct(s.passing)}` : '') : null),
      moved.length ? card({title: 'What moved', sub: 'What the tutor thinks you know, before and after this session', body: h('ul', {class: 'feed'}, moved.map((m) => {
        const up = (m.after || 0) >= (m.before || 0);
        return h('li', null, icon(up ? 'up' : 'down', up ? 'is-good' : 'is-critical', up ? 'Up' : 'Down'),
          h('a', {class: 'feed-title', href: link('concept', m.concept)}, m.title), h('span', {class: 'feed-when num'}, `${pct(m.before)} → ${pct(m.after)}`));
      }))}) : null,
      h('div', {class: 'study-actions'},
        canStudy() && t && t.status === 'active' ? h('button', {class: 'btn btn-primary', type: 'button', disabled: busy('start'),
          onclick: () => startStudy('learn', v.track)}, 'Keep going') : null,
        h('a', {class: 'btn', href: '#/study'}, 'Study home'), t ? h('a', {class: 'btn', href: link('track', v.track)}, 'See your progress') : null));
  }

  // -- mock exams --
  function seedMockPicks(v) {
    // After a reload, rebuild the picks from the answers the server saved.
    for (const [qid, a] of Object.entries((v.mock && v.mock.saved) || {})) {
      if (!study.picks[qid]) Object.assign(pick(qid), {choice: (a.choice || []).slice(), confidence: a.confidence || null,
        flag: !!a.flag, secs: a.seconds || 0});
    }
  }
  function mockAnswers(v) {
    // Every answer changed on this page is sent, a cleared one included: what is sent for a question
    // replaces what the server saved for it, and a question not sent keeps its saved answer.
    const out = {};
    for (const q of v.questions) {
      const p = study.picks[q.id];
      if (q.question && p && p.touched) {
        out[q.id] = {choice: p.choice, confidence: p.confidence, flag: !!p.flag, seconds: Math.round(p.secs || 0) || null};
      }
    }
    return out;
  }
  function mockCredit() {
    const m = study.mock;
    if (m.viewing) {
      const p = pick(m.viewing.qid);
      p.secs = (p.secs || 0) + (visibleMs() - m.viewing.since) / 1000;
    }
    m.viewing = null;
  }
  function mockGo(v, i) {
    mockCredit();
    study.mock.at = Math.max(0, Math.min(v.questions.length - 1, i));
    study.mock.review = false;
    render();
  }
  function scheduleMockSave() {
    clearTimeout(study.mock.saveTimer);
    study.mock.saveTimer = setTimeout(saveMock, 700);
  }
  async function saveMock() {
    const v = study.view;
    if (!v || !v.mock || v.mock.submitted) return;
    try {
      const out = await post(studyPath(v.id, 'mock', 'save'), {answers: mockAnswers(v)});
      if (out && out.now) study.offset = Date.parse(out.now) - Date.now();
    } catch (err) {
      if (['time_up', 'closed'].includes(err.code)) { toast(err.message, true); openSession(v.id); }
    }
  }
  function touch(q) { const p = pick(q.id); p.touched = true; return p; } // sent with every save from now on
  function mockPick(v, q, letter) {
    const p = touch(q);
    const select = q.question.select;
    p.full = false;
    if (select > 1) {
      if (p.choice.includes(letter)) p.choice = p.choice.filter((c) => c !== letter);
      else if (p.choice.length >= select) p.full = true;
      else p.choice = [...p.choice, letter].sort();
    } else p.choice = p.choice[0] === letter ? [] : [letter]; // pick it again to clear it
    scheduleMockSave();
    render();
  }
  function mockConfidence(v, q, n) {
    touch(q).confidence = n;
    scheduleMockSave();
    render();
  }
  function mockFlag(v, q) {
    const p = touch(q);
    p.flag = !p.flag;
    scheduleMockSave();
    render();
  }
  function submitMock(v) {
    clearTimeout(study.mock.saveTimer);
    mockCredit();
    return sessionAct('mock/submit', {answers: mockAnswers(v)}, {done: () => { study.mock.review = false; window.scrollTo(0, 0); }});
  }
  function pageMock(v) {
    const m = v.mock;
    const t = track(v.track);
    const name = `Mock exam${t ? ` · ${shortTitle(t)}` : ''}`;
    if (v.status === 'done' && (!m.started || !m.debrief)) return mockCancelled(v, name);
    if (v.status === 'done' || m.submitted) return mockDebrief(v, name);
    if (!m.started) return mockLobby(v, name);
    seedMockPicks(v);
    return study.mock.review ? mockReview(v, name) : mockRunning(v, name);
  }
  function mockCancelled(v, name) {
    const never = (v.summary || {}).reason === 'never started';
    return h('div', {class: 'stack'}, pageHead(name, '', null, [h('a', {href: '#/study'}, 'Study')]),
      card({body: [empty('Cancelled: nothing was recorded.', never ? 'It was never started, so it was closed: no answers or score were kept.'
        : 'It was cancelled before it started: no answers or score were kept.'),
      h('div', {class: 'study-actions', style: {justifyContent: 'center'}}, h('a', {class: 'btn btn-primary', href: '#/study'}, 'Back to Study'))]}));
  }
  async function retryFailed(v) {
    // Write each failed question again, one request each, stopping at the first refusal (the cap, say).
    const failed = v.questions.filter((q) => q.state === 'failed');
    if (!failed.length || busy('retry-all')) return;
    study.keys.add('retry-all');
    try {
      for (const q of failed) if (!(await act(studyPath(v.id, 'retry'), {qid: q.id}, {key: `retry:${q.id}`}))) break;
    } finally {
      study.keys.delete('retry-all');
      redraw();
    }
  }
  function mockLobby(v, name) {
    const m = v.mock;
    const writing = isNum(m.writing) ? m.writing : Math.max(0, m.total - m.ready - m.failed);
    // As the server decides: some are ready, and either enough are or nothing is still being written.
    const canStart = m.ready > 0 && (!writing || m.ready >= m.start_at);
    const short = canStart && m.ready < m.start_at;
    const failed = v.questions.filter((q) => q.state === 'failed');
    const codes = (m.failed_codes || []).length ? m.failed_codes : [...new Set(failed.map((q) => q.code || 'failed'))];
    const reasons = [...new Set(codes.map((code) => FAILED[code]
      || (failed.find((q) => (q.code || 'failed') === code && q.error) || {}).error || 'The model couldn’t write a usable question.'))];
    const cap = failed.length > 0 && failed.every((q) => q.code === 'daily_cap');
    const next = writing ? 'The exam goes ahead without them; you can try them again meanwhile.'
      : short ? `You can start with the ${plural(m.ready, 'question')} that ${m.ready === 1 ? 'is' : 'are'} ready, or try the others again.`
        : m.ready ? 'The exam goes ahead without them, or try them again first.' : 'Try again, or cancel this mock exam.';
    return h('div', {class: 'stack'}, pageHead(name, `${m.total} questions · ${m.minutes} minutes · weighted like the exam · no feedback until you submit`,
      null, [h('a', {href: '#/study'}, 'Study')]),
    card({body: [h('div', {class: 'hero'}, h('div', {class: 'big'}, `${m.ready}/${m.total}`),
      h('div', {class: 'hero-label'}, writing ? 'questions written so far' : 'questions ready')),
    h('div', {class: 'bar-h', style: {marginTop: '12px'}}, h('span', {style: {width: `${(m.ready / Math.max(1, m.total)) * 100}%`}})),
    h('ul', {class: 'md small', style: {marginTop: '12px'}},
      h('li', null, 'The timer starts when you press Start, and the server keeps the time: closing the page doesn’t stop it.'),
      h('li', null, 'Choose-two questions score only when both choices are right, as on the exam.'),
      h('li', null, 'Say how sure you are on each answer: the review then shows where you were confidently wrong.'),
      writing ? h('li', null, `Still writing ${plural(writing, 'question')}. You can start once ${m.start_at} are ready; the rest arrive while you work.`) : null),
    failed.length ? h('div', {class: `callout${writing ? '' : ' is-warning'}`, style: {marginTop: '12px'}}, icon('alert', 'is-warning'), h('div', null,
      h('b', null, !m.ready && !writing ? 'None of the questions could be written.' : `${plural(failed.length, 'question')} couldn’t be written.`),
      reasons.map((r) => h('p', {class: 'small'}, r)),
      cap ? h('p', {class: 'small muted'}, 'Raise the daily cap in Settings on the Study page, then try again.') : null,
      h('p', {class: 'small muted'}, next))) : null,
    h('div', {class: 'study-actions', style: {marginTop: '12px'}},
      canStart || writing ? h('button', {class: 'btn btn-primary', type: 'button', 'data-focus': 'begin', disabled: !canStart || busy(),
        onclick: () => sessionAct('mock/begin')}, !canStart ? `Writing… (${m.ready} of the ${m.start_at} needed to start)`
        : short ? `Start with the ${m.ready} ready` : 'Start the exam') : null,
      failed.length ? h('button', {class: `btn${canStart || writing ? '' : ' btn-primary'}`, type: 'button', 'data-focus': 'retry-all',
        disabled: busy('retry-all') || busy(), onclick: () => retryFailed(v)},
      busy('retry-all') ? 'Trying again…' : failed.length > 1 ? `Try the ${failed.length} again` : 'Try it again') : null,
      h('button', {class: 'btn', type: 'button', disabled: busy(), onclick: () => {
        if (window.confirm('Cancel this mock exam? Nothing is recorded.')) sessionAct('end');
      }}, 'Cancel'))]}));
  }
  function mockState(v) {
    const qs = v.questions;
    const answered = qs.filter((q) => q.question && (pick(q.id).choice || []).length);
    return {qs, answered, flagged: qs.filter((q) => pick(q.id).flag),
      unsure: answered.filter((q) => !pick(q.id).confidence), blank: qs.filter((q) => q.question && !(pick(q.id).choice || []).length)};
  }
  function mockTopBar(v, name, extra) {
    const m = v.mock;
    const left = Math.max(0, Math.ceil((Date.parse(m.deadline) - serverNow()) / 1000)); // 0:00 only once time is up
    return card({body: h('div', {class: 'study-bar'}, h('div', {class: 'study-meta'}, h('span', null, h('b', null, name)), extra),
      h('div', {class: 'study-actions'}, h('span', null, 'Time left ', h('span', {class: `timer${left < 300 ? ' is-low' : ''}`, id: 'mock-timer'}, clockText(left))),
        study.mock.review ? null : h('button', {class: 'btn btn-primary', type: 'button', disabled: busy(),
          onclick: () => { mockCredit(); study.mock.review = true; render(); window.scrollTo(0, 0); }}, 'Review and submit')))});
  }
  function mockRunning(v, name) {
    const st = mockState(v);
    const qs = st.qs;
    const at = Math.max(0, Math.min(qs.length - 1, study.mock.at || 0));
    const q = qs[at];
    if (!study.mock.viewing || study.mock.viewing.qid !== q.id) study.mock.viewing = {qid: q.id, since: visibleMs()};
    const p = pick(q.id);
    let body;
    if (q.state === 'writing') {
      body = h('div', {class: 'qwriting'}, h('span', {class: 'spinner', 'aria-hidden': 'true'}), 'Still being written: go on to the next question and come back.');
    } else if (!q.question) {
      const again = q.state === 'failed' && q.code !== 'daily_cap';
      body = [h('p', {class: 'muted'}, again ? 'This question couldn’t be written. It doesn’t count unless it is written again.'
        : 'This question couldn’t be written. It doesn’t count.'),
      again ? h('div', {class: 'study-actions', style: {marginTop: '10px'}}, retryButton(q)) : null];
    } else {
      const multi = q.question.select > 1;
      body = [stemBlock(q.question, q.id),
        h('ul', {class: 'opts', role: multi ? 'group' : 'radiogroup', 'aria-label': 'Options'}, q.question.options.map((o) => h('li', null,
          h('button', {class: 'opt', type: 'button', role: multi ? 'checkbox' : 'radio', 'aria-checked': String(p.choice.includes(o.key)),
            'data-focus': `m:${q.id}:${o.key}`, onclick: () => mockPick(v, q, o.key)},
          h('span', {class: 'opt-key'}, o.key), h('span', {class: 'opt-text'}, richText(o.text)))))),
        h('div', {class: 'conf', role: 'radiogroup', 'aria-label': 'How sure are you?'}, h('span', {class: 'conf-label'}, 'How sure?'),
          CONF.map(([n, label]) => h('button', {type: 'button', role: 'radio', 'aria-checked': String(p.confidence === n),
            'data-focus': `m:${q.id}:c${n}`, onclick: () => mockConfidence(v, q, n)}, h('b', null, n), label)))];
    }
    const grid = h('div', {class: 'navgrid', role: 'group', 'aria-label': 'Questions'}, qs.map((x, i) => {
      const px = pick(x.id);
      const done = (px.choice || []).length;
      return h('button', {type: 'button', class: [done ? 'is-answered' : '', px.flag ? 'is-flagged' : '',
        x.state === 'writing' ? 'is-writing' : ''].filter(Boolean).join(' '), 'aria-current': String(i === at), 'data-focus': `m:go:${x.id}`,
      'aria-label': `Question ${i + 1}${done ? ', answered' : ''}${px.flag ? ', flagged' : ''}${x.state === 'writing' ? ', still being written' : ''}`,
      onclick: () => mockGo(v, i)}, String(i + 1));
    }));
    return h('div', {class: 'stack'},
      mockTopBar(v, name, [h('span', null, `Question ${at + 1} of ${qs.length}`), h('span', null, `${st.answered.length} answered`),
        st.flagged.length ? h('span', null, `${st.flagged.length} flagged`) : null]),
      h('section', {class: 'qcard is-active', 'aria-label': `Question ${at + 1}`, 'data-focus-scope': 'm:card'},
        h('div', {class: 'qhead'}, h('span', {class: 'qnum'}, `${at + 1}.`),
          q.question && q.question.select > 1 ? h('span', {class: 'tag'}, 'Choose two') : null), body,
        h('div', {class: 'study-actions', style: {marginTop: '14px'}},
          h('button', {class: 'btn', type: 'button', 'data-focus': 'm:prev', disabled: at === 0, onclick: () => mockGo(v, at - 1)}, 'Previous'),
          h('button', {class: 'btn', type: 'button', 'data-focus': 'm:next', disabled: at >= qs.length - 1, onclick: () => mockGo(v, at + 1)}, 'Next'),
          q.question ? h('button', {class: 'btn', type: 'button', 'data-focus': 'm:flag', 'aria-pressed': String(!!p.flag), onclick: () => mockFlag(v, q)},
            p.flag ? 'Unflag' : 'Flag for review') : null)),
      card({title: 'Questions', sub: 'Answered are filled; flagged have a yellow edge', body: grid}),
      h('p', {class: 'small muted keys-help'}, 'Keys: ', h('span', {class: 'kbd'}, 'A'), '–', h('span', {class: 'kbd'}, 'F'), ' choose · ',
        h('span', {class: 'kbd'}, '1'), '–', h('span', {class: 'kbd'}, '5'), ' how sure · ', h('span', {class: 'kbd'}, '←'), ' ',
        h('span', {class: 'kbd'}, '→'), ' move · ', h('span', {class: 'kbd'}, 'M'), ' flag'));
  }
  function mockReview(v, name) {
    const st = mockState(v);
    const over = timeUp(v.mock);
    const index = (q) => v.questions.indexOf(q);
    const jump = (rows, title) => (rows.length ? h('div', {class: 'chips'}, rows.map((q) => h('button', {class: 'chip', type: 'button',
      'data-focus': `m:jump:${title}:${q.id}`, onclick: () => { mockGo(v, index(q)); focusLater('m:card'); }}, `Question ${index(q) + 1}`)))
      : h('span', {class: 'small muted'}, 'none'));
    const section = (title, rows, note) => h('div', {class: 'review-row'}, h('div', null, h('b', null, `${title} (${rows.length})`),
      note ? h('div', {class: 'small muted'}, note) : null), jump(rows, title));
    // Before the deadline the server wants a confidence on every answer; once time is up it takes them as they are.
    const blocked = st.unsure.length > 0 && !over;
    return h('div', {class: 'stack'}, mockTopBar(v, name, [h('span', null, 'Review before you submit')]),
      card({title: 'Before you submit', sub: `${st.answered.length} of ${st.qs.length} answered. Unanswered questions count as wrong.`, body: [
        section('Not answered', st.blank, 'They score zero.'),
        section('Flagged', st.flagged, 'The ones you wanted another look at.'),
        section('No confidence yet', st.unsure, over ? 'Time is up, so they go in without one.' : 'Say how sure you were: it is needed to submit.'),
        h('div', {class: 'study-actions', style: {marginTop: '14px'}},
          h('button', {class: 'btn btn-primary', type: 'button', disabled: busy() || blocked, 'data-focus': 'submit',
            onclick: () => submitMock(v)}, 'Submit the exam'),
          h('button', {class: 'btn', type: 'button', 'data-focus': 'm:back', onclick: () => { study.mock.review = false; render(); focusLater('m:card'); }},
            'Back to the questions'),
          blocked ? h('span', {class: 'form-note'}, 'Add how sure you were on every answered question first.') : null)]}));
  }
  function updateMockTimer() {
    const el = document.getElementById('mock-timer');
    const v = study.view;
    if (!el || !v || !v.mock || !v.mock.deadline || v.mock.submitted) return;
    const ms = Date.parse(v.mock.deadline) - serverNow();
    const left = Math.max(0, Math.ceil(ms / 1000));
    el.textContent = clockText(left);
    el.className = `timer${left < 300 ? ' is-low' : ''}`;
    if (ms > 0) return;
    const mk = study.mock;
    if (!mk.overShown) { mk.overShown = true; renderSoon(); } // time is up: Submit no longer waits for every confidence
    pollSession(); // and should the page never manage to submit, the poll sees the server's sweep do it
    // A second's margin: the page's clock trails the server's a little, and before the deadline the
    // server refuses a submit that lacks a confidence. A failed submit is tried again a few seconds on.
    if (ms > -1000 || busy() || mk.autoSubmitted || Date.now() < mk.retryAt) return;
    mk.autoSubmitted = true;
    if (!mk.toldTimeUp) { mk.toldTimeUp = true; toast('Time is up: submitting your exam.'); }
    submitMock(v).then((out) => {
      if (!out && study.mock === mk) { mk.autoSubmitted = false; mk.retryAt = Date.now() + 3000; }
    });
  }
  function mockDebrief(v, name) {
    const m = v.mock;
    const d = m.debrief || {};
    const t = track(v.track);
    const passing = d.passing;
    const done = v.status === 'done';
    const misses = v.questions.filter((q) => q.result && q.result.result !== 'correct' && !q.result.voided && !q.result.blank);
    const blanks = v.questions.filter((q) => q.result && q.result.blank && !q.result.voided);
    const voided = v.questions.filter((q) => q.result && q.result.voided);
    const review = (q) => attachLearn(h('div', null, stemBlock(q.question), verdictLine(q.result), resultOptions(q.result, q.id), disputeBlock(q), conceptLinks(q)),
      `mock:${v.id}:${q.id}`, termsOf(q), {track: v.track, concept: q.concept}, q.title);
    const gap = isNum(passing) && isNum(d.score) ? Math.round((passing - d.score) * 100) : null;
    const concepts = new Map(v.questions.filter((q) => q.title && q.concept).map((q) => [q.title, q.concept]));
    const weakest = d.weakest || [];
    const opened = d.opened || [];
    const gate = done ? d.gate : null; // the booking checks, once the review is finished and the score logged
    return h('div', {class: 'stack'}, pageHead(`${name}: results`, done ? ((v.summary || {}).text || '')
      : 'Go through the misses, dispute any question you think is wrong, then finish the review to log the score.', null, [h('a', {href: '#/study'}, 'Study')]),
    card({body: [h('div', {class: 'hero'}, h('div', {class: 'hero-figure'}, pct(d.score)),
      h('div', {class: 'hero-label'}, `${d.right || 0} of ${d.total || 0} right in ${Math.round(d.minutes || 0)} of ${m.minutes} minutes`,
        h('div', {class: 'small'}, `${plural(misses.length, 'wrong answer')} · ${d.unanswered || 0} not answered`),
        isNum(passing) ? h('div', {class: 'small'}, gap <= 0 ? `At or above the ${pct(passing)} pass mark` : `${plural(gap, 'point')} under the ${pct(passing)} pass mark`) : null,
        d.timed_out ? h('div', {class: 'small'}, 'Time ran out; unanswered questions count as wrong.') : null)),
    isNum(passing) ? h('div', {style: {marginTop: '14px'}}, meter(d.score, [{at: passing, label: `Pass ${pct(passing)}`, align: 'end'}], `Score ${pct(d.score)}`)) : null,
    h('div', {class: 'kpis', style: {marginTop: '14px'}},
      miniStat('Wrong', String(misses.length), opened.length ? `logged as ${opened.join(', ')}` : 'logged to fix'),
      miniStat('Not answered', String(d.unanswered || 0), 'counted as wrong, not logged'),
      miniStat('Sure but wrong', String(d.confident_misses || 0), 'fix these first'),
      miniStat('Voided', String(d.voided || 0), 'left out of the score'),
      miniStat('Predicted', pct(d.predicted), 'from your mastery')),
    d.dropped ? h('p', {class: 'small muted', style: {marginTop: '12px'}},
      `${plural(d.dropped, 'question')} couldn’t be written and ${d.dropped === 1 ? 'was' : 'were'} left out of the paper.`) : null]}),
    gate && t ? card({title: 'Ready to book?', sub: gateSub(gate), body: gateChecks(gate, t)}) : null,
    weakest.length ? card({title: 'Weakest on this paper', sub: 'The concepts that lost the most points, wrong or not answered', body: h('ul', {class: 'feed'},
      weakest.map((title) => h('li', null, icon('x', 'is-critical'), concepts.get(title)
        ? h('a', {class: 'feed-title', href: link('concept', concepts.get(title))}, title) : h('span', {class: 'feed-title'}, title))))}) : null,
    (d.by_area || []).length ? card({title: 'By area', body: h('div', {class: 'rows'}, d.by_area.map((a) => h('div', {class: 'row'},
      h('div', {class: 'row-name'}, h('b', null, a.name), h('span', {class: 'small muted'}, plural(a.questions, 'question'))),
      h('div', {class: 'row-bar'}, barH(a.score, `${a.name}: ${pct(a.score)}`)), h('div', {class: 'row-figure'}, h('b', null, pct(a.score))))))}) : null,
    card({title: `Wrong answers (${misses.length})`,
      sub: 'Each one is now a mistake your next sessions fix first.' + (d.full_length ? ' A full-length mock counts toward booking once the review is finished.' : ''),
      body: misses.length ? misses.map((q) => folded(`mock:${q.id}`, `${q.n}. ${q.title || ''} · you chose ${(q.result.choice || []).join(' and ')}`, review(q)))
        : !d.total ? empty('Nothing was scored.', 'Every question was voided or left out.')
          : empty('No wrong answers.', blanks.length ? 'Every question you answered was right.' : 'Every answer was right.')}),
    blanks.length ? card({title: `Not answered (${blanks.length})`, sub: 'They count against the score but are not logged as mistakes.',
      body: blanks.map((q) => folded(`mock:${q.id}`, `${q.n}. ${q.title || ''}`, review(q)))}) : null,
    voided.length ? card({title: `Voided (${voided.length})`, body: voided.map((q) => h('p', {class: 'small'}, `${q.n}. ${q.title || ''}`,
      q.dispute && q.dispute.explanation ? [' · ', richText(q.dispute.explanation)] : null))}) : null,
    h('div', {class: 'study-actions'},
      !done ? h('button', {class: 'btn btn-primary', type: 'button', 'data-focus': 'finish', disabled: busy(), onclick: () => sessionAct('mock/finish')},
        'Finish the review and log the score') : null,
      canStudy() && misses.length ? h('button', {class: `btn${done ? ' btn-primary' : ''}`, type: 'button', disabled: busy('start'),
        onclick: () => startStudy('learn', v.track)}, 'Fix these now') : null,
      h('a', {class: 'btn', href: link('track', v.track)}, 'See your progress')));
  }

  // -- the study home page --
  function pageStudyHome() {
    if (!app.snapshot) loadStudyHome(); // a snapshot file has no server to ask
    const info = {...studyInfo(), ...(study.home || {})};
    const head = pageHead('Study', info.writer ? `Questions come from ${info.writer}. Your answers are recorded as you go.`
      : 'Answer exam-style questions here; the tutor keeps score.');
    if (app.snapshot) {
      return h('div', {class: 'stack'}, head, card({body: empty('Studying needs the dashboard server.',
        'This is a snapshot file. Run polymath.py serve to study in the browser.')}));
    }
    if (!info.available) return h('div', {class: 'stack'}, head, card({body: offReason(info)}));
    const list = tracks().filter((t) => t.status === 'active');
    return h('div', {class: 'stack'}, head, writerStatus(info), resumeCard(info.active || []),
      list.length ? list.map(studyTrackCard) : card({body: empty('No active goals.', 'Set up a goal in Claude Code with /polymath:learn, or reactivate one on its Settings tab.')}),
      card({title: 'Coach', sub: 'Talk through your plan, your pace or a worry, with your real numbers in view.',
        body: h('a', {class: 'btn btn-primary', href: '#/coach'}, 'Talk to your coach')}),
      settingsCard(info));
  }
  function offReason(info) {
    const why = {
      'read-only': ['This dashboard was started read-only.', 'Restart it without --read-only to record answers here.'],
      'no-writer': ['No AI model is set up.', 'Choose one on the AI model page.'],
      'not-owner': ['Another dashboard is already running study for this data folder.', 'Use that one, or stop it and restart this one.'],
    }[info.reason] || ['Studying here is off.', info.error || 'Restart the dashboard to try again.'];
    return [empty(why[0], why[1]), info.reason === 'no-writer' && !models.absent
      ? h('div', {class: 'study-actions', style: {justifyContent: 'center'}}, h('a', {class: 'btn btn-primary', href: '#/models'}, 'Choose an AI model')) : null];
  }
  function writerStatus(info) {
    const a = info.auth;
    const u = info.usage;
    if (a && !a.ok) {
      if (models.status && !models.status.ok) return null; // the banner at the top of the page says it, with what to do
      return h('div', {class: 'callout is-warning'}, icon('alert', 'is-warning'), h('div', null, h('b', null, a.problem || 'Your AI model isn’t ready.'),
        h('div', {class: 'small muted'}, 'No questions can be written until then. ', h('a', {href: '#/models'}, 'Open AI model'))));
    }
    const bits = [];
    if (a && a.ok && a.method === 'none') bits.push('practice questions only: nothing is sent to an AI model');
    else if (a && a.ok && a.method === 'local') bits.push('runs on your computer');
    else if (a && a.ok && a.method === 'api-key') bits.push('billed to your API key');
    else if (a && a.ok) bits.push(a.plan ? `Signed in to Claude (${capital(a.plan)})` : 'Signed in to Claude');
    if (u && a && a.method === 'local') bits.push(`today: ${plural(u.calls, 'call')}`);
    else if (u && a && a.method === 'api-key') bits.push(`today: ${plural(u.calls, 'call')} of ${u.limit_calls}`);
    else if (u) bits.push(`today: ${plural(u.calls, 'call')}, about $${Number(u.usd || 0).toFixed(2)} at API prices, against a cap of $${Number(u.limit_usd || 0).toFixed(2)}`);
    if (u && u.rate && u.rate.status && u.rate.status !== 'allowed') bits.push(`Usage: ${String(u.rate.status).replace(/_/g, ' ')}`);
    const over = u && u.over;
    return h('div', {class: `callout${over ? ' is-warning' : ''}`}, icon(over ? 'alert' : 'check', over ? 'is-warning' : 'is-good'),
      h('div', null, h('b', null, over ? 'Today’s study cap is reached.' : 'Ready to study.'),
        h('div', {class: 'small muted'}, bits.join(' · ')),
        over ? h('div', {class: 'small muted'}, 'Raise the cap in Settings below.') : null));
  }
  function resumeCard(active) {
    if (!active.length) return null;
    return card({title: 'Pick up where you left off', body: h('ul', {class: 'feed'}, active.map((a) => {
      const t = track(a.track);
      const m = a.mock;
      const left = m && m.started && !m.submitted && m.deadline ? Math.max(0, Math.round((Date.parse(m.deadline) - serverNow()) / 1000)) : null;
      const meta = m && m.submitted ? 'submitted: finish the review' : left != null ? `running: ${clockText(left)} left`
        : `${plural(a.answered, 'answer')}, ${a.right} right`;
      return h('li', null, icon(left != null ? 'clock' : 'dot', left != null ? 'is-warning' : 'is-neutral'),
        h('div', null, h('a', {class: 'feed-title', href: link('study', a.id)}, `${MODE_NAME[a.mode] || a.mode}${t ? ` · ${shortTitle(t)}` : ''}`),
          h('div', {class: 'feed-meta'}, meta)), h('div', {class: 'feed-when'}, relTime(a.updated)));
    }))});
  }
  function studyTrackCard(t) {
    const nxt = t.next || {};
    const exam = t.exam || {};
    const per = exam.questions && exam.minutes ? exam.minutes / exam.questions : 1.75;
    const id = `quiz-${t.slug}`;
    const form = app.drafts[id] || {};
    const remember = (k, value) => { (app.drafts[id] = app.drafts[id] || {})[k] = value; };
    const areaSel = h('select', {class: 'input', 'aria-label': 'Area', onchange: (e) => remember('area', e.target.value)},
      h('option', {value: ''}, 'Weakest concepts first'), h('option', {value: '!mistakes', selected: form.area === '!mistakes'}, 'Only my mistakes'),
      (t.domains || []).map((d) => h('option', {value: d.id, selected: form.area === d.id}, d.name)));
    const countSel = h('select', {class: 'input', 'aria-label': 'Questions', onchange: (e) => remember('count', e.target.value)},
      [5, 10, 20].map((n) => h('option', {value: String(n), selected: String(form.count || 10) === String(n)}, `${n} questions`)));
    const due = (nxt.mistakes_due || 0) + (nxt.due_total || 0);
    const waiting = [nxt.mistakes_due ? plural(nxt.mistakes_due, 'mistake') : null, nxt.due_total ? plural(nxt.due_total, 'review') : null,
      nxt.new_available ? `${nxt.new_available} new` : null].filter(Boolean).join(', ');
    const block = (app.state.learner.preferences || {}).block_size || 3;
    const start = (mode, extra) => () => startStudy(mode, t.slug, extra);
    return card({title: t.title, sub: [isNum(t.predicted_score) ? `${pct(t.predicted_score)} predicted` : null,
      isNum(t.passing_score) ? `pass ${pct(t.passing_score)}` : null, t.target_date ? `exam ${fmtShort(localDay(t.target_date))}` : null].filter(Boolean).join(' · '),
    actions: [badge(readiness(t))],
    body: [h('div', {class: 'mode-grid'},
      h('button', {class: 'mode', type: 'button', disabled: busy('start'), onclick: start('learn')}, h('b', null, 'Learn'),
        h('span', null, `Question-first, ${plural(block, 'question')} at a time. Waiting: ${waiting || 'nothing'}.`)),
      h('button', {class: 'mode', type: 'button', disabled: busy('start') || !due, onclick: start('review')}, h('b', null, 'Review'),
        h('span', null, due ? `${plural(due, 'item')} due: mistakes first, then what you are about to forget.` : 'Nothing due right now.')),
      h('button', {class: 'mode', type: 'button', disabled: busy('start'), onclick: start('mock', {count: 'mini'})}, h('b', null, 'Mini mock'),
        h('span', null, `20 questions, ${Math.round(20 * per)} minutes, weighted like the exam.`)),
      exam.questions ? h('button', {class: 'mode', type: 'button', disabled: busy('start'), onclick: start('mock', {count: 'full'})},
        h('b', null, 'Full mock'), h('span', null, `${exam.questions} questions, ${exam.minutes || Math.round(exam.questions * per)} minutes. Counts toward booking.`)) : null),
    h('div', {class: 'filters', style: {marginTop: '14px', marginBottom: 0}}, h('b', {class: 'small'}, 'Quiz'), areaSel, countSel,
      h('button', {class: 'btn', type: 'button', disabled: busy('start'), onclick: () => {
        const f = app.drafts[id] || {};
        const scope = f.area === '!mistakes' ? {mistakes: true} : f.area ? {area: f.area} : {};
        startStudy('quiz', t.slug, {count: Number(f.count || 10), scope});
      }}, 'Start the quiz'))]});
  }
  function settingsCard(info) {
    const prefs = (app.state.learner || {}).preferences || {};
    const u = info.usage || {};
    const id = 'study-settings';
    const blockSel = h('select', {class: 'input'}, [1, 2, 3, 4, 5].map((n) => h('option', {value: String(n),
      selected: Number(prefs.block_size || 3) === n}, n === 1 ? 'One at a time' : `${n} at a time`)));
    // Any whole number of minutes, and any amount of dollars, the server accepts: a value set by the coach or the
    // command line (42 minutes, $7.25) must never make the form invalid.
    const minutes = h('input', {class: 'input', type: 'number', min: 10, max: 480, step: 1, value: String(prefs.session_minutes || 30)});
    const cap = h('input', {class: 'input', type: 'number', min: 0.5, max: 200, step: 'any',
      value: String(isNum(u.limit_usd) ? u.limit_usd : (prefs.web_daily_usd || 10))});
    return card({title: 'Settings', sub: 'How sessions run here and in Claude Code', body: h('form', {class: 'form-grid', onsubmit: async (e) => {
      e.preventDefault();
      try {
        await post('api/preferences', {block_size: Number(blockSel.value), session_minutes: Number(minutes.value), web_daily_usd: Number(cap.value)});
        delete app.drafts[id];
        toast('Saved.');
        await refresh();
        loadStudyHome(true);
      } catch (err) { toast(err.message || String(err), true); }
    }}, field(id, 'block', 'Questions per block', blockSel), field(id, 'minutes', 'Session length in minutes', minutes),
    field(id, 'cap', 'Daily cap in dollars, at API prices', cap),
    h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-primary', type: 'submit'}, 'Save'),
      h('span', {class: 'form-note'}, 'The cap stops new questions for the day.')))});
  }
  function studyButtons(t, label) {
    // Start studying from any page: shown only where the dashboard can run study.
    if (!canStudy() || !t || t.status !== 'active') return null;
    const nxt = t.next || {};
    const due = (nxt.mistakes_due || 0) + (nxt.due_total || 0);
    return h('div', {class: 'study-actions', style: {marginTop: '12px'}},
      h('button', {class: 'btn btn-primary', type: 'button', disabled: busy('start'), onclick: () => startStudy('learn', t.slug)}, label || 'Study now'),
      due ? h('button', {class: 'btn', type: 'button', disabled: busy('start'), onclick: () => startStudy('review', t.slug)}, `Review ${due} due`) : null,
      h('a', {class: 'btn', href: '#/study'}, 'More ways to study'));
  }

  function gateAction(t, g) {
    if (!canStudy() || t.status !== 'active' || !g || g.met) return null;
    const mocks = g.checks.find((c) => c.id === 'mocks');
    const others = g.checks.filter((c) => c.id !== 'mocks' && !c.met);
    return h('div', {class: 'study-actions', style: {marginTop: '12px'}},
      others.length ? h('button', {class: 'btn', type: 'button', disabled: busy('start'), onclick: () => startStudy('learn', t.slug)}, 'Study to close the gap') : null,
      mocks && !mocks.met ? h('button', {class: 'btn', type: 'button', disabled: busy('start'),
        onclick: () => startStudy('mock', t.slug, {count: 'full'})}, 'Take a full mock') : null);
  }
  function trackStudyButton(t) {
    if (!canStudy() || t.status !== 'active') return null;
    return h('button', {class: 'btn btn-primary', type: 'button', disabled: busy('start'), onclick: () => startStudy('learn', t.slug)}, 'Study now');
  }
  function conceptQuiz(c, t) {
    if (!canStudy() || !t || t.status !== 'active') return null;
    return h('button', {class: 'btn btn-small', type: 'button', disabled: busy('start'),
      onclick: () => startStudy('quiz', t.slug, {count: 5, scope: {concept: c.concept}})}, 'Quiz me on this');
  }
  function mistakesAction() {
    if (!canStudy()) return null;
    const open = (slug) => (app.state.mistakes || []).filter((m) => m.status === 'open' && (m.tracks || []).includes(slug)).length;
    const goal = app.ui.mistakesTrack ? track(app.ui.mistakesTrack) : tracks().find((t) => t.status === 'active' && open(t.slug));
    if (!goal || goal.status !== 'active' || !open(goal.slug)) return null;
    const n = open(goal.slug);
    return h('button', {class: 'btn btn-primary', type: 'button', disabled: busy('start'),
      onclick: () => startStudy('quiz', goal.slug, {count: Math.min(20, Math.max(5, n)), scope: {mistakes: true}})},
    `Fix ${n > 1 ? `these ${n}` : 'it'} now`);
  }

  // -- coach --
  async function loadChat(force) {
    if (study.chatLoading || (!force && study.chat && Date.now() - study.chatAt < 30000)) return;
    study.chatLoading = true;
    try { study.chat = await getJSON('api/coach'); }
    catch (err) { study.chat = {messages: [], busy: false, error: err.message || String(err)}; }
    finally { study.chatLoading = false; study.chatAt = Date.now(); }
    if (app.route && app.route.name === 'coach') renderSoon();
    pollChat();
  }
  function pollChat() {
    if (study.chatPoll || !study.chat || !study.chat.busy) return;
    study.chatPoll = setTimeout(async () => {
      let changed = false;
      try {
        const fresh = await getJSON('api/coach');
        const last = (c) => { const ms = (c && c.messages) || []; return ms.length ? `${ms.length}:${ms[ms.length - 1].ts}` : '0'; };
        changed = !study.chat || fresh.busy !== study.chat.busy || last(fresh) !== last(study.chat); // else nothing to redraw
        study.chat = fresh;
        study.chatAt = Date.now();
      } catch (err) { /* try again */ }
      study.chatPoll = 0;
      if (changed && app.route && app.route.name === 'coach') renderSoon();
      pollChat();
    }, 1500);
  }
  async function sendChat(text) {
    const msg = String(text || '').trim();
    if (!msg || (study.chat && study.chat.busy) || busy('chat')) return;
    study.chatSent = Date.now();
    const out = await act('api/coach', {message: msg}, {key: 'chat'});
    if (out && out.messages) {
      study.chat = out;
      study.chatAt = Date.now();
      delete app.drafts.coach;
      redraw();
      pollChat();
    }
  }
  function goalName(slug) { const t = track(slug); return t ? shortTitle(t) : slug; }
  function areaName(slug, area) {
    const t = track(slug);
    return ((t && t.domains) || []).find((d) => d.id === area)?.name || area;
  }
  function proposalText(p) {
    switch (p.kind) {
      case 'target_date': return `Set the ${goalName(p.track)} exam date to ${fmtDay(localDay(p.value))}`;
      case 'track_status': return `Mark ${goalName(p.track)} as ${p.value}`;
      case 'daily_minutes': return `Plan ${p.value} minutes a day for ${goalName(p.track)}`;
      case 'session_minutes': return `Make sessions ${p.value} minutes`;
      case 'block_size': return p.value === '1' ? 'Ask one question at a time' : `Ask ${p.value} questions at a time`;
      case 'start_level': return `Pitch new topics at ${p.value} level`;
      case 'desired_retention': return `Aim to remember ${pct(Number(p.value))} at each review`;
      case 'note': return `Add to your notes: ${p.value}`;
      case 'study': return {learn: 'Start a learn session', review: 'Start a review', 'mock-mini': 'Take a mini mock',
        'mock-full': 'Take a full mock'}[p.value] || `Quiz me on ${areaName(p.track, p.value.slice(5))}`;
      default: return p.value;
    }
  }
  // The numbers a proposal may set, as the server takes them: [lowest, highest, whole numbers only].
  const PROPOSAL_NUMBERS = {daily_minutes: [1, 1440, true], session_minutes: [10, 480, true], block_size: [1, 5, true],
    desired_retention: [0.8, 0.97, false]};
  function proposalNumber(p) {
    // A bare number in range, or null ("about 45" or "" would otherwise go out as null and clear the setting).
    const [low, high, whole] = PROPOSAL_NUMBERS[p.kind];
    const text = String(p.value == null ? '' : p.value).trim();
    const n = /^\d+(\.\d+)?$/.test(text) ? Number(text) : NaN;
    if (!isNum(n) || n < low || n > high) return null;
    return whole ? Math.round(n) : n;
  }
  // A proposal the page can offer as one click, checked as the server now cleans new ones (clean_proposal in
  // polymath_study.py), or null. The coach history can still hold older ones that were never cleaned ("about 45",
  // an empty number, a quiz on an area the goal doesn't have): those would draw as odd labels that only refuse.
  const STUDY_PROPOSALS = ['learn', 'review', 'mock-mini', 'mock-full'];
  const START_LEVELS = ['beginner', 'intermediate', 'experienced', 'expert'];
  function usableProposal(p) {
    if (!p || typeof p !== 'object') return null;
    const out = {...p, value: String(p.value == null ? '' : p.value).trim()};
    if (['target_date', 'track_status', 'daily_minutes', 'study'].includes(p.kind) && !track(p.track)) {
      const all = app.state.tracks || []; // with one goal, a proposal that names none means that one
      if (all.length !== 1) return null;
      out.track = all[0].slug;
    }
    if (Object.prototype.hasOwnProperty.call(PROPOSAL_NUMBERS, p.kind)) {
      const n = proposalNumber(out);
      return n === null ? null : {...out, value: String(n)};
    }
    switch (p.kind) {
      case 'study': {
        if (STUDY_PROPOSALS.includes(out.value)) return out;
        const area = out.value.startsWith('quiz:') ? out.value.slice(5) : '';
        return area && (track(out.track).domains || []).some((d) => d.id === area) ? out : null;
      }
      case 'target_date': return /^\d{4}-\d{2}-\d{2}$/.test(out.value) && dayKey(localDay(out.value)) === out.value ? out : null;
      case 'track_status': return ['active', 'paused', 'done'].includes(out.value) ? out : null;
      case 'start_level': return START_LEVELS.includes(out.value.toLowerCase()) ? {...out, value: out.value.toLowerCase()} : null;
      case 'note': return out.value ? out : null;
      default: return null;
    }
  }
  // Whether what a (usable) proposal asks for is already so, read from the data rather than from this page's
  // memory: after a reload, or on another device, an applied note or setting still shows done, and a note can't be
  // added twice. A note counts once it is in the learner's notes (the server stores it as "- <date>: <text>"); a
  // study proposal is an action, never already so.
  const squish = (text) => String(text == null ? '' : text).split(/\s+/).filter(Boolean).join(' ');
  function proposalInEffect(p) {
    const learner = (app.state && app.state.learner) || {};
    const prefs = learner.preferences || {};
    const t = p.track ? track(p.track) : null;
    switch (p.kind) {
      case 'note': {
        const want = squish(p.value);
        return String(learner.notes_md || '').split('\n').some((line) => {
          const m = line.match(/^\s*[-*]\s+\d{4}-\d{2}-\d{2}:\s*(.*)$/);
          return !!m && squish(m[1]) === want;
        });
      }
      case 'session_minutes': case 'block_size': case 'desired_retention':
        return isNum(prefs[p.kind]) && prefs[p.kind] === Number(p.value);
      case 'start_level': return String(prefs.start_level || '').toLowerCase() === p.value;
      case 'daily_minutes': return !!t && isNum(t.daily_minutes) && t.daily_minutes === Number(p.value);
      case 'target_date': return !!t && t.target_date === p.value;
      case 'track_status': return !!t && t.status === p.value;
      default: return false;
    }
  }
  async function applyProposal(p, id) {
    if (p.kind === 'study') {
      if (p.value.startsWith('quiz:')) return startStudy('quiz', p.track, {count: 10, scope: {area: p.value.slice(5)}});
      if (p.value === 'mock-mini' || p.value === 'mock-full') return startStudy('mock', p.track, {count: p.value === 'mock-full' ? 'full' : 'mini'});
      return startStudy(p.value, p.track);
    }
    let value = p.value;
    if (Object.prototype.hasOwnProperty.call(PROPOSAL_NUMBERS, p.kind)) {
      value = proposalNumber(p);
      if (value === null) {
        toast(`That suggestion (“${p.value}”) isn’t a number the setting accepts, so nothing was changed.`, true);
        return null;
      }
    }
    let url = 'api/preferences', body = {[p.kind]: value};
    if (['target_date', 'track_status', 'daily_minutes'].includes(p.kind)) {
      url = `api/tracks/${encodeURIComponent(p.track)}/settings`;
      body = {[{target_date: 'target_date', track_status: 'status', daily_minutes: 'daily_minutes'}[p.kind]]: value};
    } else if (p.kind === 'note') {
      url = 'api/notes';
      body = {text: p.value, section: 'Coaching notes'};
    }
    // One request at a time per proposal (a double click would apply it twice); it shows as done once applied.
    // That mark covers the moment until the new data arrives; from then on a setting's chip follows the setting
    // itself, so it can be applied again once something else has changed it. A note's mark stays.
    const out = await act(url, body, {key: `proposal:${id}`, done: () => study.applied.add(id)});
    if (out) {
      toast('Done.');
      await refresh();
      if (p.kind !== 'note' && !app.failing && proposalInEffect(p)) study.applied.delete(id);
    }
    return null;
  }
  function coachMessage(m, n) {
    const who = m.role === 'you' ? 'You' : ((app.state.learner.persona || {}).name || 'Coach');
    if (m.role === 'you') return h('div', {class: 'msg is-you'}, h('div', {class: 'msg-who'}, who), h('p', null, m.text));
    if (m.error) {
      return h('div', {class: 'msg is-coach is-error'}, h('div', {class: 'msg-who'}, who),
        h('p', null, FAILED[m.code] || 'The coach couldn’t answer.'), m.code && FAILED[m.code] ? null : h('p', {class: 'small muted'}, m.error));
    }
    const noteKey = `note:${m.ts}:${n}`;
    const note = study.forms[noteKey];
    const editor = note ? h('textarea', {class: 'input', rows: 4, 'data-focus': noteKey, 'aria-label': 'Note to save'}) : null;
    if (editor) {
      editor.value = note.text;
      editor.addEventListener('input', () => { study.forms[noteKey] = {text: editor.value}; });
    }
    const saving = busy(noteKey);
    // Each proposal keeps its place in the message for its id (what marks it applied), usable or not.
    const offered = writable() ? (m.proposals || []).map((p, i) => ({p: usableProposal(p), i})) : [];
    const usable = offered.filter((x) => x.p);
    const unusable = offered.length - usable.length;
    return h('div', {class: 'msg is-coach', 'data-focus-scope': `msg:${m.ts}:${n}`}, h('div', {class: 'msg-who'}, who), markdown(m.text),
      usable.length ? h('div', {class: 'proposals'}, usable.map(({p, i}) => {
        const id = `${m.ts}:${n}:${i}`;
        const already = proposalInEffect(p);
        const done = already || study.applied.has(id);
        return h('button', {class: `chip${done ? ' is-done' : ''}`, type: 'button', 'data-focus': `proposal:${id}`,
          disabled: done || busy('start') || busy(`proposal:${id}`), title: already ? 'Already so: nothing to change.' : done ? 'Done.' : p.reason || '',
          onclick: () => applyProposal(p, id)}, done ? icon('check', 'is-good') : null, proposalText(p));
      })) : null,
      unusable ? h('p', {class: 'small muted proposals-note'}, unusable === 1
        ? 'One suggestion here has no button: Polymath can’t apply it as written.'
        : `${unusable} suggestions here have no button: Polymath can’t apply them as written.`) : null,
      writable() ? (editor ? h('div', {class: 'note-edit'}, editor, h('div', {class: 'form-actions'},
        h('button', {class: 'btn btn-small btn-primary', type: 'button', 'data-focus': `${noteKey}:save`, disabled: saving, onclick: async () => {
          // One save at a time: a double click would add the note twice.
          const out = await act('api/notes', {text: editor.value.slice(0, 1990), section: 'Coaching notes'},
            {key: noteKey, done: () => { delete study.forms[noteKey]; }});
          if (out) {
            toast('Saved to your coaching notes.');
            await refresh();
          }
        }}, saving ? 'Saving…' : 'Save the note'),
        h('button', {class: 'btn btn-small', type: 'button', disabled: saving, onclick: () => { delete study.forms[noteKey]; render(); }}, 'Cancel')))
        : h('div', {class: 'study-actions', style: {marginTop: '8px'}}, h('button', {class: 'btn btn-small', type: 'button',
          'data-focus': `${noteKey}:open`, onclick: () => {
            study.forms[noteKey] = {text: m.text.replace(/\s+/g, ' ').slice(0, 600)};
            render();
            focusLater(noteKey);
          }}, 'Save as a note…'))) : null);
  }
  function pageCoach() {
    if (app.snapshot) {
      return h('div', {class: 'stack'}, pageHead('Coach', 'Talk through your plan, your pace or a worry, with your real numbers in view.'),
        card({body: empty('The coach needs the dashboard server.', 'This is a snapshot file. Run polymath.py serve to talk to your coach.')}));
    }
    loadChat();
    const info = studyInfo();
    const chat = study.chat || {messages: [], busy: false};
    const msgs = chat.messages || [];
    const waiting = chat.busy || busy('chat');
    const can = info.available && writable();
    const prompts = ['Am I on track for my exam date?', 'What should I focus on this week?', 'Should I book the exam yet?',
      'I keep making the same mistakes. Why?'];
    const input = h('textarea', {class: 'input', rows: 3, 'data-focus': 'coach-input', 'aria-label': 'Message to your coach',
      placeholder: 'Ask about your plan, your pace, or what to study next', disabled: !can || waiting});
    input.value = (app.drafts.coach || {}).text || '';
    input.addEventListener('input', () => { app.drafts.coach = {text: input.value}; });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); sendChat(input.value); } });
    const thread = h('div', {class: 'chat', 'aria-live': 'polite'}, msgs.length ? msgs.map(coachMessage)
      : empty('Ask your coach anything about your study.', 'It sees your real numbers: readiness, pace, mistakes, mocks and your plan.'),
    waiting ? h('div', {class: 'msg is-coach'}, h('div', {class: 'qwriting'}, h('span', {class: 'spinner', 'aria-hidden': 'true'}),
      h('span', null, 'Thinking it through…'))) : null);
    return h('div', {class: 'stack'}, pageHead('Coach', info.writer ? `Replies come from ${info.writer}. It suggests changes; you decide.`
      : 'The coach needs an AI model.', null, [h('a', {href: '#/study'}, 'Study')]),
    card({body: thread}),
    card({body: [h('div', {class: 'prompts'}, prompts.map((p) => h('button', {class: 'chip', type: 'button', disabled: !can || waiting,
      onclick: () => sendChat(p)}, p))), h('div', {style: {marginTop: '12px'}}, input),
    h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-primary', type: 'button', disabled: !can || waiting,
      onclick: () => sendChat(input.value)}, waiting ? 'Waiting…' : 'Send'), h('span', {class: 'form-note'}, 'Ctrl+Enter sends'))]}));
  }

  // -- keyboard --
  function mockKeys(e, v, key) {
    const m = v.mock;
    if (!m || !m.started || m.submitted || study.mock.review) return;
    const qs = v.questions;
    const at = Math.max(0, Math.min(qs.length - 1, study.mock.at || 0));
    const q = qs[at];
    if (key === 'arrowright' || key === 'j') { e.preventDefault(); mockGo(v, at + 1); return; }
    if (key === 'arrowleft' || key === 'k') { e.preventDefault(); mockGo(v, at - 1); return; }
    if (!q || !q.question) return;
    if (key === 'm') { e.preventDefault(); mockFlag(v, q); return; } // M, not F: F is the sixth option
    const letters = q.question.options.map((o) => o.key.toLowerCase());
    if (letters.includes(key)) { e.preventDefault(); mockPick(v, q, key.toUpperCase()); }
    else if (/^[1-5]$/.test(key)) { e.preventDefault(); mockConfidence(v, q, Number(key)); }
  }
  document.addEventListener('keydown', (e) => {
    if (!app.route || app.route.name !== 'study' || !study.view || e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
    const focus = document.activeElement;
    if (focus && /^(INPUT|TEXTAREA|SELECT)$/.test(focus.tagName)) return;
    const v = study.view;
    if (v.id !== app.route.id || v.error) return;
    const key = e.key.toLowerCase();
    if (v.mode === 'mock') { mockKeys(e, v, key); return; }
    if (v.status === 'done' || busy()) return;
    const target = activeTarget(v);
    const onChoice = focus && focus.closest && focus.closest('.opts, .conf');
    if (target && /^[a-f]$/.test(key) && target.q.options.some((o) => o.key === key.toUpperCase())) {
      e.preventDefault();
      choose(target.key, target.q, key.toUpperCase());
    } else if (target && /^[1-5]$/.test(key) && pick(target.key).choice.length) {
      e.preventDefault();
      setConfidence(target.key, target.q, Number(key));
    } else if (key === 'enter') {
      // Enter belongs to what has focus (a link, a summary, a button, the theme switch), except on the page
      // itself, a question card, or an option or confidence control: there it checks the answers once they
      // are complete.
      const free = !focus || focus === document.body || focus === view || onChoice
        || (view.contains(focus) && focus.hasAttribute('data-focus-scope'));
      if (!free) return;
      if (readyToCheck(v)) { e.preventDefault(); checkBlock(v); return; }
      const offered = v.questions.find((q) => q.followup && q.followup.state === 'offered' && q.followup.question && !q.result.voided);
      if (offered && complete(pick(`${offered.id}:t`), offered.followup.question.select)) { e.preventDefault(); answerFollowup(offered); }
      // Nothing to check yet: Enter on an option or a confidence button is that button's own click, so it picks.
      // It never un-picks (Space and a click still do): an Enter meant as "check", pressed before every answer
      // is complete, mustn't quietly drop a choice already made.
      else if (onChoice && focus.getAttribute('aria-checked') === 'true') e.preventDefault();
    } else if (key === 'n') {
      const nxt = v.next || {};
      const wait = waitingOn(v);
      if (v.status === 'active' && !openQuestions(v).length && !wait.reasons && !wait.writing && (nxt.exists || nxt.more)) {
        e.preventDefault();
        nextBlock(v);
      }
    }
  });
  setInterval(() => { if (app.route && app.route.name === 'study') updateMockTimer(); }, 1000);

  // --- frame -------------------------------------------------------------------------------------
  function renderNav(route) {
    const items = [['Overview', '#/', route.name === 'overview']];
    if (app.state && app.state.ready) items.push(['Goals', '#/goals', route.name === 'goals']);
    if (app.state && app.state.ready && !app.snapshot) items.push(['Study', '#/study', route.name === 'study' || route.name === 'coach', goToStudy]);
    if (app.state && app.state.ready) {
      // Up to three active tracks, plus the one open; the rest are a click away on Goals.
      const shown = tracks().filter((t) => t.status === 'active').slice(0, 3);
      const open = route.name === 'track' ? track(route.slug) : null;
      if (open && !shown.includes(open)) shown.push(open);
      for (const t of shown) items.push([shortTitle(t), link('track', t.slug), route.name === 'track' && route.slug === t.slug]);
      items.push(['Mistakes', '#/mistakes', route.name === 'mistakes'], ['Activity', '#/activity', route.name === 'activity'],
        ['You', '#/you', route.name === 'you']);
    }
    if (app.state && !app.snapshot && writable() && !models.absent) items.push(['AI model', '#/models', route.name === 'models']);
    navEl.replaceChildren(...items.map(([label, href, current, onclick]) => h('a', {href, 'aria-current': current ? 'page' : null, onclick: onclick || null}, label)));
  }
  function updateLive() {
    const st = app.state;
    const flags = [];
    let cls = 'live', text;
    if (!st) text = app.failing ? 'Offline' : 'Connecting…';
    else if (app.snapshot) text = `Snapshot · ${fmtWhen(st.app.generated_at)}`;
    else if (app.failing) { cls += ' is-offline'; text = 'Offline · retrying'; }
    else if (app.pending && editing()) text = 'New data waiting until you finish typing';
    else {
      cls += ' is-live';
      const sec = Math.round((Date.now() - app.fetchedAt) / 1000);
      text = sec < 60 ? 'Live' : `Live · updated ${Math.round(sec / 60)} min ago`;
    }
    if (st && st.app.demo) flags.push(h('span', {class: 'chip-flag'}, 'Demo data'));
    if (st && st.app.read_only && !app.snapshot) flags.push(h('span', {class: 'chip-flag'}, 'Read-only'));
    liveEl.className = cls;
    liveEl.replaceChildren(h('span', {class: 'live-dot'}), h('span', {class: 'live-text'}, text), ...flags);
    liveEl.title = text;
  }
  function render(fromHash) {
    app.pending = false;
    const route = parseRoute();
    const same = app.route && JSON.stringify(app.route) === JSON.stringify(route);
    const y = window.scrollY;
    const was = app.route;
    if (was && was.name === 'study' && was.id && !(route.name === 'study' && route.id === was.id)) leaveSession(was.id, y);
    app.route = route;
    if (route.name === 'study' && route.id) arriveAtSession(route.id);
    renderNav(route);
    updateLive();
    hideTip();
    pendingCharts.length = 0;
    let page;
    const st = app.state;
    if (!st) page = h('p', {class: 'loading'}, 'Loading your progress…');
    else if (route.name === 'models') page = pageModels(route);
    else if (!st.ready) page = pageSetup();
    else {
      page = {overview: pageOverview, track: pageTrack, concept: pageConcept, mistakes: pageMistakes, activity: pageActivity, you: pageYou,
        study: (r) => (r.id ? pageSession(r.id) : pageStudyHome()), coach: pageCoach,
        goals: (r) => (r.id === 'new' ? pageGoalNew() : r.id === 'interview' ? pageInterviewNew() : r.id ? pageGoal(r.id) : pageGoals())}[route.name](route);
    }
    // Keep focus where it was: on the same control (data-focus), or, when that control is gone, on the card
    // or message it was in (data-focus-scope). A control disabled while its request runs is remembered, and
    // focus goes back to it on a later redraw.
    const active = document.activeElement;
    const parked = same && app.refocus && Date.now() - app.refocus.at < 15000 ? app.refocus : null;
    app.refocus = null;
    let want = null;
    if (active && active !== view && view.contains(active)) {
      const box = active.closest('[data-focus-scope]');
      want = {key: active.getAttribute('data-focus'), scope: box ? box.getAttribute('data-focus-scope') : null};
      if (parked && !want.key && want.scope === parked.scope) want = parked;
    } else if (parked && (!active || active === document.body)) want = parked;
    const alert = st ? modelBanner(route) : null;
    view.replaceChildren(...(alert ? [alert, page] : [page]));
    if (want) restoreFocus(want);
    mountCharts();
    if (route.name === 'study' && route.id) pollSession();
    const name = route.name === 'track' && st && st.ready && track(route.slug) ? shortTitle(track(route.slug))
      : {overview: '', mistakes: 'Mistakes', activity: 'Activity', you: 'You', concept: 'Concept', study: 'Study', coach: 'Coach', goals: 'Goals', models: 'AI model'}[route.name] || '';
    document.title = name ? `${name} · Polymath` : 'Polymath';
    if (same) window.scrollTo(0, y);
    else {
      window.scrollTo(0, 0);
      if (fromHash) view.focus({preventScroll: true});
    }
    if (route.name === 'study' && route.id) applyRestore(route.id);
  }

  // A control that is gone because it opened what comes next hands focus to it: Start the exam, to its question.
  const FOCUS_AFTER = {begin: 'm:card'};
  function restoreFocus(want) {
    const find = (attr, value) => (value ? view.querySelector(`[${attr}="${CSS.escape(value)}"]`) : null);
    const el = find('data-focus', want.key);
    if (el && !el.disabled) { el.focus({preventScroll: true}); return; }
    const box = find('data-focus-scope', want.scope) || (el ? null : find('data-focus-scope', FOCUS_AFTER[want.key]));
    if (box) focusable(box).focus({preventScroll: true});
    if (el) app.refocus = {key: want.key, scope: want.scope, at: want.at || Date.now()}; // after the focus: focusin clears it
  }
  function focusable(el) {
    // A card or message is made focusable only when focus has nowhere better to go (not when it is built), so a
    // click inside a card doesn't normally move focus to the card.
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
    return el;
  }

  // --- active time: only what you actually spend using the app -----------------------------------
  // Time counts between your own actions, while this window is on screen and in focus. A longer pause counts as nothing.
  const IDLE_LIMIT = 180e3;
  const activeClock = {last: null, pending: 0};
  const durationText = (sec) => {
    const m = Math.round((sec || 0) / 60);
    if (m < 60) return `${m} min`;
    return m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${Math.floor(m / 60)} h`;
  };
  function noteInput() {
    const now = performance.now();
    if (document.hidden || !document.hasFocus()) { activeClock.last = null; return; }
    if (activeClock.last != null && now - activeClock.last <= IDLE_LIMIT) activeClock.pending += now - activeClock.last;
    activeClock.last = now;
  }
  function clockWhere() {
    const r = app.route || {};
    if (r.name === 'track') return {track: r.slug};
    if (r.name === 'study' && r.id && study.view && study.view.id === r.id) return {track: study.view.track, session: r.id};
    return {};
  }
  function showClock(t) {
    if (!app.state || !t) return;
    app.state.time = {...(app.state.time || {}), ...t};
    view.querySelectorAll('[data-active]').forEach((el) => {
      const [field, slug] = el.getAttribute('data-active').split(':');
      el.textContent = durationText(slug ? ((t.tracks || {})[slug] || {})[field] : t[field]);
    });
  }
  async function sendClock(leaving) {
    if (app.snapshot || !writable() || activeClock.pending < 1000) return;
    const seconds = Math.round(activeClock.pending / 100) / 10;
    activeClock.pending = 0;
    const where = clockWhere();
    if (leaving) { // the page may be going away: this one has to outlive it
      try {
        fetch('api/activity', {method: 'POST', keepalive: true, credentials: 'same-origin', body: JSON.stringify({seconds, ...where}),
          headers: {'Content-Type': 'application/json', 'X-Polymath': '1', 'X-Polymath-Key': pageKey()}}).catch(() => {});
      } catch (err) { /* nothing more to do */ }
      return;
    }
    try { showClock(await post('api/activity', {seconds, ...where})); }
    catch (err) { activeClock.pending = Math.min(240e3, activeClock.pending + seconds * 1000); }
  }
  function startClock() {
    ['pointermove', 'pointerdown', 'keydown', 'wheel', 'touchstart', 'scroll'].forEach((type) =>
      window.addEventListener(type, noteInput, {capture: true, passive: true}));
    const away = () => { activeClock.last = null; sendClock(true); };
    window.addEventListener('blur', away);
    window.addEventListener('pagehide', away);
    window.addEventListener('focus', noteInput);
    document.addEventListener('visibilitychange', () => { if (document.hidden) away(); else noteInput(); });
    noteInput();
    setInterval(() => sendClock(false), 15000);
  }
  function timeCard() {
    const t = app.state.time || {};
    const plan = (app.state.learner.preferences || {}).session_minutes;
    return card({title: 'Active study time', sub: 'Counts only while you’re using the app. Stepping away, or another tab or window, doesn’t count.',
      body: h('div', {class: 'kpis'},
        h('div', null, h('div', {class: 'stat-label'}, 'Today'), h('div', {class: 'stat-value', style: {fontSize: '18px'}, 'data-active': 'today'}, durationText(t.today)),
          plan ? h('div', {class: 'stat-sub'}, `of ${durationText(plan * 60)} planned`) : null),
        h('div', null, h('div', {class: 'stat-label'}, 'Last 7 days'), h('div', {class: 'stat-value', style: {fontSize: '18px'}, 'data-active': 'week'}, durationText(t.week))),
        h('div', null, h('div', {class: 'stat-label'}, 'All time'), h('div', {class: 'stat-value', style: {fontSize: '18px'}, 'data-active': 'total'}, durationText(t.total))))});
  }

  // --- AI model: what writes your questions ---------------------------------------------------------
  const models = {status: null, view: null, loading: false, loadError: null, absent: false, scan: null, scanning: false,
    working: null, testing: false, errors: {}, lists: {}, listing: null, picked: {}, keys: {}, urls: {}, provider: null};
  const MODEL_DOWN = new Set(['model_offline', 'model_missing', 'model_memory', 'key_rejected', 'no_model']);
  const isModels = () => !!(app.route && app.route.name === 'models');
  async function checkModel(force) {
    if (app.snapshot || !app.state || !writable() || models.absent || (document.hidden && force !== true)) return;
    let s;
    try { s = await getJSON('api/models/status'); }
    catch (err) {
      if (err.status === 404) { models.absent = true; redraw(); }
      return; // the dashboard itself is unreachable: the Live chip says so
    }
    const was = models.status;
    models.status = s;
    if (was && !was.ok && s.ok && was.provider === s.provider) {
      toast(`${s.label} is back.`);
      loadStudyHome(true);
      retryAfterModel();
    }
    if (!was || was.ok !== s.ok || was.code !== s.code || was.writer !== s.writer || was.problem !== s.problem) renderSoon();
  }
  // Questions that failed only because the model was down are written again as soon as it is back.
  async function retryAfterModel() {
    const v = study.view;
    if (!v || !v.questions || v.mode === 'mock' || !onSession() || app.route.id !== v.id) return;
    const stuck = v.questions.filter((q) => q.state === 'failed' && MODEL_DOWN.has(q.code));
    for (const q of stuck) {
      try { accept(await post(studyPath(v.id, 'retry'), {qid: q.id})); } catch (err) { break; }
    }
    if (stuck.length) { renderSoon(); pollSession(); }
  }
  const DOWN_TITLE = (s) => ({model_offline: `${s.label} isn’t responding`, model_missing: `${s.model || 'That model'} isn’t available`,
    model_memory: 'Not enough free memory for this model', key_rejected: `${s.label} rejected your API key`, no_model: 'Choose an AI model'}[s.code]
    || 'Your AI model isn’t ready');
  function modelBanner(route) {
    const s = models.status;
    if (!s || s.ok || route.name === 'models' || app.snapshot) return null;
    return h('div', {class: 'callout is-warning model-banner', role: 'status'}, icon('alert', 'is-warning'), h('div', null,
      h('b', null, DOWN_TITLE(s)), h('div', {class: 'small'}, s.problem || ''),
      h('div', {class: 'small muted'}, 'Your progress and answers are safe. New questions can’t be written until this is fixed.'),
      h('div', {class: 'study-actions', style: {marginTop: '8px'}},
        h('button', {class: 'btn btn-small', type: 'button', onclick: async () => {
          await checkModel(true);
          if (models.status && !models.status.ok) toast('Still not responding.', true);
        }}, 'Check again'),
        h('a', {class: 'btn btn-small btn-primary', href: '#/models'}, 'Change AI model'))));
  }
  async function loadModels(force) {
    if (models.loading || (models.view && !force)) return;
    models.loading = true;
    try {
      models.view = await getJSON('api/models');
      models.status = models.view.status;
      models.loadError = null;
    } catch (err) {
      if (err.status === 404) models.absent = true;
      models.loadError = err.message || String(err);
    } finally { models.loading = false; }
    if (isModels()) redraw();
    scanLocal();
  }
  const offersLocal = () => ((models.view || {}).providers || []).some((p) => p.kind === 'local');
  async function scanLocal(force) {
    if (!offersLocal() || models.scanning || (models.scan && !force)) return;
    models.scanning = true;
    if (force && isModels()) redraw();
    try { models.scan = (await post('api/models/scan', {})).servers || []; }
    catch (err) { models.scan = models.scan || []; toast(err.message || String(err), true); }
    finally { models.scanning = false; }
    if (isModels()) redraw();
  }
  async function listModels(provider, url) {
    if (models.listing) return;
    models.listing = provider;
    models.errors[provider] = null;
    redraw();
    try {
      const out = await post('api/models/list', {provider, url: url || '', key: models.keys[provider] || ''});
      models.lists[provider] = out.models || [];
      if (!models.lists[provider].length) models.errors[provider] = 'That server has no models to choose from yet.';
    } catch (err) { models.errors[provider] = err.message || String(err); }
    finally { models.listing = null; redraw(); }
  }
  async function useModel(provider, model, url) {
    if (models.working) return;
    if (!model) { models.errors[provider] = 'Choose a model first.'; redraw(); return; }
    models.working = provider;
    models.errors[provider] = null;
    redraw();
    try {
      const out = await post('api/models/use', {provider, model, url: url || '', key: models.keys[provider] || ''});
      models.view = out;
      models.status = out.status;
      delete models.keys[provider];
      toast(`Now using ${out.status.model || out.status.label}.`);
      await refresh();
      loadStudyHome(true);
      retryAfterModel();
    } catch (err) { models.errors[provider] = err.message || String(err); }
    finally { models.working = null; redraw(); }
  }
  async function testModel() {
    if (models.testing) return;
    models.testing = true;
    redraw();
    try {
      const out = await post('api/models/test', {});
      models.status = out.status;
      toast(`It works. Answered in ${out.seconds} s.`);
    } catch (err) {
      toast(err.message || String(err), true);
      await checkModel(true);
    } finally { models.testing = false; redraw(); }
  }
  async function forgetKey(provider) {
    if (!window.confirm('Remove the saved API key from this computer?')) return;
    try {
      models.view = await post('api/models/forget', {provider});
      delete models.lists[provider];
      toast('Key removed.');
    } catch (err) { toast(err.message || String(err), true); }
    redraw();
  }
  function placeText(s) {
    if (s.kind === 'subscription') return 'Your questions and answers go to Anthropic through your Claude subscription.';
    if (s.place === 'device') return 'Runs on this computer. What you study stays here.';
    if (s.place === 'network') return 'Runs on another computer on your network. What you study goes there.';
    return `Your questions, answers and any interview material go to ${s.label}, billed to your key.`;
  }
  function currentModelCard(v) {
    const s = models.status || v.status;
    const ready = s.ok;
    return card({title: 'In use now', body: [
      h('div', {class: 'model-now'}, h('span', {class: `model-pill ${ready ? 'is-ok' : 'is-down'}`}, icon(ready ? 'check' : 'alert', ready ? 'is-good' : 'is-warning'), ready ? 'Ready' : 'Not ready'),
        h('div', null, h('b', null, s.kind === 'none' ? 'No model chosen' : s.model ? `${s.model} · ${s.label}` : s.label),
          s.kind !== 'none' ? h('div', {class: 'small muted'}, placeText(s)) : null)),
      !ready ? h('div', {class: 'callout is-warning', style: {marginTop: '12px'}}, icon('alert', 'is-warning'), h('div', null, h('b', null, DOWN_TITLE(s)), h('div', {class: 'small'}, s.problem || ''))) : null,
      s.kind !== 'none' ? h('div', {class: 'study-actions', style: {marginTop: '12px'}},
        h('button', {class: 'btn', type: 'button', disabled: models.testing || !!models.working, onclick: testModel},
          models.testing ? 'Testing… a first call can take a minute' : 'Test it'),
        s.kind === 'key' ? h('span', {class: 'form-note'}, 'A test uses a few tokens on your key.') : null) : null,
      s.kind === 'local' || s.kind === 'key' ? h('p', {class: 'small muted', style: {marginTop: '12px'}},
        'This model can’t search the web yet, so lessons, goal research and company research will tell you when they can’t run. Practice questions, reviews, mocks and the coach all work. Smaller models can write wrong answer keys, so check anything surprising against the official docs.') : null]});
  }
  function modelPicker(provider, names, url) {
    const s = models.status || {};
    const chosen = models.picked[provider] || (s.provider === provider && names.includes(s.model) ? s.model : names[0]);
    const sel = h('select', {class: 'input', 'aria-label': 'Model', 'data-focus': `model:${provider}`, onchange: (e) => { models.picked[provider] = e.target.value; render(); }},
      names.map((n) => h('option', {value: n, selected: n === chosen}, n)));
    const working = models.working === provider;
    return h('div', null, h('div', {class: 'model-pick'}, sel, h('button', {class: 'btn btn-primary', type: 'button', disabled: !!models.working,
      onclick: () => useModel(provider, sel.value, url)}, working ? 'Testing it…' : s.provider === provider && s.model === sel.value ? 'Use it again' : 'Use this model')),
    ratingNote(provider, chosen));
  }
  const modelError = (provider) => (models.errors[provider] ? h('div', {class: 'callout is-warning', style: {marginTop: '10px'}}, icon('alert', 'is-warning'),
    h('div', {class: 'small'}, models.errors[provider])) : null);
  const workingNote = (provider) => (models.working === provider
    ? h('p', {class: 'small muted', style: {marginTop: '8px'}}, 'Sending one short test. A local model may need a minute or two to load the first time.') : null);
  function localHelp(s) {
    if (s.provider === 'ollama') return ['Install it from ollama.com, open it, then download a model, for example: ', h('code', null, 'ollama pull qwen3:8b')];
    if (s.provider === 'lmstudio') return ['Open LM Studio, download a model, then turn on its local server in the Developer tab.'];
    return ['Start the server, then check again.'];
  }
  function localServerRow(s) {
    const names = s.models || [];
    return h('div', {class: 'model-row'}, h('div', {class: 'model-row-head'}, h('b', null, s.label),
      h('span', {class: `model-pill ${s.running ? 'is-ok' : 'is-down'}`}, s.running ? `Running · ${plural(names.length, 'model')}` : 'Not running'),
      h('span', {class: 'small muted'}, s.url)),
    s.running && names.length ? modelPicker(s.provider, names, s.url) : null,
    s.running && !names.length ? h('p', {class: 'small muted'}, 'It’s running, but has no models yet. ', localHelp(s)) : null,
    !s.running ? h('p', {class: 'small muted'}, localHelp(s)) : null,
    modelError(s.provider), workingNote(s.provider));
  }
  function otherServerRow(v) {
    const spec = v.providers.find((p) => p.id === 'local');
    const urlValue = models.urls.local != null ? models.urls.local : (spec.url || '');
    const input = h('input', {class: 'input', type: 'url', value: urlValue, placeholder: 'http://127.0.0.1:8080/v1', 'aria-label': 'Server address',
      oninput: (e) => { models.urls.local = e.target.value; }});
    const names = models.lists.local || [];
    return h('div', {class: 'model-row'}, h('div', {class: 'model-row-head'}, h('b', null, 'Another local server'),
      h('span', {class: 'small muted'}, 'llama.cpp, Jan, vLLM or anything OpenAI-compatible')),
    h('div', {class: 'model-pick'}, input, h('button', {class: 'btn', type: 'button', disabled: !!models.listing, onclick: () => listModels('local', input.value)},
      models.listing === 'local' ? 'Looking…' : 'Find its models')),
    names.length ? modelPicker('local', names, input.value) : null, modelError('local'), workingNote('local'));
  }
  function localTab(v) {
    const rows = (models.scan || []).filter((s) => s.provider !== 'local');
    return [h('div', {class: 'study-actions', style: {marginBottom: '12px'}}, h('span', {class: 'small muted'}, 'Free and private: the model runs on your own computer.'),
      h('button', {class: 'btn btn-small', type: 'button', disabled: models.scanning, onclick: () => scanLocal(true)}, models.scanning ? 'Looking…' : 'Look again')),
    models.scan ? rows.map(localServerRow) : h('p', {class: 'loading'}, 'Looking for models on this computer…'), otherServerRow(v)];
  }
  function keyTab(v) {
    const rows = v.providers.filter((p) => p.kind === 'key');
    const s = models.status || {};
    const id = models.provider || (rows.some((p) => p.id === s.provider) ? s.provider : 'openai');
    const spec = rows.find((p) => p.id === id) || rows[0];
    const pick = h('select', {class: 'input', 'aria-label': 'Provider', onchange: (e) => { models.provider = e.target.value; render(); }},
      rows.map((p) => h('option', {value: p.id, selected: p.id === spec.id}, p.label + (p.has_key ? ' (key saved)' : ''))));
    const keyInput = h('input', {class: 'input', type: 'password', autocomplete: 'off', spellcheck: 'false', value: models.keys[spec.id] || '',
      placeholder: spec.has_key ? 'A key is saved. Paste a new one to replace it.' : 'Paste your API key', 'aria-label': `${spec.label} API key`,
      oninput: (e) => { models.keys[spec.id] = e.target.value.trim(); }});
    const urlValue = models.urls[spec.id] != null ? models.urls[spec.id] : (spec.url || '');
    const urlInput = spec.needs_url ? h('input', {class: 'input', type: 'url', value: urlValue, placeholder: 'https://api.example.com/v1', 'aria-label': 'API address',
      oninput: (e) => { models.urls[spec.id] = e.target.value; }}) : null;
    const names = models.lists[spec.id] || [];
    const typed = h('input', {class: 'input', value: models.picked[spec.id] || '', placeholder: 'Or type a model name', 'aria-label': 'Model name',
      oninput: (e) => { models.picked[spec.id] = e.target.value.trim(); }});
    return [h('p', {class: 'small muted', style: {marginBottom: '12px'}}, 'Your key stays on this computer and is sent only to the provider you choose. What you study goes to that provider, and they bill you.'),
      h('div', {class: 'form-grid'}, h('label', {class: 'field'}, 'Provider', pick), h('label', {class: 'field'}, 'API key', keyInput),
        urlInput ? h('label', {class: 'field'}, 'API address', urlInput) : null),
      h('div', {class: 'study-actions', style: {marginTop: '12px'}},
        h('button', {class: 'btn', type: 'button', disabled: !!models.listing, onclick: () => listModels(spec.id, urlInput ? urlInput.value : '')},
          models.listing === spec.id ? 'Loading…' : 'Load its models'),
        spec.has_key ? h('button', {class: 'btn', type: 'button', onclick: () => forgetKey(spec.id)}, 'Remove saved key') : null),
      names.length ? h('div', {style: {marginTop: '12px'}}, modelPicker(spec.id, names, urlInput ? urlInput.value : '')) : h('div', {class: 'model-pick', style: {marginTop: '12px'}}, typed,
        h('button', {class: 'btn btn-primary', type: 'button', disabled: !!models.working, onclick: () => useModel(spec.id, typed.value.trim(), urlInput ? urlInput.value : '')},
          models.working === spec.id ? 'Testing it…' : 'Use this model')),
      modelError(spec.id), workingNote(spec.id)];
  }
  function claudeTab(v) {
    const spec = v.providers.find((p) => p.id === 'claude');
    if (!spec.installed) return [h('p', {class: 'small muted'}, 'Claude Code isn’t installed on this computer. Install it and log in, then come back.')];
    return [h('p', {class: 'small muted', style: {marginBottom: '12px'}}, 'Uses your Claude subscription through Claude Code. It’s the only option here that can also search the web for lessons and goal research.'),
      modelPicker('claude', spec.models, ''), modelError('claude'), workingNote('claude')];
  }
  // -- the benchmark: is this model safe to learn from? --
  const bench = {data: null, loading: false, error: null, runs: {}, poll: 0, target: '', budget: '', estimate: null, estimating: false,
    busy: false, exam: '', show: 'spot'};
  const RATING = {recommended: ['Recommended', 'is-ok'], not_recommended: ['Not recommended', 'is-down'], unfinished: ['Unfinished', '']};
  const onBench = () => !!(app.route && app.route.name === 'models' && app.route.tab === 'benchmark');
  const ratingPill = (rating) => h('span', {class: `model-pill ${(RATING[rating] || RATING.unfinished)[1]}`}, (RATING[rating] || RATING.unfinished)[0]);
  const money = (n) => `$${Number(n || 0).toFixed(2)}`;
  const share = (n) => (isNum(n) ? `${Math.round(n * 1000) / 10}%` : '–');
  function ratingNote(provider, model) {
    if (!(models.view || {}).benchmark) return null;
    const r = ((models.view || {}).ratings || {})[`${provider}:${model}`];
    if (!r) return h('div', {class: 'small muted model-rating'}, 'Not benchmarked yet. ', h('a', {href: link('models', 'benchmark')}, 'Test it'));
    return h('div', {class: 'small model-rating'}, ratingPill(r.rating), ` ${r.escaped} of ${r.judged} questions would teach a wrong or unclear answer. `,
      h('a', {href: link('models', 'benchmark', r.run)}, 'See the results'));
  }
  async function loadBench() {
    if (bench.loading) return;
    bench.loading = true;
    try { bench.data = await getJSON('api/bench'); bench.error = null; }
    catch (err) { bench.error = err.message || String(err); }
    finally { bench.loading = false; }
    const id = app.route && app.route.id;
    if (onBench() && id) await loadRun(id);
    if (onBench()) renderSoon();
    pollBench();
  }
  async function loadRun(id) {
    try { bench.runs[id] = await getJSON(`api/bench/${encodeURIComponent(id)}`); }
    catch (err) { bench.runs[id] = {error: err.message || String(err)}; }
  }
  function pollBench() {
    clearTimeout(bench.poll);
    if (!onBench() || !bench.data || !bench.data.active) return;
    bench.poll = setTimeout(() => { if (!document.hidden) loadBench(); else pollBench(); }, 3000);
  }
  function benchTargets(v) {
    const out = [];
    const add = (provider, model, url, label) => {
      const key = `${provider}:${model}`;
      if (model && !out.some((t) => t.key === key)) out.push({key, provider, model, url: url || '', label});
    };
    for (const s of models.scan || []) if (s.running) for (const m of s.models || []) add(s.provider, m, s.url, s.label);
    const s = models.status || {};
    if (s.provider && s.model) add(s.provider, s.model, '', s.label);
    for (const p of v.providers) if (p.kind === 'key' && p.has_key) for (const m of models.lists[p.id] || []) add(p.id, m, p.url, p.label);
    return out;
  }
  async function loadEstimate(t) {
    if (bench.estimating) return;
    bench.estimating = true;
    try { bench.estimate = {for: t.key, ...(await post('api/bench/estimate', {provider: t.provider, model: t.model}))}; }
    catch (err) { bench.estimate = {for: t.key, error: err.message || String(err)}; }
    finally { bench.estimating = false; }
    if (onBench()) renderSoon();
  }
  async function benchAct(path, body, after) {
    if (bench.busy) return;
    bench.busy = true;
    redraw();
    try {
      const out = await post(path, body || {});
      if (after) after(out);
      else if (out.runs) bench.data = out;
      await loadBench();
      await loadModels(true);
    } catch (err) { toast(err.message || String(err), true); await loadBench(); }
    finally { bench.busy = false; redraw(); }
  }
  function startBench(t, e) {
    const budget = Number(bench.budget || e.default_budget);
    if (!window.confirm(`Start the benchmark on ${t.model}?\n\nClaude will judge ${e.questions} questions: about ${e.judge_calls_low} to ${e.judge_calls_high} calls on your Claude plan, roughly ${money(e.judge_usd_low)} to ${money(e.judge_usd_high)} at API prices. It pauses if it reaches ${money(budget)}.\n\nIt takes about ${e.hours} hours. You can pause it at any time.`)) return;
    benchAct('api/bench/start', {provider: t.provider, model: t.model, url: t.url, max_usd: budget, confirm: true});
  }
  function benchStartCard(v) {
    const d = bench.data;
    if (v.locked) return card({title: 'Test a model', body: empty('The model is fixed for this run.', 'This dashboard was started with --tutor or in demo mode.')});
    if (!d.judge.ok) return card({title: 'Test a model', body: empty('The judge isn’t available.', d.judge.problem)});
    const list = benchTargets(v);
    if (!list.length) {
      return card({title: 'Test a model', body: [empty('No model to test yet.', 'Start a local model such as Ollama, or load an API model on the API key tab.'),
        h('div', {class: 'study-actions', style: {justifyContent: 'center'}}, h('button', {class: 'btn', type: 'button', disabled: models.scanning, onclick: () => scanLocal(true)}, models.scanning ? 'Looking…' : 'Look again'))]});
    }
    if (!list.some((x) => x.key === bench.target)) bench.target = list[0].key;
    const t = list.find((x) => x.key === bench.target);
    const e = bench.estimate && bench.estimate.for === t.key ? bench.estimate : null;
    if (!e && !bench.estimating) loadEstimate(t);
    const sel = h('select', {class: 'input', 'aria-label': 'Model to test', 'data-focus': 'bench-target', onchange: (ev) => { bench.target = ev.target.value; render(); }},
      list.map((x) => h('option', {value: x.key, selected: x.key === t.key}, `${x.model} · ${x.label}`)));
    const budget = h('input', {class: 'input', type: 'number', min: 1, max: 200, step: 1, 'aria-label': 'Budget in dollars at API prices', 'data-focus': 'bench-budget',
      value: bench.budget || (e && !e.error ? String(e.default_budget) : '15'), oninput: (ev) => { bench.budget = ev.target.value; }});
    return card({title: 'Test a model', sub: 'The same 5 exams and 50 questions for every model, so results compare fairly.', body: [
      h('div', {class: 'form-grid'}, h('label', {class: 'field'}, 'Model to test', sel), h('label', {class: 'field'}, 'Stop at this budget (API-price dollars)', budget)),
      e && e.error ? h('div', {class: 'callout is-warning', style: {marginTop: '12px'}}, icon('alert', 'is-warning'), h('div', {class: 'small'}, e.error)) : null,
      e && !e.error ? h('div', {class: 'kpis', style: {marginTop: '14px'}},
        stat('Claude usage', `${e.judge_calls_low}–${e.judge_calls_high} calls`, `about ${money(e.judge_usd_low)}–${money(e.judge_usd_high)} at API prices, on your plan`),
        stat('Time', `about ${e.hours} h`, e.measured ? 'from your earlier runs' : 'on a 16 GB laptop'),
        stat('Model calls', String(e.target_calls), e.target_note || 'free on a local model')) : h('p', {class: 'loading'}, 'Working out the usage…'),
      h('div', {class: 'study-actions', style: {marginTop: '14px'}}, h('button', {class: 'btn btn-primary', type: 'button', disabled: !e || !!e.error || bench.busy,
        onclick: () => startBench(t, e)}, bench.busy ? 'Starting…' : 'Start benchmark'),
      h('button', {class: 'btn', type: 'button', disabled: models.scanning, onclick: () => scanLocal(true)}, models.scanning ? 'Looking…' : 'Look for local models'),
      h('span', {class: 'form-note'}, 'Nothing runs until you start it. You can pause at any time.'))]});
  }
  function benchProgress(r) {
    const s = r.summary;
    const pc = Math.round((s.progress || 0) * 100);
    return h('div', null, h('div', {class: 'small', style: {marginBottom: '6px'}}, h('b', null, `${pc}% done`), r.stage ? ` · ${r.stage}` : ''),
      h('div', {class: 'writing-bar', role: 'progressbar', 'aria-valuenow': String(pc), 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-label': 'Benchmark progress'},
        h('span', {style: {width: `${pc}%`}})),
      h('div', {class: 'small muted', style: {marginTop: '6px'}}, `${s.written} of ${s.questions} written · ${s.judged} judged · Claude: ${r.usage.judge.calls} calls, ${money(r.usage.judge.usd)} of ${money(r.max_usd)}`));
  }
  function benchRunning(r) {
    return card({title: `Testing ${r.target.model}`, sub: r.target.label, actions: [h('a', {class: 'btn btn-small', href: link('models', 'benchmark', r.id)}, 'Open')],
      body: [benchProgress(r), h('div', {class: 'study-actions', style: {marginTop: '12px'}}, h('button', {class: 'btn', type: 'button', disabled: bench.busy,
        onclick: () => benchAct(`api/bench/${r.id}/pause`)}, 'Pause'))]});
  }
  function benchResults() {
    const runs = bench.data.runs;
    if (!runs.length) return card({title: 'Results', body: empty('No results yet.', 'Test a model to see how often it would teach something wrong.')});
    return card({title: 'Results', sub: `Recommended means under ${Math.round(bench.data.bar.escaped * 100)}% of its questions would mislead a learner.`, body: h('div', {class: 'table-wrap'}, h('table', {class: 'table'},
      h('thead', null, h('tr', null, ['Model', 'Result', 'Would mislead', 'Caught by itself', 'Couldn’t write', 'Syllabus sections', 'Time per question', 'Claude usage'].map((x, i) => h('th', {scope: 'col', class: i > 1 ? 'num' : ''}, x)))),
      h('tbody', null, runs.map((r) => {
        const s = r.summary;
        const state = r.status === 'done' ? ratingPill(s.rating) : h('span', {class: 'model-pill'}, r.status === 'running' ? 'Running' : r.status === 'paused' ? 'Paused' : 'Stopped');
        return h('tr', null, h('td', null, h('a', {href: link('models', 'benchmark', r.id)}, r.target.model), h('div', {class: 'small muted'}, r.target.label)), h('td', null, state),
          h('td', {class: 'num'}, s.judged ? `${s.escaped} of ${s.judged} (${share(s.escaped_rate)})` : '–'), h('td', {class: 'num'}, s.bad ? `${s.caught} of ${s.bad}` : '–'),
          h('td', {class: 'num'}, `${s.failed} of ${s.questions}`), h('td', {class: 'num'}, s.syllabi_done ? `${s.sections_matched} of ${s.sections}` : '–'),
          h('td', {class: 'num'}, s.median_seconds ? `${Math.round(s.median_seconds)} s` : '–'), h('td', {class: 'num'}, money(r.usage.judge.usd)));
      })))) });
  }
  function benchAbout() {
    const d = bench.data;
    return card({title: 'How it works', body: [h('ul', {class: 'plain-list small'},
      h('li', null, 'The model builds 5 syllabi from the official outlines, writes 50 questions through Polymath’s real prompt, and checks its own answers.'),
      h('li', null, 'Claude answers each question without seeing the key. Only disagreements go on to a second check against official documentation.'),
      h('li', null, 'The main score counts bad questions the model’s own check missed, because those reach a learner with no warning.'),
      h('li', null, 'Claude can be wrong too, so spot-check a sample, ideally on an exam you know. Your answer outranks the judge.'),
      h('li', null, 'None of your study data, resume or notes is in any benchmark prompt.')),
    h('p', {class: 'small muted', style: {marginTop: '10px'}}, `Exam outlines checked ${d.suite.checked}: `, d.suite.exams.map((e, i) => [i ? ', ' : '', h('a', {href: e.source, target: '_blank', rel: 'noopener noreferrer'}, e.name)]), '.')]});
  }
  function benchItem(r, q, locked) {
    const pick = (key) => benchAct(`api/bench/${r.id}/review`, {item: q.id, key}, (out) => { bench.runs[r.id] = out; });
    const j = q.judge || {};
    const human = (q.human || {}).key;
    const verdict = {correct: 'Key is right', wrong_key: 'Wrong key', ambiguous: 'Ambiguous', flawed: 'Flawed question'}[j.verdict] || 'Not judged yet';
    const qq = q.question;
    return h('section', {class: 'qcard'}, h('div', {class: 'qhead'}, h('span', {class: `tag`}, q.exam_name), h('span', null, q.title), q.bad && !q.caught ? h('span', {class: 'tag bench-flag'}, 'Would reach a learner') : null),
      h('div', {class: 'qstem'}, richText(qq.stem)), qq.code ? h('pre', {class: 'qcode'}, qq.code) : null,
      h('ul', {class: 'opts'}, qq.options.map((o) => h('li', null, h('div', {class: `opt${qq.answer.includes(o.key) ? ' is-picked' : ''}`}, h('span', {class: 'opt-key'}, o.key),
        h('span', {class: 'opt-text'}, richText(o.text), qq.answer.includes(o.key) ? h('span', {class: 'why'}, 'The model’s answer key') : null))))),
      h('div', {class: 'small bench-verdicts'},
        h('div', null, h('b', null, 'Its own check: '), !q.self || !q.self.ran ? 'didn’t run' : q.self.agrees ? 'agreed with its key' : `disagreed${(q.self.answer || []).length ? ` (chose ${q.self.answer.join(', ')})` : ''}`),
        h('div', null, h('b', null, 'Claude: '), verdict, j.blind ? ` · answered ${j.blind.answer.join(', ') || 'none'} without seeing the key` : '', (j.right_answer || []).length && j.verdict !== 'correct' ? ` · right answer ${j.right_answer.join(', ')}` : ''),
        j.reason ? h('div', {class: 'muted'}, j.reason) : null,
        (j.sources || []).length ? h('div', null, j.sources.map((u, i) => [i ? ' · ' : '', h('a', {href: u, target: '_blank', rel: 'noopener noreferrer'}, 'Official source ', icon('out'))])) : j.adjudicated ? h('div', {class: 'muted'}, 'No official source was found for this verdict.') : null),
      locked ? h('p', {class: 'small muted'}, 'You can check this once the run is paused or finished.') : h('div', {class: 'study-actions', style: {marginTop: '10px'}}, h('span', {class: 'small'}, 'Your check:'),
        [['right', 'Key is right'], ['wrong', 'Key is wrong'], ['unsure', 'Not sure']].map(([key, label]) => h('button', {class: `btn btn-small${human === key ? ' btn-primary' : ''}`, type: 'button', 'aria-pressed': String(human === key), disabled: bench.busy,
          onclick: () => pick(human === key ? null : key)}, label))));
  }
  function benchRunPage(id) {
    const r = bench.runs[id];
    const back = h('a', {class: 'btn btn-small', href: link('models', 'benchmark')}, 'All results');
    if (!r) { loadRun(id).then(() => { if (onBench()) renderSoon(); }); return [h('p', {class: 'loading'}, 'Loading the results…')]; }
    if (r.error) return [card({body: [empty('This run couldn’t be loaded.', r.error), back]})];
    const s = r.summary;
    const running = bench.data.active === r.id;
    const paused = r.status === 'paused' || r.status === 'failed';
    const head = card({title: `${r.target.model} · ${r.target.label}`, sub: `Started ${fmtWhen(r.created)}`, actions: [running ? null : (r.status === 'done' ? ratingPill(s.rating) : h('span', {class: 'model-pill'}, 'Paused')), back], body: [
      running ? benchProgress(r) : null,
      paused && r.reason ? h('div', {class: 'callout is-warning'}, icon('alert', 'is-warning'), h('div', null, h('b', null, 'Paused'), h('div', {class: 'small'}, r.reason))) : null,
      s.reasons.length ? h('ul', {class: 'plain-list small'}, s.reasons.map((x) => h('li', null, x))) : null,
      r.self_graded ? h('p', {class: 'small muted'}, 'Claude is judging a Claude model here, so check a sample yourself before relying on this result.') : null,
      h('div', {class: 'kpis', style: {marginTop: '12px'}},
        stat('Would mislead a learner', s.judged ? share(s.escaped_rate) : '–', s.judged ? `${s.escaped} of ${s.judged} · likely range ${share(s.escaped_range[0])} to ${share(s.escaped_range[1])}` : ''),
        stat('Bad questions', String(s.bad), `${s.caught} caught by its own check`),
        stat('Couldn’t write', `${s.failed} of ${s.questions}`, s.median_seconds ? `${Math.round(s.median_seconds)} s per question` : ''),
        stat('Syllabus', s.syllabi_done ? `${s.sections_matched} of ${s.sections}` : '–', `${s.syllabi_done} of ${s.syllabi} finished${isNum(s.weight_off) ? ` · weights ${s.weight_off} pts off` : ''}`),
        stat('Your spot-check', s.reviewed ? `${s.agreed} of ${s.reviewed}` : 'Not yet', s.reviewed ? 'agreed with Claude' : 'see the questions below')),
      h('div', {class: 'study-actions', style: {marginTop: '12px'}},
        running ? h('button', {class: 'btn', type: 'button', disabled: bench.busy, onclick: () => benchAct(`api/bench/${r.id}/pause`, {}, () => {})}, 'Pause') : null,
        paused ? h('button', {class: 'btn btn-primary', type: 'button', disabled: bench.busy, onclick: () => {
          let max = null;
          if (r.code === 'budget') {
            const asked = window.prompt('Raise the budget to how many API-price dollars?', String(Math.min(200, Math.ceil(r.max_usd * 1.5))));
            if (asked == null) return;
            max = Number(asked);
          }
          benchAct(`api/bench/${r.id}/resume`, max == null ? {} : {max_usd: max}, () => {});
        }}, r.code === 'budget' ? 'Raise budget and resume' : 'Resume') : null)]});
    const questions = r.items.filter((x) => x.kind === 'question');
    const exams = [...new Map(questions.map((x) => [x.exam, x.exam_name])).entries()];
    const table = card({title: 'By exam', body: h('div', {class: 'table-wrap'}, h('table', {class: 'table'},
      h('thead', null, h('tr', null, ['Exam', 'Syllabus sections', 'Weights off', 'Judged', 'Bad', 'Would mislead', 'Couldn’t write'].map((x, i) => h('th', {scope: 'col', class: i ? 'num' : ''}, x)))),
      h('tbody', null, exams.map(([id2, name]) => {
        const row = s.by_exam[id2] || {};
        const syl = r.items.find((x) => x.kind === 'syllabus' && x.exam === id2) || {};
        return h('tr', null, h('td', null, name), h('td', {class: 'num'}, syl.score ? `${syl.score.matched} of ${syl.score.sections}` : syl.state === 'failed' ? 'failed' : '–'),
          h('td', {class: 'num'}, syl.score ? `${syl.score.weight_off} pts` : '–'), h('td', {class: 'num'}, String(row.judged || 0)), h('td', {class: 'num'}, String(row.bad || 0)),
          h('td', {class: 'num'}, String(row.escaped || 0)), h('td', {class: 'num'}, String(row.failed || 0)));
      }))))});
    const examSel = h('select', {class: 'input', 'aria-label': 'Exam', onchange: (e) => { bench.exam = e.target.value; render(); }},
      h('option', {value: ''}, 'All exams'), exams.map(([x, name]) => h('option', {value: x, selected: bench.exam === x}, name)));
    const showSel = h('select', {class: 'input', 'aria-label': 'Which questions', onchange: (e) => { bench.show = e.target.value; render(); }},
      [['spot', 'Spot-check sample'], ['bad', 'Bad questions'], ['escaped', 'Would reach a learner'], ['all', 'All questions']].map(([x, label]) => h('option', {value: x, selected: bench.show === x}, label)));
    const shown = questions.filter((q) => q.question && (!bench.exam || q.exam === bench.exam)
      && (bench.show === 'all' || (bench.show === 'spot' && q.spot) || (bench.show === 'bad' && q.bad) || (bench.show === 'escaped' && q.bad && !q.caught)));
    return [head, table, card({title: 'Check the judge', sub: 'Mark a few on an exam you know. Your answer outranks Claude and updates the score.', body: h('div', {class: 'filters', style: {marginBottom: 0}}, examSel, showSel)}),
      ...(shown.length ? shown.map((q) => benchItem(r, q, running)) : [card({body: empty('Nothing to show here yet.')})])];
  }
  function benchmarkPage(v, route) {
    if (!bench.data) {
      if (!bench.loading && !bench.error) loadBench();
      return [bench.error ? card({body: empty('The benchmark couldn’t be loaded.', bench.error)}) : h('p', {class: 'loading'}, 'Loading…')];
    }
    if (!models.scan && !models.scanning) scanLocal();
    pollBench();
    if (route.id) return benchRunPage(route.id);
    const active = bench.data.runs.find((r) => r.id === bench.data.active);
    return [active ? benchRunning(active) : benchStartCard(v), benchResults(), benchAbout()];
  }
  function pageModels(route) {
    if (app.snapshot || !writable()) return h('div', {class: 'stack'}, pageHead('AI model'), card({body: empty('This dashboard can’t change its model.', 'It is a snapshot or was started read-only.')}));
    if (!models.view) {
      if (!models.loading && !models.loadError) loadModels();
      return h('div', {class: 'stack'}, pageHead('AI model'), models.loadError ? card({body: empty('The model settings couldn’t be loaded.', models.loadError)}) : h('p', {class: 'loading'}, 'Loading…'));
    }
    if (offersLocal() && !models.scan && !models.scanning) scanLocal();
    const v = models.view;
    const s = models.status || v.status;
    const fromKind = {key: 'key', subscription: 'claude', local: 'local'}[s.kind] || 'claude';
    const offered = [['claude', 'Claude subscription'], ['key', 'API key'], ['local', 'On this computer']]
      .filter(([id]) => id === 'claude' || v.providers.some((p) => p.kind === id));
    if (v.benchmark) offered.push(['benchmark', 'Benchmark']);
    const has = (id) => offered.some(([one]) => one === id);
    const tab = has(route.tab) ? route.tab : has(fromKind) ? fromKind : 'claude';
    const tabs = offered.length < 2 ? null : h('nav', {class: 'tabs', 'aria-label': 'Kinds of model'}, offered.map(([id, label]) =>
      h('a', {href: link('models', id), 'aria-current': id === tab ? 'page' : null}, label)));
    if (tab === 'benchmark') {
      return h('div', {class: 'stack'}, pageHead('AI model', 'Find out whether a model is safe to learn from, before you trust its questions.'),
        h('div', {class: 'bench-tabs'}, tabs), ...benchmarkPage(v, route));
    }
    return h('div', {class: 'stack'}, pageHead('AI model', 'Choose what writes your questions. You can change it any time, and your progress stays.'),
      currentModelCard(v), v.locked ? card({body: empty('The model is fixed for this run.', 'This dashboard was started with --tutor or in demo mode.')})
        : card({title: 'Choose a model', body: [tabs, tab === 'key' ? keyTab(v) : tab === 'claude' ? claudeTab(v) : localTab(v)]}));
  }

  // --- goals: say what you want, and get a plan -------------------------------------------------
  const goalUI = {id: null, view: null, loading: false, error: null, poll: 0, picked: {}};
  const GOAL_STEPS = ['Understand', 'Research', 'Clarify', 'Plan', 'Study'];
  const GOAL_STEP = {understand: 0, research: 1, clarify: 2, planning: 3, plan: 3, active: 4};
  const KIND_NAME = {exam: 'Exam', credential: 'Credential', board: 'Review board', skill: 'Skill', experience: 'Experience', admin: 'Admin step'};
  const BUSY_HINT = {thinking: ['Usually under 30 seconds.', 20], researching: ['Reading the official sources. Usually 1 to 4 minutes.', 90],
    planning: ['Usually about a minute.', 40]};
  const goalWorking = (g) => !!(g && (g.busy || ((g.plan || {}).milestones || []).some((m) => m.building)
    || ((g.prep || {}).questions || []).some((q) => q.checking)));
  const onGoal = (id) => !!(app.route && app.route.name === 'goals' && app.route.id === id);
  async function loadGoal(id) {
    if (goalUI.loading) return;
    goalUI.loading = true;
    try {
      const fresh = await getJSON(`api/goals/${encodeURIComponent(id)}`);
      if (goalUI.id === id) { goalUI.view = fresh; goalUI.error = null; }
    } catch (err) {
      if (goalUI.id === id) goalUI.error = err.message || String(err);
    } finally { goalUI.loading = false; }
    if (onGoal(id)) renderSoon();
    pollGoal();
  }
  function pollGoal() {
    const g = goalUI.view;
    if (goalUI.poll || !g || !goalWorking(g) || !onGoal(g.id)) return;
    goalUI.poll = setTimeout(async () => {
      goalUI.poll = 0;
      try {
        const fresh = await getJSON(`api/goals/${encodeURIComponent(g.id)}`);
        if (goalUI.id !== g.id) return;
        const changed = goalKey(fresh) !== goalKey(goalUI.view); // "updated" has one-second steps: a quick job can finish inside one
        goalUI.view = fresh;
        if (changed) renderSoon();
        else if (fresh.busy) {
          const el = view.querySelector('[data-goal-clock]');
          if (el) el.textContent = `${fresh.busy.label}… ${fresh.busy.seconds || 0} s`;
          const bar = view.querySelector('[data-goal-bar]');
          if (bar) bar.style.width = `${goalPct(fresh.busy)}%`;
        }
      } catch (err) { /* try again */ }
      pollGoal();
    }, 1500);
  }
  const goalKey = (g) => JSON.stringify({...g, busy: g.busy ? {stage: g.busy.stage, label: g.busy.label} : null});
  const goalPct = (b) => Math.min(92, Math.round(100 * (1 - Math.exp(-(b.seconds || 0) / ((BUSY_HINT[b.stage] || [])[1] || 30)))));
  function goalAct(g, action, body, key) {
    return act(`api/goals/${g.id}/${action}`, body || {}, {key: key || `goal:${action}`, done: (out) => {
      if (out && out.id === g.id) { goalUI.view = out; }
      refresh();
      setTimeout(pollGoal, 0);
    }});
  }
  function goalSteps(g) {
    const at = GOAL_STEP[g.stage] ?? 0;
    return h('ol', {class: 'goal-steps', 'aria-label': 'Where this goal is'}, GOAL_STEPS.map((name, i) =>
      h('li', {class: i < at ? 'is-done' : i === at ? 'is-now' : '', 'aria-current': i === at ? 'step' : null}, name)));
  }
  function goalProgress(g) {
    return h('div', {class: 'goal-meter'}, h('div', {class: 'writing-bar', role: 'img', 'aria-label': `${pct(g.progress)} of the plan done`},
      h('span', {style: {width: `${Math.round((g.progress || 0) * 100)}%`}})), h('span', {class: 'small muted'}, `${pct(g.progress)} done`));
  }
  function goalCard(g) {
    if (g.kind === 'interview') {
      const line = g.busy ? `${g.busy.label}…` : g.error ? 'Needs you: it stopped partway' : g.next ? `Next: ${g.next.title}` : g.total ? 'Every question is ready' : 'No questions yet';
      return card({title: h('a', {href: link('goals', g.id)}, g.title),
        sub: g.target_date ? `Interview ${fmtDay(localDay(g.target_date))}${isNum(g.days_left) && g.days_left >= 0 ? ` · ${g.days_left === 0 ? 'today' : plural(g.days_left, 'day') + ' to go'}` : ''}` : 'Interview prep',
        actions: [h('span', {class: 'tag'}, 'Interview'), g.total ? [' ', h('span', {class: 'tag'}, `${g.done_count} of ${g.total} ready`)] : null],
        body: [g.total ? goalProgress(g) : null, h('p', {class: 'small', style: {marginTop: '8px'}}, line),
          h('div', {class: 'study-actions', style: {marginTop: '10px'}}, h('a', {class: 'btn btn-small', href: link('goals', g.id)}, 'Open the prep'))]});
    }
    const s = g.schedule || {};
    const line = g.busy ? `${g.busy.label}…` : g.error ? 'Needs you: it stopped partway'
      : g.status === 'draft' ? (g.plan ? 'Plan ready for you to review' : 'Still talking it through')
        : g.next ? `Next: ${g.next.title}` : 'Every step is done';
    return card({title: h('a', {href: link('goals', g.id)}, g.title),
      sub: s.finish ? `On this pace, done by ${fmtDay(localDay(s.finish))}${g.target_date ? ` · target ${fmtDay(localDay(g.target_date))}` : ''}` : (g.target_date ? `Target ${fmtDay(localDay(g.target_date))}` : ''),
      actions: [g.status === 'draft' ? h('span', {class: 'tag'}, 'Draft') : isNum(g.total) ? h('span', {class: 'tag'}, `${g.done_count} of ${g.total} steps`) : null],
      body: [g.plan !== null && isNum(g.progress) && g.total ? goalProgress(g) : null, h('p', {class: 'small', style: {marginTop: '8px'}}, line),
        h('div', {class: 'study-actions', style: {marginTop: '10px'}}, h('a', {class: 'btn btn-small', href: link('goals', g.id)}, g.status === 'active' ? 'Open the plan' : 'Continue'))]});
  }
  function pageGoals() {
    const st = app.state;
    const goals = st.goals || [];
    const add = canStudy() ? h('div', {class: 'head-actions'}, h('a', {class: 'btn', href: link('goals', 'interview')}, 'Prepare for an interview'),
      h('a', {class: 'btn btn-primary', href: link('goals', 'new')}, 'Add a goal')) : null;
    const rest = tracks();
    return h('div', {class: 'stack'}, pageHead('Goals', 'Everything you’re working toward. Say a new goal and Polymath researches what it takes.', add),
      goals.length ? h('div', {class: 'grid-cards'}, goals.map(goalCard))
        : card({body: empty('No planned goals yet.', 'Add one, such as “I want to become a Salesforce CTA”. It finds the prerequisites, asks what you already have, and builds your plan.')}),
      rest.length ? card({title: 'Your study tracks', sub: 'Each exam or subject you’re studying', body: h('div', {class: 'rows'}, rest.map((t) => h('div', {class: 'row'},
        h('div', {class: 'row-name'}, h('a', {href: link('track', t.slug)}, h('b', null, t.title)), h('span', {class: 'small muted'},
          `${t.status === 'active' ? 'Active' : t.status === 'paused' ? 'Paused' : 'Done'}${t.target_date ? ` · ${fmtDay(localDay(t.target_date))}` : ''} · ${durationText(((st.time || {}).tracks || {})[t.slug]?.total || 0)} active`)),
        h('div', {class: 'row-bar'}, barH(t.concepts ? t.mastered / t.concepts : 0, `${t.mastered} of ${t.concepts} mastered`)),
        h('div', {class: 'row-figure'}, h('b', null, `${t.mastered}/${t.concepts}`)))))}) : null);
  }
  function pageGoalNew() {
    const can = canStudy();
    const examples = ['I want to achieve the Salesforce CTA certification', 'Pass the AWS Solutions Architect Professional exam', 'Learn Rust well enough to build production services', 'Become a Claude Certified Architect'];
    const input = h('textarea', {class: 'input goal-input', rows: 3, 'data-focus': 'goal-new', 'aria-label': 'What you want to achieve',
      placeholder: 'For example: I want to achieve the Salesforce CTA certification', disabled: !can || busy('goal-new')});
    input.value = app.drafts.goalNew || '';
    input.addEventListener('input', () => { app.drafts.goalNew = input.value; });
    const go = (text) => {
      const said = String(text || '').trim();
      if (!said) { input.focus(); return; }
      act('api/goals', {text: said}, {key: 'goal-new', done: (out) => {
        if (!out || !out.id) return;
        goalUI.id = out.id; goalUI.view = out; goalUI.error = null;
        delete app.drafts.goalNew;
        location.hash = link('goals', out.id);
        refresh();
      }});
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); go(input.value); } });
    return h('div', {class: 'stack'}, pageHead('Add a goal', 'Say it in your own words. Polymath looks up what it really takes, asks what you already have, and builds your plan.',
      h('a', {class: 'btn', href: link('goals', 'interview')}, 'Prepare for an interview instead'), [h('a', {href: '#/goals'}, 'Goals')]),
      card({body: [h('label', {class: 'stat-label', style: {display: 'block', marginBottom: '6px'}}, 'What do you want to achieve?'), input,
        h('div', {class: 'prompts', style: {marginTop: '10px'}}, examples.map((x) => h('button', {class: 'chip', type: 'button', disabled: !can, onclick: () => { app.drafts.goalNew = x; render(); }}, x))),
        h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-primary', type: 'button', 'data-focus': 'goal-go', disabled: !can || busy('goal-new'), onclick: () => go(input.value)},
          busy('goal-new') ? 'Starting…' : 'Find out what it takes'),
        can ? h('span', {class: 'form-note'}, 'Ctrl+Enter also works') : h('span', {class: 'form-note'}, 'This needs an AI model. Choose one on the AI model page.'))]}));
  }
  function goalChat(g) {
    const can = canStudy() && !g.busy;
    const msgs = g.messages || [];
    const last = msgs[msgs.length - 1];
    const who = (m) => (m.role === 'you' ? 'You' : ((app.state.learner.persona || {}).name || 'Polymath'));
    const send = (text) => {
      const said = String(text || '').trim();
      if (!said || !can) return;
      goalAct(g, 'message', {text: said}, 'goal-say').then((out) => { if (out) { delete app.drafts[`goal:${g.id}`]; goalUI.picked = {}; redraw(); } });
    };
    const options = () => {
      if (!last || last.role !== 'coach' || !(last.options || []).length || g.busy) return null;
      if (!last.multi) return h('div', {class: 'prompts'}, last.options.map((o) => h('button', {class: 'chip', type: 'button', disabled: !can, onclick: () => send(o)}, o)));
      const picked = goalUI.picked;
      const list = Object.keys(picked).filter((k) => picked[k]);
      return [h('div', {class: 'prompts', role: 'group', 'aria-label': 'Choose any that apply'}, last.options.map((o) => h('button', {class: 'chip', type: 'button',
        'aria-pressed': String(!!picked[o]), disabled: !can, onclick: () => { picked[o] = !picked[o]; redraw(); }}, o))),
      list.length ? h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-primary btn-small', type: 'button', disabled: !can, onclick: () => send(list.join(', '))}, `Send ${list.length} selected`)) : null];
    };
    const input = h('textarea', {class: 'input', rows: 2, 'data-focus': `goal-say:${g.id}`, 'aria-label': 'Your reply',
      placeholder: g.plan ? 'Ask about the plan, or tell me what to change' : 'Type your answer', disabled: !can});
    input.value = app.drafts[`goal:${g.id}`] || '';
    input.addEventListener('input', () => { app.drafts[`goal:${g.id}`] = input.value; });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input.value); } });
    const b = g.busy;
    const hint = b ? BUSY_HINT[b.stage] : null;
    const thread = h('div', {class: 'chat', 'aria-live': 'polite'},
      msgs.map((m) => h('div', {class: `msg ${m.role === 'you' ? 'is-you' : 'is-coach'}`}, h('div', {class: 'msg-who'}, who(m)), m.role === 'you' ? h('p', null, m.text) : markdown(m.text))),
      b ? h('div', {class: 'msg is-coach'}, h('div', {class: 'qwriting'}, h('span', {class: 'spinner', 'aria-hidden': 'true'}), h('span', {'data-goal-clock': ''}, `${b.label}… ${b.seconds || 0} s`)),
        h('div', {class: 'writing-bar', 'aria-hidden': 'true'}, h('span', {'data-goal-bar': '', style: {width: `${goalPct(b)}%`}})),
        hint ? h('div', {class: 'small muted', style: {marginTop: '6px'}}, hint[0]) : null) : null,
      g.error ? h('div', {class: 'callout'}, icon('alert', 'is-warning'), h('div', null, h('b', null, 'That step didn’t finish.'), h('div', {class: 'small muted'}, g.error.text),
        h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-small', type: 'button', disabled: busy('goal:retry') || !canStudy(), onclick: () => goalAct(g, 'retry')}, 'Try again')))) : null);
    requestAnimationFrame(() => { thread.scrollTop = thread.scrollHeight; }); // the thread only: never the page
    return card({title: 'Conversation', cls: 'goal-chat', body: [thread,
    h('div', {style: {marginTop: '12px'}}, options()), h('div', {style: {marginTop: '10px'}}, input),
    h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-primary', type: 'button', disabled: !can || busy('goal-say'), onclick: () => send(input.value)}, g.busy ? 'Working…' : 'Send'),
      h('span', {class: 'form-note'}, 'Enter sends, Shift+Enter adds a line'))]});
  }
  function researchCard(g, foldIt) {
    const r = g.research;
    if (!r) return null;
    const names = new Map(r.requirements.map((x) => [x.id, x.title]));
    const body = [r.summary ? h('p', null, r.summary) : null, h('ul', {class: 'road'}, r.requirements.map((x) => h('li', {class: 'road-item'},
      h('div', {class: 'road-main'}, h('b', null, x.title), ' ', h('span', {class: 'tag'}, KIND_NAME[x.kind] || x.kind), x.required ? null : [' ', h('span', {class: 'tag'}, 'Recommended')],
        x.details ? h('div', {class: 'small'}, x.details) : null,
        x.exam_facts ? h('div', {class: 'small muted'}, x.exam_facts) : null,
        x.depends_on.length ? h('div', {class: 'small muted'}, `Needs: ${x.depends_on.map((d) => names.get(d) || d).join(', ')}`) : null)))),
    (r.uncertain || []).length ? h('div', {class: 'callout', style: {marginTop: '12px'}}, icon('alert', 'is-warning'), h('div', null, h('b', null, 'Couldn’t confirm'),
      h('ul', {class: 'plain-list small'}, r.uncertain.map((u) => h('li', null, u))))) : null,
    (r.sources || []).length ? h('div', {class: 'small muted', style: {marginTop: '12px'}}, 'Sources: ', r.sources.filter((s) => safeHref(s.url)).map((s, i) => [i ? ' · ' : '',
      h('a', {href: s.url, target: '_blank', rel: 'noopener noreferrer'}, s.title, ' ', icon('out'))])) : null];
    return foldIt ? card({body: folded(`goal-research:${g.id}`, `What it takes (${plural(r.requirements.length, 'step')}, from ${plural((r.sources || []).length, 'source')})`, body)})
      : card({title: 'What it takes', sub: 'From the current official sources', body});
  }
  function planSummary(g) {
    const p = g.plan, s = g.schedule || {};
    const save = (body) => goalAct(g, 'settings', body, 'goal-settings');
    const minutes = h('input', {class: 'input', type: 'number', min: '5', max: '1440', step: '5', inputmode: 'numeric', value: g.daily_minutes || '',
      'aria-label': 'Minutes a day', 'data-focus': `goal-min:${g.id}`, disabled: !writable()});
    minutes.addEventListener('change', () => { const n = Number(minutes.value); if (n >= 5) save({daily_minutes: Math.round(n)}); });
    const when = h('input', {class: 'input', type: 'date', value: g.target_date || '', 'aria-label': 'Target date', 'data-focus': `goal-date:${g.id}`, disabled: !writable()});
    when.addEventListener('change', () => save({target_date: when.value || ''}));
    const fit = s.fits === false ? h('div', {class: 'callout', style: {marginTop: '12px'}}, icon('alert', 'is-warning'), h('div', null, h('b', null, 'This doesn’t fit your target date.'),
      s.minutes_needed ? h('div', {class: 'small muted'}, `You’d need about ${durationText(s.minutes_needed * 60)} a day, or a later date.`) : null))
      : s.fits ? h('div', {class: 'callout', style: {marginTop: '12px'}}, icon('check', 'is-good'), h('div', null, h('b', null, 'On this pace you make your target date.'))) : null;
    return card({title: 'Your plan', actions: g.status === 'active' ? [h('button', {class: 'btn btn-small', type: 'button', onclick: () => {
      if (window.confirm('Remove this goal from your list? Your study tracks and progress stay.')) goalAct(g, 'archive').then((o) => { if (o) location.hash = '#/goals'; });
    }}, 'Remove')] : null, body: [p.summary ? h('p', null, p.summary) : null, g.status === 'active' ? goalProgress(g) : null,
      h('div', {class: 'kpis', style: {marginTop: '12px'}},
        miniStat('Done by', s.finish ? fmtDay(localDay(s.finish)) : '–', s.finish ? localDay(s.finish).getFullYear() : ''),
        miniStat('Study left', `${s.hours_left || 0} h`, `${g.done_count || 0} of ${g.total || 0} steps done`),
        h('div', null, h('div', {class: 'stat-label'}, 'Minutes a day'), minutes),
        h('div', null, h('div', {class: 'stat-label'}, 'Target date'), when)),
      fit, p.first_step ? h('p', {style: {marginTop: '12px'}}, h('b', null, 'First step: '), p.first_step) : null,
      g.status !== 'active' ? h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-primary', type: 'button', disabled: busy('goal:accept') || !!g.busy, onclick: () => goalAct(g, 'accept')}, 'Start this plan'),
        h('span', {class: 'form-note'}, 'Or tell me what to change in the conversation.')) : null]});
  }
  function gapsCard(p) {
    if (!(p.gaps || []).length) return null;
    const tone = {high: 'is-critical', medium: 'is-warning', low: 'is-good'};
    return card({title: 'Your gaps', sub: 'What stands between you and this goal, worst first', body: h('ul', {class: 'road'}, p.gaps.map((x) => h('li', {class: 'road-item'},
      h('span', {class: `gap-dot ${tone[x.severity] || ''}`, title: `${x.severity} priority`}), h('div', {class: 'road-main'}, h('b', null, x.title), h('span', {class: 'sr-only'}, ` (${x.severity} priority)`),
        x.why ? h('div', {class: 'small'}, x.why) : null, x.action ? h('div', {class: 'small muted'}, `Next: ${x.action}`) : null))))});
  }
  function milestoneRow(m, g, names) {
    const label = {done: 'Done', studying: 'Studying', next: 'Up next', locked: 'Waiting', building: 'Building'}[m.status];
    const act1 = (action) => goalAct(g, 'milestone', {milestone: m.id, action}, `ms:${m.id}`);
    const pending = busy(`ms:${m.id}`);
    const active = g.status === 'active';
    const buttons = [];
    if (active && m.status === 'studying' && m.track && track(m.track)) {
      if (canStudy() && track(m.track).status === 'active') buttons.push(h('button', {class: 'btn btn-primary btn-small', type: 'button', disabled: busy('start'), onclick: () => startStudy('learn', m.track)}, 'Study'));
      buttons.push(h('a', {class: 'btn btn-small', href: link('track', m.track)}, 'Progress'));
    } else if (active && m.status === 'next' && m.can_study) {
      buttons.push(h('button', {class: 'btn btn-primary btn-small', type: 'button', disabled: pending || !canStudy(), onclick: () => act1('start')},
        m.blueprint ? 'Start studying' : m.build_error ? 'Try building it again' : 'Build its syllabus'));
    }
    if (m.status === 'done') buttons.push(h('button', {class: 'btn btn-small', type: 'button', disabled: pending, onclick: () => act1('undo')}, 'Undo'));
    else if (m.kind !== 'credential' && m.status !== 'building') {
      buttons.push(h('button', {class: 'btn btn-small', type: 'button', disabled: pending, onclick: () => act1('done')}, m.status === 'studying' ? 'I passed it' : 'I already have this'));
    }
    const when = m.start && m.end ? (m.start === m.end ? fmtDay(localDay(m.end)) : `${fmtDay(localDay(m.start))} to ${fmtDay(localDay(m.end))}`) : '';
    const facts = [KIND_NAME[m.kind] || m.kind, m.hours ? `${m.hours} h` : null, when || null].filter(Boolean).join(' · ');
    return h('li', {class: `road-item is-${m.status}`}, h('span', {class: 'road-dot', 'aria-hidden': 'true'}, m.status === 'done' ? icon('check') : null),
      h('div', {class: 'road-main'}, h('div', {class: 'road-title'}, h('b', null, m.title), ' ', h('span', {class: `tag status-${m.status}`}, label)),
        h('div', {class: 'small muted'}, facts), m.why ? h('div', {class: 'small'}, m.why) : null,
        m.depends_on.length && m.status === 'locked' ? h('div', {class: 'small muted'}, `Needs: ${m.depends_on.map((d) => names.get(d) || d).join(', ')}`) : null,
        isNum(m.mastered) && m.concepts && m.status !== 'done' ? h('div', {class: 'small muted'}, `${m.mastered} of ${m.concepts} concepts mastered`) : null,
        m.status === 'building' ? h('div', {class: 'qwriting small', style: {marginTop: '6px'}}, h('span', {class: 'spinner', 'aria-hidden': 'true'}), 'Reading the official guide and building its syllabus. Usually 2 to 6 minutes.') : null,
        m.build_error ? h('div', {class: 'small', style: {color: 'var(--critical-ink)', marginTop: '4px'}}, m.build_error) : null,
        buttons.length ? h('div', {class: 'study-actions', style: {marginTop: '8px'}}, buttons) : null));
  }
  function pageGoal(id) {
    if (goalUI.id !== id) { goalUI.id = id; goalUI.view = null; goalUI.error = null; goalUI.picked = {}; }
    const g = goalUI.view;
    if (!g || g.id !== id) {
      if (!goalUI.error) loadGoal(id);
      return goalUI.error ? card({body: empty('Couldn’t open this goal.', goalUI.error)}) : h('p', {class: 'loading'}, 'Loading your goal…');
    }
    if (g.kind === 'interview') return pageInterview(g);
    pollGoal();
    const p = g.plan;
    const names = new Map(((p || {}).milestones || []).map((m) => [m.id, m.title]));
    const side = p ? [planSummary(g), gapsCard(p),
      card({title: 'Roadmap', sub: 'In order. Each step opens when what it depends on is done.', body: h('ol', {class: 'road is-line'}, inRoadOrder(p.milestones).map((m) => milestoneRow(m, g, names)))}),
      (p.risks || []).length ? card({title: 'Risks', body: h('ul', {class: 'plain-list small'}, p.risks.map((x) => h('li', null, x)))}) : null,
      researchCard(g, true)]
      : g.research ? [researchCard(g, false)]
        : [card({title: 'What happens next', body: h('ol', {class: 'plain-list'}, h('li', null, 'I look up the current official requirements and every prerequisite.'),
          h('li', null, 'I ask what you already have, and how much time you’ve got.'), h('li', null, 'You get your gaps and a dated plan you can change.'))})];
    return h('div', {class: 'stack'}, pageHead(g.title, null, null, [h('a', {href: '#/goals'}, 'Goals')]), goalSteps(g),
      h('div', {class: 'goal-grid'}, h('div', {class: 'stack'}, side), goalChat(g)));
  }
  function inRoadOrder(items) {
    const seen = new Set(), ids = new Set(items.map((m) => m.id)), out = [], left = items.slice();
    while (left.length) {
      const i = Math.max(0, left.findIndex((m) => (m.depends_on || []).every((d) => seen.has(d) || !ids.has(d))));
      const [m] = left.splice(i, 1);
      seen.add(m.id);
      out.push(m);
    }
    return out;
  }

  // --- interview prep: a job, your resume and who will interview you --------------------------------
  const IV_CATEGORY = {resume: 'Your resume', role: 'The role', technical: 'Technical', design: 'Design', behavioral: 'Behavioral',
    leadership: 'Leadership', company: 'The company', interviewer: 'Interviewer'};
  const ivUI = {tab: {}, filter: {}, open: {}, follow: {}, defaults: null, uploading: null};
  const blankPerson = () => ({name: '', title: '', url: '', round: '', notes: ''});
  const blankInterview = () => ({company: '', role: '', level: '', location: '', job_url: '', company_url: '', round: '', interview_date: '',
    notes: '', description: '', resume: '', interviewers: [blankPerson()]});
  function ivField(d, key, label, o = {}) {
    const el = h(o.rows ? 'textarea' : 'input', {class: 'input', 'data-focus': `${o.scope || 'iv'}:${key}`, ...(o.rows ? {rows: o.rows} : {type: o.type || 'text'}),
      placeholder: o.placeholder || null, autocomplete: 'off'});
    el.value = d[key] || '';
    el.addEventListener('input', () => { d[key] = el.value; });
    return h('label', {class: `iv-field${o.wide ? ' is-wide' : ''}`}, h('span', {class: 'stat-label'}, label, o.required ? h('span', {'aria-hidden': 'true'}, ' *') : null),
      el, o.hint ? h('span', {class: 'small muted'}, o.hint) : null);
  }
  async function ivUpload(d, key, file) {
    if (file.size > 5 * 1024 * 1024) { toast('That file is over 5 MB. Paste the text instead.', true); return; }
    ivUI.uploading = key;
    redraw();
    try {
      const data = await new Promise((ok, no) => {
        const r = new FileReader();
        r.onload = () => ok(String(r.result).split(',')[1] || '');
        r.onerror = () => no(new Error('Couldn’t read that file.'));
        r.readAsDataURL(file);
      });
      const out = await post('api/goals/extract', {name: file.name, data});
      d[key] = out.text;
      toast(`Read ${file.name}. Check the text below.`);
    } catch (err) { toast(err.message || String(err), true); }
    finally { ivUI.uploading = null; redraw(); }
  }
  function uploadButton(d, key, label) {
    const input = h('input', {type: 'file', class: 'sr-only', accept: '.pdf,.docx,.doc,.rtf,.odt,.txt,.md', 'aria-label': label, disabled: !!ivUI.uploading});
    input.addEventListener('change', () => { const f = input.files[0]; input.value = ''; if (f) ivUpload(d, key, f); });
    return h('label', {class: 'btn btn-small iv-upload'}, input, ivUI.uploading === key ? 'Reading the file…' : label);
  }
  function interviewForm(d, scope) {
    const people = d.interviewers && d.interviewers.length ? d.interviewers : (d.interviewers = [blankPerson()]);
    const o = (extra) => ({scope, ...extra});
    return [card({title: 'The job', body: h('div', {class: 'iv-grid'},
      ivField(d, 'company', 'Company', o({required: true, placeholder: 'Acme Corp'})), ivField(d, 'role', 'Role', o({required: true, placeholder: 'Principal Salesforce Architect'})),
      ivField(d, 'level', 'Level', o({placeholder: 'Director, Staff, Principal…'})), ivField(d, 'location', 'Location', o({placeholder: 'Remote, San Francisco…'})),
      ivField(d, 'job_url', 'Link to the job posting', o({type: 'url', placeholder: 'https://'})), ivField(d, 'company_url', 'Company website', o({type: 'url', placeholder: 'https://'})),
      ivField(d, 'interview_date', 'Interview date', o({type: 'date'})), ivField(d, 'round', 'Which round', o({placeholder: 'Hiring manager, panel, system design…'})),
      ivField(d, 'notes', 'Anything else about this job', o({rows: 3, wide: true, placeholder: 'What the recruiter told you, the team, why they’re hiring, what you want from it', hint: 'Stays private: never sent to a web search.'})))}),
    card({title: 'Job description', actions: [uploadButton(d, 'description', 'Upload a file')],
      body: ivField(d, 'description', 'Paste the full job description', o({rows: 9, wide: true, required: true}))}),
    card({title: 'Your resume', sub: 'Used to compare you with the job. It’s never sent to a web search.', actions: [uploadButton(d, 'resume', 'Upload PDF or Word')],
      body: ivField(d, 'resume', 'Resume text', o({rows: 9, wide: true, required: true, hint: 'After uploading, check the text came through correctly.'}))}),
    card({title: 'Who’s interviewing you', sub: 'Optional. Polymath looks up their public professional background only, never their personal life.',
      body: [people.map((p, n) => h('div', {class: 'iv-person'}, h('div', {class: 'iv-grid'},
        ivField(p, 'name', 'Name', {scope: `${scope}:p${n}`, placeholder: 'Their full name'}), ivField(p, 'title', 'Their title', {scope: `${scope}:p${n}`, placeholder: 'VP of Engineering'}),
        ivField(p, 'url', 'Profile link', {scope: `${scope}:p${n}`, type: 'url', placeholder: 'https://', hint: 'Helps confirm it’s the right person.'}),
        ivField(p, 'round', 'Their round', {scope: `${scope}:p${n}`, placeholder: 'Technical, culture…'}),
        ivField(p, 'notes', 'What you already know, or their bio pasted', {scope: `${scope}:p${n}`, rows: 2, wide: true, hint: 'Useful when a profile can’t be read automatically. Never sent to a web search.'})),
      people.length > 1 ? h('button', {class: 'btn btn-small', type: 'button', onclick: () => { people.splice(n, 1); render(); }}, 'Remove') : null)),
      people.length < 6 ? h('button', {class: 'btn btn-small', type: 'button', style: {marginTop: '10px'}, onclick: () => { people.push(blankPerson()); render(); }}, 'Add another interviewer') : null]})];
  }
  const ivMissing = (d) => [['company', 'the company'], ['role', 'the role'], ['description', 'the job description'], ['resume', 'your resume']]
    .filter(([k]) => !String(d[k] || '').trim()).map(([, label]) => label);
  function pageInterviewNew() {
    if (!app.drafts.interview) app.drafts.interview = blankInterview();
    const d = app.drafts.interview;
    if (ivUI.defaults === null) {
      ivUI.defaults = false;
      getJSON('api/goals/interview-defaults').then((o) => {
        ivUI.defaults = o;
        if (o.resume && !d.resume) { d.resume = o.resume; if (app.route && app.route.id === 'interview') renderSoon(); }
      }).catch(() => { ivUI.defaults = {resume: ''}; });
    }
    const can = canStudy();
    const go = () => {
      const missing = ivMissing(d);
      if (missing.length) { toast(`Add ${missing.join(', ')}.`, true); return; }
      act('api/goals/interview', d, {key: 'iv-create', done: (out) => {
        if (!out || !out.id) return;
        goalUI.id = out.id; goalUI.view = out; goalUI.error = null;
        delete app.drafts.interview;
        ivUI.defaults = null;
        location.hash = link('goals', out.id);
        refresh();
      }});
    };
    return h('div', {class: 'stack'}, pageHead('Prepare for an interview', 'Add the job, your resume and who you’re meeting. Polymath researches them and works out what you’re likely to be asked.', null, [h('a', {href: '#/goals'}, 'Goals')]),
      interviewForm(d, 'iv'), h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-primary', type: 'button', 'data-focus': 'iv-go', disabled: !can || busy('iv-create'), onclick: go},
        busy('iv-create') ? 'Starting…' : 'Research and prepare my questions'), h('span', {class: 'form-note'}, can ? 'Usually 3 to 8 minutes. You can keep studying meanwhile.' : 'This needs an AI model. Choose one on the AI model page.')));
  }
  const IV_SCORE = (n) => (n >= 4 ? 'is-good' : n === 3 ? 'is-warning' : 'is-critical');
  const bullets = (rows) => h('ul', {class: 'plain-list small'}, rows.map((x) => h('li', null, inline(x))));
  function ivFeedback(g, q, a) {
    return h('div', {class: 'iv-feedback'}, h('div', {class: 'iv-score-row'}, h('span', {class: `iv-score ${IV_SCORE(a.score)}`}, `${a.score}/5`), h('b', null, a.verdict)),
      a.followup ? h('div', {class: 'small muted'}, `Follow-up: ${a.question}`) : null,
      a.strengths.length ? [h('h4', null, 'What worked'), bullets(a.strengths)] : null,
      a.missing.length ? [h('h4', null, 'What it needed'), bullets(a.missing)] : null,
      a.conflicts.length ? h('div', {class: 'lesson-box is-warn'}, h('h4', null, 'Hard to defend'), bullets(a.conflicts)) : null,
      a.stronger_answer ? [h('h4', null, 'A stronger version, from your own experience'), markdown(a.stronger_answer)] : null);
  }
  function ivPractice(g, q) {
    const key = `iv-ans:${g.id}:${q.id}`;
    const attempts = q.attempts || [];
    const last = attempts[attempts.length - 1];
    const following = !!ivUI.follow[key] && !!(last && last.follow_up);
    const can = canStudy() && !q.checking;
    const input = h('textarea', {class: 'input', rows: 6, 'data-focus': key, disabled: !can, 'aria-label': 'Your answer',
      placeholder: following ? 'Answer the follow-up as you would out loud' : 'Answer as you would out loud'});
    input.value = app.drafts[key] || '';
    input.addEventListener('input', () => { app.drafts[key] = input.value; });
    const send = () => {
      const text = input.value.trim();
      if (text.length < 15) { toast('Write out your answer first: a sentence or two at least.', true); return; }
      goalAct(g, 'practice', {qid: q.id, answer: text, followup: following}, `iv:${q.id}`).then((out) => {
        if (out) { delete app.drafts[key]; delete ivUI.follow[key]; redraw(); }
      });
    };
    const guide = (q.covers || q.your_material) ? folded(`iv-guide:${q.id}`, 'What a strong answer covers', [
      (q.covers || []).length ? bullets(q.covers) : null,
      (q.your_material || []).length ? [h('h4', null, 'From your resume'), bullets(q.your_material)] : null,
      q.watch_out ? h('p', {class: 'small'}, h('b', null, 'Watch out: '), q.watch_out) : null,
      (q.follow_ups || []).length ? [h('h4', null, 'Likely follow-ups'), bullets(q.follow_ups)] : null]) : null;
    return h('div', {class: 'iv-practice'},
      last ? ivFeedback(g, q, last) : null,
      last && last.follow_up && !following ? h('div', {class: 'callout', style: {marginTop: '10px'}}, icon('dot'), h('div', null, h('b', null, 'They’d probably ask next: '), last.follow_up,
        h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-small', type: 'button', onclick: () => { ivUI.follow[key] = true; render(); }}, 'Answer this follow-up')))) : null,
      following ? h('p', {style: {marginTop: '10px'}}, h('b', null, 'Follow-up: '), last.follow_up) : null,
      q.checking ? h('div', {class: 'qwriting', style: {marginTop: '10px'}}, h('span', {class: 'spinner', 'aria-hidden': 'true'}), 'Judging your answer. Usually under a minute.')
        : [h('div', {style: {marginTop: '10px'}}, input), q.feedback_error ? h('p', {class: 'small', style: {color: 'var(--critical-ink)'}}, q.feedback_error) : null,
          h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-primary btn-small', type: 'button', disabled: !can || busy(`iv:${q.id}`), onclick: send}, last ? 'Send this answer' : 'Get feedback'),
            following ? h('button', {class: 'btn btn-small', type: 'button', onclick: () => { delete ivUI.follow[key]; render(); }}, 'Back to the main question') : null,
            !attempts.length && !q.revealed ? h('button', {class: 'btn btn-small', type: 'button', disabled: busy(`iv-r:${q.id}`), onclick: () => goalAct(g, 'reveal', {qid: q.id}, `iv-r:${q.id}`)}, 'Show me what to cover') : null)],
      guide);
  }
  function ivQuestion(g, q) {
    const open = ivUI.open[g.id] === q.id;
    const state = q.status === 'ready' ? ['Ready', 'status-done'] : q.status === 'practiced' ? [`Best ${q.best}/5`, 'status-next'] : null;
    return h('li', {class: `iv-q${open ? ' is-open' : ''}`, 'data-focus-scope': `ivq:${q.id}`},
      h('button', {class: 'iv-q-head', type: 'button', 'aria-expanded': String(open), 'data-focus': `ivq-open:${q.id}`, onclick: () => { ivUI.open[g.id] = open ? null : q.id; render(); }},
        h('span', {class: `gap-dot ${{high: 'is-critical', medium: 'is-warning', low: 'is-good'}[q.likelihood]}`, title: `${q.likelihood} likelihood`}),
        h('span', {class: 'iv-q-text'}, q.question,
          h('span', {class: 'iv-q-tags'}, h('span', {class: 'tag'}, {high: 'Very likely', medium: 'Likely', low: 'Possible'}[q.likelihood]), h('span', {class: 'tag'}, IV_CATEGORY[q.category] || q.category), q.interviewer ? h('span', {class: 'tag'}, q.interviewer) : null,
            state ? h('span', {class: `tag ${state[1]}`}, state[0]) : null))),
      open ? [q.why ? h('p', {class: 'small muted iv-why'}, h('b', null, 'Why it may come up: '), q.why) : null, ivPractice(g, q)] : null);
  }
  function ivQuestions(g) {
    const all = (g.prep || {}).questions || [];
    if (!all.length) return card({body: empty('No questions yet.', g.busy ? 'They appear here once the research is done.' : 'Something stopped before they were written.')});
    const filter = ivUI.filter[g.id] || 'all';
    const cats = [...new Set(all.map((q) => q.category))];
    const shown = all.filter((q) => (filter === 'all' ? true : filter === 'todo' ? q.status !== 'ready' : q.category === filter));
    const chip = (id, label) => h('button', {class: 'chip', type: 'button', 'aria-pressed': String(filter === id), onclick: () => { ivUI.filter[g.id] = id; render(); }}, label);
    return card({title: 'Likely questions', sub: 'Predictions from the job, your resume and the research, most likely first. Answer first, then see the feedback.',
      actions: g.next ? [h('button', {class: 'btn btn-primary btn-small', type: 'button', onclick: () => { ivUI.filter[g.id] = 'all'; ivUI.open[g.id] = g.next.id; render(); requestAnimationFrame(() => view.querySelector(`[data-focus="ivq-open:${g.next.id}"]`)?.scrollIntoView({block: 'center'})); }}, 'Practice the next one')] : null,
      body: [h('div', {class: 'prompts'}, chip('all', `All ${all.length}`), chip('todo', `Not ready ${all.filter((q) => q.status !== 'ready').length}`), cats.map((c) => chip(c, IV_CATEGORY[c] || c))),
        h('ul', {class: 'iv-list'}, shown.map((q) => ivQuestion(g, q)))]});
  }
  function ivFit(g) {
    const p = g.prep;
    if (!p) return card({body: empty('Not ready yet.', 'Your fit appears once the research is done.')});
    const tone = {high: 'is-critical', medium: 'is-warning', low: 'is-good'};
    return [card({title: 'How you stand', body: h('p', null, p.summary)}),
      p.strengths.length ? card({title: 'Your strengths for this job', body: h('ul', {class: 'road'}, p.strengths.map((x) => h('li', {class: 'road-item'}, h('span', {class: 'gap-dot is-good'}),
        h('div', {class: 'road-main'}, h('b', null, x.point), x.evidence ? h('div', {class: 'small muted'}, x.evidence) : null))))}) : null,
      p.gaps.length ? card({title: 'Your gaps', sub: 'What the job asks for that your resume doesn’t show, worst first', body: h('ul', {class: 'road'}, p.gaps.map((x) => h('li', {class: 'road-item'}, h('span', {class: `gap-dot ${tone[x.severity] || ''}`, title: `${x.severity} priority`}),
        h('div', {class: 'road-main'}, h('b', null, x.title), x.why ? h('div', {class: 'small'}, x.why) : null, x.how_to_close ? h('div', {class: 'small muted'}, `Before the interview: ${x.how_to_close}`) : null))))}) : null,
      p.resume_risks.length ? card({title: 'What they’ll probe on your resume', body: bullets(p.resume_risks)}) : null,
      p.stories.length ? card({title: 'Stories to prepare', sub: 'Real examples from your resume', body: h('ul', {class: 'road'}, p.stories.map((x) => h('li', {class: 'road-item'}, h('span', {class: 'road-dot'}),
        h('div', {class: 'road-main'}, h('b', null, x.title), x.from_resume ? h('div', {class: 'small'}, x.from_resume) : null, x.use_for ? h('div', {class: 'small muted'}, `Use for: ${x.use_for}`) : null))))}) : null];
  }
  const sourceLine = (rows) => (rows || []).filter((s) => safeHref(s.url)).map((s, i) => [i ? ' · ' : '', h('a', {href: s.url, target: '_blank', rel: 'noopener noreferrer'}, s.title, ' ', icon('out'))]);
  function ivCompany(g) {
    const c = g.company;
    if (!c) return card({body: empty('No company research yet.', g.busy ? 'It’s on the way.' : 'Use Try again, or save the job details to run it.')});
    const part = (title, rows) => (rows && rows.length ? card({title, body: bullets(rows)}) : null);
    return [card({title: g.job.company, body: [h('p', null, c.summary), c.tech_stack.length ? h('div', {class: 'prompts', style: {marginTop: '8px'}}, c.tech_stack.map((t) => h('span', {class: 'tag'}, t))) : null]}),
      part('The business', c.business), part('Products', c.products),
      c.recent_news.length ? card({title: 'Recent news', body: h('ul', {class: 'road'}, c.recent_news.map((n) => h('li', {class: 'road-item'}, h('div', {class: 'road-main'},
        safeHref(n.url) ? h('a', {href: n.url, target: '_blank', rel: 'noopener noreferrer'}, h('b', null, n.title), ' ', icon('out')) : h('b', null, n.title),
        n.date ? h('span', {class: 'small muted'}, ` · ${n.date}`) : null, n.why_it_matters ? h('div', {class: 'small'}, n.why_it_matters) : null))))}) : null,
      part('Culture', c.culture), part('How they’re reported to interview', c.interview_process), part('What this role is usually accountable for', c.role_expectations),
      part('Couldn’t confirm', c.uncertain), card({body: h('div', {class: 'small muted'}, 'Sources: ', sourceLine(c.sources))})];
  }
  function ivPeople(g) {
    const people = g.job.interviewers || [];
    if (!people.length) return card({body: [empty('No interviewers added.', 'Add who you’re meeting under Job details, and Polymath tailors the questions to them.')]});
    const key = (p) => [p.name, p.title, p.url, g.job.company].map((x) => String(x || '').trim().toLowerCase()).join('|');
    const label = {confirmed: ['Confirmed', 'status-done'], likely: ['Likely match', 'status-next'], not_found: ['Not confirmed', '']};
    return people.map((p) => {
      const r = (g.people || {})[key(p)];
      if (!r) return card({title: p.name, sub: p.title, body: h('p', {class: 'small muted'}, g.busy ? 'Being researched…' : 'Not researched yet.')});
      const [text, cls] = label[r.match] || label.not_found;
      return card({title: p.name, sub: r.current_role || p.title, actions: [h('span', {class: `tag ${cls}`}, text)], body: r.match === 'not_found'
        ? [h('p', {class: 'small'}, 'Couldn’t confirm which person this is, so nothing about them is used. ', r.note || ''), h('p', {class: 'small muted'}, 'Add a profile link, or paste their bio under Job details.')]
        : [r.match === 'likely' ? h('p', {class: 'small muted'}, 'Probably the right person. Check before relying on it.') : null,
          r.background.length ? [h('h4', {class: 'iv-h'}, 'Background'), bullets(r.background)] : null, r.focus_areas.length ? [h('h4', {class: 'iv-h'}, 'What they work on'), bullets(r.focus_areas)] : null,
          r.likely_focus.length ? [h('h4', {class: 'iv-h'}, 'What they’ll likely care about (inferred)'), bullets(r.likely_focus)] : null,
          r.public_work.length ? [h('h4', {class: 'iv-h'}, 'Their public work'), h('div', {class: 'small'}, sourceLine(r.public_work))] : null,
          h('div', {class: 'small muted', style: {marginTop: '10px'}}, 'Sources: ', sourceLine(r.sources))]});
    });
  }
  function ivAsk(g) {
    const rows = (g.prep || {}).ask_them || [];
    if (!rows.length) return card({body: empty('Not ready yet.', 'These appear once the research is done.')});
    return card({title: 'Questions to ask them', sub: 'Sharp questions show how you think', body: h('ul', {class: 'road'}, rows.map((x) => h('li', {class: 'road-item'}, h('span', {class: 'road-dot'}),
      h('div', {class: 'road-main'}, h('b', null, x.question), x.why ? h('div', {class: 'small'}, x.why) : null, x.for ? h('div', {class: 'small muted'}, `Ask: ${x.for}`) : null))))});
  }
  function ivDetails(g) {
    const key = `iv:${g.id}`;
    if (!app.drafts[key]) app.drafts[key] = JSON.parse(JSON.stringify(g.job));
    const d = app.drafts[key];
    const save = () => {
      const missing = ivMissing(d);
      if (missing.length) { toast(`Add ${missing.join(', ')}.`, true); return; }
      goalAct(g, 'interview', d, 'iv-save').then((out) => { if (out) { delete app.drafts[key]; ivUI.tab[g.id] = 'questions'; redraw(); } });
    };
    return [...interviewForm(d, key), h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-primary', type: 'button', disabled: !!g.busy || busy('iv-save') || !canStudy(), onclick: save}, 'Save and update my prep'),
      h('span', {class: 'form-note'}, 'Only what changed is researched again. Your practice is kept.')),
    h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-small', type: 'button', disabled: !!g.busy, onclick: () => {
      if (window.confirm('Remove this interview from your goals?')) goalAct(g, 'archive').then((o) => { if (o) location.hash = '#/goals'; });
    }}, 'Remove this interview'))];
  }
  function pageInterview(g) {
    pollGoal();
    const tab = ivUI.tab[g.id] || (g.prep ? 'questions' : 'company');
    const total = g.total || 0;
    const at = g.prep ? 4 : g.busy && g.busy.stage === 'planning' ? 3 : 1;
    const steps = h('ol', {class: 'goal-steps', 'aria-label': 'Where this prep is'}, ['Details', 'Research', 'Your fit', 'Questions', 'Practice'].map((name, i) =>
      h('li', {class: i < at ? 'is-done' : i === at ? 'is-now' : '', 'aria-current': i === at ? 'step' : null}, name)));
    const b = g.busy;
    const status = [b ? card({body: [h('div', {class: 'qwriting'}, h('span', {class: 'spinner', 'aria-hidden': 'true'}), h('span', {'data-goal-clock': ''}, `${b.label}… ${b.seconds || 0} s`)),
      h('div', {class: 'writing-bar', 'aria-hidden': 'true'}, h('span', {'data-goal-bar': '', style: {width: `${goalPct(b)}%`}})),
      h('div', {class: 'small muted', style: {marginTop: '6px'}}, 'Usually 3 to 8 minutes in all. You can leave this page and keep studying; it carries on.')]}) : null,
    g.error ? h('div', {class: 'callout'}, icon('alert', 'is-warning'), h('div', null, h('b', null, 'That step didn’t finish.'), h('div', {class: 'small muted'}, g.error.text),
      h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-small', type: 'button', disabled: busy('goal:retry') || !canStudy(), onclick: () => goalAct(g, 'retry')}, 'Try again')))) : null,
    ...(g.warnings || []).map((w) => h('div', {class: 'callout'}, icon('alert', 'is-warning'), h('div', null, h('b', null, 'Some research is missing. '), h('span', {class: 'small'}, w),
      h('div', {class: 'form-actions'}, h('button', {class: 'btn btn-small', type: 'button', disabled: !!b || !canStudy(), onclick: () => goalAct(g, 'retry')}, 'Try again')))))];
    const when = g.target_date ? (isNum(g.days_left) && g.days_left >= 0 ? `${fmtDay(localDay(g.target_date))} · ${g.days_left === 0 ? 'today' : plural(g.days_left, 'day') + ' to go'}` : fmtDay(localDay(g.target_date))) : 'No date set';
    const summary = g.prep ? card({body: [h('div', {class: 'kpis'}, miniStat('Readiness', pct(g.progress), 'by how likely each question is'),
      miniStat('Questions ready', `${g.done_count}/${total}`, 'scored 4 or more'), miniStat('Interview', when.split(' · ')[0], when.split(' · ')[1] || ''),
      miniStat('Interviewers', String((g.job.interviewers || []).length), 'researched where confirmed')),
    h('div', {style: {marginTop: '12px'}}, goalProgress(g))]}) : null;
    const tabs = [['questions', `Likely questions${total ? ` (${total})` : ''}`], ['fit', 'Your fit'], ['company', 'Company'], ['people', 'Interviewers'], ['ask', 'Questions to ask'], ['details', 'Job details']];
    const body = {questions: ivQuestions, fit: ivFit, company: ivCompany, people: ivPeople, ask: ivAsk, details: ivDetails}[tab](g);
    return h('div', {class: 'stack'}, pageHead(g.title, null, null, [h('a', {href: '#/goals'}, 'Goals')]), steps, status, summary,
      h('div', {class: 'prompts iv-tabs', role: 'group', 'aria-label': 'Sections'}, tabs.map(([id, label]) => h('button', {class: 'chip', type: 'button', 'aria-pressed': String(tab === id),
        'data-focus': `ivtab:${id}`, onclick: () => { ivUI.tab[g.id] = id; render(); }}, label))), body);
  }

  // --- lessons: click a term, read it in depth right here, hide it again ---------------------------
  const learn = {open: {}, cache: {}, scopes: {}, polling: new Set()};
  const sameTerm = (a, b) => String(a || '').trim().toLowerCase() === String(b || '').trim().toLowerCase();
  const LEARN_TEXT = '.qstem, .opt-text, .teach p, .primer p';
  const LEARN_SKIP = 'code, pre, button, a, input, textarea, .lesson, .sr-only';
  function linkTerms(root, terms, scope) {
    const list = [...new Set((terms || []).map((t) => String(t).trim()).filter((t) => t.length > 1))].sort((a, b) => b.length - a.length);
    const found = new Set();
    if (!list.length) return found;
    const re = new RegExp(`(?<![\\w-])(${list.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})(?![\\w-])`, 'gi');
    const nodes = [];
    root.querySelectorAll(LEARN_TEXT).forEach((box) => {
      const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) if (!walker.currentNode.parentElement.closest(LEARN_SKIP)) nodes.push(walker.currentNode);
    });
    for (const node of nodes) {
      const text = node.nodeValue;
      const parts = [];
      let last = 0, m;
      re.lastIndex = 0;
      while ((m = re.exec(text))) {
        const key = m[1].toLowerCase();
        if (found.has(key)) continue; // once per question: a page of links is harder to read than none
        found.add(key);
        if (m.index > last) parts.push(text.slice(last, m.index));
        const word = m[1];
        parts.push(h('button', {class: 'term', type: 'button', 'data-term': key, 'data-focus': `term:${scope}:${key}`,
          'aria-expanded': String(!!learn.open[scope] && sameTerm(learn.open[scope].term, word)), title: `Read about ${word}`,
          onclick: () => openLesson(scope, word)}, word));
        last = m.index + word.length;
      }
      if (!parts.length) continue;
      if (last < text.length) parts.push(text.slice(last));
      node.replaceWith(...parts);
    }
    return found;
  }
  function attachLearn(root, scope, terms, ctx, mainTerm) {
    if (app.snapshot || !canStudy()) return root;
    learn.scopes[scope] = ctx;
    root.setAttribute('data-learn-scope', scope);
    const found = linkTerms(root, terms, scope);
    const extra = [...new Set([mainTerm, ...(terms || [])].filter(Boolean))].filter((t) => !found.has(String(t).toLowerCase()));
    const row = h('div', {class: 'learn-row'}, extra.length ? h('span', {class: 'small muted'}, 'Read in depth:') : null,
      extra.map((t) => h('button', {class: 'chip', type: 'button', 'data-focus': `term:${scope}:${t}`,
        'aria-pressed': String(!!learn.open[scope] && sameTerm(learn.open[scope].term, t)), onclick: () => openLesson(scope, t)}, t)),
      h('span', {class: 'small muted'}, extra.length ? (found.size ? 'or click an underlined term, or select any words' : 'or select any words')
        : 'Click an underlined term, or select any words, to read about it here.'));
    root.append(row);
    const o = learn.open[scope];
    if (o) {
      const panel = lessonPanel(scope, o);
      const spot = root.querySelector(`.term[data-term="${CSS.escape(String(o.term).toLowerCase())}"]`);
      const block = spot && spot.closest('li, p, .qstem');
      if (block && block.tagName === 'LI') block.append(panel);
      else if (block) block.after(panel);
      else row.after(panel);
    }
    return root;
  }
  async function openLesson(scope, term, opts = {}) {
    const ctx = learn.scopes[scope] || {};
    const now = learn.open[scope];
    if (now && !opts.push && !opts.retry && sameTerm(now.term, term)) { delete learn.open[scope]; redraw(); return; } // the same word again hides it
    const stack = opts.push && now ? now.stack.slice() : opts.retry && now ? now.stack.slice(0, -1) : [];
    const entry = {term: opts.push && now ? now.term : term, stack, pending: term};
    learn.open[scope] = entry;
    redraw();
    try {
      const out = await post('api/learn', {term, track: ctx.track, concept: ctx.concept, retry: !!opts.retry});
      learn.cache[out.key] = out;
      if (learn.open[scope] !== entry) return;
      entry.stack = [...stack, out.key];
      delete entry.pending;
      redraw();
      pollLesson(out.key);
    } catch (err) {
      if (learn.open[scope] === entry) { entry.error = err.message || String(err); delete entry.pending; redraw(); }
    }
  }
  function pollLesson(key) {
    const e = learn.cache[key];
    if (!e || e.state !== 'writing' || learn.polling.has(key)) return;
    learn.polling.add(key);
    setTimeout(async () => {
      learn.polling.delete(key);
      let fresh = null;
      try { fresh = await getJSON(`api/learn/${key}`); } catch (err) { /* try again */ }
      if (fresh) {
        const changed = fresh.state !== learn.cache[key].state;
        learn.cache[key] = fresh;
        if (changed) redraw();
        else {
          view.querySelectorAll(`[data-lesson-clock="${key}"]`).forEach((el) => { el.textContent = `Reading the official documentation… ${fresh.seconds || 0} s`; });
          view.querySelectorAll(`[data-lesson-bar="${key}"]`).forEach((el) => { el.style.width = `${Math.min(92, Math.round(100 * (1 - Math.exp(-(fresh.seconds || 0) / 45))))}%`; });
        }
      }
      pollLesson(key);
    }, 1500);
  }
  function lessonBody(scope, e) {
    const l = e.lesson;
    const list = (title, rows, cls) => (rows && rows.length ? h('div', {class: `lesson-box ${cls || ''}`}, h('h4', null, title), h('ul', null, rows.map((x) => h('li', null, inline(x))))) : null);
    const ex = l.example || {};
    return [l.summary ? h('p', {class: 'lesson-summary'}, inline(l.summary)) : null,
      (l.sections || []).map((s) => h('div', {class: 'lesson-section'}, s.heading ? h('h4', null, s.heading) : null, markdown(s.body))),
      ex.walkthrough || ex.code ? h('div', {class: 'lesson-section'}, h('h4', null, ex.title || 'Worked example'), ex.code ? h('pre', {class: 'qcode'}, ex.code) : null, ex.walkthrough ? markdown(ex.walkthrough) : null) : null,
      list('Common mistakes', l.pitfalls, 'is-warn'), list('How exams test it', l.exam_tips), list('Couldn’t confirm', l.uncertain, 'is-warn'),
      (l.related || []).length ? h('div', {class: 'lesson-section'}, h('h4', null, 'Read next'), h('div', {class: 'prompts'}, l.related.map((r) => h('button', {class: 'chip', type: 'button',
        onclick: () => openLesson(scope, r, {push: true})}, r)))) : null,
      h('div', {class: 'lesson-sources small muted'}, 'Written by AI from these pages; the official docs are the final word: ',
        (l.sources || []).filter((s) => safeHref(s.url)).map((s, i) => [i ? ' · ' : '', h('a', {href: s.url, target: '_blank', rel: 'noopener noreferrer'}, s.title, ' ', icon('out'))]))];
  }
  function lessonPanel(scope, o) {
    const key = o.stack[o.stack.length - 1];
    const e = key ? learn.cache[key] : null;
    const title = o.pending || (e && e.lesson && e.lesson.title) || (e && e.term) || o.term;
    const hide = () => { delete learn.open[scope]; redraw(); };
    const back = o.stack.length > 1 && !o.pending ? h('button', {class: 'btn btn-small', type: 'button', onclick: () => { o.stack.pop(); redraw(); pollLesson(o.stack[o.stack.length - 1]); }}, 'Back') : null;
    let body;
    if (o.error) body = h('p', {class: 'small'}, o.error);
    else if (o.pending || !e || e.state === 'writing') {
      const secs = (e && e.seconds) || 0;
      body = [h('div', {class: 'qwriting'}, h('span', {class: 'spinner', 'aria-hidden': 'true'}), h('span', {'data-lesson-clock': key || ''}, `Reading the official documentation… ${secs} s`)),
        h('div', {class: 'writing-bar', 'aria-hidden': 'true'}, h('span', {'data-lesson-bar': key || '', style: {width: `${Math.min(92, Math.round(100 * (1 - Math.exp(-secs / 45))))}%`}})),
        h('div', {class: 'small muted', style: {marginTop: '6px'}}, 'A new lesson usually takes 1 to 3 minutes. Once written it opens instantly. You can hide this and keep studying.')];
    } else if (e.state === 'failed') {
      body = [h('p', {class: 'small'}, e.error), h('button', {class: 'btn btn-small', type: 'button', onclick: () => openLesson(scope, e.term, {retry: true})}, 'Try again')];
    } else body = lessonBody(scope, e);
    return h('div', {class: 'lesson', role: 'region', 'aria-label': `Lesson: ${title}`},
      h('div', {class: 'lesson-head'}, back, h('h3', null, title), h('button', {class: 'btn btn-small', type: 'button', 'data-focus': `lesson-hide:${scope}`, onclick: hide}, 'Hide')), body);
  }
  // Any words, not only the underlined ones: select them on an answered question or in a lesson.
  const learnPop = h('button', {class: 'learn-pop', type: 'button', hidden: true});
  document.body.append(learnPop);
  let popChoice = null;
  learnPop.addEventListener('pointerdown', (e) => e.preventDefault()); // keep the selection while it is clicked
  learnPop.addEventListener('click', () => {
    learnPop.hidden = true;
    if (popChoice) openLesson(popChoice.scope, popChoice.text);
    window.getSelection()?.removeAllRanges();
  });
  document.addEventListener('pointerup', () => setTimeout(() => {
    const sel = window.getSelection();
    const text = sel && !sel.isCollapsed ? sel.toString().replace(/\s+/g, ' ').trim() : '';
    const anchor = sel && sel.anchorNode ? (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement) : null;
    const box = anchor && anchor.closest('[data-learn-scope]');
    if (!box || text.length < 3 || text.length > 60 || anchor.closest('input, textarea')) { learnPop.hidden = true; return; }
    popChoice = {scope: box.getAttribute('data-learn-scope'), text};
    const r = sel.getRangeAt(0).getBoundingClientRect();
    learnPop.textContent = `Read about “${text.length > 32 ? `${text.slice(0, 30)}…` : text}”`;
    learnPop.hidden = false;
    learnPop.style.left = `${Math.max(8, Math.min(window.innerWidth - learnPop.offsetWidth - 8, r.left))}px`;
    learnPop.style.top = `${r.bottom + 6 + learnPop.offsetHeight > window.innerHeight ? r.top - learnPop.offsetHeight - 6 : r.bottom + 6}px`;
  }, 0));
  window.addEventListener('scroll', () => { learnPop.hidden = true; }, {passive: true});

  // --- start -------------------------------------------------------------------------------------
  function start() {
    applyTheme(storedTheme());
    window.addEventListener('hashchange', () => render(true));
    const embedded = document.getElementById('polymath-data');
    if (embedded) {
      app.snapshot = true;
      try { app.state = JSON.parse(embedded.textContent); } catch (err) { app.state = null; }
      render();
      return;
    }
    refresh();
    startClock();
    setInterval(poll, 4000);
    setInterval(updateLive, 15000);
    checkModel();
    setInterval(checkModel, 15000);
    window.addEventListener('pagehide', () => { if (onSession()) saveScreen(); });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) { if (onSession()) saveScreen(); } else { poll(); checkModel(); }
    });
  }
  start();
});
