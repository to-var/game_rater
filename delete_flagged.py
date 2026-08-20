"""
Read every <metadata-dir>\\<PLATFORM>.json, delete any ROM file whose
"remove" field is true, and drop that entry from the JSON.

Usage:
    python delete_flagged.py --metadata-dir "F:\\metadata"            # asks for confirmation
    python delete_flagged.py --metadata-dir "F:\\metadata" --yes      # skips confirmation
"""

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete ROM files flagged with remove=true in the metadata JSON files.")
    parser.add_argument("--metadata-dir", required=True, help=r'Path to the metadata folder, e.g. "F:\metadata"')
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    metadata_dir = Path(args.metadata_dir)
    json_paths = sorted(metadata_dir.glob("*.json"))
    if not json_paths:
        print(f"No platform JSON files found in {metadata_dir}")
        return

    # Collect everything flagged for removal first, across all platforms.
    flagged: list[tuple[Path, list[dict], dict]] = []
    for json_path in json_paths:
        with open(json_path, "r", encoding="utf-8") as f:
            entries = json.load(f)
        for entry in entries:
            if entry.get("remove"):
                flagged.append((json_path, entries, entry))

    if not flagged:
        print("Nothing flagged for removal.")
        return

    print(f"{len(flagged)} file(s) flagged for removal:")
    for json_path, _, entry in flagged:
        print(f"  [{json_path.stem}] {entry['file_path']}")

    if not args.yes:
        answer = input("\nDelete these files permanently? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted, nothing deleted.")
            return

    deleted_count = 0
    modified_jsons: dict[Path, list[dict]] = {}
    for json_path, entries, entry in flagged:
        file_path = Path(entry["file_path"])
        try:
            if file_path.exists():
                file_path.unlink()
                print(f"Deleted: {file_path}")
                deleted_count += 1
            else:
                print(f"Already missing on disk, removing from JSON: {file_path}")
            entries.remove(entry)
            modified_jsons[json_path] = entries
        except OSError as e:
            print(f"FAILED to delete {file_path}: {e}")

    for json_path, entries in modified_jsons.items():
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)

    print(f"\nDone. {deleted_count} file(s) deleted, JSON files updated.")


if __name__ == "__main__":
    main()
