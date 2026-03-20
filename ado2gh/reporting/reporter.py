"""Rich-based reporting for ADO-to-GitHub migration status."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.panel import Panel
from rich.table import Table

from ado2gh.logging_config import console, log
from ado2gh.state.db import StateDB


_STATUS_COLOURS = {
    "completed":   "green",
    "in_progress": "yellow",
    "pending":     "dim",
    "failed":      "bold red",
    "skipped":     "cyan",
    "rolled_back": "magenta",
}

_COMPLEXITY_COLOURS = {
    "simple":  "green",
    "medium":  "yellow",
    "complex": "bold red",
}


def _colour(status: str, palette: dict) -> str:
    colour = palette.get(status, "white")
    return f"[{colour}]{status}[/{colour}]"


class Reporter:
    """Console and HTML reporting for the migration tool."""

    def __init__(self, db: StateDB):
        self.db = db

    # ── Console: repo migrations ─────────────────────────────────────────

    def print_wave_status(self, wave_id: int) -> None:
        """Print a rich table showing repo migration status for a wave."""
        migrations = self.db.get_wave_migrations(wave_id)
        if not migrations:
            console.print(f"[dim]No migrations found for wave {wave_id}.[/dim]")
            return

        table = Table(
            title=f"Wave {wave_id} - Repo Migrations",
            box=ROUNDED,
            show_lines=True,
            title_style="bold cyan",
        )
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("ADO Project", style="bold")
        table.add_column("ADO Repo")
        table.add_column("GH Org/Repo")
        table.add_column("Scope")
        table.add_column("Status", justify="center")
        table.add_column("Started", justify="center")
        table.add_column("Completed", justify="center")
        table.add_column("Error", max_width=40)

        for i, m in enumerate(migrations, 1):
            status_str = _colour(m["status"], _STATUS_COLOURS)
            started = _fmt_ts(m.get("started_at"))
            completed = _fmt_ts(m.get("completed_at"))
            error = (m.get("error_message") or "")[:40]
            table.add_row(
                str(i),
                m["ado_project"],
                m["ado_repo"],
                f"{m['gh_org']}/{m['gh_repo']}",
                m["scope"],
                status_str,
                started,
                completed,
                f"[red]{error}[/red]" if error else "",
            )

        summary = self.db.wave_summary(wave_id)
        summary_parts = []
        for scope, counts in sorted(summary.items()):
            total = sum(counts.values())
            done = counts.get("completed", 0)
            failed = counts.get("failed", 0)
            summary_parts.append(f"{scope}: {done}/{total} done, {failed} failed")

        console.print(table)
        if summary_parts:
            console.print(
                Panel(
                    " | ".join(summary_parts),
                    title="Summary",
                    border_style="cyan",
                    box=ROUNDED,
                )
            )

    # ── Console: pipeline migrations ─────────────────────────────────────

    def print_pipeline_status(self, wave_id: int) -> None:
        """Print a rich table showing pipeline migration status with complexity colours."""
        pipelines = self.db.get_wave_pipeline_migrations(wave_id)
        if not pipelines:
            console.print(f"[dim]No pipeline migrations found for wave {wave_id}.[/dim]")
            return

        table = Table(
            title=f"Wave {wave_id} - Pipeline Migrations",
            box=ROUNDED,
            show_lines=True,
            title_style="bold magenta",
        )
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("Pipeline", style="bold")
        table.add_column("Project")
        table.add_column("Repo")
        table.add_column("GH Org/Repo")
        table.add_column("Complexity", justify="center")
        table.add_column("Status", justify="center")
        table.add_column("Workflow File")
        table.add_column("Warnings", justify="right")
        table.add_column("Error", max_width=35)

        for i, p in enumerate(pipelines, 1):
            status_str = _colour(p["status"], _STATUS_COLOURS)
            complexity_str = _colour(p.get("complexity") or "simple", _COMPLEXITY_COLOURS)
            warnings_count = _count_json_list(p.get("warnings"))
            error = (p.get("error_message") or "")[:35]
            table.add_row(
                str(i),
                p["pipeline_name"],
                p["project"],
                p["repo_name"],
                f"{p['gh_org']}/{p['gh_repo']}",
                complexity_str,
                status_str,
                p.get("workflow_file") or "",
                str(warnings_count) if warnings_count else "",
                f"[red]{error}[/red]" if error else "",
            )

        summary = self.db.pipeline_migration_summary(wave_id)
        by_status = summary.get("by_status", {})
        by_complexity = summary.get("by_complexity", {})
        total = sum(by_status.values())
        done = by_status.get("completed", 0)
        failed = by_status.get("failed", 0)

        console.print(table)
        console.print(
            Panel(
                f"Total: {total} | Completed: [green]{done}[/green] | "
                f"Failed: [red]{failed}[/red] | "
                f"Simple: {by_complexity.get('simple', 0)} | "
                f"Medium: {by_complexity.get('medium', 0)} | "
                f"Complex: {by_complexity.get('complex', 0)}",
                title="Pipeline Summary",
                border_style="magenta",
                box=ROUNDED,
            )
        )

    # ── Console: all-waves summary ───────────────────────────────────────

    def print_all_status(self) -> None:
        """Print a summary table across all waves."""
        all_migrations = self.db.get_all_migrations()
        if not all_migrations:
            console.print("[dim]No migrations recorded.[/dim]")
            return

        # Group by wave
        waves: dict[int, dict] = {}
        for m in all_migrations:
            wid = m["wave_id"]
            if wid not in waves:
                waves[wid] = {"total": 0, "completed": 0, "failed": 0, "in_progress": 0, "pending": 0}
            waves[wid]["total"] += 1
            status = m["status"]
            if status in waves[wid]:
                waves[wid][status] += 1

        table = Table(
            title="All Waves - Migration Summary",
            box=SIMPLE_HEAVY,
            title_style="bold white",
        )
        table.add_column("Wave", style="bold cyan", justify="center")
        table.add_column("Total", justify="right")
        table.add_column("Completed", justify="right", style="green")
        table.add_column("Failed", justify="right", style="red")
        table.add_column("In Progress", justify="right", style="yellow")
        table.add_column("Pending", justify="right", style="dim")
        table.add_column("Success %", justify="right")

        grand = {"total": 0, "completed": 0, "failed": 0, "in_progress": 0, "pending": 0}
        for wid in sorted(waves):
            w = waves[wid]
            pct = (w["completed"] / w["total"] * 100) if w["total"] else 0
            pct_colour = "green" if pct >= 95 else ("yellow" if pct >= 80 else "red")
            table.add_row(
                str(wid),
                str(w["total"]),
                str(w["completed"]),
                str(w["failed"]),
                str(w["in_progress"]),
                str(w["pending"]),
                f"[{pct_colour}]{pct:.1f}%[/{pct_colour}]",
            )
            for k in grand:
                grand[k] += w[k]

        # Grand total row
        grand_pct = (grand["completed"] / grand["total"] * 100) if grand["total"] else 0
        grand_colour = "green" if grand_pct >= 95 else ("yellow" if grand_pct >= 80 else "red")
        table.add_section()
        table.add_row(
            "TOTAL",
            str(grand["total"]),
            str(grand["completed"]),
            str(grand["failed"]),
            str(grand["in_progress"]),
            str(grand["pending"]),
            f"[bold {grand_colour}]{grand_pct:.1f}%[/bold {grand_colour}]",
        )

        console.print(table)

    # ── HTML report ──────────────────────────────────────────────────────

    def generate_html(self, output_path: str) -> str:
        """Generate a full HTML report with tabbed repos/pipelines view and dark theme."""
        all_migrations = self.db.get_all_migrations()
        all_pipelines = []
        # Gather pipeline migrations across all waves
        wave_ids = sorted({m["wave_id"] for m in all_migrations})
        for wid in wave_ids:
            all_pipelines.extend(self.db.get_wave_pipeline_migrations(wid))

        repo_rows = _html_repo_rows(all_migrations)
        pipeline_rows = _html_pipeline_rows(all_pipelines)

        # Counts for header
        total_repos = len(all_migrations)
        done_repos = sum(1 for m in all_migrations if m["status"] == "completed")
        failed_repos = sum(1 for m in all_migrations if m["status"] == "failed")
        total_pipes = len(all_pipelines)
        done_pipes = sum(1 for p in all_pipelines if p["status"] == "completed")
        failed_pipes = sum(1 for p in all_pipelines if p["status"] == "failed")

        html = _HTML_TEMPLATE.format(
            generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            total_repos=total_repos,
            done_repos=done_repos,
            failed_repos=failed_repos,
            total_pipes=total_pipes,
            done_pipes=done_pipes,
            failed_pipes=failed_pipes,
            repo_rows=repo_rows,
            pipeline_rows=pipeline_rows,
        )

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        log.info("HTML report written to %s", output_path)
        return str(out.resolve())


def _fmt_ts(ts: Optional[str]) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return ts[:16] if len(ts) > 16 else ts


def _count_json_list(raw: Optional[str]) -> int:
    if not raw:
        return 0
    try:
        import json
        data = json.loads(raw)
        return len(data) if isinstance(data, list) else 0
    except Exception:
        return 0


def _html_status_badge(status: str) -> str:
    colours = {
        "completed": "#22c55e", "in_progress": "#eab308",
        "pending": "#6b7280", "failed": "#ef4444",
        "skipped": "#06b6d4", "rolled_back": "#a855f7",
    }
    bg = colours.get(status, "#6b7280")
    return (f'<span style="background:{bg};color:#fff;padding:2px 8px;'
            f'border-radius:4px;font-size:0.85em;">{status}</span>')


def _html_complexity_badge(complexity: str) -> str:
    colours = {"simple": "#22c55e", "medium": "#eab308", "complex": "#ef4444"}
    bg = colours.get(complexity, "#6b7280")
    return (f'<span style="background:{bg};color:#fff;padding:2px 8px;'
            f'border-radius:4px;font-size:0.85em;">{complexity}</span>')


def _html_repo_rows(migrations: list[dict]) -> str:
    rows = []
    for m in migrations:
        error = (m.get("error_message") or "")[:60]
        rows.append(
            f"<tr>"
            f"<td>{m['wave_id']}</td>"
            f"<td>{m['ado_project']}</td>"
            f"<td>{m['ado_repo']}</td>"
            f"<td>{m['gh_org']}/{m['gh_repo']}</td>"
            f"<td>{m['scope']}</td>"
            f"<td>{_html_status_badge(m['status'])}</td>"
            f"<td>{_fmt_ts(m.get('started_at'))}</td>"
            f"<td>{_fmt_ts(m.get('completed_at'))}</td>"
            f"<td class='err'>{error}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


def _html_pipeline_rows(pipelines: list[dict]) -> str:
    rows = []
    for p in pipelines:
        error = (p.get("error_message") or "")[:60]
        rows.append(
            f"<tr>"
            f"<td>{p['wave_id']}</td>"
            f"<td>{p['pipeline_name']}</td>"
            f"<td>{p['project']}</td>"
            f"<td>{p['repo_name']}</td>"
            f"<td>{p['gh_org']}/{p['gh_repo']}</td>"
            f"<td>{_html_complexity_badge(p.get('complexity') or 'simple')}</td>"
            f"<td>{_html_status_badge(p['status'])}</td>"
            f"<td>{p.get('workflow_file') or ''}</td>"
            f"<td class='err'>{error}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ADO2GH Migration Report</title>
<style>
  :root {{
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #c9d1d9; --text-muted: #8b949e; --accent: #58a6ff;
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:
         -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
         padding:24px; }}
  h1 {{ color:var(--accent); margin-bottom:8px; }}
  .meta {{ color:var(--text-muted); margin-bottom:20px; font-size:0.9em; }}
  .cards {{ display:flex; gap:16px; margin-bottom:24px; flex-wrap:wrap; }}
  .card {{ background:var(--surface); border:1px solid var(--border);
           border-radius:8px; padding:16px 24px; min-width:180px; }}
  .card h3 {{ color:var(--text-muted); font-size:0.8em; text-transform:uppercase;
              letter-spacing:0.05em; }}
  .card .num {{ font-size:2em; font-weight:700; }}
  .card .num.green {{ color:#22c55e; }} .card .num.red {{ color:#ef4444; }}
  .tabs {{ display:flex; gap:0; margin-bottom:0; }}
  .tab {{ padding:10px 24px; cursor:pointer; background:var(--surface);
          border:1px solid var(--border); border-bottom:none; border-radius:8px 8px 0 0;
          color:var(--text-muted); font-weight:600; }}
  .tab.active {{ background:var(--bg); color:var(--accent);
                 border-bottom:2px solid var(--bg); }}
  .tab-content {{ display:none; }}
  .tab-content.active {{ display:block; }}
  .table-wrap {{ overflow-x:auto; border:1px solid var(--border);
                 border-radius:0 8px 8px 8px; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.88em; }}
  th {{ background:var(--surface); color:var(--accent); text-align:left;
       padding:10px 12px; position:sticky; top:0; }}
  td {{ padding:8px 12px; border-top:1px solid var(--border); }}
  tr:hover td {{ background:rgba(88,166,255,0.04); }}
  .err {{ color:#f87171; font-size:0.82em; max-width:300px;
          overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
</style>
</head>
<body>
<h1>ADO2GH Migration Report</h1>
<p class="meta">Generated {generated_at}</p>

<div class="cards">
  <div class="card"><h3>Repo Migrations</h3><div class="num">{total_repos}</div></div>
  <div class="card"><h3>Repos Completed</h3><div class="num green">{done_repos}</div></div>
  <div class="card"><h3>Repos Failed</h3><div class="num red">{failed_repos}</div></div>
  <div class="card"><h3>Pipelines</h3><div class="num">{total_pipes}</div></div>
  <div class="card"><h3>Pipelines Completed</h3><div class="num green">{done_pipes}</div></div>
  <div class="card"><h3>Pipelines Failed</h3><div class="num red">{failed_pipes}</div></div>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('repos')">Repositories</div>
  <div class="tab" onclick="switchTab('pipelines')">Pipelines</div>
</div>

<div id="repos" class="tab-content active">
<div class="table-wrap">
<table>
<thead>
  <tr><th>Wave</th><th>ADO Project</th><th>ADO Repo</th><th>GH Org/Repo</th>
      <th>Scope</th><th>Status</th><th>Started</th><th>Completed</th><th>Error</th></tr>
</thead>
<tbody>
{repo_rows}
</tbody>
</table>
</div>
</div>

<div id="pipelines" class="tab-content">
<div class="table-wrap">
<table>
<thead>
  <tr><th>Wave</th><th>Pipeline</th><th>Project</th><th>Repo</th><th>GH Org/Repo</th>
      <th>Complexity</th><th>Status</th><th>Workflow</th><th>Error</th></tr>
</thead>
<tbody>
{pipeline_rows}
</tbody>
</table>
</div>
</div>

<script>
function switchTab(id) {{
  document.querySelectorAll('.tab-content').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(e => e.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  event.target.classList.add('active');
}}
</script>
</body>
</html>
"""
