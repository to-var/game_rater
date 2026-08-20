"""
Fill in "rating" and "language" for games stored in the DB (see db.py).

Metadata source is decided per-game by filename shape, not by which
platform folder the file happens to live in (folders can mix romset types,
e.g. NEOGEO holding both real MAME arcade sets and Neo Geo Pocket homebrew):
  - Filenames that look like a MAME romset short-name ("ffight", no spaces
    or parens) are tried against ArcadeItalia's free, keyless MAME database
    (offline copy first, see data/arcade_names.json / build_offline_arcade_db.py),
    which returns name + rating + language in one call.
  - Anything else -- or a MAME lookup with no match -- falls back to
    RAWG.io by cleaned game name for a numeric rating; language is derived
    from filename region/language tags (No-Intro/GoodTools style, e.g.
    "(U)", "(En,Fr,De)") since RAWG has no per-release language field.

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

# Local, offline MAME short-name -> {title, rating, language} database, built
# once via build_offline_arcade_db.py. When present, arcade scraping reads
# from here and makes zero network calls.
ARCADE_DB_PATH = Path(__file__).parent / "data" / "arcade_names.json"

_offline_arcade_db: dict | None = None

# MAME romset short-names are lowercase alnum (plus underscore), no spaces
# or parenthetical tags, e.g. "ffight", "sfiii3". Folders like NEOGEO mix
# genuine MAME arcade sets with descriptively-named console ROMs (e.g. Neo
# Geo Pocket homebrew: "Baseball Stars Color (JUE) [!].zip") -- those aren't
# MAME sets and must be routed to the console (RAWG) lookup instead.
MAME_SHORTNAME_RE = re.compile(r"^[a-z0-9_]+$")


def looks_like_mame_shortname(name: str) -> bool:
    return bool(MAME_SHORTNAME_RE.match(name))


def load_offline_arcade_db() -> dict:
    global _offline_arcade_db
    if _offline_arcade_db is None:
        if ARCADE_DB_PATH.exists():
            with open(ARCADE_DB_PATH, "r", encoding="utf-8") as f:
                _offline_arcade_db = json.load(f)
        else:
            _offline_arcade_db = {}
    return _offline_arcade_db

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


def scrape_console_game(game, api_key: str | None, log=print) -> None:
    search_name = clean_search_name(game["name"])
    rating = lookup_rawg_rating(api_key, search_name, log=log) if api_key else None
    language = parse_language(game["name"])
    db.update_metadata(game["id"], rating, language)
    log(f"  {game['name']!r} -> rating={rating}, language={language} (RAWG)")
    if api_key:
        time.sleep(REQUEST_DELAY_SECONDS)


def scrape_game(game, api_key: str | None, log=print) -> None:
    """Decides how to resolve one game purely from its filename shape, not
    which platform folder it's in: a MAME-shortname-looking file (e.g.
    'ffight') is tried against the arcade MAME db first; anything else, or a
    MAME lookup that finds no match, falls back to the console (RAWG) path."""
    short_name = Path(game["file_path"]).stem

    if looks_like_mame_shortname(short_name):
        offline_db = load_offline_arcade_db()
        result = offline_db.get(short_name)
        source = "offline"
        if result is None:
            result = lookup_arcade_metadata(short_name, log=log)
            source = "network"
            time.sleep(REQUEST_DELAY_SECONDS)

        if result and result.get("title"):
            db.update_game(game["id"], name=result["title"], rating=result["rating"], language=result["language"])
            log(f"  {short_name!r} -> {result['title']!r} rating={result['rating']} language={result['language']} (arcade/{source})")
            return
        log(f"  {short_name!r} -> no arcade match ({source}), falling back to RAWG")

    scrape_console_game(game, api_key, log=log)


def scrape_platform(session_id: int, platform: str, api_key: str | None, log=print) -> None:
    games = db.get_games_needing_scrape(session_id, platform)
    log(f"{platform}: {len(games)} entries need scraping")
    for game in games:
        scrape_game(game, api_key, log=log)
    log(f"{platform}: done ({len(games)} entries scraped)")


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

    api_key = os.environ.get("RAWG_API_KEY") or config.get_api_key()
    if not api_key:
        print(
            "No RAWG API key set (RAWG_API_KEY env var, or via the desktop app's "
            "Settings menu). Arcade/MAME romsets will still be resolved via the "
            "offline db and ArcadeItalia; everything else will get language only, "
            "no rating. Get a free key at https://rawg.io/apidocs."
        )

    for platform in platforms:
        scrape_platform(session_id, platform, api_key)


if __name__ == "__main__":
    main()
