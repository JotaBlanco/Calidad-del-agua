#!/bin/bash
# Retry launching scrapers every 30 minutes until SINAC is back up.
# Tests the data endpoint; if it returns real data (not the error page),
# launches scrapers for remaining provinces.

cd "$(dirname "$0")/.."
VENV_PYTHON=".venv/bin/python3"
TEST_URL="https://sinac.sanidad.gob.es/CiudadanoWeb/ciudadano/informacionAbastecimientoActionBuscar.do"
PROVINCES="44 46 48 49 50"
MAX_ATTEMPTS=20
INTERVAL=1800  # 30 minutes

echo "[$(date)] Starting SINAC availability monitor"
echo "  Will check every 30 minutes, up to $MAX_ATTEMPTS attempts"
echo "  Remaining provinces: $PROVINCES"
echo ""

for attempt in $(seq 1 $MAX_ATTEMPTS); do
    echo "[$(date)] Attempt $attempt/$MAX_ATTEMPTS — testing SINAC..."

    # Fetch the search page and check for the error message
    RESPONSE=$(curl -s -m 30 "$TEST_URL")
    
    if echo "$RESPONSE" | grep -q "problema técnico"; then
        echo "  Site still down (technical error page). Waiting 30 min..."
        sleep $INTERVAL
        continue
    fi

    if [ -z "$RESPONSE" ]; then
        echo "  No response / timeout. Waiting 30 min..."
        sleep $INTERVAL
        continue
    fi

    # If we get here, the site seems to be responding normally
    echo "  SINAC is BACK UP!"
    echo ""

    # Launch scrapers for each remaining province sequentially via parallel mode
    # Using --parallel with the 5 specific provinces
    for PROV in $PROVINCES; do
        echo "[$(date)] Launching scraper for province $PROV..."
        $VENV_PYTHON -u scraper/scrape_all.py --provincia $PROV --parallel --max-parallel 2
    done

    echo "[$(date)] All scrapers launched. Done!"
    exit 0
done

echo "[$(date)] Gave up after $MAX_ATTEMPTS attempts (~$(($MAX_ATTEMPTS * 30 / 60)) hours)."
exit 1
