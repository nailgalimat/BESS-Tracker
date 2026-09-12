"""
services/sync_cursor.py
------------------------
The pull cursors are `updated_at` strings with one-second resolution, and every
pull endpoint returns rows strictly newer than the cursor. So a row written in
the same second as the newest row a device has already received was never sent
to it: the phone flushes three PM events inside one second, the desktop pulls
between the first and the third, its cursor becomes that second — and the
third event never arrives, while the phone shows it as sent.

The fix is to serve only rows whose second has fully passed. Once a second is
SETTLE_SECONDS in the past, every write stamped with it has committed, so a
cursor can no longer step over one still being written. Server-side only: the
cursor format and every client — including installed APKs — are unchanged; a
row simply arrives on the next sync instead of this one.

Not fixed by this: more than a page of rows sharing one second (page sizes are
200/500) still loses the overflow; that needs a compound (updated_at, id)
cursor.
"""
from datetime import datetime, timedelta, timezone

SETTLE_SECONDS = 2


def settled_before() -> str:
    """The newest `updated_at` a pull may return right now."""
    t = datetime.now(timezone.utc) - timedelta(seconds=SETTLE_SECONDS)
    return t.strftime("%Y-%m-%d %H:%M:%S")
