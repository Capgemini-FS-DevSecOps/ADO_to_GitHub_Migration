"""GitHub REST API client with multi-token support."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

from ado2gh.clients.token_manager import TokenManager
from ado2gh.http_utils import make_session
from ado2gh.logging_config import log


class GHClient:
    """GitHub API client with rate-limit-aware multi-token rotation."""

    def __init__(self, token_manager: TokenManager,
                 base_url: str = "https://api.github.com"):
        self.BASE = base_url.rstrip("/")
        self._tm = token_manager
        self.session = make_session()
        self.session.headers.update({
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    @classmethod
    def from_single_token(cls, token: str,
                           base_url: str = "https://api.github.com") -> "GHClient":
        return cls(TokenManager.from_single_token(token), base_url)

    def _auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self._tm.get_token()}"}

    def _update_limits(self, r: requests.Response, token: str):
        remaining = int(r.headers.get("x-ratelimit-remaining", 5000))
        reset = float(r.headers.get("x-ratelimit-reset", 0))
        self._tm.update_rate_limit(token, remaining, reset)

    def _get(self, path: str, params: dict = None) -> Any:
        token = self._tm.get_token()
        r = self.session.get(
            f"{self.BASE}{path}", params=params,
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        self._update_limits(r, token)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict = None) -> Any:
        token = self._tm.get_token()
        r = self.session.post(
            f"{self.BASE}{path}", json=body,
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        self._update_limits(r, token)
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, body: dict) -> Any:
        token = self._tm.get_token()
        r = self.session.patch(
            f"{self.BASE}{path}", json=body,
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        self._update_limits(r, token)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, body: dict = None) -> requests.Response:
        token = self._tm.get_token()
        r = self.session.put(
            f"{self.BASE}{path}", json=body,
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        self._update_limits(r, token)
        return r

    def _delete(self, path: str) -> requests.Response:
        token = self._tm.get_token()
        r = self.session.delete(
            f"{self.BASE}{path}",
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        self._update_limits(r, token)
        return r

    # ── Repo operations ─────────────────────────────────────────────────────

    def repo_exists(self, org: str, repo: str) -> bool:
        try:
            self._get(f"/repos/{org}/{repo}")
            return True
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return False
            raise

    def get_repo(self, org: str, repo: str) -> dict:
        return self._get(f"/repos/{org}/{repo}")

    def create_repo(self, org: str, repo: str, private: bool = True,
                    description: str = "") -> dict:
        return self._post(f"/orgs/{org}/repos", {
            "name": repo, "private": private, "description": description,
            "has_issues": True, "has_wiki": True, "auto_init": False,
        })

    def archive_repo(self, org: str, repo: str) -> dict:
        return self._patch(f"/repos/{org}/{repo}", {"archived": True})

    def delete_repo(self, org: str, repo: str) -> bool:
        r = self._delete(f"/repos/{org}/{repo}")
        return r.ok

    # ── Issues ──────────────────────────────────────────────────────────────

    def create_issue(self, org: str, repo: str, title: str,
                     body: str = "", labels: list = None) -> dict:
        return self._post(f"/repos/{org}/{repo}/issues", {
            "title": title[:255], "body": body[:65535], "labels": labels or [],
        })

    def create_label(self, org: str, repo: str, name: str,
                     color: str = "0075ca") -> bool:
        try:
            self._post(f"/repos/{org}/{repo}/labels", {"name": name, "color": color})
            return True
        except Exception:
            return False

    # ── Environments ────────────────────────────────────────────────────────

    def create_environment(self, org: str, repo: str, env_name: str,
                           reviewers: list[str] = None) -> bool:
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
                              status_checks: list = None) -> dict:
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
                         permission: str = "push"):
        r = self._put(
            f"/orgs/{org}/teams/{team_slug}/repos/{org}/{repo}",
            {"permission": permission},
        )
        r.raise_for_status()

    # ── Secrets ─────────────────────────────────────────────────────────────

    def get_repo_public_key(self, org: str, repo: str) -> dict:
        return self._get(f"/repos/{org}/{repo}/actions/secrets/public-key")

    def create_secret(self, org: str, repo: str, secret_name: str,
                      encrypted_value: str, key_id: str):
        r = self._put(
            f"/repos/{org}/{repo}/actions/secrets/{secret_name}",
            {"encrypted_value": encrypted_value, "key_id": key_id},
        )
        r.raise_for_status()

    # ── Validation helpers (for post-migration) ─────────────────────────────

    def list_branches(self, org: str, repo: str) -> list[dict]:
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

    def list_issues(self, org: str, repo: str, state: str = "all") -> list[dict]:
        try:
            return self._get(
                f"/repos/{org}/{repo}/issues",
                params={"state": state, "per_page": 100},
            )
        except Exception:
            return []

    def list_workflows(self, org: str, repo: str) -> list[dict]:
        try:
            return self._get(
                f"/repos/{org}/{repo}/actions/workflows"
            ).get("workflows", [])
        except Exception:
            return []

    @property
    def token_manager(self) -> TokenManager:
        return self._tm
