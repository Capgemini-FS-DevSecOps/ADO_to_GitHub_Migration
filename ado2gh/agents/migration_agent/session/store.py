"""Persistent session store for the migration agent.

Wraps the existing SessionStore with LangGraph state patterns.
Moved from ado2gh/agents/session_store.py and refactored.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id        TEXT PRIMARY KEY,
    profile_id        TEXT NOT NULL,
    model_id          TEXT,
    status            TEXT NOT NULL DEFAULT 'idle',
    messages_json     TEXT NOT NULL DEFAULT '[]',
    pending_form_json TEXT,
    migration_plan_json TEXT,
    dry_run           INTEGER NOT NULL DEFAULT 1,
    iteration_count   INTEGER NOT NULL DEFAULT 0,
    pev_retry_count   INTEGER NOT NULL DEFAULT 0,
    user_username     TEXT,
    created_at        TEXT NOT NULL,
    last_activity_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_messages (
    message_id        TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    from_role         TEXT NOT NULL,
    to_role           TEXT NOT NULL,
    message_type      TEXT NOT NULL,
    payload_json      TEXT NOT NULL DEFAULT '{}',
    timestamp         TEXT NOT NULL,
    correlation_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS migration_plans (
    plan_id           TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    repos_json        TEXT NOT NULL DEFAULT '[]',
    work_items_json   TEXT NOT NULL DEFAULT '[]',
    dry_run           INTEGER NOT NULL DEFAULT 1,
    assumptions_json  TEXT NOT NULL DEFAULT '[]',
    blocked_items_json TEXT NOT NULL DEFAULT '[]',
    revision          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executor_results (
    result_id         TEXT PRIMARY KEY,
    plan_id           TEXT NOT NULL REFERENCES migration_plans(plan_id),
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    per_repo_results_json TEXT NOT NULL DEFAULT '[]',
    failures_json     TEXT NOT NULL DEFAULT '[]',
    skipped_json      TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_results (
    validation_id     TEXT PRIMARY KEY,
    plan_id           TEXT NOT NULL REFERENCES migration_plans(plan_id),
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    per_scope_json    TEXT NOT NULL DEFAULT '{}',
    evidence_json     TEXT NOT NULL DEFAULT '[]',
    failures_json     TEXT NOT NULL DEFAULT '[]',
    remediation_json  TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pev_cycle_summaries (
    cycle_id          TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    cycle_number      INTEGER NOT NULL,
    repos_processed   INTEGER NOT NULL DEFAULT 0,
    repos_succeeded   INTEGER NOT NULL DEFAULT 0,
    repos_failed      INTEGER NOT NULL DEFAULT 0,
    failures_json     TEXT NOT NULL DEFAULT '[]',
    next_action       TEXT NOT NULL,
    timestamp         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migration_queues (
    queue_id          TEXT PRIMARY KEY,
    plan_id           TEXT NOT NULL REFERENCES migration_plans(plan_id),
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    items_json        TEXT NOT NULL DEFAULT '[]',
    current_index     INTEGER NOT NULL DEFAULT 0,
    completed_items_json TEXT NOT NULL DEFAULT '[]',
    failed_items_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS guardrail_decisions (
    decision_id       TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    timestamp         TEXT NOT NULL,
    agent_role        TEXT NOT NULL,
    tool_name         TEXT NOT NULL,
    operation_type    TEXT NOT NULL,
    target_resource   TEXT NOT NULL,
    decision          TEXT NOT NULL,
    reason            TEXT NOT NULL,
    plan_reference    TEXT
);

CREATE TABLE IF NOT EXISTS rollback_records (
    record_id         TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    resource_type     TEXT NOT NULL,
    resource_name     TEXT NOT NULL,
    github_org        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    correlation_id    TEXT NOT NULL,
    rollback_status   TEXT NOT NULL DEFAULT 'eligible'
);

CREATE INDEX IF NOT EXISTS idx_rollback_session
    ON rollback_records(session_id) WHERE rollback_status = 'eligible';
CREATE INDEX IF NOT EXISTS idx_sessions_activity
    ON agent_sessions(last_activity_at);

CREATE TABLE IF NOT EXISTS service_connection_mappings (
    mapping_id          TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES agent_sessions(session_id),
    plan_id             TEXT NOT NULL REFERENCES migration_plans(plan_id),
    ado_connection_name TEXT NOT NULL,
    connection_type     TEXT NOT NULL,
    github_target_type  TEXT NOT NULL,
    github_target_name  TEXT NOT NULL,
    protection_rules_json TEXT,
    correlation_id      TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repo_locks (
    lock_id            TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL,
    repository_id      TEXT NOT NULL,
    locked_at          TEXT NOT NULL,
    released_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_repo_locks_active
    ON repo_locks(repository_id) WHERE released_at IS NULL;
"""


class MigrationSessionStore:
    """Persistent session storage for the migration agent."""

    def __init__(self, db=None) -> None:
        if db is None:
            from ado2gh.state.factory import create_state_db
            db = create_state_db()
        self._db = db
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        if hasattr(self._db, "_conn"):
            with self._db._conn() as conn:
                conn.executescript(SCHEMA)
                self._migrate_user_username_column(conn)

    def _migrate_user_username_column(self, conn: Any) -> None:
        """Add user_username to agent_sessions when upgrading existing DBs."""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_sessions)").fetchall()}
        if cols and "user_username" not in cols:
            conn.execute("ALTER TABLE agent_sessions ADD COLUMN user_username TEXT")

    # ── Agent sessions ──────────────────────────────────────────────────

    def create_session(
        self,
        profile_id: str,
        model_id: str = "",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        sid = _uuid()
        now = _now()
        record = {
            "session_id": sid,
            "profile_id": profile_id,
            "model_id": model_id,
            "status": "idle",
            "messages": [],
            "pending_form": None,
            "migration_plan": None,
            "dry_run": dry_run,
            "iteration_count": 0,
            "pev_retry_count": 0,
            "created_at": now,
            "last_activity_at": now,
        }
        with self._db._conn() as conn:
            conn.execute(
                """INSERT INTO agent_sessions
                   (session_id, profile_id, model_id, status, messages_json,
                    pending_form_json, migration_plan_json, dry_run,
                    iteration_count, pev_retry_count, created_at, last_activity_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, profile_id, model_id, "idle", "[]", None, None,
                 1 if dry_run else 0, 0, 0, now, now),
            )
        return record

    def register_http_session(self, session: dict[str, Any]) -> None:
        """Insert or refresh agent_sessions row for an HTTP-managed session id."""
        session_id = str(session.get("session_id") or "").strip()
        if not session_id:
            return
        now = _now()
        profile_id = str(session.get("profile_id") or "lightweight")
        model_id = str(session.get("selected_model_id") or "")
        status = str(session.get("status") or "idle")
        dry_run = 1 if session.get("dry_run", True) else 0
        messages_json = json.dumps(session.get("messages") or [])
        pending_form = session.get("pending_form")
        migration_plan = session.get("migration_plan")
        pending_form_json = json.dumps(pending_form) if pending_form else None
        migration_plan_json = json.dumps(migration_plan) if migration_plan else None
        created_at = str(session.get("created_at") or now)
        user_username = session.get("user_username")
        with self._db._conn() as conn:
            conn.execute(
                """INSERT INTO agent_sessions
                   (session_id, profile_id, model_id, status, messages_json,
                    pending_form_json, migration_plan_json, dry_run,
                    iteration_count, pev_retry_count, user_username, created_at, last_activity_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_id) DO UPDATE SET
                    profile_id=excluded.profile_id,
                    model_id=excluded.model_id,
                    status=excluded.status,
                    messages_json=excluded.messages_json,
                    pending_form_json=excluded.pending_form_json,
                    migration_plan_json=excluded.migration_plan_json,
                    dry_run=excluded.dry_run,
                    last_activity_at=excluded.last_activity_at""",
                (
                    session_id,
                    profile_id,
                    model_id,
                    status,
                    messages_json,
                    pending_form_json,
                    migration_plan_json,
                    dry_run,
                    int(session.get("iteration_count", 0) or 0),
                    int(session.get("pev_retry_count", 0) or 0),
                    user_username,
                    created_at,
                    str(session.get("updated_at") or now),
                ),
            )

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        with self._db._conn() as conn:
            row = conn.execute(
                "SELECT * FROM agent_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["messages"] = json.loads(d.get("messages_json") or "[]")
        d["pending_form"] = json.loads(d["pending_form_json"]) if d.get("pending_form_json") else None
        d["migration_plan"] = json.loads(d["migration_plan_json"]) if d.get("migration_plan_json") else None
        d["dry_run"] = bool(d.get("dry_run", 1))
        return d

    def update_session_status(self, session_id: str, status: str) -> None:
        now = _now()
        with self._db._conn() as conn:
            conn.execute(
                "UPDATE agent_sessions SET status=?, last_activity_at=? WHERE session_id=?",
                (status, now, session_id),
            )

    def update_session(
        self,
        session_id: str,
        *,
        pending_form: Optional[dict] = None,
        migration_plan: Optional[dict] = None,
        iteration_count: Optional[int] = None,
        pev_retry_count: Optional[int] = None,
        dry_run: Optional[bool] = None,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        if pending_form is not None:
            fields.append("pending_form_json=?")
            values.append(json.dumps(pending_form))
        if migration_plan is not None:
            fields.append("migration_plan_json=?")
            values.append(json.dumps(migration_plan))
        if iteration_count is not None:
            fields.append("iteration_count=?")
            values.append(iteration_count)
        if pev_retry_count is not None:
            fields.append("pev_retry_count=?")
            values.append(pev_retry_count)
        if dry_run is not None:
            fields.append("dry_run=?")
            values.append(1 if dry_run else 0)
        if not fields:
            return
        fields.append("last_activity_at=?")
        values.append(_now())
        values.append(session_id)
        with self._db._conn() as conn:
            conn.execute(
                f"UPDATE agent_sessions SET {', '.join(fields)} WHERE session_id=?",
                values,
            )

    def list_sessions(self, profile_id: str | None = None) -> list[dict[str, Any]]:
        with self._db._conn() as conn:
            if profile_id:
                rows = conn.execute(
                    "SELECT * FROM agent_sessions WHERE profile_id=? ORDER BY last_activity_at DESC",
                    (profile_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_sessions ORDER BY last_activity_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, session_id: str) -> None:
        with self._db._conn() as conn:
            for table in (
                "agent_messages", "migration_plans", "executor_results",
                "validation_results", "pev_cycle_summaries", "migration_queues",
                "guardrail_decisions", "rollback_records",
                "service_connection_mappings", "repo_locks",
            ):
                conn.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM agent_sessions WHERE session_id=?", (session_id,))

    # ── Agent messages ──────────────────────────────────────────────────

    def add_message(
        self,
        session_id: str,
        from_role: str,
        to_role: str,
        message_type: str,
        payload: dict[str, Any],
        correlation_ids: Optional[dict] = None,
    ) -> str:
        mid = _uuid()
        now = _now()
        with self._db._conn() as conn:
            conn.execute(
                """INSERT INTO agent_messages
                   (message_id, session_id, from_role, to_role, message_type,
                    payload_json, timestamp, correlation_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (mid, session_id, from_role, to_role, message_type,
                 json.dumps(payload), now, json.dumps(correlation_ids or {})),
            )
        return mid

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._db._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_messages WHERE session_id=? ORDER BY timestamp",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Migration plans ─────────────────────────────────────────────────

    def save_plan(self, session_id: str, plan: dict[str, Any]) -> str:
        pid = _uuid()
        now = _now()
        with self._db._conn() as conn:
            conn.execute(
                """INSERT INTO migration_plans
                   (plan_id, session_id, repos_json, work_items_json, dry_run,
                    assumptions_json, blocked_items_json, revision, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (pid, session_id,
                 json.dumps(plan.get("repos", [])),
                 json.dumps(plan.get("work_items", [])),
                 1 if plan.get("dry_run", True) else 0,
                 json.dumps(plan.get("assumptions", [])),
                 json.dumps(plan.get("blocked_items", [])),
                 plan.get("revision", 0), now),
            )
        return pid

    # ── PEV cycle summaries ─────────────────────────────────────────────

    def save_cycle_summary(self, session_id: str, summary: dict[str, Any]) -> str:
        cid = _uuid()
        now = _now()
        with self._db._conn() as conn:
            conn.execute(
                """INSERT INTO pev_cycle_summaries
                   (cycle_id, session_id, cycle_number, repos_processed,
                    repos_succeeded, repos_failed, failures_json, next_action, timestamp)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (cid, session_id,
                 summary.get("cycle_number", 0),
                 summary.get("repos_processed", 0),
                 summary.get("repos_succeeded", 0),
                 summary.get("repos_failed", 0),
                 json.dumps(summary.get("failures", [])),
                 summary.get("next_action", "unknown"), now),
            )
        return cid

    def get_cycle_summaries(self, session_id: str) -> list[dict[str, Any]]:
        with self._db._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pev_cycle_summaries WHERE session_id=? ORDER BY cycle_number",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Guardrail decisions ─────────────────────────────────────────────

    def save_guardrail_decision(self, session_id: str, decision: dict[str, Any]) -> str:
        did = _uuid()
        now = _now()
        with self._db._conn() as conn:
            conn.execute(
                """INSERT INTO guardrail_decisions
                   (decision_id, session_id, timestamp, agent_role, tool_name,
                    operation_type, target_resource, decision, reason, plan_reference)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (did, session_id, now,
                 decision.get("agent_role", ""),
                 decision.get("tool_name", ""),
                 decision.get("operation_type", ""),
                 decision.get("target_resource", ""),
                 decision.get("decision", ""),
                 decision.get("reason", ""),
                 decision.get("plan_reference")),
            )
        return did

    # ── Rollback records ────────────────────────────────────────────────

    def save_rollback_record(self, session_id: str, record: dict[str, Any]) -> str:
        rid = _uuid()
        now = _now()
        with self._db._conn() as conn:
            conn.execute(
                """INSERT INTO rollback_records
                   (record_id, session_id, resource_type, resource_name,
                    github_org, created_at, correlation_id, rollback_status)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (rid, session_id,
                 record.get("resource_type", ""),
                 record.get("resource_name", ""),
                 record.get("github_org", ""),
                 now,
                 record.get("correlation_id", ""),
                 record.get("rollback_status", "eligible")),
            )
        return rid

    def get_rollback_records(self, session_id: str) -> list[dict[str, Any]]:
        with self._db._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM rollback_records WHERE session_id=? AND rollback_status='eligible'",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Repo locks ──────────────────────────────────────────────────────

    def acquire_repo_lock(self, session_id: str, repository_id: str) -> bool:
        now = _now()
        with self._db._conn() as conn:
            existing = conn.execute(
                "SELECT session_id FROM repo_locks WHERE repository_id=? AND released_at IS NULL",
                (repository_id,),
            ).fetchone()
            if existing:
                return False
            conn.execute(
                """INSERT INTO repo_locks (lock_id, session_id, repository_id, locked_at)
                   VALUES (?,?,?,?)""",
                (_uuid(), session_id, repository_id, now),
            )
        return True

    def repo_lock_holder(self, repository_id: str) -> str | None:
        """Return session_id holding an active lock, or None if unlocked."""
        with self._db._conn() as conn:
            row = conn.execute(
                "SELECT session_id FROM repo_locks WHERE repository_id=? AND released_at IS NULL",
                (repository_id,),
            ).fetchone()
        if not row:
            return None
        return str(row["session_id"])

    def release_repo_lock(self, session_id: str, repository_id: str) -> None:
        now = _now()
        with self._db._conn() as conn:
            conn.execute(
                "UPDATE repo_locks SET released_at=? WHERE session_id=? AND repository_id=? AND released_at IS NULL",
                (now, session_id, repository_id),
            )

    def release_all_locks(self, session_id: str) -> None:
        now = _now()
        with self._db._conn() as conn:
            conn.execute(
                "UPDATE repo_locks SET released_at=? WHERE session_id=? AND released_at IS NULL",
                (now, session_id),
            )

    # ── Executor results ────────────────────────────────────────────────

    def save_executor_result(self, session_id: str, plan_id: str, result: dict[str, Any]) -> str:
        rid = _uuid()
        now = _now()
        with self._db._conn() as conn:
            conn.execute(
                """INSERT INTO executor_results
                   (result_id, plan_id, session_id, per_repo_results_json,
                    failures_json, skipped_json, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (rid, plan_id, session_id,
                 json.dumps(result.get("per_repo_results", [])),
                 json.dumps(result.get("failures", [])),
                 json.dumps(result.get("skipped", [])), now),
            )
        return rid

    def get_executor_results(self, session_id: str) -> list[dict[str, Any]]:
        with self._db._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM executor_results WHERE session_id=? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Validation results ──────────────────────────────────────────────

    def save_validation_result(self, session_id: str, plan_id: str, result: dict[str, Any]) -> str:
        vid = _uuid()
        now = _now()
        with self._db._conn() as conn:
            conn.execute(
                """INSERT INTO validation_results
                   (validation_id, plan_id, session_id, per_scope_json,
                    evidence_json, failures_json, remediation_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (vid, plan_id, session_id,
                 json.dumps(result.get("per_scope", {})),
                 json.dumps(result.get("evidence", [])),
                 json.dumps(result.get("failures", [])),
                 json.dumps(result.get("remediation", [])), now),
            )
        return vid

    def get_validation_results(self, session_id: str) -> list[dict[str, Any]]:
        with self._db._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM validation_results WHERE session_id=? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Full session state persistence (T059-T060) ──────────────────────

    def save_session_state(self, session_id: str, state: dict[str, Any]) -> None:
        """Persist full LangGraph agent state for resume.

        Saves migration plan, executor result, validation result, cycle summaries,
        rollback records, and updates session metadata.
        """
        session = state.get("session") or {}
        migration_plan = state.get("migration_plan") or session.get("migration_plan")
        executor_result = state.get("executor_result")
        validation_result = state.get("validation_result")
        cycle_summaries = state.get("cycle_summaries", [])
        rollback_records = state.get("rollback_records", [])
        iteration = state.get("iteration", 0)
        pev_retry_count = state.get("pev_retry_count", 0)

        # Update session metadata
        self.update_session(
            session_id,
            migration_plan=migration_plan,
            iteration_count=iteration,
            pev_retry_count=pev_retry_count,
        )

        # Save migration plan
        plan_id = None
        if migration_plan:
            plan_id = self.save_plan(session_id, migration_plan)

        # Save executor result
        if executor_result and plan_id:
            self.save_executor_result(session_id, plan_id, executor_result)

        # Save validation result
        if validation_result and plan_id:
            self.save_validation_result(session_id, plan_id, validation_result)

        # Save cycle summaries
        for summary in cycle_summaries:
            self.save_cycle_summary(session_id, summary)

        # Save rollback records
        for record in rollback_records:
            self.save_rollback_record(session_id, record)

    def load_session_state(self, session_id: str) -> dict[str, Any]:
        """Load persisted session state for resume.

        Returns a dict suitable for seeding AgentState on graph re-invocation.
        """
        session = self.get_session(session_id)
        if not session:
            return {}

        cycle_summaries_raw = self.get_cycle_summaries(session_id)
        rollback_records = self.get_rollback_records(session_id)

        # Get latest executor and validation results
        executor_results = self.get_executor_results(session_id)
        validation_results = self.get_validation_results(session_id)

        latest_executor = None
        latest_validation = None
        if executor_results:
            latest = executor_results[-1]
            latest_executor = {
                "per_repo_results": json.loads(latest.get("per_repo_results_json") or "[]"),
                "failures": json.loads(latest.get("failures_json") or "[]"),
                "skipped": json.loads(latest.get("skipped_json") or "[]"),
            }
        if validation_results:
            latest = validation_results[-1]
            latest_validation = {
                "per_scope": json.loads(latest.get("per_scope_json") or "{}"),
                "evidence": json.loads(latest.get("evidence_json") or "[]"),
                "failures": json.loads(latest.get("failures_json") or "[]"),
            }

        cycle_summaries = [
            {
                "cycle_number": s.get("cycle_number", 0),
                "repos_processed": s.get("repos_processed", 0),
                "repos_succeeded": s.get("repos_succeeded", 0),
                "repos_failed": s.get("repos_failed", 0),
                "failures": json.loads(s.get("failures_json") or "[]"),
                "next_action": s.get("next_action", "unknown"),
            }
            for s in cycle_summaries_raw
        ]

        return {
            "session": session,
            "migration_plan": session.get("migration_plan"),
            "executor_result": latest_executor,
            "validation_result": latest_validation,
            "cycle_summaries": cycle_summaries,
            "rollback_records": rollback_records,
            "iteration": session.get("iteration_count", 0),
            "pev_retry_count": session.get("pev_retry_count", 0),
        }

    def add_message_to_session(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        kind: str = "message",
        subagent: str | None = None,
    ) -> None:
        """Add a chat message to the session's messages_json."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._db._conn() as conn:
            row = conn.execute(
                "SELECT messages_json FROM agent_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if not row:
                return
            messages = json.loads(row["messages_json"] or "[]")
            messages.append({
                "role": role,
                "content": content,
                "kind": kind,
                "subagent": subagent,
                "timestamp": now,
            })
            conn.execute(
                "UPDATE agent_sessions SET messages_json=?, last_activity_at=? WHERE session_id=?",
                (json.dumps(messages), now, session_id),
            )
