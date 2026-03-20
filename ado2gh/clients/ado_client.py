"""Azure DevOps REST API client."""
from __future__ import annotations

import base64
from typing import Any, Iterator
from urllib.parse import quote

from ado2gh.http_utils import make_session


class ADOClient:
    API = "api-version=7.1"
    API_RELEASE = "api-version=7.1"
    PAGE_SIZE = 100

    def __init__(self, org_url: str, pat: str):
        self.org_url = org_url.rstrip("/")
        self.pat     = pat
        token = base64.b64encode(f":{pat}".encode()).decode()
        self.session = make_session()
        self.session.headers.update({
            "Authorization": f"Basic {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        })

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

    # ── Projects & Repos ────────────────────────────────────────────────────

    def list_projects(self) -> list[dict]:
        url = f"{self.org_url}/_apis/projects?{self.API}&$top=500"
        return self._get(url).get("value", [])

    def list_repos(self, project: str) -> list[dict]:
        url = f"{self.org_url}/{self._p(project)}/_apis/git/repositories?{self.API}"
        return self._get(url).get("value", [])

    def get_repo(self, project: str, repo: str) -> dict:
        url = (f"{self.org_url}/{self._p(project)}/_apis/git/repositories"
               f"/{quote(repo, safe='')}?{self.API}")
        return self._get(url)

    def get_repo_stats(self, project: str, repo_id: str) -> dict:
        url = (f"{self.org_url}/{self._p(project)}/_apis/git/repositories"
               f"/{repo_id}/stats/branches?{self.API}")
        try:
            data = self._get(url)
            return {"branch_count": data.get("count", 0)}
        except Exception:
            return {"branch_count": 0}

    def get_repo_commits(self, project: str, repo_id: str,
                          top: int = 1) -> list[dict]:
        url = (f"{self.org_url}/{self._p(project)}/_apis/git/repositories"
               f"/{repo_id}/commits?{self.API}&$top={top}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

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
        except Exception:
            return ""

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
        try:
            return self._get(url)
        except Exception:
            return {}

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
        vsrm_base = self.org_url.replace(
            "dev.azure.com", "vsrm.dev.azure.com"
        ).replace(
            ".visualstudio.com", ".vsrm.visualstudio.com"
        )
        url = (f"{vsrm_base}/{self._p(project)}/_apis/release/definitions"
               f"/{def_id}?{self.API_RELEASE}")
        try:
            return self._get(url)
        except Exception:
            return {}

    # ── Environments & Approvals ────────────────────────────────────────────

    def list_environments(self, project: str) -> list[dict]:
        url = (f"{self.org_url}/{self._p(project)}/_apis/distributedtask/environments"
               f"?{self.API}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    def get_pipeline_approvals(self, project: str, pipeline_id: int) -> list[dict]:
        url = (f"{self.org_url}/{self._p(project)}/_apis/pipelines/checks/configurations"
               f"?{self.API}&resourceType=pipeline&resourceId={pipeline_id}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    # ── Variable Groups & Service Connections ───────────────────────────────

    def list_variable_groups(self, project: str) -> list[dict]:
        url = (f"{self.org_url}/{self._p(project)}"
               f"/_apis/distributedtask/variablegroups?{self.API}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    def list_service_connections(self, project: str) -> list[dict]:
        url = (f"{self.org_url}/{self._p(project)}"
               f"/_apis/serviceendpoint/endpoints?{self.API}")
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    # ── Agent Pools ─────────────────────────────────────────────────────────

    def list_agent_pools(self) -> list[dict]:
        url = f"{self.org_url}/_apis/distributedtask/pools?{self.API}"
        try:
            return self._get(url).get("value", [])
        except Exception:
            return []

    # ── Work Items ──────────────────────────────────────────────────────────

    def list_work_items(self, project: str, top: int = 500) -> list[dict]:
        url = f"{self.org_url}/{self._p(project)}/_apis/wit/wiql?{self.API}"
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
        url = f"{self.org_url}/{self._p(project)}/_apis/wiki/wikis?{self.API}"
        wikis = self._get(url).get("value", [])
        out = []
        for wiki in wikis:
            try:
                root = self._get(
                    f"{self.org_url}/{self._p(project)}/_apis/wiki/wikis"
                    f"/{wiki['id']}/pages?recursionLevel=full&{self.API}"
                )
                out.append({"wiki": wiki, "root": root})
            except Exception:
                pass
        return out

    def list_branch_policies(self, project: str, repo_id: str) -> list[dict]:
        url = (f"{self.org_url}/{self._p(project)}"
               f"/_apis/policy/configurations?{self.API}")
        all_pol = self._get(url).get("value", [])
        return [
            p for p in all_pol
            if p.get("settings", {}).get("scope", [{}])[0]
               .get("repositoryId") == repo_id
        ]
