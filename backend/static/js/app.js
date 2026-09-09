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

  // ── Boot ───────────────────────────────────────────────────────────────────
  async init() {
    const token  = localStorage.getItem('access_token');
    const server = localStorage.getItem('server_url');
    if (token && server) {
      this._show('screen-home');
      await this._loadTimeline();
      // Background sync on startup
      if (navigator.onLine) this._syncQuiet();
    } else {
      this._show('screen-login');
    }
  },

  // ── Screen navigation ──────────────────────────────────────────────────────
  _show(screenId) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(screenId).classList.add('active');
    window.scrollTo(0, 0);
  },

  goHome() {
    this._show('screen-home');
    this._loadTimeline();
  },

  async goCreate() {
    this._stagedPhotos = [];
    document.getElementById('photo-preview').innerHTML = '';
    document.getElementById('f-date').value    = _today();
    document.getElementById('f-cat').value     = 'maintenance';
    document.getElementById('f-loc').value     = '';
    document.getElementById('f-serial').value  = '';
    document.getElementById('f-desc').value    = '';
    document.getElementById('f-fault').value   = '';
    document.getElementById('f-status').value  = '';
    document.getElementById('f-sap').value     = '';
    document.getElementById('f-parts').value   = '';
    document.getElementById('f-tags').value    = '';
    document.getElementById('create-error').style.display = 'none';
    this._refreshFaultList();
    await this._fillProjectSelect();
    this._show('screen-create');
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
      _showErr(errEl, 'Date and Description are required.');
      return;
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
      description:      desc,
      fault_name:       fault  || '',
      status:           status || '',
      sap_ticket:       sap    || '',
      spare_parts:      parts  || '',
      site_location:    loc    || null,
      equipment_serial: ser    || null,
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

    if (navigator.onLine) this._syncQuiet();

    this._show('screen-home');
    await this._loadTimeline();
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

  onEventKind() {
    const k = document.getElementById('ev-kind').value;
    document.getElementById('ev-excltype-group').style.display = (k === 'excluded') ? 'block' : 'none';
    document.getElementById('ev-blocks-label').textContent = (k === 'counts') ? 'Block(s) *' : 'Block(s)';
    const hint = document.getElementById('ev-hint');
    if (k === 'pm')          hint.textContent = 'PM counts as downtime — reduces availability in the monthly report (per block × duration).';
    else if (k === 'counts') hint.textContent = 'Counted as unavailability — reduces availability.';
    else                     hint.textContent = 'Credited back — does NOT reduce availability.';
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
    if (!from) { _showErr(errEl, 'Pick a date.'); return; }
    if (kind === 'counts' && !blocks) { _showErr(errEl, 'Block is required for downtime that counts.'); return; }
    if ((kind === 'counts' || kind === 'excluded') && !(hours > 0)) { _showErr(errEl, 'Enter the downtime duration in hours.'); return; }

    localStorage.setItem('last_project_id', pid);
    const ev = {
      id: _uuid(), project_id: parseInt(pid, 10), kind, blocks,
      date_from: from, date_to: to, hours,
      exclusion_type: kind === 'excluded' ? document.getElementById('ev-excltype').value : '',
      description: desc, created_at: new Date().toISOString(), sync_status: 'local',
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
      else if (r.kind === 'pm') bits.push('whole plant');
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

function _today() {
  const d = new Date();
  return d.toISOString().split('T')[0];
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

function _renderCard(entry) {
  const proj = entry.project_id != null
    ? `<span class="card-loc">🔋 ${_esc(_projName(entry.project_id))}</span>` : '';
  const loc  = entry.site_location
    ? `<span class="card-loc">📍 ${_esc(entry.site_location)}</span>` : '';
  const desc = entry.description
    ? `<p class="card-desc">${_esc(entry.description).slice(0, 160)}${entry.description.length > 160 ? '…' : ''}</p>` : '';
  const tags = (entry.tags || []).map(t => `<span class="tag">${_esc(t)}</span>`).join('');
  const dot  = entry.sync_status === 'conflict'
    ? '<span class="sync-dot conflict" title="Sync conflict — server version applied">⚠</span>'
    : entry.sync_status !== 'synced'
      ? '<span class="sync-dot" title="Not yet synced">●</span>'
      : '';

  return `
    <div class="log-card" data-id="${entry.id}">
      <div class="card-head">
        <span class="cat-badge cat-${entry.category}">${_catLabel(entry.category)}</span>
        ${dot}
      </div>
      ${proj}
      ${loc}
      ${desc}
      ${tags ? `<div class="tag-row">${tags}</div>` : ''}
      <div class="card-actions">
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
