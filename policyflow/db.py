"""SQLite database layer — records every routing decision for cost analysis."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DB_PATH = Path("policyflow.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    user TEXT DEFAULT 'default',
    original_model TEXT NOT NULL,
    routed_model TEXT NOT NULL,
    policy_name TEXT,
    method TEXT,
    similarity_score REAL,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    estimated_cost REAL DEFAULT 0.0,
    compared_cost REAL DEFAULT 0.0,
    cascade_attempts INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    success INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_requests_timestamp ON requests(timestamp);
CREATE INDEX IF NOT EXISTS idx_requests_user ON requests(user);
CREATE INDEX IF NOT EXISTS idx_requests_policy ON requests(policy_name);
"""


def get_db() -> sqlite3.Connection:
    """Get a database connection (not thread-safe — fine for single-worker uvicorn)."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create tables and indexes if they don't exist."""
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def log_request(
    user: str,
    original_model: str,
    routed_model: str,
    policy_name: str,
    method: str,
    similarity_score: float,
    prompt_tokens: int,
    completion_tokens: int,
    estimated_cost: float,
    compared_cost: float,
    cascade_attempts: int,
    duration_ms: int,
    success: bool,
) -> None:
    """Insert a request log entry."""
    conn = get_db()
    conn.execute(
        """INSERT INTO requests
           (timestamp, user, original_model, routed_model, policy_name,
            method, similarity_score, prompt_tokens, completion_tokens,
            total_tokens, estimated_cost, compared_cost,
            cascade_attempts, duration_ms, success)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            time.strftime("%Y-%m-%d %H:%M:%S"),
            user,
            original_model,
            routed_model,
            policy_name,
            method,
            similarity_score,
            prompt_tokens,
            completion_tokens,
            prompt_tokens + completion_tokens,
            estimated_cost,
            compared_cost,
            cascade_attempts,
            duration_ms,
            1 if success else 0,
        ),
    )
    conn.commit()
    conn.close()


# ── Query helpers for Dashboard API ──────────────────────────────

def query_summary(days: int = 30) -> dict:
    """Get summary stats for the last N days."""
    conn = get_db()
    row = conn.execute(
        """SELECT
             COUNT(*) as total_requests,
             COALESCE(SUM(estimated_cost), 0) as total_cost,
             COALESCE(SUM(compared_cost), 0) as compared_cost
           FROM requests
           WHERE timestamp >= date('now', ? || ' days')""",
        (f"-{days}",),
    ).fetchone()
    conn.close()
    saved = row["compared_cost"] - row["total_cost"]
    saved_pct = (saved / row["compared_cost"] * 100) if row["compared_cost"] > 0 else 0.0
    return {
        "total_requests": row["total_requests"],
        "total_cost": round(row["total_cost"], 4),
        "compared_cost": round(row["compared_cost"], 4),
        "saved_amount": round(saved, 4),
        "saved_pct": round(saved_pct, 1),
    }


def query_daily_costs(days: int = 30) -> list[dict]:
    """Daily cost breakdown."""
    conn = get_db()
    rows = conn.execute(
        """SELECT
             date(timestamp) as day,
             COUNT(*) as requests,
             COALESCE(SUM(estimated_cost), 0) as actual_cost,
             COALESCE(SUM(compared_cost), 0) as compared_cost
           FROM requests
           WHERE timestamp >= date('now', ? || ' days')
           GROUP BY day ORDER BY day""",
        (f"-{days}",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_policy_breakdown(days: int = 30) -> list[dict]:
    """Cost breakdown by policy."""
    conn = get_db()
    rows = conn.execute(
        """SELECT
             COALESCE(policy_name, 'unknown') as policy,
             COUNT(*) as requests,
             COALESCE(SUM(estimated_cost), 0) as cost,
             COALESCE(SUM(compared_cost), 0) as compared_cost
           FROM requests
           WHERE timestamp >= date('now', ? || ' days')
           GROUP BY policy_name ORDER BY cost DESC""",
        (f"-{days}",),
    ).fetchall()
    conn.close()
    results = []
    total_cost = sum(r["cost"] for r in rows)
    for r in rows:
        saved = r["compared_cost"] - r["cost"]
        results.append({
            "policy": r["policy"],
            "requests": r["requests"],
            "cost": round(r["cost"], 4),
            "saved": round(saved, 4),
            "pct": round(r["cost"] / total_cost * 100, 1) if total_cost > 0 else 0,
        })
    return results


def query_cascade_stats(days: int = 30) -> dict:
    """Cascade/fallback statistics."""
    conn = get_db()
    totals = conn.execute(
        """SELECT
             COUNT(*) as total,
             SUM(CASE WHEN cascade_attempts > 0 THEN 1 ELSE 0 END) as cascaded,
             SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failed
           FROM requests
           WHERE timestamp >= date('now', ? || ' days')""",
        (f"-{days}",),
    ).fetchone()
    conn.close()
    total = totals["total"] or 1
    cascaded = totals["cascaded"] or 0
    return {
        "total_requests": total,
        "cascade_attempts": cascaded,
        "direct_success": total - cascaded - (totals["failed"] or 0),
        "direct_pct": round((total - cascaded - (totals["failed"] or 0)) / total * 100, 1),
        "cascade_pct": round(cascaded / total * 100, 1),
        "failed": totals["failed"] or 0,
    }


def query_recent_requests(limit: int = 50) -> list[dict]:
    """Get the most recent requests."""
    conn = get_db()
    rows = conn.execute(
        """SELECT timestamp, user, original_model, routed_model,
                  policy_name, method, estimated_cost, cascade_attempts, success
           FROM requests ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
