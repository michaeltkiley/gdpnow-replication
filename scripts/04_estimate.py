"""Stage 04: estimate model parameters from data (L2: the workbook's data; L3: public data, later).

Writes estimates to DuckDB (est_params, est_series, est_provenance) and a diagnostics CSV comparing each
estimate with the workbook's, plus a stage-by-stage attribution of the headline difference from L1.

Usage: python scripts/04_estimate.py --level L2 [--vintage YYYYMMDD] [--force]
"""
import argparse
import dataclasses
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import estimate, inputs, nowcast, params, store
from gdpnow.config import DATA


def run_l3(a, con, vintage, wb):
    """L3: estimate everything from the public bundle (stage 02); the workbook is used only for diagnostics."""
    import pickle
    stem = a.asof.replace("-", "") + a.tag
    run_id = f'L3_{stem}'
    path = DATA / f'{stem}_public_inputs.pkl'
    d = pickle.load(open(path, 'rb'))
    inp, panel, act, qprices = d['bundle']
    last = pd.Timestamp(d['last_price_month'])
    est, diag = estimate.estimate_all(inp, panel, act, qprices, last,
                                      inp.monthly_prices['MGDPN_USECONsplicefr'].dropna().index.max())
    tag = f'estimated:{run_id}'
    fields = ['factor', 'faar', 'bridge', 'bvar', 'prices_T1', 'blend', 'monthly_prices', 'cons_growth',
              'util_travel', 'farm_other', 'cipi_paths', 'iva_paths', 'inv_deflators']
    prov = {f: f'public:{a.asof}' for f in inputs.FIELD_REGISTRY}
    prov.update({f: tag for f in fields})
    est = dataclasses.replace(est, provenance=prov)
    params.save(con, run_id, est, fields)
    rows = []
    wbf = wb.factor
    j = pd.concat([diag['factor'].factor, wbf], axis=1, keys=['ours', 'wb']).dropna()
    rows.append(('factor', 'corr', 'full sample', 1.0, j.ours.corr(j.wb)))
    rows.append(('factor', 'Sep value', str(diag['factor'].last_data.date()), wbf[diag['factor'].last_data],
                 diag['factor'].factor[diag['factor'].last_data]))
    for lhs, c in est.bridge.items():
        rows += [('bridge', lhs, k, wb.bridge.get(lhs, {}).get(k), v) for k, v in c.items()]
    rows += [('blend', k, 'monthly', wb.blend[k][0], v[0]) for k, v in est.blend.items()]
    rows += [('bvar', k, '', wb.bvar.get(k), v) for k, v in est.bvar.items()]
    for k, v in diag.get('blend_sample', {}).items():
        rows.append(('blend_sample', k, v.get('start'), None, v.get('n')))
    pd.DataFrame(rows, columns=['block', 'item', 'term', 'workbook', 'ours']).to_csv(
        DATA / f'{stem}_diagnostics_{run_id}.csv', index=False)
    est_pkl = DATA / f'{stem}_estimated_{run_id}.pkl'
    pickle.dump({'est': est, 'diag_blend_hist': diag.get('inventory_model_history')}, open(est_pkl, 'wb'))
    print(f'estimates stored for {run_id}')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--level', required=True, choices=['L2', 'L3'])
    ap.add_argument('--asof', help='L3 only: as-of date of the public data bundle')
    ap.add_argument('--vintage')
    ap.add_argument('--last-price-month', default='2026-08', help='last actual month of monthly prices')
    ap.add_argument('--mgdp-last-month', default='2026-07', help='last actual month of monthly nominal GDP')
    ap.add_argument('--tag', default='', help='suffix for run id and files (release-effect runs)')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    con = store.connect()
    vintage = a.vintage or store.latest_vintage(con)
    run_id = f'{a.level}_{vintage}'
    if store.table_exists(con, 'est_provenance') and not a.force:
        if con.execute('SELECT count(*) FROM est_provenance WHERE run_id = ?', [run_id]).fetchone()[0]:
            print(f'estimates for {run_id} exist; use --force to re-estimate')
            return

    wb = inputs.from_workbook(con, vintage)
    if a.level == 'L3':
        run_l3(a, con, vintage, wb)
        return
    # L2 data: the workbook's own series (monthly panel with transformation codes; ISM levels for the factor).
    tick = store.query(con, "SELECT ticker FROM wb_series_meta WHERE vintage = ? AND sheet = 'TransformedMonthlySeries' "
                            "AND tcode IS NOT NULL", (vintage,)).ticker
    ism = store.series_frame(con, vintage, 'InventoryRaw')[['NAPMC_USECON', 'NAPMII_USECON']]
    panel = estimate.factor_panel(wb.growth, list(tick), ism).loc['1967-02-28':]
    act = store.series_frame(con, vintage, 'QtrlyActDLog').rename(columns=lambda c: c.replace('_USNAqtr', ''))
    prices = store.series_frame(con, vintage, 'QtrlyPriceForecasts').loc[:wb.T]

    # Last actual month of the monthly price panel and of monthly nominal GDP on the vintage date
    # (all CPI/PPI/import-export/PCE prices through Aug 2026 on Oct 1; monthly GDP through Jul).
    last_price = pd.Timestamp(a.last_price_month) + pd.offsets.MonthEnd(0)
    mgdp_last = pd.Timestamp(a.mgdp_last_month) + pd.offsets.MonthEnd(0)
    est, diag = estimate.estimate_all(wb, panel, act, prices, last_price, mgdp_last)
    tag = f'estimated:{run_id}'
    fields = ['factor', 'faar', 'bridge', 'bvar', 'prices_T1', 'blend', 'monthly_prices', 'cons_growth',
              'util_travel', 'farm_other', 'cipi_paths', 'iva_paths', 'inv_deflators']
    prov = dict(wb.provenance)
    prov.update({f: tag for f in fields})
    prov['cons_growth'] = f'{tag} (model-derived columns); workbook:{vintage} (data columns)'
    est = dataclasses.replace(est, provenance=prov)
    params.save(con, run_id, est, fields)

    # Diagnostics: our estimates next to the workbook's (benchmark only).
    rows = []
    for t, (q, r) in diag['faar_lags'].items():
        w = wb.faar[t]
        rows.append(('faar_lags', t, '', f"{max([int(k[2:]) for k in w if k.startswith('ar')] + [0])},"
                     f"{max([int(k[1:]) for k in w if k.startswith('f')] + [-1])}", f'{q},{r}'))
    for lhs, c in est.bridge.items():
        rows += [('bridge', lhs, k, wb.bridge[lhs].get(k), v) for k, v in c.items()]
    rows += [('blend', k, 'monthly', wb.blend[k][0], v[0]) for k, v in est.blend.items()]
    rows += [('bvar', k, '', wb.bvar.get(k), v) for k, v in est.bvar.items()]
    j = pd.concat([diag['factor'].factor, wb.factor], axis=1, keys=['ours', 'wb']).dropna()
    rows.append(('factor', 'corr', 'full sample', 1.0, j.ours.corr(j.wb)))
    rows.append(('factor', 'Sep value', str(diag['factor'].last_data.date()), wb.factor[diag['factor'].last_data],
                 diag['factor'].factor[diag['factor'].last_data]))
    diag_df = pd.DataFrame(rows, columns=['block', 'item', 'term', 'workbook', 'ours'])
    diag_df.to_csv(DATA / f'{vintage}_diagnostics_{run_id}.csv', index=False)

    run = lambda i: nowcast.run(i)[1]['GDP']
    att = estimate.attribution(wb, est, [
        ('dynamic factor', ['factor']), ('FA-AR equations', ['faar']), ('bridge equations', ['bridge']),
        ('quarterly BVARs', ['bvar', 'prices_T1']), ('monthly price BVAR', ['monthly_prices', 'cons_growth']),
        ('travel/utility regressions', ['util_travel']), ('inventory IVA model', ['iva_paths']),
        ('inventory CIPI block BVARs', ['cipi_paths']), ('inventory deflators', ['inv_deflators']),
        ('farm/other inventory AR', ['farm_other']), ('blend weights', ['blend'])], run)
    att.to_csv(DATA / f'{vintage}_attribution_{run_id}.csv', index=False)
    store.replace_rows(con, 'attribution', att.assign(run_id=run_id), {'run_id': run_id})
    print(att.round(4).to_string(index=False))
    print(f'estimates stored for {run_id}')


if __name__ == '__main__':
    main()
