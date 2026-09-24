/**
 * The phone's action-item screen, run for real: app.js is loaded into a
 * sandbox with a recording DOM, a memory IndexedDB and a fake server, and an
 * action item the office assigned is opened, settled and sent the way a thumb
 * does it.
 *
 * Checked:
 *   * an action item is shown on the Tasks tab NEXT TO the jobs but visibly
 *     apart from them — its own card class, its own chip, and "No block"
 *     said out loud. Mistaking one for plant work is exactly what would put
 *     an office errand in front of the customer;
 *   * opening one shows the topic, the description, what to do, who it is
 *     from and when it is due;
 *   * marking it done writes the note onto THAT item (no second record, and
 *     nothing is written into the work-record store at all) and sends only
 *     the completion — the topic, the date and the assignment stay the
 *     office's;
 *   * it carries the phone's own stamp, so a late upload cannot beat an
 *     office correction;
 *   * it all works with no network, and the item is kept to send;
 *   * an item still waiting to send is never overwritten by the server's
 *     copy, and one the office cancelled goes from the phone;
 *   * Today / Week / My open behave, and an item with no target date is open
 *     work rather than today's work.
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

function today(offset = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// what the office published and the server handed to this phone
const GAS = {
  uuid: 'act-gas', project_id: 7, seq: 8, topic: 'Gas sensors',
  description: 'How often need to replace? Every 2 years as per UM?',
  todo: 'Confirm frequency of replacement\nNeed code for sensor alone',
  due_date: today(), assigned_to: 'u-tech1', assigned_name: 'tech1',
  assigned_by: 'office', status: 'open', done_at: '', done_note: '',
  done_by: '', updated_at: '2026-09-20T08:00:00.000Z', deleted_at: null,
  sync_status: 'synced',
};
const HVAC = Object.assign({}, GAS, {
  uuid: 'act-hvac', seq: 5, topic: 'HVAC', description: 'Need BOM',
  todo: 'Get the BOM\nPlace order', due_date: today(3),
});
const CERT = Object.assign({}, GAS, {
  uuid: 'act-cert', seq: 12, topic: 'Electrical safety certificate',
  description: 'Group IV as minimum', todo: 'Updated certificates',
  due_date: '',                        // nobody has dated it
});
// one real job, so the two kinds of card can be told apart on one screen
const JOB = {
  id: 'job-1', project_id: 7, category: 'fault', log_date: today(),
  description: 'Coolant low', fault_name: 'Antifreeze Low Level',
  status: 'open', plant_block: 5, node_lc: 'LC1', node_device: 'BESS 3',
  ptw_no: '', internal_note: '', hours: null, time_from: '', time_to: '',
  spare_parts: '', site_location: '', equipment_serial: '', sap_ticket: '',
  availability_impact: 'none', assigned_to: 'u-tech1', assigned_name: 'tech1',
  assigned_by: 'office', due_date: today(), sync_status: 'synced', version: 3,
  created_at: '2026-09-20T08:00:00.000Z', updated_at: '2026-09-20T08:00:00.000Z',
  deleted_at: null, tags: [], image_ids: [],
};

function element() {
  return {
    value: '', textContent: '', innerHTML: '', disabled: false, style: {},
    dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
    querySelectorAll: () => [], appendChild() {}, addEventListener() {},
  };
}

function sandbox(server, entries, actions) {
  const els = {};
  const el = id => (els[id] || (els[id] = element()));
  const store = { entries: {}, meta: {}, actions: {} };
  for (const e of entries) store.entries[e.id] = JSON.parse(JSON.stringify(e));
  for (const a of actions) store.actions[a.uuid] = JSON.parse(JSON.stringify(a));
  const sortActions = all => all.sort(
    (a, b) => (a.due_date ? 0 : 1) - (b.due_date ? 0 : 1)
              || String(a.due_date || '').localeCompare(String(b.due_date || ''))
              || (a.seq || 0) - (b.seq || 0));
  const DB = {
    async getMeta(k, d) { return k in store.meta ? store.meta[k] : d; },
    async setMeta(k, v) { store.meta[k] = v; },
    async saveEntry(e) { store.entries[e.id] = e; },
    async getEntry(id) { return store.entries[id] || null; },
    async getAllEntries() { return Object.values(store.entries); },
    async getPendingEntries() {
      return Object.values(store.entries)
        .filter(e => ['local', 'pending'].includes(e.sync_status));
    },
    async markSynced(id, v) {
      const e = store.entries[id];
      if (e) { e.sync_status = 'synced'; if (v != null) e.version = v; }
    },
    async deleteEntry(id) { delete store.entries[id]; },
    async getImagesForEntry() { return []; },
    async getPendingImages() { return []; },
    async deleteImagesForEntry() {},
    async getAllFieldEvents() { return []; },
    async getPendingFieldEvents() { return []; },
    async getPendingWriteoffs() { return []; },
    async getAllChecklists() { return []; },
    async getPendingChecklists() { return []; },
    async getChecklist() { return null; },
    async saveChecklist() {}, async updateChecklist() {},
    async saveAction(a) { store.actions[a.uuid] = a; },
    async getAction(u) { return store.actions[u] || null; },
    async getAllActions() { return sortActions(Object.values(store.actions)); },
    async getPendingActions() {
      return sortActions(Object.values(store.actions))
        .filter(a => a.sync_status && a.sync_status !== 'synced');
    },
    async updateAction(u, patch, expect) {
      const rec = store.actions[u];
      if (!rec) return null;
      if (expect !== undefined && expect !== null
          && String(rec.updated_at || '') !== String(expect)) return rec;
      store.actions[u] = Object.assign({}, rec, patch);
      return store.actions[u];
    },
  };
  const ctxObj = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { onLine: true, serviceWorker: { addEventListener() {} } },
    localStorage: {
      _d: { username: 'tech1', user_id: 'u-tech1', last_project_id: '7',
            device_id: 'phone-1', access_token: 'tok' },
      getItem(k) { return this._d[k] === undefined ? null : this._d[k]; },
      setItem(k, v) { this._d[k] = v; }, removeItem(k) { delete this._d[k]; },
    },
    document: {
      addEventListener() {}, getElementById: el, querySelectorAll: () => [],
      createElement: () => element(), body: { classList: { toggle() {} } },
    },
    alert() {}, confirm: () => true,
    fetch: () => Promise.reject(new Error('no network here')),
    indexedDB: {}, crypto: { randomUUID: () => 'uuid-' + Math.random() },
    scrollTo() {}, DB, API: Object.assign({}, server),
  };
  ctxObj.window = ctxObj;
  ctxObj.self = ctxObj;
  vm.createContext(ctxObj);
  vm.runInContext(fs.readFileSync(APP, 'utf8'), ctxObj, { filename: 'app.js' });
  ctxObj.App = vm.runInContext('App', ctxObj);
  ctxObj._els = els;
  ctxObj._store = store;
  return ctxObj;
}

(async () => {
  const doneCalls = [];
  // The server keeps what the phone sent it, the way the real one does: the
  // assigned list a refresh reads back is the list as it stands now, not a
  // frozen copy that would look like the office had undone the completion.
  const onServer = {};
  for (const a of [GAS, HVAC, CERT]) onServer[a.uuid] = Object.assign({}, a);
  const server = {
    async getProjects() { return [{ id: 7, name: 'TK', num_blocks: 16 }]; },
    async pullDelta() { return { changes: [], cursor: '1', has_more: false }; },
    async pushChanges(changes) {
      return { results: changes.map(c => ({ id: c.id, outcome: 'applied',
                                            server_version: (c.version || 1) + 1 })) };
    },
    async uploadImage() { return {}; },
    // the server only ever sends this phone what is assigned to it
    async getActionItems() {
      return { items: Object.values(onServer), cursor: 'act-cert',
               has_more: false };
    },
    async postActionDone(uuid, payload) {
      doneCalls.push({ uuid, payload });
      onServer[uuid] = Object.assign({}, onServer[uuid], payload);
      return onServer[uuid];
    },
  };

  const ctx = sandbox(server, [JOB], [GAS, HVAC, CERT]);
  const App = ctx.App;

  // ── the list ─────────────────────────────────────────────────────────────
  await App.goTasks();
  let html = ctx._els['tasks-body'].innerHTML;
  check(html.includes('Gas sensors') && html.includes('Action item'),
        'the action item is on the Tasks tab, with its own chip');
  check(html.includes('tcard action'),
        'drawn as its own kind of card, not as a job card');
  check(html.includes('No block — office action'),
        'and it says out loud that it is not plant work');
  check(html.includes('Confirm frequency of replacement')
        && !html.includes('Need code for sensor alone'),
        'the card shows the first line of the to-do, not the whole cell');
  check(html.includes("App.openAction('act-gas')"),
        'tapping it opens the action, not a work record');
  check(html.includes('Antifreeze Low Level') && html.includes('Block 5'),
        'and the technician\'s real job is still there beside it');
  check(html.indexOf('job-1') < html.indexOf('act-gas'),
        'plant work comes first: an errand does not push a fault down the list');

  await App.taskSeg('week');
  html = ctx._els['tasks-body'].innerHTML;
  check(html.includes('act-hvac'), 'Week reaches an item due in three days');
  check(!html.includes('act-cert'),
        'an item nobody has dated is not "this week"');
  await App.taskSeg('today');
  html = ctx._els['tasks-body'].innerHTML;
  check(html.includes('act-gas') && !html.includes('act-hvac'),
        'Today is only today');
  await App.taskSeg('open');
  html = ctx._els['tasks-body'].innerHTML;
  check(html.includes('act-gas') && html.includes('act-hvac')
        && html.includes('act-cert'),
        'My open lists every open item, dated or not');

  // ── one item, opened and settled ─────────────────────────────────────────
  await App.openAction('act-gas');
  check(ctx._els['act-topic'].textContent === 'Gas sensors',
        'the screen shows the topic: ' + ctx._els['act-topic'].textContent);
  check(ctx._els['act-from'].textContent.includes('No block — office action')
        && ctx._els['act-from'].textContent.includes('from office')
        && ctx._els['act-from'].textContent.includes('due ' + today()),
        'where it is from and when it is due: ' + ctx._els['act-from'].textContent);
  check(ctx._els['act-ask'].textContent.includes('Every 2 years as per UM')
        && ctx._els['act-ask'].textContent.includes('Need code for sensor alone'),
        'and the whole description and to-do, line breaks and all');
  check(ctx._els['act-note'].value === '',
        'the note starts empty — the office\'s ask is not an answer');

  ctx._els['act-note'].value = 'Honeywell: every 2 years, code 30110';
  await App.saveAction();
  let saved = ctx._store.actions['act-gas'];
  // Save sends in the background, like the job screen does; let that finish
  // before the next tap, so what follows is measured and not a race.
  const settle = () => new Promise(r => setTimeout(r, 0));
  check(saved.status === 'open' && saved.done_note.includes('code 30110'),
        'Save keeps the note and leaves the item open');

  await settle();
  await App.completeAction();
  await settle();
  const done = ctx._store.actions['act-gas'];
  check(Object.keys(ctx._store.actions).length === 3,
        'no second item is created: ' + Object.keys(ctx._store.actions).join(', '));
  check(done.status === 'done' && done.done_at && done.done_by === 'tech1',
        'the item itself is closed, with who and when');
  check(Object.keys(ctx._store.entries).length === 1
        && !ctx._store.entries['act-gas'],
        'and NOTHING was written into the work records — an office errand '
        + 'never becomes plant work');

  const sent = doneCalls.filter(c => c.uuid === 'act-gas');
  check(sent.length >= 1 && sent[sent.length - 1].payload.status === 'done'
        && sent[sent.length - 1].payload.done_note.includes('code 30110'),
        'the completion is sent for that very item');
  check(!('topic' in sent[0].payload) && !('due_date' in sent[0].payload)
        && !('assigned_to' in sent[0].payload),
        'and only the completion: the topic, the date and the assignment '
        + "stay the office's");
  check(sent[sent.length - 1].payload.updated_at
        && sent[sent.length - 1].payload.updated_at !== '',
        "carrying the phone's own stamp, so a late upload cannot beat an "
        + 'office correction');
  check(ctx._store.actions['act-gas'].sync_status === 'synced',
        'and once it is away it is marked sent');

  // ── no signal ────────────────────────────────────────────────────────────
  const off = sandbox(server, [], [GAS]);
  off.navigator.onLine = false;
  await off.App.goTasks();
  await off.App.openAction('act-gas');
  off._els['act-note'].value = 'settled in a container with no signal';
  await off.App.completeAction();
  const kept = off._store.actions['act-gas'];
  check(kept.status === 'done' && kept.sync_status === 'local'
        && kept.done_note.includes('no signal'),
        'work done offline is kept on the phone, marked to send');
  await off.App.taskSeg('open');
  check(!off._els['tasks-body'].innerHTML.includes('act-gas'),
        'and it leaves the open list at once');

  // ── an unsent answer is never overwritten by the server's copy ───────────
  const race = sandbox(server, [], [GAS]);
  race.navigator.onLine = false;
  await race.App.openAction('act-gas');
  race._els['act-note'].value = 'the only copy of this sentence';
  await race.App.completeAction();
  race.navigator.onLine = true;
  race.API.postActionDone = async () => { throw new Error('502 Bad Gateway'); };
  const r = await race.App._syncActions();
  check(r.error && race._store.actions['act-gas'].done_note
                     .includes('the only copy'),
        'the upload fails and the work is still here: ' + r.error);
  check(race._store.actions['act-gas'].sync_status === 'error'
        && race._store.actions['act-gas'].last_error,
        'flagged as not sent, with the reason: '
        + race._store.actions['act-gas'].last_error);

  // ── the office cancels one ───────────────────────────────────────────────
  const gone = sandbox({
    ...server,
    async getActionItems() {
      return { items: [HVAC], cursor: 'act-hvac', has_more: false };
    },
  }, [], [GAS, HVAC]);
  await gone.App._syncActions();
  check(gone._store.actions['act-gas'].deleted_at,
        'an item the office took back goes from the phone');
  check(!gone._store.actions['act-hvac'].deleted_at,
        'and the rest of the list is untouched');
  await gone.App.taskSeg('open');
  check(!gone._els['tasks-body'].innerHTML.includes('act-gas'),
        'so it is off the Tasks tab too');

  console.log(failures ? 'RESULT FAIL (' + failures + ' check(s))' : 'RESULT PASS');
  process.exit(0);
})();
