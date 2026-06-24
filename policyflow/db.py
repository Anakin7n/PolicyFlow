"""SQLite database layer — records every routing decision for cost analysis."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

# Database lives at the project root, resolved from this file's location —
# so the DB path is the same no matter which directory the server is
# launched from (db.py is at <root>/policyflow/db.py, so parent.parent = root).
DB_PATH = Path(__file__).resolve().parent.parent / "policyflow.db"

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

# Columns added after initial release (migrated in _migrate_schema)
MIGRATIONS = [
    "ALTER TABLE requests ADD COLUMN prompt_hash TEXT DEFAULT ''",
    "ALTER TABLE requests ADD COLUMN prompt_preview TEXT DEFAULT ''",
    "ALTER TABLE requests ADD COLUMN session_status TEXT DEFAULT ''",
    "ALTER TABLE requests DROP COLUMN judge_reason",
    "ALTER TABLE requests ADD COLUMN session_key TEXT DEFAULT ''",
]


def _migrate_schema() -> None:
    """Apply schema migrations. Each migration is attempted once;
    if the column already exists (ADD) or already removed (DROP), the
    OperationalError is ignored and the next migration runs."""
    conn = sqlite3.connect(str(DB_PATH))
    for sql in MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # idempotent — column state already matches the goal
    conn.commit()
    conn.close()


def hash_prompt(text: str) -> str:
    """Return a truncated SHA-256 hash of the prompt text.

    Used for clustering similar requests in the optimizer without
    storing the full prompt text.
    """
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def get_db() -> sqlite3.Connection:
    """Get a database connection (not thread-safe — fine for single-worker uvicorn)."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create tables, indexes, and apply migrations."""
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    _migrate_schema()


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
    prompt_hash: str = "",
    prompt_preview: str = "",
    session_status: str = "",
    session_key: str = "",
) -> None:
    """Insert a request log entry."""
    conn = get_db()
    conn.execute(
        """INSERT INTO requests
           (timestamp, user, original_model, routed_model, policy_name,
            method, similarity_score, prompt_tokens, completion_tokens,
            total_tokens, estimated_cost, compared_cost,
            cascade_attempts, duration_ms, success,
            prompt_hash, prompt_preview, session_status, session_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            prompt_hash,
            prompt_preview,
            session_status,
            session_key,
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


def query_session_stats(days: int = 30) -> dict:
    """Conversation-level metrics that show the session-stickiness strategy
    is actually working: how many distinct sessions, average turns per
    session, and what fraction of turns were sticky (i.e. saved an
    embedding round-trip and preserved upstream prompt cache).

    Only counts rows with a non-empty ``session_key`` — older logs from
    before the column was introduced are excluded so the averages aren't
    diluted by missing data.
    """
    conn = get_db()
    row = conn.execute(
        """SELECT
             COUNT(DISTINCT session_key) AS sessions,
             COUNT(*) AS turns,
             SUM(CASE WHEN session_status = 'sticky' THEN 1 ELSE 0 END) AS sticky_turns
           FROM requests
           WHERE timestamp >= date('now', ? || ' days')
             AND session_key != ''""",
        (f"-{days}",),
    ).fetchone()
    conn.close()
    sessions = row["sessions"] or 0
    turns = row["turns"] or 0
    sticky = row["sticky_turns"] or 0
    return {
        "sessions": sessions,
        "avg_turns": round(turns / sessions, 1) if sessions else 0.0,
        "sticky_pct": round(sticky / turns * 100, 1) if turns else 0.0,
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
                  policy_name, method, prompt_tokens, completion_tokens,
                  estimated_cost, cascade_attempts, success, session_status
           FROM requests ORDER BY timestamp DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_capability_breakdown(days: int = 30) -> list[dict]:
    """For capability-routed requests, break down by task → routed model.

    Aggregates by ``policy_name`` (not ``method``) so that sticky follow-up
    turns — whose method is ``session_sticky`` but whose ``policy_name`` was
    inherited from the first turn's capability decision — are counted toward
    the same task.  Without this, sticky turns (the majority of agent traffic)
    would silently vanish from the breakdown.

    Filtered to rows whose first-turn classification was a capability route,
    detected by looking at the first turn's ``method`` for each policy.

    Returns rows: {method, routed_model, requests, cost}.  ``method`` here is
    the policy's display label like ``capability(代码生成)``; we re-synthesize
    it from policy_name for backward compatibility with the dashboard renderer.
    """
    conn = get_db()
    rows = conn.execute(
        """SELECT policy_name, routed_model,
                  COUNT(*) as requests,
                  COALESCE(SUM(estimated_cost), 0) as cost
           FROM requests
           WHERE timestamp >= date('now', ? || ' days')
             AND policy_name IN (
               SELECT DISTINCT policy_name FROM requests
               WHERE method LIKE 'capability(%'
             )
           GROUP BY policy_name, routed_model
           ORDER BY policy_name, cost DESC""",
        (f"-{days}",),
    ).fetchall()
    conn.close()
    return [
        {
            "method": f"capability({r['policy_name']})",
            "routed_model": r["routed_model"],
            "requests": r["requests"],
            "cost": r["cost"],
        }
        for r in rows
    ]

def query_policy_stats(days: int = 30) -> list[dict]:
    """Per-policy statistics for the AI optimizer."""
    conn = get_db()
    rows = conn.execute(
        """SELECT
             COALESCE(policy_name, 'none') as name,
             COUNT(*) as hits,
             COALESCE(SUM(estimated_cost), 0) as total_cost,
             ROUND(AVG(similarity_score), 3) as avg_similarity,
             SUM(CASE WHEN cascade_attempts > 0 THEN 1 ELSE 0 END) as cascade_count,
             ROUND(CAST(SUM(CASE WHEN cascade_attempts > 0 THEN 1 ELSE 0 END) AS REAL)
                   / MAX(COUNT(*), 1) * 100, 1) as cascade_pct
           FROM requests
           WHERE timestamp >= date('now', ? || ' days')
           GROUP BY policy_name ORDER BY hits DESC""",
        (f"-{days}",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_unmatched_prompts(days: int = 30, limit: int = 20) -> list[dict]:
    """Get prompt hashes and previews for requests that hit the default policy.

    These are candidates for new policies in the optimizer.
    """
    conn = get_db()
    rows = conn.execute(
        """SELECT prompt_hash, prompt_preview, COUNT(*) as cnt
           FROM requests
           WHERE timestamp >= date('now', ? || ' days')
             AND (policy_name = 'default' OR policy_name = 'none'
                  OR policy_name IS NULL)
             AND prompt_hash != ''
           GROUP BY prompt_hash ORDER BY cnt DESC LIMIT ?""",
        (f"-{days}", limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_cascade_anomalies(days: int = 30, limit: int = 30) -> list[dict]:
    """Get requests that triggered cascade escalation — signals where the
    cheap model failed validation, useful for the optimizer to detect
    policies whose cheap-tier model is consistently inadequate."""
    conn = get_db()
    rows = conn.execute(
        """SELECT policy_name, routed_model, prompt_preview, cascade_attempts
           FROM requests
           WHERE timestamp >= date('now', ? || ' days')
             AND cascade_attempts > 0
           ORDER BY id DESC LIMIT ?""",
        (f"-{days}", limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_export(days: int = 30) -> list[dict]:
    """Export all log entries for the given period."""
    conn = get_db()
    rows = conn.execute(
        """SELECT timestamp, user, original_model, routed_model, policy_name,
                  method, similarity_score, prompt_tokens, completion_tokens,
                  estimated_cost, compared_cost, cascade_attempts,
                  duration_ms, success, prompt_hash, prompt_preview, session_status
           FROM requests
           WHERE timestamp >= date('now', ? || ' days')
           ORDER BY id DESC""",
        (f"-{days}",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_health(days: int = 30) -> dict:
    """Two product-health metrics: success rate and cascade rate.

    - success_pct: how often the request returned a normal response
    - cascade_pct: how often a cheaper model failed validation and triggered
      escalation — a proxy for "how often the first-turn classifier was
      optimistic enough that a stronger model was needed".
    """
    conn = get_db()
    row = conn.execute(
        """SELECT
             COUNT(*) as total,
             SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as succeeded,
             SUM(CASE WHEN cascade_attempts > 0 THEN 1 ELSE 0 END) as cascaded
           FROM requests
           WHERE timestamp >= date('now', ? || ' days')""",
        (f"-{days}",),
    ).fetchone()
    conn.close()
    total = row["total"] or 0
    if total == 0:
        return {"total": 0, "success_pct": 0.0, "cascade_pct": 0.0}
    return {
        "total": total,
        "success_pct": round((row["succeeded"] or 0) / total * 100, 1),
        "cascade_pct": round((row["cascaded"] or 0) / total * 100, 1),
    }
