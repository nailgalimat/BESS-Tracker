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
  // the node being chosen: plant block, level (LC), device
  _node:           { block: null, lc: '', device: '', zone: '' },
  _nodeTab:        'zone',
  _nodeNum:        '',
  _nodeFor:        'create',
  _swReg:          null,

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

  taskSeg(seg) {
    this._taskSeg = seg;
    document.querySelectorAll('#task-seg button').forEach(b =>
      b.classList.toggle('on', b.dataset.seg === seg));
    this._renderTasks();
  },

  async _renderTasks() {
    const body = document.getElementById('tasks-body');
    if (!body) return;
    const all = (await DB.getAllEntries()).filter(e => !e.deleted_at);
    const today = _today();
    const weekAgo = _today(-7);
    const open = all.filter(e => ['open', 'in_progress', 'needs_visit'].includes(e.status || ''));
    let rows;
    if (this._taskSeg === 'today') rows = open.filter(e => e.log_date === today);
    else if (this._taskSeg === 'week') rows = open.filter(e => e.log_date >= weekAgo);
    else rows = open;
    rows.sort((a, b) => (a.log_date || '').localeCompare(b.log_date || ''));

    const events = (await DB.getAllFieldEvents()).filter(e => e.sync_status !== 'synced');
    let html = '';
    if (events.length) {
      html += `<div class="task-note warn">${events.length} downtime record(s) still
               waiting to send · <b>Records</b> shows why</div>`;
    }
    if (!rows.length) {
      html += `<div class="empty">Nothing open ${this._taskSeg === 'today' ? 'today' : ''}.
               <br>Tap ＋ to write a record.</div>`;
    }
    for (const e of rows) {
      const node = e.plant_block ? ('Block ' + e.plant_block) : 'No block';
      html += `<div class="tcard">
        <div class="tcard-top"><span class="chip ${e.status === 'needs_visit' ? 'crit' : 'warn'}">
          ${_esc(_statusLabel(e.status))}</span><span class="hint">${_esc(e.log_date || '')}</span></div>
        <div class="tcard-blk">${_esc(node)}${e.node_lc ? ' · ' + _esc(e.node_lc) : ''}${
          e.node_device ? ' · ' + _esc(e.node_device) : ''}</div>
        <div class="tcard-meta">${_esc(e.fault_name || e.description || '')}</div>
        <button class="btn btn-outline btn-block" onclick="App._showDetail('${e.id}')">Open</button>
      </div>`;
    }
    html += `<p class="hint-line">Campaign tasks planned on the desktop appear
             here once the plan is synced to phones — not in this version.</p>`;
    body.innerHTML = html;
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
      this._node = { block: null, lc: '', device: '', zone: '' };
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
    document.getElementById('f-hours-hint').textContent =
      'End − start = ' + (Math.round(mins / 60 * 100) / 100) + ' h · one PM record per block per day.';
  },

  // ── Node picker ───────────────────────────────────────────────────────────
  // "9zona 7block 2bsc" typed into a free-text location could never become a
  // plant block, so those records never reached section 3.2. The node is
  // chosen now: recent, zone -> block, or the number itself.
  _showNode() {
    const t = document.getElementById('f-node-text');
    if (!t) return;
    const n = this._node;
    t.textContent = n.block
      ? ('Block ' + n.block + (n.zone ? ' · ' + n.zone : '')
         + (n.lc ? ' · ' + n.lc : '') + (n.device ? ' · ' + n.device : ''))
      : 'Choose the block';
    t.classList.toggle('chosen', !!n.block);
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
    this._renderNode();
    this._show('screen-node');
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
        cells += `<button class="tile${this._node.block === b ? ' on' : ''}" onclick="App.pickBlock(${b})">
            <b>B${b - z[1] + 1}</b><span>${b}</span></button>`;
      }
      bg.innerHTML = cells;
      document.getElementById('np-block-label').textContent = 'Block in zone ' + z[0];
    } else if (np.blocks) {
      zg.innerHTML = '<p class="hint-line">This project has no zones mirrored yet.</p>';
      let cells = '';
      for (let b = 1; b <= np.blocks; b++) {
        cells += `<button class="tile${this._node.block === b ? ' on' : ''}" onclick="App.pickBlock(${b})">
            <b>${b}</b></button>`;
      }
      bg.innerHTML = cells;
    } else {
      zg.innerHTML = '';
      bg.innerHTML = '<p class="hint-line">No block count yet — sync the desktop once, '
                   + 'or use the Number tab.</p>';
    }

    // number keypad
    const num = this._nodeNum || (this._node.block ? String(this._node.block) : '');
    document.getElementById('np-num').textContent = num || '—';
    const n = parseInt(num, 10);
    const max = np.blocks || 9999;
    const okNum = n >= 1 && n <= max;
    document.getElementById('np-num-sub').textContent = okNum
      ? ('Block ' + n + (this._zoneLabel(n) ? ' · ' + this._zoneLabel(n) : ''))
      : ('Enter 1–' + (np.blocks || '…'));
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

    const chosen = this._nodeTab === 'number' ? n : this._node.block;
    const use = document.getElementById('np-use');
    use.disabled = !(chosen >= 1);
    use.textContent = chosen >= 1
      ? ('Use Block ' + chosen + (this._zoneLabel(chosen) ? ' · ' + this._zoneLabel(chosen) : '')
         + (this._node.device || this._node.lc ? ' · ' + (this._node.device || this._node.lc) : ''))
      : 'Choose a block';
  },

  pickZone(z) { this._node.zoneIdx = z; this._renderNode(); },
  pickBlock(b) { this._node.block = b; this._nodeNum = String(b); this._renderNode(); },
  pickLc(l) { this._node.lc = (this._node.lc === l ? '' : l); this._renderNode(); },
  pickDevice(d) { this._node.device = (this._node.device === d ? '' : d); this._renderNode(); },
  pickRecent(b, lc, dev) {
    this._node.block = b; this._node.lc = lc || ''; this._node.device = dev || '';
    this._nodeNum = String(b);
    this.useNode();
  },

  nodeKey(k) {
    if (k === 'C') this._nodeNum = '';
    else if (k === '⌫') this._nodeNum = (this._nodeNum || '').slice(0, -1);
    else this._nodeNum = ((this._nodeNum || '') + k).slice(0, 4);
    const n = parseInt(this._nodeNum, 10);
    if (n >= 1) this._node.block = n;
    this._renderNode();
  },

  useNode() {
    const b = this._nodeTab === 'number'
      ? parseInt(this._nodeNum, 10) : this._node.block;
    if (!(b >= 1)) return;
    this._node.block = b;
    this._node.zone = this._zoneLabel(b);
    this._rememberNode(this._node);
    document.getElementById('f-block').value  = String(b);
    document.getElementById('f-lc').value     = this._node.lc || '';
    document.getElementById('f-device').value = this._node.device || '';
    this._showNode();
    this._show('screen-create');
  },

  // The same defect on the next node: keep the text, drop the node.
  repeatOnAnotherNode() {
    const cat = document.getElementById('f-cat').value;
    this._node = { block: null, lc: this._node.lc, device: '', zone: '' };
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
                  + (await DB.getPendingFieldEvents()).length;
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
  async saveEntry() {
    const date   = document.getElementById('f-date').value;
    const cat    = document.getElementById('f-cat').value;
    const desc   = document.getElementById('f-desc').value.trim();
    const block  = parseInt(document.getElementById('f-block').value, 10) || null;
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
    if (cat === 'maintenance') {
      if (!(hours > 0)) { _showErr(errEl, 'Enter the PM hours (more than 0).'); return; }
      if (hours > 24) { _showErr(errEl, 'PM hours are per block per day — at most 24.'); return; }
      if (hours > 12 && !confirm(hours + ' h of PM on one block in one day — is that right?')) return;
    }

    const projectId = projVal ? parseInt(projVal, 10) : null;
    if (projVal) localStorage.setItem('last_project_id', projVal);
    else         localStorage.removeItem('last_project_id');

    const tags = tagStr.split(',').map(t => t.trim()).filter(Boolean);
    const now  = new Date().toISOString();
    const id   = _uuid();

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

    // Save staged photos to IndexedDB
    const imageIds = [];
    for (const staged of this._stagedPhotos) {
      const imgId = _uuid();
      await DB.saveImage({
        id:            imgId,
        entry_id:      id,
        data_url:      staged.dataUrl,
        filename:      staged.file.name || 'photo.jpg',
        size:          staged.file.size,
        upload_status: 'local',
      });
      imageIds.push(imgId);
    }
    entry.image_ids = imageIds;

    await DB.saveEntry(entry);
    this._rememberFault(fault);        // remember a newly-typed fault/alarm
    this._stagedPhotos = [];
    localStorage.removeItem('record_draft');

    // A PM record is also the block's PM hours for the month: it goes to the
    // desktop as a field event, which is the one writer of pm_activities.
    if (cat === 'maintenance' && hours > 0 && projectId) {
      await DB.saveFieldEvent({
        id: _uuid(), project_id: projectId, kind: 'pm', blocks: String(block),
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
    Array.from(input.files).forEach(file => {
      const id     = _uuid();
      const reader = new FileReader();
      reader.onload = e => {
        const dataUrl = e.target.result;
        this._stagedPhotos.push({ id, file, dataUrl });

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
           + (await DB.getPendingFieldEvents()).length;
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
        // Accept remote if: no local copy, local is already synced, or remote is newer
        const remoteNewer = !local
          || local.sync_status === 'synced'
          || (d.version || 0) > (local.version || 0);
        if (remoteNewer) {
          await DB.saveEntry({ ...d, tags: d.tags || [], sync_status: 'synced' });
          pulled++;
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
  const [text, kind] = _SEND[entry.sync_status] || ['Waiting to send', 'warn'];
  return `<span class="chip ${kind}">${text}</span>`;
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
        <button class="del-btn danger-link" data-id="${entry.id}">Delete</button>
      </div>
    </div>`;
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
