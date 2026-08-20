"""
Fill in "rating" and "language" for every entry in <metadata-dir>\\<PLATFORM>.json
using TheGamesDB (https://thegamesdb.net/) for rating, and filename
region/language tags for language (TheGamesDB has no per-release language
field, so this is the best available signal without a different API).

Setup:
    1. Create a free account at https://thegamesdb.net/
    2. Go to your account page and request/copy your API key.
    3. Set it as an environment variable before running:
         PowerShell:  $env:TGDB_API_KEY = "your-key-here"
         bash:        export TGDB_API_KEY="your-key-here"

Usage:
    python scrape_metadata.py --metadata-dir "F:\\metadata"             # all platforms
    python scrape_metadata.py --metadata-dir "F:\\metadata" FC SFC      # only these

Resumable: entries that already have a non-null "rating" are skipped, so you
can re-run this after it's interrupted (rate limits, network errors, etc).
Progress is saved after each platform finishes.
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
        # Multi-language tag, e.g. "En,Fr,De"
        parts = [p.strip().lower() for p in tag.split(",")]
        langs = [LANGUAGE_TAG_MAP[p] for p in parts if p in LANGUAGE_TAG_MAP]
        if langs:
            return ", ".join(dict.fromkeys(langs))  # dedupe, keep order
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


def load_platform_ids(api_key: str, metadata_dir: Path) -> dict:
    cache_path = metadata_dir / "_tgdb_platforms_cache.json"
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

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

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(resolved, f, indent=2)
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


def process_platform(platform: str, api_key: str, platform_ids: dict, metadata_dir: Path) -> None:
    json_path = metadata_dir / f"{platform}.json"
    if not json_path.exists():
        print(f"Skipping {platform}: {json_path} not found (run fetch_games.py first)")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        entries = json.load(f)

    platform_id = platform_ids.get(platform)
    changed = False

    for entry in entries:
        if entry.get("rating") is not None and entry.get("language") not in (None, ""):
            continue  # already filled in, skip (resumable)

        search_name = clean_search_name(entry["name"])
        rating = lookup_rating(api_key, search_name, platform_id)
        language = parse_language(entry["name"])

        entry["rating"] = rating
        entry["language"] = language
        changed = True

        print(f"  [{platform}] {entry['name']!r} -> rating={rating}, language={language}")
        time.sleep(REQUEST_DELAY_SECONDS)

    if changed:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    print(f"{platform}: done ({len(entries)} entries)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill in rating and language for ROM metadata JSON files using TheGamesDB.")
    parser.add_argument("--metadata-dir", required=True, help=r'Path to the metadata folder, e.g. "F:\metadata"')
    parser.add_argument("platforms", nargs="*", help="Platform names to process (default: all *.json in metadata dir)")
    args = parser.parse_args()

    api_key = os.environ.get("TGDB_API_KEY") or config.get_api_key()
    if not api_key:
        raise SystemExit(
            "Missing TheGamesDB API key. Either set the TGDB_API_KEY environment "
            "variable, or set one persistently via the desktop app's Settings menu "
            "(saved to ~/.game_rater/config.json). Get a free key at "
            "https://thegamesdb.net/."
        )

    metadata_dir = Path(args.metadata_dir)
    platforms = args.platforms or sorted(
        p.stem for p in metadata_dir.glob("*.json") if not p.stem.startswith("_")
    )

    platform_ids = load_platform_ids(api_key, metadata_dir)

    for platform in platforms:
        process_platform(platform, api_key, platform_ids, metadata_dir)


if __name__ == "__main__":
    main()
