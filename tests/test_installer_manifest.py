"""What the installer ships, read straight out of installer.iss.

The installer once carried the owner's live database, and with it a customer's
data, to whoever ran the setup. This test reads the .iss as text and pins the
promises that stop that happening again, plus the two that keep an upgrade safe:
the database is installed only if it is not already there, and the install folder
is user-writable (the database lives next to the exe, so Program Files cannot
work).

It also proves the guard in tools/build_release.py actually rejects a bad .iss,
on a poisoned copy in the work folder — the real file is never edited here.
"""
import _harness as H            # must be first

import importlib.util
import os

MVP = H.MVP
ISS = os.path.join(MVP, 'installer.iss')

spec = importlib.util.spec_from_file_location(
    'build_release', os.path.join(MVP, 'tools', 'build_release.py'))
br = importlib.util.module_from_spec(spec)
spec.loader.exec_module(br)

text = open(ISS, encoding='utf-8').read()
low = text.lower()

# ── nothing private ─────────────────────────────────────────────────────────
H.check('dist\\pv_bess_tracker.db' not in low,
        'the live database is not referenced')
H.check('sync_config.json' not in low, 'sync_config.json is not referenced')
for nope in ('db_backups', 'field_images', 'report_data'):
    H.check(nope not in low, '{} is not shipped'.format(nope))

# ── what it does ship ───────────────────────────────────────────────────────
sources = [ln.strip() for ln in text.splitlines()
           if ln.strip().lower().startswith('source:')]
H.check(len(sources) == 3, 'exactly three Source lines: {}'.format(len(sources)))
H.check(any('dist\\bess tracker.exe' in s.lower() for s in sources),
        'the exe')
H.check(any('docs\\setup_for_a_new_team.md' in s.lower() for s in sources),
        'the setup guide')

db_lines = [s for s in sources if 'build\\clean\\pv_bess_tracker.db' in s.lower()]
H.check(len(db_lines) == 1, 'the clean database, once')
if db_lines:
    H.check('onlyifdoesntexist' in db_lines[0].lower(),
            'flagged onlyifdoesntexist, so an upgrade keeps existing data')
    H.check('uninsneveruninstall' in db_lines[0].lower(),
            'and uninsneveruninstall, so uninstalling keeps it')

# ── where it installs ───────────────────────────────────────────────────────
default_dir = next((ln for ln in text.splitlines()
                    if ln.lower().startswith('defaultdirname')), '')
H.check('{localappdata}' in default_dir.lower(),
        'DefaultDirName is under {{localappdata}}: {}'.format(default_dir.strip()))
directives = '\n'.join(ln for ln in low.splitlines()
                       if not ln.strip().startswith(';'))
H.check('{autopf}' not in directives and 'program files' not in directives,
        'never Program Files — the database sits next to the exe')
H.check('privilegesrequired=lowest' in low.replace(' ', ''),
        'PrivilegesRequired=lowest')

# ── version, identity, uninstall ────────────────────────────────────────────
H.check('#include "installer\\version.iss"' in low,
        'it includes the generated version file')
H.check('#error' in low, 'and fails loudly when that file is missing')
H.check('{#appversion}' in low, 'AppVersion comes from that file')
H.check('appid={{515103f8-8cab-4dad-899d-cc2e04639d79}' in low.replace(' ', ''),
        'the AppId is a fixed GUID, so versions upgrade in place')
H.check('type: dirifempty' in low and 'type: files' not in low,
        'uninstall only removes the folder if it is empty')

# ── the build script's guard ────────────────────────────────────────────────
H.check(br.scan_iss(text) == [], 'build_release accepts the real .iss')

poison_dir = os.path.join(H.WORK, 'poisoned')
os.makedirs(poison_dir, exist_ok=True)
for name, extra in (
        ('live_db.iss', 'Source: "dist\\pv_bess_tracker.db"; DestDir: "{app}"\n'),
        ('tokens.iss',  'Source: "sync_config.json"; DestDir: "{app}"\n')):
    path = os.path.join(poison_dir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text + extra)
    problems = br.scan_iss(open(path, encoding='utf-8').read())
    H.check(bool(problems), '{} is rejected: {}'.format(name, problems[:1]))
    try:
        br.check_iss(path)
        raised = None
    except SystemExit as e:
        raised = e.code
    H.check(raised == 2, 'check_iss stops the build for it (exit {})'.format(raised))

# a .iss that forgot the flag is caught too
path = os.path.join(poison_dir, 'no_flag.iss')
with open(path, 'w', encoding='utf-8') as f:
    f.write(text.replace('onlyifdoesntexist', ''))
H.check(bool(br.scan_iss(open(path, encoding='utf-8').read())),
        'a missing onlyifdoesntexist is rejected')

H.finish()
