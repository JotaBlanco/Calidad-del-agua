#!/bin/bash
# Run the same scrape the nightly CI job runs, against a small slice, locally.
#
#   ./scripts/test_scrape_local.sh 36           # 3 municipalities of Pontevedra
#   ./scripts/test_scrape_local.sh 36 10        # 10 of them
#   ./scripts/test_scrape_local.sh 36,15 3      # both provinces in parallel, 3 each
#
# The comma form is the one worth rehearsing: it puts exactly two scrapers on
# SINAC at once, which is what the workflow does every night.  Never raise it
# past two — heavier parallelism has taken the site down before.
#
# Each province's progress file is backed up and restored on exit, including on
# Ctrl-C, so the fully-scraped state survives.  Raw CSVs are appended and
# deduplicated exactly as in production — that part is intentionally real.

set -uo pipefail
cd "$(dirname "$0")/.."

PROVS_RAW="${1:?usage: $0 <province_code[,code]> [n_municipalities]}"
LIMIT="${2:-3}"
IFS=', ' read -r -a PROVS <<< "$PROVS_RAW"

if [ "${#PROVS[@]}" -gt 2 ]; then
    echo "Refusing to run more than 2 scrapers at once (asked for ${#PROVS[@]})." >&2
    exit 1
fi

PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"

BACKUP_DIR="$(mktemp -d)"

restore() {
    echo ""
    for PROV in "${PROVS[@]}"; do
        PROGRESS="data/progress/progress_${PROV}.json"
        if [ -f "$BACKUP_DIR/$PROV.json" ]; then
            mv "$BACKUP_DIR/$PROV.json" "$PROGRESS"
            echo "Progress for province $PROV restored."
        else
            rm -f "$PROGRESS"
            echo "Province $PROV had no prior progress; removed the one just created."
        fi
    done
    rmdir "$BACKUP_DIR" 2>/dev/null || true
}
trap restore EXIT INT TERM

for PROV in "${PROVS[@]}"; do
    [ -f "data/progress/progress_${PROV}.json" ] \
        && cp "data/progress/progress_${PROV}.json" "$BACKUP_DIR/$PROV.json"
done

echo "Provinces: ${PROVS[*]} — $LIMIT municipalities each, HTML mode (same flags as CI)"
echo "Concurrent scrapers: ${#PROVS[@]}"
echo ""

START=$(date +%s)
PIDS=()
for PROV in "${PROVS[@]}"; do
    (
        "$PYTHON" -u scraper/scrape_all.py \
            --provincia "$PROV" --skip-pdfs --use-html --reset --limit "$LIMIT" 2>&1 \
        | while IFS= read -r line; do printf '[%s] %s\n' "$PROV" "$line"; done
    ) &
    PIDS+=($!)
done
for PID in "${PIDS[@]}"; do wait "$PID"; done
ELAPSED=$(( $(date +%s) - START ))

N=$(( LIMIT * ${#PROVS[@]} ))
echo ""
echo "Elapsed: ${ELAPSED}s for $N municipalities across ${#PROVS[@]} province(s)"
echo "         ~$(( ELAPSED * ${#PROVS[@]} / N ))s per municipality per scraper"
echo "         → a 153-municipality province (the median) would take about" \
     "$(( ELAPSED * ${#PROVS[@]} * 153 / N / 3600 ))h" \
     "$(( (ELAPSED * ${#PROVS[@]} * 153 / N % 3600) / 60 ))m"
echo ""
echo "The nightly job stops each scraper at 4h (timeout --signal=INT 14400)."
