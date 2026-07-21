from __future__ import annotations

import hashlib

from ado2gh.models import RepoConfig
from ado2gh.reporting.post_migration_validator import PostMigrationValidator


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(content)}\0".encode("ascii") + content
    ).hexdigest()


class _GraphGH:
    def __init__(self, files):
        self.files = dict(files)

    def get_repo(self, owner, repo):
        return {
            "node_id": f"R_{owner}_{repo}",
            "visibility": "public",
        }

    def get_commit_sha(self, _owner, _repo, ref):
        return ref

    def get_file_sha(self, owner, repo, path, ref):
        content = self.files.get((owner, repo, ref, path))
        return _blob_sha(content) if content is not None else ""

    def get_file_content(self, owner, repo, path, ref):
        return self.files[(owner, repo, ref, path)]


def _repo() -> RepoConfig:
    return RepoConfig("Payments", "api", "octo", "payments-api")


def test_pinned_composite_with_mutable_nested_action_fails_closed():
    wrapper_ref = "a" * 40
    files = {
        (
            "trusted",
            "wrapper",
            wrapper_ref,
            "action.yml",
        ): b"runs:\n  using: composite\n  steps:\n    - uses: evil/action@main\n",
    }
    validator = PostMigrationValidator(
        object(), _GraphGH(files), object()
    )

    receipts, failures = validator._verify_action_dependencies(
        _repo(), {f"trusted/wrapper@{wrapper_ref}"}, "b" * 40
    )

    assert failures
    assert "full commit SHA" in failures[0]["reason"]
    assert not any(
        item.get("uses") == "evil/action@main" for item in receipts
    )


def test_node_action_binds_descriptor_and_all_runtime_entrypoints():
    ref = "c" * 40
    files = {
        ("trusted", "node-action", ref, "action.yml"): (
            b"runs:\n  using: node20\n  main: dist/main.js\n"
            b"  pre: dist/pre.js\n  post: dist/post.js\n"
        ),
        ("trusted", "node-action", ref, "dist/main.js"): b"main();\n",
        ("trusted", "node-action", ref, "dist/pre.js"): b"pre();\n",
        ("trusted", "node-action", ref, "dist/post.js"): b"post();\n",
    }
    validator = PostMigrationValidator(
        object(), _GraphGH(files), object()
    )

    receipts, failures = validator._verify_action_dependencies(
        _repo(), {f"trusted/node-action@{ref}"}, "d" * 40
    )

    assert failures == []
    [receipt] = [item for item in receipts if item["kind"] == "action"]
    assert {item["role"] for item in receipt["dependency_files"]} == {
        "main", "pre", "post",
    }


def test_pinned_docker_action_rejects_mutable_base_image():
    ref = "e" * 40
    files = {
        ("trusted", "docker-action", ref, "action.yml"): (
            b"runs:\n  using: docker\n  image: Dockerfile\n"
        ),
        ("trusted", "docker-action", ref, "Dockerfile"): (
            b"FROM alpine:latest\nCOPY entrypoint.sh /entrypoint.sh\n"
        ),
    }
    validator = PostMigrationValidator(
        object(), _GraphGH(files), object()
    )

    _receipts, failures = validator._verify_action_dependencies(
        _repo(), {f"trusted/docker-action@{ref}"}, "f" * 40
    )

    assert failures
    assert "base image is not digest pinned" in failures[0]["reason"]
