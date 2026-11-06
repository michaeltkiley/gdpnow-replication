"""Named conditions that decide whether the model reads an input at all.

Some inputs are read only in part of the release cycle: the Advance Economic Indicators table and the end-use trade detail behind
the gold and capital-goods BVARs exist in the model only while the advance trade report is newer than the full release. A series
that is absent on a day its gate is closed is the model working as designed, not a loss. Every read that depends on a calendar or
data condition goes through `gate()`, so the condition is recorded with the run and the freshness check (gdpnow/freshness.py)
compares a day only with days that had the same gates open.

Rule for new code: an input read inside an `if` on the calendar or on data availability needs a gate, or the first day the
condition flips will be held by the freshness check.
"""
import pandas as pd

from . import store

TABLE = 'input_gates'


def gate(con, asof, name, is_open, detail=''):
    """Record whether the gate `name` is open on `asof` and return it as a bool (use it as the condition of the read)."""
    df = pd.DataFrame({'as_of': [str(asof)], 'name': [name], 'is_open': [bool(is_open)], 'detail': [detail]})
    store.replace_rows(con, TABLE, df, {'as_of': str(asof), 'name': name})
    return bool(is_open)


def regime(con, asof):
    """The day's gate states as one string, e.g. 'aei_window=closed' ('' when no gate was recorded)."""
    if not store.table_exists(con, TABLE):
        return ''
    rows = con.execute(f'SELECT name, is_open FROM {TABLE} WHERE as_of = ? ORDER BY name', [str(asof)]).fetchall()
    return ','.join(f'{n}={"open" if o else "closed"}' for n, o in rows)
