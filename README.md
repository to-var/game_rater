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

Metadata source is decided **per game, by filename shape** — not by which
platform folder the file is in (folders can mix romset types, e.g. `NEOGEO`
holding both real MAME arcade sets and Neo Geo Pocket homebrew):

- A filename that looks like a bare MAME romset short-name (`ffight`, no
  spaces or parens) is tried against `data/arcade_names.json` — a local,
  offline MAME short-name → {title, rating, language} database bundled in
  this repo (586 entries, built once via `build_offline_arcade_db.py` from
  [ArcadeItalia](https://adb.arcadeitalia.net/)'s free MAME database). A hit
  here costs **zero network calls**. Only a short-name with no local match
  falls through to a live ArcadeItalia lookup.
- Everything else — descriptive filenames (No-Intro style, e.g. "Donkey
  Kong Country (U) (V1.1)"), or a MAME short-name with no match — falls
  back to [RAWG.io](https://rawg.io/apidocs) by cleaned name for a numeric
  rating; language is parsed from the filename's region/language tag
  (`(U)`, `(En,Fr,De)`, ...) since RAWG has no per-release language field.

The RAWG fallback needs a free API key: sign up at https://rawg.io/apidocs,
copy your key, then either set it via the desktop app's Settings menu
(persists to `~/.game_rater/config.json`) or set `RAWG_API_KEY` as an
environment variable. Without a key, MAME-shortname games still resolve
fully offline; everything else gets language only, no rating.

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

### build_offline_arcade_db.py

Extends `data/arcade_names.json` with any new MAME-shortname romsets found
in a roms folder (incremental — skips short-names already in the file):

```
python build_offline_arcade_db.py --roms-dir "F:\Roms"
python build_offline_arcade_db.py --roms-dir "F:\Roms" --platforms CPS1 NEOGEO
```

### migrate_json_to_db.py

One-time import for the old per-platform JSON files from before the SQLite
rewrite:

```
python migrate_json_to_db.py --metadata-dir "F:\metadata" --roms-dir "F:\Roms"
```
