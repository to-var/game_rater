"""
Scan <roms-dir>\\<PLATFORM>\\*.zip for every platform folder and store one
row per ROM file in the SQLite DB (~/.game_rater/game_rater.db), under a
session keyed by the roms folder's absolute path.

Re-running this script for the same --roms-dir reuses that folder's
existing session: files already present keep their rating/language/
remove_flag; files no longer on disk are dropped; new files are inserted
with NULL rating/language for scrape_metadata.py to fill in later.

Usage:
    python fetch_games.py --roms-dir "F:\\Roms"
"""

import argparse
from pathlib import Path

import db

ROM_EXTENSIONS = {".zip", ".bin", ".iso", ".chd", ".cue"}


def clean_name(filename: str) -> str:
    return Path(filename).stem


def scan_platform(platform_dir: Path) -> list[dict]:
    rom_files = sorted(
        p for p in platform_dir.iterdir()
        if p.is_file() and p.suffix.lower() in ROM_EXTENSIONS
    )
    return [
        {"file_path": str(rom_file), "name": clean_name(rom_file.name)}
        for rom_file in rom_files
    ]


def fetch_roms_dir(roms_dir: Path) -> int:
    """Scans roms_dir and upserts all platforms into the DB. Returns the session id."""
    if not roms_dir.exists():
        raise SystemExit(f"Roms folder not found: {roms_dir}")

    session_id = db.get_or_create_session(str(roms_dir.resolve()))

    platform_dirs = sorted(p for p in roms_dir.iterdir() if p.is_dir())
    for platform_dir in platform_dirs:
        entries = scan_platform(platform_dir)
        db.upsert_games(session_id, platform_dir.name, entries)
        print(f"{platform_dir.name}: {len(entries)} entries")

    return session_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan a roms folder into the game_rater database.")
    parser.add_argument("--roms-dir", required=True, help=r'Path to roms folder, e.g. "F:\Roms"')
    args = parser.parse_args()

    session_id = fetch_roms_dir(Path(args.roms_dir))
    print(f"Session id: {session_id}")


if __name__ == "__main__":
    main()
