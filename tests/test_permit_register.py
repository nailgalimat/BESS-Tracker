"""The CPTW permit register: day and month swapped by Excel (D2).

The register is typed as day/month/year text; where the day was 12 or less
Excel stored a datetime with day and month swapped. CPTW-APRS-312 read issue
2026-10-04 (datetime) with valid till 13/4/2026 (text), and the April permit
was listed in October's section 6. A made-up register (no customer data)
reproduces each pattern seen in the real one.
"""
import _harness as H            # must be first
import datetime as dt
import os

import openpyxl

import services.tashkent_report_service as T

path = os.path.join(H.WORK, 'register.xlsx')
wb = openpyxl.Workbook()
ws = wb.active
ws.title = 'CPTW Register'
ws.append(['Commissioning Permit To Work Register'])
ws.append(['Sr. No', 'CPTW No', 'Issued To', 'Area', 'Discription', 'Issue Date',
           'Valid till', 'PTW Status'])
D = dt.datetime
rows = [
    # no, to, area, title, issue, valid, status
    ('CPTW-APRS-312', 'SUNGROW', 'BESS', 'Fan replacement', D(2026, 10, 4), '13/4/2026', 'Closed'),
    ('CPTW-APRS-616', 'SUNGROW', 'RMU-06', 'BESS Preventive Maintenance', '02/10/2026', '03/10/2026', 'Closed'),
    ('CPTW-APRS-617', 'HEEC', 'BESS', 'Painting', '15/10/2026', '20/10/2026', 'Closed'),
    # issue as text, "valid till" swapped by Excel: the issue date stands
    ('CPTW-APRS-276', 'Sungrow Power', 'BESS', 'Cable tray', '24/10/2026', D(2026, 3, 11), 'Closed'),
    # both swapped: really 06.10 .. 12.10
    ('CPTW-APRS-299', 'SUNGROW', 'BESS', 'KKS', D(2026, 6, 10), D(2026, 12, 10), 'Closed'),
    # an October permit stored as April: issue 2026-04-10 (= 04.10), valid 13/10/2026
    ('CPTW-APRS-620', 'SUNGROW', 'RMU-21', 'BESS Preventive Maintenance', D(2026, 4, 10), '13/10/2026', 'Closed'),
    # unswappable: datetime issue, a year typo in "valid till"
    ('CPTW-APRS-380', 'SUNGROW', 'BESS', 'Inspection', D(2026, 10, 5), '13/5/2025', 'Closed'),
    # a true October datetime with day > 12: kept as is
    ('CPTW-APRS-630', 'SUNGROW', 'BESS', 'Filter cleaning', D(2026, 10, 20), None, 'Open'),
    # text issue after a text "valid till": cannot be swapped, kept
    ('CPTW-APRS-504', 'sungrow', 'BESS', 'Labeling', '13/10/2026', '18/6/2026', 'Closed'),
]
for i, (no, to, area, title, iss, val, st) in enumerate(rows, start=1):
    ws.append([i, no, to, area, title, iss, val, st])
ws.append([None] * 8)                       # trailing empty rows, as in the real file
wb.save(path)

print('=== October ===')
unclear = []
oct_ = T.load_cptw_register(path, 2026, 10, unclear_out=unclear)
nos = [r['no'] for r in oct_]
print('   ', nos)
H.check('CPTW-APRS-312' not in nos, '312 (2026-10-04 datetime, valid 13/4/2026) is not October')
H.check({'CPTW-APRS-616', 'CPTW-APRS-617', 'CPTW-APRS-276', 'CPTW-APRS-299', 'CPTW-APRS-620',
         'CPTW-APRS-630', 'CPTW-APRS-504'} == set(nos), 'the real October permits are all listed')
by = {r['no']: r for r in oct_}
H.check(by['CPTW-APRS-616']['date'] == '02.10.2026' and by['CPTW-APRS-616']['valid_till'] == '03.10.2026',
        'text dates as written, valid_till returned')
H.check(by['CPTW-APRS-276']['date'] == '24.10.2026', 'text issue kept when only "valid till" was swapped')
H.check(by['CPTW-APRS-299']['date'] == '06.10.2026' and by['CPTW-APRS-299']['valid_till'] == '12.10.2026',
        'both swapped: 06.10 .. 12.10')
H.check(by['CPTW-APRS-620']['date'] == '04.10.2026', 'an October permit stored as April comes back to October')
H.check(by['CPTW-APRS-630']['date'] == '20.10.2026' and by['CPTW-APRS-630']['valid_till'] == '',
        'datetime with day > 12 kept; no valid till -> ""')
H.check(by['CPTW-APRS-616']['area'] == 'RMU-06', 'area column returned')
H.check([u['no'] for u in unclear] == ['CPTW-APRS-380'] and unclear[0]['date_unclear'],
        'the unswappable 380 is reported unclear: {}'.format([u['no'] for u in unclear]))
H.check('CPTW-APRS-380' not in nos, 'and left out of the month')

print('\n=== April ===')
apr = [r['no'] for r in T.load_cptw_register(path, 2026, 4)]
H.check(apr == ['CPTW-APRS-312'], '312 is an April permit (10.04): {}'.format(apr))
H.check(T.load_cptw_register(path, 2026, 4)[0]['date'] == '10.04.2026', 'dated 10.04.2026')

print('\n=== issued_to, case-insensitive ===')
sg = [r['no'] for r in T.load_cptw_register(path, 2026, 10, issued_to='Sungrow')]
H.check('CPTW-APRS-617' not in sg and 'CPTW-APRS-504' in sg and 'CPTW-APRS-276' in sg,
        'issued_to "Sungrow" matches SUNGROW / sungrow / Sungrow Power, not HEEC')
u2 = []
T.load_cptw_register(path, 2026, 10, issued_to='HEEC', unclear_out=u2)
H.check(u2 == [], 'unclear rows follow the issued_to filter too')

print('\n=== resolve_permit_dates rules ===')
p = lambda v: v.date() if isinstance(v, dt.datetime) else (dt.datetime.strptime(v, '%d/%m/%Y').date() if v else None)
H.check(T.resolve_permit_dates(D(2026, 3, 5), '20/3/2026', p) == (dt.date(2026, 3, 5), dt.date(2026, 3, 20), False),
        'a consistent pair is never swapped')
H.check(T.resolve_permit_dates(D(2026, 3, 5), '20/9/2026', p)[:2] == (dt.date(2026, 3, 5), dt.date(2026, 9, 20)),
        'a long permit as written, when no swap makes it short')
H.check(T.load_cptw_register(os.path.join(H.WORK, 'missing.xlsx'), 2026, 10) == [],
        'a missing workbook gives no permits, no error')

H.finish()
