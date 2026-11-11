/**
 * A new record on the phone, run for real: app.js in a sandbox with a
 * recording DOM and a memory store (the same approach as edit_check.js).
 *
 * Checked:
 *   * a new Fault record does not show the PM hours block; a new PM record does
 *   * start, end and hours left in the form are not saved with a fault
 *   * a PM record still saves its hours
 *   * a record is not saved without a project when the phone knows the projects
 *   * a phone that knows one project picks it by itself
 *   * a phone that has never synced (no projects) can still save
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = path.join(__dirname, '..', 'backend', 'static', 'js', 'app.js');
let failures = 0;
function check(cond, msg) {
  console.log((cond ? '   ok    ' : '   FAIL  ') + msg);
  if (!cond) failures++;
}

function element() {
  return {
    value: '', textContent: '', innerHTML: '', disabled: false, style: {},
    dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
    querySelectorAll: () => [], appendChild() {}, addEventListener() {},
    options: [], selectedOptions: [{ textContent: 'Plant A' }],
    setAttribute() {}, remove() {}, focus() {},
  };
}

/** A <select> that counts its options the way a browser does: setting
    innerHTML leaves the one placeholder, appendChild adds one. Choosing a
    value that is not among them leaves it empty. */
function selectElement() {
  const e = element();
  let html = '', val = '';
  e.options = [];
  Object.defineProperty(e, 'innerHTML', {
    get: () => html,
    set(v) { html = v; e.options = v ? [{ value: '' }] : []; val = ''; },
  });
  Object.defineProperty(e, 'value', {
    get: () => val,
    set(v) { val = e.options.some(o => String(o.value) === String(v)) ? String(v) : ''; },
  });
  e.appendChild = o => { e.options.push(o); };
  return e;
}

function sandbox(projects, lastProject) {
  const els = { 'f-proj': selectElement() };
  const el = id => (els[id] || (els[id] = element()));
  const store = { entries: {}, meta: { projects } };
  const DB = new Proxy({
    async getMeta(k, d) { return k in store.meta ? store.meta[k] : d; },
    async setMeta(k, v) { store.meta[k] = v; },
    async saveEntry(e) { store.entries[e.id] = JSON.parse(JSON.stringify(e)); },
    async getEntry(id) { return store.entries[id] || null; },
    async getAllEntries() { return Object.values(store.entries); },
  }, {
    // every other store call: nothing there
    get(t, k) { return k in t ? t[k] : (async () => (/^get(All|Pending)/.test(k) ? [] : null)); },
  });
  const ls = { username: 'tech1', user_id: 'u-tech1', device_id: 'phone-1', access_token: 'tok' };
  if (lastProject) ls.last_project_id = lastProject;
  let n = 0;
  const ctx = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { onLine: false, serviceWorker: { addEventListener() {} } },
    localStorage: {
      _d: ls,
      getItem(k) { return this._d[k] === undefined ? null : this._d[k]; },
      setItem(k, v) { this._d[k] = v; }, removeItem(k) { delete this._d[k]; },
    },
    document: {
      addEventListener() {}, getElementById: el, querySelectorAll: () => [],
      createElement: () => element(), body: { classList: { toggle() {} } },
    },
    alert() {}, confirm: () => true,
    fetch: () => Promise.reject(new Error('no network here')),
    indexedDB: {}, crypto: { randomUUID: () => 'uuid-' + (++n) },
    scrollTo() {}, DB, API: {},
  };
  ctx.window = ctx; ctx.self = ctx;
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(APP, 'utf8'), ctx, { filename: 'app.js' });
  ctx.App = vm.runInContext('App', ctx);
  ctx._els = els; ctx._store = store;
  return ctx;
}

/** Open the form for `kind`, fill what a thumb fills, Save. Returns the
    records saved by this call. */
async function write(ctx, kind, values) {
  const before = new Set(Object.keys(ctx._store.entries));
  await ctx.App.goCreate(kind);
  const base = { 'f-date': '2026-11-10', 'f-desc': 'Fan replaced', 'f-block': '5',
                 'f-blocks': '5', 'f-status': 'done' };
  for (const [id, v] of Object.entries(Object.assign(base, values || {}))) {
    ctx.document.getElementById(id).value = v;
  }
  await ctx.App.saveEntry();
  return Object.values(ctx._store.entries).filter(e => !before.has(e.id));
}

const TWO = [{ id: 7, name: 'Plant A', num_blocks: 16 }, { id: 8, name: 'Plant B', num_blocks: 8 }];
const ONE = [TWO[0]];

(async () => {
  // ══ 1. the PM hours block belongs to PM records only ══════════════════════
  let ctx = sandbox(TWO, '7');
  await ctx.App.goCreate('fault');
  check(ctx._els['f-pm-group'].style.display === 'none',
        'a new Fault record does not show the PM hours block');
  await ctx.App.goCreate('maintenance');
  check(ctx._els['f-pm-group'].style.display !== 'none',
        'a new PM record shows the PM hours block');

  // ══ 2. hours left in the form are not saved with a fault ══════════════════
  let rows = await write(ctx, 'fault', { 'f-start': '09:00', 'f-end': '13:00', 'f-hours': '4' });
  check(rows.length === 1, 'the fault record is saved');
  check(rows[0] && rows[0].hours === null && rows[0].time_from === '' && rows[0].time_to === '',
        'a fault record carries no hours, start or end');
  check(rows[0] && rows[0].project_id === 7, 'and it carries the project');

  rows = await write(ctx, 'maintenance', { 'f-start': '09:00', 'f-end': '13:00', 'f-hours': '4' });
  check(rows.length === 1 && rows[0].hours === 4 && rows[0].time_from === '09:00',
        'a PM record still saves its hours');

  // ══ 3. the project is asked for ═══════════════════════════════════════════
  ctx = sandbox(TWO, null);
  rows = await write(ctx, 'fault');
  check(rows.length === 0, 'with two projects known and none chosen, nothing is saved');
  check(/project/i.test(ctx._els['create-error'].textContent || ctx._els['create-error'].innerHTML || ''),
        'and the form says to choose the project');
  rows = await write(ctx, 'fault', { 'f-proj': '8' });
  check(rows.length === 1 && rows[0].project_id === 8, 'once chosen, the record is saved under it');

  ctx = sandbox(ONE, null);
  rows = await write(ctx, 'fault');
  check(rows.length === 1 && rows[0].project_id === 7,
        'a phone that knows one project picks it by itself');

  ctx = sandbox([], null);
  rows = await write(ctx, 'fault');
  check(rows.length === 1 && rows[0].project_id === null,
        'a phone that has never synced has no project to offer and still saves');

  console.log(failures ? 'RESULT FAIL (' + failures + ')' : 'RESULT PASS');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); console.log('RESULT FAIL'); process.exit(1); });
