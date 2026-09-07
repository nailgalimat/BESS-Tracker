/**
 * api.js — REST client for BESS Sync API
 * Handles auth (JWT + refresh), sync push/pull, image upload.
 */
const API = (() => {

  function base() {
    return (localStorage.getItem('server_url') || '').replace(/\/$/, '');
  }

  function authHeaders() {
    const h = { 'Content-Type': 'application/json' };
    const tok = localStorage.getItem('access_token');
    if (tok) h['Authorization'] = `Bearer ${tok}`;
    return h;
  }

  // ── Internal fetch wrapper with auto-refresh ─────────────────────────────────
  async function _req(method, path, body, isRetry = false) {
    const url  = base() + path;
    const opts = { method, headers: authHeaders() };
    if (body !== undefined) opts.body = JSON.stringify(body);

    let res;
    try {
      res = await fetch(url, opts);
    } catch (e) {
      throw new Error('Network error — check connection.');
    }

    // Auto-refresh on 401
    if (res.status === 401 && !isRetry) {
      const ok = await _refresh();
      if (ok) return _req(method, path, body, true);
      // Refresh failed — force re-login
      throw new Error('Session expired. Please log in again.');
    }

    if (!res.ok) {
      let msg = `Server error ${res.status}`;
      try {
        const j = await res.json();
        msg = j.detail || JSON.stringify(j);
      } catch (_) {}
      throw new Error(msg);
    }

    if (res.status === 204) return null;
    return res.json();
  }

  async function _refresh() {
    const rt = localStorage.getItem('refresh_token');
    if (!rt) return false;
    try {
      const res = await fetch(base() + '/auth/refresh', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ refresh_token: rt }),
      });
      if (!res.ok) return false;
      const d = await res.json();
      localStorage.setItem('access_token', d.access_token);
      // Server rotates the refresh token on every refresh; store the new one
      if (d.refresh_token) localStorage.setItem('refresh_token', d.refresh_token);
      return true;
    } catch (_) {
      return false;
    }
  }

  // ── Auth ─────────────────────────────────────────────────────────────────────

  async function login(serverUrl, username, password) {
    const url = serverUrl.replace(/\/$/, '') + '/auth/login';
    let res;
    try {
      res = await fetch(url, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ username, password }),
      });
    } catch (e) {
      throw new Error('Cannot reach server — check URL and network.');
    }
    if (!res.ok) {
      let msg = 'Login failed';
      try { const j = await res.json(); msg = j.detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return res.json();
  }

  async function logout() {
    try { await _req('POST', '/auth/logout', {}); } catch (_) {}
  }

  async function ping() {
    return _req('GET', '/health');
  }

  /** List of projects published by the desktop: [{ id, name, project_type }] */
  async function getProjects() {
    return _req('GET', '/projects');
  }

  // ── Stock (spare parts) ────────────────────────────────────────────────────

  /** Project stock: [{ project_id, material_number, description, quantity, unit, min_quantity }] */
  async function getStock(projectId) {
    return _req('GET', `/stock?project_id=${encodeURIComponent(projectId)}`);
  }

  /** Record a material write-off. payload = { id, project_id, material_number, description, quantity, block, note, log_date } */
  async function postWriteoff(payload) {
    return _req('POST', '/stock/writeoff', payload);
  }

  /** Record a field event (PM / downtime / exclusion). payload = { id, project_id, kind, blocks, date_from, date_to, hours, exclusion_type, description } */
  async function postEvent(payload) {
    return _req('POST', '/events', payload);
  }

  // ── Sync ─────────────────────────────────────────────────────────────────────

  /**
   * Pull changes from server since `cursor` (ISO datetime string or null).
   * Returns { changes: [...], cursor: "<new_cursor>" }
   */
  async function pullDelta(cursor) {
    const deviceId = localStorage.getItem('device_id') || '';
    let path = `/sync/pull?device_id=${encodeURIComponent(deviceId)}`;
    if (cursor) path += `&since=${encodeURIComponent(cursor)}`;
    return _req('GET', path);
  }

  /**
   * Push an array of SyncChange objects.
   * idempotencyKey is a UUID generated per-batch; safe to retry on network error.
   * Returns { results: [{ id, outcome, server_version, message }] }
   */
  async function pushChanges(changes, deviceId, idempotencyKey) {
    return _req('POST', '/sync/push', {
      changes,
      device_id:       deviceId       || '',
      idempotency_key: idempotencyKey || null,
    });
  }

  /**
   * Upload a single image (Blob) for a worklog entry.
   * imageId is a client-generated UUID; the server uses it as X-Image-ID for idempotency.
   * Returns image metadata from server.
   */
  async function uploadImage(worklogId, blob, filename, imageId) {
    const token = localStorage.getItem('access_token');
    const form  = new FormData();
    form.append('file', blob, filename || 'photo.jpg');

    const headers = { 'Authorization': `Bearer ${token}` };
    if (imageId) headers['X-Image-ID'] = imageId;

    let res;
    try {
      res = await fetch(base() + `/worklogs/${worklogId}/images`, {
        method: 'POST',
        headers,
        body:   form,
      });
    } catch (e) {
      throw new Error('Image upload failed: network error.');
    }

    if (!res.ok) throw new Error(`Image upload failed: ${res.status}`);
    return res.json();
  }

  // ── Public API ───────────────────────────────────────────────────────────────
  return { login, logout, ping, getProjects, pullDelta, pushChanges, uploadImage,
           getStock, postWriteoff, postEvent };
})();
