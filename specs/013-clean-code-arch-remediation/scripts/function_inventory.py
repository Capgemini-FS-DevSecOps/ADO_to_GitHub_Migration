#!/usr/bin/env python
"""Generate the Python half of the 013 function inventory (FR-001a…FR-004, FR-013).

Merges three inputs into ``inventory.json``:

1. ``ruff check <roots> --output-format json --select <R1 set> --config lint.pylint.max-args=5``
   for the mechanical tags (``missing_docstring``, ``untyped``, ``gt5_params``,
   ``mutable_default``, ``unused_param``) and the ``inconsistent_return`` proposal.
2. ``vulture <roots> --min-confidence 60`` for zero-reference candidates.
3. Its own ``ast`` pass for the rows themselves, the stale-``Args:`` and keyword-only /
   unannotated boolean proposals, the ``name_review`` proposal, and the protected
   entry-point list.

Stdlib only (``ast``, ``json``, ``subprocess``, ``pathlib``, ``re``, ``argparse``).

Usage (see contracts/artifact-schemas.md for the binding contract)::

    python specs/013-clean-code-arch-remediation/scripts/function_inventory.py \
        [--roots ado2gh services] [--package ado2gh/state] [--no-ruff] [--no-vulture] \
        [--out specs/013-clean-code-arch-remediation] [--excluded <path>] \
        [--confirm <id>:<tag> | --reject <id>:<tag>] [--rationale "…"] [--decided-by <who>] \
        [--pending]

Paths are repo-relative with forward slashes and the current working directory is the
repo root, so the output is machine-independent (FR-004).
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------------------
# Pins and rule sets (research R1, verified in T002 against the installed toolchain)
# --------------------------------------------------------------------------------------

RUFF_PIN = "0.15.17"
RUFF_SELECT = "D1,ANN,FBT001,FBT002,PLR0913,B006,ARG,RET501,RET502,RET503"
RUFF_MAX_ARGS = "lint.pylint.max-args=5"
VULTURE_MIN_CONFIDENCE = "60"

#: ruff code prefix -> mechanical tag. ``ANN101``/``ANN102`` were removed in ruff 0.8.0
#: and are never named explicitly (T002).
RUFF_TAGS = (
    ("D1", "missing_docstring"),
    ("ANN", "untyped"),
    ("PLR0913", "gt5_params"),
    ("B006", "mutable_default"),
    ("ARG", "unused_param"),
)
#: ruff codes that only *propose* a judgment tag (FR-002a).
RUFF_PROPOSALS = (
    ("FBT001", "bool_flag"),
    ("FBT002", "bool_flag"),
    ("RET501", "inconsistent_return"),
    ("RET502", "inconsistent_return"),
    ("RET503", "inconsistent_return"),
)

#: The four judgment classes of FR-002a plus the per-module proposal of FR-013.
JUDGMENT_TAGS = frozenset(
    {"name_review", "stale_docstring", "bool_flag", "inconsistent_return", "module_name_review"}
)

MODULE_TAG = "module_name_review"

#: First snake_case token accepted as a verb (or predicate/conversion prefix) by
#: ``name_review``. Deliberately generous: a false negative costs nothing, a false
#: positive costs a reviewer's minute.
VERBS = frozenset(
    """
    abort accept add aggregate allow announce append apply archive assert assign attach
    authenticate authorize await bind bootstrap build bump cache calculate call cancel
    capture cast check clean cleanup clear clone close collect commit compare compile
    compose compute configure confirm connect consume convert copy count crawl create
    decide decode decorate decrement deduplicate defer delete deny deploy derive describe
    deserialize detach detect determine disable disconnect discover dispatch dispose do
    drain drop dump emit enable encode encrypt end enforce ensure enter enumerate escape
    estimate evaluate execute exit expand expect export extend extract fail fetch fill
    filter finalize find finish fire fix flatten flush format forward gather generate get
    group guard handle has hash hydrate identify ignore import increment infer init
    initialise initialize insert inspect install instantiate invalidate invoke is issue
    iter iterate join keep kill list listen load lock log lookup main make map mark mask
    match materialize merge migrate mock monitor move must normalise normalize note notify
    obtain on open orchestrate override paginate parse partition patch pause persist pick
    ping plan poll pop populate post prepare print probe process produce promote propose
    provision prune publish pull purge push put query queue raise rate read rebuild receive
    record redact reduce refresh register reject release reload remediate remove rename
    render replace report request require reset resolve restart restore resume retry
    return revert rewrite roll rollback route run save scan schedule score seed select
    send serialise serialize serve set setup should show shutdown sign skip sleep sort
    split start stop store stream strip submit subscribe summarise summarize sync
    synchronize teardown tear test to toggle track transform translate traverse trigger
    truncate try unbind uninstall unlink unlock unregister unwrap update upgrade upload
    upsert use validate verify wait walk warn watch will with wrap write yield
    can did from as at by for if not or with_
    """.split()
)

#: Property-like decorators: a noun name is correct there, so no ``name_review``.
PROPERTY_DECORATORS = frozenset({"property", "cached_property", "setter", "getter", "deleter"})

CLI_DECORATOR_ATTRS = frozenset({"command", "group"})
ROUTE_DECORATOR_ATTRS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "websocket"}
)
ROUTE_DECORATOR_BASES = frozenset({"router", "app"})

#: Fixed roots of the reference scan (FR-003a). ``--roots`` are appended.
SCAN_DIRS = (
    "ado2gh",
    "services",
    "tests",
    "apps/migration-ui/src",
    "ado2gh/agents/migration_agent/prompts",
    "scripts",
    ".github",
    "docs",
    "in",
)
SCAN_GLOBS = (
    "docker-compose*.yml",
    "Dockerfile*",
    ".env.example",
    "*.yml",
    "*.yaml",
    "*.toml",
    "*.json",
    "*.md",
)
SCAN_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".next",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        "htmlcov",
        "specs",
    }
)
SCAN_SKIP_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".zip", ".gz", ".tar",
        ".woff", ".woff2", ".ttf", ".eot", ".db", ".sqlite", ".sqlite3", ".pyc", ".so",
        ".dll", ".exe", ".whl", ".lock", ".map",
    }
)
SCAN_MAX_BYTES = 4_000_000

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DEF_SITE_RE = re.compile(r"\b(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)")
ARGS_SECTION_RE = re.compile(r"^\s*(?:Args|Arguments|Parameters)\s*:\s*$", re.MULTILINE)
ARG_NAME_RE = re.compile(r"^\s{2,}(\*{0,2}[A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*:")
SECTION_RE = re.compile(r"^\s*[A-Z][A-Za-z ]*:\s*$")
TS_BLOCK_RE = re.compile(r"<!-- ts:begin -->.*?<!-- ts:end -->", re.DOTALL)


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def rel(path: Path) -> str:
    """Repo-relative posix path for ``path`` (the repo root is the CWD)."""
    return Path(os.path.relpath(path, Path.cwd())).as_posix()


def package_key(path: str) -> str:
    """Increment key for a repo-relative path (research R6, data-model § package)."""
    parts = path.split("/")
    if parts[0] == "ado2gh":
        return f"ado2gh/{parts[1]}" if len(parts) > 2 else "ado2gh"
    if parts[0] == "services":
        return f"services/{parts[1]}" if len(parts) > 2 else "services"
    if path.startswith("apps/migration-ui/"):
        return "apps/migration-ui"
    return posixpath.dirname(path) or parts[0]


def load_patterns(path: Path) -> list[str]:
    """Read a `#`-commented, one-entry-per-line file (exclusions, manual protections)."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def matches_pattern(path: str, pattern: str) -> bool:
    """Gitignore-ish match: a slash-less pattern matches the basename at any depth."""
    if "/" not in pattern:
        return fnmatch.fnmatchcase(posixpath.basename(path), pattern)
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(
        path, pattern.replace("/**/", "/")
    )


def first_match(path: str, patterns: list[str]) -> str | None:
    """First exclusion pattern matching ``path``, or ``None``."""
    for pattern in patterns:
        if matches_pattern(path, pattern):
            return pattern
    return None


def git_head() -> str:
    """Current commit sha, or an empty string outside a repository."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def write_json(path: Path, payload) -> None:
    """Write ``payload`` as UTF-8 JSON with a trailing newline (byte-stable, FR-004)."""
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


# --------------------------------------------------------------------------------------
# The ``ast`` pass
# --------------------------------------------------------------------------------------


def header_text(lines: list[str], node) -> str:
    """One-line source text of a ``def`` header: from ``def`` to the colon that closes it.

    The scan stops at the first ``:`` outside brackets after the parameter list, so
    single-line stubs (``def f(self) -> int: ...``) and multi-line parameter lists both
    yield the header alone. The window is capped at the first body statement's line so a
    malformed header cannot run away through the file.
    """
    start = node.lineno - 1
    window = lines[start : max(node.body[0].lineno, node.lineno)]
    depth = 0
    seen_open = False
    parts: list[str] = []
    for raw in window:
        chunk: list[str] = []
        for char in raw:
            if char in "([{":
                depth += 1
                seen_open = True
            elif char in ")]}":
                depth -= 1
            chunk.append(char)
            if char == ":" and depth <= 0 and seen_open:
                parts.append("".join(chunk))
                return re.sub(r"\s+", " ", " ".join(p.strip() for p in parts)).strip()
        parts.append(raw)
    return re.sub(r"\s+", " ", " ".join(p.strip() for p in parts)).strip()


def decorator_name(node: ast.AST) -> tuple[str, str]:
    """``(base, attribute)`` of a decorator expression, e.g. ``@router.get(…)`` -> ``("router", "get")``."""
    expr = node.func if isinstance(node, ast.Call) else node
    attr = ""
    if isinstance(expr, ast.Attribute):
        attr = expr.attr
        expr = expr.value
    elif isinstance(expr, ast.Name):
        attr = expr.id
    base = ""
    while isinstance(expr, ast.Attribute):
        expr = expr.value
    if isinstance(expr, ast.Name):
        base = expr.id
    return base, attr


def param_names(node, is_method: bool) -> list[str]:
    """Parameter names with the receiver removed; variadics count one each."""
    args = node.args
    positional = [a.arg for a in list(args.posonlyargs) + list(args.args)]
    if is_method and positional and positional[0] in ("self", "cls"):
        positional = positional[1:]
    names = list(positional)
    if args.vararg:
        names.append(args.vararg.arg)
    names.extend(a.arg for a in args.kwonlyargs)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return names


def documented_args(docstring: str) -> list[str]:
    """Parameter names listed in the Google-style ``Args:`` section of ``docstring``."""
    match = ARGS_SECTION_RE.search(docstring)
    if not match:
        return []
    names = []
    for line in docstring[match.end() :].splitlines():
        if not line.strip():
            continue
        if SECTION_RE.match(line):
            break
        found = ARG_NAME_RE.match(line)
        if found:
            names.append(found.group(1).lstrip("*"))
    return names


def bool_flag_params(node, is_method: bool) -> bool:
    """True when a parameter is a boolean switch ruff's FBT rules cannot see (R1 item 3)."""
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    if is_method and positional and positional[0].arg in ("self", "cls"):
        positional = positional[1:]
    defaults = list(args.defaults)
    padded = [None] * (len(positional) - len(defaults)) + defaults
    candidates = list(zip(positional, padded)) + list(zip(args.kwonlyargs, args.kw_defaults))
    for arg, default in candidates:
        annotation = getattr(arg, "annotation", None)
        if isinstance(annotation, ast.Name) and annotation.id == "bool":
            return True
        if isinstance(default, ast.Constant) and isinstance(default.value, bool):
            return True
    return False


def needs_name_review(name: str, decorators: list[tuple[str, str]]) -> bool:
    """True when the first token of ``name`` is not a verb (candidate, FR-002a)."""
    if name.startswith("__") and name.endswith("__"):
        return False
    if any(attr in PROPERTY_DECORATORS or base in PROPERTY_DECORATORS for base, attr in decorators):
        return False
    token = name.lstrip("_").split("_", 1)[0].lower()
    return bool(token) and token not in VERBS


def walk_module(path_rel: str, source: str) -> tuple[list[dict], dict[str, str], set[str]]:
    """Rows for one module plus the nested-def owner map and the ``__main__``-guard names.

    Returns ``(rows, nested_owner, main_guard_names)``. Only ``ast`` nodes whose parent is
    a ``Module`` or a ``ClassDef`` become rows (FR-001a); names defined inside a row map to
    that row through ``nested_owner`` so a tool finding inside a nested def is attributed
    to its enclosing row.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    rows: list[dict] = []
    nested_owner: dict[str, str] = {}

    def emit(node, class_chain: list[str]) -> None:
        is_method = bool(class_chain)
        qualname = ".".join(class_chain + [node.name])
        row_id = f"{path_rel}::{qualname}"
        decorators = [decorator_name(dec) for dec in node.decorator_list]
        names = param_names(node, is_method)
        docstring = ast.get_docstring(node, clean=True) or ""
        signature = header_text(lines, node)

        proposed = set()
        if bool_flag_params(node, is_method):
            proposed.add("bool_flag")
        listed = documented_args(docstring)
        if listed and sorted(listed) != sorted(names):
            proposed.add("stale_docstring")
        if needs_name_review(node.name, decorators):
            proposed.add("name_review")

        tags = set()
        if len(names) > 5:
            tags.add("gt5_params")

        rows.append(
            {
                "id": row_id,
                "language": "py",
                "path": path_rel,
                "line": node.lineno,
                "package": package_key(path_rel),
                "qualname": qualname,
                "signature": signature,
                "is_export": True,
                "param_count": len(names),
                "tags": tags,
                "proposed_tags": proposed,
                "state_hash": state_hash(signature, docstring),
                "reference_count": 0,
                "vulture_confidence": None,
                "protected": False,
                "disposition": "pending",
                "note": "",
                "_end_line": getattr(node, "end_lineno", node.lineno) or node.lineno,
                # Own decorators plus every nested def's: a Click command or FastAPI route
                # declared inside this function has no row of its own (FR-001a), so its
                # protection is attributed here — the same rule tools' findings follow.
                "_decorators": list(decorators),
            }
        )
        for inner in ast.walk(node):
            if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner is not node:
                nested_owner.setdefault(inner.name, row_id)
                rows[-1]["_decorators"].extend(decorator_name(dec) for dec in inner.decorator_list)

    def descend(body, class_chain: list[str]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                emit(node, class_chain)
            elif isinstance(node, ast.ClassDef):
                descend(node.body, class_chain + [node.name])

    descend(tree.body, [])

    guarded: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.If) and "__main__" in ast.dump(node.test):
            guarded.update(n.id for n in ast.walk(node) if isinstance(n, ast.Name))

    return rows, nested_owner, guarded


def state_hash(signature: str, docstring: str) -> str:
    """sha1 of the normalised signature text plus the docstring (data-model § state_hash)."""
    normalised = re.sub(r"\s+", " ", signature).strip()
    body = re.sub(r"[ \t]+", " ", docstring).strip()
    return hashlib.sha1(f"{normalised}\n{body}".encode("utf-8")).hexdigest()


def module_id(path_rel: str) -> str:
    """FR-013 proposal id: a package ``__init__.py`` is reported as its directory."""
    if posixpath.basename(path_rel) == "__init__.py":
        return posixpath.dirname(path_rel) + "/"
    return path_rel


# --------------------------------------------------------------------------------------
# External tools
# --------------------------------------------------------------------------------------


def run_tool(args: list[str]) -> subprocess.CompletedProcess | None:
    """Run ``python -m <tool>`` in the active interpreter; ``None`` when it is missing."""
    try:
        return subprocess.run(
            [sys.executable, "-m", *args], capture_output=True, text=True, check=False
        )
    except OSError:
        return None


def ruff_findings(roots: list[str]) -> tuple[list[dict], int]:
    """``ruff check`` findings as ``(findings, exit_code)``; exit 2 means the pin is wrong."""
    version = run_tool(["ruff", "--version"])
    if version is None or version.returncode != 0:
        print(
            "ruff is not installed. Install the CI pin (research R12):\n"
            f'    .venv/Scripts/python.exe -m pip install "ruff=={RUFF_PIN}"',
            file=sys.stderr,
        )
        return [], 2
    printed = version.stdout.strip().split()[-1]
    if printed != RUFF_PIN:
        print(
            f"ruff {printed} is installed but the inventory is pinned to {RUFF_PIN}; "
            f'install it with: .venv/Scripts/python.exe -m pip install "ruff=={RUFF_PIN}"',
            file=sys.stderr,
        )
        return [], 2
    done = run_tool(
        [
            "ruff",
            "check",
            *roots,
            "--output-format",
            "json",
            "--select",
            RUFF_SELECT,
            "--config",
            RUFF_MAX_ARGS,
        ]
    )
    if done is None or not done.stdout.strip():
        return [], 0
    try:
        return json.loads(done.stdout), 0
    except json.JSONDecodeError:
        print(f"ruff produced unparseable output:\n{done.stderr}", file=sys.stderr)
        return [], 2


VULTURE_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+): unused (?:function|method) '(?P<name>[^']+)' \((?P<pct>\d+)% confidence\)")


def vulture_findings(roots: list[str]) -> tuple[dict[tuple[str, int], int], int]:
    """``{(path, line): confidence}`` for unused functions/methods; exit 2 when missing."""
    done = run_tool(["vulture", *roots, "--min-confidence", VULTURE_MIN_CONFIDENCE])
    if done is None or done.returncode not in (0, 3):
        print(
            "vulture is not installed. Install it (research R12):\n"
            "    .venv/Scripts/python.exe -m pip install vulture==2.16",
            file=sys.stderr,
        )
        return {}, 2
    out: dict[tuple[str, int], int] = {}
    for line in done.stdout.splitlines():
        match = VULTURE_RE.match(line.strip())
        if match:
            path = match.group("path").replace("\\", "/")
            out[(path, int(match.group("line")))] = int(match.group("pct"))
    return out, 0


# --------------------------------------------------------------------------------------
# Reference scan (FR-003a)
# --------------------------------------------------------------------------------------


def scan_files(roots: list[str]) -> list[Path]:
    """Every text file of the reference scan, deduplicated and sorted."""
    cwd = Path.cwd()
    seen: dict[str, Path] = {}

    def add(path: Path) -> None:
        if not path.is_file():
            return
        if path.suffix.lower() in SCAN_SKIP_SUFFIXES:
            return
        try:
            if path.stat().st_size > SCAN_MAX_BYTES:
                return
        except OSError:
            return
        seen.setdefault(str(path.resolve()), path)

    for name in list(SCAN_DIRS) + roots:
        base = cwd / name
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SCAN_SKIP_DIRS)
            for filename in sorted(filenames):
                add(Path(dirpath) / filename)
    for pattern in SCAN_GLOBS:
        for path in sorted(cwd.glob(pattern)):
            add(path)
    return [seen[key] for key in sorted(seen)]


def reference_counts(roots: list[str]) -> dict[str, int]:
    """Occurrences of every identifier across the scan roots, minus its definition sites."""
    totals: dict[str, int] = {}
    definitions: dict[str, int] = {}
    for path in scan_files(roots):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for token in IDENT_RE.findall(text):
            totals[token] = totals.get(token, 0) + 1
        if path.suffix == ".py":
            for name in DEF_SITE_RE.findall(text):
                definitions[name] = definitions.get(name, 0) + 1
    return {name: max(0, count - definitions.get(name, 0)) for name, count in totals.items()}


# --------------------------------------------------------------------------------------
# Protected entry points (research R5)
# --------------------------------------------------------------------------------------


def orphan_allowlist_modules() -> set[str]:
    """Module names allowlisted in ``tests/unit/test_no_orphaned_modules.py``."""
    guard = Path("tests/unit/test_no_orphaned_modules.py")
    if not guard.exists():
        return set()
    wanted = {"_ENTRY_POINTS", "_DYNAMIC_IMPORTS", "_STANDALONE_EXECUTABLES"}
    modules: set[str] = set()
    for node in ast.walk(ast.parse(guard.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id in wanted for t in node.targets):
            continue
        for element in ast.walk(node.value):
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                modules.add(element.value)
    return modules


def module_to_paths(name: str) -> tuple[str, str]:
    """``ado2gh.cli.main`` -> the module file and the package ``__init__`` candidate."""
    base = name.replace(".", "/")
    return f"{base}.py", f"{base}/__init__.py"


def collect_protection(
    rows_by_path: dict[str, list[dict]],
    nested_owners: dict[str, dict[str, str]],
    guards: dict[str, set[str]],
    manual: list[str],
) -> dict[str, set[str]]:
    """``{inventory id: {reason, …}}`` from the eight generated sources plus the manual file."""
    protection: dict[str, set[str]] = {}

    def mark(row_id: str, reason: str) -> None:
        protection.setdefault(row_id, set()).add(reason)

    by_name_global: dict[str, set[str]] = {}
    for path, rows in rows_by_path.items():
        for row in rows:
            by_name_global.setdefault(row["qualname"].split(".")[-1], set()).add(row["id"])

    # 1/2/7 — decorators and conftest fixtures, straight off the rows.
    for path, rows in rows_by_path.items():
        is_conftest = posixpath.basename(path) == "conftest.py"
        for row in rows:
            if is_conftest:
                mark(row["id"], "pytest_fixture")
            for base, attr in row["_decorators"]:
                if attr in CLI_DECORATOR_ATTRS:
                    mark(row["id"], "cli_command")
                if attr in ROUTE_DECORATOR_ATTRS and (
                    base in ROUTE_DECORATOR_BASES
                    or base.endswith("_router")
                    or base.endswith("_app")
                ):
                    mark(row["id"], "http_route")

    # 3 — ``if __name__ == "__main__":`` blocks.
    for path, names in guards.items():
        for row in rows_by_path.get(path, []):
            if row["qualname"] in names:
                mark(row["id"], "main_guard")

    # 4 — the orphan guard's allowlists: every row in an allowlisted module.
    for name in orphan_allowlist_modules():
        for candidate in module_to_paths(name):
            for row in rows_by_path.get(candidate, []):
                mark(row["id"], "orphan_allowlist")

    # 5 — ``StructuredTool.from_function(func=/coroutine=…)`` under the agent tools package.
    tools_dir = "ado2gh/agents/migration_agent/tools/"
    for path, rows in rows_by_path.items():
        if not path.startswith(tools_dir):
            continue
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        local = {row["qualname"]: row["id"] for row in rows}
        owners = nested_owners.get(path, {})
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            _, attr = decorator_name(node)
            if attr != "from_function":
                continue
            for keyword in node.keywords:
                if keyword.arg in ("func", "coroutine") and isinstance(keyword.value, ast.Name):
                    target = keyword.value.id
                    row_id = local.get(target) or owners.get(target)
                    if row_id:
                        mark(row_id, "langchain_tool")

    # 6 — LangGraph node callables registered in the graph builder.
    builder = "ado2gh/agents/migration_agent/graph/builder.py"
    if Path(builder).exists():
        try:
            tree = ast.parse(Path(builder).read_text(encoding="utf-8"))
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                _, attr = decorator_name(node)
                if attr != "add_node":
                    continue
                for name_node in ast.walk(node):
                    if isinstance(name_node, ast.Name):
                        for row_id in by_name_global.get(name_node.id, ()):
                            mark(row_id, "graph_node")

    # 9 — hand-maintained additions.
    for row_id in manual:
        mark(row_id, "manual")

    return protection


# --------------------------------------------------------------------------------------
# Judgment-tag decisions
# --------------------------------------------------------------------------------------


def load_decisions(path: Path) -> list[dict]:
    """Read ``tag-decisions.json`` (an empty list when absent or malformed)."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def save_decisions(path: Path, decisions: list[dict]) -> None:
    """Write ``tag-decisions.json`` sorted by ``(id, tag)``."""
    write_json(path, sorted(decisions, key=lambda d: (d.get("id", ""), d.get("tag", ""))))


def apply_decisions(
    rows: list[dict], module_proposals: dict[str, str], decisions_path: Path
) -> list[dict]:
    """Promote/clear proposals per recorded decision; drop decisions with a stale hash."""
    live_hash: dict[str, str] = {row["id"]: row["state_hash"] for row in rows}
    live_hash.update(module_proposals)

    kept: list[dict] = []
    applicable: dict[tuple[str, str], dict] = {}
    for decision in load_decisions(decisions_path):
        key = (decision.get("id", ""), decision.get("tag", ""))
        current = live_hash.get(key[0])
        if current is None:
            continue  # the function or module no longer exists
        if decision.get("state_hash") != current:
            continue  # stale: dropped from the file, the proposal is re-raised
        kept.append(decision)
        applicable[key] = decision

    save_decisions(decisions_path, kept)

    for row in rows:
        for tag in sorted(row["proposed_tags"]):
            decision = applicable.get((row["id"], tag))
            if decision is None:
                continue
            verdict = decision.get("decision")
            if verdict == "escalate":
                continue  # stays pending for the operator (FR-002b)
            row["proposed_tags"].discard(tag)
            if verdict == "confirm":
                row["tags"].add(tag)
            elif verdict == "reject" and tag == "bool_flag":
                row["tags"].add("bool_data")
    return kept


# --------------------------------------------------------------------------------------
# Summary and history
# --------------------------------------------------------------------------------------


def tally(rows: list[dict]) -> dict[str, int]:
    """Counts for one row set: functions, clean, pending, and one entry per tag."""
    out = {"functions": len(rows), "clean": 0, "pending": 0}
    for row in rows:
        if row["proposed_tags"]:
            out["pending"] += 1
        elif not row["tags"]:
            out["clean"] += 1
        for tag in row["tags"]:
            out[tag] = out.get(tag, 0) + 1
        for tag in row["proposed_tags"]:
            key = f"proposed_{tag}"
            out[key] = out.get(key, 0) + 1
    return out


#: Module-level tables that hold data rather than behaviour. The inventory has one
#: row per function (data-model.md, Entry granularity), so a table produces no row.
#: T052 called out the ADO task mapping tables; they are listed in the summary so
#: their absence from the counts is deliberate and visible, exactly as an excluded
#: path would be. The modules themselves stay out of ``excluded-paths.txt``, because
#: excluding them would also hide the hand-written functions beside the tables.
DATA_TABLE_NOTE = [
    "",
    "## Data tables (FR-003b, recorded not excluded)",
    "",
    "One row per function, so a module-level table is never inventoried. These are",
    "the mapping tables that carry the ADO to GitHub Actions conversion data; they",
    "are listed here so their absence from the counts is deliberate.",
    "",
    "| Table | Module | Kind |",
    "|---|---|---|",
    "| `ADO_TASK_MAP` | `ado2gh/pipelines/transform/task_registry.py` | dict literal |",
    "| `RUN_BASED_TASKS` | `ado2gh/pipelines/transform/task_registry.py` | set derived from `ADO_TASK_MAP` |",
    "| `_POOL_RUNNER_MAP` | `ado2gh/pipelines/transform/task_registry.py` | dict literal |",
    "| `_extra` | `ado2gh/pipelines/transform/task_registry.py` | dict filled by `register_task` |",
    "| `_DOW_NAMES`, `_DOW_BITS` | `ado2gh/pipelines/transform/triggers.py` | day-of-week lookup lists |",
    "| `_SC_INPUT_KEYS`, `_SC_KEY_PATTERNS` | `ado2gh/pipelines/task_scanner.py` | service connection input key tables |",
    "| `POOL_MAP` | `ado2gh/pipelines/extractor.py` | agent pool to runner dict |",
    "",
    "The six modules under `ado2gh/pipelines/transform/` are deliberately absent from",
    "`excluded-paths.txt`: excluding them would also hide the 25 hand-written",
    "functions that sit beside the tables, which are inventoried and cleaned like any",
    "other.",
]


def render_summary(
    out_dir: Path,
    totals: dict[str, int],
    per_package: dict[str, dict[str, int]],
    excluded: dict[str, int],
    previous: dict | None,
    module_pending: list[str],
    module_decided: int,
    head: str,
) -> None:
    """Rewrite ``inventory-summary.md`` from the latest history line, keeping the TS block."""
    prev_totals = (previous or {}).get("totals", {})

    def show(key: str) -> str:
        now = totals.get(key, 0)
        if not previous:
            return str(now)
        diff = now - prev_totals.get(key, 0)
        return f"{now} ({diff:+d})" if diff else f"{now} (0)"

    tag_keys = sorted(k for k in set(totals) | set(prev_totals) if k not in ("functions", "clean", "pending"))

    lines = [
        "# Function Inventory Summary",
        "",
        "<!-- generated by specs/013-clean-code-arch-remediation/scripts/function_inventory.py -->",
        "",
        f"- git HEAD: `{head or 'unknown'}`",
        f"- functions: {show('functions')}",
        f"- clean (no tags, no proposals): {show('clean')}",
        f"- rows with open proposals: {show('pending')}",
        "",
        "## Tags",
        "",
        "| Tag | Count |",
        "|---|---:|",
    ]
    for key in tag_keys:
        lines.append(f"| `{key}` | {show(key)} |")
    lines += [
        "",
        "## Per package",
        "",
        "| Package | Functions | Clean | Pending |",
        "|---|---:|---:|---:|",
    ]
    for package in sorted(per_package):
        counts = per_package[package]
        lines.append(
            f"| `{package}` | {counts['functions']} | {counts['clean']} | {counts['pending']} |"
        )
    lines += [
        "",
        "## Excluded (FR-003b)",
        "",
        "Definitions in files matched by `excluded-paths.txt`. They produce no inventory",
        "rows; the counts are recorded here so an exclusion cannot shrink the zero-tag",
        "denominator unnoticed.",
        "",
        "| Pattern | Definitions not inventoried |",
        "|---|---:|",
    ]
    if excluded:
        for pattern in sorted(excluded):
            lines.append(f"| `{pattern}` | {excluded[pattern]} |")
    else:
        lines.append("| _(none)_ | 0 |")
    lines += DATA_TABLE_NOTE
    lines += [
        "",
        "## Module rename proposals (FR-013)",
        "",
        f"- open: {len(module_pending)}",
        f"- decided: {module_decided}",
        "",
    ]
    if module_pending:
        for module in module_pending[:50]:
            lines.append(f"- `{module}`")
        if len(module_pending) > 50:
            lines.append(f"- … and {len(module_pending) - 50} more (`--pending` lists them all)")
        lines.append("")

    body = "\n".join(lines)
    summary_path = out_dir / "inventory-summary.md"
    ts_block = "<!-- ts:begin -->\n_No TypeScript rows yet — run `function_inventory_ts.mjs`._\n<!-- ts:end -->"
    if summary_path.exists():
        found = TS_BLOCK_RE.search(summary_path.read_text(encoding="utf-8"))
        if found:
            ts_block = found.group(0)
    summary_path.write_text(body + "\n" + ts_block + "\n", encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------------------


def collect_sources(roots: list[str], exclusions: list[str]) -> tuple[list[str], dict[str, int]]:
    """Included module paths and ``{pattern: definition count}`` for excluded ones (FR-003b)."""
    included: list[str] = []
    excluded_counts: dict[str, int] = {}
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            if base.is_file() and base.suffix == ".py":
                included.append(rel(base))
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SCAN_SKIP_DIRS)
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path_rel = rel(Path(dirpath) / filename)
                pattern = first_match(path_rel, exclusions)
                if pattern is None:
                    included.append(path_rel)
                    continue
                try:
                    rows, _, _ = walk_module(path_rel, Path(path_rel).read_text(encoding="utf-8"))
                except (OSError, SyntaxError):
                    rows = []
                excluded_counts[pattern] = excluded_counts.get(pattern, 0) + len(rows)
    return sorted(set(included)), excluded_counts


def build_rows(sources: list[str]):
    """Walk every source file: rows, nested owners, ``__main__`` guards, module proposals."""
    rows_by_path: dict[str, list[dict]] = {}
    nested_owners: dict[str, dict[str, str]] = {}
    guards: dict[str, set[str]] = {}
    modules: dict[str, str] = {}
    for path_rel in sources:
        try:
            source = Path(path_rel).read_text(encoding="utf-8")
            rows, owners, guarded = walk_module(path_rel, source)
        except (OSError, SyntaxError) as exc:
            print(f"skipped {path_rel}: {exc}", file=sys.stderr)
            continue
        rows_by_path[path_rel] = rows
        nested_owners[path_rel] = owners
        guards[path_rel] = guarded
        mid = module_id(path_rel)
        modules[mid] = hashlib.sha1(mid.encode("utf-8")).hexdigest()
    return rows_by_path, nested_owners, guards, modules


def attribute_findings(rows_by_path: dict[str, list[dict]], findings: list[dict]) -> None:
    """Attach ruff findings to the innermost enclosing row (FR-001a attribution rule)."""
    index: dict[str, list[dict]] = {}
    for path, rows in rows_by_path.items():
        index[str(Path(path).resolve()).lower()] = rows
    for finding in findings:
        rows = index.get(str(Path(finding["filename"]).resolve()).lower())
        if not rows:
            continue
        line = finding["location"]["row"]
        best = None
        for row in rows:
            if row["line"] <= line <= row["_end_line"] and (best is None or row["line"] > best["line"]):
                best = row
        if best is None:
            continue
        code = finding["code"] or ""
        for prefix, tag in RUFF_TAGS:
            if code.startswith(prefix):
                best["tags"].add(tag)
        for exact, tag in RUFF_PROPOSALS:
            if code == exact:
                best["proposed_tags"].add(tag)


def generate(args, out_dir: Path) -> int:
    """Full regeneration: ast pass + ruff + vulture + reference scan + merge + summary."""
    exclusions = load_patterns(Path(args.excluded))
    sources, excluded_counts = collect_sources(args.roots, exclusions)
    rows_by_path, nested_owners, guards, module_hashes = build_rows(sources)

    if not args.no_ruff:
        findings, code = ruff_findings(args.roots)
        if code:
            return code
        attribute_findings(rows_by_path, findings)

    vulture_map: dict[tuple[str, int], int] = {}
    if not args.no_vulture:
        vulture_map, code = vulture_findings(args.roots)
        if code:
            return code

    manual = load_patterns(out_dir / "protected-entry-points.manual.txt")
    protection = collect_protection(rows_by_path, nested_owners, guards, manual)
    references = reference_counts(args.roots)

    fresh: list[dict] = []
    for path, rows in rows_by_path.items():
        for row in rows:
            row["protected"] = row["id"] in protection
            row["vulture_confidence"] = vulture_map.get((path, row["line"]))
            row["reference_count"] = references.get(row["qualname"].split(".")[-1], 0)
            if (
                row["vulture_confidence"] is not None
                and row["reference_count"] == 0
                and not row["protected"]
            ):
                row["tags"].add("dead")
            fresh.append(row)

    decisions = apply_decisions(fresh, module_hashes, out_dir / "tag-decisions.json")

    inventory_path = out_dir / "inventory.json"
    existing = []
    if inventory_path.exists():
        try:
            existing = json.loads(inventory_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = []
    carried = {row["id"]: row for row in existing}

    out_rows: list[dict] = []
    if args.package:
        selected = set(args.package)
        out_rows = [
            row
            for row in existing
            if row.get("language") == "py" and row.get("package") not in selected
        ]
        fresh = [row for row in fresh if row["package"] in selected]

    for row in fresh:
        previous = carried.get(row["id"])
        row["tags"] = sorted(row["tags"])
        row["proposed_tags"] = sorted(row["proposed_tags"])
        row.pop("_end_line", None)
        row.pop("_decorators", None)
        if previous:
            row["disposition"] = previous.get("disposition", "pending")
            row["note"] = previous.get("note", "")
            if row["disposition"] == "delete" and (row["protected"] or row["reference_count"]):
                row["disposition"] = "pending"
        out_rows.append(row)

    out_rows.extend(row for row in existing if row.get("language") == "ts")
    out_rows.sort(key=lambda row: (row["language"], row["id"]))
    write_json(inventory_path, out_rows)

    py_rows = [row for row in out_rows if row["language"] == "py"]
    totals = tally(py_rows)
    per_package: dict[str, dict[str, int]] = {}
    for package in sorted({row["package"] for row in py_rows}):
        per_package[package] = tally([row for row in py_rows if row["package"] == package])

    decided_modules = {d["id"] for d in decisions if d.get("tag") == MODULE_TAG}
    module_pending = sorted(set(module_hashes) - decided_modules)

    protected_path = out_dir / "protected-entry-points.json"
    # ``next_route_export`` rows belong to the TS walker; carry them over untouched, the
    # same way the ``ts`` inventory rows above are carried over.
    carried = [
        entry
        for entry in (json.loads(protected_path.read_text(encoding="utf-8")) if protected_path.exists() else [])
        if entry.get("reason") == "next_route_export"
    ]
    protected_entries = sorted(
        [{"id": row_id, "reason": reason} for row_id, reasons in protection.items() for reason in reasons] + carried,
        key=lambda entry: (entry["id"], entry["reason"]),
    )
    write_json(protected_path, protected_entries)

    history_path = out_dir / "inventory-history.jsonl"
    previous = None
    if history_path.exists():
        lines = [line for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines:
            previous = json.loads(lines[-1])
    head = git_head()
    entry = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_head": head,
        "totals": totals,
        "per_package": per_package,
        "excluded": {key: excluded_counts[key] for key in sorted(excluded_counts)},
    }
    with history_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=False) + "\n")

    render_summary(
        out_dir,
        totals,
        per_package,
        entry["excluded"],
        previous,
        module_pending,
        len(decided_modules),
        head,
    )
    return 0


# --------------------------------------------------------------------------------------
# --pending / --confirm / --reject
# --------------------------------------------------------------------------------------


def module_proposal_ids(args) -> dict[str, str]:
    """``{module id: state_hash}`` for every module in scope (FR-013)."""
    exclusions = load_patterns(Path(args.excluded))
    sources, _ = collect_sources(args.roots, exclusions)
    out: dict[str, str] = {}
    for path_rel in sources:
        if args.package and package_key(path_rel) not in set(args.package):
            continue
        mid = module_id(path_rel)
        out[mid] = hashlib.sha1(mid.encode("utf-8")).hexdigest()
    return out


def list_pending(args, out_dir: Path) -> int:
    """Print every open proposal (functions then modules); exit 1 while any remain."""
    inventory_path = out_dir / "inventory.json"
    if not inventory_path.exists():
        print(f"no inventory at {inventory_path}; run the generator first", file=sys.stderr)
        return 2
    rows = json.loads(inventory_path.read_text(encoding="utf-8"))
    selected = set(args.package or [])
    decided = {
        (d.get("id"), d.get("tag"))
        for d in load_decisions(out_dir / "tag-decisions.json")
        if d.get("decision") in ("confirm", "reject")
    }

    count = 0
    for row in rows:
        if selected and row.get("package") not in selected:
            continue
        for tag in row.get("proposed_tags", []):
            count += 1
            print(f"{row['id']}:{tag}")
            print(f"  signature: {row.get('signature', '')}")
            print(f"  path: {row.get('path')}:{row.get('line')}")

    for mid in sorted(module_proposal_ids(args)):
        if (mid, MODULE_TAG) in decided:
            continue
        count += 1
        print(f"{mid}:{MODULE_TAG}")
        print(f"  path: {mid}")
    return 1 if count else 0


def record_decision(spec: str, verdict: str, args, out_dir: Path) -> int:
    """Write one ``JudgmentTagDecision`` keyed by the target's current ``state_hash``."""
    if ":" not in spec:
        print(f"expected <id>:<tag>, got {spec!r}", file=sys.stderr)
        return 2
    target_id, tag = spec.rsplit(":", 1)
    if tag not in JUDGMENT_TAGS:
        print(f"{tag!r} is not a judgment tag ({', '.join(sorted(JUDGMENT_TAGS))})", file=sys.stderr)
        return 2

    if tag == MODULE_TAG:
        current = hashlib.sha1(target_id.encode("utf-8")).hexdigest()
    else:
        inventory_path = out_dir / "inventory.json"
        if not inventory_path.exists():
            print(f"no inventory at {inventory_path}; run the generator first", file=sys.stderr)
            return 2
        rows = {row["id"]: row for row in json.loads(inventory_path.read_text(encoding="utf-8"))}
        if target_id not in rows:
            print(f"{target_id} is not in the inventory", file=sys.stderr)
            return 2
        current = rows[target_id]["state_hash"]

    decisions = [
        d
        for d in load_decisions(out_dir / "tag-decisions.json")
        if (d.get("id"), d.get("tag")) != (target_id, tag)
    ]
    decisions.append(
        {
            "id": target_id,
            "tag": tag,
            "decision": verdict,
            "rationale": args.rationale or "",
            "state_hash": current,
            "decided_by": args.decided_by or "review-agent",
        }
    )
    save_decisions(out_dir / "tag-decisions.json", decisions)
    return 0


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """The CLI surface fixed by contracts/artifact-schemas.md."""
    feature_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--roots", nargs="+", default=["ado2gh", "services"])
    parser.add_argument("--package", action="append", default=None)
    parser.add_argument("--no-ruff", action="store_true")
    parser.add_argument("--no-vulture", action="store_true")
    parser.add_argument("--out", default=str(feature_dir))
    parser.add_argument("--confirm", default=None, metavar="ID:TAG")
    parser.add_argument("--reject", default=None, metavar="ID:TAG")
    parser.add_argument("--rationale", default=None)
    parser.add_argument("--decided-by", dest="decided_by", default=None)
    parser.add_argument("--pending", action="store_true")
    parser.add_argument("--excluded", default=str(feature_dir / "excluded-paths.txt"))
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the generator; returns the process exit code (0 ok, 1 pending, 2 tooling)."""
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.confirm and args.reject:
        print("--confirm and --reject are mutually exclusive", file=sys.stderr)
        return 2
    if args.confirm or args.reject:
        spec = args.confirm or args.reject
        code = record_decision(spec, "confirm" if args.confirm else "reject", args, out_dir)
        if code:
            return code
        return generate(args, out_dir)
    if args.pending:
        return list_pending(args, out_dir)
    return generate(args, out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
