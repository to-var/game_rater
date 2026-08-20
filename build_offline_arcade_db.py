"""
Builds/updates data/arcade_names.json: a local, offline MAME short-name ->
{title, rating, language} database for arcade platforms (CPS1/2/3, NEOGEO,
NEOCD), sourced once from ArcadeItalia's free MAME database.

Once built, scrape_metadata.py resolves arcade games entirely from this
bundled file -- no network calls needed at scrape time. Re-run this script
only when you want to add coverage for new romsets (it skips short-names
already present, so it's incremental/resumable).

Usage:
    python build_offline_arcade_db.py --roms-dir "F:\\Roms"
    python build_offline_arcade_db.py --roms-dir "F:\\Roms" --platforms CPS1 NEOGEO
"""

import argparse
import json
import time
from pathlib import Path

import db
import scrape_metadata


def save_offline_db(offline_db: dict) -> None:
    scrape_metadata.ARCADE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(scrape_metadata.ARCADE_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(offline_db, f, indent=2, ensure_ascii=False, sort_keys=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the local offline arcade (MAME) name/rating/language database.")
    parser.add_argument("--roms-dir", required=True, help=r'Path to roms folder, e.g. "F:\Roms"')
    parser.add_argument("--platforms", nargs="*", default=sorted(scrape_metadata.ARCADE_PLATFORMS),
                         help="Which arcade platforms to cover (default: all)")
    args = parser.parse_args()

    session_id = db.get_or_create_session(str(Path(args.roms_dir).resolve()))
    offline_db = scrape_metadata.load_offline_arcade_db()

    short_names = set()
    skipped_non_mame = 0
    for platform in args.platforms:
        for game in db.get_games(session_id, platform):
            stem = Path(game["file_path"]).stem
            if scrape_metadata.looks_like_mame_shortname(stem):
                short_names.add(stem)
            else:
                skipped_non_mame += 1

    if skipped_non_mame:
        print(f"Skipped {skipped_non_mame} non-MAME-shaped filenames (not real romsets, e.g. console ROMs mixed into an arcade folder)")

    to_fetch = sorted(short_names - offline_db.keys())
    print(f"{len(short_names)} distinct romsets, {len(to_fetch)} not yet in {scrape_metadata.ARCADE_DB_PATH}")

    for i, short_name in enumerate(to_fetch, 1):
        result = scrape_metadata.lookup_arcade_metadata(short_name)
        offline_db[short_name] = result or {"title": None, "rating": None, "language": None}
        print(f"[{i}/{len(to_fetch)}] {short_name} -> {offline_db[short_name]}")
        if i % 20 == 0:
            save_offline_db(offline_db)  # checkpoint periodically
        time.sleep(scrape_metadata.REQUEST_DELAY_SECONDS)

    save_offline_db(offline_db)
    print(f"Saved {len(offline_db)} entries to {scrape_metadata.ARCADE_DB_PATH}")


if __name__ == "__main__":
    main()
