"""ADO pipeline expression rewriting for GitHub Actions."""
from __future__ import annotations

import re
from typing import Any

_RE_ADO_PARAM = re.compile(r"\$\{\{\s*parameters\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_RE_ADO_MACRO = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)")


def map_condition(condition: str) -> str:
    """Translate common ADO pipeline conditions to GHA ``if:`` expressions."""
    if not condition:
        return ""

    c = condition.strip()
    c = re.sub(r"\bsucceeded\(\)", "success()", c)
    c = re.sub(r"\bfailed\(\)", "failure()", c)
    c = re.sub(r"\balways\(\)", "always()", c)
    c = re.sub(r"\bcanceled\(\)", "cancelled()", c)
    c = re.sub(r"variables\[(['\"])(.+?)\1\]", r"env.\2", c)
    c = re.sub(r"variables\.(\w+)", r"env.\1", c)
    c = re.sub(r"\beq\(", "== (", c)
    c = re.sub(r"\bne\(", "!= (", c)
    c = re.sub(r"\band\(", "&& (", c)
    c = re.sub(r"\bor\(", "|| (", c)
    c = re.sub(r"\bnot\(", "! (", c)
    c = re.sub(r"Build\.SourceBranch", "github.ref", c)
    c = re.sub(r"Build\.Reason", "github.event_name", c)
    return c


def rewrite_expression_string(s: str, env_keys: set) -> str:
    if not isinstance(s, str) or "$" not in s:
        return s
    s = _RE_ADO_PARAM.sub(r"${{ inputs.\1 }}", s)
    if env_keys:
        def _macro(m: re.Match) -> str:
            name = m.group(1)
            return f"${{{{ env.{name} }}}}" if name in env_keys else m.group(0)
        s = _RE_ADO_MACRO.sub(_macro, s)
    return s


def rewrite_expressions_inplace(obj: Any, env_keys: set) -> None:
    """Walk a workflow dict in place, rewriting ADO expressions."""
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, str):
                obj[k] = rewrite_expression_string(v, env_keys)
            else:
                rewrite_expressions_inplace(v, env_keys)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = rewrite_expression_string(v, env_keys)
            else:
                rewrite_expressions_inplace(v, env_keys)
