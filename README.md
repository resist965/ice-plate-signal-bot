# ice-plate-signal-bot

A Signal bot that checks license plates against the [stopice.net](https://www.stopice.net) and [defrostmn.net](https://defrostmn.net) databases. Designed to run in a Signal group chat — send `/plate ABC1234` or send `/plate` with a photo of a license plate and the bot will look up the plate across both sources concurrently.

## Privacy

- **No logging in production** — `DEBUG=false` (default) disables all console and file logging
- **Minimal data storage** — optional disk cache (`CACHE_DIR`) stores only defrostmn.net plate data to avoid re-downloading on restart; disabled by default
- **No plate persistence** — queried plate numbers are not stored beyond the request lifecycle
- **Generic User-Agent** — HTTP requests to the lookup services do not identify this bot

## Prerequisites

- Docker and Docker Compose
- A spare phone number for Signal (the bot needs its own number)

## Setup

1. Clone the repository:
   ```
   git clone https://github.com/resist965/ice-plate-signal-bot.git
   cd ice-plate-signal-bot
   ```

2. Copy the environment template and fill in your values:
   ```
   cp .env.example .env
   ```

   Edit `.env`:
   - `PHONE_NUMBER` — the phone number for the bot's Signal account
   - `SIGNAL_GROUP` — the name or ID of the group the bot should respond in
   - `SIGNAL_SERVICE` — signal-cli-rest-api address (default: `localhost:8080`)
   - `DEBUG` — set to `true` for development logging, `false` (default) for silent operation
   - `DEFROST_DECRYPT_KEY` — passphrase for decrypting defrostmn.net paginated plate data (optional; paginated lookups disabled if unset)
   - `DEFROST_JSON_URL` — URL of the defrostmn.net stopice snapshot JSON file (optional; stopice fallback disabled if unset)
   - `CHECK_PLATE` — plate known to exist in stopice.net, used by `check_sources.py` (also accepted as a CLI arg)
   - `CACHE_DIR` — directory for persisting defrostmn.net caches to disk (optional; disabled if unset). Docker-compose sets this to `/app/cache` automatically.

3. Start the services:
   ```
   docker compose up -d
   ```

4. Link the Signal account by scanning the QR code:
   ```
   http://localhost:8080/v1/qrcodelink?device_name=bot
   ```

5. Restart the bot service after linking:
   ```
   docker compose restart bot
   ```

## Commands

| Command | Description |
|---------|-------------|
| `/plate [LICENSE PLATE]` | Check a plate against the ICE vehicle databases (stopice.net and defrostmn.net) |
| `/plate` + image | Attach a photo of a license plate — the bot reads the plate via OCR and runs the lookup |
| `/help` | Show available commands |

When a match is found, the bot replies with a per-source summary (match/no match for each source, plus plate status like "Confirmed ICE" for defrostmn matches). React with 👀 to that reply to fetch full details including dates, locations, vehicle info, and sighting descriptions.

The bot only responds in the configured group — it ignores DMs and other groups.

## Architecture

- `bot.py` — Entrypoint: loads config from env, registers commands, starts the Signal bot
- `commands/plate.py` — `/plate` command handler and 👀 reaction handler for detail lookups
- `commands/help.py` — `/help` command
- `lookup.py` — stopice.net lookup: HTTP requests and HTML parsing
- `lookup_defrost.py` — defrostmn.net lookup: paginated encrypted plates + legacy stopice snapshot
- `ocr.py` — License plate OCR: ALPR-based plate detection and reading (fast-alpr)
- `check_sources.py` — Health-check script for live data sources

### Lookup flow

1. `/plate ABC123` (or `/plate` + image attachment) → queries both stopice.net and defrostmn.net concurrently
   - *Image path*: decodes the attached image, runs ALPR (YOLO plate detection + CCT OCR) to extract the plate text, then proceeds with the same lookup flow
   - **stopice.net**: POST to search endpoint → regex-based parsing of the (malformed) HTML results page
   - **defrostmn.net**: searches two sub-sources in parallel and merges results:
     - *Paginated encrypted plates* — fetches metadata, decrypts AES-256-GCM pages, exact match (cached until data changes)
     - *Stopice snapshot* — fetches legacy JSON, exact match (cached for 3 hours)
2. Bot replies with per-source results (match/no match/error for each)
3. 👀 reaction on the reply → fetches details from matched sources only
   - **stopice.net**: GET the detail page → BeautifulSoup parsing → full sighting details
   - **defrostmn.net**: re-queries both sub-sources → returns merged records

## Health Check

`check_sources.py` makes live requests to stopice.net and defrostmn.net, exercises the existing parsing and decryption code, and validates that the data structure hasn't changed. Run it to verify that the bot's lookup sources are reachable and returning well-formed data.

```
# With a known stopice plate:
python check_sources.py SXF180

# Or via environment variable:
CHECK_PLATE=SXF180 python check_sources.py
```

Six checks are run:

| # | Source | Check | Skipped if |
|---|--------|-------|------------|
| 1 | stopice.net | Search page parse | — |
| 2 | stopice.net | Detail page parse | — |
| 3 | defrostmn.net | Meta fetch (structure) | — |
| 4 | defrostmn.net | Page decryption | `DEFROST_DECRYPT_KEY` unset |
| 5 | defrostmn.net | Stopice JSON snapshot | `DEFROST_JSON_URL` unset |
| 6 | defrostmn.net | Full plate lookup (merge) | Neither defrost env var set |

Exit codes: `0` = all passed, `1` = failures, `2` = no plate provided.

## Testing

Install test dependencies and run the suite:

```
pip install -r requirements-dev.txt
pytest -v
pytest --cov=. --cov-report=term-missing
```

228 tests covering parsers, async HTTP retry logic, encrypted page decryption, caching, JSON lookup, command handlers, OCR pipeline, formatting helpers, bot configuration, and health-check script orchestration.

Tests use saved HTML/JSON snapshots in `html_snapshots/` and mock all HTTP requests — no live requests to external services are made during testing.

## Development

Run outside Docker with debug logging enabled:

```
pip install -r requirements.txt
export SIGNAL_SERVICE=localhost:8080
export PHONE_NUMBER=+1234567890
export SIGNAL_GROUP=your-group-name
export DEFROST_DECRYPT_KEY=your-decryption-passphrase
export DEFROST_JSON_URL=https://defrostmn.net/plate-check/stopice_plates.json
export DEBUG=true
python bot.py
```

## License

AGPLv3 — see [LICENSE](LICENSE).
