"""The run's calendar date. FRED/ALFRED real-time dates are US Central dates and a real-time date in the future is rejected (HTTP 400),
so the as-of date is the Central date, not the runner's UTC date: a scheduled run delayed past 19:00 Central (00:00 UTC) would otherwise
ask for tomorrow."""
import datetime as dt
from zoneinfo import ZoneInfo


def today():
    return dt.datetime.now(ZoneInfo('America/Chicago')).date()
