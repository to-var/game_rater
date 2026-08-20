"""
SQLite storage for game_rater. Replaces the old per-platform JSON files.

A "session" is one scanned roms folder (unique by absolute path). Each game
row belongs to a session + platform. Re-scanning a folder reuses its
existing session (upsert: new files added, missing files dropped, existing
rows keep their rating/language/remove_flag).

DB lives at ~/.game_rater/game_rater.db (same config dir as config.py).
"""

import datetime
import sqlite3
from pathlib import Path

import config

DB_PATH = config.CONFIG_DIR / "game_rater.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roms_dir TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_scanned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    file_path TEXT NOT NULL,
    name TEXT NOT NULL,
    rating REAL,
    language TEXT,
    remove_flag INTEGER NOT NULL DEFAULT 0,
    UNIQUE(session_id, file_path)
);

CREATE INDEX IF NOT EXISTS idx_games_session_platform ON games(session_id, platform);
"""


def get_connection() -> sqlite3.Connection:
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def get_or_create_session(roms_dir: str) -> int:
    conn = get_connection()
    row = conn.execute("SELECT id FROM sessions WHERE roms_dir = ?", (roms_dir,)).fetchone()
    now = _now()
    if row:
        session_id = row["id"]
        conn.execute("UPDATE sessions SET last_scanned_at = ? WHERE id = ?", (now, session_id))
    else:
        cur = conn.execute(
            "INSERT INTO sessions (roms_dir, created_at, last_scanned_at) VALUES (?, ?, ?)",
            (roms_dir, now, now),
        )
        session_id = cur.lastrowid
    conn.commit()
    conn.close()
    return session_id


def list_sessions() -> list[sqlite3.Row]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT s.id, s.roms_dir, s.created_at, s.last_scanned_at, COUNT(g.id) AS game_count
        FROM sessions s
        LEFT JOIN games g ON g.session_id = s.id
        GROUP BY s.id
        ORDER BY s.last_scanned_at DESC
        """
    ).fetchall()
    conn.close()
    return rows


def get_session(session_id: int) -> sqlite3.Row | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    conn.close()
    return row


def get_latest_session() -> sqlite3.Row | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM sessions ORDER BY last_scanned_at DESC LIMIT 1").fetchone()
    conn.close()
    return row


def upsert_games(session_id: int, platform: str, entries: list[dict]) -> None:
    """entries: list of {"file_path": str, "name": str} freshly scanned from
    disk. Existing rows (matched by file_path) are left untouched (keeps
    rating/language/remove_flag). Rows for files no longer on disk are
    deleted."""
    conn = get_connection()
    existing_paths = {
        r["file_path"]
        for r in conn.execute(
            "SELECT file_path FROM games WHERE session_id = ? AND platform = ?",
            (session_id, platform),
        )
    }
    seen_paths = set()
    for entry in entries:
        seen_paths.add(entry["file_path"])
        if entry["file_path"] in existing_paths:
            continue
        conn.execute(
            "INSERT INTO games (session_id, platform, file_path, name, rating, language, remove_flag) "
            "VALUES (?, ?, ?, ?, NULL, NULL, 0)",
            (session_id, platform, entry["file_path"], entry["name"]),
        )

    stale_paths = existing_paths - seen_paths
    for file_path in stale_paths:
        conn.execute(
            "DELETE FROM games WHERE session_id = ? AND platform = ? AND file_path = ?",
            (session_id, platform, file_path),
        )
    conn.commit()
    conn.close()


def get_platforms(session_id: int) -> list[str]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT platform FROM games WHERE session_id = ? ORDER BY platform",
        (session_id,),
    ).fetchall()
    conn.close()
    return [r["platform"] for r in rows]


def get_games(session_id: int, platform: str) -> list[sqlite3.Row]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM games WHERE session_id = ? AND platform = ? ORDER BY name",
        (session_id, platform),
    ).fetchall()
    conn.close()
    return rows


def get_games_needing_scrape(session_id: int, platform: str) -> list[sqlite3.Row]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM games WHERE session_id = ? AND platform = ? "
        "AND (rating IS NULL OR language IS NULL OR language = '')",
        (session_id, platform),
    ).fetchall()
    conn.close()
    return rows


def set_remove_flag(game_id: int, remove: bool) -> None:
    conn = get_connection()
    conn.execute("UPDATE games SET remove_flag = ? WHERE id = ?", (1 if remove else 0, game_id))
    conn.commit()
    conn.close()


def update_metadata(game_id: int, rating: float | None, language: str | None) -> None:
    conn = get_connection()
    conn.execute("UPDATE games SET rating = ?, language = ? WHERE id = ?", (rating, language, game_id))
    conn.commit()
    conn.close()


def update_game(game_id: int, name: str | None = None, rating: float | None = None, language: str | None = None) -> None:
    """Like update_metadata, but also updates the display name (e.g. when
    resolving a cryptic MAME short-name like 'ffight' to 'Final Fight')."""
    conn = get_connection()
    conn.execute(
        "UPDATE games SET name = ?, rating = ?, language = ? WHERE id = ?",
        (name, rating, language, game_id),
    )
    conn.commit()
    conn.close()


def get_flagged_games(session_id: int | None = None) -> list[sqlite3.Row]:
    conn = get_connection()
    if session_id is not None:
        rows = conn.execute(
            "SELECT * FROM games WHERE session_id = ? AND remove_flag = 1", (session_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM games WHERE remove_flag = 1").fetchall()
    conn.close()
    return rows


def delete_game(game_id: int) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()


init_db()
