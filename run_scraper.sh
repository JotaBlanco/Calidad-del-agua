#!/bin/bash
cd /Users/javiquix/Desktop/Personal/Calidad-del-agua
source .venv/bin/activate
python3 -u scraper/scrape_all.py --parallel --skip-pdfs --max-parallel 6 --use-html
