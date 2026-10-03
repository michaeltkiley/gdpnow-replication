"""Paths and specification constants shared by all pipeline stages."""
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
RAW = DATA / 'raw'
DB_PATH = DATA / 'gdpnow.duckdb'
CONFIG = ROOT / 'config'

WORKBOOK_URL = ('https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/'
                'research-and-data/data/gdpnow/GDPTrackingModelDataAndForecasts.xlsx')
WORKBOOK_NAME = 'GDPTrackingModelDataAndForecasts.xlsx'


def load_toml(name):
    with open(CONFIG / name, 'rb') as f:
        return tomllib.load(f)


def load_env():
    """Read KEY=VALUE lines from .env into os.environ (no override of existing values)."""
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())
