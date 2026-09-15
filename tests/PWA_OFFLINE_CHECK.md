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
