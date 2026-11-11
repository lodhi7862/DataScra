"""Database connections, schema, and the small key/value caches.

PostgreSQL is used when DATABASE_URL is set (Neon and Render). SQLite is the
local fallback so the app still runs with no hosted database.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from app.models import DEFAULT_SETTINGS, GEOCODE_TTL_DAYS, SEARCH_TTL_HOURS, now_iso

BASE_DIR = Path(__file__).resolve().parent.parent

class IntegrityError(Exception):
    """A unique constraint failed. Raised for both SQLite and PostgreSQL."""


_LEAD_COLUMNS = """
    name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    phone_normalized TEXT NOT NULL DEFAULT '',
    whatsapp TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    facebook TEXT NOT NULL DEFAULT '',
    instagram TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    area TEXT NOT NULL DEFAULT '',
    lat REAL,
    lng REAL,
    rating REAL,
    source TEXT NOT NULL DEFAULT 'Manual',
    status TEXT NOT NULL DEFAULT 'New',
    notes TEXT NOT NULL DEFAULT '',
    follow_up_date TEXT NOT NULL DEFAULT '',
    osm_type TEXT NOT NULL DEFAULT '',
    osm_id TEXT NOT NULL DEFAULT '',
    opening_hours TEXT NOT NULL DEFAULT '',
    listing_url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
"""

_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_osm ON leads(osm_type, osm_id) WHERE osm_id != ''",
    "CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone_normalized)",
    "CREATE INDEX IF NOT EXISTS idx_leads_whatsapp ON leads(whatsapp)",
    "CREATE INDEX IF NOT EXISTS idx_leads_city ON leads(city)",
    "CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)",
    "CREATE INDEX IF NOT EXISTS idx_leads_follow ON leads(follow_up_date)",
    """CREATE TABLE IF NOT EXISTS geocode_cache (
        query TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS search_cache (
        cache_key TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS import_batches (
        token TEXT PRIMARY KEY,
        filename TEXT NOT NULL DEFAULT '',
        rows_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS listing_cache (
        cache_key TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""",
)

_ready = False


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def using_postgres() -> bool:
    return bool(database_url())


def database_label() -> str:
    if using_postgres():
        return "PostgreSQL"
    return f"SQLite {database_path().name}"


def _postgres_dsn() -> str:
    url = database_url()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if "sslmode=" not in url:
        url += "&" if "?" in url else "?"
        url += "sslmode=require"
    return url


def _adapt_sql(sql: str) -> str:
    """Turn SQLite '?' placeholders into psycopg2 '%s' placeholders."""
    if not using_postgres():
        return sql
    out: list[str] = []
    quoted = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "'":
            if quoted and index + 1 < len(sql) and sql[index + 1] == "'":
                out.append("''")
                index += 2
                continue
            quoted = not quoted
            out.append(char)
        elif char == "?" and not quoted:
            out.append("%s")
        elif char == "%" and not quoted:
            out.append("%%")
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _is_integrity(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    name = type(exc).__module__ + "." + type(exc).__name__
    return name.startswith("psycopg2") and "IntegrityError" in name


class DBCursor:
    def __init__(self, cursor: object, lastrowid: int | None = None):
        self._cursor = cursor
        self.lastrowid = lastrowid if lastrowid is not None else getattr(cursor, "lastrowid", None)
        self.rowcount = getattr(cursor, "rowcount", -1)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)


class DBConnection:
    """The small slice of the DB API this app uses, for either backend."""

    def __init__(self, raw: object, postgres: bool):
        self.raw = raw
        self.postgres = postgres
        self._savepoint = 0

    def execute(self, sql: str, params: tuple | list = ()) -> DBCursor:
        statement = _adapt_sql(sql)
        savepoint = ""
        if self.postgres:
            self._savepoint += 1
            savepoint = f"sp_{self._savepoint}"
            self.raw.cursor().execute(f"SAVEPOINT {savepoint}")
        try:
            if self.postgres:
                from psycopg2.extras import RealDictCursor

                cursor = self.raw.cursor(cursor_factory=RealDictCursor)
                cursor.execute(statement, tuple(params))
            else:
                cursor = self.raw.execute(statement, tuple(params))
        except Exception as exc:
            if _is_integrity(exc):
                if savepoint:
                    self.raw.cursor().execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                raise IntegrityError(str(exc)) from exc
            raise
        if savepoint:
            self.raw.cursor().execute(f"RELEASE SAVEPOINT {savepoint}")
        return DBCursor(cursor)

    def executemany(self, sql: str, rows: list) -> None:
        statement = _adapt_sql(sql)
        if self.postgres:
            cursor = self.raw.cursor()
            cursor.executemany(statement, rows)
            return
        self.raw.executemany(statement, rows)

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()


def _lead_table_sql() -> str:
    if using_postgres():
        identity = "id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
    else:
        identity = "id INTEGER PRIMARY KEY AUTOINCREMENT"
    return f"CREATE TABLE IF NOT EXISTS leads (\n    {identity},{_LEAD_COLUMNS}\n)"


def _has_listing_url(conn: DBConnection) -> bool:
    if conn.postgres:
        row = conn.execute(
            "SELECT 1 AS ok FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?",
            ("leads", "listing_url"),
        ).fetchone()
        return row is not None
    rows = conn.execute("PRAGMA table_info(leads)").fetchall()
    return any(row[1] == "listing_url" for row in rows)


def database_path() -> Path:
    override = os.environ.get("JEWELLEADS_DB", "").strip()
    if override:
        return Path(override)
    return BASE_DIR / "leads.db"


def connect() -> DBConnection:
    if using_postgres():
        import psycopg2

        raw = psycopg2.connect(_postgres_dsn(), connect_timeout=15, application_name="jewelleads")
        return DBConnection(raw, postgres=True)
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(path, timeout=30)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("PRAGMA journal_mode = WAL")
    raw.execute("PRAGMA busy_timeout = 5000")
    return DBConnection(raw, postgres=False)


def init_db() -> None:
    """Create tables and default settings. Safe to call more than once."""
    global _ready
    conn = connect()
    try:
        conn.execute(_lead_table_sql())
        for statement in _INDEXES:
            conn.execute(statement)
        if not _has_listing_url(conn):
            conn.execute("ALTER TABLE leads ADD COLUMN listing_url TEXT NOT NULL DEFAULT ''")
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                (key, value),
            )
        _purge(conn)
        conn.commit()
    finally:
        conn.close()
    _ready = True


@contextmanager
def session():
    """One short transaction. The connection is always closed."""
    ensure_db()
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_db() -> None:
    if not _ready:
        init_db()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def _purge(conn: sqlite3.Connection) -> None:
    now = now_iso()
    conn.execute("DELETE FROM search_cache WHERE expires_at <= ?", (now,))
    conn.execute("DELETE FROM listing_cache WHERE expires_at <= ?", (now,))
    cutoff = (datetime.now().astimezone() - timedelta(days=2)).isoformat()
    conn.execute("DELETE FROM import_batches WHERE created_at <= ?", (cutoff,))
    geo_cutoff = datetime.now().astimezone() - timedelta(days=GEOCODE_TTL_DAYS)
    rows = conn.execute("SELECT query, created_at FROM geocode_cache").fetchall()
    stale = [row["query"] for row in rows if _parse_iso(row["created_at"]) <= geo_cutoff]
    if stale:
        conn.executemany("DELETE FROM geocode_cache WHERE query = ?", [(q,) for q in stale])


def load_settings() -> dict[str, str]:
    ensure_db()
    with session() as conn:
        found = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(found)
    return merged


def save_settings(values: dict[str, str]) -> dict[str, str]:
    ensure_db()
    with session() as conn:
        for key, value in values.items():
            if key not in DEFAULT_SETTINGS:
                continue
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        conn.commit()
    return load_settings()


def country_code() -> str:
    return load_settings().get("country_code") or "92"


def cache_get_geocode(query: str) -> dict | None:
    ensure_db()
    with session() as conn:
        row = conn.execute(
            "SELECT payload, created_at FROM geocode_cache WHERE query = ?",
            (query,),
        ).fetchone()
        cached = None if row is None else (row["created_at"], row["payload"])
    if cached is None:
        return None
    age = datetime.now().astimezone() - _parse_iso(cached[0])
    if age > timedelta(days=GEOCODE_TTL_DAYS):
        return None
    return json.loads(cached[1])


def cache_set_geocode(query: str, payload: dict) -> None:
    ensure_db()
    with session() as conn:
        conn.execute(
            "INSERT INTO geocode_cache(query, payload, created_at) VALUES(?, ?, ?) "
            "ON CONFLICT(query) DO UPDATE SET payload = excluded.payload, "
            "created_at = excluded.created_at",
            (query, json.dumps(payload), now_iso()),
        )
        conn.commit()


def cache_get_search(cache_key: str) -> dict | None:
    ensure_db()
    now = now_iso()
    with session() as conn:
        row = conn.execute(
            "SELECT payload FROM search_cache WHERE cache_key = ? AND expires_at > ?",
            (cache_key, now),
        ).fetchone()
        payload = None if row is None else row["payload"]
    if payload is None:
        return None
    return json.loads(payload)


def cache_get_listing(cache_key: str) -> dict | None:
    ensure_db()
    now = now_iso()
    with session() as conn:
        row = conn.execute(
            "SELECT payload FROM listing_cache WHERE cache_key = ? AND expires_at > ?",
            (cache_key, now),
        ).fetchone()
        payload = None if row is None else row["payload"]
    if payload is None:
        return None
    return json.loads(payload)


def cache_set_listing(cache_key: str, payload: dict, *, days: int = 7) -> None:
    ensure_db()
    created = datetime.now().astimezone()
    expires = (created + timedelta(days=days)).replace(microsecond=0).isoformat()
    with session() as conn:
        conn.execute(
            "INSERT INTO listing_cache(cache_key, payload, created_at, expires_at) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET payload = excluded.payload, "
            "created_at = excluded.created_at, expires_at = excluded.expires_at",
            (cache_key, json.dumps(payload), created.replace(microsecond=0).isoformat(), expires),
        )
        conn.commit()


def cache_set_search(cache_key: str, payload: dict) -> None:
    ensure_db()
    created = datetime.now().astimezone()
    expires = (created + timedelta(hours=SEARCH_TTL_HOURS)).replace(microsecond=0).isoformat()
    with session() as conn:
        conn.execute(
            "INSERT INTO search_cache(cache_key, payload, created_at, expires_at) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET payload = excluded.payload, "
            "created_at = excluded.created_at, expires_at = excluded.expires_at",
            (cache_key, json.dumps(payload), created.replace(microsecond=0).isoformat(), expires),
        )
        conn.commit()
