"""Targets and the GDPNow archive.
 * BEA advance estimate of real GDP growth (SAAR %): first vintage in which a quarter appears
   (Philadelphia Fed RTDSM monthly vintages), cross-checked against Atlanta Fed's TrackRecord.
 * GDPNow headline nowcast archive (Atlanta Fed workbook) for like-for-like comparison.
Inputs: $MIDAS_DATA/rtdsm.xlsx, $MIDAS_DATA/gdpnow_workbook.xlsx (downloaded by run_all.sh)"""
import re, numpy as np, pandas as pd
from common import *

def advance_rtdsm():
    d = pd.read_excel(DATA / 'rtdsm.xlsx')
    d['q'] = pd.PeriodIndex([x.replace(':', '') for x in d['DATE']], freq='Q')
    d = d.set_index('q').drop(columns='DATE')
    rows = {}
    d = d.copy()                              # columns are in chronological vintage order
    for q in d.index[1:]:
        s = d.loc[q]
        first = s.first_valid_index()
        if first is None: continue
        prev = d.loc[q - 1, first]
        if np.isnan(prev): continue
        rows[q] = (100 * ((s[first] / prev) ** 4 - 1), first)
    return pd.DataFrame(rows, index=['adv_rtdsm', 'vintage']).T

def gdpnow_tables():
    x = pd.ExcelFile(DATA / 'gdpnow_workbook.xlsx')
    tr = x.parse('TrackRecord').iloc[:, :4]
    tr.columns = ['q', 'gdpnow_last', 'adv_atl', 'rel']
    tr = tr.dropna(subset=['q'])
    tr['q'] = pd.PeriodIndex(pd.to_datetime(tr['q']), freq='Q')
    arch = []
    for s in ['TrackingDeepArchives', 'TrackingArchives']:
        a = x.parse(s)
        a = a[['Forecast Date', 'Quarter being forecasted', 'GDP Nowcast']].copy()
        a.columns = ['date', 'q', 'gdpnow']
        arch.append(a)
    a = pd.concat(arch).dropna()
    a['date'] = pd.to_datetime(a['date'])
    a['q'] = pd.PeriodIndex(pd.to_datetime(a['q']), freq='Q')
    a['gdpnow'] = pd.to_numeric(a['gdpnow'], errors='coerce')
    a = a.dropna().drop_duplicates(['date', 'q'], keep='last').sort_values('date')
    return tr, a

if __name__ == '__main__':
    r = advance_rtdsm()
    tr, a = gdpnow_tables()
    m = r.join(tr.set_index('q')['adv_atl'], how='inner')
    m['diff'] = m['adv_rtdsm'] - m['adv_atl']
    print('overlap quarters', len(m), 'max |RTDSM-Atlanta| =', m['diff'].abs().max().round(3),
          'mean abs', m['diff'].abs().mean().round(3))
    print(m.sort_values('diff', key=abs).tail(5))
    T = r[['adv_rtdsm']].rename(columns={'adv_rtdsm': 'adv'})
    A = tr.drop_duplicates('q').set_index('q')['adv_atl'].astype(float)
    T = T.reindex(T.index.union(A.index)).sort_index()
    T.loc[A.index, 'adv'] = A                                           # Atlanta's BEA numbers where available
    T['src'] = np.where(T.index.isin(tr['q']), 'BEA advance (Atlanta Fed file)', 'RTDSM first release')
    T.index.name = 'q'
    T.to_csv(OUT / 'targets.csv'); a.to_csv(OUT / 'gdpnow_archive.csv', index=False)
    print(T.loc['2004Q4':'2005Q2'], T.tail(3), a.tail(3), sep='\n')
