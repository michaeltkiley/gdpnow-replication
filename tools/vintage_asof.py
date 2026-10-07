"""Read the vintage archive: a series as the archive knew it at the end of a run date.
Usage: python tools/vintage_asof.py --dir vintage --asof 2026-10-07 --source fred --series UMCSENT [--tail 12]"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import vintage

ap = argparse.ArgumentParser()
ap.add_argument('--dir', default='vintage')
ap.add_argument('--asof', default=None, help='run date YYYY-MM-DD (default: latest)')
ap.add_argument('--source', required=True)
ap.add_argument('--series', required=True)
ap.add_argument('--tail', type=int, default=12)
a = ap.parse_args()
s = vintage.state(a.dir, a.asof, a.source, a.series).sort_values('date')
print(s.tail(a.tail).to_string(index=False) if len(s) else 'no such series in the archive')
