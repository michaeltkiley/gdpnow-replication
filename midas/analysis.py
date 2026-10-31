"""Evaluate forecasts.csv: RMSFE by horizon/sample, DM tests, GDPNow comparison + combination, figures."""
import numpy as np, pandas as pd, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy import stats
import os
from common import *
SPEC = os.environ.get('MIDAS_SPEC', 'nominal')

df = pd.read_csv(OUT / f'forecasts_{SPEC}.csv', parse_dates=['origin'])
df['qper'] = pd.PeriodIndex(df['qp'], freq='Q')
live = df[df['actual'].isna()].copy()
df = df[df['actual'].notna()].copy()
df['comb_gn'] = 0.5 * df['full'] + 0.5 * df['gdpnow']
MODELS = {'ar2': 'AR(2)', 'trio': 'MIDAS, 3 vars (Chan set)', 'full': 'MIDAS, 15 vars', 'gdpnow': 'GDPNow (published)',
          'comb_gn': '50/50 MIDAS-15 + GDPNow'}
P = lambda s: pd.Period(s, 'Q')
SAMPLES = {
    'pre-COVID 2005Q1-2019Q4': (P('2005Q1'), P('2019Q4'), None),
    'post-COVID 2022Q1-2026Q3': (P('2022Q1'), P('2026Q3'), None),
    'GDPNow overlap 2011Q3-2019Q4 + 2022Q1-2026Q3': None,
    'COVID 2020Q1-2021Q4': (P('2020Q1'), P('2021Q4'), None),
}

def pick(name):
    if name.startswith('GDPNow overlap'):
        m = (((df.qper >= P('2011Q3')) & (df.qper <= P('2019Q4'))) | ((df.qper >= P('2022Q1')) & (df.qper <= P('2026Q3'))))
        return df[m & df.gdpnow.notna()]
    a, b, _ = SAMPLES[name]
    return df[(df.qper >= a) & (df.qper <= b)]

def rmse(d, m): return float(np.sqrt(np.mean((d[m] - d['actual']) ** 2)))

def dm(d, m1, m2):
    """Diebold-Mariano (squared loss, 1-step; Harvey-Leybourne-Newbold small-sample t). >0 favours m1 (m1 has smaller loss)."""
    l = (d[m2] - d['actual']) ** 2 - (d[m1] - d['actual']) ** 2
    n = len(l); 
    if n < 8: return np.nan, np.nan
    t = l.mean() / (l.std(ddof=1) / np.sqrt(n)) * np.sqrt((n - 1) / n)
    return t, 2 * (1 - stats.t.cdf(abs(t), n - 1))

md = []
for sname in SAMPLES:
    d = pick(sname)
    nq = d['qp'].nunique()
    md.append(f'\n### {sname}  ({nq} quarters)\n')
    ms = [m for m in MODELS if (m not in ('gdpnow', 'comb_gn') or sname.startswith('GDPNow'))]
    md.append('| Forecast origin | ' + ' | '.join(MODELS[m] for m in ms) + ' |')
    md.append('|---|' + '---|' * len(ms))
    for h, lab in ((1, 'end of month 1'), (2, 'end of month 2'), (3, 'end of month 3'), (4, '15th of month after quarter')):
        dh = d[d.h == h]
        md.append(f'| {lab} | ' + ' | '.join(f'{rmse(dh, m):.2f}' for m in ms) + ' |')
    md.append('\n*RMSFE vs BEA advance estimate, pp annualised.*')
    # DM tests at each horizon
    md.append('\nMIDAS-15 vs AR(2) / vs 3-var / vs GDPNow, Diebold-Mariano p-values (two-sided; + = MIDAS-15 better):\n')
    for h in (1, 2, 3, 4):
        dh = d[d.h == h]
        r = []
        for other in ('ar2', 'trio') + (('gdpnow',) if sname.startswith('GDPNow') else ()):
            t, p = dm(dh, 'full', other)
            r.append(f'{MODELS[other]}: t={t:+.2f}, p={p:.2f}')
        md.append(f'- h={h}: ' + '; '.join(r))
open(OUT / f'tables_{SPEC}.md', 'w').write('\n'.join(md))
print('\n'.join(md))

# ------------------------------------------------------------- figures
plt.rcParams.update({'font.size': 9, 'axes.spines.top': False, 'axes.spines.right': False})
col = {'ar2': '#9aa0a6', 'trio': '#e8a33d', 'full': '#1a73e8', 'gdpnow': '#d93025', 'comb_gn': '#188038'}
fig, axs = plt.subplots(1, 2, figsize=(10, 3.6), sharey=False)
for ax, sname in zip(axs, ['pre-COVID 2005Q1-2019Q4', 'GDPNow overlap 2011Q3-2019Q4 + 2022Q1-2026Q3']):
    d = pick(sname); ms = ['ar2', 'trio', 'full'] + (['gdpnow', 'comb_gn'] if sname.startswith('GDPNow') else [])
    w = 0.8 / len(ms)
    for i, m in enumerate(ms):
        v = [rmse(d[d.h == h], m) for h in (1, 2, 3, 4)]
        ax.bar(np.arange(4) + i * w, v, w, color=col[m], label=MODELS[m])
    ax.set_xticks(np.arange(4) + 0.4 - w / 2); ax.set_xticklabels(['end M1', 'end M2', 'end M3', '15th after'])
    ax.set_title(sname.split(' (')[0].replace('GDPNow overlap', 'GDPNow-overlap sample'), fontsize=9); ax.set_ylabel('RMSFE (pp)')
    ax.set_ylim(0, 2.9); ax.legend(frameon=False, fontsize=7, ncol=2, loc='upper left')
fig.tight_layout(); fig.savefig(OUT / f'fig_rmsfe_{SPEC}.png', dpi=160); plt.close(fig)

d3 = df[df.h == 3].sort_values('qper')
fig, ax = plt.subplots(figsize=(10, 3.4))
x = [p.to_timestamp() for p in d3.qper]
ax.plot(x, d3.actual, color='k', lw=1.6, label='BEA advance estimate')
ax.plot(x, d3.full, color=col['full'], lw=1.2, label='MIDAS-15 (end of quarter)')
ax.plot(x, d3.gdpnow, color=col['gdpnow'], lw=1.2, label='GDPNow (end of quarter)')
ax.set_ylim(-8, 9); ax.axhline(0, color='#ccc', lw=.6); ax.legend(frameon=False, ncol=3); ax.set_ylabel('% SAAR')
ax.set_title('End-of-quarter nowcasts (COVID quarters off-scale)', fontsize=9)
fig.tight_layout(); fig.savefig(OUT / f'fig_series_{SPEC}.png', dpi=160); plt.close(fig)

# ------------------------------------------------------------- what the model uses: avg |contribution| by group
cc = [c for c in df.columns if c.startswith('c_')]
grp = {s: SERIES[s][3] for s in SERIES}; grp['gdp_lag'] = 'GDP lag'
G = {}
for c in cc:
    G.setdefault(grp[c[2:]], []).append(c)
dd = df[(df.qper <= P('2019Q4')) & (df.h == 3)]
imp = pd.Series({g: dd[cs].sum(axis=1).abs().mean() for g, cs in G.items()}).sort_values()
fig, ax = plt.subplots(figsize=(5.5, 3))
ax.barh(imp.index, imp.values, color=col['full']); ax.set_xlabel('mean |contribution to nowcast − mean| (pp), end-of-quarter, 2005-19')
fig.tight_layout(); fig.savefig(OUT / f'fig_groups_{SPEC}.png', dpi=160); plt.close(fig)
print('\nGroup importance (pp):\n', imp.round(3).to_string())
open(OUT / f'tables_{SPEC}.md', 'a').write('\n\n### Average absolute contribution by predictor group (end-of-quarter, 2005-19)\n\n' +
                                    '| group | pp |\n|---|---|\n' + '\n'.join(f'| {k} | {v:.2f} |' for k, v in imp.round(2).items()))

# ------------------------------------------------------------- live
if len(live):
    print('\nLIVE', live[['qp', 'h', 'origin', 'full', 'trio', 'ar2']].round(2).to_string())
    r = live.iloc[-1]
    gl = {g: sum(r[c] for c in cs) for g, cs in G.items()}
    print({k: round(v, 2) for k, v in gl.items()}, 'mean of training y', round(r['mean'], 2))
