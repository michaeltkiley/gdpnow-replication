"""Static data for docs/midas.html -> docs/data/midas_static.json (track record, accuracy tables, blends, predictors)."""
import json, numpy as np, pandas as pd
from common import *

SPEC = 'nominal_notrade_pc'
d = pd.read_csv(OUT / f'forecasts_{SPEC}.csv')
d['gdpnow'] = d['gdpnow'].where(d['gdpnow'].notna(), None)
done = d[d.actual.notna()].copy()
rm = lambda x, m: float(np.sqrt(np.mean((x[m] - x.actual) ** 2)))
SAMPLES = {
    'pre': ('2005Q1-2019Q4', lambda x: x[x.y <= 2019], False),
    'post': ('2022Q1-2026Q3', lambda x: x[x.y >= 2022], False),
    'gn': ('2011Q3-2019Q4 and 2022Q1-2026Q3', lambda x: x[x.gdpnow.notna() & ((x.y >= 2022) | (x.y <= 2019))], True),
    'covid': ('2020Q1-2021Q4', lambda x: x[(x.y == 2020) | (x.y == 2021)], False),
}
stats = {}
for k, (lab, f, gn) in SAMPLES.items():
    x = f(done)
    models = ['full', 'trio', 'ar2'] + (['gdpnow'] if gn else [])
    stats[k] = {'label': lab, 'quarters': int(x.qp.nunique()),
                'rmsfe': {m: [round(rm(x[x.h == h], m), 3) for h in (1, 2, 3, 4)] for m in models}}
# blend with GDPNow (in-sample best weight), 2011Q3-2019Q4 and 2022-26
bl = {}
for nm, x in (('2011Q3-2019Q4', done[done.gdpnow.notna() & (done.y <= 2019)]), ('2022Q1-2026Q3', done[done.gdpnow.notna() & (done.y >= 2022)])):
    rows = []
    for h in (1, 2, 3, 4):
        z = x[x.h == h]; ws = np.linspace(0, 1, 21)
        e = [np.sqrt(np.mean(((1 - w) * z.gdpnow + w * z.full - z.actual) ** 2)) for w in ws]
        i = int(np.argmin(e))
        rows.append({'gdpnow': round(rm(z, 'gdpnow'), 3), 'midas': round(rm(z, 'full'), 3), 'w': round(float(ws[i]), 2), 'blend': round(float(e[i]), 3),
                     'corr': round(float(np.corrcoef(z.gdpnow - z.actual, z.full - z.actual)[0, 1]), 2)})
    bl[nm] = rows
# bias
bias = {k: {m: round(float((SAMPLES[k][1](done)[lambda x: x.h == 3][m] - SAMPLES[k][1](done)[lambda x: x.h == 3].actual).mean()), 2) for m in ('full', 'ar2')} for k in ('pre', 'post')}
# specification path (end of month 3 RMSFE, 2005-19 / 2022-26)
path = []
for s, lab in (('nominal', 'All 15 indicators, GDP growth'), ('nominal_notrade', 'Drop trade balance'), ('nominal_notrade_pc', 'Per-capita target (final)')):
    z = pd.read_csv(OUT / f'forecasts_{s}.csv'); z = z[z.actual.notna() & (z.h == 3)]
    path.append({'label': lab, 'pre': round(rm(z[z.y <= 2019], 'full'), 2), 'post': round(rm(z[z.y >= 2022], 'full'), 2),
                 'ar_pre': round(rm(z[z.y <= 2019], 'ar2'), 2), 'ar_post': round(rm(z[z.y >= 2022], 'ar2'), 2)})
# predictor groups
grp = {s: SERIES[s][3] for s in SERIES}
cc = [c for c in d.columns if c.startswith('c_')]
dd = done[(done.y <= 2019) & (done.h == 3)]
G = {}
for c in cc:
    G.setdefault('GDP lag' if c == 'c_gdp_lag' else grp[c[2:]], []).append(c)
imp = {g: round(float(dd[cs].sum(axis=1).abs().mean()), 3) for g, cs in G.items()}
meta = [{'id': s, 'label': v[2], 'group': v[3], 'freq': {'M': 'monthly', 'W': 'weekly', 'D': 'daily'}[v[0]],
         'transform': {'dlog': '% change', 'diff': 'change', 'diffk': 'change ($bn)', 'lvl': 'level'}[v[1]],
         'trio': s in TRIO, 'used': s != 'BOPGSTB'} for s, v in SERIES.items()]
track = [{k: (None if (isinstance(v, float) and np.isnan(v)) else (round(v, 3) if isinstance(v, float) else v))
          for k, v in r.items() if k in ('qp', 'h', 'origin', 'actual', 'full', 'trio', 'ar2', 'gdpnow')} for r in d.to_dict('records')]
out = {'spec': SPEC, 'stats': stats, 'blend': bl, 'bias': bias, 'path': path, 'importance': imp, 'predictors': meta, 'track': track}
(HERE.parent / 'docs' / 'data' / 'midas_static.json').write_text(json.dumps(out, separators=(',', ':')))
print({k: (stats[k]['rmsfe']) for k in ('pre', 'post')}, bias, path, imp, sep='\n')
