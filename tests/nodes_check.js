/**
 * Several blocks in one record (v18), run for real: app.js is loaded into a
 * sandbox with a recording DOM and a memory IndexedDB, and the node picker is
 * driven the way a thumb does it.
 *
 * Checked:
 *   * three blocks picked for one alarm write THREE records — one per block,
 *     same text, type, status, PTW, hours, LC and device, different ids;
 *   * the photos are stored once, on the first block's record, and the other
 *     records say in their internal note where they are (a site connection
 *     must not carry the same photos three times);
 *   * one block behaves exactly as it did: one record, the photos on it, the
 *     internal note untouched, the same taps;
 *   * "All blocks" fills every block of the project;
 *   * a PM on three blocks writes ONE field event naming all three (the
 *     desktop's record_pm splits it into one PM record per block, each charged
 *     the full hours) and says so on the form before it is saved.
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

// a 16-block plant in two zones, as the desktop mirrors it to the phone
const PROJECT = { id: 7, name: 'TK', num_blocks: 16, zones: '[[1,1,8],[2,9,16]]' };

function element() {
  return {
    value: '', textContent: '', innerHTML: '', disabled: false, style: {},
    dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
    options: [],          // the fault <datalist> the form reads suggestions from
    querySelectorAll: () => [], appendChild() {}, addEventListener() {},
  };
}

function sandbox() {
  const els = {};
  const el = id => (els[id] || (els[id] = element()));
  // the project select is a real <select> on the phone: the record form reads
  // the chosen option's text for the photo stamp
  els['f-proj'] = Object.assign(element(), {
    selectedOptions: [{ textContent: 'TK' }],
  });
  const store = { entries: {}, images: [], events: [], meta: { projects: [PROJECT] } };
  const DB = {
    async getMeta(k, d) { return k in store.meta ? store.meta[k] : d; },
    async setMeta(k, v) { store.meta[k] = v; },
    async saveEntry(e) { store.entries[e.id] = JSON.parse(JSON.stringify(e)); },
    async getEntry(id) { return store.entries[id] || null; },
    async getAllEntries() { return Object.values(store.entries); },
    async getPendingEntries() { return []; },
    async markSynced() {}, async deleteEntry() {},
    async saveImage(im) { store.images.push(im); },
    async getImagesForEntry(id) { return store.images.filter(i => i.entry_id === id); },
    async getPendingImages() { return []; },
    async deleteImagesForEntry() {},
    async saveFieldEvent(ev) { store.events.push(ev); },
    async getAllFieldEvents() { return store.events; },
    async getPendingFieldEvents() { return []; },
    async getPendingWriteoffs() { return []; },
    async getAllChecklists() { return []; },
    async getPendingChecklists() { return []; },
    async getChecklist() { return null; },
    async saveChecklist() {}, async updateChecklist() {},
  };
  const ctxObj = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { onLine: false, serviceWorker: { addEventListener() {} } },
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
    scrollTo() {}, DB, API: {},
  };
  ctxObj.window = ctxObj;
  ctxObj.self = ctxObj;
  vm.createContext(ctxObj);
  vm.runInContext(fs.readFileSync(APP, 'utf8'), ctxObj, { filename: 'app.js' });
  ctxObj.App = vm.runInContext('App', ctxObj);
  // the stamp itself is checked by stamp_check.js; here it must not need a canvas
  ctxObj.App._stampPhoto = async () => 'data:image/jpeg;base64,STAMPED';
  ctxObj._els = els;
  ctxObj._store = store;
  return ctxObj;
}

function photo(id) {
  return { id, file: { size: 120000, name: id + '.jpg' },
           dataUrl: 'data:image/jpeg;base64,RAW', geo: null, taken: Date.now() };
}

function fill(ctx, over) {
  const v = Object.assign({
    'f-date': today(), 'f-desc': 'Antifreeze topped up and level checked',
    'f-fault': 'Antifreeze Low Level', 'f-ptw': 'PTW-2609-140',
    'f-note': 'pump was warm again',
  }, over || {});
  for (const k of Object.keys(v)) ctx._els[k].value = v[k];
}

(async () => {
  // ── three blocks, one trip ────────────────────────────────────────────────
  const ctx = sandbox();
  const App = ctx.App;
  await App.goCreate('fault');
  await App.openNodePicker('create');
  App.pickBlock(4);                       // zone 1
  App.pickZone(2);
  App.pickBlock(12); App.pickBlock(16);   // zone 2 — the selection survives
  App.pickLc('LC1'); App.pickDevice('BESS 3');
  check(ctx._els['np-use'].textContent === 'Use 3 blocks · 4, 12, 16 · BESS 3',
        'the button says what it will do: ' + ctx._els['np-use'].textContent);
  check(/3 blocks · 4, 12, 16/.test(ctx._els['np-picked'].textContent),
        'and the picker says how many are chosen: ' + ctx._els['np-picked'].textContent);
  App.useNode();
  check(ctx._els['f-blocks'].value === '4,12,16' && ctx._els['f-block'].value === '4',
        'the form carries all three, first one leading: '
        + ctx._els['f-blocks'].value);
  check(ctx._els['f-node-text'].textContent === '3 blocks · 4, 12, 16 · LC1 · BESS 3',
        'and shows them: ' + ctx._els['f-node-text'].textContent);

  fill(ctx);
  App._stagedPhotos = [photo('p1'), photo('p2')];
  await App.saveEntry();

  let recs = Object.values(ctx._store.entries).sort((a, b) => a.plant_block - b.plant_block);
  check(recs.length === 3, 'three blocks write three records: ' + recs.length);
  check(recs.map(r => r.plant_block).join(',') === '4,12,16',
        'one per block: ' + recs.map(r => r.plant_block).join(','));
  check(new Set(recs.map(r => r.id)).size === 3, 'each with its own id');
  check(recs.every(r => r.description === 'Antifreeze topped up and level checked'
                     && r.fault_name === 'Antifreeze Low Level'
                     && r.ptw_no === 'PTW-2609-140'
                     && r.category === 'fault' && r.status === 'done'
                     && r.node_lc === 'LC1' && r.node_device === 'BESS 3'
                     && r.project_id === 7 && r.log_date === today()),
        'same text, type, status, PTW, LC and device on all three');
  check(ctx._store.images.length === 2,
        'the two photos are stored once, not six times: ' + ctx._store.images.length);
  check(ctx._store.images.every(i => i.entry_id === recs[0].id)
        && recs[0].image_ids.length === 2
        && recs[1].image_ids.length === 0 && recs[2].image_ids.length === 0,
        'and they hang on the first block\'s record only');
  check(recs[0].internal_note === 'pump was warm again',
        'the first record\'s internal note is the technician\'s own: '
        + JSON.stringify(recs[0].internal_note));
  check(recs[1].internal_note === 'pump was warm again\nPhotos on the Block 4 record.'
        && recs[2].internal_note.includes('Photos on the Block 4 record.'),
        'the others say where the photos are: ' + JSON.stringify(recs[2].internal_note));
  check(ctx._store.events.length === 0, 'a fault writes no PM field event');

  // ── one block: exactly what it always did ─────────────────────────────────
  const one = sandbox();
  await one.App.goCreate('fault');
  await one.App.openNodePicker('create');
  one.App.pickZone(2);
  one.App.pickBlock(12);                  // one tap on the block…
  one.App.pickLc('LC2');
  check(one._els['np-use'].textContent === 'Use Block 12 · Z2/B4 · LC2',
        'the button is the single-block one: ' + one._els['np-use'].textContent);
  one.App.useNode();                      // …and one on Use, as before
  check(one._els['f-node-text'].textContent === 'Block 12 · Z2/B4 · LC2',
        'the form reads exactly as before: ' + one._els['f-node-text'].textContent);
  check(one._els['f-blocks'].value === '12' && one._els['f-block'].value === '12',
        'and carries the one block');
  fill(one);
  one.App._stagedPhotos = [photo('p1')];
  await one.App.saveEntry();
  const solo = Object.values(one._store.entries);
  check(solo.length === 1 && solo[0].plant_block === 12,
        'one block still writes ONE record: ' + solo.length);
  check(solo[0].image_ids.length === 1 && one._store.images[0].entry_id === solo[0].id,
        'with the photo on it');
  check(solo[0].internal_note === 'pump was warm again',
        'and nothing added to the internal note: '
        + JSON.stringify(solo[0].internal_note));

  // tapping a chosen block again takes it back
  const off = sandbox();
  await off.App.goCreate('fault');
  await off.App.openNodePicker('create');
  off.App.pickBlock(4); off.App.pickBlock(5); off.App.pickBlock(4);
  check(off._els['np-use'].textContent.startsWith('Use Block 5'),
        'a second tap unpicks a block: ' + off._els['np-use'].textContent);

  // ── "All blocks" ──────────────────────────────────────────────────────────
  const all = sandbox();
  await all.App.goCreate('inspection');
  await all.App.openNodePicker('create');
  all.App.nodeAll();
  all.App.useNode();
  check(all._els['f-blocks'].value === Array.from({ length: 16 }, (_, i) => i + 1).join(','),
        'All fills every block of the project: ' + all._els['f-blocks'].value);
  check(all._els['f-node-text'].textContent === '16 blocks · 1-16',
        'and reads as a range: ' + all._els['f-node-text'].textContent);
  all.App.nodeNone();
  check(all._els['np-use'].disabled === true,
        'Clear empties it again and Use goes dead');

  // ── the Number tab, for a project whose zones are not mirrored yet ────────
  const nz = sandbox();
  nz._store.meta.projects = [{ id: 7, name: 'TK', num_blocks: 16, zones: '[]' }];
  await nz.App.goCreate('fault');
  await nz.App.openNodePicker('create');
  nz.App.nodeTab('number');
  nz.App.nodeKey('1'); nz.App.nodeKey('2');
  nz.App.nodeAddNumber();                 // keep 12, type the next one
  nz.App.nodeKey('1'); nz.App.nodeKey('5');
  nz.App.useNode();                       // the typed one counts without Add
  check(nz._els['f-blocks'].value === '12,15',
        'the keypad names several blocks too: ' + nz._els['f-blocks'].value);

  // reopening the picker shows what the form already carries
  await nz.App.openNodePicker('create');
  check(nz._els['np-use'].textContent === 'Use 2 blocks · 12, 15',
        'reopening keeps them: ' + nz._els['np-use'].textContent);

  // ── a PM on three blocks ──────────────────────────────────────────────────
  const pm = sandbox();
  await pm.App.goCreate('maintenance');
  await pm.App.openNodePicker('create');
  pm.App.pickBlock(4); pm.App.pickBlock(5); pm.App.pickBlock(6);
  pm.App.useNode();
  check(/EACH of the 3 blocks/.test(pm._els['f-hours-hint'].textContent),
        'the form says the hours are charged per block: '
        + pm._els['f-hours-hint'].textContent);
  fill(pm, { 'f-desc': 'PM as per the checklist (PCS + BESS)', 'f-fault': '' });
  pm._els['f-hours'].value = '4';
  pm._els['f-start'].value = '09:00';
  pm._els['f-end'].value = '13:00';
  await pm.App.saveEntry();
  const pmRecs = Object.values(pm._store.entries).sort((a, b) => a.plant_block - b.plant_block);
  check(pmRecs.length === 3 && pmRecs.every(r => r.hours === 4),
        'three PM records, 4 h on each: '
        + pmRecs.map(r => r.plant_block + '=' + r.hours).join(' '));
  check(pmRecs.every(r => /^PM: /.test(r.description)),
        'each keeps saying PM in the customer\'s line (3.2 counts it as PM hours)');
  check(pm._store.events.length === 1,
        'ONE field event, not three: ' + pm._store.events.length);
  const ev = pm._store.events[0];
  check(ev.kind === 'pm' && ev.blocks === '4,5,6' && ev.hours === 4
        && ev.project_id === 7 && ev.date_from === today(),
        'carrying all three blocks and the hours: ' + JSON.stringify(
          { blocks: ev.blocks, hours: ev.hours }));
  check(ev.ptw_no === 'PTW-2609-140' && ev.description.includes('PTW-2609-140'),
        'and the permit: ' + ev.description);

  // "All blocks" is one tap; 70 records must not be one tap too
  {
    const many = sandbox();
    const asked = [];
    many.confirm = q => { asked.push(q); return false; };
    await many.App.goCreate('fault');
    await many.App.openNodePicker('create');
    many.App.nodeAll();
    many.App.useNode();
    fill(many);
    await many.App.saveEntry();
    check(asked.length === 1 && /16 records/.test(asked[0]),
          'a whole-plant record asks first: '
          + JSON.stringify((asked[0] || '').slice(0, 60)));
    check(Object.keys(many._store.entries).length === 0,
          'and answering no writes nothing');

    const few = sandbox();
    const asked2 = [];
    few.confirm = q => { asked2.push(q); return true; };
    await few.App.goCreate('fault');
    await few.App.openNodePicker('create');
    few.App.pickBlock(4); few.App.pickBlock(5); few.App.pickBlock(6);
    few.App.useNode();
    fill(few);
    await few.App.saveEntry();
    check(asked2.length === 0 && Object.keys(few._store.entries).length === 3,
          'three blocks are saved without a question');
  }

  console.log(failures ? 'RESULT FAIL (' + failures + ' check(s))' : 'RESULT PASS');
  process.exit(0);
})();
