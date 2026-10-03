"""DuckDB storage: one file holding workbook vintages, public-data pulls, run outputs and provenance."""
import duckdb
import pandas as pd

from .config import DB_PATH, DATA


def connect():
    DATA.mkdir(exist_ok=True)
    return duckdb.connect(str(DB_PATH))


def replace_rows(con, table, df, where):
    """Delete rows matching `where` (dict of column -> value), then append df. Creates table if absent."""
    con.register('_df', df)
    con.execute(f'CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM _df LIMIT 0')
    cond = ' AND '.join(f'{k} = ?' for k in where)
    con.execute(f'DELETE FROM {table} WHERE {cond}', list(where.values()))
    con.execute(f'INSERT INTO {table} SELECT * FROM _df')
    con.unregister('_df')


def table_exists(con, table):
    return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
                       [table]).fetchone()[0] > 0


def query(con, sql, params=()):
    return con.execute(sql, list(params)).fetchdf()


def latest_vintage(con):
    return con.execute('SELECT max(vintage) FROM wb_vintage').fetchone()[0]


def series_frame(con, vintage, sheet, freq='M'):
    """Wide DataFrame (date index x ticker columns) of one workbook sheet's time series."""
    df = query(con, 'SELECT ticker, date, value FROM wb_series WHERE vintage = ? AND sheet = ?',
               (vintage, sheet))
    wide = df.pivot(index='date', columns='ticker', values='value')
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()
