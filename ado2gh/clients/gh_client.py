"""GitHub REST API client with multi-token support."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

from ado2gh.clients.gh_token_manager import TokenManager
from ado2gh.http_utils import get_thread_session
from ado2gh.models import RepoConfig


class GHClient:
    """GitHub API client with rate-limit-aware multi-token rotation.

    Every request takes a token from the ``TokenManager``, sends it as a bearer
    token and feeds the response's rate-limit headers back to the manager. Methods
    address the target by ``org`` and ``repo`` name; ``put_file`` and
    ``create_pull_request`` take the migration's ``RepoConfig`` instead.
    """

    def __init__(self, token_manager: TokenManager,
                 base_url: str = "https://api.github.com") -> None:
        """Create a client.

        Args:
            token_manager: Pool the bearer token for each request is drawn from.
            base_url: REST API base URL; override it for GitHub Enterprise Server.
        """
        self.BASE = base_url.rstrip("/")
        self._tm = token_manager

    @property
    def _session(self) -> requests.Session:
        """The calling thread's shared session with the GitHub JSON headers applied."""
        sess = get_thread_session()
        if "Accept" not in sess.headers:
            sess.headers.update({
                "Accept":               "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            })
        return sess

    @classmethod
    def from_single_token(cls, token: str,
                           base_url: str = "https://api.github.com") -> "GHClient":
        """Create a client around a single token.

        Args:
            token: The credential value.
            base_url: REST API base URL.

        Returns:
            A client whose pool holds only that token.
        """
        return cls(TokenManager.from_single_token(token), base_url)

    def _update_limits(self, r: requests.Response, token: str) -> None:
        """Feed the response's rate-limit headers back to the token manager."""
        remaining = int(r.headers.get("x-ratelimit-remaining", 5000))
        reset = float(r.headers.get("x-ratelimit-reset", 0))
        self._tm.update_rate_limit(token, remaining, reset)

    def _get(self, path: str,
             params: dict[str, Any] | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        """GET ``path`` with the next token and return the decoded JSON.

        Args:
            path: Path relative to the API base, starting with ``/``.
            params: Optional query parameters.

        Returns:
            The response body: an object for most endpoints, an array for the
            directory-contents and branch listings.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        token = self._tm.get_token()
        r = self._session.get(
            f"{self.BASE}{path}", params=params,
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        self._update_limits(r, token)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST ``body`` as JSON and return the decoded JSON object; raises on non-2xx."""
        token = self._tm.get_token()
        r = self._session.post(
            f"{self.BASE}{path}", json=body,
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        self._update_limits(r, token)
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """PATCH ``body`` as JSON and return the decoded JSON object; raises on non-2xx."""
        token = self._tm.get_token()
        r = self._session.patch(
            f"{self.BASE}{path}", json=body,
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        self._update_limits(r, token)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, body: dict[str, Any] | None = None) -> requests.Response:
        """PUT ``body`` as JSON and return the raw response; the caller checks the status."""
        token = self._tm.get_token()
        r = self._session.put(
            f"{self.BASE}{path}", json=body,
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        self._update_limits(r, token)
        return r

    def _delete(self, path: str) -> requests.Response:
        """DELETE ``path`` and return the raw response; the caller checks the status."""
        token = self._tm.get_token()
        r = self._session.delete(
            f"{self.BASE}{path}",
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        self._update_limits(r, token)
        return r

    # ── Repo operations ─────────────────────────────────────────────────────

    def repo_exists(self, org: str, repo: str) -> bool:
        """Report whether the repository exists and is visible to the token.

        Args:
            org: Organisation or user login.
            repo: Repository name.

        Returns:
            True on a 2xx response, False on 404.

        Raises:
            requests.HTTPError: On any other non-2xx response.
        """
        try:
            self._get(f"/repos/{org}/{repo}")
            return True
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return False
            raise

    def get_repo(self, org: str, repo: str) -> dict:
        """Fetch the repository record.

        Args:
            org: Organisation or user login.
            repo: Repository name.

        Returns:
            The repository record.

        Raises:
            requests.HTTPError: On a non-2xx response, including 404 when it does not exist.
        """
        return self._get(f"/repos/{org}/{repo}")

    def create_private_repo(self, org: str, repo: str, description: str = "") -> dict:
        """Create a private repository in an organisation.

        Migrated code is always created private. Issues and the wiki are enabled and
        the repository is left empty for the mirror push.

        Args:
            org: Organisation login.
            repo: Repository name.
            description: Repository description.

        Returns:
            The created repository record.

        Raises:
            requests.HTTPError: On a non-2xx response, including 422 when the name is taken.
        """
        return self._post(f"/orgs/{org}/repos", {
            "name": repo, "private": True, "description": description,
            "has_issues": True, "has_wiki": True, "auto_init": False,
        })

    def archive_repo(self, org: str, repo: str) -> dict:
        """Mark the repository archived, which makes it read-only.

        Args:
            org: Organisation or user login.
            repo: Repository name.

        Returns:
            The updated repository record.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        return self._patch(f"/repos/{org}/{repo}", {"archived": True})

    def delete_repo(self, org: str, repo: str) -> bool:
        """Delete the repository.

        Args:
            org: Organisation or user login.
            repo: Repository name.

        Returns:
            True when GitHub answered with a 2xx status, False otherwise.
        """
        r = self._delete(f"/repos/{org}/{repo}")
        return r.ok

    # ── Issues ──────────────────────────────────────────────────────────────

    def create_issue(self, org: str, repo: str, title: str,
                     body: str = "", labels: list[str] | None = None) -> dict:
        """Open an issue, truncating the title to 255 and the body to 65535 characters.

        Args:
            org: Organisation or user login.
            repo: Repository name.
            title: Issue title.
            body: Issue body in Markdown.
            labels: Label names to apply.

        Returns:
            The created issue record.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        return self._post(f"/repos/{org}/{repo}/issues", {
            "title": title[:255], "body": body[:65535], "labels": labels or [],
        })

    def create_label(self, org: str, repo: str, name: str,
                     color: str = "0075ca") -> bool:
        """Create a label.

        Args:
            org: Organisation or user login.
            repo: Repository name.
            name: Label name.
            color: Six-digit hex colour without the leading ``#``.

        Returns:
            True when the label was created, False on any error, including when it
            already exists.
        """
        try:
            self._post(f"/repos/{org}/{repo}/labels", {"name": name, "color": color})
            return True
        except Exception:
            return False

    # ── Environments ────────────────────────────────────────────────────────

    def create_environment(self, org: str, repo: str, env_name: str,
                           reviewers: list[str] | None = None) -> bool:
        """Create or update a deployment environment.

        Args:
            org: Organisation or user login.
            repo: Repository name.
            env_name: Environment name; URL-encoded for the request.
            reviewers: Required reviewers in the shape GitHub's environments API expects.

        Returns:
            True when GitHub answered with a 2xx status, False on any error.
        """
        try:
            r = self._put(
                f"/repos/{org}/{repo}/environments/{quote(env_name, safe='')}",
                {"wait_timer": 0, "reviewers": reviewers or []},
            )
            return r.ok
        except Exception:
            return False

    # ── Branch protection ───────────────────────────────────────────────────

    def set_branch_protection(self, org: str, repo: str, branch: str,
                              required_reviewers: int = 1,
                              status_checks: list[str] | None = None) -> dict:
        """Protect a branch with required reviews and strict status checks.

        Args:
            org: Organisation or user login.
            repo: Repository name.
            branch: Branch name.
            required_reviewers: Approving reviews required before merging.
            status_checks: Status-check contexts that must pass.

        Returns:
            The resulting branch-protection record.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        return self._post(f"/repos/{org}/{repo}/branches/{branch}/protection", {
            "required_status_checks": {
                "strict": True,
                "checks": [{"context": c} for c in (status_checks or [])],
            },
            "enforce_admins": False,
            "required_pull_request_reviews": {
                "required_approving_review_count": required_reviewers,
                "dismiss_stale_reviews": True,
            },
            "restrictions": None,
        })

    # ── Teams ───────────────────────────────────────────────────────────────

    def add_team_to_repo(self, org: str, team_slug: str, repo: str,
                         permission: str = "push") -> None:
        """Grant a team access to a repository.

        Args:
            org: Organisation login.
            team_slug: Team slug.
            repo: Repository name.
            permission: GitHub permission level such as ``pull``, ``push`` or ``admin``.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        r = self._put(
            f"/orgs/{org}/teams/{team_slug}/repos/{org}/{repo}",
            {"permission": permission},
        )
        r.raise_for_status()

    # ── Secrets ─────────────────────────────────────────────────────────────

    def get_repo_public_key(self, org: str, repo: str) -> dict:
        """Fetch the public key used to encrypt Actions secrets for the repository.

        Args:
            org: Organisation or user login.
            repo: Repository name.

        Returns:
            A mapping with ``key_id`` and the base64 ``key``.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        return self._get(f"/repos/{org}/{repo}/actions/secrets/public-key")

    def create_secret(self, org: str, repo: str, secret_name: str,
                      encrypted_value: str, key_id: str) -> None:
        """Create or update an Actions secret from an already encrypted value.

        The plaintext never passes through this client: the caller seals it with
        the repository public key before calling.

        Args:
            org: Organisation or user login.
            repo: Repository name.
            secret_name: Secret name.
            encrypted_value: The value sealed with the repository public key, base64-encoded.
            key_id: ``key_id`` of the public key used for sealing.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        r = self._put(
            f"/repos/{org}/{repo}/actions/secrets/{secret_name}",
            {"encrypted_value": encrypted_value, "key_id": key_id},
        )
        r.raise_for_status()

    # ── Validation helpers (for post-migration) ─────────────────────────────

    def list_directory(self, org: str, repo: str, path: str, ref: str) -> list[dict]:
        """List the entries of a directory at a given ref.

        Args:
            org: Organisation or user login.
            repo: Repository name.
            path: Directory path inside the repository.
            ref: Branch, tag or commit to read.

        Returns:
            Content entries; empty when the path is a file, does not exist, or the
            request fails.
        """
        try:
            data = self._get(
                f"/repos/{org}/{repo}/contents/{quote(path, safe='/')}",
                params={"ref": ref},
            )
            if isinstance(data, list):
                return data
            return []
        except Exception:
            return []

    def list_branches(self, org: str, repo: str) -> list[dict]:
        """List every branch, 100 per page.

        Args:
            org: Organisation or user login.
            repo: Repository name.

        Returns:
            Branch records; the pages fetched so far when a request fails.
        """
        branches = []
        page = 1
        while True:
            try:
                batch = self._get(
                    f"/repos/{org}/{repo}/branches",
                    params={"per_page": 100, "page": page},
                )
                branches.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
            except Exception:
                break
        return branches


    def list_workflows(self, org: str, repo: str) -> list[dict]:
        """List the repository's Actions workflows.

        Args:
            org: Organisation or user login.
            repo: Repository name.

        Returns:
            Workflow records; empty when the request fails.
        """
        try:
            return self._get(
                f"/repos/{org}/{repo}/actions/workflows"
            ).get("workflows", [])
        except Exception:
            return []

    # ── Branch + content operations (used by push-workflows) ────────────────

    def get_default_branch(self, org: str, repo: str) -> str:
        """Return the repository's default branch name.

        Args:
            org: Organisation or user login.
            repo: Repository name.

        Returns:
            The default branch, or ``main`` when the record does not name one.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        return self._get(f"/repos/{org}/{repo}").get("default_branch", "main")

    def set_default_branch(self, org: str, repo: str, branch: str) -> dict:
        """Change the repository's default branch.

        Args:
            org: Organisation or user login.
            repo: Repository name.
            branch: Existing branch to make the default.

        Returns:
            The updated repository record.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        return self._patch(f"/repos/{org}/{repo}", {"default_branch": branch})

    def get_branch_sha(self, org: str, repo: str, branch: str) -> str:
        """Return the commit SHA a branch points at.

        Args:
            org: Organisation or user login.
            repo: Repository name.
            branch: Branch name.

        Returns:
            The 40-character commit SHA.

        Raises:
            requests.HTTPError: On a non-2xx response; 404 for an unknown branch,
                409 for an empty repository.
        """
        data = self._get(f"/repos/{org}/{repo}/git/ref/heads/{branch}")
        return data["object"]["sha"]

    def create_branch(self, org: str, repo: str, branch: str, sha: str) -> dict:
        """Create a branch pointing at a commit.

        Args:
            org: Organisation or user login.
            repo: Repository name.
            branch: New branch name.
            sha: Commit SHA the branch starts from.

        Returns:
            The created git reference record.

        Raises:
            requests.HTTPError: On a non-2xx response, including 422 when the branch exists.
        """
        return self._post(
            f"/repos/{org}/{repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": sha},
        )

    def get_file_sha(self, org: str, repo: str, path: str, ref: str) -> str:
        """Return the blob SHA of a file at a given ref.

        Args:
            org: Organisation or user login.
            repo: Repository name.
            path: File path inside the repository.
            ref: Branch, tag or commit to read.

        Returns:
            The blob SHA; empty when the path is missing, is a directory, or the
            request fails.
        """
        try:
            data = self._get(f"/repos/{org}/{repo}/contents/{path}",
                             params={"ref": ref})
            return data.get("sha", "") if isinstance(data, dict) else ""
        except Exception:
            return ""

    def put_file(self, repo: RepoConfig, path: str, content_b64: str,
                 branch: str, message: str) -> dict:
        """Create or update one file on a branch with a single commit.

        The existing blob SHA, if any, is looked up first so that an update replaces
        the file instead of failing with 422.

        Args:
            repo: The migration target; ``gh_org`` and ``gh_repo`` name the repository.
            path: File path inside the repository.
            content_b64: File content, base64-encoded.
            branch: Branch to commit to.
            message: Commit message.

        Returns:
            The content and commit record GitHub returns.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        body: dict = {"message": message, "content": content_b64, "branch": branch}
        sha = self.get_file_sha(repo.gh_org, repo.gh_repo, path, branch)
        if sha:
            body["sha"] = sha
        r = self._put(f"/repos/{repo.gh_org}/{repo.gh_repo}/contents/{path}", body)
        r.raise_for_status()
        return r.json()

    def create_pull_request(self, repo: RepoConfig, title: str, body: str,
                            head: str, base: str) -> dict:
        """Open a pull request.

        Args:
            repo: The migration target; ``gh_org`` and ``gh_repo`` name the repository.
            title: Pull request title.
            body: Pull request body in Markdown.
            head: Branch holding the changes.
            base: Branch to merge into.

        Returns:
            The created pull request record.

        Raises:
            requests.HTTPError: On a non-2xx response, including 422 when a pull
                request for ``head`` already exists.
        """
        return self._post(
            f"/repos/{repo.gh_org}/{repo.gh_repo}/pulls",
            {"title": title, "body": body, "head": head, "base": base},
        )

    @property
    def token_manager(self) -> TokenManager:
        """The token pool this client draws from."""
        return self._tm
