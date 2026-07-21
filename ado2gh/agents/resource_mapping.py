"""Fixed ADO → GitHub resource mapping configuration.

Defines field mappings for Boards (work items → Issues),
Test Plans (test cases → Issues with labels, test suites → milestones),
Artifacts (feeds → GitHub Packages, supported types only),
Wiki (pages → GitHub Wiki markdown).

Each mapping type has an `override_allowed` flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResourceMappingConfig:
    """Configuration for a single ADO → GitHub resource mapping."""
    ado_type: str
    github_target: str
    field_mappings: dict[str, str] = field(default_factory=dict)
    label_mappings: dict[str, str] = field(default_factory=dict)
    override_allowed: bool = False
    supported_types: list[str] = field(default_factory=list)


BOARDS_MAPPING = ResourceMappingConfig(
    ado_type="work_item",
    github_target="issue",
    field_mappings={
        "System.Title": "title",
        "System.Description": "body",
        "System.State": "state",
        "System.AssignedTo": "assignee",
        "System.Tags": "labels",
        "System.AreaPath": "milestone",
    },
    label_mappings={
        "Bug": "bug",
        "Feature": "enhancement",
        "Task": "task",
        "User Story": "story",
        "Epic": "epic",
    },
    override_allowed=True,
)

TEST_CASE_MAPPING = ResourceMappingConfig(
    ado_type="test_case",
    github_target="issue_with_labels",
    field_mappings={
        "System.Title": "title",
        "System.Description": "body",
        "Microsoft.VSTS.TCM.Steps": "checklist",
    },
    label_mappings={
        "test-case": "test-case",
        "automated": "automated-test",
    },
    override_allowed=False,
)

TEST_SUITE_MAPPING = ResourceMappingConfig(
    ado_type="test_suite",
    github_target="milestone",
    field_mappings={
        "System.Title": "title",
        "System.Description": "description",
    },
    label_mappings={},
    override_allowed=False,
)

ARTIFACT_FEED_MAPPING = ResourceMappingConfig(
    ado_type="artifact_feed",
    github_target="package",
    field_mappings={
        "feed_name": "package_name",
        "feed_type": "package_type",
    },
    label_mappings={},
    override_allowed=False,
    supported_types=["npm", "NuGet", "Docker", "Maven", "PyPI"],
)

WIKI_MAPPING = ResourceMappingConfig(
    ado_type="wiki_page",
    github_target="wiki",
    field_mappings={
        "page_title": "page_title",
        "page_content": "page_content",
        "page_path": "page_path",
    },
    label_mappings={},
    override_allowed=False,
)

ALL_MAPPINGS: dict[str, ResourceMappingConfig] = {
    "boards": BOARDS_MAPPING,
    "test_case": TEST_CASE_MAPPING,
    "test_suite": TEST_SUITE_MAPPING,
    "artifact_feed": ARTIFACT_FEED_MAPPING,
    "wiki_page": WIKI_MAPPING,
}


def get_mapping(ado_type: str) -> ResourceMappingConfig | None:
    """Get the resource mapping config for a given ADO type."""
    return ALL_MAPPINGS.get(ado_type)


def list_mappings() -> list[dict[str, Any]]:
    """List all available resource mappings."""
    return [
        {
            "ado_type": m.ado_type,
            "github_target": m.github_target,
            "field_mappings": m.field_mappings,
            "label_mappings": m.label_mappings,
            "override_allowed": m.override_allowed,
            "supported_types": m.supported_types,
        }
        for m in ALL_MAPPINGS.values()
    ]


def is_supported_artifact_type(feed_type: str) -> bool:
    """Check if an ADO Artifacts feed type is supported for GitHub Packages."""
    return feed_type in ARTIFACT_FEED_MAPPING.supported_types
