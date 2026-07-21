from __future__ import annotations

import re

import pytest

from ado2gh.clients.ado_client import ADOClient


class Response:
    def __init__(self, payload, token=None):
        self._payload = payload
        self.headers = (
            {"x-ms-continuationtoken": token} if token is not None else {}
        )

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class CollectionSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=45):
        self.calls.append((url, dict(params or {}), timeout))
        token = (params or {}).get("continuationToken")
        if token is None:
            return Response({"value": [{"id": "first"}]}, "next-token")
        assert token == "next-token"
        return Response({"value": [{"id": "second"}]})


@pytest.mark.parametrize(
    "method_name",
    ["list_variable_groups", "list_service_connections"],
)
def test_enterprise_collections_follow_continuation_tokens(method_name):
    client = ADOClient("https://dev.azure.com/example", "pat")
    session = CollectionSession()
    client.session = session

    values = getattr(client, method_name)("Payments")

    assert [item["id"] for item in values] == ["first", "second"]
    assert session.calls[1][1]["continuationToken"] == "next-token"


class WikiSession:
    def __init__(self):
        self.collection_calls = 0
        self.page_urls = []

    def get(self, url, params=None, timeout=45):
        if "/pages?" in url:
            self.page_urls.append(url)
            wiki_id = "wiki-1" if "/wiki-1/" in url else "wiki-2"
            if "recursionLevel=none" in url:
                return Response({
                    "path": "/Child",
                    "content": f"{wiki_id}-child",
                    "subPages": [],
                })
            return Response({
                "path": "/Home",
                "content": wiki_id,
                "subPages": [{"path": "/Child", "subPages": []}],
            })
        self.collection_calls += 1
        if not (params or {}).get("continuationToken"):
            return Response(
                {"value": [{"id": "wiki-1", "name": "One"}]}, "wiki-next"
            )
        return Response({"value": [{"id": "wiki-2", "name": "Two"}]})


def test_wiki_enumeration_is_paginated_and_fetches_page_content():
    client = ADOClient("https://dev.azure.com/example", "pat")
    session = WikiSession()
    client.session = session

    wikis = client.list_wiki_pages("Payments")

    assert len(wikis) == 2
    assert session.collection_calls == 2
    assert all("includeContent=true" in url for url in session.page_urls)
    assert wikis[0]["root"]["subPages"][0]["content"] == "wiki-1-child"


class WorkItemClient(ADOClient):
    def __init__(self):
        super().__init__("https://dev.azure.com/example", "pat")
        self.wiql_queries = []

    def _post(self, _url, body, timeout=45):
        self.wiql_queries.append(body["query"])
        lower_bound = int(re.search(r"\[System.Id\] > (\d+)", body["query"]).group(1))
        if lower_bound == 0:
            return {"workItems": [{"id": 1}, {"id": 2}]}
        if lower_bound == 2:
            return {"workItems": [{"id": 3}]}
        raise AssertionError(lower_bound)

    def _get(self, _url, params=None, timeout=45):
        return {
            "value": [
                {"id": int(item_id), "fields": {}, "relations": []}
                for item_id in params["ids"].split(",")
            ],
        }


def test_work_items_use_id_keyset_pagination_beyond_wiql_page_limit():
    client = WorkItemClient()

    items = client.list_work_items(
        "Payments", top=2, include_unlinked=True
    )

    assert [item["id"] for item in items] == [1, 2, 3]
    assert len(client.wiql_queries) == 2
    assert "[System.Id] > 2" in client.wiql_queries[1]


class PolicySession:
    def get(self, _url, params=None, timeout=45):
        if not (params or {}).get("continuationToken"):
            return Response({"value": [{
                "id": 1,
                "settings": {"scope": [{"repositoryId": "repo-id"}]},
            }]}, "policy-next")
        return Response({"value": [
            {
                "id": 2,
                "settings": {"scope": [{"repositoryId": "REPO-ID"}]},
            },
            {
                "id": 3,
                "settings": {"scope": [{"repositoryId": "other"}]},
            },
        ]})


def test_branch_policy_enumeration_is_paginated_before_repo_filtering():
    client = ADOClient("https://dev.azure.com/example", "pat")
    client.session = PolicySession()

    policies = client.list_branch_policies("Payments", "repo-id")

    assert [policy["id"] for policy in policies] == [1, 2]
