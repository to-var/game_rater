"""
Fill in "rating" and "language" for games stored in the DB (see db.py) using
TheGamesDB (https://thegamesdb.net/) for rating, and filename region/
language tags for language (TheGamesDB has no per-release language field,
so this is the best available signal without a different API).

Setup: set your API key persistently via the desktop app's Settings menu
(saved to ~/.game_rater/config.json), or set the TGDB_API_KEY environment
variable. Get a free key at https://thegamesdb.net/.

Usage:
    python scrape_metadata.py --roms-dir "F:\\Roms"             # all platforms in that session
    python scrape_metadata.py --roms-dir "F:\\Roms" FC SFC      # only these platforms
    python scrape_metadata.py --session-id 3

Resumable: entries that already have rating+language are skipped, so you can
re-run this after it's interrupted (rate limits, network errors, etc).
"""

import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import config
import db

API_BASE = "https://api.thegamesdb.net/v1"
REQUEST_DELAY_SECONDS = 1.0  # be polite to the free-tier API

# Our roms folder name -> a name (or substring) of TheGamesDB's platform.
PLATFORM_NAME_HINTS = {
    "FC": "Nintendo Entertainment System (NES)",
    "SFC": "Super Nintendo (SNES)",
    "GBA": "Nintendo Game Boy Advance",
    "GB": "Nintendo Game Boy",
    "GBC": "Nintendo Game Boy Color",
    "PS": "Sony Playstation",
    "NEOGEO": "Neo Geo",
    "NEOCD": "Neo Geo CD",
    "FDS": "Nintendo Famicom Disk System",
    "CPS1": "Arcade",
    "CPS2": "Arcade",
    "CPS3": "Arcade",
}

# Region/language tags commonly found in No-Intro / GoodTools style ROM
# filenames, e.g. "Donkey Kong Country (U) (V1.1).zip" or
# "Some Game (Europe) (En,Fr,De).zip".
LANGUAGE_TAG_MAP = {
    "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "it": "Italian", "nl": "Dutch", "pt": "Portuguese", "sv": "Swedish",
    "no": "Norwegian", "da": "Danish", "fi": "Finnish", "zh": "Chinese",
    "ja": "Japanese", "ko": "Korean", "pl": "Polish", "ru": "Russian",
}

REGION_TAG_MAP = {
    "u": "English", "usa": "English", "e": "English", "europe": "English",
    "w": "English", "world": "English",
    "j": "Japanese", "japan": "Japanese",
    "g": "German", "germany": "German",
    "f": "French", "france": "French",
    "s": "Spanish", "spain": "Spanish",
    "i": "Italian", "italy": "Italian",
    "k": "Korean", "korea": "Korean",
    "c": "Chinese", "china": "Chinese",
    "nl": "Dutch", "holland": "Dutch",
    "sw": "Swedish", "sweden": "Swedish",
}

PAREN_TAG_RE = re.compile(r"\(([^)]+)\)")


def parse_language(filename: str) -> str:
    """Best-effort language guess from filename region/language tags."""
    tags = PAREN_TAG_RE.findall(filename)
    for tag in tags:
        parts = [p.strip().lower() for p in tag.split(",")]
        langs = [LANGUAGE_TAG_MAP[p] for p in parts if p in LANGUAGE_TAG_MAP]
        if langs:
            return ", ".join(dict.fromkeys(langs))
    for tag in tags:
        key = tag.strip().lower()
        if key in REGION_TAG_MAP:
            return REGION_TAG_MAP[key]
    return "unknown"


def clean_search_name(name: str) -> str:
    """Strip parenthetical/bracketed tags to get a plain search query."""
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"\[[^\]]*\]", "", name)
    return " ".join(name.split()).strip()


def api_get(path: str, api_key: str, params: dict) -> dict:
    query = dict(params)
    query["apikey"] = api_key
    url = f"{API_BASE}/{path}?{urllib.parse.urlencode(query)}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    config.record_api_call()
    return result


_platform_id_cache: dict | None = None


def load_platform_ids(api_key: str) -> dict:
    global _platform_id_cache
    if _platform_id_cache is not None:
        return _platform_id_cache

    data = api_get("Platforms", api_key, {})
    platforms = data["data"]["platforms"]  # id -> {name, ...}

    name_to_id = {info["name"]: pid for pid, info in platforms.items()}
    resolved = {}
    for folder, hint in PLATFORM_NAME_HINTS.items():
        match_id = None
        if hint in name_to_id:
            match_id = name_to_id[hint]
        else:
            for name, pid in name_to_id.items():
                if hint.lower() in name.lower():
                    match_id = pid
                    break
        if match_id is None:
            print(f"WARNING: no TheGamesDB platform match for '{folder}' (hint: {hint})")
        resolved[folder] = match_id

    _platform_id_cache = resolved
    return resolved


def lookup_rating(api_key: str, name: str, platform_id) -> float | None:
    params = {"name": name}
    if platform_id is not None:
        params["filter[platform]"] = platform_id
    try:
        data = api_get("Games/ByGameName", api_key, params)
    except Exception as e:
        print(f"  API error for '{name}': {e}")
        return None

    games = data.get("data", {}).get("games", [])
    if not games:
        return None

    rating = games[0].get("rating")
    try:
        return float(rating) if rating not in (None, "") else None
    except (TypeError, ValueError):
        return None


def scrape_platform(session_id: int, platform: str, api_key: str, platform_ids: dict) -> None:
    games = db.get_games_needing_scrape(session_id, platform)
    platform_id = platform_ids.get(platform)

    for game in games:
        search_name = clean_search_name(game["name"])
        rating = lookup_rating(api_key, search_name, platform_id)
        language = parse_language(game["name"])
        db.update_metadata(game["id"], rating, language)
        print(f"  [{platform}] {game['name']!r} -> rating={rating}, language={language}")
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"{platform}: done ({len(games)} entries scraped)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill in rating and language for ROM games in the database using TheGamesDB.")
    parser.add_argument("--roms-dir", help=r'Roms folder identifying the session, e.g. "F:\Roms"')
    parser.add_argument("--session-id", type=int, help="Session id (alternative to --roms-dir)")
    parser.add_argument("platforms", nargs="*", help="Platform names to process (default: all platforms in the session)")
    args = parser.parse_args()

    if not args.roms_dir and not args.session_id:
        raise SystemExit("Provide either --roms-dir or --session-id")

    api_key = os.environ.get("TGDB_API_KEY") or config.get_api_key()
    if not api_key:
        raise SystemExit(
            "Missing TheGamesDB API key. Either set the TGDB_API_KEY environment "
            "variable, or set one persistently via the desktop app's Settings menu "
            "(saved to ~/.game_rater/config.json). Get a free key at "
            "https://thegamesdb.net/."
        )

    if args.session_id:
        session = db.get_session(args.session_id)
        if not session:
            raise SystemExit(f"No session with id {args.session_id}")
        session_id = session["id"]
    else:
        session_id = db.get_or_create_session(str(Path(args.roms_dir).resolve()))

    platforms = args.platforms or db.get_platforms(session_id)
    platform_ids = load_platform_ids(api_key)

    for platform in platforms:
        scrape_platform(session_id, platform, api_key, platform_ids)


if __name__ == "__main__":
    main()
