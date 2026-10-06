"""Daily probe: did any raw public input change since the last build? Replays every request logged by the last
production build (table `fetch_log`, written by scripts/02_build_public.py) and compares digests of the responses,
without running the build. The FRED/ALFRED date, API keys and other volatile echoes are normalised out
(public_data.tokenise / VOLATILE), so an unchanged input gives an identical digest on any day."""
from concurrent.futures import ThreadPoolExecutor

from . import public_data as P
from . import store

WORKERS = 3          # FRED allows 120 requests a minute and BEA 100; the build itself stays well below both


def run(con, asof, workers=WORKERS):
    """Returns {'base': as-of of the log replayed, 'n': requests, 'changed': [urls]}, or None if there is no log."""
    if not store.table_exists(con, 'fetch_log'):
        return None
    base = con.execute('SELECT max(as_of) FROM fetch_log WHERE as_of <= ?', [str(asof)]).fetchone()[0]
    if base is None:
        return None
    rows = con.execute('SELECT key, kind, url, body, digest FROM fetch_log WHERE as_of = ?', [base]).fetchall()

    def one(r):
        key, kind, url, body, digest = r
        try:
            new = P.replay(kind, P.detokenise(url, asof), P.detokenise(body, asof))
        except Exception as e:                       # a failed request is a failed probe, never "no change"
            raise RuntimeError(f'probe request failed ({kind} {url[:120]}): {e}') from e
        return (url, new != digest)

    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(one, rows))
    return {'base': str(base), 'n': len(rows), 'changed': [u for u, c in res if c]}
