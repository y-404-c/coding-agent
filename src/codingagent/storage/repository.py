from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from codingagent.domain.models import RunTrace

_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    strategy      TEXT NOT NULL,
    problem_id    TEXT NOT NULL,
    repeat_index  INTEGER NOT NULL DEFAULT 0,
    model         TEXT NOT NULL DEFAULT '',
    passed        INTEGER NOT NULL DEFAULT 0,
    error_category TEXT NOT NULL DEFAULT 'none',
    total_tokens  INTEGER NOT NULL DEFAULT 0,
    total_steps   INTEGER NOT NULL DEFAULT 0,
    attempts      INTEGER NOT NULL DEFAULT 0,
    degraded      INTEGER NOT NULL DEFAULT 0,
    config_hash   TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    trace_json    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_strategy ON runs(strategy);
CREATE INDEX IF NOT EXISTS idx_runs_problem ON runs(problem_id);
CREATE INDEX IF NOT EXISTS idx_runs_signature ON runs(strategy, problem_id, repeat_index, config_hash);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class TraceRepository:
    """SQLite 落库：trace 是项目二的输入，也是 bad case 定位素材。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(_DDL)

    def save(self, trace: RunTrace) -> None:
        degraded = int(any(attempt.degraded for attempt in trace.attempts))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, strategy, problem_id, repeat_index, model, passed,
                    error_category, total_tokens, total_steps, attempts, degraded,
                    config_hash, prompt_version, started_at, finished_at, trace_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.run_id,
                    trace.strategy.value,
                    trace.problem_id,
                    trace.repeat_index,
                    trace.model,
                    int(trace.passed),
                    trace.error_category.value,
                    trace.total_tokens,
                    trace.total_steps,
                    len(trace.attempts),
                    degraded,
                    trace.config_hash,
                    trace.prompt_version,
                    trace.started_at.isoformat(),
                    trace.finished_at.isoformat() if trace.finished_at else None,
                    trace.model_dump_json(),
                ),
            )

    def get(self, run_id: str) -> RunTrace | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT trace_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return RunTrace.model_validate_json(row["trace_json"]) if row else None

    def list_traces(
        self,
        *,
        strategy: str | None = None,
        problem_id: str | None = None,
        config_hash: str | None = None,
        limit: int | None = None,
    ) -> list[RunTrace]:
        clauses = []
        params: list[object] = []
        if strategy:
            clauses.append("strategy = ?")
            params.append(str(strategy))
        if problem_id:
            clauses.append("problem_id = ?")
            params.append(problem_id)
        if config_hash:
            clauses.append("config_hash = ?")
            params.append(config_hash)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_sql = f"LIMIT {int(limit)}" if limit else ""
        query = f"SELECT trace_json FROM runs {where} ORDER BY started_at {limit_sql}"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [RunTrace.model_validate_json(row["trace_json"]) for row in rows]

    def exists(
        self, strategy: str, problem_id: str, repeat_index: int, config_hash: str
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM runs
                   WHERE strategy = ? AND problem_id = ? AND repeat_index = ? AND config_hash = ?
                   LIMIT 1""",
                (str(strategy), problem_id, repeat_index, config_hash),
            ).fetchone()
        return row is not None

    def set_meta(self, key: str, value: object) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def get_meta(self, key: str) -> object | None:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total, SUM(passed) AS passed FROM runs"
            ).fetchone()
        return {"total": int(row["total"] or 0), "passed": int(row["passed"] or 0)}
