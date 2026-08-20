"""
One-time migration: import the old per-platform JSON files (produced by the
pre-SQLite version of fetch_games.py) into the game_rater database as a
single session pointing at the given roms folder.

Usage:
    python migrate_json_to_db.py --metadata-dir "F:\\metadata" --roms-dir "F:\\Roms"
"""

import argparse
import json
from pathlib import Path

import db


def main() -> None:
    parser = argparse.ArgumentParser(description="Import old JSON metadata files into the game_rater database.")
    parser.add_argument("--metadata-dir", required=True, help=r'Folder containing the old <PLATFORM>.json files')
    parser.add_argument("--roms-dir", required=True, help=r'Roms folder these JSON files were scanned from')
    args = parser.parse_args()

    metadata_dir = Path(args.metadata_dir)
    roms_dir = Path(args.roms_dir)
    session_id = db.get_or_create_session(str(roms_dir.resolve()))

    for json_path in sorted(metadata_dir.glob("*.json")):
        if json_path.stem.startswith("_"):
            continue
        platform = json_path.stem
        with open(json_path, "r", encoding="utf-8") as f:
            old_entries = json.load(f)

        entries = [{"file_path": e["file_path"], "name": e["name"]} for e in old_entries]
        db.upsert_games(session_id, platform, entries)

        games = {g["file_path"]: g for g in db.get_games(session_id, platform)}
        for old_entry in old_entries:
            game = games.get(old_entry["file_path"])
            if game is None:
                continue
            db.update_metadata(game["id"], old_entry.get("rating"), old_entry.get("language"))
            if old_entry.get("remove"):
                db.set_remove_flag(game["id"], True)

        print(f"{platform}: imported {len(entries)} entries")

    print(f"Session id: {session_id}")


if __name__ == "__main__":
    main()
