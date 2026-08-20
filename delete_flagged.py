"""
Delete every ROM file flagged remove=true in the database, and drop those
rows. Defaults to the current session (--roms-dir / --session-id); pass
--all-sessions to sweep every session in the DB.

Usage:
    python delete_flagged.py --roms-dir "F:\\Roms"                 # asks for confirmation
    python delete_flagged.py --roms-dir "F:\\Roms" --yes           # skips confirmation
    python delete_flagged.py --all-sessions --yes
"""

import argparse
from pathlib import Path

import db


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete ROM files flagged remove=true in the database.")
    parser.add_argument("--roms-dir", help=r'Roms folder identifying the session, e.g. "F:\Roms"')
    parser.add_argument("--session-id", type=int, help="Session id (alternative to --roms-dir)")
    parser.add_argument("--all-sessions", action="store_true", help="Sweep flagged games across every session")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    if args.all_sessions:
        session_id = None
    elif args.session_id:
        session = db.get_session(args.session_id)
        if not session:
            raise SystemExit(f"No session with id {args.session_id}")
        session_id = session["id"]
    elif args.roms_dir:
        session_id = db.get_or_create_session(str(Path(args.roms_dir).resolve()))
    else:
        raise SystemExit("Provide --roms-dir, --session-id, or --all-sessions")

    flagged = db.get_flagged_games(session_id)
    if not flagged:
        print("Nothing flagged for removal.")
        return

    print(f"{len(flagged)} file(s) flagged for removal:")
    for game in flagged:
        print(f"  [{game['platform']}] {game['file_path']}")

    if not args.yes:
        answer = input("\nDelete these files permanently? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted, nothing deleted.")
            return

    deleted_count = 0
    for game in flagged:
        file_path = Path(game["file_path"])
        try:
            if file_path.exists():
                file_path.unlink()
                print(f"Deleted: {file_path}")
                deleted_count += 1
            else:
                print(f"Already missing on disk, removing from DB: {file_path}")
            db.delete_game(game["id"])
        except OSError as e:
            print(f"FAILED to delete {file_path}: {e}")

    print(f"\nDone. {deleted_count} file(s) deleted.")


if __name__ == "__main__":
    main()
