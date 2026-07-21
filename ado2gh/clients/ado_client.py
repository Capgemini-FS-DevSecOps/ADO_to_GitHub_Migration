"""Azure DevOps REST API client."""
from __future__ import annotations

import base64
import threading
from typing import Any, Iterator
from urllib.parse import quote, urlsplit

import requests

from ado2gh.http_utils import make_session


class ADOClient:
    API = "api-version=7.1"
    API_RELEASE = "api-version=7.1"
    PAGE_SIZE = 100
    GIT_SECURITY_NAMESPACE = "2e9eb7ed-3c0a-47d4-87c1-0ffdd275fd87"

    def __init__(self, org_url: str, pat: str):
        parsed = urlsplit(str(org_url).rstrip("/"))
        if (
            parsed.scheme != "https" or not parsed.netloc
            or parsed.username or parsed.password
            or parsed.query or parsed.fragment
        ):
            raise ValueError(
                "ADO organization URL must be absolute HTTPS without "
                "credentials, query, or fragment"
            )
        self.org_url = str(org_url).rstrip("/")
        self.pat     = pat
        token = base64.b64encode(f":{pat}".encode()).decode()
        self._session_local = threading.local()
        self._session_override = None
        self._session_headers = {
            "Authorization": f"Basic {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

    @property
    def session(self):
        if self._session_override is not None:
            return self._session_override
        session = getattr(self._session_local, "value", None)
        if session is None:
            session = make_session()
            session.headers.update(self._session_headers)
            self._session_local.value = session
        return session

    @session.setter
    def session(self, value):
        """Allow test doubles while production uses one Session per thread."""
        self._session_override = value

    def _get(self, url: str, params: dict = None, timeout: int = 45) -> Any:
        r = self.session.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, url: str, body: dict, timeout: int = 45) -> Any:
        r = self.session.post(url, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _p(self, project: str) -> str:
        return quote(project, safe="")

    def get_organization_identity(self) -> str:
        """Return Azure DevOps' immutable collection/organization instance ID."""
        data = self._get(
            f"{self.org_url}/_apis/connectionData",
            params={
                "connectOptions": "1",
                "lastChangeId": "-1",
                "lastChangeId64": "-1",
            },
        )
        if not isinstance(data, dict):
            raise RuntimeError("ADO connectionData response must be an object")
        instance_id = str(data.get("instanceId") or "").strip()
        if not instance_id or any(
            ord(char) < 32 or ord(char) == 127 for char in instance_id
        ):
            raise RuntimeError(
                "ADO connectionData did not return an immutable instanceId"
            )
        return instance_id

    def _paged_values(self, url: str, params: dict = None,
                      continuation_param: str = "continuationToken") -> list[dict]:
        """Read an ADO collection without silently truncating an enterprise org."""
        values: list[dict] = []
        query = dict(params or {})
        seen_tokens: set[str] = set()
        while True:
            r = self.session.get(url, params=query, timeout=45)
            r.raise_for_status()
            payload = r.json()
            page = payload.get("value") if isinstance(payload, dict) else None
            if not isinstance(page, list):
                raise TypeError("ADO paginated response must contain a value list")
            values.extend(page)
            token = r.headers.get("x-ms-continuationtoken")
            if not token:
                return values
            token = str(token)
            if token in seen_tokens:
                raise RuntimeError("ADO pagination returned a repeated continuation token")
            seen_tokens.add(token)
            query[continuation_param] = token

    # ── Projects & Repos ────────────────────────────────────────────────────

    def list_projects(self) -> list[dict]:
        return self._paged_values(
            f"{self.org_url}/_apis/projects",
            {"api-version": "7.1", "$top": self.PAGE_SIZE},
        )

    def list_repos(self, project: str) -> list[dict]:
        return self._paged_values(
            f"{self.org_url}/{self._p(project)}/_apis/git/repositories",
            {"api-version": "7.1", "$top": self.PAGE_SIZE},
        )

    def get_repo(self, project: str, repo: str) -> dict:
        url = (f"{self.org_url}/{self._p(project)}/_apis/git/repositories"
               f"/{quote(repo, safe='')}?{self.API}")
        return self._get(url)

    def list_project_git_acls(self, project_id: str) -> list[dict]:
        """Return the project ACL plus all repository/branch child ACLs."""
        project_id = str(project_id).strip()
        if not project_id:
            raise ValueError("project_id is required for Git ACL inventory")
        data = self._get(
            f"{self.org_url}/_apis/accesscontrollists/"
            f"{self.GIT_SECURITY_NAMESPACE}",
            params={
                "api-version": "7.1",
                "token": f"repoV2/{project_id}",
                "includeExtendedInfo": "true",
                "recurse": "true",
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("value"), list):
            raise TypeError("ADO Git ACL response must contain a value list")
        if data.get("count") != len(data["value"]):
            raise RuntimeError("ADO Git ACL inventory count is inconsistent")
        return data["value"]

    def list_git_root_acls(self) -> list[dict]:
        """Return organization-level Git ACLs inherited by every project."""
        data = self._get(
            f"{self.org_url}/_apis/accesscontrollists/"
            f"{self.GIT_SECURITY_NAMESPACE}",
            params={
                "api-version": "7.1",
                "token": "repoV2",
                "includeExtendedInfo": "true",
                "recurse": "false",
            },
        )
        if not isinstance(data, dict) or not isinstance(data.get("value"), list):
            raise TypeError("ADO Git root ACL response must contain a value list")
        if data.get("count") != len(data["value"]):
            raise RuntimeError("ADO Git root ACL inventory count is inconsistent")
        return data["value"]

    def resolve_acl_identities(
        self,
        descriptors: list[str],
    ) -> dict[str, dict]:
        """Resolve ACL principals and their expanded group membership.

        Git security ACLs expose legacy identity descriptors.  The Identities
        API is the supported bridge from those descriptors to ADO's identity
        graph and, with ``ExpandedDown``, returns the effective descendants of
        container identities.  Requests are batched by project inventory so
        repeated principals on thousands of repositories are resolved once.
        """
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in descriptors:
            descriptor = str(raw).strip()
            if not descriptor:
                raise ValueError("ACL identity descriptors must be non-empty")
            folded = descriptor.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            normalized.append(descriptor)
        if not normalized:
            return {}

        parsed = urlsplit(self.org_url)
        host = (parsed.hostname or "").casefold()
        if host == "dev.azure.com":
            identity_root = f"https://vssps.dev.azure.com{parsed.path}"
        elif host.endswith(".visualstudio.com") \
                and not host.endswith(".vssps.visualstudio.com"):
            account = host[: -len(".visualstudio.com")]
            identity_root = (
                f"https://{account}.vssps.visualstudio.com{parsed.path}"
            )
        else:
            # Azure DevOps Server exposes IMS beneath the collection URL.
            identity_root = self.org_url

        resolved: dict[str, dict] = {}
        # Bound both the item count and query size. Legacy descriptors can be
        # long, and an unbounded comma-separated query is rejected by common
        # enterprise proxies before it reaches Azure DevOps.
        batches: list[list[str]] = []
        batch: list[str] = []
        query_chars = 0
        for descriptor in normalized:
            added = len(descriptor) + (1 if batch else 0)
            if batch and (len(batch) >= 25 or query_chars + added > 3_500):
                batches.append(batch)
                batch = []
                query_chars = 0
                added = len(descriptor)
            batch.append(descriptor)
            query_chars += added
        if batch:
            batches.append(batch)

        for requested in batches:
            data = self._get(
                f"{identity_root.rstrip('/')}/_apis/identities",
                params={
                    "api-version": "7.1",
                    "descriptors": ",".join(requested),
                    "queryMembership": "ExpandedDown",
                },
                timeout=120,
            )
            values = data.get("value") if isinstance(data, dict) else None
            if not isinstance(values, list) or data.get("count") != len(values):
                raise TypeError(
                    "ADO identity inventory must contain a consistent value list"
                )
            requested_by_fold = {item.casefold(): item for item in requested}
            observed: set[str] = set()
            for row in values:
                if not isinstance(row, dict):
                    raise TypeError("ADO identity inventory item must be an object")
                descriptor = str(row.get("descriptor", "")).strip()
                folded = descriptor.casefold()
                canonical = requested_by_fold.get(folded)
                if canonical is None:
                    raise RuntimeError(
                        "ADO identity inventory returned an unrequested principal"
                    )
                if folded in observed:
                    raise RuntimeError(
                        "ADO identity inventory returned a duplicate principal"
                    )
                observed.add(folded)
                resolved[canonical] = row
            missing = set(requested_by_fold) - observed
            if missing:
                raise RuntimeError(
                    "ADO identity inventory did not resolve every ACL principal"
                )
        return resolved

    def list_refs(self, project: str, repo_id: str,
                  prefix: str) -> list[dict]:
        """List every branch or tag ref for an immutable repository ID.

        Azure DevOps expects ``filter`` without the leading ``refs/`` and
        returns continuation tokens in response headers.  Only the two
        namespaces migrated by ado2gh are accepted so callers cannot approve
        an accidentally partial or overly broad ref set.
        """
        repo_id = str(repo_id).strip()
        normalized = str(prefix).removeprefix("refs/")
        if not repo_id:
            raise ValueError("repo_id is required for ref listing")
        if normalized not in {"heads/", "tags/"}:
            raise ValueError("ref prefix must be 'heads/' or 'tags/'")
        return self._paged_values(
            f"{self.org_url}/{self._p(project)}/_apis/git/repositories/"
            f"{quote(repo_id, safe='')}/refs",
            {
                "api-version": "7.1",
                "filter": normalized,
                "$top": 1000,
            },
        )

    def get_repo_stats(self, project: str, repo_id: str) -> dict:
        url = (f"{self.org_url}/{self._p(project)}/_apis/git/repositories"
               f"/{repo_id}/stats/branches?{self.API}")
        data = self._get(url)
        return {"branch_count": data.get("count", 0)}

    def get_repo_commits(self, project: str, repo_id: str,
                          top: int = 1, branch: str = "") -> list[dict]:
        url = (f"{self.org_url}/{self._p(project)}/_apis/git/repositories"
               f"/{repo_id}/commits?{self.API}&$top={top}")
        if branch:
            # Scope the query to a specific branch — without this, ADO
            # returns "latest commit across all branches" which can pick
            # up dangling PR-merge commits that aren't on any branch tip.
            url += (f"&searchCriteria.itemVersion.version={quote(branch, safe='')}"
                    f"&searchCriteria.itemVersion.versionType=branch")
        return self._get(url).get("value", [])

    # ── Build Pipelines ─────────────────────────────────────────────────────

    def list_all_pipelines(self, project: str) -> Iterator[dict]:
        url = (f"{self.org_url}/{self._p(project)}/_apis/pipelines"
               f"?{self.API}&$top={self.PAGE_SIZE}&orderBy=name asc")
        while url:
            r = self.session.get(url, timeout=45)
            r.raise_for_status()
            data = r.json()
            for pipe in data.get("value", []):
                yield pipe
            cont = r.headers.get("x-ms-continuationtoken")
            if cont:
                url = (f"{self.org_url}/{self._p(project)}/_apis/pipelines"
                       f"?{self.API}&$top={self.PAGE_SIZE}"
                       f"&continuationToken={cont}&orderBy=name asc")
            else:
                break

    def get_pipeline_definition(self, project: str, pipeline_id: int) -> dict:
        url = (f"{self.org_url}/{self._p(project)}/_apis/pipelines"
               f"/{pipeline_id}?{self.API}")
        return self._get(url)

    def get_pipeline_yaml_from_git(self, project: str, repo_id: str,
                                    yaml_path: str, branch: str = "main") -> str:
        if not yaml_path:
            return ""
        path_enc = quote(yaml_path.lstrip("/"), safe="")
        url = (f"{self.org_url}/{self._p(project)}/_apis/git/repositories"
               f"/{repo_id}/items?path={path_enc}"
               f"&versionDescriptor.version={quote(branch, safe='')}"
               f"&versionDescriptor.versionType=branch"
               f"&$format=text&{self.API}")
        try:
            r = self.session.get(url, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return ""
            raise

    def list_repo_yaml_files(self, project: str, repo_id: str,
                              branch: str = "main",
                              max_results: int = 50) -> list[str]:
        """List *.yml/*.yaml file paths in a repo on the given branch.

        Used to suggest candidates when an ADO pipeline references a YAML
        file that is empty or missing in the source.
        """
        if not repo_id:
            return []
        url = (f"{self.org_url}/{self._p(project)}/_apis/git/repositories"
               f"/{repo_id}/items?recursionLevel=Full"
               f"&versionDescriptor.version={quote(branch, safe='')}"
               f"&versionDescriptor.versionType=branch"
               f"&{self.API}")
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        items = r.json().get("value", [])
        yaml_paths: list[str] = []
        for it in items:
            if it.get("gitObjectType") != "blob":
                continue
            p = it.get("path", "")
            if p.lower().endswith((".yml", ".yaml")):
                yaml_paths.append(p)
                if len(yaml_paths) >= max_results:
                    break
        return yaml_paths

    def get_pipeline_runs(self, project: str, pipeline_id: int,
                           top: int = 10) -> list[dict]:
        url = (f"{self.org_url}/{self._p(project)}/_apis/pipelines"
               f"/{pipeline_id}/runs?{self.API}&$top={top}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    def get_build_definition_full(self, project: str, pipeline_id: int) -> dict:
        url = (f"{self.org_url}/{self._p(project)}/_apis/build/definitions"
               f"/{pipeline_id}?{self.API}&includeLatestBuilds=true")
        return self._get(url)

    # ── Release Pipelines ───────────────────────────────────────────────────

    def list_all_release_pipelines(self, project: str) -> Iterator[dict]:
        vsrm_base = self.org_url.replace(
            "dev.azure.com", "vsrm.dev.azure.com"
        ).replace(
            ".visualstudio.com", ".vsrm.visualstudio.com"
        )
        top, skip = self.PAGE_SIZE, 0
        while True:
            url = (f"{vsrm_base}/{self._p(project)}/_apis/release/definitions"
                   f"?{self.API_RELEASE}&$top={top}&$skip={skip}"
                   f"&$expand=artifacts,environments&queryOrder=nameAscending")
            data = self._get(url)
            items = data.get("value", [])
            for item in items:
                yield item
            if len(items) < top:
                break
            skip += top

    def get_release_definition(self, project: str, def_id: int) -> dict:
        vsrm_base = self.org_url.replace(
            "dev.azure.com", "vsrm.dev.azure.com"
        ).replace(
            ".visualstudio.com", ".vsrm.visualstudio.com"
        )
        url = (f"{vsrm_base}/{self._p(project)}/_apis/release/definitions"
               f"/{def_id}?{self.API_RELEASE}")
        return self._get(url)

    # ── Environments & Approvals ────────────────────────────────────────────

    def list_environments(self, project: str) -> list[dict]:
        url = (f"{self.org_url}/{self._p(project)}/_apis/distributedtask/environments"
               f"?{self.API}")
        return self._get(url).get("value", [])

    def get_pipeline_approvals(self, project: str, pipeline_id: int) -> list[dict]:
        url = (f"{self.org_url}/{self._p(project)}/_apis/pipelines/checks/configurations"
               f"?{self.API}&resourceType=pipeline&resourceId={pipeline_id}")
        return self._get(url).get("value", [])

    # ── Variable Groups & Service Connections ───────────────────────────────

    def list_variable_groups(self, project: str) -> list[dict]:
        return self._paged_values(
            f"{self.org_url}/{self._p(project)}"
            "/_apis/distributedtask/variablegroups",
            {"api-version": "7.1", "$top": self.PAGE_SIZE},
        )

    def list_service_connections(self, project: str) -> list[dict]:
        return self._paged_values(
            f"{self.org_url}/{self._p(project)}"
            "/_apis/serviceendpoint/endpoints",
            {"api-version": "7.1", "$top": self.PAGE_SIZE},
        )

    # ── Agent Pools ─────────────────────────────────────────────────────────

    def list_agent_pools(self) -> list[dict]:
        url = f"{self.org_url}/_apis/distributedtask/pools?{self.API}"
        return self._get(url).get("value", [])

    # ── Work Items ──────────────────────────────────────────────────────────

    def list_work_items(self, project: str, top: int = 20_000,
                        repo_id: str = "", include_unlinked: bool = False) -> list[dict]:
        """List work items, optionally restricted to links for one repository.

        ADO work items belong to projects, not repositories.  The old caller
        copied every project item into every migrated repo.  Repository-scoped
        migrations now include only work items with an artifact relation that
        names the source repository; unlinked project backlog items require an
        explicit project-level mapping and are not guessed.
        """
        page_size = min(max(1, int(top)), 20_000)
        url = (f"{self.org_url}/{self._p(project)}/_apis/wit/wiql"
               f"?{self.API}&$top={page_size}")
        escaped_project = project.replace("'", "''")
        ids: list[str] = []
        seen_ids: set[str] = set()
        last_id = 0
        # WIQL has a 20,000-result server cap and no offset parameter.  ID
        # keyset pagination removes the old silent truncation while remaining
        # stable for monotonically allocated work-item IDs.
        while True:
            wiql = {"query": (
                "SELECT [System.Id] FROM WorkItems "
                f"WHERE [System.TeamProject]='{escaped_project}' "
                f"AND [System.Id] > {last_id} ORDER BY [System.Id]"
            )}
            result = self._post(url, wiql)
            rows = result.get("workItems") if isinstance(result, dict) else None
            if not isinstance(rows, list):
                raise TypeError("ADO WIQL response must contain a workItems list")
            page_ids: list[int] = []
            for row in rows:
                if not isinstance(row, dict):
                    raise TypeError("ADO WIQL workItems entry must be an object")
                try:
                    item_id = int(row["id"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("ADO WIQL returned an invalid work-item ID") from exc
                if item_id <= last_id or str(item_id) in seen_ids:
                    raise RuntimeError(
                        "ADO WIQL keyset pagination returned a repeated or "
                        "out-of-order work-item ID"
                    )
                page_ids.append(item_id)
                seen_ids.add(str(item_id))
            if page_ids != sorted(page_ids):
                raise RuntimeError("ADO WIQL ignored the requested ID ordering")
            ids.extend(str(item_id) for item_id in page_ids)
            if len(page_ids) < page_size:
                break
            if not page_ids:
                raise RuntimeError("ADO WIQL pagination made no progress")
            last_id = page_ids[-1]
        if not ids:
            return []
        fields = (
            "System.Id,System.Title,System.WorkItemType,System.State,"
            "System.Description,System.AssignedTo,System.Tags,"
            "Microsoft.VSTS.Common.Priority"
        )
        items: list[dict] = []
        # The batch endpoint has practical request-size limits.  Chunking also
        # keeps response memory bounded for large enterprise projects.
        for offset in range(0, len(ids), 200):
            chunk = ids[offset:offset + 200]
            data = self._get(
                f"{self.org_url}/_apis/wit/workItems",
                params={
                    "ids": ",".join(chunk),
                    "fields": fields,
                    "$expand": "Relations",
                    "api-version": "7.1",
                },
            )
            batch = data.get("value") if isinstance(data, dict) else None
            if not isinstance(batch, list):
                raise TypeError(
                    "ADO work-item batch response must contain a value list"
                )
            returned: list[str] = []
            for item in batch:
                if not isinstance(item, dict) or "id" not in item:
                    raise ValueError(
                        "ADO work-item batch returned a malformed item"
                    )
                returned.append(str(item["id"]))
            if sorted(returned, key=int) != sorted(chunk, key=int) \
                    or len(set(returned)) != len(returned):
                raise RuntimeError(
                    "ADO work-item batch did not return the complete requested ID set"
                )
            items.extend(batch)
        if not repo_id or include_unlinked:
            return items
        needle = repo_id.casefold()
        return [
            item for item in items
            if any(
                needle in str(relation.get("url", "")).casefold()
                for relation in item.get("relations", [])
                if relation.get("rel") == "ArtifactLink"
            )
        ]

    # ── Wiki ────────────────────────────────────────────────────────────────

    def list_wiki_pages(self, project: str) -> list[dict]:
        wikis = self._paged_values(
            f"{self.org_url}/{self._p(project)}/_apis/wiki/wikis",
            {"api-version": "7.1", "$top": self.PAGE_SIZE},
        )
        out = []
        for wiki in wikis:
            wiki_id = quote(str(wiki["id"]), safe="")
            page_base = (
                f"{self.org_url}/{self._p(project)}/_apis/wiki/wikis"
                f"/{wiki_id}/pages"
            )
            root = self._get(
                f"{page_base}?path=%2F&recursionLevel=full"
                f"&includeContent=true&{self.API}"
            )
            out.append({
                "wiki": wiki,
                "root": self._hydrate_wiki_content(page_base, root),
            })
        return out

    def _hydrate_wiki_content(self, page_base: str, page: Any) -> dict:
        """Ensure every enumerated page contains the content being approved."""
        if not isinstance(page, dict):
            raise TypeError("ADO wiki page response must be an object")
        hydrated = dict(page)
        path = str(hydrated.get("path", "/"))
        if "content" not in hydrated:
            exact = self._get(
                f"{page_base}?path={quote(path, safe='')}"
                f"&recursionLevel=none"
                f"&includeContent=true&{self.API}"
            )
            if not isinstance(exact, dict) or "content" not in exact:
                raise RuntimeError(f"ADO wiki page {path!r} returned no content")
            hydrated["content"] = exact["content"]
        children = hydrated.get("subPages", [])
        if not isinstance(children, list):
            raise TypeError(f"ADO wiki page {path!r} subPages must be a list")
        hydrated["subPages"] = [
            self._hydrate_wiki_content(page_base, child) for child in children
        ]
        return hydrated

    def list_branch_policies(self, project: str, repo_id: str) -> list[dict]:
        all_pol = self._paged_values(
            f"{self.org_url}/{self._p(project)}/_apis/policy/configurations",
            {"api-version": "7.1", "$top": self.PAGE_SIZE},
        )
        expected_repo = str(repo_id).casefold()
        return [
            p for p in all_pol
            if any(
                str(scope.get("repositoryId", "")).casefold() == expected_repo
                for scope in p.get("settings", {}).get("scope", [])
            )
        ]
