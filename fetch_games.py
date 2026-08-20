"""
Scan <roms-dir>\\<PLATFORM>\\*.zip for every platform folder and write/update
<metadata-dir>\\<PLATFORM>.json with one entry per ROM file:

    {
        "file_path": "F:\\Roms\\SFC\\Donkey Kong Country (U) (V1.1).zip",
        "name": "Donkey Kong Country (U) (V1.1)",
        "rating": null,
        "language": null,
        "remove": false
    }

Re-running this script MERGES with any existing platform JSON: files already
present keep their "rating" / "language" / "remove" values (so re-scanning
never wipes scraped metadata or the user's remove flags). Files no longer on
disk are dropped from the JSON. New files are appended with placeholder
fields for scrape_metadata.py to fill in later.

Usage:
    python fetch_games.py --roms-dir "F:\\Roms" [--metadata-dir "F:\\metadata"]

If --metadata-dir is omitted, it defaults to a "metadata" folder next to
--roms-dir (i.e. <roms-dir>\\..\\metadata).
"""

import argparse
import json
from pathlib import Path

ROM_EXTENSIONS = {".zip", ".bin", ".iso", ".chd", ".cue"}


def clean_name(filename: str) -> str:
    return Path(filename).stem


def fetch_platform(platform_dir: Path, metadata_dir: Path) -> list[dict]:
    rom_files = sorted(
        p for p in platform_dir.iterdir()
        if p.is_file() and p.suffix.lower() in ROM_EXTENSIONS
    )

    json_path = metadata_dir / f"{platform_dir.name}.json"
    existing_by_path: dict[str, dict] = {}
    if json_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
            for entry in json.load(f):
                existing_by_path[entry["file_path"]] = entry

    entries = []
    for rom_file in rom_files:
        file_path = str(rom_file)
        if file_path in existing_by_path:
            entries.append(existing_by_path[file_path])
        else:
            entries.append({
                "file_path": file_path,
                "name": clean_name(rom_file.name),
                "rating": None,
                "language": None,
                "remove": False,
            })

    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan a roms folder and build per-platform metadata JSON files.")
    parser.add_argument("--roms-dir", required=True, help=r'Path to roms folder, e.g. "F:\Roms"')
    parser.add_argument("--metadata-dir", default=None, help='Path to write JSON files to (default: "metadata" folder next to --roms-dir)')
    args = parser.parse_args()

    roms_dir = Path(args.roms_dir)
    metadata_dir = Path(args.metadata_dir) if args.metadata_dir else roms_dir.parent / "metadata"

    if not roms_dir.exists():
        raise SystemExit(f"Roms folder not found: {roms_dir}")

    metadata_dir.mkdir(parents=True, exist_ok=True)

    platform_dirs = sorted(p for p in roms_dir.iterdir() if p.is_dir())

    for platform_dir in platform_dirs:
        entries = fetch_platform(platform_dir, metadata_dir)
        json_path = metadata_dir / f"{platform_dir.name}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        print(f"{platform_dir.name}: {len(entries)} entries -> {json_path}")


if __name__ == "__main__":
    main()
