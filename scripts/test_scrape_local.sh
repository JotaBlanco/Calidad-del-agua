#!/bin/bash
# Run the same scrape the daily CI job runs, against a small slice, locally.
#
#   ./scripts/test_scrape_local.sh 36        # 3 municipalities of Pontevedra
#   ./scripts/test_scrape_local.sh 36 10     # 10 of them
#
# The province's progress file is backed up and restored on exit (including on
# Ctrl-C), so the fully-scraped state survives.  Raw CSVs are appended and
# deduplicated exactly as in production — that part is intentionally real.

set -euo pipefail
cd "$(dirname "$0")/.."

PROV="${1:?usage: $0 <province_code> [n_municipalities]}"
LIMIT="${2:-3}"
PROGRESS="data/progress/progress_${PROV}.json"

PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"

BACKUP=""
if [ -f "$PROGRESS" ]; then
    BACKUP="$(mktemp)"
    cp "$PROGRESS" "$BACKUP"
fi

restore() {
    if [ -n "$BACKUP" ]; then
        mv "$BACKUP" "$PROGRESS"
        echo ""
        echo "Progress for province $PROV restored."
    else
        rm -f "$PROGRESS"
    fi
}
trap restore EXIT INT TERM

echo "Province $PROV — $LIMIT municipalities, HTML mode (same flags as CI)"
echo ""

START=$(date +%s)
"$PYTHON" -u scraper/scrape_all.py \
    --provincia "$PROV" --skip-pdfs --use-html --reset --limit "$LIMIT"
ELAPSED=$(( $(date +%s) - START ))

echo ""
echo "Elapsed: ${ELAPSED}s for $LIMIT municipalities"
echo "         ~$(( ELAPSED / LIMIT ))s per municipality"
echo "         → a 153-municipality province (the median) would take" \
     "~$(( ELAPSED * 153 / LIMIT / 3600 ))h$(( (ELAPSED * 153 / LIMIT % 3600) / 60 ))m"
