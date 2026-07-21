from __future__ import annotations

import pytest

from ado2gh.clients.gh_client import GHClient


def _client(responses):
    client = object.__new__(GHClient)
    client.enterprise_slug = ""
    calls = []

    def get_repo(_org, _repo):
        return {"id": 42, "name": "target"}

    def get(path, params=None):
        calls.append((path, dict(params or {})))
        value = responses(path, dict(params or {}))
        return value

    client.get_repo = get_repo
    client._get = get
    return client, calls


def test_repo_app_inventory_proves_empty_with_two_stable_org_reads():
    client, calls = _client(
        lambda path, _params: {"total_count": 0, "installations": []}
    )

    assert client.list_repo_app_installations("octo", "target") == []
    assert [path for path, _params in calls] == [
        "/orgs/octo/installations",
        "/orgs/octo/installations",
    ]


def test_all_repository_installation_is_reported_even_when_suspended():
    installation = {
        "id": 7,
        "app_id": 9,
        "app_slug": "security-agent",
        "repository_selection": "all",
        "permissions": {"metadata": "read", "contents": "write"},
        "suspended_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
    }
    client, _calls = _client(lambda path, _params: {
        "total_count": 1, "installations": [dict(installation)],
    })

    result = client.list_repo_app_installations("octo", "target")

    assert result == [{
        "installation_id": 7,
        "app_id": 9,
        "app_slug": "security-agent",
        "repository_selection": "all",
        "permissions": {"contents": "write", "metadata": "read"},
        "suspended_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
    }]


def test_selected_installation_requires_complete_repository_expansion():
    installation = {
        "id": 11,
        "app_id": 19,
        "app_slug": "deploy",
        "repository_selection": "selected",
        "permissions": {"metadata": "read"},
        "suspended_at": None,
        "updated_at": "2026-07-01T00:00:00Z",
    }

    def responses(path, _params):
        if path == "/orgs/octo/installations":
            return {"total_count": 1, "installations": [dict(installation)]}
        assert path == "/user/installations/11/repositories"
        return {"total_count": 1, "repositories": [{"id": 42}]}

    client, calls = _client(responses)
    result = client.list_repo_app_installations("octo", "target")

    assert result[0]["installation_id"] == 11
    assert sum(
        path == "/user/installations/11/repositories"
        for path, _params in calls
    ) == 2


def test_selected_installation_incomplete_page_fails_closed():
    installation = {
        "id": 11,
        "app_id": 19,
        "app_slug": "deploy",
        "repository_selection": "selected",
        "permissions": {"metadata": "read"},
        "suspended_at": None,
        "updated_at": "2026-07-01T00:00:00Z",
    }

    def responses(path, _params):
        if path == "/orgs/octo/installations":
            return {"total_count": 1, "installations": [dict(installation)]}
        return {"total_count": 2, "repositories": [{"id": 42}]}

    client, _calls = _client(responses)
    with pytest.raises(RuntimeError, match="incomplete or duplicated"):
        client.list_repo_app_installations("octo", "target")


def test_enterprise_audit_api_expands_arbitrary_selected_installations():
    installation = {
        "id": 23,
        "app_id": 29,
        "app_slug": "third-party",
        "repository_selection": "selected",
        "permissions": {"metadata": "read", "contents": "write"},
        "suspended_at": None,
        "updated_at": "2026-07-01T00:00:00Z",
    }

    def responses(path, _params):
        if path.endswith("/installations"):
            return [{"value": dict(installation)}]
        assert path.endswith("/installations/23/repositories")
        return [{"id": 42, "name": "target"}]

    client, calls = _client(responses)
    client.enterprise_slug = "acme-enterprise"
    result = client.list_repo_app_installations("octo", "target")

    assert result[0]["app_slug"] == "third-party"
    assert all(
        path.startswith("/enterprises/acme-enterprise/apps/organizations/octo/")
        for path, _params in calls
    )
