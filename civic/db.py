"""psycopg2 helpers: connections, idempotent upserts, run log, checkpoints."""
from __future__ import annotations

import contextlib

import psycopg2
import psycopg2.extras
from psycopg2.extras import Json

from .config import settings


@contextlib.contextmanager
def connect():
    conn = psycopg2.connect(settings.dsn)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(sql, params=None) -> list[dict]:
    with connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def upsert(cur, table, rows: list[dict], conflict: list[str], update=None,
           template=None, page_size=500) -> int:
    """INSERT ... ON CONFLICT DO UPDATE for a list of same-shaped dicts."""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    # Postgres refuses to update the same row twice in one statement, so dedupe on the key
    dedup = {tuple(r[c] for c in conflict): r for r in rows}
    values = [tuple(r[c] for c in cols) for r in dedup.values()]
    if update is None:
        update = [c for c in cols if c not in conflict]
    action = ("DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in update)) if update else "DO NOTHING"
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s "
           f"ON CONFLICT ({', '.join(conflict)}) {action}")
    psycopg2.extras.execute_values(cur, sql, values, template=template, page_size=page_size)
    return len(values)


def get_checkpoint(key: str) -> dict:
    rows = fetch_all("SELECT value FROM civic.etl_state WHERE key = %s", (key,))
    return rows[0]["value"] if rows else {}


def set_checkpoint(cur, key: str, value: dict) -> None:
    cur.execute(
        """INSERT INTO civic.etl_state (key, value, updated_at) VALUES (%s, %s, now())
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        (key, Json(value)),
    )


@contextlib.contextmanager
def run_log(job: str):
    """Record each loader run in civic.etl_run. Yields a dict; loaders set stats['rows']."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO civic.etl_run (job) VALUES (%s) RETURNING run_id", (job,))
        run_id = cur.fetchone()[0]
    stats = {"rows": 0, "message": None}
    status = "ok"
    try:
        yield stats
    except BaseException as e:
        status = "error"
        stats["message"] = f"{type(e).__name__}: {e}"[:2000]
        raise
    finally:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE civic.etl_run SET finished_at = now(), status = %s, rows = %s, message = %s
                   WHERE run_id = %s""",
                (status, stats["rows"], stats["message"], run_id),
            )
