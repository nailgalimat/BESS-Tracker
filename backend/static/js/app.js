/**
 * app.js — BESS Field Log PWA — main application logic
 *
 * Screen IDs:  screen-login | screen-home | screen-create | screen-detail | screen-settings
 * All public methods are properties of the global `App` object (called from HTML onclick=).
 */

const App = {
  // ── State ──────────────────────────────────────────────────────────────────
  _currentCat:     '',
  _stagedPhotos:   [],   // { id, file, dataUrl }
  _currentEntryId: null,
  _tab:            'tasks',
  _taskSeg:        'today',
  // The node being chosen: plant block, level (LC), device. `blocks` holds
  // every block of the same trip (one alarm on 4, 35 and 60 is one form);
  // `block` stays the first of them, because that is the node a photo stamp
  // and a "recent node" are about.
  _node:           { block: null, blocks: [], lc: '', device: '', zone: '' },
  _nodeTab:        'zone',
  _nodeNum:        '',
  _nodeFor:        'create',
  _swReg:          null,
  _geo:            null,   // last known position, for the photo stamp

  // ── Boot ───────────────────────────────────────────────────────────────────
  async init() {
    const token  = localStorage.getItem('access_token');
    const server = localStorage.getItem('server_url');
    // The server is the one this app was installed from — asking a technician
    // for a LAN IP address was the first thing the old login did.
    const urlEl = document.getElementById('login-url');
    if (urlEl && !urlEl.value) urlEl.value = server || window.location.origin;
    if (token && server) {
      await this.goTasks();
      // Background sync on startup
      if (navigator.onLine) this._syncQuiet();
    } else {
      this._show('screen-login');
    }
  },

  // ── A new version of the app ───────────────────────────────────────────────
  // A deployed change used to reach an open phone only by chance: the service
  // worker installed quietly and the old code kept running. Now the phone says
  // so, and reloads only when the engineer taps — after the draft is saved.
  watchForUpdate(reg) {
    if (!reg) return;
    this._swReg = reg;
    const show = () => { this._updateBanner(true); };
    if (reg.waiting) show();
    reg.addEventListener('updatefound', () => {
      const sw = reg.installing;
      if (!sw) return;
      sw.addEventListener('statechange', () => {
        if (sw.state === 'installed' && navigator.serviceWorker.controller) show();
      });
    });
    let reloading = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (reloading) return;
      reloading = true;
      window.location.reload();
    });
  },

  _updateBanner(on) {
    const b = document.getElementById('update-banner');
    if (b) b.style.display = on ? 'flex' : 'none';
  },

  async applyUpdate() {
    try { await this._saveDraft(); } catch (_) {}
    const reg = this._swReg;
    if (reg && reg.waiting) reg.waiting.postMessage({ type: 'SKIP_WAITING' });
    else window.location.reload();
  },

  async checkUpdate() {
    if (!this._swReg) { alert('Updates are handled by the browser here.'); return; }
    try {
      await this._swReg.update();
      if (this._swReg.waiting) this._updateBanner(true);
      else alert('This is the latest version.');
    } catch (_) { alert('Could not check — no network.'); }
  },

  // The half-typed record survives a reload.
  async _saveDraft() {
    if (!document.getElementById('screen-create').classList.contains('active')) return;
    const d = {
      block: this._node.block, lc: this._node.lc, device: this._node.device,
      date: document.getElementById('f-date').value,
      cat: document.getElementById('f-cat').value,
      fault: document.getElementById('f-fault').value,
      desc: document.getElementById('f-desc').value,
      note: document.getElementById('f-note').value,
      ptw: document.getElementById('f-ptw').value,
      sap: document.getElementById('f-sap').value,
      hours: document.getElementById('f-hours').value,
    };
    localStorage.setItem('record_draft', JSON.stringify(d));
  },

  // ── Screen navigation ──────────────────────────────────────────────────────
  _show(screenId) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(screenId).classList.add('active');
    const TAB = { 'screen-tasks': 'tasks', 'screen-home': 'records',
                  'screen-stock': 'stock', 'screen-settings': 'more' };
    const bar = document.getElementById('tabbar');
    const tab = TAB[screenId];
    if (bar) {
      bar.style.display = (screenId === 'screen-login') ? 'none' : 'flex';
      bar.querySelectorAll('.tab').forEach(t =>
        t.classList.toggle('on', t.dataset.tab === (tab || this._tab)));
    }
    if (tab) this._tab = tab;
    document.body.classList.toggle('has-tabs', screenId !== 'screen-login');
    window.scrollTo(0, 0);
  },

  // ── Tabs ───────────────────────────────────────────────────────────────────
  async goTasks() {
    this._show('screen-tasks');
    await this._renderTasks();
  },

  goRecords() {
    this._show('screen-home');
    this._loadTimeline();
  },

  // kept: older buttons and code paths still call goHome()
  goHome() { this.goRecords(); },

  async taskSeg(seg) {
    this._taskSeg = seg;
    document.querySelectorAll('#task-seg button').forEach(b =>
      b.classList.toggle('on', b.dataset.seg === seg));
    await this._renderTasks();
  },

  // A job the office gave to the person logged in here. The server only sends
  // what is theirs, but an admin's phone sees everything, so check the id too.
  _assignedToMe(e) {
    const me = localStorage.getItem('user_id') || '';
    return !!e.assigned_to && (!me || e.assigned_to === me);
  },

  async _renderTasks() {
    const body = document.getElementById('tasks-body');
    if (!body) return;
    const all = (await DB.getAllEntries()).filter(e => !e.deleted_at);
    const today = _today();
    const weekAgo = _today(-7);
    // A job planned for next Tuesday is this week's work as much as one from
    // last Tuesday, so Week reaches forward as well as back.
    const weekAhead = _today(7);
    const open = all.filter(e => ['open', 'in_progress', 'needs_visit'].includes(e.status || ''));
    const when = e => e.due_date || e.log_date || '';
    let rows;
    if (this._taskSeg === 'today') rows = open.filter(e => when(e) === today);
    else if (this._taskSeg === 'week') {
      rows = open.filter(e => when(e) >= weekAgo && when(e) <= weekAhead);
    } else rows = open;
    // what the office is waiting for comes first, oldest due date at the top
    rows.sort((a, b) => (this._assignedToMe(b) ? 1 : 0) - (this._assignedToMe(a) ? 1 : 0)
                        || when(a).localeCompare(when(b)));

    const events = (await DB.getAllFieldEvents()).filter(e => e.sync_status !== 'synced');

    // The office's action items given to this person. They are NOT plant work
    // — no block, no hours — so they are picked out here and drawn as their
    // own kind of card below the jobs. An item with no target date is open
    // work rather than today's work, so Today leaves it out.
    const acts = (await DB.getAllActions()).filter(a => !a.deleted_at);
    const openActs = acts.filter(a => !['done', 'dropped'].includes(a.status || ''));
    const actRows = this._taskSeg === 'open' ? openActs : openActs.filter(a => {
      if (!a.due_date) return false;
      return this._taskSeg === 'today' ? a.due_date <= today
                                       : a.due_date <= weekAhead;
    });

    let html = '';
    if (events.length) {
      html += `<div class="task-note warn">${events.length} downtime record(s) still
               waiting to send · <b>Records</b> shows why</div>`;
    }
    if (!rows.length && !actRows.length) {
      html += `<div class="empty">Nothing open ${this._taskSeg === 'today' ? 'today' : ''}.
               <br>Jobs the office gives you appear here after a sync.
               <br>Tap ＋ to write a record yourself.</div>`;
    }
    for (const e of rows) {
      const node = e.plant_block ? ('Block ' + e.plant_block) : 'No block';
      const mine = this._assignedToMe(e);
      const from = mine
        ? `<div class="tcard-from">From ${_esc(e.assigned_by || 'the office')}${
            e.due_date ? ' · due ' + _esc(e.due_date) : ''}</div>`
        : '';
      html += `<div class="tcard${mine ? ' assigned' : ''}">
        <div class="tcard-top"><span class="chip ${e.status === 'needs_visit' ? 'crit' : 'warn'}">
          ${_esc(_statusLabel(e.status))}</span>${mine
            ? '<span class="chip job">Assigned</span>' : ''
          }<span class="hint">${_esc(when(e))}</span></div>
        <div class="tcard-blk">${_esc(node)}${e.node_lc ? ' · ' + _esc(e.node_lc) : ''}${
          e.node_device ? ' · ' + _esc(e.node_device) : ''}</div>
        <div class="tcard-meta">${_esc(e.fault_name || e.description || '')}</div>
        ${from}
        <button class="btn ${mine ? 'btn-primary' : 'btn-outline'} btn-block"
                onclick="App.${mine ? `openTask('${e.id}')` : `_showDetail('${e.id}')`}">
          ${mine ? 'Open the job' : 'Open'}</button>
      </div>`;
    }
    for (const a of actRows) {
      const late = a.due_date && a.due_date < today;
      html += `<div class="tcard action">
        <div class="tcard-top"><span class="chip ${late ? 'crit' : 'pend'}">
          Action item</span><span class="hint">${_esc(a.due_date || 'no date')}</span></div>
        <div class="tcard-blk">${_esc(a.topic || '(no topic)')}</div>
        <div class="tcard-meta">${_esc((a.todo || a.description || '')
          .split('\n')[0])}</div>
        <div class="tcard-from">No block — office action${
          a.assigned_by ? ' · from ' + _esc(a.assigned_by) : ''}</div>
        <button class="btn btn-outline btn-block"
                onclick="App.openAction('${a.uuid}')">Open the action</button>
      </div>`;
    }

    // The PM checklists the office planned for this project — the other half
    // of a technician's day, and until v15 they were only on paper.
    const tpls = await DB.getMeta('checklist_templates', {}) || {};
    const cls = (await DB.getAllChecklists()).filter(r => !r.deleted_at);
    const openCls = cls.filter(r => {
      const p = _clCount(r, tpls[r.template_uuid]);
      return !p.total || p.done < p.total;
    });
    if (cls.length) {
      html += `<div class="tcard">
        <div class="tcard-top"><span class="chip ${openCls.length ? 'warn' : 'ok'}">
          PM checklists</span><span class="hint">${cls.length} assigned</span></div>
        <div class="tcard-blk">${openCls.length
          ? openCls.length + ' still to finish'
          : 'all finished'}</div>
        <div class="tcard-meta">${_esc(openCls.slice(0, 4)
          .map(r => 'Block ' + (r.plant_block || '—')).join(' · '))}</div>
        <button class="btn btn-outline btn-block" onclick="App.goChecklists()">Open checklists</button>
      </div>`;
    } else {
      html += `<p class="hint-line">Jobs and PM checklists planned in the office
               appear here after a sync — checklists also under
               <b>More → PM checklists</b>.</p>`;
    }
    body.innerHTML = html;
  },

  // ── One assigned job ───────────────────────────────────────────────────────
  // The office writes the record and gives it to a technician; the technician
  // fills that same record in. Anything else leaves the office holding a job
  // with no answer and the phone holding an answer to no job.
  _task: null,

  async openTask(entryId) {
    const e = await DB.getEntry(entryId);
    if (!e) return;
    this._task = e;
    const node = e.plant_block ? ('Block ' + e.plant_block) : 'No block';
    document.getElementById('task-title').textContent = node;
    document.getElementById('task-node').textContent = node
      + (e.node_lc ? ' · ' + e.node_lc : '') + (e.node_device ? ' · ' + e.node_device : '');
    document.getElementById('task-from').textContent =
      'From ' + (e.assigned_by || 'the office')
      + (e.due_date ? ' · due ' + e.due_date : '')
      + (e.ptw_no ? ' · PTW ' + e.ptw_no : '');
    document.getElementById('task-ask').textContent =
      e.fault_name || e.description || '';
    // What the office asked for is the description until the work is done;
    // pre-filling it would send it back as "what was done".
    document.getElementById('task-desc').value =
      (e.status === 'done' ? (e.description || '') : '');
    document.getElementById('task-start').value = e.time_from || '';
    document.getElementById('task-end').value   = e.time_to || '';
    document.getElementById('task-hours').value = e.hours || 0;
    document.getElementById('task-ptw').value   = e.ptw_no || '';
    document.getElementById('task-note').value  = e.internal_note || '';
    document.getElementById('task-error').style.display = 'none';
    this._taskBanner('', '');
    this._show('screen-task');
  },

  _taskBanner(text, kind) {
    const b = document.getElementById('task-banner');
    if (!b) return;
    b.textContent = text || '';
    b.className = 'sync-bar' + (kind === 'err' ? ' sync-bar-conflict'
                              : kind === 'warn' ? ' sync-bar-warn' : '');
    b.style.display = text ? 'block' : 'none';
  },

  recalcTaskHours() {
    const a = document.getElementById('task-start').value;
    const b = document.getElementById('task-end').value;
    if (!a || !b) return;
    const [h1, m1] = a.split(':').map(Number);
    const [h2, m2] = b.split(':').map(Number);
    let mins = (h2 * 60 + m2) - (h1 * 60 + m1);
    if (mins < 0) mins += 24 * 60;
    document.getElementById('task-hours').value = String(Math.round(mins / 60 * 100) / 100);
  },

  /** Write what the phone holds back into the office's own record. `done`
      closes it; otherwise it stays open with the progress on it. */
  async _storeTask(done) {
    const e = this._task;
    if (!e) return null;
    const desc = document.getElementById('task-desc').value.trim();
    const errEl = document.getElementById('task-error');
    if (done && !desc) {
      _showErr(errEl, 'Write what was done — the office reads this line.');
      return null;
    }
    errEl.style.display = 'none';
    const hours = parseFloat(document.getElementById('task-hours').value) || null;
    // A PM job keeps saying PM in the customer's line: that is what keeps it
    // out of section 3.2, which reports PM as hours instead.
    const wasPm = e.category === 'maintenance'
                  || /PM|preventive/i.test(e.description || '')
                  || /PM|preventive/i.test(e.fault_name || '');
    let text = desc || e.description || '';
    if (text && wasPm && !/PM|preventive/i.test(text)) text = 'PM: ' + text;
    const next = Object.assign({}, e, {
      description:   text,
      status:        done ? 'done' : (e.status || 'open'),
      time_from:     document.getElementById('task-start').value || '',
      time_to:       document.getElementById('task-end').value || '',
      hours:         hours,
      ptw_no:        document.getElementById('task-ptw').value.trim(),
      internal_note: document.getElementById('task-note').value.trim(),
      // The same id, so the push updates the office's record instead of
      // creating a second one. The assignment itself is the office's: it
      // travels back untouched and the server ignores any change to it.
      sync_status:   'local',
      updated_at:    new Date().toISOString(),
    });
    await DB.saveEntry(next);
    this._task = next;
    return next;
  },

  async saveTask() {
    if (!await this._storeTask(false)) return;
    this._taskBanner('✓ Saved on the phone — it goes with the next sync.', '');
    if (navigator.onLine) this._syncQuiet();
  },

  async completeTask() {
    if (!await this._storeTask(true)) return;
    if (navigator.onLine) {
      this._taskBanner('🔄 Sending…', '');
      try { await this._doSync(); } catch (_) {}
    }
    await this.goTasks();
  },

  async goCreate(kind, keep) {
    this._stagedPhotos = [];
    document.getElementById('photo-preview').innerHTML = '';
    document.getElementById('f-date').value    = _today();
    document.getElementById('f-cat').value     = kind || 'fault';
    document.getElementById('f-loc').value     = '';
    document.getElementById('f-serial').value  = '';
    document.getElementById('f-tags').value    = '';
    if (!keep) {
      document.getElementById('f-desc').value  = '';
      document.getElementById('f-fault').value = '';
      document.getElementById('f-note').value  = '';
      document.getElementById('f-ptw').value   = '';
      document.getElementById('f-sap').value   = '';
      document.getElementById('f-parts').value = '';
      document.getElementById('f-start').value = '';
      document.getElementById('f-end').value   = '';
      document.getElementById('f-hours').value = '0';
      this._node = { block: null, blocks: [], lc: '', device: '', zone: '' };
      document.getElementById('f-blocks').value = '';
    }
    this.pickStatus(kind === 'maintenance' ? 'done' : 'done');
    this._showNode();
    this.onCategory();
    document.getElementById('create-error').style.display = 'none';
    document.getElementById('create-title').textContent =
      kind === 'maintenance' ? 'PM record' : 'Work record';
    this._refreshFaultList();
    await this._fillProjectSelect();
    this._show('screen-create');
  },

  onCategory() {
    const cat = document.getElementById('f-cat').value;
    const pm = (cat === 'maintenance');
    document.getElementById('f-pm-group').style.display = pm ? 'block' : 'none';
    document.getElementById('f-fault-group').style.display = pm ? 'none' : 'block';
    if (pm && !document.getElementById('f-start').value) {
      const now = new Date();
      document.getElementById('f-start').value =
        String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');
    }
  },

  pickStatus(st) {
    document.getElementById('f-status').value = st;
    document.querySelectorAll('#f-status-seg button').forEach(b =>
      b.classList.toggle('on', b.dataset.st === st));
  },

  bumpHours(d) {
    const el = document.getElementById('f-hours');
    let v = (parseFloat(el.value) || 0) + d;
    if (v < 0) v = 0;
    if (v > 24) v = 24;
    el.value = String(Math.round(v * 100) / 100);
  },

  recalcHours() {
    const a = document.getElementById('f-start').value;
    const b = document.getElementById('f-end').value;
    if (!a || !b) return;
    const [h1, m1] = a.split(':').map(Number);
    const [h2, m2] = b.split(':').map(Number);
    let mins = (h2 * 60 + m2) - (h1 * 60 + m1);
    if (mins < 0) mins += 24 * 60;
    document.getElementById('f-hours').value = String(Math.round(mins / 60 * 100) / 100);
    this._hoursHint('End − start = ' + (Math.round(mins / 60 * 100) / 100) + ' h · ');
  },

  // ── Node picker ───────────────────────────────────────────────────────────
  // "9zona 7block 2bsc" typed into a free-text location could never become a
  // plant block, so those records never reached section 3.2. The node is
  // chosen now: recent, zone -> block, or the number itself.
  _showNode() {
    const t = document.getElementById('f-node-text');
    if (!t) return;
    const n = this._node;
    const many = (n.blocks || []).length > 1;
    const where = many
      ? (n.blocks.length + ' blocks · ' + this._formatBlocks(n.blocks))
      : ('Block ' + n.block + (n.zone ? ' · ' + n.zone : ''));
    t.textContent = n.block
      ? (where + (n.lc ? ' · ' + n.lc : '') + (n.device ? ' · ' + n.device : ''))
      : 'Choose the block';
    t.classList.toggle('chosen', !!n.block);
    this._hoursHint('');
  },

  /** The line under the PM hours. On several blocks the hours are charged to
      EACH block, exactly as record_pm splits them on the desktop — that is
      real money in the availability figure, so the form says it out loud. */
  _hoursHint(prefix) {
    const el = document.getElementById('f-hours-hint');
    if (!el) return;
    const n = (this._node.blocks || []).length;
    const tail = n > 1
      ? 'these hours are charged to EACH of the ' + n + ' blocks ('
        + this._formatBlocks(this._node.blocks) + ').'
      : 'one PM record per block per day.';
    el.textContent = prefix
      ? prefix + tail
      : tail.charAt(0).toUpperCase() + tail.slice(1);
  },

  async _project() {
    const projects = await DB.getMeta('projects', []);
    const pid = document.getElementById('f-proj').value
             || localStorage.getItem('last_project_id');
    return (projects || []).find(p => String(p.id) === String(pid))
        || (projects || [])[0] || null;
  },

  _zonesOf(project) {
    if (!project) return [];
    try {
      const z = JSON.parse(project.zones || '[]');
      if (Array.isArray(z) && z.length) return z;
    } catch (_) {}
    return [];
  },

  async openNodePicker(forWhat) {
    this._nodeFor = forWhat || 'create';
    const p = await this._project();
    this._np = { project: p, zones: this._zonesOf(p), blocks: (p && p.num_blocks) || 0 };
    if (!this._np.zones.length) this._nodeTab = this._np.blocks ? 'zone' : 'number';
    // reopening the picker shows what is already on the form, not a blank slate
    // (and never a number half-typed on the way out of it last time)
    this._nodePick = new Set(this._node.blocks && this._node.blocks.length
      ? this._node.blocks : (this._node.block ? [this._node.block] : []));
    this._nodeNum = '';
    this._renderNode();
    this._show('screen-node');
  },

  /** Every block the form would take right now: what was tapped, plus the
      number being typed on the Number tab (which is committed on Use, so a
      single typed block still needs exactly the taps it always did). */
  _nodeChosen() {
    const out = new Set(this._nodePick || []);
    if (this._nodeTab === 'number') {
      const n = parseInt(this._nodeNum, 10);
      const max = (this._np && this._np.blocks) || 9999;
      if (n >= 1 && n <= max) out.add(n);
    }
    return [...out].sort((a, b) => a - b);
  },

  closeNodePicker() { this._show('screen-create'); },

  nodeTab(tab) {
    this._nodeTab = tab;
    this._renderNode();
  },

  _recentNodes() {
    try { return JSON.parse(localStorage.getItem('recent_nodes') || '[]'); }
    catch (_) { return []; }
  },

  _rememberNode(n) {
    const list = this._recentNodes().filter(
      x => !(x.block === n.block && x.lc === n.lc && x.device === n.device));
    list.unshift({ block: n.block, lc: n.lc, device: n.device, zone: n.zone });
    localStorage.setItem('recent_nodes', JSON.stringify(list.slice(0, 5)));
  },

  _zoneLabel(block) {
    for (const z of ((this._np && this._np.zones) || [])) {
      if (block >= z[1] && block <= z[2]) return 'Z' + z[0] + '/B' + (block - z[1] + 1);
    }
    return '';
  },

  _renderNode() {
    const np = this._np || { zones: [], blocks: 0 };
    const picked = this._nodePick || (this._nodePick = new Set());
    document.querySelectorAll('#np-tabs button').forEach(b =>
      b.classList.toggle('on', b.dataset.tab === this._nodeTab));
    document.getElementById('np-recent').style.display = this._nodeTab === 'recent' ? 'block' : 'none';
    document.getElementById('np-zone').style.display   = this._nodeTab === 'zone'   ? 'block' : 'none';
    document.getElementById('np-number').style.display = this._nodeTab === 'number' ? 'block' : 'none';

    // recent
    const rec = this._recentNodes();
    document.getElementById('np-recent').innerHTML = rec.length
      ? rec.map(r => `<button class="sheet-item" onclick="App.pickRecent(${r.block},'${r.lc || ''}','${r.device || ''}')">
            <b>Block ${r.block}${r.lc ? ' · ' + _esc(r.lc) : ''}${r.device ? ' · ' + _esc(r.device) : ''}</b>
            <span>${_esc(r.zone || '')}</span></button>`).join('')
      : '<p class="hint-line">Nodes you use appear here.</p>';

    // zone -> block
    const zg = document.getElementById('np-zones');
    const bg = document.getElementById('np-blocks');
    if (np.zones.length) {
      const cur = this._node.zoneIdx || np.zones[0][0];
      zg.innerHTML = np.zones.map(z =>
        `<button class="tile${z[0] === cur ? ' on' : ''}" onclick="App.pickZone(${z[0]})">
           <b>Z${z[0]}</b><span>${z[1]}–${z[2]}</span></button>`).join('');
      const z = np.zones.find(x => x[0] === cur) || np.zones[0];
      let cells = '';
      for (let b = z[1]; b <= z[2]; b++) {
        cells += `<button class="tile${picked.has(b) ? ' on' : ''}" onclick="App.pickBlock(${b})">
            <b>B${b - z[1] + 1}</b><span>${b}</span></button>`;
      }
      bg.innerHTML = cells;
      document.getElementById('np-block-label').textContent = 'Block in zone ' + z[0];
    } else if (np.blocks) {
      zg.innerHTML = '<p class="hint-line">This project has no zones mirrored yet.</p>';
      let cells = '';
      for (let b = 1; b <= np.blocks; b++) {
        cells += `<button class="tile${picked.has(b) ? ' on' : ''}" onclick="App.pickBlock(${b})">
            <b>${b}</b></button>`;
      }
      bg.innerHTML = cells;
    } else {
      zg.innerHTML = '';
      bg.innerHTML = '<p class="hint-line">No block count yet — sync the desktop once, '
                   + 'or use the Number tab.</p>';
    }

    // number keypad — the one block already chosen is shown, several are not
    // (the keypad types one at a time; "Add" puts it with the others)
    const num = this._nodeNum || (picked.size === 1 ? String([...picked][0]) : '');
    document.getElementById('np-num').textContent = num || '—';
    const n = parseInt(num, 10);
    const max = np.blocks || 9999;
    const okNum = n >= 1 && n <= max;
    document.getElementById('np-num-sub').textContent = okNum
      ? ('Block ' + n + (this._zoneLabel(n) ? ' · ' + this._zoneLabel(n) : ''))
      : ('Enter 1–' + (np.blocks || '…'));
    const add = document.getElementById('np-add');
    if (add) add.disabled = !(parseInt(this._nodeNum, 10) >= 1
                              && parseInt(this._nodeNum, 10) <= max);
    const pad = document.getElementById('np-keypad');
    if (!pad.dataset.built) {
      pad.innerHTML = ['1','2','3','4','5','6','7','8','9','C','0','⌫']
        .map(k => `<button onclick="App.nodeKey('${k}')">${k}</button>`).join('');
      pad.dataset.built = '1';
    }

    // level + device
    const lcs = ['LC1', 'LC2', 'Whole block'];
    document.getElementById('np-lc').innerHTML = lcs.map(l =>
      `<button class="pchip${this._node.lc === l ? ' on' : ''}" onclick="App.pickLc('${l}')">${l}</button>`).join('');
    const devs = ['PCS 1', 'PCS 2', 'PCS 3', 'PCS 4', 'BESS 1', 'BESS 2', 'BESS 3',
                  'BESS 4', 'LC cabinet', 'MV station'];
    document.getElementById('np-device').innerHTML = devs.map(d =>
      `<button class="pchip${this._node.device === d ? ' on' : ''}" onclick="App.pickDevice('${d}')">${d}</button>`).join('');

    // what is chosen so far, and the two shortcuts for a whole-plant job
    const list = this._nodeChosen();
    const tail = (this._node.device || this._node.lc)
      ? ' · ' + (this._node.device || this._node.lc) : '';
    const sum = document.getElementById('np-picked');
    if (sum) {
      sum.textContent = list.length > 1
        ? (list.length + ' blocks · ' + this._formatBlocks(list)
           + ' — one record each, same text')
        : 'Tap more blocks for the same work — one record is written per block.';
      sum.classList.toggle('on', list.length > 1);
    }
    const none = document.getElementById('np-none');
    if (none) none.disabled = !list.length;

    const use = document.getElementById('np-use');
    use.disabled = !list.length;
    use.textContent = !list.length ? 'Choose a block'
      : list.length === 1
        ? ('Use Block ' + list[0]
           + (this._zoneLabel(list[0]) ? ' · ' + this._zoneLabel(list[0]) : '') + tail)
        : ('Use ' + list.length + ' blocks · ' + this._formatBlocks(list) + tail);
  },

  pickZone(z) { this._node.zoneIdx = z; this._renderNode(); },
  // A tap adds a block, a second tap on it takes it back: the same tap that
  // used to choose the one block still chooses exactly that one.
  pickBlock(b) {
    const picked = this._nodePick || (this._nodePick = new Set());
    if (picked.has(b)) picked.delete(b);
    else picked.add(b);
    this._nodeNum = '';
    this._renderNode();
  },

  nodeAll() {
    const n = (this._np && this._np.blocks) || 0;
    if (!n) return;
    this._nodePick = new Set();
    for (let b = 1; b <= n; b++) this._nodePick.add(b);
    this._nodeNum = '';
    this._renderNode();
  },

  nodeNone() {
    this._nodePick = new Set();
    this._nodeNum = '';
    this._renderNode();
  },

  pickLc(l) { this._node.lc = (this._node.lc === l ? '' : l); this._renderNode(); },
  pickDevice(d) { this._node.device = (this._node.device === d ? '' : d); this._renderNode(); },
  // A recent node is one node: it replaces the selection instead of adding to it.
  pickRecent(b, lc, dev) {
    this._nodePick = new Set([b]);
    this._node.lc = lc || ''; this._node.device = dev || '';
    this._nodeNum = '';
    this.useNode();
  },

  nodeKey(k) {
    if (k === 'C') this._nodeNum = '';
    else if (k === '⌫') this._nodeNum = (this._nodeNum || '').slice(0, -1);
    else this._nodeNum = ((this._nodeNum || '') + k).slice(0, 4);
    this._renderNode();
  },

  // Keep the typed block and start typing the next one — the Number tab's way
  // of naming several blocks, for a project whose zones are not mirrored yet.
  nodeAddNumber() {
    const n = parseInt(this._nodeNum, 10);
    const max = (this._np && this._np.blocks) || 9999;
    if (!(n >= 1 && n <= max)) return;
    (this._nodePick || (this._nodePick = new Set())).add(n);
    this._nodeNum = '';
    this._renderNode();
  },

  useNode() {
    const list = this._nodeChosen();
    if (!list.length) return;
    // The first block leads: it is the node the photos are stamped with and
    // the one remembered as "recent". The rest get their own records.
    this._node.blocks = list;
    this._node.block = list[0];
    this._node.zone = this._zoneLabel(list[0]);
    this._nodePick = new Set(list);
    this._nodeNum = '';
    this._rememberNode(this._node);
    document.getElementById('f-block').value  = String(list[0]);
    document.getElementById('f-blocks').value = list.join(',');
    document.getElementById('f-lc').value     = this._node.lc || '';
    document.getElementById('f-device').value = this._node.device || '';
    this._showNode();
    this._show('screen-create');
  },

  // The same defect on the next node: keep the text, drop the node.
  repeatOnAnotherNode() {
    const cat = document.getElementById('f-cat').value;
    this._node = { block: null, blocks: [], lc: this._node.lc, device: '', zone: '' };
    this._nodePick = new Set();
    // the node is being chosen again: the old list must not write its records
    document.getElementById('f-blocks').value = '';
    this.goCreate(cat, true);
    this.openNodePicker('create');
  },

  async _fillProjectSelect() {
    const sel      = document.getElementById('f-proj');
    const projects = await DB.getMeta('projects', []);
    sel.innerHTML  = '<option value="">— No project —</option>';
    for (const p of projects) {
      const opt = document.createElement('option');
      opt.value       = p.id;
      opt.textContent = p.name;
      sel.appendChild(opt);
    }
    // Preselect the last used project (field techs usually stay on one site)
    const last = localStorage.getItem('last_project_id');
    if (last && projects.some(p => String(p.id) === last)) sel.value = last;
  },

  // ── Add sheet (choose what to create) ───────────────────────────────────────
  openAddSheet()  { document.getElementById('add-sheet').style.display = 'flex'; },
  closeAddSheet() { document.getElementById('add-sheet').style.display = 'none'; },

  // ── Custom fault / alarm suggestions (remembered per device) ─────────────────
  _refreshFaultList() {
    const dl = document.getElementById('fault-list');
    if (!dl) return;
    [...dl.querySelectorAll('option[data-custom]')].forEach(o => o.remove());
    let customs = [];
    try { customs = JSON.parse(localStorage.getItem('custom_faults') || '[]'); } catch (_) {}
    const known = new Set([...dl.options].map(o => o.value.toLowerCase()));
    for (const f of customs) {
      if (f && !known.has(f.toLowerCase())) {
        const o = document.createElement('option');
        o.value = f; o.setAttribute('data-custom', '1');
        dl.appendChild(o); known.add(f.toLowerCase());
      }
    }
  },

  _rememberFault(fault) {
    if (!fault) return;
    const dl = document.getElementById('fault-list');
    const known = new Set([...(dl ? dl.options : [])].map(o => o.value.toLowerCase()));
    if (known.has(fault.toLowerCase())) return;   // already a base or saved suggestion
    let customs = [];
    try { customs = JSON.parse(localStorage.getItem('custom_faults') || '[]'); } catch (_) {}
    if (!customs.some(x => x.toLowerCase() === fault.toLowerCase())) {
      customs.push(fault);
      try { localStorage.setItem('custom_faults', JSON.stringify(customs)); } catch (_) {}
    }
  },

  goSettings() {
    const user   = localStorage.getItem('username') || '—';
    const server = localStorage.getItem('server_url') || '—';
    document.getElementById('s-user').textContent   = user;
    document.getElementById('s-server').textContent = server;
    this._fillSettingsStatus();
    this._show('screen-settings');
  },

  async _fillSettingsStatus() {
    const v = document.getElementById('s-version');
    if (v) v.textContent = (window.APP_VERSION || 'v13');
    const last    = await DB.getMeta('last_sync_at', null);
    const pending = (await DB.getPendingEntries()).length
                  + (await DB.getPendingWriteoffs()).length
                  + (await DB.getPendingFieldEvents()).length
                  + (await DB.getPendingChecklists()).length;
    document.getElementById('s-lastsync').textContent = last ? last.slice(0, 16).replace('T', ' ') : '—';
    document.getElementById('s-pending').textContent  = pending;
  },

  // ── Auth ───────────────────────────────────────────────────────────────────
  async login() {
    const url  = document.getElementById('login-url').value.trim().replace(/\/$/, '');
    const user = document.getElementById('login-user').value.trim();
    const pass = document.getElementById('login-pass').value;
    const errEl = document.getElementById('login-error');

    if (!url || !user || !pass) {
      _showErr(errEl, 'Fill in all three fields.');
      return;
    }

    const btn = document.getElementById('login-btn');
    btn.disabled    = true;
    btn.textContent = 'Logging in…';
    errEl.style.display = 'none';

    try {
      const data = await API.login(url, user, pass);

      localStorage.setItem('server_url',    url);
      localStorage.setItem('access_token',  data.access_token);
      localStorage.setItem('refresh_token', data.refresh_token);
      localStorage.setItem('username',      data.user.username);
      // the id the office assigns jobs to — without it the phone cannot tell
      // its own jobs from the ones an admin account can see
      if (data.user.id) localStorage.setItem('user_id', data.user.id);

      if (!localStorage.getItem('device_id')) {
        // Generate a stable device identifier
        const id = 'mobile-' + _uuid();
        localStorage.setItem('device_id', id);
      }

      this._show('screen-home');
      await this._loadTimeline();
      if (navigator.onLine) this._syncQuiet();

    } catch (e) {
      _showErr(errEl, e.message);
    } finally {
      btn.disabled    = false;
      btn.textContent = 'Log In';
    }
  },

  async logout() {
    if (!confirm('Log out?')) return;
    try { await API.logout(); } catch (_) {}
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    this._show('screen-login');
  },

  // ── Timeline ───────────────────────────────────────────────────────────────
  async _loadTimeline() {
    const tl = document.getElementById('timeline');
    tl.innerHTML = '<div class="loading">Loading…</div>';
    this._refreshPendingBadge();   // every path here can change the count
    try {
      const projects = await DB.getMeta('projects', []);
      _PROJECT_NAMES = {};
      for (const p of projects) _PROJECT_NAMES[p.id] = p.name;

      let entries = await DB.getAllEntries();
      entries = entries.filter(e => !e.deleted_at);
      if (this._currentCat) entries = entries.filter(e => e.category === this._currentCat);
      entries.sort((a, b) => (b.log_date || '').localeCompare(a.log_date || '') || (b.created_at || '').localeCompare(a.created_at || ''));

      if (!entries.length) {
        tl.innerHTML = '<div class="empty">No entries yet.<br>Tap ＋ to add one.</div>';
        return;
      }

      // Group by date
      const byDate = new Map();
      for (const e of entries) {
        if (!byDate.has(e.log_date)) byDate.set(e.log_date, []);
        byDate.get(e.log_date).push(e);
      }

      let html = '';
      for (const [date, group] of byDate) {
        html += `<div class="date-header">${_fmtDate(date)}</div>`;
        for (const entry of group) html += _renderCard(entry);
      }
      tl.innerHTML = html;

      // Attach events
      tl.querySelectorAll('.log-card').forEach(card => {
        card.addEventListener('click', e => {
          if (!e.target.closest('.card-actions')) {
            this._showDetail(card.dataset.id);
          }
        });
      });

      tl.querySelectorAll('.del-btn').forEach(btn => {
        btn.addEventListener('click', async e => {
          e.stopPropagation();
          if (confirm('Delete this entry?')) {
            await this._deleteById(btn.dataset.id);
            await this._loadTimeline();
          }
        });
      });

    } catch (err) {
      tl.innerHTML = `<div class="empty error">Error: ${_esc(err.message)}</div>`;
    }
  },

  filterCat(btn, cat) {
    this._currentCat = cat;
    document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    this._loadTimeline();
  },

  // ── Create / Save ──────────────────────────────────────────────────────────
  /** Save the record. One tap, one record: the button is dead until this
      returns. Stamping a photo and waiting up to 10 s for a fix made the gap
      long enough that a second tap was normal, and it wrote a second record —
      and a second line in the customer's 3.2. */
  async saveEntry() {
    if (this._saving) return;
    this._saving = true;
    const btn = document.getElementById('save-btn');
    if (btn) btn.disabled = true;
    try {
      await this._saveEntry();
    } finally {
      this._saving = false;
      if (btn) btn.disabled = false;
    }
  },

  async _saveEntry() {
    const date   = document.getElementById('f-date').value;
    const cat    = document.getElementById('f-cat').value;
    const desc   = document.getElementById('f-desc').value.trim();
    const block  = parseInt(document.getElementById('f-block').value, 10) || null;
    // Every block of this trip. The same alarm on 4, 35 and 60 is one form and
    // one set of photos, but one record per block: that is what the customer's
    // report (a line per block) and the desktop's Work journal are made of.
    const blocks = ((document.getElementById('f-blocks') || {}).value || '')
      .split(',').map(s => parseInt(s, 10)).filter(b => b >= 1);
    if (!blocks.length && block) blocks.push(block);
    const lc     = document.getElementById('f-lc').value || '';
    const device = document.getElementById('f-device').value || '';
    const ptw    = document.getElementById('f-ptw').value.trim();
    const note   = document.getElementById('f-note').value.trim();
    const tFrom  = document.getElementById('f-start').value || '';
    const tTo    = document.getElementById('f-end').value || '';
    const hours  = parseFloat(document.getElementById('f-hours').value) || null;
    const loc    = document.getElementById('f-loc').value.trim();
    const ser    = document.getElementById('f-serial').value.trim();
    const fault  = document.getElementById('f-fault').value.trim();
    const status = document.getElementById('f-status').value;
    const sap    = document.getElementById('f-sap').value.trim();
    const parts  = document.getElementById('f-parts').value.trim();
    const tagStr = document.getElementById('f-tags').value;
    const projVal = document.getElementById('f-proj').value;
    const errEl  = document.getElementById('create-error');

    if (!date || !desc) {
      _showErr(errEl, 'Enter the date and what was done.');
      return;
    }
    // A record with no plant block cannot reach the customer's report — it is
    // the single most common reason a phone record is lost on the desktop.
    if (!block) {
      _showErr(errEl, 'Choose the node — the plant block is what the report needs.');
      return;
    }
    // "All blocks" is one tap, and on this plant that is 70 records. Saying
    // the number out loud is the difference between a day's work and a mess
    // the office has to clean up by hand.
    if (blocks.length > 5 && !confirm(
        'This writes ' + blocks.length + ' records, one per block ('
        + blocks.slice(0, 6).join(', ') + (blocks.length > 6 ? '…' : '') + ').\n\n'
        + 'Continue?')) return;
    if (cat === 'maintenance') {
      if (!(hours > 0)) { _showErr(errEl, 'Enter the PM hours (more than 0).'); return; }
      if (hours > 24) { _showErr(errEl, 'PM hours are per block per day — at most 24.'); return; }
      // the hours are charged to each block, so say how many are about to be
      if (hours > 12 && !confirm(hours + ' h of PM on '
          + (blocks.length > 1 ? 'EACH of ' + blocks.length + ' blocks' : 'one block')
          + ' in one day — is that right?')) return;
    }

    const projectId = projVal ? parseInt(projVal, 10) : null;
    if (projVal) localStorage.setItem('last_project_id', projVal);
    else         localStorage.removeItem('last_project_id');

    const tags = tagStr.split(',').map(t => t.trim()).filter(Boolean);
    const now  = new Date().toISOString();
    const id   = _uuid();        // the first block's record: it carries the photos

    const entry = {
      id,
      project_id:       projectId,
      category:         cat,
      log_date:         date,
      description:      (cat === 'maintenance' && !/PM|preventive/i.test(desc))
                          ? ('PM: ' + desc) : desc,
      fault_name:       fault  || '',
      status:           status || '',
      sap_ticket:       sap    || '',
      spare_parts:      parts  || '',
      site_location:    loc    || null,
      equipment_serial: ser    || null,
      plant_block:         block,
      node_lc:             lc,
      node_device:         device,
      ptw_no:              ptw,
      internal_note:       note,
      time_from:           tFrom,
      time_to:             tTo,
      hours:               hours,
      availability_impact: 'none',
      tags,
      sync_status:      'local',
      version:          1,
      created_at:       now,
      updated_at:       now,
      deleted_at:       null,
      image_ids:        [],
    };

    // Save staged photos to IndexedDB, each with its caption burned in
    const projName = projVal
      ? ((document.getElementById('f-proj').selectedOptions[0] || {}).textContent || '').trim()
      : '';
    // The stamp names the node the photo was taken at — the first block.
    const nodeText = ['Block ' + blocks[0], lc, device].filter(Boolean).join(' · ');
    const imageIds = [];
    for (const staged of this._stagedPhotos) {
      const imgId = _uuid();
      let dataUrl = staged.dataUrl;
      let size = staged.file.size;
      let filename = staged.file.name || 'photo.jpg';
      try {
        dataUrl = await this._stampPhoto(staged, { project: projName, node: nodeText });
        size = Math.round((dataUrl.length - dataUrl.indexOf(',') - 1) * 0.75);
        filename = filename.replace(/\.[^.]+$/, '') + '.jpg';
      } catch (e) {
        // a photo without its caption still beats no photo
        console.warn('stamp failed', e);
      }
      await DB.saveImage({
        id:            imgId,
        entry_id:      id,
        data_url:      dataUrl,
        filename:      filename,
        size:          size,
        upload_status: 'local',
      });
      imageIds.push(imgId);
    }
    entry.image_ids = imageIds;
    entry.plant_block = blocks[0];   // the block the photos and the stamp name

    // One record per block, same text, same type, status, PTW and hours. The
    // photos stay on the first record only — a site connection must not carry
    // the same five photos three times — and the others say where they are.
    const photoNote = imageIds.length
      ? 'Photos on the Block ' + blocks[0] + ' record.' : '';
    await DB.saveEntry(entry);
    for (const b of blocks.slice(1)) {
      await DB.saveEntry(Object.assign({}, entry, {
        id:            _uuid(),
        plant_block:   b,
        image_ids:     [],
        internal_note: [note, photoNote].filter(Boolean).join('\n'),
      }));
    }
    this._rememberFault(fault);        // remember a newly-typed fault/alarm
    this._stagedPhotos = [];
    localStorage.removeItem('record_draft');

    // A PM record is also the block's PM hours for the month: it goes to the
    // desktop as a field event, which is the one writer of pm_activities.
    // One event naming every block — record_pm splits it into one PM record
    // per block, each charged the full hours (4 h on three blocks is 4 h each).
    if (cat === 'maintenance' && hours > 0 && projectId) {
      await DB.saveFieldEvent({
        id: _uuid(), project_id: projectId, kind: 'pm', blocks: blocks.join(','),
        date_from: date, date_to: date, hours: hours, exclusion_type: '',
        ptw_no: ptw,
        description: (desc || 'PM as per checklist') + (ptw ? ' [' + ptw + ']' : ''),
        created_at: now, sync_status: 'local',
      });
    }

    if (navigator.onLine) this._syncQuiet();

    this.goRecords();
  },

  // ── Photos ─────────────────────────────────────────────────────────────────
  addPhotos(input) {
    const preview = document.getElementById('photo-preview');
    // where the engineer is standing now, for the stamp; asked once per photo
    // batch and never blocking: a photo without it is still worth having
    const geo = this._location();
    Array.from(input.files).forEach(file => {
      const id     = _uuid();
      const reader = new FileReader();
      reader.onload = e => {
        const dataUrl = e.target.result;
        this._stagedPhotos.push({ id, file, dataUrl, geo, taken: file.lastModified || Date.now() });

        const wrap = document.createElement('div');
        wrap.className = 'photo-thumb';
        wrap.innerHTML = `<img src="${dataUrl}" alt="" />
          <button class="photo-remove" type="button">×</button>`;
        wrap.querySelector('.photo-remove').addEventListener('click', () => {
          this._stagedPhotos = this._stagedPhotos.filter(p => p.id !== id);
          wrap.remove();
        });
        preview.appendChild(wrap);
      };
      reader.readAsDataURL(file);
    });
    input.value = '';   // allow re-selecting same file
  },

  /** The phone's position, or null. Never rejects and never blocks the form:
      indoors, in a container, or with the permission refused, the stamp simply
      carries no coordinates — the node already says where the work was. */
  _location() {
    if (this._geo && Date.now() - this._geo.t < 120000) return Promise.resolve(this._geo);
    return new Promise(resolve => {
      if (!navigator.geolocation) return resolve(null);
      navigator.geolocation.getCurrentPosition(
        p => {
          this._geo = { lat: p.coords.latitude, lon: p.coords.longitude,
                        acc: Math.round(p.coords.accuracy || 0), t: Date.now() };
          resolve(this._geo);
        },
        () => resolve(null),
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
    });
  },

  /** Draw the photo with a caption strip burned into it: project, date and
      time, node, coordinates. EXIF is stripped by messengers and by the
      browser itself, so the text has to be part of the picture. Re-encoded as
      JPEG, which also takes a 3-8 MB camera photo down to well under 1 MB. */
  async _stampPhoto(staged, meta) {
    const bmp = await _decodeImage(staged.file, staged.dataUrl);
    const MAX = 1600;
    const scale = Math.min(1, MAX / Math.max(bmp.width, bmp.height));
    const w = Math.max(1, Math.round(bmp.width * scale));
    const h = Math.max(1, Math.round(bmp.height * scale));
    const cv = document.createElement('canvas');
    cv.width = w; cv.height = h;
    const ctx = cv.getContext('2d');
    ctx.drawImage(bmp, 0, 0, w, h);
    if (bmp.close) bmp.close();

    const geo = await staged.geo;
    const lines = [];
    const when = new Date(staged.taken || Date.now());
    lines.push([meta.project, _fmtStampTime(when)].filter(Boolean).join(' · '));
    if (meta.node) lines.push(meta.node);
    if (geo) lines.push(`${geo.lat.toFixed(5)}, ${geo.lon.toFixed(5)}`
                        + (geo.acc ? ` ±${geo.acc} m` : ''));

    const size = Math.max(13, Math.round(w / 42));
    const pad  = Math.round(size * 0.5);
    ctx.font = `600 ${size}px system-ui, -apple-system, Segoe UI, Roboto, sans-serif`;
    ctx.textBaseline = 'top';
    const wrapped = [];
    for (const line of lines) {
      let cur = '';
      for (const word of String(line).split(' ')) {
        const next = cur ? cur + ' ' + word : word;
        if (ctx.measureText(next).width > w - pad * 2 && cur) { wrapped.push(cur); cur = word; }
        else cur = next;
      }
      if (cur) wrapped.push(cur);
    }
    const lh = Math.round(size * 1.3);
    const strip = wrapped.length * lh + pad * 2;
    ctx.fillStyle = 'rgba(0,0,0,0.55)';
    ctx.fillRect(0, h - strip, w, strip);
    ctx.fillStyle = '#FFFFFF';
    wrapped.forEach((t, i) => ctx.fillText(t, pad, h - strip + pad + i * lh));
    const out = cv.toDataURL('image/jpeg', 0.82);
    // A phone low on memory does not throw here — it hands back "data:," and
    // the stamped "photo" would silently replace a good one. Anything this
    // short is not a photo; throwing lets the caller keep the original.
    if (!out || out.indexOf('data:image/') !== 0 || out.length < 100) {
      throw new Error('the phone could not re-encode this photo');
    }
    return out;
  },

  // ── Detail view ────────────────────────────────────────────────────────────
  async _showDetail(entryId) {
    this._currentEntryId = entryId;
    const entry = await DB.getEntry(entryId);
    if (!entry) return;

    document.getElementById('detail-title').textContent = _fmtDate(entry.log_date);

    const images = await DB.getImagesForEntry(entryId);
    const imgHtml = images.map(img =>
      `<img class="detail-img" src="${img.data_url}" alt="" data-img-id="${img.id}" />`
    ).join('');

    document.getElementById('detail-body').innerHTML = `
      <div class="detail-card">
        <div><span class="cat-badge cat-${entry.category}">${_catLabel(entry.category)}</span></div>
        ${entry.project_id != null ? `<div class="detail-row"><label>Project</label><span>${_esc(_projName(entry.project_id))}</span></div>` : ''}
        ${entry.fault_name       ? `<div class="detail-row"><label>Fault</label><span>${_esc(entry.fault_name)}</span></div>`            : ''}
        ${entry.status           ? `<div class="detail-row"><label>Status</label><span>${entry.status === 'done' ? '✅ Done' : '🔵 Open'}</span></div>` : ''}
        ${entry.sap_ticket       ? `<div class="detail-row"><label>SAP Ticket</label><span>${_esc(entry.sap_ticket)}</span></div>`        : ''}
        ${entry.spare_parts      ? `<div class="detail-row"><label>Spare Parts</label><span>${_esc(entry.spare_parts)}</span></div>`      : ''}
        ${entry.site_location    ? `<div class="detail-row"><label>Location</label><span>${_esc(entry.site_location)}</span></div>`      : ''}
        ${entry.equipment_serial ? `<div class="detail-row"><label>Equipment Serial</label><span>${_esc(entry.equipment_serial)}</span></div>` : ''}
        <div class="detail-row"><label>Description</label><p>${_esc(entry.description)}</p></div>
        ${(entry.tags||[]).length ? `<div class="tag-row">${(entry.tags||[]).map(t=>`<span class="tag">${_esc(t)}</span>`).join('')}</div>` : ''}
        ${imgHtml ? `<div class="detail-imgs">${imgHtml}</div>` : ''}
        <div class="detail-meta">
          Created ${entry.created_at.slice(0,16).replace('T',' ')}
          ${entry.sync_status !== 'synced' ? ' · <em>Not synced</em>' : ''}
        </div>
      </div>`;

    // Lightbox on image tap
    document.querySelectorAll('.detail-img').forEach(img => {
      img.addEventListener('click', () => {
        document.getElementById('lightbox-img').src = img.src;
        document.getElementById('lightbox').style.display = 'flex';
      });
    });

    this._show('screen-detail');
  },

  async deleteEntry() {
    if (!this._currentEntryId) return;
    if (!confirm('Delete this entry?')) return;
    await this._deleteById(this._currentEntryId);
    this._currentEntryId = null;
    this._show('screen-home');
    await this._loadTimeline();
  },

  async _deleteById(id) {
    const entry = await DB.getEntry(id);
    if (!entry) return;
    if (entry.sync_status === 'synced') {
      // Soft-delete — will be pushed on next sync
      entry.deleted_at  = new Date().toISOString();
      entry.updated_at  = entry.deleted_at;
      entry.sync_status = 'local';
      entry.version    += 1;
      await DB.saveEntry(entry);
    } else {
      // Never synced — hard-delete locally
      await DB.deleteEntry(id);
      await DB.deleteImagesForEntry(id);
    }
  },

  // ── Stock (spare parts + write-off) ─────────────────────────────────────────
  _stockItems: [],
  _woItem:     null,

  async goStock() {
    this._tab = 'stock';
    let projects = await DB.getMeta('projects', []);
    if ((!projects || !projects.length) && navigator.onLine) {
      try { projects = await API.getProjects(); await DB.setMeta('projects', projects); } catch (_) {}
    }
    const sel = document.getElementById('stock-proj');
    sel.innerHTML = '';
    if (!projects || !projects.length) {
      sel.innerHTML = '<option value="">— No projects —</option>';
    } else {
      for (const p of projects) {
        const o = document.createElement('option');
        o.value = p.id; o.textContent = p.name; sel.appendChild(o);
      }
      const last = localStorage.getItem('last_project_id');
      if (last && projects.some(p => String(p.id) === last)) sel.value = last;
    }
    this._show('screen-stock');
    await this.loadStock();
  },

  onStockProject() {
    const v = document.getElementById('stock-proj').value;
    if (v) localStorage.setItem('last_project_id', v);
    this.loadStock();
  },

  async loadStock() {
    const sel  = document.getElementById('stock-proj');
    const pid  = sel.value ? parseInt(sel.value, 10) : null;
    const list = document.getElementById('stock-list');
    const note = document.getElementById('stock-note');
    if (!pid) { list.innerHTML = '<div class="empty">Select a project.</div>'; return; }
    list.innerHTML = '<div class="loading">Loading…</div>';

    let items = null;
    if (navigator.onLine) {
      try { items = await API.getStock(pid); await DB.setMeta('stock:' + pid, items); note.style.display = 'none'; }
      catch (_) { items = null; }
    }
    if (items === null) {
      items = await DB.getMeta('stock:' + pid, []);
      note.textContent = '⚠ Offline — showing last synced stock.';
      note.style.display = 'block';
    }
    this._stockItems = await this._adjustedStock(pid, items);
    this._renderStock();
  },

  async _adjustedStock(pid, items) {
    // Subtract not-yet-synced write-offs so the displayed quantity is honest offline
    const pend = await DB.getPendingWriteoffs();
    const used = {};
    for (const w of pend) {
      if (w.project_id === pid) used[w.material_number] = (used[w.material_number] || 0) + Number(w.quantity || 0);
    }
    return (items || []).map(it => ({
      ...it,
      quantity: Math.max(0, Number(it.quantity || 0) - (used[it.material_number] || 0)),
    }));
  },

  _renderStock() {
    const list = document.getElementById('stock-list');
    if (!this._stockItems.length) {
      list.innerHTML = '<div class="empty">No stock for this project.<br>Add it on the desktop Spare Parts page.</div>';
      return;
    }
    list.innerHTML = this._stockItems.map((it, i) => {
      const low = it.min_quantity > 0 && it.quantity <= it.min_quantity;
      return `<div class="stock-item" data-i="${i}">
        <div class="stock-main">
          <div class="stock-mat">${_esc(it.material_number)}</div>
          <div class="stock-desc">${_esc(it.description || '')}</div>
        </div>
        <div class="stock-qty ${low ? 'low' : ''}">${(+it.quantity).toLocaleString()} <span>${_esc(it.unit || '')}</span></div>
      </div>`;
    }).join('');
    list.querySelectorAll('.stock-item').forEach(el => {
      el.addEventListener('click', () => this.openWriteoff(this._stockItems[+el.dataset.i]));
    });
  },

  openWriteoff(item) {
    this._woItem = item;
    document.getElementById('wo-title').textContent = 'Write off — ' + item.material_number;
    document.getElementById('wo-avail').textContent = `In stock: ${(+item.quantity).toLocaleString()} ${item.unit || ''}`;
    document.getElementById('wo-qty').value   = 1;
    document.getElementById('wo-block').value = '';
    document.getElementById('wo-note').value  = '';
    document.getElementById('wo-error').style.display = 'none';
    document.getElementById('writeoff-modal').style.display = 'flex';
  },

  closeWriteoff() {
    document.getElementById('writeoff-modal').style.display = 'none';
    this._woItem = null;
  },

  async confirmWriteoff() {
    if (!this._woItem) return;
    const qty   = parseFloat(document.getElementById('wo-qty').value);
    const errEl = document.getElementById('wo-error');
    if (!(qty > 0)) { _showErr(errEl, 'Enter a quantity greater than 0.'); return; }
    const pid = parseInt(document.getElementById('stock-proj').value, 10);
    const wo  = {
      id:              _uuid(),
      project_id:      pid,
      material_number: this._woItem.material_number,
      description:     this._woItem.description || '',
      quantity:        qty,
      block:           document.getElementById('wo-block').value.trim(),
      note:            document.getElementById('wo-note').value.trim(),
      log_date:        _today(),
      created_at:      new Date().toISOString(),
      sync_status:     'local',
    };
    await DB.saveWriteoff(wo);
    this.closeWriteoff();
    await this.loadStock();                       // reflect immediately (pending applied)
    if (navigator.onLine) { await this._syncWriteoffs(); await this.loadStock(); }
  },

  async _syncWriteoffs() {
    const pend = await DB.getPendingWriteoffs();
    for (const w of pend) {
      try {
        await API.postWriteoff({
          id: w.id, project_id: w.project_id, material_number: w.material_number,
          description: w.description, quantity: w.quantity, block: w.block,
          note: w.note, log_date: w.log_date, created_at: w.created_at,
        });
        await DB.deleteWriteoff(w.id);            // server owns it now
      } catch (_) { /* keep pending, retry next sync */ }
    }
  },

  // ── Report event (PM / downtime / exclusion) ────────────────────────────────
  async goEvent() {
    let projects = await DB.getMeta('projects', []);
    if ((!projects || !projects.length) && navigator.onLine) {
      try { projects = await API.getProjects(); await DB.setMeta('projects', projects); } catch (_) {}
    }
    const sel = document.getElementById('ev-proj');
    sel.innerHTML = '';
    if (!projects || !projects.length) {
      sel.innerHTML = '<option value="">— No projects —</option>';
    } else {
      for (const p of projects) {
        const o = document.createElement('option');
        o.value = p.id; o.textContent = p.name; sel.appendChild(o);
      }
      const last = localStorage.getItem('last_project_id');
      if (last && projects.some(p => String(p.id) === last)) sel.value = last;
    }
    document.getElementById('ev-kind').value  = 'pm';
    document.getElementById('ev-from').value   = _today();
    document.getElementById('ev-to').value     = _today();
    document.getElementById('ev-blocks').value = '';
    document.getElementById('ev-hours').value  = '0';
    document.getElementById('ev-desc').value   = '';
    document.getElementById('ev-error').style.display = 'none';
    this.onEventKind();
    this._show('screen-event');
  },

  // ── Block picker ────────────────────────────────────────────────────────────
  // Typing "1,2,3" was impossible: the field asked for a numeric keypad, which
  // on Android has no comma. Blocks are tapped now; the text field still
  // accepts typing, including ranges like 1-8.
  _parseBlocks(text) {
    const out = new Set();
    for (const tok of String(text || '').split(',')) {
      const t = tok.trim();
      if (!t) continue;
      const m = t.match(/^(\d+)\s*-\s*(\d+)$/);
      if (m) {
        const a = Math.min(+m[1], +m[2]), z = Math.max(+m[1], +m[2]);
        for (let i = a; i <= z; i++) out.add(i);
      } else if (/^\d+$/.test(t)) {
        out.add(+t);
      }
    }
    return [...out].sort((a, b) => a - b);
  },

  // 1,2,3,5,6,7 -> "1-3, 5-7" so a long selection stays readable
  _formatBlocks(list) {
    const n = [...list].sort((a, b) => a - b);
    if (!n.length) return '';
    const runs = [];
    let start = n[0], prev = n[0];
    for (const v of n.slice(1)) {
      if (v === prev + 1) { prev = v; continue; }
      runs.push([start, prev]); start = prev = v;
    }
    runs.push([start, prev]);
    return runs.map(([a, b]) => (a === b ? `${a}` : `${a}-${b}`)).join(', ');
  },

  async _blockCount() {
    const pid = document.getElementById('ev-proj').value;
    const projects = await DB.getMeta('projects', []);
    const p = (projects || []).find(x => String(x.id) === String(pid));
    return (p && p.num_blocks) ? p.num_blocks : 0;
  },

  async openBlockPicker() {
    const n = await this._blockCount();
    if (!n) {
      _showErr(document.getElementById('ev-error'),
               'This project has no block count yet — sync the desktop once, '
               + 'or type the blocks by hand (3 or 1,2,3 or 1-8).');
      return;
    }
    this._picked = new Set(this._parseBlocks(
      document.getElementById('ev-blocks').value));
    const grid = document.getElementById('bp-grid');
    grid.innerHTML = '';
    for (let i = 1; i <= n; i++) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'bp-cell' + (this._picked.has(i) ? ' on' : '');
      b.textContent = i;
      b.dataset.block = i;
      b.addEventListener('click', () => {
        if (this._picked.has(i)) { this._picked.delete(i); b.classList.remove('on'); }
        else { this._picked.add(i); b.classList.add('on'); }
        this._blockPickCount();
      });
      grid.appendChild(b);
    }
    this._blockPickCount();
    document.getElementById('block-sheet').style.display = 'flex';
  },

  _blockPickCount() {
    const n = this._picked ? this._picked.size : 0;
    document.getElementById('bp-count').textContent =
      n ? `${n} selected — ${this._formatBlocks([...this._picked])}`
        : 'none selected — pick the block(s), or “All” for the whole plant';
  },

  blockPickAll() {
    document.querySelectorAll('#bp-grid .bp-cell').forEach(b => {
      this._picked.add(+b.dataset.block); b.classList.add('on');
    });
    this._blockPickCount();
  },

  blockPickNone() {
    this._picked.clear();
    document.querySelectorAll('#bp-grid .bp-cell').forEach(b => b.classList.remove('on'));
    this._blockPickCount();
  },

  closeBlockPicker() { document.getElementById('block-sheet').style.display = 'none'; },

  applyBlockPicker() {
    document.getElementById('ev-blocks').value =
      this._formatBlocks([...(this._picked || [])]);
    this.closeBlockPicker();
  },

  onEventKind() {
    const k = document.getElementById('ev-kind').value;
    document.getElementById('ev-excltype-group').style.display = (k === 'excluded') ? 'block' : 'none';
    document.getElementById('ev-blocks-label').textContent = 'Block(s) *';
    const hint = document.getElementById('ev-hint');
    if (k === 'pm')          hint.textContent = 'PM counts as downtime — the hours you enter reduce availability for each block (one record per block per day, up to 24 h).';
    else if (k === 'counts') hint.textContent = 'Counted as unavailability once the office confirms the real times and blocks. Put the times in the description.';
    else                     hint.textContent = 'Credited back (does NOT reduce availability) once the office confirms the real start and end time. Put the times in the description.';
  },

  async saveEvent() {
    const errEl = document.getElementById('ev-error');
    const pid   = document.getElementById('ev-proj').value;
    if (!pid) { _showErr(errEl, 'Select a project.'); return; }
    const kind   = document.getElementById('ev-kind').value;
    const from   = document.getElementById('ev-from').value;
    const to     = document.getElementById('ev-to').value || from;
    const blocks = document.getElementById('ev-blocks').value.trim();
    const hours  = parseFloat(document.getElementById('ev-hours').value) || 0;
    const desc   = document.getElementById('ev-desc').value.trim();
    // Same rules as the desktop (report_workflow_service.validate_pm): an empty
    // block field used to mean the whole plant — a 3-h PM without a block
    // charged all 70 blocks. The whole plant is now the explicit "All" choice.
    if (!from) { _showErr(errEl, 'Pick a date.'); return; }
    if (from > _today(1)) { _showErr(errEl, 'The date is in the future.'); return; }
    if (to < from) { _showErr(errEl, 'The end date is before the start date.'); return; }
    if (!blocks) { _showErr(errEl, 'Choose the block(s) — tap “Choose blocks”, or “All” for the whole plant.'); return; }
    if (!this._parseBlocks(blocks).length || /[^\d,\s-]/.test(blocks)) {
      _showErr(errEl, 'Blocks must be numbers: 3 or 1,2,3 or 1-8.'); return;
    }
    if (!(hours > 0)) { _showErr(errEl, 'Enter the duration in hours (more than 0).'); return; }
    if (hours > 24 * 31) { _showErr(errEl, 'That is more hours than the month has.'); return; }
    if (kind === 'pm' && hours > 24) { _showErr(errEl, 'PM hours are per block per day — at most 24.'); return; }
    if (kind === 'pm' && hours > 12 && !confirm(`${hours} h of PM on one block in one day — is that right?`)) return;

    localStorage.setItem('last_project_id', pid);
    const tFrom = (document.getElementById('ev-time-from') || {}).value || '';
    const tTo   = (document.getElementById('ev-time-to') || {}).value || '';
    const ptw   = ((document.getElementById('ev-ptw') || {}).value || '').trim();
    // The times go in the text: the desktop confirms the real window against
    // SCADA before anything counts, and it must see what the phone meant.
    const when = (tFrom || tTo) ? ('Reported ' + (tFrom || '?') + '–' + (tTo || 'open') + '. ') : '';
    const ev = {
      id: _uuid(), project_id: parseInt(pid, 10), kind, blocks,
      date_from: from, date_to: to, hours,
      exclusion_type: kind === 'excluded' ? document.getElementById('ev-excltype').value : '',
      time_from: tFrom, time_to: tTo, ptw_no: ptw,
      description: when + desc + (ptw ? ' [' + ptw + ']' : ''),
      created_at: new Date().toISOString(), sync_status: 'local',
    };
    await DB.saveFieldEvent(ev);
    // Go to the records list, not home — the engineer must be able to see the
    // record land and whether it reached the server.
    await this.goEvents();
    if (navigator.onLine) {
      this._eventsBanner('🔄 Sending…', '');
      const r = await this._syncEvents();
      if (r.failed) this._eventsBanner('⚠ Not sent: ' + r.error + ' — kept on the phone, will retry.', 'err');
      else          this._eventsBanner('✓ Sent to the server.', 'ok');
    } else {
      this._eventsBanner('📴 Offline — saved on the phone, will send on the next sync.', 'warn');
    }
    await this._renderEvents();
  },

  // ── PM / downtime records ───────────────────────────────────────────────────
  async goEvents() {
    this._show('screen-events');
    this._eventsBanner('', '');
    await this._renderEvents();
  },

  _eventsBanner(text, kind) {
    const b = document.getElementById('ev-banner');
    if (!b) return;
    b.textContent = text || '';
    b.className = 'sync-bar' + (kind === 'err' ? ' sync-bar-conflict'
                              : kind === 'warn' ? ' sync-bar-warn' : '');
    b.style.display = text ? 'block' : 'none';
  },

  async _renderEvents() {
    const list = document.getElementById('ev-list');
    if (!list) return;
    const rows = await DB.getAllFieldEvents();
    const projects = await DB.getMeta('projects', []);
    const names = {};
    for (const p of (projects || [])) names[p.id] = p.name;

    const nPend = rows.filter(r => r.sync_status !== 'synced').length;
    const retry = document.getElementById('ev-retry');
    if (retry) retry.style.display = nPend ? 'block' : 'none';

    if (!rows.length) {
      list.innerHTML = '<div class="empty">No PM or downtime records yet.<br>'
                     + 'Tap ＋ on the home screen → “PM / downtime”.</div>';
      return;
    }

    const KIND = {
      pm:       { icon: '🧰', label: 'Preventive maintenance' },
      counts:   { icon: '⛔', label: 'Downtime — counts' },
      excluded: { icon: '➖', label: 'Excluded' },
    };
    let html = '';
    for (const r of rows) {
      const k = KIND[r.kind] || { icon: '•', label: r.kind || '' };
      const synced = r.sync_status === 'synced';
      const badge = synced
        ? '<span class="ev-badge ev-ok">✓ sent</span>'
        : (r.sync_status === 'error'
            ? '<span class="ev-badge ev-err">⚠ not sent</span>'
            : '<span class="ev-badge ev-pend">⏳ pending</span>');
      const when = r.date_from + (r.date_to && r.date_to !== r.date_from ? ' → ' + r.date_to : '');
      const bits = [];
      if (r.blocks) bits.push('Block(s) ' + _esc(r.blocks));
      else bits.push('no block — the office will ask');
      if (r.hours > 0) bits.push(r.hours + ' h');
      if (r.exclusion_type) bits.push(_esc(r.exclusion_type));
      html += `<div class="ev-card">
          <div class="ev-card-top">
            <span class="ev-kind">${k.icon} ${_esc(k.label)}</span>${badge}
          </div>
          <div class="ev-when">${_esc(when)} · ${_esc(names[r.project_id] || ('project ' + r.project_id))}</div>
          <div class="ev-meta">${bits.join(' · ')}</div>
          ${r.description ? `<div class="ev-desc">${_esc(r.description)}</div>` : ''}
          ${(!synced && r.last_error) ? `<div class="ev-error">${_esc(r.last_error)}</div>` : ''}
        </div>`;
    }
    list.innerHTML = html;
  },

  async retryEvents() {
    if (!navigator.onLine) {
      this._eventsBanner('📴 Still offline.', 'warn');
      return;
    }
    this._eventsBanner('🔄 Retrying…', '');
    const r = await this._syncEvents();
    if (r.failed) this._eventsBanner(`⚠ ${r.failed} still not sent: ${r.error}`, 'err');
    else if (r.sent) this._eventsBanner(`✓ ${r.sent} record(s) sent.`, 'ok');
    else this._eventsBanner('Nothing pending.', '');
    await this._renderEvents();
  },

  // Returns { sent, failed, error } so the caller can actually say what
  // happened. Records are marked synced, never deleted — the phone keeps the
  // history of what was reported.
  async _syncEvents() {
    const pend = await DB.getPendingFieldEvents();
    let sent = 0, failed = 0, error = '';
    for (const e of pend) {
      try {
        await API.postEvent({
          id: e.id, project_id: e.project_id, kind: e.kind, blocks: e.blocks,
          date_from: e.date_from, date_to: e.date_to, hours: e.hours,
          exclusion_type: e.exclusion_type, description: e.description, created_at: e.created_at,
        });
        await DB.updateFieldEvent(e.id, {
          sync_status: 'synced', synced_at: new Date().toISOString(), last_error: '',
        });
        sent++;
      } catch (err) {
        failed++;
        error = error || (err && err.message) || 'Upload failed';
        await DB.updateFieldEvent(e.id, {
          sync_status: 'error', last_error: error,
        });
      }
    }
    return { sent, failed, error };
  },

  // ── PM checklists ───────────────────────────────────────────────────────────
  // The office plans one checklist per block out of the customer's workbook;
  // the phone shows the items, takes OK / NOK / N/A and a comment, and keeps
  // everything in IndexedDB so a container with no signal changes nothing.
  _clRun:  null,   // the checklist open right now
  _clTpls: {},     // template uuid -> template, for the item text

  async goChecklists() {
    let projects = await DB.getMeta('projects', []);
    if ((!projects || !projects.length) && navigator.onLine) {
      try { projects = await API.getProjects(); await DB.setMeta('projects', projects); } catch (_) {}
    }
    const sel = document.getElementById('cl-proj');
    sel.innerHTML = '';
    if (!projects || !projects.length) {
      sel.innerHTML = '<option value="">— No projects —</option>';
    } else {
      for (const p of projects) {
        const o = document.createElement('option');
        o.value = p.id; o.textContent = p.name; sel.appendChild(o);
      }
      const last = localStorage.getItem('last_project_id');
      if (last && projects.some(p => String(p.id) === last)) sel.value = last;
    }
    this._show('screen-checklists');
    this._clBanner('', '');
    await this._renderChecklists();
    if (navigator.onLine) await this.refreshChecklists();
  },

  onChecklistProject() {
    const v = document.getElementById('cl-proj').value;
    if (v) localStorage.setItem('last_project_id', v);
    this.refreshChecklists();
  },

  _clBanner(text, kind) {
    const b = document.getElementById('cl-banner');
    if (!b) return;
    b.textContent = text || '';
    b.className = 'sync-bar' + (kind === 'err' ? ' sync-bar-conflict'
                              : kind === 'warn' ? ' sync-bar-warn' : '');
    b.style.display = text ? 'block' : 'none';
  },

  async refreshChecklists() {
    if (!navigator.onLine) {
      this._clBanner('📴 Offline — showing what is on the phone.', 'warn');
      await this._renderChecklists();
      return;
    }
    this._clBanner('🔄 Checking…', '');
    const r = await this._syncChecklists();
    this._clBanner(r.error ? ('⚠ ' + r.error) : '', r.error ? 'err' : '');
    await this._renderChecklists();
  },

  /** Send what was ticked, then take the assigned list. A checklist with
      unsent answers is never overwritten by the server's copy — the work was
      done on this phone and nothing else has it yet. */
  async _syncChecklists() {
    let sent = 0, error = '';
    try {
      for (const run of await DB.getPendingChecklists()) {
        try {
          await API.postChecklistResults(run.uuid, {
            results:   run.results || {},
            status:    run.status || 'In Progress',
            ptw_no:    run.ptw_no || '',
            serial:    run.serial || '',
            filled_by: run.filled_by || localStorage.getItem('username') || '',
            // When this was filled in, not when the phone found signal: the
            // office may have corrected it in between, and the server needs
            // the real order to know which copy is the later one.
            updated_at: run.updated_at || '',
          });
          // Only mark it sent if it is still the checklist that was sent. An
          // upload takes seconds, and an answer ticked in the meantime used
          // to be marked "sent" without ever leaving the phone.
          await DB.updateChecklist(run.uuid, { sync_status: 'synced', last_error: '' },
                                   run.updated_at);
          sent++;
        } catch (e) {
          error = error || (e && e.message) || 'Upload failed';
          await DB.updateChecklist(run.uuid, { sync_status: 'error', last_error: error },
                                   run.updated_at);
        }
      }
      const pid = parseInt(document.getElementById('cl-proj').value, 10)
               || parseInt(localStorage.getItem('last_project_id'), 10);
      if (pid) {
        // Every page, not the first 200. Three checklists on each of 70
        // blocks is 210 runs, and the blocks past the cut simply never
        // reached the phone — with nothing on screen to say so.
        const tpls = {};
        const seen = new Set();
        let after = '', complete = false;
        for (let page = 0; page < 50; page++) {
          const got = await API.getChecklists(pid, after);
          for (const t of (got.templates || [])) tpls[t.uuid] = t;
          for (const r of (got.runs || [])) {
            seen.add(r.uuid);
            const local = await DB.getChecklist(r.uuid);
            if (local && local.sync_status && local.sync_status !== 'synced') continue;
            await DB.saveChecklist(Object.assign({}, r, { sync_status: 'synced' }));
          }
          after = got.cursor || '';
          if (!got.has_more || !after) { complete = true; break; }
        }
        await DB.setMeta('checklist_templates', tpls);
        this._clTpls = tpls;
        // A checklist the office cancelled is gone from the assigned list, so
        // it goes from the phone too — unless this phone still holds answers
        // nobody else has. A list that was cut short prunes nothing: that
        // would hide checklists that are simply on a page we never asked for.
        if (complete) {
          for (const r of await DB.getAllChecklists()) {
            if (r.project_id !== pid || seen.has(r.uuid) || r.deleted_at) continue;
            if (r.sync_status && r.sync_status !== 'synced') continue;
            await DB.updateChecklist(r.uuid, { deleted_at: new Date().toISOString() });
          }
        }
      }
    } catch (e) {
      error = error || (e && e.message) || 'Could not reach the server';
    }
    return { sent, error };
  },

  async _renderChecklists() {
    const list = document.getElementById('cl-list');
    if (!list) return;
    this._clTpls = await DB.getMeta('checklist_templates', {}) || {};
    const pid = parseInt(document.getElementById('cl-proj').value, 10);
    const rows = (await DB.getAllChecklists())
      .filter(r => !r.deleted_at && (!pid || r.project_id === pid));
    if (!rows.length) {
      list.innerHTML = '<div class="empty">No checklist assigned yet.<br>'
                     + 'The office plans them; they arrive on the next sync.</div>';
      return;
    }
    let html = '', campaign = null;
    for (const r of rows) {
      if ((r.campaign || '') !== campaign) {
        campaign = r.campaign || '';
        html += `<div class="date-header">${_esc(campaign || 'No campaign')}</div>`;
      }
      const p = _clCount(r, this._clTpls[r.template_uuid]);
      const chip = r.sync_status && r.sync_status !== 'synced'
        ? '<span class="chip warn">Waiting to send</span>'
        : (p.done >= p.total && p.total
            ? '<span class="chip ok">Sent</span>'
            : '<span class="chip">Assigned</span>');
      html += `<div class="cl-card" onclick="App.openChecklist('${r.uuid}')">
          <div class="cl-card-top">
            <span class="cl-blk">Block ${r.plant_block || '—'}</span>${chip}
          </div>
          <div class="cl-name">${_esc((this._clTpls[r.template_uuid] || {}).name
                                       || 'Checklist')} · ${_esc(r.run_date || '')}</div>
          <div class="cl-bar"><i style="width:${p.total ? Math.round(p.done / p.total * 100) : 0}%"></i></div>
          <div class="cl-meta">${p.done} of ${p.total} done${
            p.nok ? ` · <b class="cl-nok">${p.nok} NOK</b>` : ''}${
            p.excluded ? ` · ${p.excluded} not in this campaign` : ''}</div>
        </div>`;
    }
    list.innerHTML = html;
  },

  async openChecklist(uuid) {
    const run = await DB.getChecklist(uuid);
    if (!run) return;
    this._clRun = run;
    this._clTpls = await DB.getMeta('checklist_templates', {}) || {};
    const tpl = this._clTpls[run.template_uuid] || { items: [] };
    document.getElementById('cl-title').textContent =
      (tpl.kind || tpl.name || 'Checklist') + ' · block ' + (run.plant_block || '—');
    document.getElementById('cl-ptw').value    = run.ptw_no || '';
    document.getElementById('cl-serial').value = run.serial || '';
    document.getElementById('cl-signed').value = run.filled_by || '';

    let html = '', group = null;
    for (const it of (tpl.items || [])) {
      const res = (run.results || {})[String(it.item_id)] || { result: '', comment: '' };
      if ((it.equipment || '') !== group) {
        group = it.equipment || '';
        html += `<div class="cl-group">${_esc(group || 'Checks')}</div>`;
      }
      const excluded = res.result === 'Excluded';
      if (excluded) {
        // Out of scope for this campaign: shown, never tickable, with the
        // note the office wrote. The row still goes to the customer.
        html += `<div class="cl-item out">
            <div class="cl-text">${_esc(it.s_no ? it.s_no + '. ' : '')}${_esc(it.text || '')}</div>
            <div class="cl-out">Not in this campaign${
              res.comment ? ' · ' + _esc(res.comment) : ''}</div>
          </div>`;
        continue;
      }
      html += `<div class="cl-item" id="cl-i-${it.item_id}">
          <div class="cl-text">${_esc(it.s_no ? it.s_no + '. ' : '')}${_esc(it.text || '')}${
            it.activity ? `<span class="cl-act">${_esc(it.activity)}</span>` : ''}</div>
          <div class="cl-btns">
            ${['OK', 'NOK', 'N/A'].map(code => `<button type="button"
                class="cl-btn ${code === 'NOK' ? 'nok' : ''}${res.result === code ? ' on' : ''}"
                data-res="${code}"
                onclick="App.setChecklistResult(${it.item_id}, '${code}')">${code}</button>`).join('')}
          </div>
          <input class="cl-comment" type="text" placeholder="comment — measurement, which unit…"
                 value="${_esc(res.comment || '').replace(/<br>/g, ' ')}"
                 onchange="App.setChecklistComment(${it.item_id}, this.value)" />
        </div>`;
    }
    document.getElementById('cl-items').innerHTML = html
      || '<div class="empty">This checklist has no items yet — sync once with network.</div>';
    this._clProgress();
    this._show('screen-checklist');
  },

  _clProgress() {
    const run = this._clRun;
    if (!run) return;
    const p = _clCount(run, this._clTpls[run.template_uuid]);
    const el = document.getElementById('cl-progress');
    el.innerHTML = `<div class="cl-bar big"><i style="width:${
      p.total ? Math.round(p.done / p.total * 100) : 0}%"></i></div>
      <span>${p.done} of ${p.total}${p.nok ? ` · ${p.nok} NOK` : ''}${
        p.excluded ? ` · ${p.excluded} left out` : ''}</span>`;
  },

  /** Every tap is saved at once: a phone that dies mid-checklist must not take
      an hour of work with it. */
  async _clStore(patch) {
    if (!this._clRun) return;
    const run = Object.assign({}, this._clRun, patch, {
      sync_status: 'local', updated_at: new Date().toISOString(),
    });
    this._clRun = run;
    await DB.saveChecklist(run);
  },

  async setChecklistResult(itemId, code) {
    if (!this._clRun) return;
    const results = Object.assign({}, this._clRun.results || {});
    const cur = results[String(itemId)] || { result: '', comment: '' };
    // Tapping the same answer again clears it — a mis-tap is not an answer.
    const next = (cur.result === code) ? '' : code;
    results[String(itemId)] = { result: next, comment: cur.comment || '' };
    const total = ((this._clTpls[this._clRun.template_uuid] || {}).items || [])
      .filter(i => (results[String(i.item_id)] || {}).result !== 'Excluded').length;
    const done = Object.values(results).filter(
      v => ['OK', 'NOK', 'N/A'].includes(v.result)).length;
    await this._clStore({ results, status: (total && done >= total) ? 'Done' : 'In Progress' });
    const box = document.getElementById('cl-i-' + itemId);
    if (box) {
      box.querySelectorAll('.cl-btn').forEach(b =>
        b.classList.toggle('on', b.dataset.res === next));
    }
    this._clProgress();
  },

  async setChecklistComment(itemId, text) {
    if (!this._clRun) return;
    const results = Object.assign({}, this._clRun.results || {});
    const cur = results[String(itemId)] || { result: '', comment: '' };
    results[String(itemId)] = { result: cur.result || '', comment: text || '' };
    await this._clStore({ results });
  },

  async saveChecklistHead() {
    await this._clStore({
      ptw_no:    document.getElementById('cl-ptw').value.trim(),
      serial:    document.getElementById('cl-serial').value.trim(),
      filled_by: document.getElementById('cl-signed').value.trim(),
    });
  },

  async sendChecklist() {
    if (!this._clRun) return;
    await this.saveChecklistHead();
    if (!navigator.onLine) {
      alert('Offline — the checklist is saved on the phone and goes on the next sync.');
      return;
    }
    const btn = document.getElementById('cl-send');
    btn.disabled = true; btn.textContent = 'Sending…';
    const r = await this._syncChecklists();
    btn.disabled = false; btn.textContent = 'Send';
    await this.goChecklists();
    this._clBanner(r.error ? ('⚠ Not sent: ' + r.error + ' — kept on the phone.')
                           : '✓ Sent to the office.', r.error ? 'err' : 'ok');
  },

  // ── Action items ────────────────────────────────────────────────────────────
  // The office's own list — "confirm the EPC bought the spare parts", "get the
  // HVAC BOM". No block, no hours, no report: what a technician does with one
  // is say it is done and write a line about how. Everything is kept in
  // IndexedDB, so a container with no signal changes nothing.
  _act: null,

  async openAction(uuid) {
    const a = await DB.getAction(uuid);
    if (!a) return;
    this._act = a;
    document.getElementById('act-title').textContent = a.topic || 'Action';
    document.getElementById('act-topic').textContent = a.topic || '';
    document.getElementById('act-from').textContent =
      'No block — office action'
      + (a.assigned_by ? ' · from ' + a.assigned_by : '')
      + (a.due_date ? ' · due ' + a.due_date : '');
    document.getElementById('act-ask').textContent =
      [a.description || '', a.todo || ''].filter(Boolean).join('\n\n');
    document.getElementById('act-note').value = a.done_note || '';
    document.getElementById('act-error').style.display = 'none';
    this._actBanner('', '');
    this._show('screen-action');
  },

  _actBanner(text, kind) {
    const b = document.getElementById('act-banner');
    if (!b) return;
    b.textContent = text || '';
    b.className = 'sync-bar' + (kind === 'err' ? ' sync-bar-conflict'
                              : kind === 'warn' ? ' sync-bar-warn' : '');
    b.style.display = text ? 'block' : 'none';
  },

  /** Write the completion onto the item this phone holds. Saved at once: a
      phone that dies on the way back must not take the answer with it. */
  async _storeAction(done) {
    const a = this._act;
    if (!a) return null;
    const note = document.getElementById('act-note').value.trim();
    const next = Object.assign({}, a, {
      done_note:   note,
      status:      done ? 'done' : (a.status || 'open'),
      done_at:     done ? new Date().toISOString() : (a.done_at || ''),
      done_by:     localStorage.getItem('username') || a.done_by || '',
      sync_status: 'local',
      updated_at:  new Date().toISOString(),
    });
    await DB.saveAction(next);
    this._act = next;
    return next;
  },

  async saveAction() {
    if (!await this._storeAction(false)) return;
    this._actBanner('✓ Saved on the phone — it goes with the next sync.', '');
    if (navigator.onLine) this._syncActions();
  },

  async completeAction() {
    if (!await this._storeAction(true)) return;
    if (navigator.onLine) {
      this._actBanner('🔄 Sending…', '');
      try { await this._syncActions(); } catch (_) {}
    }
    await this.goTasks();
  },

  /** Send what was finished, then take the list of what is assigned here. An
      item with an unsent completion is never overwritten by the server's
      copy — the work was done on this phone and nothing else has it yet. */
  async _syncActions() {
    let sent = 0, error = '';
    try {
      for (const a of await DB.getPendingActions()) {
        try {
          await API.postActionDone(a.uuid, {
            status:    a.status || 'done',
            done_note: a.done_note || '',
            done_by:   a.done_by || localStorage.getItem('username') || '',
            done_at:   a.done_at || '',
            // When it was really finished, not when the phone found signal:
            // the office may have corrected it in between, and the server
            // needs the real order to know which copy is the later one.
            updated_at: a.updated_at || '',
          });
          await DB.updateAction(a.uuid, { sync_status: 'synced', last_error: '' },
                                a.updated_at);
          sent++;
        } catch (e) {
          error = error || (e && e.message) || 'Upload failed';
          await DB.updateAction(a.uuid, { sync_status: 'error', last_error: error },
                                a.updated_at);
        }
      }
      const pid = parseInt(localStorage.getItem('last_project_id'), 10);
      if (pid) {
        const seen = new Set();
        let after = '', complete = false;
        for (let page = 0; page < 50; page++) {
          const got = await API.getActionItems(pid, after);
          for (const it of (got.items || [])) {
            seen.add(it.uuid);
            const local = await DB.getAction(it.uuid);
            if (local && local.sync_status && local.sync_status !== 'synced') continue;
            await DB.saveAction(Object.assign({}, it, { sync_status: 'synced' }));
          }
          after = got.cursor || '';
          if (!got.has_more || !after) { complete = true; break; }
        }
        // An item the office cancelled, or gave to somebody else, is gone
        // from the assigned list, so it goes from the phone too — unless this
        // phone still holds an answer nobody else has. A list that was cut
        // short prunes nothing: that would hide items simply on a later page.
        if (complete) {
          for (const a of await DB.getAllActions()) {
            if (a.project_id !== pid || seen.has(a.uuid) || a.deleted_at) continue;
            if (a.sync_status && a.sync_status !== 'synced') continue;
            await DB.updateAction(a.uuid, { deleted_at: new Date().toISOString() });
          }
        }
      }
    } catch (e) {
      error = error || (e && e.message) || 'Could not reach the server';
    }
    return { sent, error };
  },

  // ── Sync ───────────────────────────────────────────────────────────────────
  async syncNow() {
    const bar = document.getElementById('sync-bar');
    bar.className      = '';
    bar.textContent    = '🔄 Syncing…';
    bar.style.display  = 'block';
    try {
      const r = await this._doSync();
      if (r.conflicts > 0) {
        bar.className   = 'sync-bar-conflict';
        bar.textContent = `⚠️ ${r.conflicts} conflict${r.conflicts > 1 ? 's' : ''} — server version kept. ↑${r.pushed} ↓${r.pulled}`;
        setTimeout(() => { bar.style.display = 'none'; bar.className = ''; }, 8000);
      } else if (r.evError) {
        bar.className   = 'sync-bar-conflict';
        bar.textContent = `⚠️ ${r.evError}`;
        setTimeout(() => { bar.style.display = 'none'; bar.className = ''; }, 9000);
      } else {
        bar.textContent = `✅ Done — ↑${r.pushed} uploaded · ↓${r.pulled} received`;
        setTimeout(() => { bar.style.display = 'none'; }, 3500);
      }
      await this._loadTimeline();
      await this._fillSettingsStatus();
      await this._refreshPendingBadge();
    } catch (e) {
      bar.textContent = `❌ ${e.message}`;
      setTimeout(() => { bar.style.display = 'none'; }, 5000);
    }
  },

  /** Connectivity came back, or the app returned to the foreground. */
  async _syncOnReconnect() {
    if (!navigator.onLine) return;
    if (!localStorage.getItem('access_token')) return;   // not logged in
    if (this._reconnecting) return;                      // both events can fire
    this._reconnecting = true;
    try {
      const pending = await this._pendingCount();
      if (pending) await this._syncQuiet();
      await this._refreshPendingBadge();
    } catch (_) {
    } finally {
      this._reconnecting = false;
    }
  },

  async _pendingCount() {
    try {
      return (await DB.getPendingEntries()).length
           + (await DB.getPendingWriteoffs()).length
           + (await DB.getPendingFieldEvents()).length
           + (await DB.getPendingChecklists()).length;
    } catch (_) {
      return 0;
    }
  },

  /** A standing reminder on the home screen while anything is unsent. */
  async _refreshPendingBadge() {
    const bar = document.getElementById('sync-bar');
    if (!bar) return;
    const n = await this._pendingCount();
    if (!n) {
      if (bar.dataset.pending === '1') {
        bar.dataset.pending = '';
        bar.style.display = 'none';
        bar.className = '';
        bar.textContent = '';      // don't let a stale count flash later
      }
      return;
    }
    bar.dataset.pending = '1';
    bar.className = 'sync-bar-warn';
    bar.textContent = navigator.onLine
      ? `⏳ ${n} record(s) not sent yet — tap 🔄`
      : `📴 offline · ${n} record(s) waiting to send`;
    bar.style.display = 'block';
  },

  async _syncQuiet() {
    try { await this._doSync(); await this._loadTimeline(); } catch (_) {}
    await this._refreshPendingBadge();
  },

  async _doSync() {
    const deviceId = localStorage.getItem('device_id') || '';
    let pushed = 0, pulled = 0, conflicts = 0;

    // ── Refresh project list (non-fatal — keep cached copy on failure) ───────
    try {
      const projects = await API.getProjects();
      if (Array.isArray(projects)) await DB.setMeta('projects', projects);
    } catch (_) { /* offline or old server — picker uses cached list */ }

    // ── Push pending material write-offs + field events (non-fatal) ───────────
    // Non-fatal, but not silent: a PM record that never reaches the server
    // must say so, or it is invisibly lost.
    let evError = '';
    try { await this._syncWriteoffs(); } catch (_) {}
    try {
      const r = await this._syncEvents();
      if (r.failed) evError = `${r.failed} PM/downtime record(s) not sent: ${r.error}`;
    } catch (e) { evError = 'PM/downtime records not sent: ' + (e.message || e); }
    // PM checklists: send what was ticked, take what the office has planned.
    try {
      const r = await this._syncChecklists();
      if (r.error) evError = evError || ('Checklists: ' + r.error);
    } catch (e) { evError = evError || ('Checklists: ' + (e.message || e)); }
    // The action list: send what was finished, take what is assigned here.
    try {
      const r = await this._syncActions();
      if (r.error) evError = evError || ('Action list: ' + r.error);
    } catch (e) { evError = evError || ('Action list: ' + (e.message || e)); }

    // ── Push pending entries ──────────────────────────────────────────────────
    const pending = await DB.getPendingEntries();
    if (pending.length) {
      // Build SyncChange array matching backend schema:
      // { entity, id, action, version, payload: { ...entry fields } }
      const changes = pending.map(e => ({
        entity:  'work_log',
        id:      e.id,
        action:  e.deleted_at ? 'delete' : 'upsert',
        version: e.version || 1,
        payload: {
          project_id:       e.project_id       || null,
          category:         e.category,
          log_date:         e.log_date,
          description:      e.description      || '',
          fault_name:       e.fault_name        || '',
          status:           e.status            || '',
          sap_ticket:       e.sap_ticket        || '',
          spare_parts:      e.spare_parts       || '',
          site_location:    e.site_location     || '',
          equipment_serial: e.equipment_serial  || '',
          plant_block:         e.plant_block         || null,
          node_lc:             e.node_lc             || '',
          node_device:         e.node_device         || '',
          ptw_no:              e.ptw_no              || '',
          time_from:           e.time_from           || '',
          time_to:             e.time_to             || '',
          hours:               (e.hours === undefined ? null : e.hours),
          internal_note:       e.internal_note       || '',
          availability_impact: e.availability_impact || 'none',
          tags:             e.tags              || [],
          deleted_at:       e.deleted_at        || null,
          updated_at:       e.updated_at,
          created_at:       e.created_at,
        },
      }));

      // Idempotency key: one UUID per batch — safe to retry without false conflicts
      const idempotencyKey = _uuid();
      const pushResult = await API.pushChanges(changes, deviceId, idempotencyKey);

      for (const r of (pushResult.results || [])) {
        if (r.outcome === 'applied') {
          await DB.markSynced(r.id, r.server_version);
          pushed++;
          // Upload any local images for this entry
          const imgs = await DB.getImagesForEntry(r.id);
          for (const img of imgs.filter(i => i.upload_status === 'local')) {
            try {
              const blob = await fetch(img.data_url).then(x => x.blob());
              await API.uploadImage(r.id, blob, img.filename, img.id);
              img.upload_status = 'uploaded';
              await DB.saveImage(img);
            } catch (_) { /* image upload optional — retry next sync */ }
          }
        } else if (r.outcome === 'conflict') {
          conflicts++;
          // Mark local entry as conflicted so the UI can flag it
          const local = await DB.getEntry(r.id);
          if (local) {
            local.sync_status = 'conflict';
            await DB.saveEntry(local);
          }
        } else if (r.outcome === 'error') {
          // The server refused this change. Until now nothing was done with
          // that answer at all: a job deleted on an older build vanished from
          // Tasks and stayed on the server, and nobody was told. Say why, and
          // put a refused delete back — the record is still real work.
          const local = await DB.getEntry(r.id);
          if (local) {
            const change = changes.find(c => c.id === r.id);
            if (change && change.action === 'delete' && local.deleted_at) {
              local.deleted_at = null;
              // _deleteById bumped the version for the delete; undo that too,
              // so this phone is back on the version the server holds.
              local.version = Math.max(1, (local.version || 1) - 1);
            }
            local.sync_status = 'error';
            local.last_error  = r.message || 'The server refused this change.';
            await DB.saveEntry(local);
          }
        }
      }
    }

    // ── Photos that failed to upload earlier ─────────────────────────────────
    // Photos used to go up only with an entry applied in this same push, and a
    // failure was swallowed — so a photo that missed its moment never went.
    // Every sync now retries each photo still waiting whose entry is on the
    // server (the upload is idempotent by photo id).
    try {
      for (const img of await DB.getPendingImages()) {
        const entry = await DB.getEntry(img.entry_id);
        if (!entry || entry.sync_status !== 'synced') continue;
        try {
          const blob = await fetch(img.data_url).then(x => x.blob());
          await API.uploadImage(img.entry_id, blob, img.filename, img.id);
          img.upload_status = 'uploaded';
          await DB.saveImage(img);
        } catch (_) { /* still offline or rejected — next sync tries again */ }
      }
    } catch (_) { /* photo retry must never break the sync */ }

    // ── Pull delta from server (paginated) ────────────────────────────────────
    let cursor  = await DB.getMeta('last_cursor', null);
    let hasMore = true;
    while (hasMore) {
      const pullResult = await API.pullDelta(cursor);
      hasMore = pullResult.has_more === true;

      for (const change of (pullResult.changes || [])) {
        if (change.entity !== 'work_log') continue;   // images handled separately
        const d = change.data;
        if (!d || !d.id || !d.log_date) continue;     // skip malformed rows
        const local = await DB.getEntry(d.id);
        // A record already in conflict is never overwritten. The office
        // publishes a job (v1), the technician fills it in offline (still
        // v1, unsent), the office edits the same record (v2): the push comes
        // back "conflict" and this very same sync then applied the server's
        // v2 over the only copy of the technician's text, hours and PTW.
        // The card says the office changed it; the work stays put.
        const conflicted = !!local && local.sync_status === 'conflict';
        // Accept remote if: no local copy, local is already synced, or remote is newer
        const remoteNewer = !local
          || local.sync_status === 'synced'
          || (d.version || 0) > (local.version || 0);
        if (remoteNewer && !conflicted) {
          await DB.saveEntry({ ...d, tags: d.tags || [], sync_status: 'synced' });
          pulled++;
        } else if (conflicted && (d.version || 0) > (local.version || 0)
                   && !local.server_changed) {
          local.server_changed = true;
          await DB.saveEntry(local);
        }
      }

      if (pullResult.cursor) cursor = pullResult.cursor;
      if (!hasMore) break;
    }

    if (cursor) await DB.setMeta('last_cursor', cursor);
    await DB.setMeta('last_sync_at', new Date().toISOString());

    return { pushed, pulled, conflicts, evError };
  },
};

// ── Private helpers ─────────────────────────────────────────────────────────

function _uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  // Fallback for older iOS
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

// The phone's own calendar date. toISOString() is UTC: from 00:00 to 05:00
// Tashkent time it gave yesterday, so a night entry on the 1st fell into the
// previous month's report.
function _today(offsetDays = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function _fmtDate(iso) {
  const [y, m, d] = iso.split('-');
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${+d} ${months[+m - 1]} ${y}`;
}

function _esc(str) {
  return String(str)
    .replace(/&/g,  '&amp;')
    .replace(/</g,  '&lt;')
    .replace(/>/g,  '&gt;')
    .replace(/"/g,  '&quot;')
    .replace(/\n/g, '<br>');
}

let _PROJECT_NAMES = {};   // { project_id: name } — refreshed on timeline load

function _projName(id) {
  if (id == null) return '';
  return _PROJECT_NAMES[id] || `Project #${id}`;
}

const _CAT_LABELS = {
  maintenance:   '🔧 Maintenance',
  fault:         '⚡ Fault',
  inspection:    '🔍 Inspection',
  commissioning: '🚀 Commissioning',
  repair:        '🛠 Repair',
  other:         '📝 Other',
};
function _catLabel(cat) { return _CAT_LABELS[cat] || cat; }

// What happened to this record, in the engineer's words. Before this, a record
// was either "synced" or a grey dot, and an upload that the server refused
// looked exactly like one still on its way.
const _SEND = {
  local:    ['Waiting to send', 'warn'],
  pending:  ['Waiting to send', 'warn'],
  synced:   ['Sent', 'ok'],
  error:    ['Error', 'crit'],
  conflict: ['Conflict — the office decides', 'crit'],
};

function _sendChip(entry) {
  // A conflict where the office has since changed the record too: the work
  // done here is still the only copy of itself, so say what happened rather
  // than leaving the engineer with a bare "Conflict".
  if (entry.sync_status === 'conflict' && entry.server_changed) {
    return '<span class="chip crit">The office changed this — your work is kept</span>';
  }
  const [text, kind] = _SEND[entry.sync_status] || ['Waiting to send', 'warn'];
  return `<span class="chip ${kind}">${text}</span>`;
}

/** How far one checklist is. The template holds the items; a run that arrived
    without its template is counted from the answers it carries, so the card
    still says something honest. */
function _clCount(run, tpl) {
  const results = (run && run.results) || {};
  const ids = (tpl && tpl.items && tpl.items.length)
    ? tpl.items.map(i => String(i.item_id))
    : Object.keys(results);
  let done = 0, nok = 0, excluded = 0;
  for (const id of ids) {
    const r = (results[id] || {}).result || '';
    if (r === 'Excluded') { excluded++; continue; }
    if (r === 'NOK') nok++;
    if (r === 'OK' || r === 'NOK' || r === 'N/A') done++;
  }
  return { total: ids.length - excluded, done, nok, excluded };
}

function _statusLabel(st) {
  return { open: 'Open', in_progress: 'In progress', needs_visit: 'Needs visit',
           done: 'Done' }[st] || (st || 'Open');
}

function _nodeText(entry) {
  if (entry.plant_block) {
    return 'Block ' + entry.plant_block
      + (entry.node_lc ? ' · ' + entry.node_lc : '')
      + (entry.node_device ? ' · ' + entry.node_device : '');
  }
  // an old record, written before the node picker
  return entry.site_location ? ('📍 ' + entry.site_location) : 'No block';
}

function _renderCard(entry) {
  const proj = entry.project_id != null
    ? `<span class="card-loc">${_esc(_projName(entry.project_id))}</span>` : '';
  const desc = entry.description
    ? `<p class="card-desc">${_esc(entry.description).slice(0, 160)}${entry.description.length > 160 ? '…' : ''}</p>` : '';
  const fault = entry.fault_name ? `<div class="card-fault">${_esc(entry.fault_name)}</div>` : '';
  const bits = [];
  if (entry.hours) bits.push(entry.hours + ' h');
  if (entry.ptw_no) bits.push(_esc(entry.ptw_no));
  if (entry.status) bits.push(_statusLabel(entry.status));
  const err = (entry.sync_status === 'error' && entry.last_error)
    ? `<div class="card-err">${_esc(entry.last_error)}</div>` : '';
  const retry = (entry.sync_status === 'error' || entry.sync_status === 'local'
                 || entry.sync_status === 'pending')
    ? `<button class="retry-btn" onclick="event.stopPropagation();App.syncNow()">Retry</button>` : '';
  // A job the office handed out is the office's record: this phone fills it
  // in, it does not delete it (the server would refuse, and the delete would
  // sit unsent forever). What makes it a job is assigned_to — assigned_by is
  // legitimately empty when the office account has no username, and the
  // Delete button then appeared on a job it cannot delete.
  const del = entry.assigned_to
    ? `<span class="hint">From ${_esc(entry.assigned_by || 'the office')}</span>`
    : `<button class="del-btn danger-link" data-id="${entry.id}">Delete</button>`;

  return `
    <div class="log-card" data-id="${entry.id}">
      <div class="card-head">
        <span class="cat-badge cat-${entry.category}">${_catLabel(entry.category)}</span>
        ${_sendChip(entry)}
      </div>
      <div class="card-node">${_esc(_nodeText(entry))}</div>
      ${fault}
      ${desc}
      ${bits.length ? `<div class="card-bits">${bits.join(' · ')}</div>` : ''}
      ${proj}
      ${err}
      <div class="card-actions">
        ${retry}
        ${del}
      </div>
    </div>`;
}

/** '20.09.2026 14:32' — what a person reads on the photo. */
function _fmtStampTime(d) {
  const p = n => String(n).padStart(2, '0');
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} `
       + `${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Decode a camera photo the way it should be seen. createImageBitmap honours
    the EXIF rotation, so a portrait photo does not land on its side; an older
    browser falls back to the data URL, which it rotates itself. */
async function _decodeImage(file, dataUrl) {
  if (window.createImageBitmap) {
    try {
      return await createImageBitmap(file, { imageOrientation: 'from-image' });
    } catch (_) { /* fall through */ }
  }
  return await new Promise((resolve, reject) => {
    const im = new Image();
    im.onload = () => resolve(im);
    im.onerror = reject;
    im.src = dataUrl;
  });
}

function _showErr(el, msg) {
  el.textContent    = msg;
  el.style.display  = 'block';
}

// ── Boot ────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  App.init();

  // Work recorded on site sits on the phone until something pushes it. Until
  // now that only happened on app start, on login, and right after saving —
  // so an engineer who wrote a report underground and kept the app open was
  // still carrying the only copy hours later. A lost phone took it with it.
  window.addEventListener('online', () => App._syncOnReconnect());
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) App._syncOnReconnect();
  });
});
