from __future__ import annotations

from ado2gh.clients.gh_client import GHClient


def test_matching_git_refs_are_fully_paginated_and_preserve_raw_tag_sha():
    client = object.__new__(GHClient)
    calls = []
    first_page = [
        {
            "ref": f"refs/tags/v{index}",
            "object": {"sha": f"{index:040x}", "type": "tag"},
        }
        for index in range(100)
    ]
    annotated_tag = {
        "ref": "refs/tags/final",
        "object": {"sha": "f" * 40, "type": "tag"},
    }

    def fake_get(path, params=None):
        calls.append((path, dict(params or {})))
        return first_page if params["page"] == 1 else [annotated_tag]

    client._get = fake_get

    refs = client.list_git_refs("octo", "service", "tags")

    assert len(refs) == 101
    assert refs[-1]["object"]["sha"] == "f" * 40
    assert [call[1]["page"] for call in calls] == [1, 2]
    assert calls[0][0].endswith("/git/matching-refs/tags/")
