"""DynamoDB migration state store (serverless production backend)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from ado2gh.models import MigrationStatus, PipelineMetadata


def _phase_value(phase) -> str:
    """Accept PhaseType enum or plain phase id string."""
    return phase.value if hasattr(phase, "value") else str(phase)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _deserialize(item: dict | None) -> dict | None:
    if not item:
        return None
    data = item.get("data")
    if isinstance(data, str):
        return json.loads(data)
    return dict(item)


class DynamoDBStateDB:
    """Single-table DynamoDB store for migration state (serverless production)."""

    def __init__(self, table_name: str, region: str = "us-east-1", endpoint_url: str | None = None):
        import boto3

        kwargs: dict[str, Any] = {"region_name": region}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        self._table_name = table_name
        self._dynamodb = boto3.resource("dynamodb", **kwargs)
        self._client = boto3.client("dynamodb", **kwargs)
        self._ensure_table()

    def _table(self):
        return self._dynamodb.Table(self._table_name)

    def _ensure_table(self) -> None:
        existing = self._client.list_tables().get("TableNames", [])
        if self._table_name in existing:
            return
        self._client.create_table(
            TableName=self._table_name,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        waiter = self._client.get_waiter("table_exists")
        waiter.wait(TableName=self._table_name)

    def _put(self, pk: str, sk: str, data: dict) -> None:
        self._table().put_item(Item={
            "pk": pk,
            "sk": sk,
            "data": json.dumps(data, default=str),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    def _get(self, pk: str, sk: str) -> dict | None:
        resp = self._table().get_item(Key={"pk": pk, "sk": sk})
        item = resp.get("Item")
        if not item:
            return None
        return json.loads(item["data"])

    def _query_pk(self, pk: str) -> list[dict]:
        resp = self._table().query(KeyConditionExpression="pk = :pk", ExpressionAttributeValues={":pk": pk})
        out = []
        for item in resp.get("Items", []):
            out.append(json.loads(item["data"]))
        return out

    def upsert_migration(self, wave_id: int, repo, scope: str,
                         status: MigrationStatus, error: str = None,
                         gh_migration_id: str = None, stats: dict = None):
        now = datetime.now(timezone.utc).isoformat()
        sk = f"{wave_id}#{repo.ado_project}#{repo.ado_repo}#{scope}"
        existing = self._get("migration", sk) or {}
        self._put("migration", sk, {
            **existing,
            "wave_id": wave_id,
            "ado_project": repo.ado_project,
            "ado_repo": repo.ado_repo,
            "gh_org": repo.gh_org,
            "gh_repo": repo.gh_repo,
            "scope": scope,
            "status": status.value,
            "started_at": existing.get("started_at") or (now if status == MigrationStatus.IN_PROGRESS else None),
            "completed_at": now if status in (MigrationStatus.COMPLETED, MigrationStatus.FAILED,
                                              MigrationStatus.ROLLED_BACK) else existing.get("completed_at"),
            "error_message": error,
            "gh_migration_id": gh_migration_id,
            "stats": stats,
        })

    def get_wave_migrations(self, wave_id: int) -> list[dict]:
        return [r for r in self._query_pk("migration") if r.get("wave_id") == wave_id]

    def get_all_migrations(self) -> list[dict]:
        return self._query_pk("migration")

    def migration_status_counts(self) -> dict:
        counts: dict[str, int] = {}
        seen: dict[str, set[str]] = {}
        for r in self._query_pk("migration"):
            status = r.get("status", "pending")
            repo = r.get("ado_repo", "")
            seen.setdefault(status, set()).add(repo)
        for status, repos in seen.items():
            counts[status] = len(repos)
        return counts

    def get_migration_repo_counts(self) -> dict:
        rows = self._query_pk("migration")
        seen_done: set[str] = set()
        seen_fail: set[str] = set()
        for r in rows:
            key = r.get("ado_repo", "")
            if r.get("status") == "completed":
                seen_done.add(key)
            elif r.get("status") == "failed":
                seen_fail.add(key)
        pipelines = self._query_pk("pipeline_migration")
        repos = {f"{r.get('ado_project')}/{r.get('ado_repo')}" for r in rows}
        return {
            "total_repos": len(repos),
            "completed_repos": len(seen_done - seen_fail),
            "failed_repos": len(seen_fail - seen_done),
            "total_pipelines": len(pipelines),
        }

    def get_failed_migrations(self, wave_id: int = None) -> list[dict]:
        rows = [r for r in self._query_pk("migration") if r.get("status") == "failed"]
        if wave_id is not None:
            rows = [r for r in rows if r.get("wave_id") == wave_id]
        return rows

    def wave_summary(self, wave_id: int) -> dict:
        result: dict = {}
        for r in self.get_wave_migrations(wave_id):
            scope = r.get("scope", "")
            status = r.get("status", "")
            result.setdefault(scope, {})[status] = result.get(scope, {}).get(status, 0) + 1
        return result

    def mark_wave_run(self, wave_id: int, status: str, dry_run: bool = False) -> int:
        now = datetime.now(timezone.utc).isoformat()
        sk = f"wave#{wave_id}"
        if status == "started":
            self._put("wave_run", sk, {
                "wave_id": wave_id, "started_at": now, "status": "in_progress", "dry_run": dry_run,
            })
            return wave_id
        existing = self._get("wave_run", sk) or {"wave_id": wave_id}
        existing.update({"completed_at": now, "status": status})
        self._put("wave_run", sk, existing)
        return -1

    def upsert_pipeline_inventory(self, meta: PipelineMetadata):
        sk = f"{meta.project}#{meta.pipeline_id}"
        self._put("inventory", sk, meta.to_dict())

    def get_pipelines_for_repo(self, project: str, repo_name: str) -> list[PipelineMetadata]:
        return [
            PipelineMetadata.from_dict(r)
            for r in self._query_pk("inventory")
            if r.get("project") == project and r.get("repo_name") == repo_name
        ]

    def get_all_inventory(self, project: str = None) -> list[dict]:
        rows = self._query_pk("inventory")
        if project:
            rows = [r for r in rows if r.get("project") == project]
        return rows

    def inventory_count(self, project: str = None) -> int:
        rows = self._query_pk("inventory")
        if project:
            rows = [r for r in rows if r.get("project") == project]
        return len(rows)

    def inventory_count_for_repo(self, project: str, repo_name: str) -> int:
        return len([
            r for r in self._query_pk("inventory")
            if r.get("project") == project and r.get("repo_name") == repo_name
        ])

    def clear_inventory(self, project: str = None):
        for r in self._query_pk("inventory"):
            if project and r.get("project") != project:
                continue
            sk = f"{r.get('project')}#{r.get('pipeline_id')}"
            self._table().delete_item(Key={"pk": "inventory", "sk": sk})

    def upsert_pipeline_migration(self, wave_id: int, meta: PipelineMetadata,
                                  gh_org: str, gh_repo: str,
                                  status: MigrationStatus,
                                  workflow_file: str = None,
                                  error: str = None,
                                  warnings: list = None,
                                  unsupported: list = None,
                                  transform_stats: dict = None):
        now = datetime.now(timezone.utc).isoformat()
        sk = f"{wave_id}#{meta.project}#{meta.pipeline_id}"
        existing = self._get("pipeline_migration", sk) or {}
        self._put("pipeline_migration", sk, {
            **existing,
            "wave_id": wave_id,
            "project": meta.project,
            "pipeline_id": meta.pipeline_id,
            "pipeline_name": meta.pipeline_name,
            "repo_name": meta.repo_name,
            "gh_org": gh_org,
            "gh_repo": gh_repo,
            "workflow_file": workflow_file,
            "status": status.value,
            "started_at": existing.get("started_at") or (now if status == MigrationStatus.IN_PROGRESS else None),
            "completed_at": now if status in (MigrationStatus.COMPLETED, MigrationStatus.FAILED) else existing.get("completed_at"),
            "error_message": error,
            "warnings": warnings or [],
            "unsupported_tasks": unsupported or [],
            "complexity": meta.complexity.value,
            "transform_stats": transform_stats or {},
        })

    def get_wave_pipeline_migrations(self, wave_id: int) -> list[dict]:
        return [r for r in self._query_pk("pipeline_migration") if r.get("wave_id") == wave_id]

    def get_failed_pipeline_migrations(self, wave_id: int) -> list[dict]:
        return [
            r for r in self.get_wave_pipeline_migrations(wave_id)
            if r.get("status") in ("failed", "pending")
        ]

    def pipeline_migration_summary(self, wave_id: int) -> dict:
        result: dict = {"by_status": {}, "by_complexity": {}}
        for r in self.get_wave_pipeline_migrations(wave_id):
            st = r.get("status", "")
            cx = r.get("complexity", "")
            result["by_status"][st] = result["by_status"].get(st, 0) + 1
            result["by_complexity"][cx] = result["by_complexity"].get(cx, 0) + 1
        return result

    def reset_failed_pipeline_migrations(self, wave_id: int):
        for r in self.get_failed_pipeline_migrations(wave_id):
            if r.get("status") == "failed":
                sk = f"{wave_id}#{r.get('project')}#{r.get('pipeline_id')}"
                self._table().delete_item(Key={"pk": "pipeline_migration", "sk": sk})

    def upsert_risk_score(self, score):
        sk = f"{score.project}#{score.repo_name}"
        self._put("risk", sk, score.to_dict())

    def get_all_risk_scores(self) -> list:
        return self._query_pk("risk")

    def get_risk_scores_for_phase(self, phase) -> list:
        phase_val = _phase_value(phase)
        return [r for r in self._query_pk("risk") if r.get("assigned_phase") == phase_val]

    def risk_score_count(self) -> int:
        return len(self._query_pk("risk"))

    def upsert_phase_gate(self, result):
        self._put("phase_gate", result.phase.value, {
            "phase": result.phase.value,
            "status": result.status.value,
            "repo_success_pct": result.repo_success_pct,
            "pipeline_success_pct": result.pipeline_success_pct,
            "repos_completed": result.repos_completed,
            "repos_total": result.repos_total,
            "pipelines_completed": result.pipelines_completed,
            "pipelines_total": result.pipelines_total,
            "failures_json": result.failures,
            "override_reason": result.override_reason,
            "checked_at": result.checked_at or datetime.now(timezone.utc).isoformat(),
        })

    def get_phase_gate(self, phase) -> Optional[dict]:
        return self._get("phase_gate", _phase_value(phase))

    def get_all_phase_gates(self) -> list:
        return self._query_pk("phase_gate")

    def upsert_batch_checkpoint(self, cp):
        sk = f"{cp.phase.value}#{cp.batch_num}"
        self._put("batch", sk, {
            "phase": cp.phase.value,
            "batch_num": cp.batch_num,
            "total_batches": cp.total_batches,
            "repos_done": cp.repos_done,
            "repos_total": cp.repos_total,
            "status": cp.status,
            "started_at": cp.started_at or datetime.now(timezone.utc).isoformat(),
            "completed_at": cp.completed_at,
        })

    def get_batch_checkpoints(self, phase) -> list:
        phase_val = _phase_value(phase)
        rows = [r for r in self._query_pk("batch") if r.get("phase") == phase_val]
        return sorted(rows, key=lambda r: r.get("batch_num", 0))

    def get_last_completed_batch(self, phase) -> int:
        done = [r.get("batch_num", -1) for r in self.get_batch_checkpoints(phase) if r.get("status") == "completed"]
        return max(done) if done else -1
