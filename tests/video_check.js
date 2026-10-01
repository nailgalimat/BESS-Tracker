/**
 * Recording a clip on the phone, run for real: app.js is loaded into a sandbox
 * with a fake camera, a fake MediaRecorder and controllable timers, and
 * App.startVideo / stopVideo are driven the way the record form drives them.
 * Prints one "ok"/"FAIL" line per check, then RESULT.
 *
 * Checked: the capture constraints (a modest frame size, an explicit video AND
 * audio bitrate, so a 30-second clip is a few MB and not a film); the 30-second
 * hard stop, and that a clip that somehow ran over is still recorded as 30 s;
 * the mime type is chosen from the candidate list at runtime and falls through
 * to whatever the browser does support; a browser that supports none of them
 * hides the button and says why in one sentence instead of failing on the tap;
 * a poster frame is produced from the live stream and carries the same caption
 * strip a photo does; a refused microphone gives a silent clip rather than no
 * clip; and the staged clip shows its length and its size and can be removed.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = path.join(__dirname, '..', 'backend', 'static', 'js', 'app.js');
let failures = 0;
let uuidN = 0;
function check(cond, msg) {
  console.log((cond ? '   ok    ' : '   FAIL  ') + msg);
  if (!cond) failures++;
}

// ── A sandbox with a camera, a recorder and timers we hold the clock of ──────

function makeEnv(opts) {
  opts = opts || {};
  const log = {
    gum: [],            // every getUserMedia constraint object
    recOpts: [],        // every MediaRecorder option object
    timeouts: [],       // { fn, ms }
    intervals: [],
    els: {},
    appended: [],
    canvases: [],
    supported: opts.supported !== undefined
      ? opts.supported
      : ['video/mp4;codecs=avc1.42E01E,mp4a.40.2', 'video/mp4',
         'video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'],
    now: 1790000000000,
  };

  function el(id) {
    if (log.els[id]) return log.els[id];
    const e = {
      id, style: {}, textContent: '', innerHTML: '', value: '',
      disabled: false, srcObject: undefined, muted: false,
      videoWidth: opts.videoWidth === undefined ? 854 : opts.videoWidth,
      videoHeight: opts.videoHeight === undefined ? 480 : opts.videoHeight,
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      children: [],
      play: () => ({ catch() {} }),
      pause() {},
      appendChild(c) { this.children.push(c); log.appended.push(c); },
      remove() { this.removed = true; },
      querySelector() { return { addEventListener: (_, fn) => { this._onRemove = fn; } }; },
      querySelectorAll: () => [],
      addEventListener() {},
      setAttribute() {}, getAttribute: () => null,
    };
    log.els[id] = e;
    return e;
  }

  function canvas() {
    const calls = { text: [], rects: [], draws: [], encoded: null };
    const ctx = {
      font: '', textBaseline: '', fillStyle: '',
      drawImage: (im, x, y, w, h) => calls.draws.push({ w, h }),
      measureText: t => ({ width: t.length * 9 }),
      fillRect: (x, y, w, h) => calls.rects.push({ x, y, w, h }),
      fillText: (t, x, y) => calls.text.push({ t, x, y }),
    };
    const cv = {
      width: 0, height: 0, calls,
      getContext: () => ctx,
      toDataURL: (type, q) => {
        calls.encoded = { type, q };
        return opts.badCanvas ? 'data:,'
          : 'data:image/jpeg;base64,' + 'P'.repeat(3000);
      },
    };
    log.canvases.push(cv);
    return cv;
  }

  class FakeRecorder {
    constructor(stream, options) {
      log.recOpts.push(options);
      if (opts.recorderThrows) throw new Error('refused');
      this.stream = stream;
      this.state = 'inactive';
      this.ondataavailable = null;
      this.onstop = null;
      log.recorder = this;
    }
    start(slice) { this.state = 'recording'; this.slice = slice; }
    stop() {
      this.state = 'inactive';
      if (this.ondataavailable) {
        this.ondataavailable({ data: { size: opts.blobSize || 1888575 } });
      }
      if (this.onstop) this.onstop();
    }
  }
  FakeRecorder.isTypeSupported = m => log.supported.indexOf(m) >= 0;

  function track(kind) {
    return { kind, stop() { this.stopped = true; } };
  }

  const ctxObj = {
    console,
    setTimeout: (fn, ms) => { log.timeouts.push({ fn, ms }); return log.timeouts.length; },
    clearTimeout: () => {},
    setInterval: (fn, ms) => { log.intervals.push({ fn, ms }); return log.intervals.length; },
    clearInterval: () => {},
    Date: class extends Date {
      constructor(...a) { super(...(a.length ? a : [log.now])); }
      static now() { return log.now; }
    },
    Blob: function (parts, o) {
      this.type = (o || {}).type || '';
      this.size = (parts || []).reduce((n, p) => n + ((p && p.size) || 0), 0);
    },
    URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
    MediaRecorder: opts.noMediaRecorder ? undefined : FakeRecorder,
    navigator: {
      onLine: true,
      geolocation: null,
      serviceWorker: { addEventListener() {} },
      mediaDevices: opts.noMediaDevices ? undefined : {
        getUserMedia: async (c) => {
          log.gum.push(c);
          if (c.audio && opts.micRefused) throw new Error('NotAllowedError');
          if (opts.cameraRefused) throw new Error('NotAllowedError');
          const ts = [track('video')];
          if (c.audio) ts.push(track('audio'));
          return { getTracks: () => ts, getAudioTracks: () => ts.filter(t => t.kind === 'audio'),
                   getVideoTracks: () => ts.filter(t => t.kind === 'video') };
        },
      },
    },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: {
      addEventListener() {},
      getElementById: el,
      querySelectorAll: () => [],
      createElement: tag => (tag === 'canvas' ? canvas()
        : { style: {}, className: '', innerHTML: '', children: [],
            classList: { add() {}, toggle() {} },
            appendChild() {}, remove() { this.removed = true; },
            querySelector() {
              const self = this;
              return { addEventListener: (_, fn) => { self._remove = fn; } };
            } }),
      body: { classList: { toggle() {} } },
    },
    // A poster frame is a data URL with no Blob behind it, so _decodeImage
    // goes through `new Image()` rather than createImageBitmap. This stub has
    // to actually resolve, or the stamp would wait for ever.
    Image: function () {
      const self = this;
      this.width = opts.posterW === undefined ? 854 : opts.posterW;
      this.height = opts.posterH === undefined ? 480 : opts.posterH;
      Object.defineProperty(this, 'src', {
        set(v) {
          self._src = v;
          setImmediate(() => {
            if (opts.decodeFails) { if (self.onerror) self.onerror(new Error('bad')); }
            else if (self.onload) self.onload();
          });
        },
        get() { return self._src; },
      });
    },
    crypto: { randomUUID: () => 'ffffffff-0000-4000-8000-' + String(++uuidN).padStart(12, '0') },
    fetch: () => Promise.reject(new Error('no network in this check')),
    indexedDB: {},
    DB: { saveImage: async () => {} }, API: {},
  };
  ctxObj.window = ctxObj;
  ctxObj.self = ctxObj;
  ctxObj.createImageBitmap = undefined;
  vm.createContext(ctxObj);
  vm.runInContext(fs.readFileSync(APP, 'utf8'), ctxObj, { filename: 'app.js' });
  ctxObj.App = vm.runInContext('App', ctxObj);
  ctxObj._log = log;
  ctxObj.el = el;
  return ctxObj;
}

(async () => {
  // ── the capture constraints ────────────────────────────────────────────────
  const env = makeEnv();
  const App = env.App, L = env._log;

  check(App.VIDEO_MAX_MS === 30000,
        'the cap is 30 seconds (' + App.VIDEO_MAX_MS + ' ms)');
  const nominalMB = (App.VIDEO_BPS + App.AUDIO_BPS) / 8 * 30 / 1048576;
  check(nominalMB > 0.5 && nominalMB < 6,
        'the asked-for bitrate puts 30 s at ' + nominalMB.toFixed(1)
        + ' MB — a few MB, not a film');
  check(App.AUDIO_BPS > 0, 'sound is recorded (' + App.AUDIO_BPS + ' bit/s)');

  await App.startVideo();

  const c = L.gum[0];
  check(L.gum.length === 1 && c && c.audio === true,
        'the camera is opened once, with audio');
  check(c.video && c.video.width.ideal <= 1280 && c.video.height.ideal <= 720,
        'at a modest frame size: ' + c.video.width.ideal + 'x' + c.video.height.ideal);
  check(c.video.frameRate && c.video.frameRate.max <= 30,
        'and a capped frame rate: ' + JSON.stringify(c.video.frameRate));
  check(c.video.facingMode && c.video.facingMode.ideal === 'environment',
        'the back camera is asked for — the engineer is filming the plant');

  const o = L.recOpts[0];
  check(o && o.videoBitsPerSecond === App.VIDEO_BPS
          && o.audioBitsPerSecond === App.AUDIO_BPS,
        'the recorder is given both bitrates: ' + JSON.stringify(o));
  check(o.mimeType === 'video/mp4;codecs=avc1.42E01E,mp4a.40.2',
        'and the first supported mime type: ' + o.mimeType);
  check(L.recorder.state === 'recording' && L.recorder.slice === 1000,
        'recording has started, in one-second slices');

  // ── the hard stop ─────────────────────────────────────────────────────────
  const hard = L.timeouts.filter(t => t.ms === 30000);
  check(hard.length === 1,
        'a stop is scheduled at exactly 30 s, not left to the engineer: '
        + JSON.stringify(L.timeouts.map(t => t.ms)));
  check(L.intervals.length === 1 && L.intervals[0].ms <= 500,
        'the elapsed time is redrawn several times a second');

  // 7.4 s in: the poster is grabbed and the clock shown
  L.now += 7400;
  L.intervals[0].fn();
  check(env.el('video-rec-time').textContent === '0:07',
        'the bar shows the elapsed time: ' + env.el('video-rec-time').textContent);

  // the phone was put in a pocket and the timer fired late — the clip is
  // still a 30-second clip, never a 47-second one
  L.now += 40000;
  hard[0].fn();
  check(L.recorder.state === 'inactive', 'the 30 s timer stops the recorder');

  const clip = App._stagedPhotos[App._stagedPhotos.length - 1];
  check(App._stagedPhotos.length === 1 && clip.kind === 'video',
        'the clip is staged beside the photos, in one list');
  check(clip.durationMs === 30000,
        'and its length is capped at 30 s even though the timer fired at '
        + '47 s: ' + clip.durationMs + ' ms');
  check(/^clip_\d{8}-\d{6}\.mp4$/.test(clip.filename),
        'the file name carries the container and the time: ' + clip.filename);
  check(clip.mime === 'video/mp4' && clip.size > 0,
        'with its mime type and size: ' + clip.mime + ', ' + clip.size + ' bytes');
  check(!!clip.posterDataUrl && clip.posterDataUrl.indexOf('data:image/jpeg') === 0,
        'A POSTER FRAME WAS PRODUCED ON THE PHONE');
  check(env.el('video-rec').style.display === 'none'
        && env.el('video-add-btn').disabled === false,
        'the recorder panel closes and the button comes back');
  check(L.recorder.stream.getTracks().every(t => t.stopped),
        'and the camera is released — the lamp does not stay on');

  const tile = L.appended[L.appended.length - 1];
  check(tile && /video-thumb/.test(tile.className),
        'the staged list shows a clip tile');
  check(tile && tile.innerHTML.indexOf('0:30') >= 0
        && /\d+(\.\d+)?\s?MB/.test(tile.innerHTML),
        'with its length and its size on it: '
        + (tile ? tile.innerHTML.replace(/<[^>]+>/g, ' ').trim() : ''));
  check(tile && tile.innerHTML.indexOf('data:image/jpeg') >= 0,
        'and the poster frame as its picture');
  check(typeof tile._remove === 'function', 'and a × to drop it before saving');
  tile._remove();
  check(App._stagedPhotos.length === 0, 'which removes it from the staged list');

  // ── the poster carries the same caption a photo does ──────────────────────
  const env2 = makeEnv();
  const stamped = await env2.App._stampPoster(
    { posterDataUrl: 'data:image/jpeg;base64,AAAA',
      taken: new Date(2026, 8, 20, 14, 32).getTime(),
      geo: Promise.resolve({ lat: 41.2995123, lon: 69.2401456, acc: 8 }) },
    { project: 'ACWA RIVERSIDE BESS', node: 'Block 33 · LC1 · BESS 3' });
  const cv = env2._log.canvases[env2._log.canvases.length - 1];
  const lines = cv.calls.text.map(t => t.t);
  check(lines.some(l => l.includes('ACWA RIVERSIDE BESS')
                     && l.includes('20.09.2026 14:32')),
        'the poster carries the project, the date and the time: '
        + JSON.stringify(lines[0]));
  check(lines.some(l => l.includes('Block 33') && l.includes('BESS 3')),
        'and the node: ' + JSON.stringify(lines[1]));
  check(lines.some(l => l.includes('41.29951') && l.includes('69.24015')),
        'and the coordinates: ' + JSON.stringify(lines[2]));
  check(cv.calls.rects.length > 0 && cv.calls.encoded.type === 'image/jpeg',
        'burned into a strip and re-encoded as JPEG, exactly like a photo');
  check(String(stamped).indexOf('data:image/jpeg') === 0,
        'and handed back as a JPEG data URL');

  // a poster the phone could not re-encode must not lose the clip
  const env3 = makeEnv({ badCanvas: true });
  const kept = await env3.App._stampPoster(
    { posterDataUrl: 'data:image/jpeg;base64,RAW', taken: Date.now(),
      geo: Promise.resolve(null) }, { project: 'TK', node: 'Block 7' });
  check(kept === 'data:image/jpeg;base64,RAW',
        'a poster that cannot be stamped is kept unstamped, not thrown away');
  const none = await env3.App._stampPoster({ posterDataUrl: null }, {});
  check(none === null, 'and a clip with no poster at all is not an error');

  // ── mime selection and its fallback ──────────────────────────────────────
  const only8 = makeEnv({ supported: ['video/webm;codecs=vp8,opus', 'video/webm'] });
  check(only8.App._videoMime() === 'video/webm;codecs=vp8,opus',
        'a browser without MP4 recording falls through to VP8/Opus: '
        + only8.App._videoMime());
  await only8.App.startVideo();
  check(only8._log.recOpts[0].mimeType === 'video/webm;codecs=vp8,opus',
        'and records in it');
  only8.App.stopVideo();
  check(only8.App._stagedPhotos[0].filename.endsWith('.webm'),
        'with a .webm name: ' + only8.App._stagedPhotos[0].filename);

  const bare = makeEnv({ supported: ['video/webm'] });
  check(bare.App._videoMime() === 'video/webm',
        'a browser that only names the container still records: '
        + bare.App._videoMime());

  // ── nothing supported: the button goes away, and says why ────────────────
  for (const [name, o] of [['no codec', { supported: [] }],
                           ['no MediaRecorder', { noMediaRecorder: true }],
                           ['no camera API', { noMediaDevices: true }]]) {
    const e = makeEnv(o);
    check(e.App._videoMime() === '' || o.noMediaDevices,
          name + ': no recordable format is claimed');
    check(e.App._videoWhyNot() !== '', name + ': the app knows it cannot record');
    e.App._showVideoAvailability();
    check(e.el('video-add-btn').style.display === 'none',
          name + ': THE BUTTON IS HIDDEN');
    const hint = e.el('video-hint');
    check(hint.style.display === '' && hint.textContent.length > 20
          && /Photos still work/.test(hint.textContent),
          name + ': and one plain sentence says why — "'
          + hint.textContent.slice(0, 64) + '…"');
    let threw = false;
    try { await e.App.startVideo(); } catch (_) { threw = true; }
    check(!threw && e._log.gum.length === 0,
          name + ': pressing it anyway does nothing, and does not throw');
  }

  // a browser where recording IS available shows the button and no hint
  const good = makeEnv();
  good.App._showVideoAvailability();
  check(good.el('video-add-btn').style.display === ''
        && good.el('video-hint').style.display === 'none',
        'where recording works, the button is shown and no excuse is made');

  // ── a refused microphone gives a silent clip, not no clip ────────────────
  const mute = makeEnv({ micRefused: true });
  await mute.App.startVideo();
  check(mute._log.gum.length === 2 && mute._log.gum[0].audio === true
        && mute._log.gum[1].audio === false,
        'sound is asked for first, then the camera alone');
  check(/microphone/i.test(mute.el('video-rec-note').textContent),
        'and the bar says the clip will be silent: "'
        + mute.el('video-rec-note').textContent + '"');
  mute.App.stopVideo();
  check(mute.App._stagedPhotos.length === 1,
        'the clip is still recorded — a refused mic must not cost the picture');

  // ── a refused camera is a message, not a broken screen ──────────────────
  const off = makeEnv({ cameraRefused: true });
  await off.App.startVideo();
  check(off.App._stagedPhotos.length === 0 && off.App._rec === null,
        'a refused camera stages nothing and leaves no recorder behind');
  check(/camera/i.test(off.el('create-error').textContent),
        'and says so: "' + off.el('create-error').textContent.slice(0, 60) + '…"');

  // ── reopening the form lets go of a recorder left running ───────────────
  const leak = makeEnv();
  await leak.App.startVideo();
  const stream = leak._log.recorder.stream;
  leak.App._cancelVideo();
  check(leak.App._rec === null && stream.getTracks().every(t => t.stopped),
        'the camera is released when the form is abandoned mid-recording');

  console.log(failures ? 'RESULT FAIL (' + failures + ' check(s))' : 'RESULT PASS');
  process.exit(0);
})().catch(e => {
  console.log('   FAIL  the check itself threw: ' + (e && e.stack || e));
  console.log('RESULT FAIL');
  process.exit(0);
});
