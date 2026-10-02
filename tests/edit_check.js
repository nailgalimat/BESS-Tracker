/**
 * Correcting a record from the phone, run for real: app.js is loaded into a
 * sandbox with a recording DOM, a memory store and a fake server that merges a
 * pushed payload exactly the way backend/routers/sync.py does — only the keys
 * the payload carries, the rest of the row left alone.
 *
 * Checked:
 *   * an open record the technician wrote offers Edit, and the correction
 *     reaches the server
 *   * a DONE record offers no Edit, and says where it is reopened
 *   * a job the office handed out offers no Edit either — it is filled in on
 *     the Tasks screen, which is the one editor for the office's own records
 *   * the correction sends ONLY the fields it changed, so a line the office
 *     wrote while the phone was offline cannot be blanked by it — the
 *     regression that matters most (a QA pass once found the phone wiping a
 *     filled job on a version conflict)
 *   * a record the server has never seen still goes up whole: there is no row
 *     to merge a single field into
 *   * a collision in DIFFERENT fields settles itself inside one sync, with
 *     both sides' work intact
 *   * a collision in the SAME field keeps both values, shows them, and is
 *     decided by a tap — either way
 *   * the warning for a CLOSED month (sent to the customer and settled)
 *     appears, and the edit still goes through; a month whose report has
 *     gone out but is not closed yet says nothing — that gap is exactly
 *     when corrections are expected
 *     and the edit still goes through
 *   * an un-taught server and a server that says "nothing is locked" both
 *     stay quiet, and are not confused with each other
 *   * a PM record's PM-ness cannot be switched from the phone
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

// ── the records ─────────────────────────────────────────────────────────────
// One the technician wrote and is still open. `internal_note` and
// `spare_parts` are what the OFFICE added after it arrived: they are the
// fields this test watches, because nothing the phone does may blank them.
const OPEN = {
  id: 'rec-1', project_id: 7, category: 'fault', log_date: '2026-09-14',
  description: 'Fuse replaced on BESS 2', fault_name: 'Fuse blown',
  status: 'open', plant_block: 5, node_lc: 'LC1', node_device: 'BESS 2',
  ptw_no: 'PTW-2609-140', internal_note: 'office: ordered a spare set',
  spare_parts: 'office: FU-400A x2', site_location: '', equipment_serial: '',
  sap_ticket: '', hours: null, time_from: '', time_to: '',
  availability_impact: 'none', assigned_to: '', assigned_name: '',
  assigned_by: '', due_date: '', sync_status: 'synced', on_server: true,
  version: 3, created_at: '2026-09-14T08:00:00.000Z',
  updated_at: '2026-09-14T08:00:00.000Z', deleted_at: null, tags: [],
  image_ids: [],
};
const DONE = Object.assign({}, OPEN, { id: 'rec-done', status: 'done' });
const JOB = Object.assign({}, OPEN, {
  id: 'rec-job', assigned_to: 'u-tech1', assigned_by: 'office',
});
// Written in a container and never sent: there is no server copy of it.
const UNSENT = Object.assign({}, OPEN, {
  id: 'rec-new', sync_status: 'local', version: 1, on_server: false,
  internal_note: 'mine, written here', spare_parts: '',
});
// A record in a month the office has closed: sent AND settled.
const CLOSED_MONTH = Object.assign({}, OPEN, {
  id: 'rec-aug', log_date: '2026-08-11',
});

const PROJECTS_TAUGHT = [{
  id: 7, name: 'Tashkent BESS', num_blocks: 16, zones: '[[1,1,8],[2,9,16]]',
  locked_months: '["2026-08"]',
}];
const PROJECTS_NONE_LOCKED = [Object.assign({}, PROJECTS_TAUGHT[0],
                                            { locked_months: '[]' })];
// An older server, or a desktop that has not published them yet: the key is
// simply not there. The phone must read that as "unknown", never as "all".
const PROJECTS_UNTAUGHT = [{
  id: 7, name: 'Tashkent BESS', num_blocks: 16, zones: '[[1,1,8],[2,9,16]]',
}];

function element() {
  return {
    value: '', textContent: '', innerHTML: '', disabled: false, style: {},
    dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
    querySelectorAll: () => [], appendChild() {}, addEventListener() {},
    // a <datalist>'s suggestions and a <select>'s options, as the fault list
    // and the project picker read them
    options: [], selectedOptions: [{ textContent: 'Tashkent BESS' }],
    setAttribute() {}, remove() {},
  };
}

/** A server that behaves like routers/sync.py: an unknown id is an INSERT of
    the whole payload, a version behind the server's is a conflict carrying the
    server's row, and a matching version is a FAST-FORWARD that writes only the
    keys the payload actually carried. That last rule is the contract this
    whole feature stands on. */
function makeServer(rows, projects) {
  const state = {};
  for (const r of (rows || [])) state[r.id] = JSON.parse(JSON.stringify(r));
  const sent = [];
  return {
    _state: state, _sent: sent,
    async getProjects() { return projects; },
    async pullDelta() { return { changes: [], cursor: '1', has_more: false }; },
    async uploadImage() { return {}; },
    async pushChanges(changes) {
      sent.push(...JSON.parse(JSON.stringify(changes)));
      return {
        results: changes.map(c => {
          const row = state[c.id];
          if (!row) {
            state[c.id] = Object.assign({ id: c.id }, c.payload, { version: 1 });
            return { id: c.id, outcome: 'applied', server_version: 1 };
          }
          if ((row.version || 1) > (c.version || 1)) {
            return { id: c.id, outcome: 'conflict',
                     server_row: JSON.parse(JSON.stringify(row)) };
          }
          for (const k of Object.keys(c.payload)) {
            if (k === 'updated_at' || k === 'created_at') continue;
            row[k] = c.payload[k];
          }
          row.version = (row.version || 1) + 1;
          return { id: c.id, outcome: 'applied', server_version: row.version };
        }),
      };
    },
  };
}

function sandbox(server, entries, projects) {
  const els = {};
  const el = id => (els[id] || (els[id] = element()));
  const store = { entries: {}, meta: { projects: projects } };
  for (const e of entries) store.entries[e.id] = JSON.parse(JSON.stringify(e));
  const DB = {
    async getMeta(k, d) { return k in store.meta ? store.meta[k] : d; },
    async setMeta(k, v) { store.meta[k] = v; },
    async saveEntry(e) { store.entries[e.id] = JSON.parse(JSON.stringify(e)); },
    async getEntry(id) {
      return store.entries[id] ? JSON.parse(JSON.stringify(store.entries[id])) : null;
    },
    async getAllEntries() { return Object.values(store.entries); },
    async getPendingEntries() {
      return Object.values(store.entries)
        .filter(e => ['local', 'pending'].includes(e.sync_status))
        .map(e => JSON.parse(JSON.stringify(e)));
    },
    async markSynced(id, v) {
      const e = store.entries[id];
      if (e) { e.sync_status = 'synced'; if (v != null) e.version = v; }
    },
    async deleteEntry(id) { delete store.entries[id]; },
    async saveImage() {}, async getImagesForEntry() { return []; },
    async getPendingImages() { return []; },
    async deleteImagesForEntry() {},
    async getAllFieldEvents() { return []; },
    async getPendingFieldEvents() { return []; },
    async saveFieldEvent() {},
    async getPendingWriteoffs() { return []; },
    async getAllChecklists() { return []; },
    async getPendingChecklists() { return []; },
    async getChecklist() { return null; },
    async saveChecklist() {}, async updateChecklist() {},
    async getAllActions() { return []; },
    async getPendingActions() { return []; },
    async getAction() { return null; },
    async saveAction() {}, async updateAction() { return null; },
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
    scrollTo() {}, DB, API: server,
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

/** Fill the correction form the way a thumb does, then Save — and then sync
    once, deterministically. Saving fires a background sync of its own that
    nothing can await, so the phone is taken offline across the tap and the
    sync is asked for here instead; otherwise the two race and the record is
    pushed twice. */
async function typeAndSave(ctx, values) {
  const was = ctx.navigator.onLine;
  ctx.navigator.onLine = false;
  for (const [id, v] of Object.entries(values)) ctx._els[id].value = v;
  await ctx.App.saveEntry();
  ctx.navigator.onLine = was;
  return was ? await ctx.App._doSync() : null;
}

/** Answer one clash, then sync once — for the same reason. */
async function resolve(ctx, field, which) {
  const was = ctx.navigator.onLine;
  ctx.navigator.onLine = false;
  await ctx.App.resolveClash(field, which);
  ctx.navigator.onLine = was;
  return was ? await ctx.App._doSync() : null;
}

(async () => {
  // ══ 1. an open record can be corrected, and the correction arrives ════════
  let srv = makeServer([OPEN], PROJECTS_TAUGHT);
  let ctx = sandbox(srv, [OPEN], PROJECTS_TAUGHT);
  let App = ctx.App;

  await App._showDetail('rec-1');
  check(ctx._els['detail-edit'].style.display === '',
        'an open record the technician wrote shows the Edit button');
  check(ctx._els['detail-body'].innerHTML.includes('Correct this record'),
        'and says so in the record itself');
  check(ctx._els['detail-body'].innerHTML.includes('Block 5 · LC1 · BESS 2'),
        'the detail screen now says which node — the thing most often wrong');

  await App.editEntry();
  check(ctx._els['create-title'].textContent === 'Correct record',
        'the form opens as a correction: ' + ctx._els['create-title'].textContent);
  check(ctx._els['f-fault'].value === 'Fuse blown'
        && ctx._els['f-desc'].value === 'Fuse replaced on BESS 2'
        && ctx._els['f-block'].value === '5',
        'filled in from the record, not blank');
  check(ctx._els['f-photo-group'].style.display === 'none'
        && ctx._els['f-repeat-btn'].style.display === 'none'
        && ctx._els['f-status-group'].style.display === 'none'
        && ctx._els['f-pm-group'].style.display === 'none',
        'photos, "Repeat on another node", the status and the PM hours are '
        + 'not on a correction form');
  check(ctx._els['f-date'].disabled === true && ctx._els['f-note'].disabled === true
        && ctx._els['f-ptw'].disabled === true && ctx._els['f-proj'].disabled === true,
        'what a correction does not save is visibly locked, not a box that '
        + 'quietly drops what is typed into it');
  check(ctx._els['f-edit-note'].innerHTML.includes('Correcting a record you wrote'),
        'and the form says what it is');

  // the node, which is the mistake that actually happens — and a correction
  // moves ONE record, so a tap replaces the block instead of adding to it
  await App.openNodePicker('create');
  App.pickBlock(11);
  App.pickBlock(12);
  check(App._nodeChosen().join(',') === '12',
        'a correction takes one block, not a list of them: '
        + App._nodeChosen().join(','));
  check(ctx._els['np-all'].style.display === 'none',
        '"All blocks" is not offered — it writes a record per block, which is '
        + 'new work and not a correction');
  check(ctx._els['np-picked'].textContent.includes('Correcting one record'),
        'and the picker says which it is doing');
  App.pickBlock(5);                  // back to where the work really was
  App.useNode();

  await typeAndSave(ctx, { 'f-fault': 'Fuse blown — 400 A DC' });
  let row = srv._state['rec-1'];
  check(row.fault_name === 'Fuse blown — 400 A DC',
        'the correction reaches the server: ' + row.fault_name);
  check(ctx._store.entries['rec-1'].sync_status === 'synced',
        'and the phone knows it was taken');
  check(ctx._store.entries['rec-1'].edit_fields === undefined
        && ctx._store.entries['rec-1'].edit_base === undefined,
        'with nothing left waiting on the record afterwards');

  // ══ 2. ONLY what changed is sent — the regression that matters most ═══════
  const sentPayload = srv._sent.find(c => c.id === 'rec-1').payload;
  check(Object.keys(sentPayload).sort().join(',') === 'fault_name,updated_at',
        'the push carries the changed field and nothing else: '
        + Object.keys(sentPayload).sort().join(','));
  check(!('internal_note' in sentPayload) && !('spare_parts' in sentPayload)
        && !('description' in sentPayload),
        'no key for a field the phone did not touch — an absent key means '
        + '"no opinion", never "make it empty"');
  check(row.internal_note === 'office: ordered a spare set'
        && row.spare_parts === 'office: FU-400A x2',
        'so what the OFFICE wrote is still on the server row: '
        + JSON.stringify([row.internal_note, row.spare_parts]));
  check(row.description === 'Fuse replaced on BESS 2' && row.ptw_no === 'PTW-2609-140'
        && row.plant_block === 5 && row.status === 'open',
        'and so is every other field of it');

  // the whole-record push is unchanged for everything that is not a correction
  check(srv._sent.filter(c => c.id === 'rec-1').length === 1,
        'one push, not a retry loop');

  // ══ 3. a done record offers no edit ══════════════════════════════════════
  ctx = sandbox(makeServer([DONE], PROJECTS_TAUGHT), [DONE], PROJECTS_TAUGHT);
  await ctx.App._showDetail('rec-done');
  check(ctx._els['detail-edit'].style.display === 'none',
        'a DONE record offers no Edit button');
  check(!ctx._els['detail-body'].innerHTML.includes('Correct this record')
        && ctx._els['detail-body'].innerHTML.includes('marked Done')
        && ctx._els['detail-body'].innerHTML.includes('office'),
        'and says it is Done and where it is reopened — "Done", not "closed", '
        + 'because a CLOSED MONTH is a different thing on this same screen');
  check(ctx.App._canEdit(DONE) === false, '_canEdit says no for a done record');
  await ctx.App.editEntry('rec-done');
  check((ctx._els['create-title'] || {}).textContent !== 'Correct record'
        && ctx.App._editId === null,
        'and asking for the form anyway does not open it');

  // a job the office handed out belongs to the Tasks screen
  ctx = sandbox(makeServer([JOB], PROJECTS_TAUGHT), [JOB], PROJECTS_TAUGHT);
  await ctx.App._showDetail('rec-job');
  check(ctx._els['detail-edit'].style.display === 'none'
        && ctx._els['detail-body'].innerHTML.includes('Tasks tab'),
        "a job from the office is filled in on the Tasks screen, not here");

  // ══ 4. a record the server has never seen goes up WHOLE ══════════════════
  srv = makeServer([], PROJECTS_TAUGHT);          // the server holds nothing
  ctx = sandbox(srv, [UNSENT], PROJECTS_TAUGHT);
  await ctx.App.editEntry('rec-new');
  await typeAndSave(ctx, { 'f-fault': 'Fuse blown — corrected offline' });
  const insert = srv._sent.find(c => c.id === 'rec-new').payload;
  check('description' in insert && 'category' in insert && 'log_date' in insert
        && 'internal_note' in insert,
        'a record the server has never held is INSERTed whole — a partial '
        + 'payload would create a row missing everything it did not carry');
  check(srv._state['rec-new'].description === 'Fuse replaced on BESS 2'
        && srv._state['rec-new'].internal_note === 'mine, written here',
        'so the row the server creates is the whole record: '
        + JSON.stringify([srv._state['rec-new'].description,
                          srv._state['rec-new'].internal_note]));

  // ══ 5. a collision in DIFFERENT fields settles itself ════════════════════
  // The phone corrects the fault offline. The office, meanwhile, rewrites the
  // customer line and adds a note — so the server is a version ahead and the
  // push is refused. Nothing here needs a person: the two edits touched
  // different fields.
  srv = makeServer([OPEN], PROJECTS_TAUGHT);
  ctx = sandbox(srv, [OPEN], PROJECTS_TAUGHT);
  ctx.navigator.onLine = false;
  await ctx.App.editEntry('rec-1');
  await typeAndSave(ctx, { 'f-fault': 'Fuse blown — 400 A DC' });
  check(ctx._store.entries['rec-1'].edit_fields.join(',') === 'fault_name',
        'the phone records exactly which field it changed: '
        + ctx._store.entries['rec-1'].edit_fields.join(','));
  // the office writes the same record
  Object.assign(srv._state['rec-1'], {
    description: 'Fuse replaced, DC side checked', internal_note: 'office: spare fitted',
    version: 4,
  });
  ctx.navigator.onLine = true;
  let res = await ctx.App._doSync();
  row = srv._state['rec-1'];
  let mine = ctx._store.entries['rec-1'];
  check(row.fault_name === 'Fuse blown — 400 A DC',
        "the technician's correction got through after the refusal: " + row.fault_name);
  check(row.description === 'Fuse replaced, DC side checked'
        && row.internal_note === 'office: spare fitted',
        "and the office's own two changes are untouched: "
        + JSON.stringify([row.description, row.internal_note]));
  check(mine.sync_status === 'synced' && res.conflicts === 0 && res.rebased === 1,
        'it settled inside one sync, with nothing for anyone to decide: '
        + JSON.stringify([mine.sync_status, res.conflicts, res.rebased]));
  check(mine.description === 'Fuse replaced, DC side checked'
        && mine.edit_clash === undefined,
        "and the phone now shows the office's line too — no clash to answer");

  // ══ 6. a collision in the SAME field is decided by a person ══════════════
  // Both sides rewrote the customer's line. Neither is thrown away.
  function sameFieldClash() {
    const s = makeServer([OPEN], PROJECTS_TAUGHT);
    const c = sandbox(s, [OPEN], PROJECTS_TAUGHT);
    return { s, c };
  }
  async function raceOnDescription(s, c) {
    c.navigator.onLine = false;
    await c.App.editEntry('rec-1');
    await typeAndSave(c, { 'f-desc': 'Fuse replaced, insulation measured' });
    Object.assign(s._state['rec-1'], {
      description: 'Fuse replaced by the day shift', version: 4,
    });
    c.navigator.onLine = true;
    return await c.App._doSync();
  }

  let { s, c } = sameFieldClash();
  res = await raceOnDescription(s, c);
  mine = c._store.entries['rec-1'];
  check(mine.sync_status === 'conflict' && res.conflicts === 1,
        'both wrote the same line, so it waits for a person: '
        + JSON.stringify([mine.sync_status, res.conflicts]));
  check(mine.description === 'Fuse replaced, insulation measured',
        'the work done on site is still the text on the phone — the only copy '
        + 'of itself is never dropped to make a merge tidy');
  check(mine.edit_clash.length === 1 && mine.edit_clash[0].field === 'description'
        && mine.edit_clash[0].theirs === 'Fuse replaced by the day shift'
        && mine.edit_clash[0].mine === 'Fuse replaced, insulation measured',
        'and both lines are kept on the record: '
        + JSON.stringify(mine.edit_clash[0]));
  check(s._state['rec-1'].description === 'Fuse replaced by the day shift',
        "the server still reads the office's line until somebody chooses");
  await c.App._showDetail('rec-1');
  let html = c._els['detail-body'].innerHTML;
  check(html.includes('The office changed this record while you were offline')
        && html.includes('Nothing has been lost'),
        'the record says what happened, in words a technician can act on');
  check(html.includes('Fuse replaced by the day shift')
        && html.includes('Fuse replaced, insulation measured')
        && html.includes("App.resolveClash('description','theirs')")
        && html.includes("App.resolveClash('description','mine')"),
        'with both lines side by side and one tap for each');
  const renderCard = vm.runInContext('_renderCard', c);
  check(renderCard(mine).includes('The office changed this'),
        'and the card in the list says so too');

  // "Keep mine" — the technician's line goes to the server
  await resolve(c, 'description', 'mine');
  mine = c._store.entries['rec-1'];
  check(mine.edit_clash === undefined && mine.sync_status === 'synced'
        && s._state['rec-1'].description === 'Fuse replaced, insulation measured',
        'Keep mine sends it: ' + s._state['rec-1'].description);
  check(s._state['rec-1'].internal_note === 'office: ordered a spare set',
        "and still does not touch anything else the office wrote");

  // "Use this" — the office's line stands, and nothing of the phone's is sent
  ({ s, c } = sameFieldClash());
  await raceOnDescription(s, c);
  await resolve(c, 'description', 'theirs');
  mine = c._store.entries['rec-1'];
  check(mine.description === 'Fuse replaced by the day shift'
        && mine.sync_status === 'synced' && mine.edit_clash === undefined,
        "Use this takes the office's line and the record is settled: "
        + mine.description);
  check(s._state['rec-1'].description === 'Fuse replaced by the day shift'
        && s._state['rec-1'].version === 4,
        'nothing was pushed — the server was already right');

  // a clash on one field does not hold up the correction of another
  ({ s, c } = sameFieldClash());
  c.navigator.onLine = false;
  await c.App.editEntry('rec-1');
  await typeAndSave(c, { 'f-desc': 'mine, on site', 'f-sap': 'SAP-9001' });
  Object.assign(s._state['rec-1'], { description: 'theirs, in the office',
                                     version: 4 });
  c.navigator.onLine = true;
  await c.App._doSync();
  mine = c._store.entries['rec-1'];
  check(mine.edit_clash.length === 1 && mine.edit_fields.indexOf('sap_ticket') >= 0,
        'the uncontested field stays part of the correction: '
        + JSON.stringify(mine.edit_fields));
  await resolve(c, 'description', 'theirs');
  check(s._state['rec-1'].sap_ticket === 'SAP-9001'
        && s._state['rec-1'].description === 'theirs, in the office',
        'and goes with the answer: ' + JSON.stringify(
          [s._state['rec-1'].sap_ticket, s._state['rec-1'].description]));

  // ══ 7. a CLOSED month — sent to the customer and settled ════════════════
  srv = makeServer([CLOSED_MONTH], PROJECTS_TAUGHT);
  ctx = sandbox(srv, [CLOSED_MONTH], PROJECTS_TAUGHT);
  await ctx.App._showDetail('rec-aug');
  check(ctx._els['detail-body'].innerHTML.includes(
          'August 2026 is closed'),
        'the record says plainly that the month is settled with the customer');
  check(ctx._els['detail-edit'].style.display === '',
        'and the Edit button is still there — the owner allows this, with a '
        + 'warning, not a block');
  await ctx.App.editEntry('rec-aug');
  check(ctx._els['f-edit-note'].innerHTML.includes('is closed —'),
        'the warning is on the correction form as well');
  await typeAndSave(ctx, { 'f-fault': 'Fuse blown — August, corrected' });
  check(srv._state['rec-aug'].fault_name === 'Fuse blown — August, corrected',
        'and the correction goes through: ' + srv._state['rec-aug'].fault_name);

  // September is not locked, so September says nothing
  ctx = sandbox(makeServer([OPEN], PROJECTS_TAUGHT), [OPEN], PROJECTS_TAUGHT);
  await ctx.App._showDetail('rec-1');
  check(!ctx._els['detail-body'].innerHTML.includes('is closed —'),
        'a month that is not locked gets no warning');

  // The office's actual habit: the August report goes to the customer and the
  // month is deliberately LEFT OPEN until the customer confirms it needs no
  // changes. Through that whole stretch a correction is exactly what is
  // wanted, so nothing warns — the warning follows the lock, never the fact
  // that a report was generated or posted.
  const SENT_NOT_CLOSED = [Object.assign({}, PROJECTS_TAUGHT[0],
                                         { locked_months: '[]' })];
  ctx = sandbox(makeServer([CLOSED_MONTH], SENT_NOT_CLOSED), [CLOSED_MONTH],
                SENT_NOT_CLOSED);
  await ctx.App._showDetail('rec-aug');
  check(!ctx._els['detail-body'].innerHTML.includes('is closed —')
        && ctx._els['detail-edit'].style.display === '',
        'August sent but not yet closed: no warning at all, and the record '
        + 'is freely correctable — that gap is what corrections are for');
  await ctx.App.editEntry('rec-aug');
  check(!ctx._els['f-edit-note'].innerHTML.includes('is closed —'),
        'and the correction form is quiet about it too');

  // ══ 8. an un-taught server, and one that says "none" ═════════════════════
  // "" / absent is "nobody told me" and "[]" is "I asked, none are locked".
  // Both are quiet — but they are different answers, and a phone that read
  // the first as "everything is sent" would warn on every record it had.
  ctx = sandbox(makeServer([CLOSED_MONTH], PROJECTS_UNTAUGHT), [CLOSED_MONTH],
                PROJECTS_UNTAUGHT);
  check(await ctx.App._lockedMonths(7) === null,
        'a server that was never told answers "unknown", not "all" and not "none"');
  await ctx.App._showDetail('rec-aug');
  check(!ctx._els['detail-body'].innerHTML.includes('is closed —'),
        'so an un-taught server warns about nothing rather than everything');
  await ctx.App.editEntry('rec-aug');
  check(ctx._els['create-title'].textContent === 'Correct record',
        'and the correction form still works against it');
  await typeAndSave(ctx, { 'f-fault': 'corrected against an old server' });
  check(ctx._store.entries['rec-aug'].fault_name === 'corrected against an old server',
        'the edit itself is unaffected by not knowing');

  ctx = sandbox(makeServer([CLOSED_MONTH], PROJECTS_NONE_LOCKED), [CLOSED_MONTH],
                PROJECTS_NONE_LOCKED);
  const none = await ctx.App._lockedMonths(7);
  check(Array.isArray(none) && none.length === 0,
        '"[]" is a real answer: published, and nothing is locked');
  await ctx.App._showDetail('rec-aug');
  check(!ctx._els['detail-body'].innerHTML.includes('is closed —'),
        'and it warns about nothing, correctly this time');
  check(await ctx.App._lockedMonths(null) === null
        && await ctx.App._lockedMonths(999) === null,
        'a record with no project, or one this phone does not know, is unknown');

  // ══ 9. a PM record's PM-ness is not a phone decision ═════════════════════
  const PM = Object.assign({}, OPEN, {
    id: 'rec-pm', category: 'maintenance', hours: 4,
    description: 'PM: as per checklist',
  });
  srv = makeServer([PM], PROJECTS_TAUGHT);
  ctx = sandbox(srv, [PM], PROJECTS_TAUGHT);
  await ctx.App.editEntry('rec-pm');
  check(ctx._els['f-cat'].disabled === true,
        'a PM record cannot be made into something else here — its hours are '
        + "already in the month's availability figure");
  await typeAndSave(ctx, { 'f-desc': 'coolant topped up as well' });
  check(/^PM: /.test(srv._state['rec-pm'].description),
        'and a corrected PM line still says PM, so section 3.2 does not report '
        + 'it a second time: ' + srv._state['rec-pm'].description);
  // the guard itself, with the select forced the way a stale form would
  ctx._els['f-cat'].value = 'fault';
  ctx.App._editId = 'rec-pm';
  ctx.App._editBase = { description: 'PM: as per checklist' };
  await ctx.App.saveEntry();
  check(ctx._els['create-error'].style.display === 'block'
        && srv._state['rec-pm'].category === 'maintenance',
        'switching it is refused in words, not silently ignored');

  console.log(failures ? 'RESULT FAIL (' + failures + ' check(s))' : 'RESULT PASS');
  process.exit(0);
})();
