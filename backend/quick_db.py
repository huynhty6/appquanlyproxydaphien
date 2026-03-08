"""
Quick API – SQLite database helper.

Tables: quick_api_keys, mikrotik_credentials, quick_request_log.
WAL mode cho đọc/ghi đồng thời tốt trên VPS.
"""

import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "quick_api.db")

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Thread-local connection (SQLite ko share connection giữa threads)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_quick_db():
    """Tạo tables nếu chưa có. Gọi 1 lần khi app startup."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS quick_api_keys (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            key             TEXT NOT NULL UNIQUE,
            name            TEXT NOT NULL DEFAULT '',
            user_id         TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'active',
            used_requests   INTEGER NOT NULL DEFAULT 0,
            limit_requests  INTEGER NOT NULL DEFAULT 100000,
            created_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS mikrotik_credentials (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL UNIQUE,
            host        TEXT NOT NULL,
            username    TEXT NOT NULL,
            password    TEXT NOT NULL,
            label       TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS quick_request_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key_id      INTEGER NOT NULL,
            action      TEXT NOT NULL,
            interface   TEXT DEFAULT '',
            result      TEXT DEFAULT '',
            created_at  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_api_keys_key ON quick_api_keys(key);
        CREATE INDEX IF NOT EXISTS idx_api_keys_user ON quick_api_keys(user_id);
        CREATE INDEX IF NOT EXISTS idx_creds_user ON mikrotik_credentials(user_id);
        CREATE INDEX IF NOT EXISTS idx_log_key_time ON quick_request_log(key_id, created_at);
    """)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── API Keys ────────────────────────────────────────────────


def create_api_key(user_id: str, name: str = "", limit: int = 100000) -> dict:
    """Tạo key htpx_ + 32 ký tự random."""
    key = "htpx_" + secrets.token_urlsafe(24)
    now = _now()
    conn = _get_conn()
    conn.execute(
        "INSERT INTO quick_api_keys (key, name, user_id, limit_requests, created_at) VALUES (?, ?, ?, ?, ?)",
        (key, name, user_id, limit, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM quick_api_keys WHERE key = ?", (key,)).fetchone()
    return dict(row)


def list_api_keys(user_id: str) -> list[dict]:
    """Danh sách keys của user."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM quick_api_keys WHERE user_id = ? ORDER BY id DESC", (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def toggle_api_key(key_id: int, user_id: str) -> dict | None:
    """Toggle active ↔ paused. Trả về row mới hoặc None nếu không tìm thấy."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM quick_api_keys WHERE id = ? AND user_id = ?", (key_id, user_id)
    ).fetchone()
    if not row:
        return None
    new_status = "paused" if row["status"] == "active" else "active"
    conn.execute(
        "UPDATE quick_api_keys SET status = ? WHERE id = ?", (new_status, key_id)
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM quick_api_keys WHERE id = ?", (key_id,)).fetchone())


def delete_api_key(key_id: int, user_id: str) -> bool:
    """Xóa key. Trả True nếu xóa thành công."""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM quick_api_keys WHERE id = ? AND user_id = ?", (key_id, user_id)
    )
    conn.commit()
    return cur.rowcount > 0


def increment_usage(key_id: int):
    """Tăng used_requests + 1."""
    conn = _get_conn()
    conn.execute(
        "UPDATE quick_api_keys SET used_requests = used_requests + 1 WHERE id = ?",
        (key_id,),
    )
    conn.commit()


# ─── MikroTik Credentials ───────────────────────────────────


def save_mikrotik_creds(user_id: str, host: str, username: str, password: str, label: str = "") -> dict:
    """INSERT OR REPLACE – mỗi user chỉ 1 router."""
    now = _now()
    conn = _get_conn()
    existing = conn.execute(
        "SELECT created_at FROM mikrotik_credentials WHERE user_id = ?", (user_id,)
    ).fetchone()
    created = existing["created_at"] if existing else now

    conn.execute(
        """INSERT OR REPLACE INTO mikrotik_credentials
           (user_id, host, username, password, label, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, host, username, password, label, created, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM mikrotik_credentials WHERE user_id = ?", (user_id,)
    ).fetchone()
    return dict(row)


# ─── Key Validation + Creds (1 query) ───────────────────────


def get_mikrotik_creds_by_key(key: str) -> dict | None:
    """
    JOIN 1 query: validate key + lấy MikroTik creds.
    Trả dict với fields: key_id, key_status, used_requests, limit_requests,
                          host, username, password, user_id.
    None nếu key không tồn tại hoặc user chưa đăng ký MikroTik.
    """
    conn = _get_conn()
    row = conn.execute(
        """SELECT
                k.id         AS key_id,
                k.status     AS key_status,
                k.used_requests,
                k.limit_requests,
                k.user_id,
                m.host,
                m.username,
                m.password
           FROM quick_api_keys k
           JOIN mikrotik_credentials m ON k.user_id = m.user_id
           WHERE k.key = ?""",
        (key,),
    ).fetchone()
    return dict(row) if row else None


# ─── Rate Limit + Logging ───────────────────────────────────


def check_rate_limit(key_id: int, max_per_minute: int = 10) -> bool:
    """True nếu còn trong giới hạn, False nếu vượt."""
    conn = _get_conn()
    row = conn.execute(
        """SELECT COUNT(*) AS cnt FROM quick_request_log
           WHERE key_id = ? AND created_at > datetime('now', '-60 seconds')""",
        (key_id,),
    ).fetchone()
    return row["cnt"] < max_per_minute


def log_request(key_id: int, action: str, interface: str = "", result: str = ""):
    """Ghi log request cho rate limit + analytics."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO quick_request_log (key_id, action, interface, result, created_at) VALUES (?, ?, ?, ?, ?)",
        (key_id, action, interface, result, _now()),
    )
    conn.commit()
