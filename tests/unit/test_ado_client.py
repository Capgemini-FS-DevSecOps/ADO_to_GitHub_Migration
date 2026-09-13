"""URL, pagination and error-branch tests for ``ado2gh/clients/ado_client.py`` (COV-DRIFT-003).

`ado_client.py` is every byte the platform reads out of Azure DevOps and had no
dedicated test module at all: 188 of its 233 statements were unexercised while
this feature rewrote 283 lines of it.

`responses`/`requests_mock` are not installed in this environment, so the
per-thread ``requests.Session`` is replaced with a recording double instead. The
assertions are on the URL and verb the client builds, on the pagination
continuation, and on which failures are swallowed into an empty result versus
raised — the client's docstrings promise both behaviours in different methods and
nothing checked which was which.

Every PAT literal is obviously fake (CA-003).
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest
import requests

from ado2gh.clients.ado_client import ADOClient

FAKE_PAT = "fake-ado-pat-value-not-real-0001"
ORG = "https://dev.azure.com/fake-org"


class FakeResponse:
    """A ``requests.Response`` stand-in the client cannot tell from the real one."""

    def __init__(
        self,
        payload: object = None,
        *,
        status: int = 200,
        headers: dict | None = None,
        text: str = "",
    ) -> None:
        self._payload = payload if payload is not None else {}
        self.status_code = status
        self.headers = headers or {}
        self.text = text
        self.ok = status < 400

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


class FakeSession:
    """Records every request and answers from a queue of prepared responses."""

    def __init__(self, *responses: object) -> None:
        self._queue = list(responses)
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict]] = []

    def _answer(self, verb: str, url: str, kwargs: dict) -> FakeResponse:
        self.calls.append((verb, url, kwargs))
        if not self._queue:
            return FakeResponse({})
        nxt = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def get(self, url, **kwargs):
        return self._answer("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._answer("POST", url, kwargs)

    @property
    def urls(self) -> list[str]:
        return [url for _, url, _ in self.calls]


def _client(*responses: object) -> tuple[ADOClient, FakeSession]:
    """An ADO client whose per-thread session is the recording double."""
    client = ADOClient(ORG + "/", pat=FAKE_PAT)
    session = FakeSession(*responses)
    client._local.session = session
    return client, session


# --------------------------------------------------------------------------
# Construction and authentication
# --------------------------------------------------------------------------


def test_a_trailing_slash_is_stripped_from_the_org_url():
    assert ADOClient(ORG + "/", pat=FAKE_PAT).org_url == ORG


def test_the_fixed_pat_is_used_when_no_token_manager_is_given():
    assert ADOClient(ORG, pat=FAKE_PAT).pat == FAKE_PAT


def test_a_token_manager_supersedes_the_fixed_pat():
    manager = MagicMock()
    manager.get_token.return_value = "rotated-fake-token-0002"
    client = ADOClient(ORG, pat=FAKE_PAT, token_manager=manager)
    assert client.pat == "rotated-fake-token-0002"
    assert client._pat == "", "the fixed PAT was retained alongside a rotating pool"


def test_the_session_carries_basic_auth_built_from_the_pat():
    client = ADOClient(ORG, pat=FAKE_PAT)
    expected = base64.b64encode(f":{FAKE_PAT}".encode()).decode()
    assert client.session.headers["Authorization"] == f"Basic {expected}"
    assert client.session.headers["Accept"] == "application/json"


def test_the_session_is_cached_per_thread():
    client = ADOClient(ORG, pat=FAKE_PAT)
    assert client.session is client.session


def test_the_project_name_is_encoded_as_a_single_path_segment():
    client = ADOClient(ORG, pat=FAKE_PAT)
    assert client._encode_project("Contoso Core/Sub") == "Contoso%20Core%2FSub"


# --------------------------------------------------------------------------
# Verb, URL and payload
# --------------------------------------------------------------------------


def test_get_uses_the_get_verb_and_returns_the_decoded_body():
    client, session = _client(FakeResponse({"value": [1, 2]}))
    assert client._get("https://example.invalid/x") == {"value": [1, 2]}
    verb, url, kwargs = session.calls[0]
    assert verb == "GET"
    assert url == "https://example.invalid/x"
    assert kwargs["timeout"] == 45


def test_post_sends_the_body_as_json():
    client, session = _client(FakeResponse({"ok": True}))
    assert client._post("https://example.invalid/x", {"query": "q"}) == {"ok": True}
    verb, _, kwargs = session.calls[0]
    assert verb == "POST"
    assert kwargs["json"] == {"query": "q"}


def test_list_projects_asks_for_the_first_five_hundred():
    client, session = _client(FakeResponse({"value": [{"name": "Contoso"}]}))
    assert client.list_projects() == [{"name": "Contoso"}]
    assert session.urls[0] == f"{ORG}/_apis/projects?api-version=7.1&$top=500"


def test_get_repo_encodes_both_the_project_and_the_repo_name():
    client, session = _client(FakeResponse({"id": "repo-guid"}))
    assert client.get_repo("Contoso Core", "my repo")["id"] == "repo-guid"
    assert session.urls[0] == (
        f"{ORG}/Contoso%20Core/_apis/git/repositories/my%20repo?api-version=7.1"
    )


def test_get_pipeline_definition_targets_the_pipeline_by_id():
    client, session = _client(FakeResponse({"id": 7}))
    assert client.get_pipeline_definition("Contoso", 7) == {"id": 7}
    assert session.urls[0] == f"{ORG}/Contoso/_apis/pipelines/7?api-version=7.1"


def test_branch_policies_are_filtered_to_the_repository_they_scope():
    payload = {
        "value": [
            {"id": 1, "settings": {"scope": [{"repositoryId": "wanted"}]}},
            {"id": 2, "settings": {"scope": [{"repositoryId": "other"}]}},
            {"id": 3, "settings": {}},
        ],
    }
    client, _ = _client(FakeResponse(payload))
    assert [p["id"] for p in client.list_branch_policies("Contoso", "wanted")] == [1]


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------


def test_list_repos_follows_the_continuation_token_until_it_stops():
    page1 = FakeResponse(
        {"value": [{"name": "a"}]}, headers={"x-ms-continuationtoken": "TOKEN2"},
    )
    page2 = FakeResponse({"value": [{"name": "b"}]}, headers={})
    client, session = _client(page1, page2)
    assert [r["name"] for r in client.list_repos("Contoso")] == ["a", "b"]
    assert len(session.calls) == 2
    assert "continuationToken" not in session.urls[0]
    assert "continuationToken=TOKEN2" in session.urls[1]
    assert "$top=100" in session.urls[1], "the page size was dropped on the second page"


def test_list_repos_stops_after_one_page_when_no_token_is_returned():
    client, session = _client(FakeResponse({"value": [{"name": "only"}]}, headers={}))
    assert len(client.list_repos("Contoso")) == 1
    assert len(session.calls) == 1


def test_list_all_pipelines_yields_across_pages_in_order():
    page1 = FakeResponse(
        {"value": [{"id": 1}, {"id": 2}]}, headers={"x-ms-continuationtoken": "NEXT"},
    )
    page2 = FakeResponse({"value": [{"id": 3}]}, headers={})
    client, session = _client(page1, page2)
    assert [p["id"] for p in client.list_all_pipelines("Contoso")] == [1, 2, 3]
    assert "continuationToken=NEXT" in session.urls[1]
    assert "orderBy=name" in session.urls[0]


def test_list_all_release_pipelines_rewrites_the_host_to_vsrm():
    client, session = _client(FakeResponse({"value": []}))
    list(client.list_all_release_pipelines("Contoso"))
    assert session.urls[0].startswith("https://vsrm.dev.azure.com/fake-org/Contoso")
    assert "$expand=artifacts,environments" in session.urls[0]


def test_list_all_release_pipelines_stops_on_a_short_page():
    client, session = _client(FakeResponse({"value": [{"id": 1}]}))
    assert [d["id"] for d in client.list_all_release_pipelines("Contoso")] == [1]
    assert len(session.calls) == 1, "a short page did not end the walk"


def test_the_vsrm_rewrite_also_covers_the_legacy_visualstudio_host():
    client = ADOClient("https://fake-org.visualstudio.com", pat=FAKE_PAT)
    session = FakeSession(FakeResponse({"id": 3}))
    client._local.session = session
    client.get_release_definition("Contoso", 3)
    assert session.urls[0].startswith("https://fake-org.vsrm.visualstudio.com/")


# --------------------------------------------------------------------------
# Failures that are raised
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403, 404, 409, 429, 500])
def test_a_non_2xx_response_is_raised_by_the_shared_get(status):
    client, _ = _client(FakeResponse({}, status=status))
    with pytest.raises(requests.HTTPError):
        client._get("https://example.invalid/x")


@pytest.mark.parametrize("status", [401, 403, 404])
def test_get_repo_raises_rather_than_returning_an_empty_record(status):
    client, _ = _client(FakeResponse({}, status=status))
    with pytest.raises(requests.HTTPError):
        client.get_repo("Contoso", "missing")


def test_list_projects_raises_on_a_failed_response():
    client, _ = _client(FakeResponse({}, status=403))
    with pytest.raises(requests.HTTPError):
        client.list_projects()


def test_list_repos_raises_on_a_failed_page():
    client, _ = _client(FakeResponse({}, status=500))
    with pytest.raises(requests.HTTPError):
        client.list_repos("Contoso")


# --------------------------------------------------------------------------
# Failures that are swallowed into an empty result
# --------------------------------------------------------------------------


def test_repo_stats_report_zero_branches_when_the_request_fails():
    client, _ = _client(FakeResponse({}, status=404))
    assert client.get_repo_stats("Contoso", "repo-guid") == {"branch_count": 0}


def test_repo_stats_report_the_branch_count_on_success():
    client, _ = _client(FakeResponse({"count": 12}))
    assert client.get_repo_stats("Contoso", "repo-guid") == {"branch_count": 12}


def test_repo_commits_return_empty_when_the_request_fails():
    client, _ = _client(FakeResponse({}, status=500))
    assert client.get_repo_commits("Contoso", "repo-guid") == []


def test_repo_commits_add_the_branch_criteria_only_when_a_branch_is_named():
    client, session = _client(FakeResponse({"value": [{"commitId": "abc"}]}))
    client.get_repo_commits("Contoso", "repo-guid", top=5, branch="release/1.0")
    url = session.urls[0]
    assert "$top=5" in url
    assert "searchCriteria.itemVersion.version=release%2F1.0" in url
    assert "searchCriteria.itemVersion.versionType=branch" in url

    client2, session2 = _client(FakeResponse({"value": []}))
    client2.get_repo_commits("Contoso", "repo-guid")
    assert "searchCriteria" not in session2.urls[0]


def test_build_definition_returns_an_empty_record_when_the_request_fails():
    client, _ = _client(FakeResponse({}, status=404))
    assert client.get_build_definition_full("Contoso", 11) == {}


def test_build_definition_asks_for_the_latest_builds():
    client, session = _client(FakeResponse({"id": 11}))
    assert client.get_build_definition_full("Contoso", 11) == {"id": 11}
    assert "includeLatestBuilds=true" in session.urls[0]


def test_pipeline_yaml_is_empty_for_an_empty_path_without_any_request():
    client, session = _client(FakeResponse({}, text="steps:"))
    assert client.get_pipeline_yaml_from_git("Contoso", "repo-guid", "") == ""
    assert session.calls == [], "an empty path still produced a request"


def test_pipeline_yaml_returns_the_raw_text_on_success():
    client, session = _client(FakeResponse({}, text="steps:\n  - script: echo hi\n"))
    body = client.get_pipeline_yaml_from_git(
        "Contoso", "repo-guid", "/pipelines/build.yml", branch="develop",
    )
    assert body == "steps:\n  - script: echo hi\n"
    url = session.urls[0]
    assert "path=pipelines%2Fbuild.yml" in url
    assert "versionDescriptor.version=develop" in url
    assert "$format=text" in url


def test_pipeline_yaml_is_empty_when_the_request_fails():
    client, _ = _client(FakeResponse({}, status=404))
    assert client.get_pipeline_yaml_from_git("Contoso", "repo-guid", "build.yml") == ""


def test_repo_yaml_files_are_empty_for_an_empty_repo_id_without_any_request():
    client, session = _client(FakeResponse({}))
    assert client.list_repo_yaml_files("Contoso", "") == []
    assert session.calls == []


def test_repo_yaml_files_keep_only_yaml_blobs():
    payload = {
        "value": [
            {"gitObjectType": "blob", "path": "/azure-pipelines.yml"},
            {"gitObjectType": "blob", "path": "/docs/README.md"},
            {"gitObjectType": "tree", "path": "/pipelines.yaml"},
            {"gitObjectType": "blob", "path": "/pipelines/Deploy.YAML"},
        ],
    }
    client, _ = _client(FakeResponse(payload))
    assert client.list_repo_yaml_files("Contoso", "repo-guid") == [
        "/azure-pipelines.yml", "/pipelines/Deploy.YAML",
    ]


def test_repo_yaml_files_stop_at_the_requested_maximum():
    payload = {
        "value": [
            {"gitObjectType": "blob", "path": f"/p{i}.yml"} for i in range(10)
        ],
    }
    client, _ = _client(FakeResponse(payload))
    assert len(client.list_repo_yaml_files("Contoso", "repo-guid", max_results=3)) == 3


def test_repo_yaml_files_are_empty_when_the_listing_fails():
    client, _ = _client(FakeResponse({}, status=500))
    assert client.list_repo_yaml_files("Contoso", "repo-guid") == []


def test_a_wiki_whose_page_tree_cannot_be_read_is_skipped_not_fatal():
    listing = FakeResponse({"value": [{"id": "w1"}, {"id": "w2"}]})
    tree_ok = FakeResponse({"path": "/"})
    tree_bad = FakeResponse({}, status=404)
    client = ADOClient(ORG, pat=FAKE_PAT)
    client._local.session = FakeSession(listing, tree_ok, tree_bad, tree_bad)
    out = client.list_wiki_pages("Contoso")
    assert [entry["wiki"]["id"] for entry in out] == ["w1"]


def test_the_wiki_listing_itself_still_raises():
    client, _ = _client(FakeResponse({}, status=403))
    with pytest.raises(requests.HTTPError):
        client.list_wiki_pages("Contoso")


# --------------------------------------------------------------------------
# Work items: WIQL query then a batched fetch
# --------------------------------------------------------------------------


def test_work_items_are_fetched_in_one_batch_after_the_wiql_query():
    wiql = FakeResponse({"workItems": [{"id": 1}, {"id": 2}]})
    batch = FakeResponse({"value": [{"id": 1}, {"id": 2}]})
    client = ADOClient(ORG, pat=FAKE_PAT)
    session = FakeSession(wiql, batch, batch)
    client._local.session = session
    assert len(client.list_work_items("Contoso")) == 2
    assert session.calls[0][0] == "POST"
    assert "wiql" in session.calls[0][1]
    assert session.calls[1][0] == "GET"
    assert "ids=1,2" in session.calls[1][1]


def test_no_work_items_means_no_batch_request_at_all():
    client, session = _client(FakeResponse({"workItems": []}))
    assert client.list_work_items("Contoso") == []
    assert len(session.calls) == 1, "an empty id list still triggered a batch fetch"


def test_the_work_item_query_is_capped_at_the_requested_top():
    wiql = FakeResponse({"workItems": [{"id": i} for i in range(10)]})
    batch = FakeResponse({"value": []})
    client = ADOClient(ORG, pat=FAKE_PAT)
    session = FakeSession(wiql, batch, batch)
    client._local.session = session
    client.list_work_items("Contoso", top=3)
    assert "ids=0,1,2&" in session.calls[1][1]
