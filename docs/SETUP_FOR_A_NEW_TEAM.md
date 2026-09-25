# BESS Tracker — setting it up for a new team

For a field-service engineer setting the app up for their own team, on their own
PC, for their own plants. No knowledge of the code is needed.

Read section A first. Sections B–E only matter if your technicians will use
phones. The app is fully usable without any server.

Requirements: Windows 10 or 11. No administrator rights, no Python, no database
server.

---

## A. Desktop only (no phones)

1. **Install.** Run `BESS_Tracker_Setup_<version>.exe`. It installs for the
   current user into `%LOCALAPPDATA%\Programs\BESS Tracker` and asks nothing
   except whether you want a desktop shortcut. It must stay in a folder your
   Windows account can write to, because the database sits next to the
   programme — do not move it into `C:\Program Files`.

2. **Launch it.** The first screen is **Select a project**, and on a new
   installation it says *"No projects yet — click "New project" to create your
   first one."* The installer ships an empty database: no other team's plants,
   records or customers are in it.

3. **Create the first project.** Click the **New project** card. In
   **Create New Project**:
   - *Step 1 — Project Information*: **Project Name**, an optional
     **Description**, and **Project Type** — `BESS`, `PV String` or
     `PV Central`. The type decides which equipment the next step creates, and
     it cannot be changed later, so pick it correctly.
   - *Step 2 — Zone & Block Configuration* (BESS): set **Number of Zones** and
     click **Apply**. One card appears per zone; in each card set **Blocks:**
     (how many blocks that zone has) and **Cont/Blk:** (containers per block).
     Tick **Transformer** / **RMU** under *Optional per block* if each block has
     them.
   - Click **⚙ Generate Table**, check the equipment list it produced, then
     **💾 Save Project**.

4. **Serial numbers.** Two ways, both offline:
   - **Project → Serial numbers** opens *Edit Container Serials & Types*: pick
     the project, **📂 Load Containers**, type the serial and type of each one,
     **💾 Save Changes**. For a large plant use **📋 Download Template**, fill
     the spreadsheet, then **📂 Import from Excel**.
   - **Equipment → Identity** shows and edits the same data per block.

5. **Everything above works with no server and no internet.** All nine menu
   items are local: *Today, Work, Plan, Equipment, Availability, Monthly
   report, Analysis, Spare parts, Project*. That includes work records, PM
   records, planned campaigns, checklists filled on the desktop, availability
   inputs and exclusions, the monthly customer report (PDF/DOCX), the analysis
   pages and the spare-parts stock. All of it is stored in one file,
   `pv_bess_tracker.db`, next to the programme.

6. **Only these need a server:** technicians' phones, sending a planned job to
   a named technician, publishing a checklist campaign to phones, and photos
   taken on a phone. If your team works from the desktop only, stop here.

---

## B. Adding phones: you need your own server

### Why it must be your own

Do **not** point your desktop at another team's server URL, even to try it:

- The desktop **publishes its whole project list** on every sync
  (`PUT /projects`), and that endpoint is replace-all: it deletes every project
  on the server that is not in the pushed list. Two desktops on one server
  therefore erase each other's plants on alternate syncs.
- Project ids are local numbers. Both teams' "project 1" are the same row on
  the server, so records would attach to the wrong plant.
- There is **no separation between organisations**. Every signed-in account can
  list every project (`GET /projects`), and an `admin` account sees every
  record of every plant on that server.

One server per team. It costs nothing to keep them separate.

### Deploying it

1. Get access to the repository — your own fork, or a copy of it in an account
   you control. Render deploys from a Git repository you own.
2. In Render: **New → Blueprint**, pick that repository. `render.yaml` in the
   repository root describes the whole service: one Docker web service built
   from `backend/Dockerfile`, plus a 1 GB persistent disk mounted at
   `/var/data`, which holds the server database (`backend.db`) and the uploaded
   photos. You do not have to configure any of that by hand.
3. Render will ask for the value of **`FIRST_ADMIN_PASSWORD`** (the Blueprint
   deliberately does not contain it). **Set a strong one now.** On its first
   start the server creates one administrator — username `admin` unless you
   change `FIRST_ADMIN_USERNAME` — and it only does this while no user exists.
   There is no "change password" screen anywhere, so a server that first booted
   with the built-in default keeps that password until the disk is wiped. If
   that happens before you have real data, delete the service together with its
   disk and apply the Blueprint again.
4. Wait for the deploy to go live, then open
   `https://<your-service>.onrender.com/healthz` in a browser. It answers with
   a small JSON status. Note the service URL — the desktop and every phone need
   it.
5. **Plan.** `render.yaml` asks for the `starter` plan: always on, and paid. The
   free tier works, but the service sleeps when nobody uses it, so the first
   phone sync after a quiet period waits for a cold start (tens of seconds)
   before it succeeds. A desktop-only team needs no server and pays nothing.

---

## C. Connecting the desktop to your server

1. **Project → Synchronisation** opens *Sync Settings*.
2. **Server URL**: `https://<your-service>.onrender.com`.
3. **Username** / **Password**: the administrator account from step B.3. Click
   **🔑 Log In** — the tokens are stored locally in `sync_config.json` next to
   the programme, and the login survives restarts.
4. **📡 Test Connection** confirms the URL, then tick **Enable automatic sync
   (every 60 s)** and click **⚡ Sync Now** once. This first sync publishes your
   project list, which is what the phones pick a project from.
5. Sign the desktop in as the **admin** account, not as an `engineer`.
   Publishing checklist templates and campaigns is admin-only — an engineer
   account gets *"Only the office can publish checklists"* — and only an admin
   sees all records and can create users.

---

## D. Creating your team's accounts

**Project → Users and roles** opens *User Management* (it needs the desktop to
be signed in as an admin). Under **Create New User**: **Username**,
**Password** (at least 6 characters), **Role**, then **➕ Create User**. The
list below shows the accounts the server already has.

| Role | What it can do |
|---|---|
| `admin` | The office desktop. Sees every record and every project on the server. The only role that can create users and publish checklist templates and campaigns. |
| `engineer` | Can be given work and can hand work to others. In records, sees only what they wrote themselves plus what is assigned to them. |
| `technician` | The normal phone account. Sees only their own records and the jobs assigned to them; cannot publish checklists or create accounts. |

Create one account per person. Accounts are shared between the desktop and the
phones, and the desktop remembers them locally so an assigned name still shows
up on a record months later.

---

## E. The phones

1. On the phone, open `https://<your-service>.onrender.com/app/` in Chrome
   (Android) or Safari (iOS).
2. Use the browser's **Add to home screen**. It then opens like an app and
   keeps working offline.
3. Log in with that person's **technician** account. Username and password
   only — the server is already filled in, and is under *Server* if it ever has
   to be changed.
4. The **Project** selector on the record form lists the projects your desktop
   published, so sync the desktop at least once (section C.4) before the first
   phone login.
5. The first login needs a network. After that the phone works offline and
   sends its records on the next sync.
6. Before you trust it in the field, walk through `tests/PWA_OFFLINE_CHECK.md`
   in the repository — it is the manual checklist for offline behaviour,
   photos, and what must survive a lost connection.

---

## F. Backups

- The desktop database is `pv_bess_tracker.db` in the installation folder
  (`%LOCALAPPDATA%\Programs\BESS Tracker` by default). **Copying that one file
  copies every record the app holds** — plants, equipment, work records, PM,
  availability, stock, users. Close the app first, or copy the
  `pv_bess_tracker.db`, `pv_bess_tracker.db-wal` and `pv_bess_tracker.db-shm`
  trio together: SQLite keeps recent writes in the `-wal` sidecar.
- Three folders sit beside it and are **not** inside that file:
  `report_data\` (the copies of the SCADA exports each month was generated
  from, and every generated report version), `field_images\` (photos attached
  to records) and `db_backups\` if you make snapshots there. Back them up too
  if you need the monthly reports' audit trail.
- `sync_config.json`, also beside the programme, holds the server URL and your
  login tokens. Keep it out of any backup you share with anyone.
- The server's own data (its database and uploaded photos) lives on the Render
  disk at `/var/data` and survives redeploys. Treat the desktop database as the
  master copy anyway, and check what backup or snapshot options your Render
  plan offers if the server data matters to you.

---

## G. Updating

Run the newer `BESS_Tracker_Setup_<version>.exe` over the existing
installation. It upgrades in place — same entry in *Apps & features*, no second
copy — and it **never** overwrites `pv_bess_tracker.db`: the database is
installed only if it is not already there. Your projects, records and settings
come through unchanged.

Uninstalling removes the programme files only. The database, the photos and
`sync_config.json` are left where they are, so a later reinstall finds your
data again. Delete the folder by hand if you really want the data gone.

The version you are running is in the main window's title bar.
