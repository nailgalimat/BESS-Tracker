"""PM checklists across the wire: the desktop plans them, the phone fills them
in, the desktop takes the answers back.

The server is a real one started here on localhost; the phone is played by raw
API calls under a technician account, the desktop by the real sync_client over
a fresh database. What this pins down:

  * the planned checklist reaches the phone with its items and its exclusions
  * what the phone ticks comes back into the desktop tables
  * a checklist corrected in the office after the phone sent it is NOT
    clobbered — last writer by updated_at wins, and both sides stamp UTC
  * a technician cannot publish templates or re-plan the campaign
"""
import _harness as H            # must be first
import os
import socket
import subprocess
import sys
import time
import uuid

import requests

import database.db_manager as dbm
from services.sync_config import sync_config
import services.sync_client as sc
import services.checklist_pm_service as cs

PM_DIR = os.environ.get('BESS_PM_DIR',
                        r'C:\Users\user1\Desktop\ACWA BESS Tashkent\PM')
PCS = os.path.join(PM_DIR, '01) PCS Checklist.xlsx')

with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    PORT = s.getsockname()[1]
URL = 'http://127.0.0.1:{}'.format(PORT)
ADMIN_PW = 'pw-' + uuid.uuid4().hex[:16]
TECH_PW = 'tp-' + uuid.uuid4().hex[:16]
env = dict(os.environ,
           DATABASE_URL='sqlite:///' + os.path.join(H.WORK, 'server.db').replace('\\', '/'),
           UPLOAD_DIR=os.path.join(H.WORK, 'uploads'), SECRET_KEY='t-' + uuid.uuid4().hex,
           FIRST_ADMIN_USERNAME='admin', FIRST_ADMIN_PASSWORD=ADMIN_PW)
log = open(os.path.join(H.WORK, 'server.log'), 'w')
srv = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1',
                        '--port', str(PORT)], cwd=os.path.join(H.MVP, 'backend'),
                       env=env, stdout=log, stderr=subprocess.STDOUT)
SETTLE = 2.3      # the server serves a row once its second is 2 s past

try:
    for _ in range(60):
        try:
            if requests.get(URL + '/healthz', timeout=1).status_code == 200:
                break
        except requests.RequestException:
            time.sleep(0.5)
    else:
        H.check(False, 'test server did not start (see server.log)')
        H.finish()

    H.fresh_db('checklist_sync.db')
    conn = dbm.get_connection()
    conn.execute("INSERT INTO projects (name, num_zones, num_blocks, num_containers, "
                 "project_type) VALUES ('TK', 2, 16, 4, 'BESS')")
    PID = conn.execute("SELECT id FROM projects").fetchone()[0]
    for z in (1, 2):
        for b in range(1, 9):
            conn.execute("INSERT INTO containers (project_id, zone_number, block_number, "
                         "container_index, container_type, serial_number) "
                         "VALUES (?,?,?,1,'LC Cabinet',?)", (PID, z, b, 'S%d%d' % (z, b)))
    conn.commit(); conn.close()

    # ── a template: the customer's own workbook when it is on this computer,
    # otherwise a small one, so the sync itself is always tested ────────────
    if os.path.isfile(PCS):
        TID = cs.import_template(PID, PCS)
        source = os.path.basename(PCS)
    else:
        conn = dbm.get_connection()
        cur = conn.execute(
            "INSERT INTO checklist_templates (uuid, name, description, "
            "container_type, scope, project_id, kind, source_file, source_sha, "
            "progress_row, updated_at) VALUES (?,'PCS test','','PCS','block',?,"
            "'PCS','(none)','',20,datetime('now'))", (str(uuid.uuid4()), PID))
        TID = cur.lastrowid
        for n, (no, text) in enumerate((('1', 'Check the cabinet door seals'),
                                        ('2', 'Check the cooling fans'),
                                        ('3', 'Check the RMU'))):
            conn.execute(
                "INSERT INTO checklist_items (template_id, order_num, category, "
                "description, expected, s_no, equipment, excel_row, added) "
                "VALUES (?,?,'Visual',?,'',?,'PCS',?,0)", (TID, n, text, no, 8 + n))
        conn.commit(); conn.close()
        source = 'a substitute template (the customer workbook is not here)'
    ITEMS = cs.items(TID)
    print('   note   template from', source, '-', len(ITEMS), 'items')

    sc.login(URL, 'admin', ADMIN_PW)                  # save() is neutered by the harness
    AH = {'Authorization': 'Bearer ' + sync_config.access_token}
    requests.post(URL + '/auth/register', headers=AH, json={
        'username': 'tech1', 'password': TECH_PW, 'role': 'technician'})
    ptok = requests.post(URL + '/auth/login', json={
        'username': 'tech1', 'password': TECH_PW,
        'device_id': 'phone-P'}).json()['access_token']
    PH = {'Authorization': 'Bearer ' + ptok}

    # ── the desktop plans a campaign and publishes it ────────────────────────
    skipped = ITEMS[-1]['id']
    uuids = cs.plan_runs(PID, TID, [12, 13], '2026-09-21', campaign='PM Sep 2026',
                         excluded_item_ids=[skipped])
    st = sc.push_checklists()
    H.check(st['pushed'] == 2 and st['errors'] == 0,
            'the desktop publishes the two planned checklists: {}'.format(st))

    got = requests.get(URL + '/checklists/assigned', headers=PH,
                       params={'project_id': PID}).json()
    H.check(len(got['runs']) == 2 and len(got['templates']) == 1,
            'the phone gets both checklists and the template they need')
    tpl = got['templates'][0]
    H.check(len(tpl['items']) == len(ITEMS)
            and all(i.get('text') for i in tpl['items']),
            'the items travel with the template — the phone works offline')
    run12 = [r for r in got['runs'] if r['plant_block'] == 12][0]
    H.check(run12['campaign'] == 'PM Sep 2026' and run12['run_date'] == '2026-09-21',
            'it knows its block, its campaign and its date')
    H.check(run12['results'][str(skipped)]['result'] == cs.EXCLUDED,
            'the item left out of the campaign arrives marked Excluded')

    # ── the phone fills it in ────────────────────────────────────────────────
    fill = {}
    for i, it in enumerate(ITEMS):
        if it['id'] == skipped:
            continue
        fill[str(it['id'])] = ({'result': cs.NOK, 'comment': 'BESS 3: door seal torn'}
                               if i == 1 else {'result': cs.OK, 'comment': ''})
    r = requests.post(URL + '/checklists/runs/' + run12['uuid'] + '/results',
                      headers=PH, json={'results': fill, 'status': 'Done',
                                        'ptw_no': 'PTW-2609-140', 'filled_by': 'tech1'})
    H.check(r.status_code == 200 and r.json()['status'] == 'Done',
            'the phone sends what it ticked: HTTP {}'.format(r.status_code))
    H.check(requests.post(URL + '/checklists/runs/nope/results', headers=PH,
                          json={'results': {}}).status_code == 404,
            'an unknown checklist is a 404, not a silently created one')

    time.sleep(SETTLE)
    st = sc.pull_checklists()
    H.check(st['applied'] >= 1 and st['errors'] == 0,
            'the desktop takes the filled checklist back: {}'.format(st))
    det = cs.run_detail(run12['uuid'])
    nok = [i for i in det['items'] if i['result'] == cs.NOK]
    H.check(det['status'] == 'Done' and det['ptw_no'] == 'PTW-2609-140'
            and det['signed_by'] == 'tech1',
            'status, PTW and who filled it arrive with it')
    H.check(len(nok) == 1 and 'door seal torn' in nok[0]['comment'],
            'the deviation and its comment are in the desktop tables')
    H.check([i for i in det['items'] if i['item_id'] == skipped][0]['result'] == cs.EXCLUDED,
            'and the excluded item is still excluded, not blanked')
    p = cs.progress(run12['uuid'])
    H.check(p['done'] == len(ITEMS) - 1 and p['nok'] == 1,
            'progress on the desktop matches what the phone did: {}'.format(p))

    # ── the office corrects it afterwards: the phone must not win ────────────
    time.sleep(1.1)                                   # stamps are per second
    cs.save_results(run12['uuid'], {nok[0]['item_id']: {
        'result': cs.NOK, 'comment': 'BESS 3: seal replaced, closed'}},
        source='desktop')
    sync_config.checklist_cursor = '0'                # re-pull everything
    st = sc.pull_checklists()
    det = cs.run_detail(run12['uuid'])
    nok = [i for i in det['items'] if i['result'] == cs.NOK]
    H.check('seal replaced' in nok[0]['comment'],
            'a correction made in the office survives the next pull ({})'.format(st))
    sc.push_checklists()
    back = requests.get(URL + '/checklists/assigned', headers=PH,
                        params={'project_id': PID}).json()
    srv_run = [r for r in back['runs'] if r['uuid'] == run12['uuid']][0]
    H.check('seal replaced' in srv_run['results'][str(nok[0]['item_id'])]['comment'],
            'and reaches the phone on its next refresh')

    # ── roles ───────────────────────────────────────────────────────────────
    r = requests.put(URL + '/checklists/templates', headers=PH,
                     json={'templates': [{'uuid': str(uuid.uuid4()), 'project_id': PID,
                                          'name': 'x', 'kind': '', 'items': []}]})
    H.check(r.status_code == 403,
            'a technician cannot publish a template: HTTP {}'.format(r.status_code))
    r = requests.put(URL + '/checklists/runs', headers=PH, json={'runs': []})
    H.check(r.status_code == 403, 'nor re-plan the campaign')

    # ── a run this desktop never planned is ignored, not invented ───────────
    stranger = {'uuid': str(uuid.uuid4()), 'project_id': PID,
                'template_uuid': 'whatever', 'plant_block': 3,
                'campaign': 'other desktop', 'run_date': '2026-09-21',
                'status': 'Done', 'results': {}, 'updated_at': '2026-09-21 10:00:00'}
    H.check(cs.apply_remote_run(stranger) is False and not cs.runs(PID, block=3),
            "another desktop's checklist is ignored here")
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except subprocess.TimeoutExpired:
        srv.kill()
    log.close()

H.finish()
