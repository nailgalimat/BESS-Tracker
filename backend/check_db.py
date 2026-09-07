import sqlite3
conn = sqlite3.connect(\"C:/Users/user1/MVP/backend/backend.db\")
conn.row_factory = sqlite3.Row
total = conn.execute(\"SELECT COUNT(*) FROM work_log_entries\").fetchone()[0]
print(\"Total in backend.db:\", total)
for r in conn.execute(\"SELECT id, project_id, category, log_date, description, origin_device, created_at FROM work_log_entries ORDER BY created_at DESC LIMIT 5\").fetchall():
    print(dict(r))
conn.close()

