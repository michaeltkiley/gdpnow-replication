"""Stage 06: verify a nowcast run against the published GDPNow figures (and, for L1, the workbook's cells).

Reports OK / MISMATCH per value and writes data/<vintage>_verify_<run_id>.csv.
Usage: python scripts/06_verify.py --run L1_20261003
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl.utils import column_index_from_string as ci

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import store
from gdpnow.config import DATA, load_toml

# Published GDPNow nowcast for 2026:Q3, Oct 1, 2026 (workbook TrackingHistory / ContribHistory, full precision;
# rounded values on RealGDPTrackingSlides.pdf p.4).
PUBLISHED_DATE = '2026-10-01'
PUBLISHED_GDP = 3.677319405706525
PUBLISHED_GROWTH = {
    'CTG': 2.976039218610005, 'CS': 3.4106699052166567, 'FNE': 18.49802092606929, 'FNP': 6.721885264362282,
    'FNS': 6.636770473952258, 'FR': -1.1695403582793196, 'GF': 5.494064966467027, 'GS': 1.0741530073677508,
    'XM': -11.614261706775375, 'XS': 4.832122137236539, 'MM': 16.474022928903096, 'MS': 1.4519803464114744,
}
PUBLISHED_CONTRIB = {
    'CTG': 0.6284257233236813, 'CS': 1.5859191288468768, 'FNE': 1.0378131580282546, 'FNP': 0.3823825376865659,
    'FNS': 0.18921686390129455, 'FR': -0.04324888595539576, 'GF': 0.3297656114583558, 'GS': 0.12203516011165605,
    'XM': -0.9402449589753905, 'XS': 0.1939777338784257, 'MM': -1.805846653442799, 'MS': -0.04257157523700933,
    'V': 2.0396955620818993,
}
PUBLISHED_AGG = {
    'GDP': PUBLISHED_GDP, 'PCE': 3.2749355187238427, 'final_sales': 1.6208886369057485,
    'final_sales_domestic': 4.116400391496766, 'private_domestic_final_purchases': 4.424056756738781,
    'cipi_level': 53.808069704056635, 'cipi_change': 117.24606970405664,
}

# Tolerances. L1 is a code test: exact up to a documented workbook-internal residual (FederalGovt sheet
# 5.494062476 vs TrackingHistory 5.494064966, worth 1.5e-7 pp of GDP). L2/L3 pass/fail is the headline only.
TOL = {'L1': {'headline': 1e-6, 'component': 1e-5, 'cell': 1e-8}, 'L2': {'headline': 0.1}, 'L3': {'headline': 0.1}}

# L1 workbook-cell checks: (block, key, sheet, row locator, column). Row locator: ('lhs', ticker) = subcomponent
# row; ('ind', lhs, name) = indicator row; ('row', n) = fixed row.
SHEET = {'FNEZ': 'Equipment', 'FNPZ': 'IntellPropProd', 'FNSZ': 'NonresStructures', 'FRZ': 'Residential',
         'GFZ': 'FederalGovt', 'GSZ': 'StateLocal'}
CONS_ROWS = {'core_retail': 8, 'new_mv': 15, 'used_mv': 29, 'gasoline': 31, 'food_services': 41,
             'electricity_gas': 48, 'travel_out': 52, 'travel_in': 58, 'other_services': 67}
# Known, explained differences between our code and the workbook's cached cells (see DESIGN.md §8).
KNOWN_CELL_EXCEPTIONS = {
    ('CONS', 'q:travel_out'): 'workbook state cells Consumption!FV64:FV65 blank -> sheet applies travel revisions the program does not',
    ('CONS', 'q:travel_in'): 'same as travel_out',
    ('CONS', 'services'): 'consequence of travel rows; published services value is matched exactly',
}


def check(rows, kind, name, ours, target, tol):
    diff = ours - target
    status = 'OK' if abs(diff) <= tol else 'MISMATCH'
    rows.append(dict(kind=kind, name=name, ours=ours, target=target, diff=diff, tol=tol, status=status, note=''))


def cell_checks(con, vintage, inter, rows, tol):
    cells = store.query(con, 'SELECT sheet, row, col, num, text FROM wb_cells WHERE vintage = ?', (vintage,))
    by = {(r.sheet, r.row, r.col): (r.num, r.text) for r in cells.itertuples()}
    text = lambda s, r, c: by.get((s, r, c), (None, None))[1]
    num = lambda s, r, col: by.get((s, r, ci(col)), (None, None))[0]

    def find(sheet, c_text, e_text):
        for (s, r, c), (_, t) in by.items():
            if s == sheet and c == 3 and t == c_text and text(sheet, r, 5) == e_text:
                return r
        return None

    bridges = load_toml('bridges.toml')['components']
    for cid, sheet in SHEET.items():
        for sub in bridges[cid]['subcomponents']:
            r = find(sheet, sub['lhs'], 'Constant')
            checks = [(f'sub:{sub["lhs"]}', 'FV'), (f'share:{sub["lhs"]}', 'GC')]
            for key, col in checks:
                ours = inter.get((cid, key))
                target = num(sheet, r, col)
                if ours is not None and target is not None:
                    check(rows, 'cell', f'{sheet}!{col}{r} {key}', ours, target, tol)
            for ind in sub.get('indicators', []):
                ri = find(sheet, sub['lhs'], ind)
                target = num(sheet, ri, 'GM') if num(sheet, ri, 'GM') is not None else num(sheet, ri, 'FY')
                if target is not None:
                    check(rows, 'cell', f'{sheet}!r{ri} ind:{ind}', inter[(cid, f'ind:{ind}')], target, tol)
        check(rows, 'cell', f'{sheet}!FV6 total', inter[(cid, 'total')], num(sheet, 6, 'FV'), tol)
    for k, r in CONS_ROWS.items():
        check(rows, 'cell', f'Consumption!FX{r} q:{k}', inter[('CONS', f'q:{k}')], num('Consumption', r, 'FX'), tol)
    check(rows, 'cell', 'Consumption!FV6 goods', inter[('CONS', 'goods')], num('Consumption', 6, 'FV'), tol)
    check(rows, 'cell', 'Consumption!FV39 services', inter[('CONS', 'services')], num('Consumption', 39, 'FV'), tol)
    for kind, sheet in (('goods', 'ExportsImportsGoods'), ('services', 'ExportsImportsServices')):
        b = f'TRADE_{kind}'
        check(rows, 'cell', f'{sheet}!FX29 monthly exports', inter[(b, 'monthly_exports')], num(sheet, 29, 'FX'), tol)
        check(rows, 'cell', f'{sheet}!FX30 monthly imports', inter[(b, 'monthly_imports')], num(sheet, 30, 'FX'), tol)
        check(rows, 'cell', f'{sheet}!FV6 exports', inter[(b, 'exports')], num(sheet, 6, 'FV'), tol)
        check(rows, 'cell', f'{sheet}!FV7 imports', inter[(b, 'imports')], num(sheet, 7, 'FV'), tol)
    check(rows, 'cell', 'Inventories!FV9 monthly-model CIPI', inter[('INV', 'cipi_monthly_model')],
          num('Inventories', 9, 'FV'), tol)
    for r in rows:
        if r['kind'] == 'cell':
            key = next((k for k in KNOWN_CELL_EXCEPTIONS if r['name'].endswith(k[1])), None)
            if key and r['status'] == 'MISMATCH':
                r['status'], r['note'] = 'KNOWN', KNOWN_CELL_EXCEPTIONS[key]


def provenance_checks(con, run_id, level, rows):
    prov = store.query(con, 'SELECT field, registry_id, source FROM provenance WHERE run_id = ?', (run_id,))
    reg = pd.read_csv(Path(__file__).resolve().parents[1] / 'registry' / 'parameters.csv')
    group = dict(zip(reg.id, reg.group))
    for r in prov.itertuples():
        g = group.get(r.registry_id, '?')
        from_wb = 'workbook' in r.source
        if level == 'L1':
            ok, note = True, 'L1 test fixture: workbook values expected'
        elif level == 'L2':
            ok, note = not (from_wb and g.startswith('1')), 'group 1 must be estimated by our code'
        else:
            ok, note = not from_wb, 'nothing may come from the workbook'
        rows.append(dict(kind='provenance', name=f'{r.registry_id} ({r.field})', ours=np.nan, target=np.nan,
                         diff=np.nan, tol=np.nan, status='OK' if ok else 'MISMATCH', note=f'{r.source}; {note}'))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--run', required=True)
    a = ap.parse_args()
    con = store.connect()
    run = store.query(con, 'SELECT * FROM runs WHERE run_id = ?', (a.run,)).iloc[0]
    level, vintage = run.level, run.vintage
    comps = store.query(con, 'SELECT * FROM nowcast_components WHERE run_id = ?', (a.run,)).set_index('id')
    agg = dict(store.query(con, 'SELECT key, value FROM nowcast_aggregates WHERE run_id = ?', (a.run,)).values)
    inter = {(r.block, r.key): r.value for r in
             store.query(con, 'SELECT block, key, value FROM nowcast_intermediates WHERE run_id = ?', (a.run,)).itertuples()}
    tol, rows = TOL[level], []

    check(rows, 'headline', 'GDP (% SAAR)', agg['GDP'], PUBLISHED_GDP, tol['headline'])
    ctol = tol.get('component', np.inf)
    for k, v in PUBLISHED_GROWTH.items():
        check(rows, 'component growth', f'{k} {comps.label[k]}', comps.growth_pct[k], v, ctol)
    for k, v in PUBLISHED_CONTRIB.items():
        check(rows, 'contribution', f'{k} {comps.label[k]}', comps.contribution[k], v, ctol)
    for k, v in PUBLISHED_AGG.items():
        if k != 'GDP':
            check(rows, 'aggregate', k, agg[k], v, ctol)
    if level == 'L1':
        cell_checks(con, vintage, inter, rows, tol['cell'])
    provenance_checks(con, a.run, level, rows)

    df = pd.DataFrame(rows)
    if level != 'L1':   # components are reported, not pass/fail, outside L1
        df.loc[df.kind.isin(['component growth', 'contribution', 'aggregate']), 'status'] = 'REPORT'
    out = DATA / f'{vintage}_verify_{a.run}.csv'
    df.to_csv(out, index=False)
    store.replace_rows(con, 'verification', df.assign(run_id=a.run), {'run_id': a.run})
    pd.set_option('display.width', 200)
    shown = df[df.kind != 'provenance']
    print(shown[['kind', 'name', 'ours', 'target', 'diff', 'status']].to_string(index=False, max_colwidth=48))
    print('\nsummary:', df.groupby(['kind', 'status']).size().to_dict())
    print('known exceptions:', *[f'  {n}: {t}' for n, t in df[df.status == 'KNOWN'][['name', 'note']].values], sep='\n')
    print(f'written {out}')
    hard = df[(df.status == 'MISMATCH')]
    sys.exit(1 if len(hard) else 0)


if __name__ == '__main__':
    main()
