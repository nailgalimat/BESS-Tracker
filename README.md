# ⚡ PV & BESS Maintenance Tracker

A desktop application for field service engineers to track daily maintenance
activities on PV (solar) and BESS (battery energy storage) systems.

---

## 🚀 Quick Start

### 1. Install Python
Download from https://www.python.org/ (Python 3.8 or higher recommended)

### 2. Install dependencies
Open a terminal in this folder and run:
```
pip install -r requirements.txt
```

### 3. Run the app
```
python main.py
```

---

## 📁 Project Structure

```
pv_bess_tracker/
├── main.py                  ← Run this to start the app
├── requirements.txt
├── pv_bess_tracker.db       ← Created automatically on first run
│
├── database/
│   └── db_manager.py        ← SQLite connection + table creation
│
├── models/
│   └── models.py            ← Data classes (Project, Container, LogEntry)
│
├── services/
│   ├── project_service.py   ← Project & container DB operations
│   ├── log_service.py       ← Daily log DB operations
│   └── report_service.py    ← Filtering + Excel export
│
└── ui/
    ├── main_window.py       ← Main window with tabs
    ├── project_dialog.py    ← New project creation wizard
    ├── daily_log_form.py    ← Daily maintenance input form
    └── reports_view.py      ← Reports table + export
```

---

## 🔧 How to Use

### Step 1 — Create a Project
1. Click **"➕ New Project"** in the toolbar
2. Fill in project name and description
3. Set the number of **Zones**, **Blocks per Zone**, and **Containers per Block**
4. Click **"Generate Container Table"**
5. For each container, set its **Type** (Battery / PCS / Communication / etc.) and **Serial Number**
6. Click **"Save Project"**

### Step 2 — Record Daily Activities
1. Go to the **📋 Daily Log** tab
2. Select your **Project** and **Date**
3. Select **Zone → Block → Container** (type and serial fill automatically)
4. Enter **Material Number** and **Quantity**
5. Add an optional **Comment**
6. Click **"✅ Save Log Entry"**

### Step 3 — Generate Reports
1. Go to the **📊 Reports** tab
2. Set filters (project, date range, zone, block)
3. Click **"🔍 Load Report"** to preview data
4. Click **"📥 Export to Excel"** to save as .xlsx

---

## 🗄️ Database

The app creates a file called `pv_bess_tracker.db` in the same folder as `main.py`.
This is your data file. **Back it up regularly!**

To view it manually, use [DB Browser for SQLite](https://sqlitebrowser.org/) (free tool).

---

## 🔮 Future Improvements (Roadmap)

1. **Excel import** for container configuration (bulk setup from spreadsheet)
2. **Material number catalog** with autocomplete
3. **Photo attachments** for log entries
4. **Project dashboard** with summary statistics
5. **Multi-user support** (move from SQLite to PostgreSQL)
6. **Offline sync** with cloud backup
7. **PDF report generation**
8. **Equipment health status** tracking (OK / Warning / Fault)

---

## ⚠️ Troubleshooting

**"Module not found" error**
→ Run `pip install -r requirements.txt` again

**App doesn't start**
→ Make sure you're using Python 3.8+: `python --version`

**Database errors**
→ Delete `pv_bess_tracker.db` to start fresh (this deletes all data)
