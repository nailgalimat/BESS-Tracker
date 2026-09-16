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
