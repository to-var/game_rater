# game_rater

Scripts to inventory a ROMs folder into per-platform JSON files, enrich them
with rating/language metadata, and delete files you flag for removal.

Expected roms folder layout: `<roms-dir>\<PLATFORM>\*.zip` (one subfolder per
platform, e.g. `SFC`, `FC`, `GBA`, ...).

## 1. fetch_games.py

Scans the roms folder and writes/updates `<metadata-dir>\<PLATFORM>.json`,
one entry per ROM file:

```json
{
  "file_path": "F:\\Roms\\SFC\\Donkey Kong Country (U) (V1.1).zip",
  "name": "Donkey Kong Country (U) (V1.1)",
  "rating": null,
  "language": null,
  "remove": false
}
```

Re-running merges with existing JSON — files already present keep their
`rating` / `language` / `remove` values.

```
python fetch_games.py --roms-dir "F:\Roms" [--metadata-dir "F:\metadata"]
```

`--metadata-dir` defaults to a `metadata` folder next to `--roms-dir`.

## 2. scrape_metadata.py

Fills in `rating` (via [TheGamesDB](https://thegamesdb.net/)) and `language`
(parsed from filename region/language tags, e.g. `(U)`, `(En,Fr,De)`).

Needs a free TheGamesDB API key:

1. Create an account at https://thegamesdb.net/
2. Request/copy your API key from your account page.
3. `$env:TGDB_API_KEY = "your-key-here"` (PowerShell) or
   `export TGDB_API_KEY="your-key-here"` (bash)

```
python scrape_metadata.py --metadata-dir "F:\metadata" [PLATFORM ...]
```

Resumable — entries that already have a rating are skipped, progress saved
after each platform.

## 3. delete_flagged.py

Manually flip `"remove": true` on entries in the JSON files for games you
want gone, then run:

```
python delete_flagged.py --metadata-dir "F:\metadata" [--yes]
```

Deletes the matching files on disk and removes those entries from the JSON.
Asks for confirmation unless `--yes` is passed.
