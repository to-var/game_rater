"""
Fill in "rating" and "language" for games stored in the DB (see db.py).

Metadata source depends on platform:
  - Arcade (CPS1/2/3, Neo Geo, Neo Geo CD): MAME romsets are named by
    cryptic short codes ("ffight.zip") instead of descriptive filenames.
    Resolved via ArcadeItalia's free, keyless MAME database, which also
    happens to return name + rating + language in one call.
  - Everything else (consoles): looked up on RAWG.io by cleaned game name
    for a numeric rating; language is derived from filename region/language
    tags (No-Intro/GoodTools style, e.g. "(U)", "(En,Fr,De)") since neither
    RAWG nor TheGamesDB expose a real per-release language field.

Setup: set your RAWG API key persistently via the desktop app's Settings
menu (saved to ~/.game_rater/config.json), or set the RAWG_API_KEY
environment variable. Get a free key at https://rawg.io/apidocs.

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

REQUEST_DELAY_SECONDS = 1.0  # be polite to free-tier APIs

ARCADE_PLATFORMS = {"CPS1", "CPS2", "CPS3", "NEOGEO", "NEOCD"}
ARCADE_ITALIA_URL = "https://adb.arcadeitalia.net/service_scraper.php"

RAWG_API_BASE = "https://api.rawg.io/api"

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


def lookup_rawg_rating(api_key: str, name: str, log=print) -> float | None:
    """RAWG's user rating is 0-5; scaled to 0-10 to match the arcade rating scale."""
    params = {"search": name, "page_size": 1, "key": api_key}
    url = f"{RAWG_API_BASE}/games?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"  RAWG error for '{name}': {e}")
        return None
    config.record_api_call()

    results = data.get("results") or []
    if not results:
        return None

    rating = results[0].get("rating")
    try:
        return round(float(rating) * 2, 1) if rating not in (None, "") else None
    except (TypeError, ValueError):
        return None


def lookup_arcade_metadata(short_name: str, log=print) -> dict | None:
    """Look up a MAME romset short-name (e.g. 'ffight') against ArcadeItalia's
    free public MAME database. Returns {"title", "rating", "language"} or
    None if there's no match."""
    url = f"{ARCADE_ITALIA_URL}?{urllib.parse.urlencode({'ajax': 'query_mame', 'game_name': short_name})}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"  ArcadeItalia error for '{short_name}': {e}")
        return None

    results = data.get("result") or []
    if not results:
        return None

    match = results[0]
    rate = match.get("rate")
    try:
        rating = round(float(rate) / 10, 1) if rate not in (None, "") else None
    except (TypeError, ValueError):
        rating = None

    return {
        "title": match.get("title") or short_name,
        "rating": rating,
        "language": match.get("languages") or None,
    }


def scrape_arcade_platform(session_id: int, platform: str, log=print) -> None:
    games = db.get_games_needing_scrape(session_id, platform)

    log(f"{platform}: {len(games)} entries need scraping (ArcadeItalia MAME db)")
    for game in games:
        short_name = Path(game["file_path"]).stem
        result = lookup_arcade_metadata(short_name, log=log)
        if result:
            db.update_game(game["id"], name=result["title"], rating=result["rating"], language=result["language"])
            log(f"  [{platform}] {short_name!r} -> {result['title']!r} rating={result['rating']} language={result['language']}")
        else:
            db.update_metadata(game["id"], None, parse_language(game["name"]))
            log(f"  [{platform}] {short_name!r} -> no match")
        time.sleep(REQUEST_DELAY_SECONDS)

    log(f"{platform}: done ({len(games)} entries scraped)")


def scrape_console_platform(session_id: int, platform: str, api_key: str, log=print) -> None:
    games = db.get_games_needing_scrape(session_id, platform)

    log(f"{platform}: {len(games)} entries need scraping (RAWG)")
    for game in games:
        search_name = clean_search_name(game["name"])
        rating = lookup_rawg_rating(api_key, search_name, log=log)
        language = parse_language(game["name"])
        db.update_metadata(game["id"], rating, language)
        log(f"  [{platform}] {game['name']!r} -> rating={rating}, language={language}")
        time.sleep(REQUEST_DELAY_SECONDS)

    log(f"{platform}: done ({len(games)} entries scraped)")


def scrape_platform(session_id: int, platform: str, api_key: str | None, log=print) -> None:
    if platform in ARCADE_PLATFORMS:
        scrape_arcade_platform(session_id, platform, log=log)
    else:
        scrape_console_platform(session_id, platform, api_key, log=log)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill in rating and language for ROM games in the database.")
    parser.add_argument("--roms-dir", help=r'Roms folder identifying the session, e.g. "F:\Roms"')
    parser.add_argument("--session-id", type=int, help="Session id (alternative to --roms-dir)")
    parser.add_argument("platforms", nargs="*", help="Platform names to process (default: all platforms in the session)")
    args = parser.parse_args()

    if not args.roms_dir and not args.session_id:
        raise SystemExit("Provide either --roms-dir or --session-id")

    if args.session_id:
        session = db.get_session(args.session_id)
        if not session:
            raise SystemExit(f"No session with id {args.session_id}")
        session_id = session["id"]
    else:
        session_id = db.get_or_create_session(str(Path(args.roms_dir).resolve()))

    platforms = args.platforms or db.get_platforms(session_id)
    needs_rawg = any(p not in ARCADE_PLATFORMS for p in platforms)

    api_key = os.environ.get("RAWG_API_KEY") or config.get_api_key()
    if needs_rawg and not api_key:
        raise SystemExit(
            "Missing RAWG API key. Either set the RAWG_API_KEY environment "
            "variable, or set one persistently via the desktop app's Settings menu "
            "(saved to ~/.game_rater/config.json). Get a free key at "
            "https://rawg.io/apidocs."
        )

    for platform in platforms:
        scrape_platform(session_id, platform, api_key)


if __name__ == "__main__":
    main()
