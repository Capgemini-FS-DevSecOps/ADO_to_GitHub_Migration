"""End-to-end agent migration test via API.

Tests dry-run and live migration through the agent SSE streaming endpoint.
Usage: python scripts/dev/test_agent_e2e.py
"""
import asyncio
import json
import sys
import time
import httpx

AGENT = "http://localhost:8090"
ACCEL = "http://localhost:8080"

REPOS = [
    "azure-pipelines/azure-pipelines-script-migration",
    "azure-pipelines/bicep-template-migration",
    "azure-pipelines/azure-pipelines-build-migration",
]


async def create_session(profile_id: str = "lightweight") -> dict:
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{AGENT}/v1/sessions", json={"profile_id": profile_id, "prompt": ""})
        r.raise_for_status()
        return r.json()


async def stream_message(session_id: str, message: str, timeout: float = 120) -> list[dict]:
    """Send a message via SSE streaming and collect all events."""
    events = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as c:
        async with c.stream(
            "POST",
            f"{AGENT}/v1/sessions/{session_id}/message-stream",
            json={"message": message},
        ) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                try:
                    evt = json.loads(data)
                    events.append(evt)
                    kind = evt.get("kind", "")
                    content = evt.get("content", "")[:120]
                    subagent = evt.get("subagent", "")
                    if kind == "__done__":
                        print(f"  [DONE] reply={evt.get('reply', '')[:120]}")
                        break
                    elif kind in ("thinking", "status", "tool_call", "tool_result", "form_request", "message"):
                        print(f"  [{kind}] {subagent}: {content}")
                except json.JSONDecodeError:
                    pass
    return events


async def submit_form_stream(session_id: str, form_id: str, values: dict, timeout: float = 120) -> list[dict]:
    """Submit a form via SSE streaming and collect all events."""
    events = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as c:
        async with c.stream(
            "POST",
            f"{AGENT}/v1/sessions/{session_id}/form-submit-stream",
            json={"form_id": form_id, "values": values},
        ) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                try:
                    evt = json.loads(data)
                    events.append(evt)
                    kind = evt.get("kind", "")
                    content = evt.get("content", "")[:120]
                    subagent = evt.get("subagent", "")
                    if kind == "__done__":
                        print(f"  [DONE] reply={evt.get('reply', '')[:120]}")
                        break
                    elif kind in ("thinking", "status", "tool_call", "tool_result", "form_request", "message"):
                        print(f"  [{kind}] {subagent}: {content}")
                except json.JSONDecodeError:
                    pass
    return events


async def get_session(session_id: str) -> dict:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(f"{AGENT}/v1/sessions/{session_id}")
                r.raise_for_status()
                return r.json()
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(2)
            else:
                raise


async def get_pipeline_runs() -> dict:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(f"{ACCEL}/v1/pipeline/runs?limit=10")
                r.raise_for_status()
                return r.json()
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(2)
            else:
                return {"runs": []}


async def delete_pipeline_run(run_id: str):
    async with httpx.AsyncClient() as c:
        try:
            await c.post(f"{ACCEL}/v1/pipeline/runs/{run_id}/cancel")
        except Exception:
            pass


def find_form(events: list[dict]) -> dict | None:
    for evt in events:
        if evt.get("kind") == "form_request" and evt.get("meta"):
            return evt["meta"]
    return None


async def test_migration(repo: str, live: bool = False) -> bool:
    mode = "live" if live else "dry-run"
    print(f"\n{'='*60}")
    print(f"TEST: {repo} ({mode})")
    print(f"{'='*60}")

    # Create session
    session = await create_session()
    session_id = session["session_id"]
    print(f"Session: {session_id}")

    # Send initial message
    msg = f"migrate repo {repo} {'live' if live else 'dry-run'}"
    print(f"\n>>> {msg}")
    events = await stream_message(session_id, msg)

    # Check for form requests
    form = find_form(events)
    if form:
        form_id = form.get("form_id", form.get("id", ""))
        print(f"\n  Form received: {form_id}")
        if form_id == "repo_selection":
            print(f"  Submitting repo_selection: {repo}")
            events2 = await submit_form_stream(session_id, "repo_selection", {"repository_id": repo})
            events.extend(events2)
            form2 = find_form(events2)
            if form2:
                form_id2 = form2.get("form_id", form2.get("id", ""))
                if form_id2 == "execution_mode":
                    mode_val = "live" if live else "dry-run"
                    print(f"  Submitting execution_mode: {mode_val}")
                    events3 = await submit_form_stream(session_id, "execution_mode", {"mode": mode_val})
                    events.extend(events3)
        elif form_id == "execution_mode":
            mode_val = "live" if live else "dry-run"
            print(f"  Submitting execution_mode: {mode_val}")
            events2 = await submit_form_stream(session_id, "execution_mode", {"mode": mode_val})
            events.extend(events2)

    # Get final session state
    final = await get_session(session_id)
    status = final.get("status", "")
    dry_run = final.get("dry_run", True)
    run_id = final.get("run_id")
    pending_form = final.get("pending_form")
    messages = final.get("messages", [])

    print(f"\n  Final status: {status}")
    print(f"  Dry run: {dry_run}")
    print(f"  Run ID: {run_id}")
    print(f"  Pending form: {pending_form is not None}")
    print(f"  Messages: {len(messages)}")

    # Check for errors in messages
    errors = [m for m in messages if "error" in m.get("content", "").lower() or "Error" in m.get("content", "")]
    if errors:
        print(f"  ERRORS FOUND:")
        for e in errors[-3:]:
            print(f"    - {e.get('content', '')[:200]}")

    # Check if migration completed
    assistant_msgs = [m for m in messages if m.get("role") == "assistant" and m.get("content")]
    if assistant_msgs:
        print(f"  Last assistant: {assistant_msgs[-1]['content'][:200]}")

    # Check pipeline runs
    runs = await get_pipeline_runs()
    agent_runs = [r for r in runs.get("runs", []) if r.get("name", "").startswith("Agent:")]
    if agent_runs:
        print(f"\n  Pipeline runs from agent:")
        for r in agent_runs[:3]:
            print(f"    - {r['id']}: {r['name']} status={r['status']} dry_run={r.get('dry_run')}")

    success = status in ("completed", "idle") and not pending_form and not errors
    print(f"\n  RESULT: {'PASS' if success else 'FAIL'}")
    return success


async def main():
    # Clean up stale repo locks from previous test runs
    import sqlite3
    import os
    db_path = os.path.join(os.path.dirname(__file__), "..", "..", "migration_state.db")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("UPDATE repo_locks SET released_at=datetime('now') WHERE released_at IS NULL")
            conn.commit()
            conn.close()
            print("Cleaned up stale repo locks.")
        except Exception:
            pass

    results = []
    for repo in REPOS:
        # Test dry-run
        ok = await test_migration(repo, live=False)
        results.append((repo, "dry-run", ok))
        await asyncio.sleep(2)

    # Test one live migration
    ok = await test_migration(REPOS[0], live=True)
    results.append((REPOS[0], "live", ok))

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for repo, mode, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'} - {repo} ({mode})")


if __name__ == "__main__":
    asyncio.run(main())
