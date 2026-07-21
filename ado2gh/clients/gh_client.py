"""GitHub REST API client with multi-token support."""
from __future__ import annotations

import base64
import re
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from urllib.parse import quote, urlsplit

import requests

from ado2gh.clients.token_manager import TokenManager
from ado2gh.http_utils import make_session
from ado2gh.logging_config import log


class GHClient:
    """GitHub API client with rate-limit-aware multi-token rotation."""

    def __init__(self, token_manager: TokenManager,
                 base_url: str = "https://api.github.com", *,
                 enterprise_slug: str = "",
                 api_version: str = "2022-11-28"):
        parsed = urlsplit(str(base_url).rstrip("/"))
        if (
            parsed.scheme != "https" or not parsed.netloc
            or parsed.username or parsed.password
            or parsed.query or parsed.fragment
        ):
            raise ValueError(
                "GitHub API URL must be absolute HTTPS without credentials, "
                "query, or fragment"
            )
        self.BASE = str(base_url).rstrip("/")
        if enterprise_slug and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9-]{0,99}", str(enterprise_slug)
        ):
            raise ValueError("GitHub enterprise slug is invalid")
        if not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", str(api_version)):
            raise ValueError("GitHub API version must be YYYY-MM-DD")
        self.enterprise_slug = str(enterprise_slug)
        self._tm = token_manager
        self._session_local = threading.local()
        self._session_override = None
        self._session_headers = {
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": str(api_version),
        }

    @property
    def session(self):
        if self._session_override is not None:
            return self._session_override
        session = getattr(self._session_local, "value", None)
        if session is None:
            # GHClient owns 403/429 handling so it can rotate credentials;
            # adapter-level rate retries would repeatedly use the same token.
            session = make_session(retry_rate_limits=False)
            session.headers.update(self._session_headers)
            self._session_local.value = session
        return session

    @session.setter
    def session(self, value):
        self._session_override = value

    @classmethod
    def from_single_token(cls, token: str,
                           base_url: str = "https://api.github.com") -> "GHClient":
        return cls(TokenManager.from_single_token(token), base_url)

    @staticmethod
    def _header(response: requests.Response, name: str) -> Optional[str]:
        headers = getattr(response, "headers", None) or {}
        value = headers.get(name)
        if value is not None:
            return str(value)
        lower_name = name.lower()
        for key, candidate in headers.items():
            if str(key).lower() == lower_name:
                return str(candidate)
        return None

    @staticmethod
    def _optional_int(value: Optional[str]) -> Optional[int]:
        if value is None:
            return None
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _retry_after_at(
        cls, response: requests.Response, now: float
    ) -> Optional[float]:
        value = cls._header(response, "retry-after")
        if not value:
            return None
        try:
            return now + max(0.0, float(value))
        except (TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(now, parsed.timestamp())
            except (TypeError, ValueError, OverflowError):
                return None

    @classmethod
    def _rate_limit_metadata(cls, response: requests.Response) -> dict:
        remaining = cls._optional_int(
            cls._header(response, "x-ratelimit-remaining")
        )
        reset_at = cls._optional_float(
            cls._header(response, "x-ratelimit-reset")
        )
        limit = cls._optional_int(cls._header(response, "x-ratelimit-limit"))
        try:
            status = int(getattr(response, "status_code", 0) or 0)
        except (TypeError, ValueError):
            status = 0

        message = ""
        if status in {403, 429}:
            try:
                payload = response.json()
                if isinstance(payload, dict) and isinstance(
                    payload.get("message"), str
                ):
                    message = payload["message"].lower()
            except (TypeError, ValueError, requests.RequestException):
                raw_text = getattr(response, "text", "")
                if isinstance(raw_text, str):
                    message = raw_text.lower()

        retry_after_at = cls._retry_after_at(response, time.time())
        primary = status in {403, 429} and remaining == 0
        secondary = status in {403, 429} and (
            status == 429
            or retry_after_at is not None
            or "secondary rate limit" in message
            or "abuse detection" in message
        )
        return {
            "remaining": remaining,
            "reset_at": reset_at,
            "limit": limit,
            "primary_rate_limited": primary,
            "secondary_rate_limited": secondary,
            "retry_after_at": retry_after_at,
            "is_rate_limited": primary or secondary,
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
        absolute_url: bool = False,
    ) -> requests.Response:
        """Send one request with leased quota and bounded token rotation."""
        if absolute_url:
            target = urlsplit(path)
            trusted = urlsplit(self.BASE)
            if target.scheme != "https" or target.netloc != trusted.netloc:
                raise ValueError(
                    "Absolute GitHub request URL must remain on the configured host"
                )
            url = path
        else:
            url = f"{self.BASE}{path}"
        reservation = self._tm.reserve_token()
        attempted_tokens: set[str] = set()
        max_attempts = max(1, self._tm.token_count)

        for attempt in range(max_attempts):
            attempted_tokens.add(reservation.token)
            response: Optional[requests.Response] = None
            metadata = {"is_rate_limited": False}
            try:
                request = getattr(self.session, method.lower())
                kwargs: dict[str, Any] = {
                    "headers": {
                        "Authorization": f"Bearer {reservation.token}"
                    },
                    "timeout": 30,
                    "allow_redirects": False,
                }
                if params is not None:
                    kwargs["params"] = params
                if body is not None:
                    kwargs["json"] = body
                response = request(url, **kwargs)
            finally:
                if response is None:
                    # The transport may have committed the request before an
                    # exception. Retain its one-unit reservation, but always
                    # release in-flight tracking.
                    self._tm.complete_reservation(reservation)
                else:
                    try:
                        metadata = self._rate_limit_metadata(response)
                    except Exception:
                        # Malformed response metadata must not leak the lease.
                        # Keep the already-reserved quota cost and let normal
                        # status handling surface the original response.
                        log.warning(
                            "Could not parse GitHub rate-limit headers",
                            exc_info=True,
                        )
                        metadata = {
                            "remaining": None,
                            "reset_at": None,
                            "limit": None,
                            "primary_rate_limited": False,
                            "secondary_rate_limited": False,
                            "retry_after_at": None,
                            "is_rate_limited": False,
                        }
                    self._tm.complete_reservation(
                        reservation,
                        remaining=metadata["remaining"],
                        reset_at=metadata["reset_at"],
                        limit=metadata["limit"],
                        primary_rate_limited=metadata[
                            "primary_rate_limited"
                        ],
                        secondary_rate_limited=metadata[
                            "secondary_rate_limited"
                        ],
                        retry_after_at=metadata["retry_after_at"],
                    )

            if response is None:  # pragma: no cover - the exception escapes
                raise RuntimeError("GitHub request produced no response")
            if not metadata["is_rate_limited"]:
                return response
            if attempt + 1 >= max_attempts:
                return response

            next_reservation = self._tm.try_reserve_token(attempted_tokens)
            if next_reservation is None:
                # Do not sleep inside an automatic retry. The recorded reset or
                # Retry-After fence will be honored by the next top-level call.
                return response
            log.warning(
                "GitHub rate limit encountered; rotating to another credential"
            )
            reservation = next_reservation

        raise RuntimeError("GitHub request retry loop exhausted unexpectedly")

    def _get(self, path: str, params: dict = None) -> Any:
        r = self._request("get", path, params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict = None) -> Any:
        r = self._request("post", path, body=body)
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, body: dict) -> Any:
        r = self._request("patch", path, body=body)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, body: dict = None) -> requests.Response:
        return self._request("put", path, body=body)

    def _delete(self, path: str) -> requests.Response:
        return self._request("delete", path)

    def get_org(self, org: str) -> dict[str, Any]:
        """Return organization metadata, including its immutable node ID."""
        value = self._get(f"/orgs/{quote(org, safe='')}")
        if not isinstance(value, dict):
            raise RuntimeError("GitHub organization response must be an object")
        return value

    # ── Repo operations ─────────────────────────────────────────────────────

    def repo_exists(self, org: str, repo: str) -> bool:
        try:
            self._get(f"/repos/{org}/{repo}")
            return True
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
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

    def delete_repo_by_id(self, repository_node_id: str) -> bool:
        """Delete the exact immutable repository node via GraphQL.

        REST deletion is name-addressed and therefore has a check/delete race:
        a repository can be replaced after an ID check but before DELETE.  The
        GraphQL mutation is ID-addressed, so it can only delete the repository
        whose ownership receipt was approved.
        """
        repository_node_id = str(repository_node_id).strip()
        if not repository_node_id:
            raise ValueError("repository_node_id is required")
        if self.BASE == "https://api.github.com":
            endpoint = f"{self.BASE}/graphql"
        elif self.BASE.endswith("/api/v3"):
            endpoint = self.BASE[:-7] + "/api/graphql"
        else:
            raise RuntimeError(
                "Cannot derive the GraphQL endpoint from the configured GitHub API URL"
            )
        response = self._request(
            "post",
            endpoint,
            absolute_url=True,
            body={
                "query": (
                    "mutation DeleteOwnedRepository($id: ID!) {"
                    " deleteRepository(input: {repositoryId: $id}) {"
                    "  clientMutationId"
                    " }"
                    "}"
                ),
                "variables": {"id": repository_node_id},
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("errors"):
            raise RuntimeError("GitHub rejected immutable-ID repository deletion")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(
            data.get("deleteRepository"), dict
        ):
            raise RuntimeError("GitHub repository deletion returned malformed data")
        return True

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
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 422:
                # 422 is also returned for invalid labels. Confirm the exact
                # resource exists before treating the operation as idempotent.
                self._get(
                    f"/repos/{org}/{repo}/labels/{quote(name, safe='')}"
                )
                return True
            raise

    # ── Environments ────────────────────────────────────────────────────────

    def get_environment(
        self, org: str, repo: str, env_name: str
    ) -> Optional[dict]:
        """Return one environment, or ``None`` when it does not exist."""
        try:
            value = self._get(
                f"/repos/{org}/{repo}/environments/{quote(env_name, safe='')}"
            )
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise
        if not isinstance(value, dict):
            raise TypeError("GitHub environment response must be an object")
        return value

    def list_environment_deployment_protection_rules(
        self, org: str, repo: str, env_name: str
    ) -> list[dict]:
        """Return the complete enabled custom-rule set for an environment."""
        value = self._get(
            f"/repos/{org}/{repo}/environments/{quote(env_name, safe='')}"
            "/deployment_protection_rules"
        )
        if not isinstance(value, dict):
            raise TypeError(
                "GitHub deployment-protection response must be an object"
            )
        rules = value.get("custom_deployment_protection_rules")
        total = value.get("total_count")
        if (
            not isinstance(rules, list)
            or isinstance(total, bool)
            or not isinstance(total, int)
            or total != len(rules)
            or any(not isinstance(rule, dict) for rule in rules)
        ):
            raise RuntimeError(
                "GitHub deployment-protection response is incomplete"
            )
        return rules

    def create_environment(self, org: str, repo: str, env_name: str,
                           reviewers: list[str] = None) -> bool:
        r = self._put(
            f"/repos/{org}/{repo}/environments/{quote(env_name, safe='')}",
            {"wait_timer": 0, "reviewers": reviewers or []},
        )
        r.raise_for_status()
        return True

    # ── Branch protection ───────────────────────────────────────────────────

    def get_branch_protection(
        self, org: str, repo: str, branch: str
    ) -> Optional[dict]:
        """Return branch protection, or ``None`` for an unprotected branch."""
        try:
            value = self._get(
                f"/repos/{org}/{repo}/branches/"
                f"{quote(branch, safe='')}/protection"
            )
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise
        if not isinstance(value, dict):
            raise TypeError("GitHub branch-protection response must be an object")
        return value

    def set_branch_protection(self, org: str, repo: str, branch: str,
                              required_reviewers: int = 1,
                              status_checks: list = None) -> dict:
        r = self._put(f"/repos/{org}/{repo}/branches/{quote(branch, safe='')}/protection", {
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
        r.raise_for_status()
        return r.json()

    # ── Teams ───────────────────────────────────────────────────────────────

    def get_team_repo_permission(
        self, org: str, team_slug: str, repo: str
    ) -> str:
        """Return the exact repository role currently granted to a team."""
        value = self._get(
            f"/orgs/{org}/teams/{quote(team_slug, safe='')}/repos/{org}/{repo}"
        )
        if not isinstance(value, dict):
            raise TypeError("GitHub team repository response must be an object")
        role = value.get("role_name")
        if role in {"pull", "triage", "push", "maintain", "admin"}:
            return str(role)
        permissions = value.get("permissions")
        if not isinstance(permissions, dict):
            raise RuntimeError(
                "GitHub team repository response has no exact role metadata"
            )
        # Permission booleans are cumulative; choose the strongest observed
        # standard role. Custom organization roles are rejected because they
        # cannot be proven equal to the approved canonical mapping.
        for candidate in ("admin", "maintain", "push", "triage", "pull"):
            if permissions.get(candidate) is True:
                return candidate
        raise RuntimeError("GitHub team repository permission readback is empty")

    def add_team_to_repo(self, org: str, team_slug: str, repo: str,
                         permission: str = "push"):
        r = self._put(
            f"/orgs/{org}/teams/{team_slug}/repos/{org}/{repo}",
            {"permission": permission},
        )
        r.raise_for_status()

    def get_team_repo_permission(
        self, org: str, team_slug: str, repo: str
    ) -> str:
        """Return the team's exact GitHub repository role."""
        value = self._get(
            f"/orgs/{org}/teams/{quote(team_slug, safe='')}/repos/{org}/{repo}"
        )
        if not isinstance(value, dict):
            raise TypeError("GitHub team repository response must be an object")
        role = value.get("role_name")
        if isinstance(role, str) and role in {
            "pull", "triage", "push", "maintain", "admin"
        }:
            return role
        permissions = value.get("permissions")
        if not isinstance(permissions, dict):
            raise RuntimeError(
                "GitHub team repository response omitted role_name/permissions"
            )
        for candidate in ("admin", "maintain", "push", "triage", "pull"):
            if permissions.get(candidate) is True:
                return candidate
        raise RuntimeError("GitHub team has no readable repository permission")

    def list_repo_collaborators(self, org: str, repo: str) -> list[dict]:
        rows: list[dict] = []
        page = 1
        while True:
            batch = self._get(
                f"/repos/{org}/{repo}/collaborators",
                params={"affiliation": "all", "per_page": 100, "page": page},
            )
            if not isinstance(batch, list) or any(
                not isinstance(item, dict) for item in batch
            ):
                raise TypeError("GitHub collaborator inventory is malformed")
            rows.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        ids = [str(item.get("node_id") or item.get("id") or "") for item in rows]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise RuntimeError("GitHub collaborator inventory is duplicated")
        return rows

    def list_repo_direct_collaborators(self, org: str, repo: str) -> list[dict]:
        """Return only users granted direct/outside repository access.

        ``affiliation=all`` also includes users who inherit access from a team
        or the organization base role, so it cannot prove that no unreviewed
        direct grant exists.  The dedicated inventory keeps those access
        sources disjoint for exact policy validation.
        """
        rows: list[dict] = []
        page = 1
        while True:
            batch = self._get(
                f"/repos/{org}/{repo}/collaborators",
                params={"affiliation": "direct", "per_page": 100, "page": page},
            )
            if not isinstance(batch, list) or any(
                not isinstance(item, dict) for item in batch
            ):
                raise TypeError(
                    "GitHub direct-collaborator inventory is malformed"
                )
            rows.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        ids = [str(item.get("node_id") or item.get("id") or "") for item in rows]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise RuntimeError(
                "GitHub direct-collaborator inventory is duplicated"
            )
        return rows

    def list_repo_invitations(self, org: str, repo: str) -> list[dict]:
        """Return pending collaborator grants that could become live later."""
        rows: list[dict] = []
        page = 1
        while True:
            batch = self._get(
                f"/repos/{org}/{repo}/invitations",
                params={"per_page": 100, "page": page},
            )
            if not isinstance(batch, list) or any(
                not isinstance(item, dict) for item in batch
            ):
                raise TypeError("GitHub repository invitation inventory is malformed")
            rows.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        ids = [str(item.get("id") or "") for item in rows]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise RuntimeError("GitHub repository invitation inventory is duplicated")
        return rows

    def list_deploy_keys(self, org: str, repo: str) -> list[dict]:
        """Return every repository deploy key, including write-capable keys."""
        rows: list[dict] = []
        page = 1
        while True:
            batch = self._get(
                f"/repos/{org}/{repo}/keys",
                params={"per_page": 100, "page": page},
            )
            if not isinstance(batch, list) or any(
                not isinstance(item, dict) for item in batch
            ):
                raise TypeError("GitHub deploy-key inventory is malformed")
            rows.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        ids = [str(item.get("id") or "") for item in rows]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise RuntimeError("GitHub deploy-key inventory is duplicated")
        return rows

    def list_repo_teams(self, org: str, repo: str) -> list[dict]:
        rows: list[dict] = []
        page = 1
        while True:
            batch = self._get(
                f"/repos/{org}/{repo}/teams",
                params={"per_page": 100, "page": page},
            )
            if not isinstance(batch, list) or any(
                not isinstance(item, dict) for item in batch
            ):
                raise TypeError("GitHub repository-team inventory is malformed")
            rows.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        ids = [str(item.get("node_id") or item.get("id") or "") for item in rows]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise RuntimeError("GitHub repository-team inventory is duplicated")
        return rows

    def list_team_members(self, org: str, team_slug: str) -> list[dict]:
        rows: list[dict] = []
        page = 1
        while True:
            batch = self._get(
                f"/orgs/{org}/teams/{team_slug}/members",
                params={"role": "all", "per_page": 100, "page": page},
            )
            if not isinstance(batch, list) or any(
                not isinstance(item, dict) for item in batch
            ):
                raise TypeError("GitHub team-member inventory is malformed")
            rows.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        ids = [str(item.get("node_id") or item.get("id") or "") for item in rows]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            raise RuntimeError("GitHub team-member inventory is duplicated")
        return rows

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

    def list_actions_secret_metadata(
        self, org: str, repo: str
    ) -> list[dict[str, str]]:
        """Return complete, version-bearing repository secret metadata.

        GitHub never returns secret values.  ``updated_at`` is the observable
        rotation version and is therefore retained exactly for credential
        canary binding.  A changing total, malformed page, duplicate name, or
        incomplete traversal fails closed.
        """
        secrets: list[dict[str, str]] = []
        page = 1
        expected_total: Optional[int] = None
        while True:
            data = self._get(
                f"/repos/{org}/{repo}/actions/secrets",
                params={"per_page": 100, "page": page},
            )
            if not isinstance(data, dict) or not isinstance(
                data.get("secrets"), list
            ):
                raise TypeError("GitHub Actions secrets response is incomplete")
            total = data.get("total_count")
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                raise TypeError("GitHub Actions secrets total_count is invalid")
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise RuntimeError(
                    "GitHub Actions secret inventory changed while paginating"
                )
            batch = data["secrets"]
            for item in batch:
                if not isinstance(item, dict) or any(
                    not isinstance(item.get(field), str) or not item.get(field)
                    for field in ("name", "created_at", "updated_at")
                ):
                    raise TypeError("GitHub Actions secret entry is malformed")
                secrets.append({
                    "name": item["name"],
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                })
            if len(batch) < 100:
                break
            page += 1
        names = [item["name"] for item in secrets]
        if len(secrets) != expected_total or len(set(names)) != len(names):
            raise RuntimeError(
                "GitHub Actions secret inventory was incomplete or duplicated"
            )
        return sorted(secrets, key=lambda item: item["name"])

    def list_actions_secret_names(self, org: str, repo: str) -> list[str]:
        """Return a complete, stable repository Actions-secret name set."""
        return [
            item["name"]
            for item in self.list_actions_secret_metadata(org, repo)
        ]

    # ── Validation helpers (for post-migration) ─────────────────────────────

    def list_branches(self, org: str, repo: str) -> list[dict]:
        branches = []
        page = 1
        while True:
            batch = self._get(
                f"/repos/{org}/{repo}/branches",
                params={"per_page": 100, "page": page},
            )
            branches.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return branches

    def list_git_refs(self, org: str, repo: str, namespace: str) -> list[dict]:
        """Return every raw Git ref in one migratable namespace.

        The matching-refs API preserves annotated tag-object SHAs, unlike the
        high-level tags API which can expose a peeled commit.  Explicit page
        traversal is required for enterprise repositories with hundreds or
        thousands of branches/tags.
        """
        namespace = str(namespace).removeprefix("refs/").strip("/")
        if namespace not in {"heads", "tags"}:
            raise ValueError("GitHub ref namespace must be 'heads' or 'tags'")
        refs: list[dict] = []
        page = 1
        while True:
            batch = self._get(
                f"/repos/{org}/{repo}/git/matching-refs/{namespace}/",
                params={"per_page": 100, "page": page},
            )
            if not isinstance(batch, list):
                raise TypeError("GitHub matching-refs response must be a list")
            refs.extend(batch)
            if len(batch) < 100:
                return refs
            page += 1

    def list_issues(self, org: str, repo: str, state: str = "all") -> list[dict]:
        issues: list[dict] = []
        page = 1
        while True:
            batch = self._get(
                f"/repos/{org}/{repo}/issues",
                params={"state": state, "per_page": 100, "page": page},
            )
            issues.extend(batch)
            if len(batch) < 100:
                return issues
            page += 1

    def find_issue_by_marker(self, org: str, repo: str, marker: str) -> dict:
        """Return an existing migrated issue containing a stable marker."""
        for issue in self.list_issues(org, repo, state="all"):
            # Pull requests are also returned by the issues endpoint.
            if "pull_request" not in issue and marker in (issue.get("body") or ""):
                return issue
        return {}

    def list_workflows(self, org: str, repo: str) -> list[dict]:
        workflows: list[dict] = []
        expected_total: Optional[int] = None
        page = 1
        while True:
            data = self._get(
                f"/repos/{org}/{repo}/actions/workflows",
                params={"per_page": 100, "page": page},
            )
            if not isinstance(data, dict) or not isinstance(
                data.get("workflows"), list
            ):
                raise TypeError("GitHub workflows response is incomplete")
            total = data.get("total_count")
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                raise TypeError("GitHub workflows total_count is invalid")
            if expected_total is None:
                expected_total = total
            elif expected_total != total:
                raise RuntimeError(
                    "GitHub workflow inventory changed while paginating"
                )
            batch = data["workflows"]
            if any(not isinstance(item, dict) for item in batch):
                raise TypeError("GitHub workflow entry is malformed")
            workflows.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        identities = [
            (str(item.get("id", "")), str(item.get("path", "")))
            for item in workflows
        ]
        if (
            len(workflows) != expected_total
            or any(not identity[0] or not identity[1] for identity in identities)
            or len(set(identities)) != len(identities)
        ):
            raise RuntimeError(
                "GitHub workflow inventory was incomplete or duplicated"
            )
        return workflows

    def get_actions_permissions(self, org: str, repo: str) -> dict:
        data = self._get(f"/repos/{org}/{repo}/actions/permissions")
        if not isinstance(data, dict):
            raise TypeError("GitHub Actions permissions response must be an object")
        return data

    def get_actions_selected_policy(self, org: str, repo: str) -> dict:
        data = self._get(
            f"/repos/{org}/{repo}/actions/permissions/selected-actions"
        )
        if not isinstance(data, dict):
            raise TypeError("GitHub selected-actions policy must be an object")
        if not isinstance(data.get("github_owned_allowed"), bool) or not isinstance(
            data.get("verified_allowed"), bool
        ) or not isinstance(data.get("patterns_allowed"), list):
            raise TypeError("GitHub selected-actions policy is incomplete")
        if any(not isinstance(item, str) for item in data["patterns_allowed"]):
            raise TypeError("GitHub selected-actions patterns must be strings")
        return data

    def get_actions_access(self, org: str, repo: str) -> dict:
        """Return whether a private/internal repository may supply actions."""
        data = self._get(f"/repos/{org}/{repo}/actions/permissions/access")
        if not isinstance(data, dict) or data.get("access_level") not in {
            "none", "user", "organization", "enterprise"
        }:
            raise TypeError("GitHub Actions access response is incomplete")
        return data

    def list_actions_runners(self, org: str, repo: str) -> list[dict]:
        runners: list[dict] = []
        page = 1
        expected_total: Optional[int] = None
        while True:
            data = self._get(
                f"/repos/{org}/{repo}/actions/runners",
                params={"per_page": 100, "page": page},
            )
            if not isinstance(data, dict) or not isinstance(
                data.get("runners"), list
            ):
                raise TypeError("GitHub Actions runners response is incomplete")
            total = data.get("total_count")
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                raise TypeError("GitHub Actions runners total_count is invalid")
            if expected_total is None:
                expected_total = total
            elif expected_total != total:
                raise RuntimeError("GitHub runner inventory changed while paginating")
            batch = data["runners"]
            if any(not isinstance(item, dict) for item in batch):
                raise TypeError("GitHub Actions runner entry is malformed")
            runners.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        if len(runners) != expected_total:
            raise RuntimeError("GitHub Actions runner inventory is incomplete")
        return runners

    def list_repo_app_installations(self, org: str, repo: str) -> list[dict]:
        """Exhaustively list GitHub App installations that can reach a repo.

        The organization endpoint requires an org-owner credential with read
        administration. ``selected`` installations additionally require a
        GitHub App user access token capable of expanding
        ``/user/installations/{id}/repositories``. Any missing permission,
        malformed page, pagination drift, or inventory drift raises so callers
        fail closed. We intentionally do not infer that a selected installation
        is harmless when its repository set cannot be enumerated.
        """

        target = self.get_repo(org, repo)
        if not isinstance(target, dict):
            raise TypeError("GitHub target repository response is incomplete")
        target_id = target.get("id")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            raise TypeError("GitHub target repository has no immutable numeric ID")

        def selected_repository_ids(installation_id: int) -> set[int]:
            repositories: list[dict] = []
            page = 1
            expected_total: Optional[int] = None
            while True:
                endpoint = (
                    f"/enterprises/{quote(self.enterprise_slug, safe='')}"
                    f"/apps/organizations/{quote(str(org), safe='')}"
                    f"/installations/{installation_id}/repositories"
                    if self.enterprise_slug else
                    f"/user/installations/{installation_id}/repositories"
                )
                data = self._get(
                    endpoint,
                    params={"per_page": 100, "page": page},
                )
                if self.enterprise_slug:
                    batch = data
                    total = None
                else:
                    batch = data.get("repositories") if isinstance(data, dict) else None
                    total = data.get("total_count") if isinstance(data, dict) else None
                if not isinstance(batch, list):
                    raise TypeError(
                        "GitHub App selected-repository inventory is incomplete"
                    )
                if not self.enterprise_slug:
                    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                        raise TypeError(
                            "GitHub App selected-repository total_count is invalid"
                        )
                    if expected_total is None:
                        expected_total = total
                    elif expected_total != total:
                        raise RuntimeError(
                            "GitHub App selected-repository inventory changed while paginating"
                        )
                if any(not isinstance(item, dict) for item in batch):
                    raise TypeError(
                        "GitHub App selected-repository entry is malformed"
                    )
                repositories.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
            ids = [item.get("id") for item in repositories]
            if (
                (expected_total is not None and len(repositories) != expected_total)
                or any(isinstance(item, bool) or not isinstance(item, int) for item in ids)
                or len(set(ids)) != len(ids)
            ):
                raise RuntimeError(
                    "GitHub App selected-repository inventory is incomplete or duplicated"
                )
            return set(ids)

        def inventory_once() -> list[dict]:
            installations: list[dict] = []
            page = 1
            expected_total: Optional[int] = None
            while True:
                endpoint = (
                    f"/enterprises/{quote(self.enterprise_slug, safe='')}"
                    f"/apps/organizations/{quote(str(org), safe='')}/installations"
                    if self.enterprise_slug else
                    f"/orgs/{quote(str(org), safe='')}/installations"
                )
                data = self._get(
                    endpoint,
                    params={"per_page": 100, "page": page},
                )
                if self.enterprise_slug:
                    if not isinstance(data, list):
                        raise TypeError(
                            "Enterprise GitHub App installation inventory is malformed"
                        )
                    batch = []
                    for item in data:
                        value = item.get("value") if isinstance(item, dict) else None
                        if not isinstance(value, dict):
                            raise TypeError(
                                "Enterprise GitHub App installation entry is malformed"
                            )
                        batch.append(value)
                    total = None
                else:
                    batch = data.get("installations") if isinstance(data, dict) else None
                    total = data.get("total_count") if isinstance(data, dict) else None
                if not isinstance(batch, list):
                    raise TypeError(
                        "GitHub organization App installation inventory is malformed"
                    )
                if not self.enterprise_slug:
                    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                        raise TypeError(
                            "GitHub organization App installation total_count is invalid"
                        )
                    if expected_total is None:
                        expected_total = total
                    elif expected_total != total:
                        raise RuntimeError(
                            "GitHub App installation inventory changed while paginating"
                        )
                if any(
                    not isinstance(item, dict) for item in batch
                ):
                    raise TypeError(
                        "GitHub organization App installation inventory is malformed"
                    )
                installations.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
            ids = [item.get("id") for item in installations]
            if (
                (expected_total is not None and len(installations) != expected_total)
                or any(isinstance(item, bool) or not isinstance(item, int) for item in ids)
                or len(set(ids)) != len(ids)
            ):
                raise RuntimeError(
                    "GitHub organization App installation inventory is ambiguous"
                )

            accessible: list[dict] = []
            for installation in installations:
                installation_id = int(installation["id"])
                selection = installation.get("repository_selection")
                if selection not in {"all", "selected", "none"}:
                    raise TypeError(
                        "GitHub App repository_selection is invalid or missing"
                    )
                app_id = installation.get("app_id")
                permissions = installation.get("permissions")
                if (
                    isinstance(app_id, bool) or not isinstance(app_id, int)
                    or not isinstance(permissions, dict)
                    or any(
                        not isinstance(key, str) or value not in {"read", "write"}
                        for key, value in permissions.items()
                    )
                ):
                    raise TypeError("GitHub App installation metadata is incomplete")
                has_access = selection == "all"
                if selection == "selected":
                    has_access = target_id in selected_repository_ids(installation_id)
                if has_access:
                    accessible.append({
                        "installation_id": installation_id,
                        "app_id": app_id,
                        "app_slug": str(installation.get("app_slug") or ""),
                        "repository_selection": selection,
                        "permissions": {
                            key: permissions[key] for key in sorted(permissions)
                        },
                        "suspended_at": installation.get("suspended_at"),
                        "updated_at": str(installation.get("updated_at") or ""),
                    })
            return sorted(accessible, key=lambda item: item["installation_id"])

        # Count-less org pagination is read twice. An installation, selection,
        # permission, or selected-repository change during the observation
        # window blocks validation instead of producing a torn snapshot.
        first = inventory_once()
        second = inventory_once()
        target_after = self.get_repo(org, repo)
        if (
            first != second
            or not isinstance(target_after, dict)
            or target_after.get("id") != target_id
        ):
            raise RuntimeError(
                "GitHub App access inventory changed while being validated"
            )
        return first

    def get_commit_sha(self, org: str, repo: str, ref: str) -> str:
        value = self._get(
            f"/repos/{org}/{repo}/commits/{quote(str(ref), safe='')}"
        )
        if not isinstance(value, dict) or not isinstance(value.get("sha"), str):
            raise TypeError("GitHub commit resolution response is incomplete")
        return value["sha"]

    def list_pull_requests(self, org: str, repo: str, state: str = "open",
                           head: str = "", base: str = "") -> list[dict]:
        params = {"state": state, "per_page": 100}
        if head:
            params["head"] = f"{org}:{head}"
        if base:
            params["base"] = base
        pulls: list[dict] = []
        page = 1
        while True:
            batch = self._get(
                f"/repos/{org}/{repo}/pulls",
                params={**params, "page": page},
            )
            if not isinstance(batch, list):
                raise TypeError("GitHub pull-request response must be a list")
            pulls.extend(batch)
            if len(batch) < 100:
                return pulls
            page += 1

    def list_releases(self, org: str, repo: str) -> list[dict]:
        releases: list[dict] = []
        page = 1
        while True:
            batch = self._get(
                f"/repos/{org}/{repo}/releases",
                params={"per_page": 100, "page": page},
            )
            if not isinstance(batch, list):
                raise TypeError("GitHub releases response must be a list")
            releases.extend(batch)
            if len(batch) < 100:
                return releases
            page += 1

    def find_open_pull_request(self, org: str, repo: str, head: str,
                               base: str = "") -> dict:
        pulls = self.list_pull_requests(org, repo, state="open", head=head, base=base)
        return pulls[0] if pulls else {}

    # ── Branch + content operations (used by push-workflows) ────────────────

    def get_default_branch(self, org: str, repo: str) -> str:
        branch = self._get(f"/repos/{org}/{repo}").get("default_branch")
        if not isinstance(branch, str) or not branch.strip():
            raise ValueError(f"GitHub repo {org}/{repo} has no default branch")
        return branch.strip()

    def set_default_branch(self, org: str, repo: str, branch: str) -> dict:
        return self._patch(f"/repos/{org}/{repo}", {"default_branch": branch})

    def get_branch_sha(self, org: str, repo: str, branch: str) -> str:
        data = self._get(f"/repos/{org}/{repo}/git/ref/heads/{branch}")
        return data["object"]["sha"]

    def create_branch(self, org: str, repo: str, branch: str, sha: str) -> dict:
        return self._post(
            f"/repos/{org}/{repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": sha},
        )

    def get_git_commit(self, org: str, repo: str, commit_sha: str) -> dict:
        data = self._get(f"/repos/{org}/{repo}/git/commits/{commit_sha}")
        if not isinstance(data, dict):
            raise TypeError("GitHub Git commit response must be an object")
        return data

    def get_git_tree(
        self, org: str, repo: str, tree_sha: str, *, recursive: bool = False
    ) -> dict:
        params = {"recursive": "1"} if recursive else None
        data = self._get(
            f"/repos/{org}/{repo}/git/trees/{tree_sha}", params=params
        )
        if not isinstance(data, dict):
            raise TypeError("GitHub Git tree response must be an object")
        return data

    def create_git_blob(self, org: str, repo: str, content: bytes) -> dict:
        if not isinstance(content, bytes):
            raise TypeError("Git blob content must be bytes")
        return self._post(
            f"/repos/{org}/{repo}/git/blobs",
            {
                "content": base64.b64encode(content).decode("ascii"),
                "encoding": "base64",
            },
        )

    def create_git_tree(
        self,
        org: str,
        repo: str,
        *,
        base_tree_sha: str,
        entries: list[dict[str, str]],
    ) -> dict:
        return self._post(
            f"/repos/{org}/{repo}/git/trees",
            {"base_tree": base_tree_sha, "tree": entries},
        )

    def create_git_commit(
        self,
        org: str,
        repo: str,
        *,
        message: str,
        tree_sha: str,
        parents: list[str],
    ) -> dict:
        return self._post(
            f"/repos/{org}/{repo}/git/commits",
            {"message": message, "tree": tree_sha, "parents": parents},
        )

    def get_file_sha(self, org: str, repo: str, path: str, ref: str) -> str:
        try:
            data = self._get(f"/repos/{org}/{repo}/contents/{path}",
                             params={"ref": ref})
            return data.get("sha", "") if isinstance(data, dict) else ""
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return ""
            raise

    def get_file_content(
        self, org: str, repo: str, path: str, ref: str
    ) -> bytes:
        data = self._get(
            f"/repos/{org}/{repo}/contents/{path}", params={"ref": ref}
        )
        if not isinstance(data, dict) or data.get("encoding") != "base64" \
                or not isinstance(data.get("content"), str):
            raise TypeError("GitHub file content response is incomplete")
        try:
            # The Contents API may wrap base64 at fixed line widths. Remove
            # ASCII whitespace but keep strict alphabet/padding validation.
            encoded = "".join(data["content"].split())
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("GitHub file content is not valid base64") from exc
        if len(content) > 10_000_000:
            raise ValueError("GitHub workflow content exceeds validation limit")
        return content

    def put_file(self, org: str, repo: str, path: str, content_b64: str,
                 branch: str, message: str, sha: str = "") -> dict:
        body: dict = {"message": message, "content": content_b64, "branch": branch}
        if sha:
            body["sha"] = sha
        r = self._put(f"/repos/{org}/{repo}/contents/{path}", body)
        r.raise_for_status()
        return r.json()

    def create_pull_request(self, org: str, repo: str, title: str, body: str,
                             head: str, base: str) -> dict:
        return self._post(
            f"/repos/{org}/{repo}/pulls",
            {"title": title, "body": body, "head": head, "base": base},
        )

    @property
    def token_manager(self) -> TokenManager:
        return self._tm
