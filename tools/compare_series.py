"""Compare public-built indicator levels with the workbook's: growth correlation by year (diagnostic)."""
import sys, pickle, numpy as np, pandas as pd, duckdb
sys.path.insert(0, '.')
from gdpnow import store
con = duckdb.connect('data/gdpnow.duckdb', read_only=True)
inp = pickle.load(open('data/20261001_public_inputs.pkl', 'rb'))['bundle']
inp = inp[0] if isinstance(inp, tuple) else inp
pub = inp.levels
wb = store.series_frame(con, store.latest_vintage(con), 'MonthlyLevels')
for n in sys.argv[1:]:
    a, b = np.log(pub[n]).diff(), np.log(wb[n]).diff()
    j = pd.concat([a, b], axis=1, keys=['pub', 'wb']).dropna()
    j = j[j.index >= '2013-01-01']
    print(n, 'corr', round(j.pub.corr(j.wb), 3), 'n', len(j), ' level ratio last3:', (pub[n] / wb[n]).dropna().tail(3).round(3).tolist())
    print((j.groupby(j.index.year).apply(lambda d: d.pub.corr(d.wb))).round(2).to_dict())
