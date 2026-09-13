"""URL, verb and error-branch tests for ``ado2gh/clients/gh_client.py`` (COV-DRIFT-003).

This module used to hold a single ten-line property assertion while 103 of
`gh_client.py`'s 149 statements were unexercised and this feature rewrote 419
lines of it. The client is every byte the platform writes to GitHub, so the
assertions here are on the endpoint and body each method builds, on which
failures are raised versus swallowed, and on the rate-limit headers being fed
back to the token manager after *every* request — the behaviour
`CLAUDE.md` § Key Patterns documents.

`responses`/`requests_mock` are not installed here, so the thread session is
replaced with a recording double. Every token literal is obviously fake
(CA-003).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from ado2gh.clients.gh_client import GHClient
from ado2gh.clients.gh_token_manager import TokenManager
from ado2gh.models import RepoConfig

FAKE_TOKEN = "ghp_faketokenvalueneverreal000000000001"
REPO = RepoConfig(
    ado_project="Contoso", ado_repo="payments",
    gh_org="fake-gh-org", gh_repo="payments",
)


class FakeResponse:
    """A ``requests.Response`` stand-in the client cannot tell from the real one."""

    def __init__(
        self,
        payload: object = None,
        *,
        status: int = 200,
        headers: dict | None = None,
        content: bytes = b"{}",
    ) -> None:
        self._payload = payload if payload is not None else {}
        self.status_code = status
        self.headers = headers or {}
        self.content = content
        self.ok = status < 400

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


class FakeSession:
    """Records every request and answers from a queue of prepared responses."""

    def __init__(self, *responses: object) -> None:
        self._queue = list(responses) or [FakeResponse({})]
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, str, dict]] = []

    def _answer(self, verb: str, url: str, kwargs: dict) -> FakeResponse:
        self.calls.append((verb, url, kwargs))
        nxt = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def get(self, url, **kw):
        return self._answer("GET", url, kw)

    def post(self, url, **kw):
        return self._answer("POST", url, kw)

    def patch(self, url, **kw):
        return self._answer("PATCH", url, kw)

    def put(self, url, **kw):
        return self._answer("PUT", url, kw)

    def delete(self, url, **kw):
        return self._answer("DELETE", url, kw)

    @property
    def urls(self) -> list[str]:
        return [url for _, url, _ in self.calls]

    @property
    def verbs(self) -> list[str]:
        return [verb for verb, _, _ in self.calls]

    def body(self, index: int = 0) -> object:
        return self.calls[index][2].get("json")


@pytest.fixture
def gh():
    """A client whose thread session is the recording double.

    ``gh.queue(*responses)`` primes the responses for the next calls; the
    session is rebuilt so each test starts with an empty call log.
    """
    client = GHClient.from_single_token(FAKE_TOKEN)
    holder: dict = {"session": FakeSession()}

    with patch(
        "ado2gh.clients.gh_client.get_thread_session",
        side_effect=lambda: holder["session"],
    ):
        def queue(*responses):
            holder["session"] = FakeSession(*responses)
            return holder["session"]

        client.queue = queue  # type: ignore[attr-defined]
        client.session_of = lambda: holder["session"]  # type: ignore[attr-defined]
        yield client


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------


def test_gh_client_token_manager_property():
    tm = TokenManager.from_single_token(FAKE_TOKEN)
    client = GHClient(tm)
    assert client.token_manager is tm
    assert client.token_manager.get_token() == FAKE_TOKEN


def test_a_trailing_slash_is_stripped_from_the_base_url():
    assert GHClient.from_single_token(FAKE_TOKEN, "https://ghe.internal/api/v3/").BASE == (
        "https://ghe.internal/api/v3"
    )


def test_the_session_carries_the_github_json_headers(gh):
    session = gh.queue(FakeResponse({}))
    gh._get("/x")
    assert session.headers["Accept"] == "application/vnd.github+json"
    assert session.headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_every_request_carries_the_pooled_token_as_a_bearer(gh):
    session = gh.queue(FakeResponse({}))
    gh._get("/x")
    assert session.calls[0][2]["headers"]["Authorization"] == f"Bearer {FAKE_TOKEN}"
    assert session.calls[0][2]["timeout"] == 30


# --------------------------------------------------------------------------
# Rate-limit feedback
# --------------------------------------------------------------------------


def test_the_rate_limit_headers_are_fed_back_to_the_token_manager(gh):
    gh.queue(FakeResponse({}, headers={"x-ratelimit-remaining": "123",
                                       "x-ratelimit-reset": "1700000000"}))
    gh._get("/x")
    info = gh.token_manager._tokens[0]
    assert info.remaining == 123
    assert info.reset_at == 1700000000.0


def test_a_response_with_no_rate_limit_headers_records_the_full_quota(gh):
    gh.token_manager.update_rate_limit(FAKE_TOKEN, 1, 999.0)
    gh.queue(FakeResponse({}, headers={}))
    gh._get("/x")
    assert gh.token_manager._tokens[0].remaining == 5000


def test_the_headers_are_recorded_even_when_the_response_is_a_failure(gh):
    """The limit must be learned from a 403 too, or the pool never backs off."""
    gh.queue(FakeResponse({}, status=403,
                          headers={"x-ratelimit-remaining": "0",
                                   "x-ratelimit-reset": "1700000000"}))
    with pytest.raises(requests.HTTPError):
        gh._get("/x")
    assert gh.token_manager._tokens[0].remaining == 0


@pytest.mark.parametrize("verb", ["_post", "_patch"])
def test_the_write_verbs_also_feed_the_rate_limit_back(gh, verb):
    gh.queue(FakeResponse({}, headers={"x-ratelimit-remaining": "7"}))
    getattr(gh, verb)("/x", {})
    assert gh.token_manager._tokens[0].remaining == 7


# --------------------------------------------------------------------------
# Endpoint, verb and body
# --------------------------------------------------------------------------


def test_get_repo_targets_the_repository_endpoint(gh):
    session = gh.queue(FakeResponse({"full_name": "fake-gh-org/payments"}))
    assert gh.get_repo("fake-gh-org", "payments")["full_name"] == "fake-gh-org/payments"
    assert session.urls[0] == "https://api.github.com/repos/fake-gh-org/payments"
    assert session.verbs == ["GET"]


def test_create_private_repo_always_creates_a_private_empty_repository(gh):
    session = gh.queue(FakeResponse({"id": 1}))
    gh.create_private_repo("fake-gh-org", "payments", description="migrated")
    assert session.urls[0] == "https://api.github.com/orgs/fake-gh-org/repos"
    assert session.body() == {
        "name": "payments", "private": True, "description": "migrated",
        "has_issues": True, "has_wiki": True, "auto_init": False,
    }


def test_archive_repo_patches_the_archived_flag(gh):
    session = gh.queue(FakeResponse({"archived": True}))
    assert gh.archive_repo("fake-gh-org", "payments") == {"archived": True}
    assert session.verbs == ["PATCH"]
    assert session.body() == {"archived": True}


def test_delete_repo_reports_the_upstream_outcome(gh):
    gh.queue(FakeResponse({}, status=204))
    assert gh.delete_repo("fake-gh-org", "payments") is True
    gh.queue(FakeResponse({}, status=403))
    assert gh.delete_repo("fake-gh-org", "payments") is False


def test_create_issue_truncates_an_over_long_title_and_body(gh):
    session = gh.queue(FakeResponse({"number": 1}))
    gh.create_issue("fake-gh-org", "payments", "T" * 400, body="B" * 70000, labels=["bug"])
    body = session.body()
    assert len(body["title"]) == 255
    assert len(body["body"]) == 65535
    assert body["labels"] == ["bug"]


def test_create_issue_defaults_to_no_labels(gh):
    session = gh.queue(FakeResponse({}))
    gh.create_issue("fake-gh-org", "payments", "Title")
    assert session.body()["labels"] == []


def test_set_branch_protection_builds_the_documented_rule(gh):
    session = gh.queue(FakeResponse({"url": "x"}))
    gh.set_branch_protection(
        "fake-gh-org", "payments", "main",
        required_reviewers=2, status_checks=["build", "test"],
    )
    assert session.urls[0].endswith("/repos/fake-gh-org/payments/branches/main/protection")
    body = session.body()
    assert body["required_status_checks"] == {
        "strict": True, "checks": [{"context": "build"}, {"context": "test"}],
    }
    assert body["required_pull_request_reviews"]["required_approving_review_count"] == 2
    assert body["required_pull_request_reviews"]["dismiss_stale_reviews"] is True
    assert body["enforce_admins"] is False
    assert body["restrictions"] is None


def test_add_team_to_repo_puts_the_permission_and_raises_on_refusal(gh):
    session = gh.queue(FakeResponse({}, status=204))
    gh.add_team_to_repo("fake-gh-org", "platform", "payments", permission="admin")
    assert session.urls[0].endswith("/orgs/fake-gh-org/teams/platform/repos/fake-gh-org/payments")
    assert session.body() == {"permission": "admin"}

    gh.queue(FakeResponse({}, status=404))
    with pytest.raises(requests.HTTPError):
        gh.add_team_to_repo("fake-gh-org", "missing", "payments")


def test_create_secret_sends_only_the_sealed_value(gh):
    session = gh.queue(FakeResponse({}, status=201))
    gh.create_secret("fake-gh-org", "payments", "ADO_PAT", "c2VhbGVk", "key-1")
    assert session.urls[0].endswith("/repos/fake-gh-org/payments/actions/secrets/ADO_PAT")
    assert session.body() == {"encrypted_value": "c2VhbGVk", "key_id": "key-1"}
    assert "plaintext" not in str(session.body())


def test_create_secret_raises_when_github_refuses(gh):
    gh.queue(FakeResponse({}, status=422))
    with pytest.raises(requests.HTTPError):
        gh.create_secret("fake-gh-org", "payments", "X", "c2VhbGVk", "key-1")


def test_the_public_key_lookup_targets_the_actions_endpoint(gh):
    session = gh.queue(FakeResponse({"key_id": "k1", "key": "AAA="}))
    assert gh.get_repo_public_key("fake-gh-org", "payments") == {"key_id": "k1", "key": "AAA="}
    assert session.urls[0].endswith("/repos/fake-gh-org/payments/actions/secrets/public-key")


def test_create_branch_names_the_full_ref(gh):
    session = gh.queue(FakeResponse({"ref": "refs/heads/feature"}))
    gh.create_branch("fake-gh-org", "payments", "feature", "abc123")
    assert session.urls[0].endswith("/repos/fake-gh-org/payments/git/refs")
    assert session.body() == {"ref": "refs/heads/feature", "sha": "abc123"}


def test_set_default_branch_patches_the_repository(gh):
    session = gh.queue(FakeResponse({"default_branch": "main"}))
    gh.set_default_branch("fake-gh-org", "payments", "main")
    assert session.body() == {"default_branch": "main"}


def test_create_pull_request_addresses_the_repo_from_the_config(gh):
    session = gh.queue(FakeResponse({"number": 4}))
    gh.create_pull_request(REPO, "Migrate workflows", "body", "head-branch", "main")
    assert session.urls[0].endswith("/repos/fake-gh-org/payments/pulls")
    assert session.body() == {
        "title": "Migrate workflows", "body": "body",
        "head": "head-branch", "base": "main",
    }


# --------------------------------------------------------------------------
# repo_exists: 404 is an answer, anything else is an error
# --------------------------------------------------------------------------


def test_repo_exists_is_true_on_a_successful_lookup(gh):
    gh.queue(FakeResponse({"id": 1}))
    assert gh.repo_exists("fake-gh-org", "payments") is True


def test_repo_exists_is_false_on_a_404(gh):
    gh.queue(FakeResponse({}, status=404))
    assert gh.repo_exists("fake-gh-org", "missing") is False


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_repo_exists_raises_on_any_other_failure(gh, status):
    """A 403 must not be reported as "the repository does not exist"."""
    gh.queue(FakeResponse({}, status=status))
    with pytest.raises(requests.HTTPError):
        gh.repo_exists("fake-gh-org", "payments")


# --------------------------------------------------------------------------
# Failures swallowed into a benign result
# --------------------------------------------------------------------------


def test_create_label_reports_false_when_the_label_already_exists(gh):
    gh.queue(FakeResponse({}, status=422))
    assert gh.create_label("fake-gh-org", "payments", "migrated") is False


def test_create_label_reports_true_and_sends_the_colour(gh):
    session = gh.queue(FakeResponse({"id": 1}))
    assert gh.create_label("fake-gh-org", "payments", "migrated", color="ff0000") is True
    assert session.body() == {"name": "migrated", "color": "ff0000"}


def test_create_environment_encodes_the_name_and_reports_the_outcome(gh):
    session = gh.queue(FakeResponse({}, status=200))
    assert gh.create_environment("fake-gh-org", "payments", "prod eu", ["reviewer"]) is True
    assert session.urls[0].endswith("/environments/prod%20eu")
    assert session.body() == {"wait_timer": 0, "reviewers": ["reviewer"]}

    gh.queue(FakeResponse({}, status=403))
    assert gh.create_environment("fake-gh-org", "payments", "prod") is False


def test_list_directory_is_empty_when_the_path_is_a_file(gh):
    gh.queue(FakeResponse({"type": "file"}))
    assert gh.list_directory("fake-gh-org", "payments", ".github", "main") == []


def test_list_directory_returns_the_entries_and_passes_the_ref(gh):
    session = gh.queue(FakeResponse([{"name": "ci.yml"}]))
    assert gh.list_directory("fake-gh-org", "payments", ".github/workflows", "main") == [
        {"name": "ci.yml"},
    ]
    assert session.calls[0][2]["params"] == {"ref": "main"}
    assert session.urls[0].endswith("/contents/.github/workflows")


def test_list_directory_is_empty_when_the_request_fails(gh):
    gh.queue(FakeResponse({}, status=404))
    assert gh.list_directory("fake-gh-org", "payments", "nope", "main") == []


def test_list_workflows_unwraps_the_workflows_key(gh):
    gh.queue(FakeResponse({"total_count": 1, "workflows": [{"name": "ci"}]}))
    assert gh.list_workflows("fake-gh-org", "payments") == [{"name": "ci"}]


def test_list_workflows_is_empty_when_the_request_fails(gh):
    gh.queue(FakeResponse({}, status=500))
    assert gh.list_workflows("fake-gh-org", "payments") == []


def test_get_file_sha_returns_the_blob_sha(gh):
    gh.queue(FakeResponse({"sha": "blob-sha"}))
    assert gh.get_file_sha("fake-gh-org", "payments", "README.md", "main") == "blob-sha"


def test_get_file_sha_is_empty_for_a_directory(gh):
    gh.queue(FakeResponse([{"name": "a"}]))
    assert gh.get_file_sha("fake-gh-org", "payments", "docs", "main") == ""


def test_get_file_sha_is_empty_when_the_request_fails(gh):
    gh.queue(FakeResponse({}, status=404))
    assert gh.get_file_sha("fake-gh-org", "payments", "missing", "main") == ""


def test_get_default_branch_falls_back_to_main(gh):
    gh.queue(FakeResponse({}))
    assert gh.get_default_branch("fake-gh-org", "payments") == "main"
    gh.queue(FakeResponse({"default_branch": "trunk"}))
    assert gh.get_default_branch("fake-gh-org", "payments") == "trunk"


def test_get_branch_sha_reads_the_ref_object(gh):
    session = gh.queue(FakeResponse({"object": {"sha": "a" * 40}}))
    assert gh.get_branch_sha("fake-gh-org", "payments", "main") == "a" * 40
    assert session.urls[0].endswith("/repos/fake-gh-org/payments/git/ref/heads/main")


@pytest.mark.parametrize("status", [404, 409])
def test_get_branch_sha_raises_for_a_missing_branch_or_empty_repo(gh, status):
    gh.queue(FakeResponse({}, status=status))
    with pytest.raises(requests.HTTPError):
        gh.get_branch_sha("fake-gh-org", "payments", "main")


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------


def test_list_branches_pages_until_a_short_batch_arrives(gh):
    full = [{"name": f"b{i}"} for i in range(100)]
    session = gh.queue(FakeResponse(full), FakeResponse([{"name": "last"}]))
    branches = gh.list_branches("fake-gh-org", "payments")
    assert len(branches) == 101
    assert branches[-1] == {"name": "last"}
    assert session.calls[0][2]["params"] == {"per_page": 100, "page": 1}
    assert session.calls[1][2]["params"] == {"per_page": 100, "page": 2}


def test_list_branches_stops_after_one_short_page(gh):
    session = gh.queue(FakeResponse([{"name": "main"}]))
    assert gh.list_branches("fake-gh-org", "payments") == [{"name": "main"}]
    assert len(session.calls) == 1


def test_list_branches_keeps_the_pages_it_already_fetched_when_one_fails(gh):
    full = [{"name": f"b{i}"} for i in range(100)]
    gh.queue(FakeResponse(full), FakeResponse({}, status=500))
    assert len(gh.list_branches("fake-gh-org", "payments")) == 100


# --------------------------------------------------------------------------
# put_file: look up the existing blob first so an update is not a 422
# --------------------------------------------------------------------------


def test_put_file_includes_the_existing_blob_sha_on_an_update(gh):
    session = gh.queue(
        FakeResponse({"sha": "old-blob-sha"}),           # get_file_sha
        FakeResponse({"commit": {"sha": "new"}}),        # the PUT
    )
    gh.put_file(REPO, ".github/workflows/ci.yml", "Y29udGVudA==", "main", "add ci")
    assert session.verbs == ["GET", "PUT"]
    assert session.body(1) == {
        "message": "add ci", "content": "Y29udGVudA==",
        "branch": "main", "sha": "old-blob-sha",
    }


def test_put_file_omits_the_sha_when_the_file_is_new(gh):
    session = gh.queue(
        FakeResponse({}, status=404),                    # get_file_sha misses
        FakeResponse({"commit": {"sha": "new"}}),
    )
    gh.put_file(REPO, "new.yml", "Y29udGVudA==", "main", "add")
    assert "sha" not in session.body(1)


def test_put_file_raises_when_the_write_is_refused(gh):
    gh.queue(FakeResponse({}, status=404), FakeResponse({}, status=409))
    with pytest.raises(requests.HTTPError):
        gh.put_file(REPO, "new.yml", "Y29udGVudA==", "main", "add")
