#!/bin/sh
# Rebuild everything from public sources (no API key). ~15 min, mostly the ALFRED downloads.
set -e
cd "$(dirname "$0")"
export MIDAS_DATA="${MIDAS_DATA:-$PWD/data}"; mkdir -p "$MIDAS_DATA" results
curl -sS -o "$MIDAS_DATA/rtdsm.xlsx" "https://www.philadelphiafed.org/-/media/frbp/assets/surveys-and-data/real-time-data/data-files/xlsx/routputmvqd.xlsx"
curl -sS -o "$MIDAS_DATA/gdpnow_workbook.xlsx" "https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/research-and-data/data/gdpnow/GDPTrackingModelDataAndForecasts.xlsx"
python3 fetch_vintages.py 32          # ALFRED vintages: 14 series + GDP at 349 forecast dates
python3 build_targets.py              # BEA advance estimates + GDPNow archive
for s in nominal real nominal_w real_w nominal_notrade nominal_notrade_pc; do MIDAS_SPEC=$s python3 nowcast.py >/dev/null; done
MIDAS_SPEC=nominal python3 analysis.py
python3 compare_specs.py
