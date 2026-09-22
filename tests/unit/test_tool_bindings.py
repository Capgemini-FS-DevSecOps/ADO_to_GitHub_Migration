"""Unit tests for tool bindings — planner tools, role-based access, bind_tools."""
import pytest
from unittest.mock import MagicMock

from ado2gh.agents.migration_agent.tools.planner_tools import get_planner_tools
from ado2gh.agents.migration_agent.tools.executor_tools import get_executor_tools
from ado2gh.agents.migration_agent.tools.validator_tools import get_validator_tools
from ado2gh.agents.migration_agent.tools.orchestrator_tools import get_orchestrator_tools


def test_planner_tools_list_not_empty():
    tools = get_planner_tools()
    assert len(tools) >= 4


def test_executor_tool_names():
    tools = get_executor_tools()
    names = {t.name for t in tools}
    assert "github_api" in names
    assert "github_api_query" not in names
    assert "ado_api_query" in names


def test_planner_tool_names():
    tools = get_planner_tools()
    names = {t.name for t in tools}
    assert "get_current_profile" in names
    assert "ado_api_query" in names
    assert "github_api_query" in names


def test_all_agent_tools_include_get_current_profile():
    for getter in (get_planner_tools, get_executor_tools, get_validator_tools, get_orchestrator_tools):
        names = {t.name for t in getter()}
        assert "get_current_profile" in names


def test_planner_tools_are_structured():
    from langchain_core.tools import StructuredTool
    tools = get_planner_tools()
    for tool in tools:
        assert isinstance(tool, StructuredTool)


def test_planner_tools_have_descriptions():
    tools = get_planner_tools()
    for tool in tools:
        assert tool.description
        assert len(tool.description) > 10


def test_planner_tools_have_args_schema():
    tools = get_planner_tools()
    for tool in tools:
        assert tool.args_schema is not None


@pytest.mark.asyncio
async def test_get_current_profile_no_accel():
    tools = get_planner_tools(accel_get=None)
    profile_tool = next(t for t in tools if t.name == "get_current_profile")
    result = await profile_tool.ainvoke({})
    assert result["error"] == "accelerator_unavailable"


@pytest.mark.asyncio
async def test_get_current_profile_with_accel():
    async def mock_get(url, session_token=None):
        if url == "/v1/settings":
            return {
                "active_profile_id": "prof-1",
                "migration_profiles": [
                    {
                        "id": "prof-1",
                        "name": "Dev",
                        "ado_org_url": "https://dev.azure.com/acme",
                        "gh_org": "acme-github",
                    }
                ],
            }
        return {}

    session = {"profile_id": "lightweight", "dry_run": True}
    tools = get_planner_tools(
        accel_get=mock_get,
        session_token="tok",
        session_getter=lambda: session,
    )
    profile_tool = next(t for t in tools if t.name == "get_current_profile")
    result = await profile_tool.ainvoke({})
    assert result["migration_profile_id"] == "prof-1"
    assert result["api_access"]["ado_org_url"] == "https://dev.azure.com/acme"
    assert result["api_access"]["gh_org"] == "acme-github"
    assert result["dry_run"] is True


def test_bind_tools_integration():
    """Test that bind_tools can be called on a mock LLM."""
    class FakeLLM:
        def bind_tools(self, tools):
            self._bound_tools = tools
            return self

    llm = FakeLLM()
    tools = get_planner_tools()
    llm_with_tools = llm.bind_tools(tools)
    assert hasattr(llm_with_tools, "_bound_tools")
    assert len(llm_with_tools._bound_tools) >= 4


# --- R10b threat-model remediations -----------------------------------------
# THR-05-001 path traversal, THR-05-002 query encoding, THR-02-003 raw
# exception text, THR-06-007 mutating default, THR-01-002 unbounded file
# content. See specs/013-clean-code-arch-remediation/threat-model-2026-09-13-001.md


def _recorder():
    seen = []

    async def accel_get(path, session_token=None):
        seen.append(path)
        return {"ok": True}

    return seen, accel_get


def test_join_api_path_keeps_a_legitimate_endpoint_byte_for_byte():
    from ado2gh.agents.migration_agent.tools.shared_tools import join_api_path

    assert join_api_path("/v1/ado", "projects/P/repos/R") == "/v1/ado/projects/P/repos/R"
    assert join_api_path("/v1/ado", "/projects/P") == "/v1/ado/projects/P"
    assert join_api_path("/v1/github", "repos/o/r?ref=main") == "/v1/github/repos/o/r?ref=main"
    assert join_api_path("/v1/ado", "") == "/v1/ado/"


@pytest.mark.parametrize(
    "endpoint",
    [
        "../../v1/migrate/git-mirror",
        "/../v1/migrate/git-mirror",
        "..%2f..%2fv1/migrate/git-mirror",
        "./a/../../b",
        r"..\..\v1\migrate",
        "../v1/adox/evil",
        "x" * 4000,
    ],
)
def test_join_api_path_rejects_prefix_escapes(endpoint):
    from ado2gh.agents.migration_agent.tools.shared_tools import ApiPathError, join_api_path

    with pytest.raises(ApiPathError):
        join_api_path("/v1/ado", endpoint)


@pytest.mark.asyncio
async def test_ado_api_query_never_leaves_the_ado_prefix():
    seen, accel_get = _recorder()
    tools = get_executor_tools({"accel_get": accel_get})
    tool = next(t for t in tools if t.name == "ado_api_query")

    result = await tool.ainvoke({"endpoint": "../../v1/migrate/git-mirror"})

    assert result["error"] == "ApiPathError"
    assert seen == []


@pytest.mark.asyncio
async def test_planner_and_validator_queries_never_leave_their_prefix():
    for getter, name in (
        (get_planner_tools, "github_api_query"),
        (get_validator_tools, "github_api_query"),
        (get_planner_tools, "ado_api_query"),
        (get_validator_tools, "ado_api_query"),
    ):
        seen, accel_get = _recorder()
        tool = next(t for t in getter(accel_get=accel_get) if t.name == name)
        result = await tool.ainvoke({"endpoint": "../../v1/migrate/git-mirror"})
        assert result["error"] == "ApiPathError", (getter, name)
        assert seen == []


@pytest.mark.asyncio
async def test_tool_error_keeps_the_raw_exception_out_of_the_result():
    async def boom(path, session_token=None):
        raise RuntimeError("GET https://accel/v1/ado/x?pat=abcdefghijklmnop failed")

    tools = get_planner_tools(accel_get=boom)
    tool = next(t for t in tools if t.name == "ado_api_query")

    result = await tool.ainvoke({"endpoint": "projects/P"})

    assert result["error"] == "RuntimeError"
    assert "abcdefghijklmnop" not in result["detail"]
    assert "***" in result["detail"]


@pytest.mark.asyncio
async def test_tool_error_reports_the_http_status_code():
    class Resp:
        status_code = 503

    class Failed(Exception):
        response = Resp()

    async def boom(path, session_token=None):
        raise Failed("upstream down")

    tools = get_planner_tools(accel_get=boom)
    tool = next(t for t in tools if t.name == "ado_api_query")

    result = await tool.ainvoke({"endpoint": "projects/P"})

    assert result["error"] == "Failed"
    assert result["status_code"] == 503


@pytest.mark.asyncio
async def test_list_github_workflows_encodes_the_ref():
    seen, accel_get = _recorder()
    tools = get_validator_tools(accel_get=accel_get)
    tool = next(t for t in tools if t.name == "list_github_workflows")

    await tool.ainvoke({"github_org": "o", "github_repo": "r", "ref": "main&admin=1#x"})

    assert seen == ["/v1/github/repos/o/r/contents/.github/workflows?ref=main%26admin%3D1%23x"]


@pytest.mark.asyncio
async def test_fetch_github_workflow_encodes_ref_and_path():
    seen = []

    async def accel_get(path, session_token=None):
        seen.append(path)
        return {"encoding": "base64", "content": ""}

    tools = get_validator_tools(accel_get=accel_get)
    tool = next(t for t in tools if t.name == "fetch_github_workflow")

    await tool.ainvoke({
        "github_org": "o",
        "github_repo": "r",
        "workflow_path": ".github/workflows/ci.yml?x=1",
        "ref": "main&y=2",
    })

    assert seen == [
        "/v1/github/repos/o/r/contents/.github/workflows/ci.yml%3Fx%3D1?ref=main%26y%3D2"
    ]


@pytest.mark.asyncio
async def test_fetch_github_workflow_caps_repository_file_content():
    import base64

    from ado2gh.agents.migration_agent.tools.validator_tools import MAX_WORKFLOW_CONTENT_CHARS

    payload = base64.b64encode(b"A" * (MAX_WORKFLOW_CONTENT_CHARS + 5000)).decode()

    async def accel_get(path, session_token=None):
        return {"encoding": "base64", "content": payload}

    tools = get_validator_tools(accel_get=accel_get)
    tool = next(t for t in tools if t.name == "fetch_github_workflow")

    result = await tool.ainvoke({
        "github_org": "o",
        "github_repo": "r",
        "workflow_path": ".github/workflows/ci.yml",
    })

    assert len(result["content"]) == MAX_WORKFLOW_CONTENT_CHARS
    assert result["truncated"] is True
    assert result["size"] == MAX_WORKFLOW_CONTENT_CHARS + 5000


@pytest.mark.asyncio
async def test_executor_github_api_never_leaves_the_github_prefix():
    """THR-05-001: the guardrail authorises repository_id, the URL must match it."""
    seen, accel_get = _recorder()
    tools = get_executor_tools({"accel_get": accel_get})
    tool = next(t for t in tools if t.name == "github_api")

    result = await tool.ainvoke({
        "endpoint": "../../v1/migrate/git-mirror",
        "method": "GET",
        "repository_id": "P/R",
    })

    assert result["error"] in {"ApiPathError", "guardrail_blocked"}
    assert seen == []


@pytest.mark.asyncio
async def test_tool_error_drops_url_userinfo_and_query_credentials():
    """redact_text names secret shapes; a URL can carry one it does not name."""
    async def boom(path, session_token=None):
        raise RuntimeError(
            "GET https://alice:hunter2@accel/v1/ado/x?sig=Zm9vYmFyYmF6 - connect failed"
        )

    tools = get_planner_tools(accel_get=boom)
    tool = next(t for t in tools if t.name == "ado_api_query")

    result = await tool.ainvoke({"endpoint": "projects/P"})

    assert "hunter2" not in result["detail"]
    assert "Zm9vYmFyYmF6" not in result["detail"]
    assert "/v1/ado/x" in result["detail"]


# --- THR-06-007: call_accelerator defaulted to the mutating verb --------------


def test_call_accelerator_schema_defaults_to_get():
    """The schema the model sees is what actually supplies the default."""
    from ado2gh.agents.migration_agent.tools.orchestrator_tools import CallAcceleratorArgs

    assert CallAcceleratorArgs(endpoint="/v1/migrate/git-mirror").method == "GET"


@pytest.mark.asyncio
async def test_call_accelerator_without_a_method_does_not_write():
    posted = []
    seen, accel_get = _recorder()

    async def accel_post(path, body, session_token=None):
        posted.append(path)
        return {"ok": True}

    tools = get_executor_tools({"accel_get": accel_get, "accel_post": accel_post})
    tool = next(t for t in tools if t.name == "call_accelerator")

    await tool.ainvoke({"endpoint": "/v1/migrate/git-mirror"})

    assert posted == []
    assert seen == ["/v1/migrate/git-mirror"]


# --- Second-review findings (fragment traversal, redact-before-truncate, CA-004)


@pytest.mark.parametrize("endpoint", ["..#", "..#junk", "x/../..#j", "..%23"])
def test_join_api_path_rejects_fragment_assisted_traversal(endpoint):
    """A `#` segment absorbs a `..` in the probe that httpx drops from the wire."""
    from ado2gh.agents.migration_agent.tools.shared_tools import ApiPathError, join_api_path

    with pytest.raises(ApiPathError):
        join_api_path("/v1/ado", endpoint)


@pytest.mark.parametrize("prefix", ["/v1/ado", "/v1/github"])
@pytest.mark.parametrize(
    "endpoint",
    [
        "..#", "..#junk", "x/..#", "a/b/../../..#z", "..%23", "..#?ref=main",
        "../../v1/migrate/git-mirror", "..%2f..%2fv1/migrate", "./a/../../b",
    ],
)
def test_join_api_path_agrees_with_what_httpx_would_send(prefix, endpoint):
    """Whatever survives the check must still be under the prefix on the wire."""
    import httpx

    from ado2gh.agents.migration_agent.tools.shared_tools import ApiPathError, join_api_path

    try:
        joined = join_api_path(prefix, endpoint)
    except ApiPathError:
        return  # refused before it could be sent, which is the safe outcome
    sent = httpx.Client(base_url="http://accelerator:8080")._merge_url(joined)
    assert str(sent.path).startswith(prefix + "/"), (endpoint, str(sent))


def test_join_api_path_refusal_is_audited():
    """CA-004: a blocked traversal must leave a durable record, not just a tool result."""
    from ado2gh.agents.migration_agent.tools import shared_tools

    recorded = []

    class FakeBridge:
        def record(self, action, **kwargs):
            recorded.append((action, kwargs))
            return "aud_test"

    original = shared_tools._audit
    shared_tools._audit = FakeBridge()
    try:
        with pytest.raises(shared_tools.ApiPathError):
            shared_tools.join_api_path("/v1/ado", "../../v1/migrate/git-mirror")
    finally:
        shared_tools._audit = original

    assert recorded, "no audit event written for a refused tool path"
    action, kwargs = recorded[0]
    assert action == "agent.tool.path_refused"
    assert kwargs["metadata"]["prefix"] == "/v1/ado"
    assert "endpoint_escapes_prefix" in kwargs["detail"]


def test_refuse_path_masks_a_secret_before_truncating_it():
    """CA-003: the endpoint is masked before the audit-record cut, not after.

    A bare 52-character token (the Azure DevOps PAT shape ``redact_text``
    recognises) is placed so it straddles ``_MAX_AUDITED_ENDPOINT_CHARS``.
    Truncating first would cut the token in half, leaving a plausible-looking
    fragment too short to be recognised and masked; masking first removes the
    whole secret before the cut ever applies.
    """
    from ado2gh.agents.migration_agent.tools import shared_tools

    recorded = []

    class FakeBridge:
        def record(self, action, **kwargs):
            recorded.append((action, kwargs))
            return "aud_test"

    original = shared_tools._audit
    shared_tools._audit = FakeBridge()
    try:
        token = "A" * 52
        endpoint = "/" * 180 + token + "/rest"
        assert 180 < shared_tools._MAX_AUDITED_ENDPOINT_CHARS < 180 + len(token)
        shared_tools._refuse_path("/v1/ado", endpoint, "endpoint_too_long")
    finally:
        shared_tools._audit = original

    assert recorded, "no audit event written for a refused path"
    stored_endpoint = recorded[0][1]["metadata"]["endpoint"]
    assert token not in stored_endpoint
    assert "A" * 20 not in stored_endpoint, "an unmasked secret fragment survived truncation"
    assert "***" in stored_endpoint


@pytest.mark.asyncio
async def test_fetch_github_workflow_redacts_before_it_truncates():
    """A PAT straddling the cap must not survive as an unrecognisable fragment."""
    import base64

    from ado2gh.agents.migration_agent.tools.validator_tools import MAX_WORKFLOW_CONTENT_CHARS

    pat = "q" * 52
    raw = "A" * (MAX_WORKFLOW_CONTENT_CHARS - 21) + " " + pat + " " + "B" * 100
    assert raw.index(pat) < MAX_WORKFLOW_CONTENT_CHARS < raw.index(pat) + len(pat)
    payload = base64.b64encode(raw.encode()).decode()

    async def accel_get(path, session_token=None):
        return {"encoding": "base64", "content": payload}

    tools = get_validator_tools(accel_get=accel_get)
    tool = next(t for t in tools if t.name == "fetch_github_workflow")

    result = await tool.ainvoke({
        "github_org": "o",
        "github_repo": "r",
        "workflow_path": ".github/workflows/ci.yml",
    })

    assert pat not in result["content"]
    assert pat[:30] not in result["content"]
    assert "***" in result["content"]
