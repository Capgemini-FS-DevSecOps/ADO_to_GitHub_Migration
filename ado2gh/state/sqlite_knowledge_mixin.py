"""SQLite knowledge-base methods, mixed into ``SQLiteStateDB``.

Kept separate so ``sqlite_db.py`` stays under the 800-line cap. Every value is
stored as text; the meaning of a column is the knowledge store's business, not
this backend's.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from ado2gh.state.base import (
    KNOWLEDGE_EDGE_COLUMNS,
    KNOWLEDGE_NODE_COLUMNS,
    KNOWLEDGE_SCAN_COLUMNS,
    KNOWLEDGE_SCAN_UPDATABLE,
)

if TYPE_CHECKING:
    import sqlite3

    class _SQLiteConnHost(Protocol):
        """Attribute ``KnowledgeBaseMixin`` expects from ``SQLiteStateDB``."""

        def _conn(self) -> sqlite3.Connection: ...
else:
    _SQLiteConnHost = object


class KnowledgeBaseMixin(_SQLiteConnHost):
    """Nodes, dependencies and scans of the knowledge base; expects ``self._conn()``."""

    def _replace_knowledge_row(
        self, table: str, columns: tuple[str, ...], row: dict[str, Any],
    ) -> None:
        """Write one knowledge row as text, replacing any row holding the same id."""
        marks = ",".join("?" * len(columns))
        with self._conn() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({marks})",
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
        assignments = ",".join(f"{c}=?" for c in columns)
        values = tuple(str(row[c]) for c in columns)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE knowledge_scans SET {assignments} WHERE id=?",
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
        clauses = ["profile_id=?"]
        params: list[Any] = [profile_id]
        if node_ids is not None:
            if not node_ids:
                return []
            clauses.append(f"id IN ({','.join('?' * len(node_ids))})")
            params.extend(node_ids)
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if identity_key:
            clauses.append("identity_key=?")
            params.append(identity_key)
        sql = (
            f"SELECT * FROM knowledge_nodes WHERE {' AND '.join(clauses)} "
            "ORDER BY identity_key, id"
        )
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]

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
        clauses = ["profile_id=?", "(LOWER(identity_key) LIKE ? OR LOWER(name) LIKE ?)"]
        params: list[Any] = [profile_id, like, like]
        if kinds:
            clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        sql = (
            f"SELECT * FROM knowledge_nodes WHERE {' AND '.join(clauses)} "
            "ORDER BY identity_key, id LIMIT ?"
        )
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, (*params, limit)).fetchall()]

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
            ends.append(f"source_node_id IN ({','.join('?' * len(source_ids))})")
            params.extend(source_ids)
        if target_ids:
            ends.append(f"target_node_id IN ({','.join('?' * len(target_ids))})")
            params.extend(target_ids)
        if not ends:
            return []
        clauses = ["profile_id=?", f"({' OR '.join(ends)})"]
        if status:
            clauses.append("status=?")
            params.append(status)
        sql = f"SELECT * FROM knowledge_edges WHERE {' AND '.join(clauses)} ORDER BY id"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]

    def mark_knowledge_edges_disappeared(self, profile_id: str, scan_id: str) -> int:
        """Mark edges a scan did not confirm; see :meth:`StateDBBase.mark_knowledge_edges_disappeared`."""
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE knowledge_edges SET status='disappeared' "
                "WHERE profile_id=? AND status='active' AND last_scan_id<>?",
                (profile_id, scan_id),
            )
            return int(cursor.rowcount)

    def latest_knowledge_scan(self, profile_id: str) -> dict | None:
        """Return the newest finished scan row; see :meth:`StateDBBase.latest_knowledge_scan`."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_scans WHERE profile_id=? AND completed_at<>'' "
                "ORDER BY completed_at DESC, id DESC LIMIT 1",
                (profile_id,),
            ).fetchone()
        return dict(row) if row else None
