"""Azure DevOps REST API client."""
from __future__ import annotations

import base64
import threading
from typing import Any, Iterator, Optional
from urllib.parse import quote

import requests

from ado2gh.clients.ado_token_manager import ADOTokenManager
from ado2gh.http_utils import make_session


class ADOClient:
    """Azure DevOps REST API client.

    Authenticates with a personal access token, either fixed or drawn from an
    ``ADOTokenManager``, and keeps one ``requests.Session`` per thread. Listing
    methods return the ``value`` array of the ADO response. Many of them swallow
    request errors and return an empty result so that one failing project does not
    abort a discovery run; each method's docstring says whether it raises or not.
    """

    API = "api-version=7.1"
    API_RELEASE = "api-version=7.1"
    PAGE_SIZE = 100

    def __init__(
        self,
        org_url: str,
        pat: str = "",
        token_manager: Optional[ADOTokenManager] = None,
    ) -> None:
        """Create a client for one Azure DevOps organisation.

        Args:
            org_url: Organisation URL such as ``https://dev.azure.com/<org>``; a
                trailing slash is removed.
            pat: A single personal access token. Ignored when ``token_manager`` is given.
            token_manager: Rotating token pool; when set, each new session takes its
                token from the pool.
        """
        self.org_url = org_url.rstrip("/")
        self._tm = token_manager
        self._pat = pat if not token_manager else ""
        self._local = threading.local()

    @property
    def pat(self) -> str:
        """The token for the next session: from the pool when configured, else the fixed one."""
        if self._tm:
            return self._tm.get_token()
        return self._pat

    @property
    def session(self) -> requests.Session:
        """The calling thread's authenticated session, created on first use.

        The session carries HTTP basic auth built from ``pat`` plus JSON headers. It
        is cached per thread, so a rotated token is only picked up by threads that
        have not yet created their session.
        """
        if not hasattr(self._local, "session"):
            token = base64.b64encode(f":{self.pat}".encode()).decode()
            sess = make_session()
            sess.headers.update({
                "Authorization": f"Basic {token}",
                "Content-Type":  "application/json",
                "Accept":        "application/json",
            })
            self._local.session = sess
        return self._local.session

    def _get(self, url: str, params: dict[str, Any] | None = None,
             timeout: int = 45) -> dict[str, Any]:
        """GET ``url`` and return the decoded JSON object; raises ``HTTPError`` on non-2xx."""
        r = self.session.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, url: str, body: dict[str, Any], timeout: int = 45) -> dict[str, Any]:
        """POST ``body`` as JSON to ``url`` and return the decoded JSON object; raises on non-2xx."""
        r = self.session.post(url, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _encode_project(self, project: str) -> str:
        """URL-encode a project name for use as a single path segment."""
        return quote(project, safe="")

    # ── Projects & Repos ────────────────────────────────────────────────────

    def list_projects(self) -> list[dict]:
        """List the organisation's projects (first 500).

        Returns:
            Project records from the ``projects`` endpoint.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        url = f"{self.org_url}/_apis/projects?{self.API}&$top=500"
        return self._get(url).get("value", [])

    def list_repos(self, project: str) -> list[dict]:
        """List Git repositories in a project (handles pagination)."""
        encoded = self._encode_project(project)
        url = (
            f"{self.org_url}/{encoded}/_apis/git/repositories"
            f"?{self.API}&$top={self.PAGE_SIZE}"
        )
        repos: list[dict] = []
        while url:
            r = self.session.get(url, timeout=45)
            r.raise_for_status()
            data = r.json()
            repos.extend(data.get("value", []))
            cont = r.headers.get("x-ms-continuationtoken")
            if cont:
                url = (
                    f"{self.org_url}/{encoded}/_apis/git/repositories"
                    f"?{self.API}&$top={self.PAGE_SIZE}&continuationToken={cont}"
                )
            else:
                break
        return repos

    def get_repo(self, project: str, repo: str) -> dict:
        """Fetch one Git repository.

        Args:
            project: Project name.
            repo: Repository name or id.

        Returns:
            The repository record.

        Raises:
            requests.HTTPError: On a non-2xx response, including 404 when it does not exist.
        """
        url = (f"{self.org_url}/{self._encode_project(project)}/_apis/git/repositories"
               f"/{quote(repo, safe='')}?{self.API}")
        return self._get(url)

    def get_repo_stats(self, project: str, repo_id: str) -> dict:
        """Count the branches of a repository.

        Args:
            project: Project name.
            repo_id: Repository id.

        Returns:
            A mapping with ``branch_count``; zero when the request fails.
        """
        url = (f"{self.org_url}/{self._encode_project(project)}/_apis/git/repositories"
               f"/{repo_id}/stats/branches?{self.API}")
        try:
            data = self._get(url)
            return {"branch_count": data.get("count", 0)}
        except Exception:
            return {"branch_count": 0}

    def get_repo_commits(self, project: str, repo_id: str,
                          top: int = 1, branch: str = "") -> list[dict]:
        """Fetch the newest commits of a repository.

        Args:
            project: Project name.
            repo_id: Repository id.
            top: Maximum number of commits to return.
            branch: Branch to read; the default branch when empty.

        Returns:
            Commit records, newest first; empty when the request fails.
        """
        url = (f"{self.org_url}/{self._encode_project(project)}/_apis/git/repositories"
               f"/{repo_id}/commits?{self.API}&$top={top}")
        if branch:
            url += (f"&searchCriteria.itemVersion.version={quote(branch, safe='')}"
                    f"&searchCriteria.itemVersion.versionType=branch")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    # ── Build Pipelines ─────────────────────────────────────────────────────

    def list_all_pipelines(self, project: str) -> Iterator[dict]:
        """Iterate over every build pipeline in a project, following pagination.

        Args:
            project: Project name.

        Yields:
            Pipeline records in name order.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        url = (f"{self.org_url}/{self._encode_project(project)}/_apis/pipelines"
               f"?{self.API}&$top={self.PAGE_SIZE}&orderBy=name asc")
        while url:
            r = self.session.get(url, timeout=45)
            r.raise_for_status()
            data = r.json()
            for pipe in data.get("value", []):
                yield pipe
            cont = r.headers.get("x-ms-continuationtoken")
            if cont:
                url = (f"{self.org_url}/{self._encode_project(project)}/_apis/pipelines"
                       f"?{self.API}&$top={self.PAGE_SIZE}"
                       f"&continuationToken={cont}&orderBy=name asc")
            else:
                break

    def get_pipeline_definition(self, project: str, pipeline_id: int) -> dict:
        """Fetch one pipeline's definition, including its YAML path or designer config.

        Args:
            project: Project name.
            pipeline_id: Pipeline id.

        Returns:
            The pipeline record.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        url = (f"{self.org_url}/{self._encode_project(project)}/_apis/pipelines"
               f"/{pipeline_id}?{self.API}")
        return self._get(url)

    def get_pipeline_yaml_from_git(self, project: str, repo_id: str,
                                    yaml_path: str, branch: str = "main") -> str:
        """Read a pipeline YAML file from a repository branch.

        Args:
            project: Project name.
            repo_id: Repository id.
            yaml_path: Path of the file inside the repository.
            branch: Branch to read from.

        Returns:
            The file text; empty when ``yaml_path`` is empty or the request fails.
        """
        if not yaml_path:
            return ""
        path_enc = quote(yaml_path.lstrip("/"), safe="")
        url = (f"{self.org_url}/{self._encode_project(project)}/_apis/git/repositories"
               f"/{repo_id}/items?path={path_enc}"
               f"&versionDescriptor.version={quote(branch, safe='')}"
               f"&versionDescriptor.versionType=branch"
               f"&$format=text&{self.API}")
        try:
            r = self.session.get(url, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception:
            return ""

    def list_repo_yaml_files(self, project: str, repo_id: str,
                              branch: str = "main",
                              max_results: int = 50) -> list[str]:
        """Find YAML files anywhere in a repository branch.

        Args:
            project: Project name.
            repo_id: Repository id.
            branch: Branch to scan.
            max_results: Stop after this many matches.

        Returns:
            Paths of ``.yml`` and ``.yaml`` blobs; empty when ``repo_id`` is empty or
            the request fails.
        """
        if not repo_id:
            return []
        url = (f"{self.org_url}/{self._encode_project(project)}/_apis/git/repositories"
               f"/{repo_id}/items?recursionLevel=Full"
               f"&versionDescriptor.version={quote(branch, safe='')}"
               f"&versionDescriptor.versionType=branch"
               f"&{self.API}")
        try:
            r = self.session.get(url, timeout=30)
            r.raise_for_status()
            items = r.json().get("value", [])
        except Exception:
            return []
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
        """Fetch a pipeline's most recent runs.

        Args:
            project: Project name.
            pipeline_id: Pipeline id.
            top: Maximum number of runs to return.

        Returns:
            Run records, newest first; empty when the request fails.
        """
        url = (f"{self.org_url}/{self._encode_project(project)}/_apis/pipelines"
               f"/{pipeline_id}/runs?{self.API}&$top={top}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    def get_build_definition_full(self, project: str, pipeline_id: int) -> dict:
        """Fetch the full classic build definition, including its latest builds.

        Args:
            project: Project name.
            pipeline_id: Build definition id.

        Returns:
            The definition record; empty when the request fails.
        """
        url = (f"{self.org_url}/{self._encode_project(project)}/_apis/build/definitions"
               f"/{pipeline_id}?{self.API}&includeLatestBuilds=true")
        try:
            return self._get(url)
        except Exception:
            return {}

    # ── Release Pipelines ───────────────────────────────────────────────────

    def list_all_release_pipelines(self, project: str) -> Iterator[dict]:
        """Iterate over every classic release definition in a project.

        Release definitions live on the ``vsrm`` host; artifacts and environments
        are expanded inline. Iteration stops silently at the first failed page.

        Args:
            project: Project name.

        Yields:
            Release definition records in name order.
        """
        vsrm_base = self.org_url.replace(
            "dev.azure.com", "vsrm.dev.azure.com"
        ).replace(
            ".visualstudio.com", ".vsrm.visualstudio.com"
        )
        top, skip = self.PAGE_SIZE, 0
        while True:
            url = (f"{vsrm_base}/{self._encode_project(project)}/_apis/release/definitions"
                   f"?{self.API_RELEASE}&$top={top}&$skip={skip}"
                   f"&$expand=artifacts,environments&queryOrder=nameAscending")
            try:
                data = self._get(url)
                items = data.get("value", [])
                for item in items:
                    yield item
                if len(items) < top:
                    break
                skip += top
            except Exception:
                break

    def get_release_definition(self, project: str, def_id: int) -> dict:
        """Fetch one classic release definition from the ``vsrm`` host.

        Args:
            project: Project name.
            def_id: Release definition id.

        Returns:
            The definition record; empty when the request fails.
        """
        vsrm_base = self.org_url.replace(
            "dev.azure.com", "vsrm.dev.azure.com"
        ).replace(
            ".visualstudio.com", ".vsrm.visualstudio.com"
        )
        url = (f"{vsrm_base}/{self._encode_project(project)}/_apis/release/definitions"
               f"/{def_id}?{self.API_RELEASE}")
        try:
            return self._get(url)
        except Exception:
            return {}

    # ── Environments & Approvals ────────────────────────────────────────────

    def list_environments(self, project: str) -> list[dict]:
        """List the project's deployment environments.

        Args:
            project: Project name.

        Returns:
            Environment records; empty when the request fails.
        """
        url = (f"{self.org_url}/{self._encode_project(project)}/_apis/distributedtask/environments"
               f"?{self.API}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    # ── Variable Groups & Service Connections ───────────────────────────────

    def list_variable_groups(self, project: str) -> list[dict]:
        """List the project's variable groups.

        Args:
            project: Project name.

        Returns:
            Variable group records; empty when the request fails.
        """
        url = (f"{self.org_url}/{self._encode_project(project)}"
               f"/_apis/distributedtask/variablegroups?{self.API}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    def list_service_connections(self, project: str) -> list[dict]:
        """List the project's service connections.

        Only names and metadata are readable; ADO never returns the credentials
        behind a connection.

        Args:
            project: Project name.

        Returns:
            Service endpoint records; empty when the request fails.
        """
        url = (f"{self.org_url}/{self._encode_project(project)}"
               f"/_apis/serviceendpoint/endpoints?{self.API}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    # ── Agent Pools ─────────────────────────────────────────────────────────

    # ── Work Items ──────────────────────────────────────────────────────────

    def list_work_items(self, project: str, top: int = 500) -> list[dict]:
        """Fetch the project's work items with the fields the Boards migration needs.

        Runs a WIQL query for the ids, then fetches the items in one batch.

        Args:
            project: Project name.
            top: Maximum number of work items to return.

        Returns:
            Work item records; empty when the project has none.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        url = f"{self.org_url}/{self._encode_project(project)}/_apis/wit/wiql?{self.API}"
        wiql = {"query": (
            f"SELECT [System.Id] FROM WorkItems "
            f"WHERE [System.TeamProject]='{project}' ORDER BY [System.Id]"
        )}
        result = self._post(url, wiql)
        ids = [str(wi["id"]) for wi in result.get("workItems", [])[:top]]
        if not ids:
            return []
        fields = (
            "System.Id,System.Title,System.WorkItemType,System.State,"
            "System.Description,System.AssignedTo,System.Tags,"
            "Microsoft.VSTS.Common.Priority"
        )
        return self._get(
            f"{self.org_url}/_apis/wit/workItems"
            f"?ids={','.join(ids)}&fields={fields}&{self.API}"
        ).get("value", [])

    # ── Wiki ────────────────────────────────────────────────────────────────

    def list_wiki_pages(self, project: str) -> list[dict]:
        """Fetch every wiki in a project together with its full page tree.

        Args:
            project: Project name.

        Returns:
            One ``{"wiki": record, "root": page tree}`` mapping per wiki; wikis
            whose page tree cannot be read are skipped.

        Raises:
            requests.HTTPError: If the wiki list itself cannot be read.
        """
        url = f"{self.org_url}/{self._encode_project(project)}/_apis/wiki/wikis?{self.API}"
        wikis = self._get(url).get("value", [])
        out = []
        for wiki in wikis:
            try:
                root = self._get(
                    f"{self.org_url}/{self._encode_project(project)}/_apis/wiki/wikis"
                    f"/{wiki['id']}/pages?recursionLevel=full&{self.API}"
                )
                out.append({"wiki": wiki, "root": root})
            except Exception:
                pass
        return out

    # ── Artifacts ───────────────────────────────────────────────────────────

    def list_artifacts(self, project: str) -> list[dict]:
        """List artifact feeds in a project."""
        url = (f"{self.org_url}/{self._encode_project(project)}"
               f"/_apis/packaging/feeds?{self.API}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    # ── Azure Boards ─────────────────────────────────────────────────────────

    def list_teams(self, project: str) -> list[dict]:
        """List teams in a project."""
        url = (f"{self.org_url}/_apis/projects/{self._encode_project(project)}/teams?{self.API}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    def list_iterations(self, project: str) -> list[dict]:
        """List iteration paths (sprints) for a project."""
        url = (f"{self.org_url}/{self._encode_project(project)}"
               f"/_apis/work/teamsettings/iterations?{self.API}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    def list_work_item_types(self, project: str) -> list[dict]:
        """List work item types defined in a project."""
        url = (f"{self.org_url}/{self._encode_project(project)}"
               f"/_apis/wit/workitemtypes?{self.API}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    # ── Test Plans ───────────────────────────────────────────────────────────

    def list_test_plans(self, project: str) -> list[dict]:
        """List test plans in a project."""
        url = (f"{self.org_url}/{self._encode_project(project)}"
               f"/_apis/test/plans?{self.API}&includePlanDetails=true")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    def list_test_suites(self, project: str, plan_id: int) -> list[dict]:
        """List test suites in a test plan."""
        url = (f"{self.org_url}/{self._encode_project(project)}"
               f"/_apis/test/Plans/{plan_id}/suites?{self.API}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    def list_branch_policies(self, project: str, repo_id: str) -> list[dict]:
        """List the branch policies whose scope targets one repository.

        Args:
            project: Project name.
            repo_id: Repository id.

        Returns:
            Policy configuration records scoped to that repository.

        Raises:
            requests.HTTPError: On a non-2xx response.
        """
        url = (f"{self.org_url}/{self._encode_project(project)}"
               f"/_apis/policy/configurations?{self.API}")
        all_pol = self._get(url).get("value", [])
        return [
            p for p in all_pol
            if p.get("settings", {}).get("scope", [{}])[0]
               .get("repositoryId") == repo_id
        ]
