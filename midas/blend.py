"""Blend of published GDPNow with the average of the two MIDAS models (MIDAS-14 and MIDAS-5).

    blend = (1 - w) * GDPNow + w * (MIDAS-14 + MIDAS-5) / 2          w in [0, 1], one weight for every stage, no intercept

w minimises squared error against the BEA advance estimate over every scored forecast date (end of months 1-3 and the
15th after) of 2011Q3-2026Q3 without the pandemic quarters 2020-21 (GDPNow's published history starts in 2011Q3).
A weight per stage, or a schedule across stages, was tried first (shrunk toward a schedule with GDPNow's weight rising
toward the release): the data did not support any stage pattern, and the flat weight scored best out of sample.
Out-of-sample = refit w leaving one whole quarter (all four of its stages) out.  Writes docs/data/blend.json."""
import json
import numpy as np, pandas as pd
from common import *

SPEC = 'nominal_notrade_pc'
STAGES = ('End of month 1', 'End of month 2', 'End of month 3', '15th of month after')
rms = lambda e: float(np.sqrt(np.mean(np.square(e))))


def sample():
    d = pd.read_csv(OUT / f'forecasts_{SPEC}.csv')
    d = d[d.actual.notna() & d.gdpnow.notna() & ~d.y.isin([2020, 2021])].copy()
    d['m'] = (d.full + d.five) / 2
    d['u'], d['v'] = d.actual - d.gdpnow, d.m - d.gdpnow          # error of GDPNow; MIDAS-average minus GDPNow
    return d


def weight(d):
    return float(np.clip((d.u * d.v).sum() / (d.v ** 2).sum(), 0, 1))


def build():
    d = sample()
    w = weight(d)
    loo = {q: weight(d[d.qp != q]) for q in d.qp.unique()}
    d['w_loo'] = d.qp.map(loo)
    d['e_blend_loo'] = d.u - d.w_loo * d.v
    d['e_blend_in'] = d.u - w * d.v
    stages = []
    for h in (1, 2, 3, 4):
        z = d[d.h == h]
        stages.append({'stage': STAGES[h - 1], 'quarters': int(len(z)),
                       'gdpnow': round(rms(z.u), 3), 'blend_oos': round(rms(z.e_blend_loo), 3), 'blend_in': round(rms(z.e_blend_in), 3),
                       'midas_avg': round(rms(z.actual - z.m), 3), 'midas14': round(rms(z.actual - z.full), 3), 'midas5': round(rms(z.actual - z.five), 3)})
    tot = lambda k: round(float(np.sqrt(np.mean([s[k] ** 2 for s in stages]))), 3)
    # sub-samples, out of sample, all four stages pooled
    sub = {}
    for nm, z in (('2011Q3-2019Q4', d[d.y <= 2019]), ('2022Q1-2026Q3', d[d.y >= 2022])):
        sub[nm] = {'quarters': int(z.qp.nunique()), 'gdpnow': round(rms(z.u), 3), 'blend_oos': round(rms(z.e_blend_loo), 3)}
    out = {'weight_midas': round(w, 3), 'weight_gdpnow': round(1 - w, 3), 'sample': '2011Q3-2026Q3 without 2020-21',
           'quarters': int(d.qp.nunique()), 'stages': stages,
           'total': {'gdpnow': tot('gdpnow'), 'blend_oos': tot('blend_oos'), 'blend_in': tot('blend_in'), 'midas_avg': tot('midas_avg')},
           'subsamples': sub, 'weight_range_loo': [round(min(loo.values()), 3), round(max(loo.values()), 3)]}
    return out


if __name__ == '__main__':
    out = build()
    (HERE.parent / 'docs' / 'data' / 'blend.json').write_text(json.dumps(out, separators=(',', ':')))
    print(json.dumps(out, indent=1))
