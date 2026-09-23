"""PostgreSQL knowledge-base methods, mixed into ``PostgresStateDB``.

Kept separate so ``postgres_db.py`` stays under the 800-line cap. The tables,
the column order and the meaning of every value match the SQLite backend.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator, Protocol

from ado2gh.state.base import (
    KNOWLEDGE_EDGE_COLUMNS,
    KNOWLEDGE_NODE_COLUMNS,
    KNOWLEDGE_SCAN_COLUMNS,
    KNOWLEDGE_SCAN_UPDATABLE,
)

if TYPE_CHECKING:

    class _PostgresConnHost(Protocol):
        """Attributes ``PostgresKnowledgeBaseMixin`` expects from ``PostgresStateDB``."""

        _extras: Any

        @contextmanager
        def _conn(self) -> Iterator[Any]: ...
else:
    _PostgresConnHost = object


class PostgresKnowledgeBaseMixin(_PostgresConnHost):
    """Nodes, dependencies and scans of the knowledge base; expects ``self._conn()``."""

    def _replace_knowledge_row(
        self, table: str, columns: tuple[str, ...], row: dict[str, Any],
    ) -> None:
        """Write one knowledge row as text, replacing any row holding the same id."""
        marks = ",".join(["%s"] * len(columns))
        updates = ",".join(f"{c}=EXCLUDED.{c}" for c in columns if c != "id")
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {table} ({','.join(columns)}) VALUES ({marks}) "
                    f"ON CONFLICT (id) DO UPDATE SET {updates}",
                    tuple(str(row.get(c, "")) for c in columns),
                )

    def upsert_knowledge_node(self, row: dict[str, Any]) -> None:
        """Insert or replace one node row; see :meth:`StateDBBase.upsert_knowledge_node`."""
        self._replace_knowledge_row("knowledge_nodes", KNOWLEDGE_NODE_COLUMNS, row)

    def upsert_knowledge_edge(self, row: dict[str, Any]) -> None:
        """Insert or replace one edge row; see :meth:`StateDBBase.upsert_knowledge_edge`."""
        self._replace_knowledge_row("knowledge_edges", KNOWLEDGE_EDGE_COLUMNS, row)

    def insert_knowledge_scan(self, row: dict[str, Any]) -> None:
        """Record that a scan has started; see :meth:`StateDBBase.insert_knowledge_scan`."""
        self._replace_knowledge_row("knowledge_scans", KNOWLEDGE_SCAN_COLUMNS, row)

    def update_knowledge_scan(self, scan_id: str, row: dict[str, Any]) -> None:
        """Record how a scan ended; see :meth:`StateDBBase.update_knowledge_scan`."""
        columns = [c for c in KNOWLEDGE_SCAN_UPDATABLE if c in row]
        if not columns:
            return
        assignments = ",".join(f"{c}=%s" for c in columns)
        values = tuple(str(row[c]) for c in columns)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE knowledge_scans SET {assignments} WHERE id=%s",
                    (*values, scan_id),
                )

    def get_knowledge_nodes(
        self,
        profile_id: str,
        *,
        node_ids: list[str] | None = None,
        kind: str = "",
        identity_key: str = "",
    ) -> list[dict]:
        """Return matching node rows; see :meth:`StateDBBase.get_knowledge_nodes`."""
        clauses = ["profile_id=%s"]
        params: list[Any] = [profile_id]
        if node_ids is not None:
            if not node_ids:
                return []
            clauses.append(f"id IN ({','.join(['%s'] * len(node_ids))})")
            params.extend(node_ids)
        if kind:
            clauses.append("kind=%s")
            params.append(kind)
        if identity_key:
            clauses.append("identity_key=%s")
            params.append(identity_key)
        sql = (
            f"SELECT * FROM knowledge_nodes WHERE {' AND '.join(clauses)} "
            "ORDER BY identity_key, id"
        )
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]

    def search_knowledge_nodes(
        self,
        profile_id: str,
        text: str,
        *,
        kinds: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Return node rows matching text; see :meth:`StateDBBase.search_knowledge_nodes`."""
        like = f"%{text.lower()}%"
        clauses = ["profile_id=%s", "(LOWER(identity_key) LIKE %s OR LOWER(name) LIKE %s)"]
        params: list[Any] = [profile_id, like, like]
        if kinds:
            clauses.append(f"kind IN ({','.join(['%s'] * len(kinds))})")
            params.extend(kinds)
        sql = (
            f"SELECT * FROM knowledge_nodes WHERE {' AND '.join(clauses)} "
            "ORDER BY identity_key, id LIMIT %s"
        )
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(sql, (*params, limit))
                return [dict(r) for r in cur.fetchall()]

    def get_knowledge_edges(
        self,
        profile_id: str,
        *,
        source_ids: list[str] | None = None,
        target_ids: list[str] | None = None,
        status: str = "",
    ) -> list[dict]:
        """Return edge rows on either end; see :meth:`StateDBBase.get_knowledge_edges`."""
        ends: list[str] = []
        params: list[Any] = [profile_id]
        if source_ids:
            ends.append(f"source_node_id IN ({','.join(['%s'] * len(source_ids))})")
            params.extend(source_ids)
        if target_ids:
            ends.append(f"target_node_id IN ({','.join(['%s'] * len(target_ids))})")
            params.extend(target_ids)
        if not ends:
            return []
        clauses = ["profile_id=%s", f"({' OR '.join(ends)})"]
        if status:
            clauses.append("status=%s")
            params.append(status)
        sql = f"SELECT * FROM knowledge_edges WHERE {' AND '.join(clauses)} ORDER BY id"
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]

    def mark_knowledge_edges_disappeared(self, profile_id: str, scan_id: str) -> int:
        """Mark edges a scan did not confirm; see :meth:`StateDBBase.mark_knowledge_edges_disappeared`."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE knowledge_edges SET status='disappeared' "
                    "WHERE profile_id=%s AND status='active' AND last_scan_id<>%s",
                    (profile_id, scan_id),
                )
                return int(cur.rowcount)

    def latest_knowledge_scan(self, profile_id: str) -> dict | None:
        """Return the newest finished scan row; see :meth:`StateDBBase.latest_knowledge_scan`."""
        with self._conn() as conn:
            with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM knowledge_scans WHERE profile_id=%s AND completed_at<>'' "
                    "ORDER BY completed_at DESC, id DESC LIMIT 1",
                    (profile_id,),
                )
                row = cur.fetchone()
        return dict(row) if row else None
