"""services/team_service.py — who a job can be given to.

The accounts live on the sync server; the desktop keeps a mirror of them in
`sync_users` so the "Assigned to" picker still works when the office is
offline — and so a record pulled months later can still show the technician's
name rather than a bare user id.

The server owns these rows: `save_users` replaces the mirror on every sync and
nothing here is ever pushed back. A user that disappears from the server is
kept as inactive rather than deleted, because old records still name them.
"""
from typing import List, Optional

from database.db_manager import get_connection

# Who the office may hand work to. Admins and engineers plan it; technicians
# do it. An admin can be assigned as well — sometimes the office does the job.
ASSIGNABLE_ROLES = ('technician', 'engineer', 'admin')


def save_users(users: List[dict]) -> int:
    """Replace the local mirror with what the server sent. Returns the count."""
    rows = [(str(u.get('id') or ''), (u.get('username') or '').strip(),
             (u.get('role') or '').strip().lower())
            for u in (users or []) if u.get('id')]
    if not rows:
        return 0
    seen = {r[0] for r in rows}
    conn = get_connection()
    try:
        for uid, name, role in rows:
            conn.execute("""
                INSERT INTO sync_users (id, username, role, is_active, updated_at)
                VALUES (?, ?, ?, 1, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    username=excluded.username, role=excluded.role,
                    is_active=1, updated_at=excluded.updated_at
            """, (uid, name, role))
        marks = ', '.join('?' for _ in seen)
        conn.execute(f"UPDATE sync_users SET is_active=0 WHERE id NOT IN ({marks})",
                     list(seen))
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def users(active_only: bool = True, roles=ASSIGNABLE_ROLES) -> List[dict]:
    """The people this desktop knows about: [{id, username, role, is_active}]."""
    q = "SELECT id, username, role, is_active FROM sync_users"
    where, params = [], []
    if active_only:
        where.append("is_active = 1")
    if roles:
        where.append("role IN ({})".format(', '.join('?' for _ in roles)))
        params += list(roles)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY role='technician' DESC, username"
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(q, params)]
    finally:
        conn.close()


def name_for(user_id: str) -> str:
    """The username behind a user id, or '' when this desktop has never seen
    it. Records carry the name with them, so this is only a fallback."""
    if not user_id:
        return ''
    conn = get_connection()
    try:
        r = conn.execute("SELECT username FROM sync_users WHERE id=?",
                         (str(user_id),)).fetchone()
        return (r['username'] if r else '') or ''
    finally:
        conn.close()


def find(name_or_id: str) -> Optional[dict]:
    """A user by id or by username — the planner has only the typed name."""
    key = (name_or_id or '').strip()
    if not key:
        return None
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT id, username, role, is_active FROM sync_users "
            "WHERE id=? OR lower(username)=lower(?) ORDER BY is_active DESC LIMIT 1",
            (key, key)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()
