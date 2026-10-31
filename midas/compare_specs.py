"""RMSFE of the 15-variable MIDAS across specification variants (pre-COVID / post-COVID / GDPNow-overlap) vs AR(2) & GDPNow."""
import pandas as pd, numpy as np
r = lambda d, m: np.sqrt(np.mean((d[m] - d.actual) ** 2))
def load(s):
    d = pd.read_csv(f'results/forecasts_{s}.csv'); return d[d.actual.notna()]
rows = []
for s in ('nominal', 'real', 'nominal_w', 'real_w', 'nominal_notrade'):
    d = load(s)
    pre = d[d.y <= 2019]; post = d[d.y >= 2022]
    ov = d[d.gdpnow.notna() & ((d.y >= 2022) | (d.y <= 2019))]
    for nm, x in (('2005-19', pre), ('2022-26', post), ('GDPNow overlap', ov)):
        rows.append([s, nm] + [f'{r(x[x.h==h], "full"):.2f}' for h in (1, 2, 3, 4)] + [f'{r(x[x.h==h], "trio"):.2f}' for h in (3,)])
t = pd.DataFrame(rows, columns=['spec', 'sample', 'h1', 'h2', 'h3', 'h4', 'trio h3'])
print(t.to_string(index=False))
d = load('nominal')
for nm, x in (('2005-19', d[d.y <= 2019]), ('2022-26', d[d.y >= 2022]), ('GDPNow overlap', d[d.gdpnow.notna() & ((d.y >= 2022) | (d.y <= 2019))])):
    print(nm, 'AR2', [f'{r(x[x.h==h], "ar2"):.2f}' for h in (1,2,3,4)], 'GDPNow', [f'{r(x[x.h==h], "gdpnow"):.2f}' for h in (1,2,3,4)] if nm.startswith('GDP') else '')
