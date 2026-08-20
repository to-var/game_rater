# game_rater

Inventories a ROMs folder into a SQLite database (`~/.game_rater/game_rater.db`),
enriches games with rating/language metadata, and deletes files you flag for
removal. Ships as a desktop GUI (`app.py`) and three standalone CLI scripts
that all share the same DB.

Expected roms folder layout: `<roms-dir>\<PLATFORM>\*.zip` (one subfolder per
platform, e.g. `SFC`, `FC`, `GBA`, ...).

Each scanned roms folder becomes a **session** (keyed by its absolute path).
Re-scanning a folder reuses its session — existing rating/language/remove
values are kept, only new/missing files change. `remove` toggles are written
straight to the DB, so closing and reopening the app resumes exactly where
you left off; the app auto-opens your most recently scanned session on
startup, and you can switch between sessions from **File → Open Previous
Session...**.

## Desktop app

```
python app.py
```

- **File → New Scan...** — pick a roms folder, scans it into a session.
- **File → Open Previous Session...** — lists every roms folder scanned
  before (path, game count, last scanned) and reopens it.
- Platform list on the left, game table on the right. Double-click a row to
  toggle its remove flag (persists immediately).
- **Scrape Metadata** — fills rating/language for the selected platform.
- **Delete Flagged Files** — deletes every file flagged `remove` in the
  current session, after confirmation.
- **Settings → Set RAWG API Key...** — saved to
  `~/.game_rater/config.json`, reused across runs and by the CLI scripts. A
  red warning banner shows in the main window whenever no key is set. Only
  needed for console platforms — arcade platforms don't need a key at all
  (see below).
- **Settings → View API Usage...** — calls used this month vs. the 1000/mo
  allowance (auto-resets each calendar month).

## CLI scripts

### fetch_games.py

```
python fetch_games.py --roms-dir "F:\Roms"
```

Scans the folder and upserts games into the session for that path.

### scrape_metadata.py

Metadata source depends on platform, since a single API doesn't cover both
well:

- **Arcade** (`CPS1`, `CPS2`, `CPS3`, `NEOGEO`, `NEOCD`) — these MAME
  romsets are named by cryptic short codes (`ffight.zip`, not "Final
  Fight"), which a general game database can't fuzzy-match. Resolved
  instead via [ArcadeItalia](https://adb.arcadeitalia.net/)'s free, keyless
  MAME database, which returns the real title, a numeric rating, and
  language in one call — no API key needed for these platforms.
- **Everything else** (consoles — `FC`, `SFC`, `GBA`, `GB`, `GBC`, `PS`,
  `FDS`) — filenames are already descriptive (No-Intro style, e.g. "Donkey
  Kong Country (U) (V1.1)"), so rating comes from
  [RAWG.io](https://rawg.io/apidocs) by name search; language is parsed
  from the filename's region/language tag (`(U)`, `(En,Fr,De)`, ...) since
  RAWG has no per-release language field either.

Console lookups need a free RAWG API key: sign up at
https://rawg.io/apidocs, copy your key, then either set it via the desktop
app's Settings menu (persists to `~/.game_rater/config.json`) or set
`RAWG_API_KEY` as an environment variable for the current shell.

```
python scrape_metadata.py --roms-dir "F:\Roms" [PLATFORM ...]
python scrape_metadata.py --session-id 3
```

Resumable — entries that already have rating+language are skipped.

### delete_flagged.py

Manually flip `remove` on games (via the app, or directly in the DB), then:

```
python delete_flagged.py --roms-dir "F:\Roms" [--yes]
python delete_flagged.py --all-sessions --yes
```

Deletes the matching files on disk and drops those rows.

### migrate_json_to_db.py

One-time import for the old per-platform JSON files from before the SQLite
rewrite:

```
python migrate_json_to_db.py --metadata-dir "F:\metadata" --roms-dir "F:\Roms"
```
