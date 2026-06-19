# Treatwell Outreach Scraper

Automated scraper for barbershop listings on Treatwell UK and FR. Exports leads to CSV and Google Sheets for cold email outreach.

## Pipeline overview

```
treatwell.co.uk / .fr  ──►  scraper  ──►  leads.csv
                                               │
                                          enricher
                                         /        \
                               Google Maps      website crawl
                              (phone + website)  (email)
                                               │
                                      leads_enriched.csv
```

## What it collects

### Step 1 — Treatwell scraper

| Field | Source |
|---|---|
| `name` | Venue detail page |
| `city` | Listing URL |
| `country` | Site (UK / FR) |
| `address` | Venue detail page |
| `rating` | Listing card |
| `review_count` | Listing card |
| `booking_url` | Listing card (their current booking platform URL) |
| `services_preview` | Listing card |

### Step 2 — Enricher (Google Maps + website crawl)

| Field | Source |
|---|---|
| `phone` | Google Maps business panel |
| `website` | Google Maps business panel |
| `email` | Business website (homepage → /contact → /about) |
| `maps_url` | Google Maps permalink |
| `enrich_status` | `found` / `not_found` / `error` / `skipped` |

## Quick start (local)

```bash
git clone https://github.com/ilyas7mourad-art/treatwell-outreach.git
cd treatwell-outreach
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env  # edit as needed

# Scrape UK barbershops in London and Manchester (2 pages each)
python -m scraper.main --site uk --cities london manchester --max-pages 2

# Scrape both UK + FR, all cities, full depth
python -m scraper.main --site both
```

Output: `output/leads_YYYYMMDD_HHMMSS.csv`

### Step 2 — Enrich with phone + email

```bash
# Install Playwright browser (once)
playwright install chromium

# Enrich all leads in a CSV
python -m scraper.enrich --input output/leads_20240101_120000.csv

# Resume after a crash (skips rows that already have enrich_status)
python -m scraper.enrich --input output/leads.csv --resume

# Skip Maps step (email-only, if you already have websites)
python -m scraper.enrich --input output/leads.csv --skip-maps

# First 50 rows only (for testing)
python -m scraper.enrich --input output/leads.csv --limit 50
```

Output: `output/leads_YYYYMMDD_HHMMSS_enriched.csv`

## CLI options

### Scraper (`python -m scraper.main`)

```
--site          uk | fr | both (default: uk)
--cities        Override cities, e.g. --cities london leeds
--max-pages     Max listing pages per city (default: 9, ~200 venues/city)
--delay-min     Min seconds between requests (default: 2)
--delay-max     Max seconds between requests (default: 5)
--output-dir    CSV output directory (default: output/)
--no-raw-html   Skip saving raw HTML (saves disk space)
--sheets        Also push to Google Sheets (requires .env config)
--log-level     DEBUG | INFO | WARNING | ERROR
```

### Enricher (`python -m scraper.enrich`)

```
--input           Path to input leads CSV (required)
--output          Output path (default: input_enriched.csv)
--limit           Only process first N rows
--resume          Skip rows already enriched (safe to restart)
--skip-maps       Skip Google Maps step
--maps-only       Skip email crawl step
--no-headless     Show browser window (useful for debugging Maps)
--maps-delay-min  Min seconds between Maps searches (default: 4)
--maps-delay-max  Max seconds between Maps searches (default: 9)
--log-level       DEBUG | INFO | WARNING | ERROR
```

## Google Sheets export

1. Create a [Google Service Account](https://console.cloud.google.com/iam-admin/serviceaccounts)
2. Enable Google Sheets API and Google Drive API
3. Download the JSON key file
4. Share your Google Sheet with the service account email
5. Set in `.env`:
   ```
   GOOGLE_SHEETS_CREDENTIALS_JSON=path/to/credentials.json
   GOOGLE_SHEET_ID=your_sheet_id_from_url
   ```
6. Run with `--sheets` flag

## Docker (recommended for server)

```bash
# Build
docker compose build

# One-off run
docker compose run --rm scraper

# Custom args
docker compose run --rm scraper python -m scraper.main --site uk --cities london --max-pages 3
```

Output lands in `./output/`, logs in `./logs/`, raw HTML in `./raw_html/`.

## Deploy to Ubuntu home server (Tailscale)

```bash
chmod +x deploy.sh
./deploy.sh
```

This SSHes to `100.96.142.127`, clones the repo to `/opt/treatwell-outreach`, installs Docker if needed, and builds the image.

### Cron schedule (run every Monday at 6am)

```bash
# SSH into your server, then:
crontab -e

# Add:
0 6 * * 1 cd /opt/treatwell-outreach && docker compose run --rm scraper >> /var/log/treatwell-scraper.log 2>&1
```

### systemd (alternative to cron)

```bash
sudo cp treatwell-scraper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start treatwell-scraper

# Schedule weekly via systemd timer:
sudo systemctl enable treatwell-scraper.timer  # (create .timer file if needed)
```

## Project structure

```
treatwell-outreach/
├── scraper/
│   ├── __init__.py
│   ├── main.py          # CLI entry point
│   ├── scraper.py       # Core scraping logic (UK + FR)
│   ├── exporters.py     # CSV + Google Sheets export
│   └── utils.py         # Rate limiting, user agents, logging
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── deploy.sh
├── requirements.txt
├── treatwell-scraper.service
└── README.md
```

## Rate limiting & anti-bot

- Random 2–5s delay between every request (configurable)
- 8 rotating user agents (desktop + mobile, Chrome + Firefox + Safari)
- Full browser-like request headers
- Automatic 30s back-off on HTTP 429
- Raw HTML saved to `raw_html/` for debugging blocked responses

## Next steps (n8n automation)

1. CSV → n8n "Read CSV" node → filter by city/rating
2. n8n → Brevo/Gmail SMTP node → send cold email with venue name, city
3. Track opens/replies in Airtable or Google Sheets
