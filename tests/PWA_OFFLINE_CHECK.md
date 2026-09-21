# PWA manual check — offline start after an update, local dates

Cannot be automated here (needs a phone, the service-worker update cycle and
airplane mode). Run it after every deploy that changes `backend/static/`.
Phone: Android Chrome with the Field Log PWA installed from
`https://bess-tracker-api.onrender.com/app/` (Add to Home screen).

## 1. The update installs
1. Deploy. On the phone, with network, open the PWA, close it fully
   (swipe it away), open it again.
2. Chrome on a PC (optional, for certainty): `chrome://inspect` → the phone's
   page → Application → Cache Storage. There must be exactly one cache,
   `bess-v12` (the `V` in `sw.js`), holding `/app/js/app.js?v=12`,
   `/app/js/db.js?v=12`, `/app/js/api.js?v=12`, `/app/css/app.css?v=12` —
   the same `?v=` as the tags in `index.html`.

## 2. It opens without network
1. Airplane mode on.
2. Close the PWA fully, open it again.
3. Pass: the home screen shows the records; ＋ → "PM / downtime" opens the form;
   a new record saves and shows "⏳ pending".
4. Fail: a blank/white screen or raw HTML — the shell was not cached under the
   URLs the page asks for.
5. Airplane mode off → the pending record is sent ("✓ sent").

## 3. Dates are the phone's local date
1. Between 00:00 and 05:00 Tashkent time (or set the phone clock to 00:30):
   ＋ → new Field Log entry and ＋ → PM / downtime.
2. Pass: the date field shows today's local date, not yesterday.

## 4. PM validation on the phone
- PM without a block → "Choose the block(s)…", nothing saved.
- PM with 0 h or 25 h → refused.
- "All" in the block picker fills 1-70 (the whole plant is an explicit choice).

## 5. Photo retry (optional)
Save an entry with a photo in airplane mode, turn the network on only briefly
so the entry is sent but the photo fails, then sync again later: the photo
appears on the desktop after the next sync.

## 6. v13: tabs, node picker, send status, update banner
1. **The v12 -> v13 step.** v13 no longer swaps itself in: an open v12 app has
   no banner, so close the app completely once (swipe it away) and reopen —
   the bottom tabs Tasks · Records · Stock · More must appear. From v13 on,
   later versions announce themselves with the banner.
2. **Login.** The Server field is folded under "Server" and already holds the
   address the app was opened from.
3. **Node picker.** ＋ → Fault / repair → Node → Zone → Block: zone 9 lists
   blocks 63–70 (the desktop must have synced once so the phone has the zone
   map); pick LC2 and BESS 3 → "Block 69 · Z9/B7 · BESS 3" on the form.
   The Number tab: type 57 → "Block 57 · Z8/B2". Recent lists the last five.
4. **Required block.** Save without a node → "Choose the node…", nothing saved.
5. **PM record.** ＋ → PM: start is pre-filled, end fills the hours, PTW No.
   saves; 0 h and 25 h are refused. On the desktop the record shows in Work and
   its hours as a PM record on Availability (once), not as a 3.2 line.
6. **Send status.** Airplane mode on → save a record: "Waiting to send" with a
   Retry button. Network on → Retry → "Sent". A record edited on the desktop
   and on the phone at the same time → "Conflict — the office decides".
7. **Update banner.** Deploy any change with V bumped (e.g. v14) while the app is
   open with a half-written record → "New version available" appears; tap
   Reload → the app reloads on the new version.

## 7. v14: the photo stamp (needs a real camera)
1. **The stamp.** ＋ → Fault / repair → choose the node → 📷 Add Photos →
   take a photo → Save. Open the record: the photo carries a dark strip along
   its bottom with the project and `20.09.2026 14:32`, the node
   (`Block 33 · LC1 · BESS 3`) and the coordinates.
2. **Permission.** The first photo asks for the location — allow it. Answer
   "Block" once and the photo still saves, with the first two lines only.
3. **Underground / inside a container.** With no GPS fix the stamp waits at
   most 10 s and then goes without coordinates; saving is never blocked.
4. **Orientation and size.** A portrait photo stays portrait (not on its
   side), and the photo that reaches the desktop is well under 1 MB.
5. **On the desktop.** After a sync, Work → the record shows the photo, and
   `Documents\BESS Tracker Photos\<project>\<date Block … - fault>\` holds the
   same stamped file.

## 8. v15: checklists on the phone

Prepare on the desktop: Plan → Checklists → ⤓ Import checklist… (the customer's
`01) PCS Checklist.xlsx`) → ＋ Plan checklists… for two blocks, untick one item
and write its note ("RMU not in this PM"), then sync (🔄 in the toolbar) so the
server has them.

1. **They arrive.** On the phone, with network: 🔄 Sync. Tasks shows a
   "PM checklists" card ("2 still to finish"); More → 📋 PM checklists lists
   Block N · the checklist name · `0 of 11 done · 1 not in this campaign`.
2. **Offline is the normal case.** Airplane mode on, close the app fully, open
   it again → More → PM checklists: the list and the item text are still there
   (they live in IndexedDB, not in the page).
3. **Filling it.** Open a checklist: items grouped by equipment, each with
   OK / NOK / N/A and a comment. Tap OK on a few, NOK on one and write
   "BESS 3: door seal torn". The strip at the top counts up as you tap. Tap the
   same answer again → it clears. Close the app mid-checklist and reopen: every
   answer is still there ("Waiting to send" on the card).
4. **Out of scope.** The item the office unticked is grey and dashed, says
   "Not in this campaign · RMU not in this PM", and has no buttons — it cannot
   be ticked, and the row still goes to the customer empty with that note.
5. **PTW, serial, signature.** PTW No. and the signature are optional; the
   serial is pre-filled when the project knows it.
6. **Sending.** Airplane mode off → Send (or 🔄 Sync): the card turns "Sent".
   Fail case: with airplane mode on, Send says the checklist stays on the phone
   and nothing is lost.
7. **Back in the office.** Desktop sync → Plan → Checklists: the block's row
   shows `11/12`, the NOK count in red, "Done" and Filled on "Phone"; open it
   and the comment is there. ⤒ Export this one… writes the customer's own
   workbook with the boxes ticked (OK = ticked, NOK = unticked + comment) and
   the excluded row empty with its note.
8. **The office wins last.** Correct the NOK comment on the desktop, sync twice:
   the phone shows the corrected text, and the desktop text is not overwritten
   by the phone's older copy.

## 9. v16: tasks from the office

Needs two accounts on the server (Project → User management): `tech1` as
**technician**, and the office's own admin. The phone in this section is logged
in as `tech1`; the desktop as the admin.

1. **The v15 → v16 step.** With the app open, deploy v16: the "New version
   available" banner appears; tap Reload. More → the version reads `v16`.
2. **One job, by hand.** Desktop → Work → ＋ New record: block 5, LC1, BESS 3,
   fault "Antifreeze low level", "What was done" = what you want done, status
   **Open**, **Assigned to = tech1**. Save, then sync (🔄).
   - Work shows it in the **Assigned to** column, and the chip "tech1" narrows
     the list to it.
   - On the phone: 🔄 Sync → **Tasks** shows it with a blue edge, the chip
     "Assigned", "From admin · due <date>" and the button **Open the job**.
   - On a second phone logged in as another technician the job must **not**
     appear — it is not theirs.
3. **Doing it.** Open the job: it says where (Block 5 · LC1 · BESS 3), who it
   is from and the PTW. "What was done" is empty. Fill it, put 09:10 → 10:40 in
   (the hours fill themselves), write a note for the office, tap
   **✓ Done — send to the office**.
   - Desktop sync → the **same** record in Work is now Done, with the text, the
     hours and the note. There must be **no second record** for that block-day.
4. **A campaign.** Desktop → Plan → ＋ New PM campaign…: pick the blocks, set
   **Assignee = tech1** (the list comes from the server, so sync once first) →
   Create → answer **Yes** to "Send these jobs to tech1 now?".
   - Plan → Schedule: every job's Assignee reads "📱 tech1".
   - Phone: 🔄 Sync → Tasks · **Week** lists the jobs for the coming days, one
     per block, each headed by the campaign name.
   - Sending the same campaign again (select the rows → 📱 Send to a
     technician…) must **not** double the jobs on the phone — the same ones are
     simply reassigned.
5. **The report is not charged twice.** Monthly report → Check: a published PM
   job never appears in section 3.2 (it is held back as "PM (see PM records)");
   its hours reach the customer only through the PM record the desktop writes
   when the job is marked done on the Plan page.
6. **Offline.** Airplane mode on → open an assigned job, fill it in, tap Done:
   it is kept on the phone ("Waiting to send"). Airplane mode off → 🔄 Sync →
   the office has it.
7. **It stays the office's job.** On the phone, Records → an assigned job shows
   "From admin" instead of a Delete button. The office can hand it to another
   technician on the desktop; after the next sync it leaves the first phone's
   Tasks and appears on the second.

## 10. v18: several blocks in one record

The same alarm on blocks 4, 35 and 60 is one trip: one form, one set of
photos, and one record per block. Phone on **v18** (More → the version reads
`v18`), project Tashkent (70 blocks, 9 zones).

1. **The v17 → v18 step.** With the app open, deploy v18: the "New version
   available" banner appears; tap Reload. More → the version reads `v18`.
2. **One block is unchanged.** ＋ → Work record → Node: tap zone 2, tap a
   block, tap **Use Block N** — the same two taps as before. The form reads
   `Block N · Z2/Bx`, Save writes **one** record, and Records shows one card.
3. **Three blocks, one form.** ＋ → Work record → Node: tap block 4 in zone 1,
   switch to zone 5 and tap 35, zone 8 and tap 60. The line at the top reads
   `3 blocks · 4, 35, 60 — one record each, same text`, the tiles stay dark in
   every zone, and the button says **Use 3 blocks · 4, 35, 60**. Choose LC1 and
   BESS 3 once. The form reads `3 blocks · 4, 35, 60 · LC1 · BESS 3`.
   - Write the fault (Antifreeze Low Level), what was done, the PTW and an
     internal note. Take **two photos**. Save.
   - Records shows **three** cards — Block 4, Block 35, Block 60 — with the
     same text, fault, PTW and LC/device. Only the Block 4 card carries the
     photos; open Block 35 and its internal note ends with "Photos on the
     Block 4 record."
   - The photo stamp still names the node it was taken at (Block 4), not all
     three.
4. **It reaches the office.** 🔄 Sync → desktop → Work: three rows, blocks 4,
   35 and 60, one customer text each. The monthly report's section 3.2 has a
   line per block. The internal note is not in the ⤓ Excel export.
5. **A PM on three blocks.** ＋ → PM record → pick three blocks → the line
   under the hours reads "these hours are charged to EACH of the 3 blocks".
   Enter 4 h, save.
   - Desktop → sync → Monthly report → PM: **three** PM records, **4 h each**
     (not 12 h shared out, not 1.33 h each). Section 3.2 has **no** line for
     them — the hours reach the customer through 3.1.
   - Syncing again must not double them (the event is applied once).
6. **All blocks.** ＋ → record → Node → **All blocks**: every block of the
   project is chosen, the button reads `Use 70 blocks · 1-70`. **Clear**
   empties it and the Use button goes dead. (Save only if you really mean 70
   records.)
7. **Number tab, no zones mirrored.** On a project without zones: Number → type
   12 → **＋ Add this block, type another** → type 15 → **Use 2 blocks ·
   12, 15**.
8. **Offline.** Airplane mode on → write a three-block record → three cards say
   "Waiting to send". Airplane mode off → 🔄 Sync → all three reach the office,
   and the photos are uploaded **once**.
