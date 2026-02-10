# ice-plate-signal-bot

A Signal bot that checks license plates against the [stopice.net](https://www.stopice.net) and [defrostmn.net](https://defrostmn.net) databases. Designed to run in a Signal group chat — send `/plate ABC1234`, send `/plate` with a photo of a license plate, or just send a voice message saying the plate number. The bot looks up the plate across both sources concurrently.

## Privacy & Security

- **No logging in production** — `DEBUG=false` (default) disables all console and file logging
- **No secrets logged** — even with `DEBUG=true`, sensitive values like `DEFROST_DECRYPT_KEY` are never written to logs
- **Minimal data storage** — optional disk cache (`CACHE_DIR`) stores only defrostmn.net plate data to avoid re-downloading on restart; disabled by default
- **No plate persistence** — queried plate numbers are not stored beyond the request lifecycle
- **Generic User-Agent** — HTTP requests to the lookup services do not identify this bot
- **Non-root container** — the Docker image runs as an unprivileged user (`botuser`)
- **Decryption key isolation** — the `DEFROST_DECRYPT_KEY` value never leaves the server; it is only used in-memory for decryption

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
   - `SIGNAL_GROUP` — the external group ID (see step 6 below)
   - `SIGNAL_SERVICE` — signal-cli-rest-api address (default: `localhost:8080`)
   - `DEBUG` — set to `true` for development logging, `false` (default) for silent operation
   - `DEFROST_DECRYPT_KEY` — passphrase for decrypting defrostmn.net paginated plate data (optional; paginated lookups disabled if unset)
   - `DEFROST_JSON_URL` — URL of the defrostmn.net stopice snapshot JSON file (optional; stopice fallback disabled if unset)
   - `CHECK_PLATE` — plate known to exist in stopice.net, used by `check_sources.py` (also accepted as a CLI arg)
   - `CACHE_DIR` — directory for persisting defrostmn.net caches to disk (optional; disabled if unset). Docker-compose sets this to `/app/cache` automatically.
   - `STOPICE_URL` — URL for stopice.net plate tracker (default provided; override if the site changes paths)
   - `DEFROST_DATA_URL` — base URL for defrostmn.net plate data (default provided; override if the site changes paths)
   - `SIGNAL_API_PORT` — host port for the signal-cli-rest-api container (default: `8080`). Change this if port 8080 is already in use on your machine.

3. Start the services:
   ```
   docker compose up -d
   ```

4. Link the Signal account by scanning the QR code. Open this URL in a browser (replace `8080` with your `SIGNAL_API_PORT` if you changed it):
   ```
   http://localhost:8080/v1/qrcodelink?device_name=bot
   ```
   If the page doesn't load, wait a minute or two — signal-cli-rest-api takes some time to start up (the health check allows up to ~2 minutes).

5. Add the bot's phone number to the target Signal group from your personal Signal app.

6. Get the group ID by querying signal-cli-rest-api (replace `+1234567890` with your bot's phone number):
   ```
   curl http://localhost:8080/v1/groups/+1234567890
   ```
   Find your group in the JSON response and copy the `id` field — this is the **external** group ID (starts with `group.`). Do **not** use `internal_id`.

7. Set `SIGNAL_GROUP` in your `.env` to the group ID from step 6.

8. Restart the bot:
   ```
   docker compose restart bot
   ```

## Commands

| Command | Description |
|---------|-------------|
| `/plate [LICENSE PLATE]` | Check a plate against the ICE vehicle databases (stopice.net and defrostmn.net) |
| `/plate` + image | Attach a photo of a license plate — the bot reads the plate via OCR and runs the lookup |
| Voice message | Send a voice note saying the plate number — no command needed, the bot auto-detects voice messages |
| `/help` | Show available commands |

When a match is found, the bot replies with a per-source summary (match/no match for each source, plus plate status like "Confirmed ICE" for defrostmn matches). React with 👀 to that reply to fetch full details including dates, locations, vehicle info, and sighting descriptions.

The bot only responds in the configured group — it ignores DMs and other groups.

## Image OCR

Send `/plate` with an image attachment (no text needed) to have the bot read the plate automatically:

- Uses YOLO-based plate detection + CCT OCR via [fast-alpr](https://github.com/ankandrew/fast-alpr)
- Works best with clear, well-lit photos; supports both full vehicle photos and cropped plate images
- The bot confirms the detected plate (e.g. "Detected plate: ABC123") before running the lookup
- When both text and an image are provided, text input takes priority

## Voice Messages

Send a voice note saying the plate number — no `/plate` command needed. The bot automatically detects voice messages and transcribes them:

- Uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2-based Whisper) for speech-to-text
- Handles natural speech like "plate number Alpha Bravo Charlie 1 2 3" — NATO alphabet, number words ("one", "two"), and filler words are all supported
- The bot reacts with a microphone emoji, confirms the detected plate, then runs the lookup
- Speaks plate characters slowly? No problem — the bot merges adjacent single characters and short words into plate candidates
- Common letter/digit confusion (O/0, I/1, S/5, etc.) is handled automatically via variant generation

## Architecture

- `bot.py` — Entrypoint: loads config from env, registers commands, starts the Signal bot
- `commands/plate.py` — `/plate` command handler, voice plate command, 👀 reaction handler for detail lookups
- `commands/help.py` — `/help` command
- `lookup.py` — stopice.net lookup: HTTP requests and HTML parsing
- `lookup_defrost.py` — defrostmn.net lookup: paginated plates + legacy stopice snapshot
- `ocr.py` — License plate OCR: ALPR-based plate detection and reading (fast-alpr)
- `stt.py` — Speech-to-text: voice message transcription and plate extraction (faster-whisper)
- `check_sources.py` — Health-check script for live data sources

### Lookup flow

1. `/plate ABC123` (or `/plate` + image, or voice message) → queries both stopice.net and defrostmn.net concurrently
   - *Image path*: decodes the attached image, runs ALPR (YOLO plate detection + CCT OCR) to extract the plate text, then proceeds with the same lookup flow
   - *Voice path*: transcribes the audio via faster-whisper, extracts plate candidates from the transcript (filtering noise words, merging characters, applying confusion swaps), then proceeds with the same lookup flow
   - **stopice.net**: POST to search endpoint → regex-based parsing of the (malformed) HTML results page
   - **defrostmn.net**: searches two sub-sources in parallel and merges results:
     - *Paginated plates* — fetches metadata, exact match (cached until data changes)
     - *Stopice snapshot* — fetches legacy JSON, exact match (cached for 3 hours)
2. Bot replies with per-source results (match/no match/error for each)
3. 👀 reaction on the reply → fetches details from matched sources only
   - **stopice.net**: GET the detail page → BeautifulSoup parsing → full sighting details
   - **defrostmn.net**: re-queries both sub-sources → returns merged records

## Health Check

`check_sources.py` makes live requests to stopice.net and defrostmn.net, exercises the existing parsing code, and validates that the data structure hasn't changed. Run it to verify that the bot's lookup sources are reachable and returning well-formed data.

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

325 tests covering parsers, async HTTP retry logic, paginated page decryption, caching, JSON lookup, command handlers, OCR pipeline, speech-to-text plate extraction, formatting helpers, bot configuration, and health-check script orchestration.

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

## Contributing

- **Tests required** — all changes must include tests. Run `pytest -v` before submitting.
- **Test approach** — use saved HTML/JSON snapshots in `html_snapshots/` and mock all HTTP requests. No live requests to external services.
- **Code style** — enforced by [ruff](https://github.com/astral-sh/ruff). Run `ruff check .` and `ruff format --check .` before submitting, or install the pre-commit hook: `pip install pre-commit && pre-commit install`.
- **Branches** — feature branches off `main`; PRs target `main`.
- **Commits** — concise, descriptive commit messages focused on "why" not "what".

## Troubleshooting

**Bot not responding in the group**
- Set `DEBUG=true` in `.env` and restart (`docker compose restart bot`) to see log output with `docker compose logs bot`
- Verify `SIGNAL_GROUP` is the external group ID (starts with `group.`), not the `internal_id`
- Confirm the bot's phone number has been added to the Signal group
- Make sure the Signal account is linked (re-scan the QR code if needed)

**QR code page not loading**
- signal-cli-rest-api can take up to ~2 minutes to start. Wait and refresh.
- Check that the host port isn't in use: `SIGNAL_API_PORT` in `.env` defaults to 8080

**OCR not detecting plates**
- Works best with clear, well-lit photos where the plate is readable
- Supports both full vehicle photos and cropped plate images
- Very blurry, dark, or angled images may not produce results

**Voice message not recognized**
- Make sure you're sending a voice note (hold the microphone button), not an audio file attachment
- Speak the plate characters clearly — you can use NATO alphabet ("Alpha Bravo Charlie") or spell them out
- Number words ("one", "two") and filler words ("um", "the", "plate number") are filtered automatically
- If the bot doesn't respond to a voice message, it may not have detected a valid plate pattern in the transcript

## License

AGPLv3 — see [LICENSE](LICENSE).

## Acknowledgements

### Data sources

- [stopice.net](https://www.stopice.net) — primary ICE vehicle plate database
- [defrostmn.net](https://defrostmn.net) — Minnesota-focused ICE vehicle tracking with plate data

### Key libraries

- [signalbot](https://github.com/signalbot-org/signalbot) — Signal bot framework (MIT)
- [fast-alpr](https://github.com/ankandrew/fast-alpr) — license plate detection and OCR (MIT)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — speech-to-text transcription (MIT)
- [aiohttp](https://github.com/aio-libs/aiohttp) — async HTTP client (Apache-2.0)
- [beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/) — HTML parsing (MIT)
- [cryptography](https://github.com/pyca/cryptography) — decryption (Apache-2.0 / BSD-3-Clause)
- [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) — REST API wrapper for Signal

All dependencies use permissive licenses (MIT, Apache-2.0, BSD-3-Clause) that are fully compatible with this project's AGPLv3 license.
