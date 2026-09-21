/**
 * The photo stamp, run for real: app.js is loaded into a sandbox with a
 * recording canvas, and App._stampPhoto is called the way the record form
 * calls it. Prints one "ok"/"FAIL" line per check, then RESULT.
 *
 * Checked: the caption is burned in at the bottom (project, date and time,
 * node, coordinates), the photo is re-encoded as JPEG, a large camera photo
 * is scaled down, and a refused or absent location still produces a photo.
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

function recordingCanvas() {
  const calls = { text: [], rects: [], draws: [], encoded: null };
  const ctx = {
    font: '', textBaseline: '', fillStyle: '',
    drawImage: (im, x, y, w, h) => calls.draws.push({ w, h }),
    measureText: t => ({ width: t.length * 9 }),
    fillRect: (x, y, w, h) => calls.rects.push({ x, y, w, h }),
    fillText: (t, x, y) => calls.text.push({ t, x, y }),
  };
  const canvas = {
    width: 0, height: 0,
    getContext: () => ctx,
    // a real re-encode comes back as tens of kilobytes of base64; the length
    // matters, because a phone low on memory answers "data:," instead
    toDataURL: (type, q) => {
      calls.encoded = { type, q };
      return canvas._out !== undefined
        ? canvas._out : 'data:image/jpeg;base64,' + 'A'.repeat(4000);
    },
  };
  return { canvas, calls };
}

function sandbox(canvasHolder) {
  const listeners = {};
  const ctxObj = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    navigator: { onLine: true, geolocation: null, serviceWorker: { addEventListener() {} } },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: {
      addEventListener: (e, fn) => { listeners[e] = fn; },
      getElementById: () => null,
      querySelectorAll: () => [],
      createElement: tag => (tag === 'canvas' ? canvasHolder.canvas : { style: {}, classList: { add() {}, toggle() {} } }),
      body: { classList: { toggle() {} } },
    },
    Image: function () { },
    fetch: () => Promise.reject(new Error('no network in this check')),
    indexedDB: {},
    DB: {}, API: {},
  };
  ctxObj.window = ctxObj;
  ctxObj.self = ctxObj;
  vm.createContext(ctxObj);
  vm.runInContext(fs.readFileSync(APP, 'utf8'), ctxObj, { filename: 'app.js' });
  // `const App` lives in the context's lexical scope, not on the object
  ctxObj.App = vm.runInContext('App', ctxObj);
  return ctxObj;
}

(async () => {
  // a 4000×3000 camera photo
  const holder = recordingCanvas();
  const ctx = sandbox(holder);
  ctx.createImageBitmap = () => Promise.resolve({ width: 4000, height: 3000, close() {} });
  const App = ctx.App;

  const staged = {
    file: { name: 'IMG_0007.heic', size: 5 * 1024 * 1024 },
    dataUrl: 'data:image/heic;base64,AAAA',
    taken: new Date(2026, 8, 20, 14, 32).getTime(),
    geo: Promise.resolve({ lat: 41.2995123, lon: 69.2401456, acc: 8 }),
  };
  const out = await App._stampPhoto(staged, {
    project: 'ACWA RIVERSIDE BESS', node: 'Block 33 · LC1 · BESS 3',
  });

  const lines = holder.calls.text.map(t => t.t);
  check(lines.some(l => l.includes('ACWA RIVERSIDE BESS') && l.includes('20.09.2026 14:32')),
        'the stamp carries the project, the date and the time: ' + JSON.stringify(lines[0]));
  check(lines.some(l => l.includes('Block 33') && l.includes('BESS 3')),
        'and the node: ' + JSON.stringify(lines[1]));
  check(lines.some(l => l.includes('41.29951') && l.includes('69.24015') && l.includes('±8 m')),
        'and the coordinates: ' + JSON.stringify(lines[2]));

  const drew = holder.calls.draws[0];
  check(Math.max(drew.w, drew.h) === 1600 && drew.w === 1600 && drew.h === 1200,
        'a 4000×3000 photo is scaled to ' + drew.w + '×' + drew.h);
  const strip = holder.calls.rects[0];
  check(strip && strip.x === 0 && strip.w === 1600 && strip.y + strip.h === 1200,
        'the caption strip sits at the bottom of the photo');
  check(holder.calls.text.every(t => t.y >= strip.y && t.y < 1200),
        'every line is written inside that strip');
  check(holder.calls.encoded.type === 'image/jpeg' && holder.calls.encoded.q <= 0.9,
        'the result is re-encoded as JPEG (q=' + holder.calls.encoded.q + ')');
  check(out.startsWith('data:image/jpeg'), 'and handed back as a JPEG data URL');

  // location refused, small photo: still stamped, never scaled up
  const h2 = recordingCanvas();
  const ctx2 = sandbox(h2);
  ctx2.createImageBitmap = () => Promise.resolve({ width: 800, height: 600, close() {} });
  await ctx2.App._stampPhoto({ ...staged, geo: Promise.resolve(null) },
                             { project: 'TK', node: 'Block 7' });
  const l2 = h2.calls.text.map(t => t.t);
  check(!l2.some(l => /\d+\.\d{5}/.test(l)) && l2.length === 2,
        'a refused location leaves the coordinates out, nothing else: ' + JSON.stringify(l2));
  check(h2.calls.draws[0].w === 800, 'a small photo is not blown up');

  // no geolocation in the browser at all
  const h3 = recordingCanvas();
  const ctx3 = sandbox(h3);
  ctx3.createImageBitmap = () => Promise.resolve({ width: 800, height: 600, close() {} });
  const geo = await ctx3.App._location();
  check(geo === null, 'a browser without geolocation returns nothing, and does not throw');

  // A phone low on memory does not throw from toDataURL — it hands back
  // "data:,". That was stored as the photo, replacing a good one with an
  // empty one; it has to read as a failure so the caller keeps the original.
  for (const broken of ['data:,', '', 'data:image/jpeg;base64,']) {
    const h4 = recordingCanvas();
    h4.canvas._out = broken;
    const ctx4 = sandbox(h4);
    ctx4.createImageBitmap = () => Promise.resolve({ width: 800, height: 600, close() {} });
    let threw = false;
    try {
      await ctx4.App._stampPhoto(staged, { project: 'TK', node: 'Block 7' });
    } catch (_) { threw = true; }
    check(threw, 'a canvas that returns ' + JSON.stringify(broken)
                 + ' is a failure, not a photo');
  }

  console.log(failures ? 'RESULT FAIL (' + failures + ' check(s))' : 'RESULT PASS');
  process.exit(0);
})();
