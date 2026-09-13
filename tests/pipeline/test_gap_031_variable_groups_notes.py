"""GAP-031 — referenced ADO variable groups must be named in the migration notes.

``ado2gh/pipelines/extractor.py`` captures every variable group a pipeline
references into ``PipelineMetadata.variable_groups`` as **names only**, but
nothing downstream read it: the generated workflow builds ``env`` from
``meta.variables`` alone and ``_write_migration_notes`` had a
``## Service Connections`` section with no equivalent for groups. An operator
therefore got a workflow whose ``$(groupVar)`` macros resolve to nothing and a
notes file that never mentioned the groups.

Operator decision memo § 4 selects option A: name the groups and their variables
in the notes, and leave the workflow YAML alone (option B would have to guess
``vars.`` versus ``secrets.`` for every placeholder). These tests hold that
shape in place at the notes-generation choke point and at the extractor that
feeds it.

Every value below is a fabricated placeholder. The assertions are written so a
leak fails on the *name* of a variable rather than by echoing its value
(CA-003).
"""
from __future__ import annotations

import yaml

from ado2gh.models import PipelineMetadata, PipelineType, PipelineVariable
from ado2gh.pipelines.extractor import PipelineMetadataExtractor
from ado2gh.pipelines.transform.transformer import PipelineTransformer

# Fabricated values. The marker makes an accidental leak obvious in a diff and
# lets the assertions scan the rendered text for it.
_LEAK_MARKER = "must-not-be-written"

BUILD_GROUP = "Shared-Build-Settings"
DEPLOY_GROUP = "Prod-Deploy-Settings"
PLAIN_NAMES = ["NUGET_FEED_NAME", "BUILD_CONFIGURATION_NAME"]
SECRET_NAME = "DEPLOY_TOKEN_NAME"

# Raw shape returned by the ADO variable-groups API: name -> {value, isSecret}.
RAW_GROUPS = [
    {
        "id": 11,
        "name": BUILD_GROUP,
        "type": "Vsts",
        "variables": {
            PLAIN_NAMES[0]: {"value": f"fake-feed-{_LEAK_MARKER}-a1b2"},
            PLAIN_NAMES[1]: {"value": f"fake-config-{_LEAK_MARKER}-c3d4"},
        },
    },
    {
        "id": 22,
        "name": DEPLOY_GROUP,
        "type": "AzureKeyVault",
        "variables": {
            SECRET_NAME: {"value": f"fake-token-{_LEAK_MARKER}-e5f6", "isSecret": True},
        },
    },
]

ALL_VALUES = {
    name: spec["value"]
    for group in RAW_GROUPS
    for name, spec in group["variables"].items()
}


def _extracted_groups() -> list[dict]:
    """Run the real extractor over the raw ADO records.

    Returns:
        The ``meta.variable_groups`` entries the extractor produced for a build
        definition that references both groups.
    """
    meta = PipelineMetadata(
        pipeline_id=31,
        pipeline_name="gap-031-variable-groups",
        pipeline_type=PipelineType.CLASSIC,
    )
    build_def = {"variableGroups": [{"id": 11}, {"id": 22}]}
    PipelineMetadataExtractor()._extract_build_variables(meta, build_def, RAW_GROUPS)
    return meta.variable_groups


def _meta(*, with_groups: bool) -> PipelineMetadata:
    """Build pipeline metadata that does or does not reference variable groups.

    Args:
        with_groups: When true, attach the groups the real extractor produced.

    Returns:
        Metadata carrying one ordinary pipeline variable, so that the notes and
        the workflow are exercised with something to render either way.
    """
    return PipelineMetadata(
        pipeline_id=31,
        pipeline_name="gap-031-variable-groups",
        pipeline_type=PipelineType.YAML,
        variables=[PipelineVariable(name="TARGET_ENV", value="staging")],
        variable_groups=_extracted_groups() if with_groups else [],
    )


def _leaked_names(text: str) -> list[str]:
    """Report which group variables had their value written into the output.

    Args:
        text: Rendered notes or workflow text.

    Returns:
        The names whose value appears verbatim. Names, never values, so a
        failure message cannot echo a credential (CA-003).
    """
    return sorted(name for name, value in ALL_VALUES.items() if value in text)


def _body(workflow_text: str) -> str:
    """Strip the generated comment header from a rendered workflow.

    Args:
        workflow_text: The workflow file as written to disk.

    Returns:
        Everything below the header, which carries a generation timestamp and so
        differs between two runs of the same metadata.
    """
    return workflow_text.split("\n\n", 1)[-1]


def test_extractor_records_group_variable_names_never_values():
    """The extractor stores variable names and the secret-flagged subset only."""
    groups = _extracted_groups()

    assert [g["name"] for g in groups] == [BUILD_GROUP, DEPLOY_GROUP]
    assert sorted(groups[0]["variables"]) == sorted(PLAIN_NAMES)
    assert groups[0]["secret_variables"] == []
    assert groups[1]["variables"] == [SECRET_NAME]
    assert groups[1]["secret_variables"] == [SECRET_NAME]
    assert _leaked_names(yaml.safe_dump(groups)) == []


def test_notes_list_every_group_and_variable_name_without_values(tmp_path):
    """The notes name both groups, every variable, and mark the secret one."""
    notes = PipelineTransformer().transform(
        _meta(with_groups=True), output_dir=tmp_path,
    )["notes_file"].read_text(encoding="utf-8")

    assert "## Variable Groups" in notes
    assert BUILD_GROUP in notes
    assert DEPLOY_GROUP in notes
    for name in [*PLAIN_NAMES, SECRET_NAME]:
        assert f"`{name}`" in notes, f"{name} is missing from the migration notes"
    assert f"`{SECRET_NAME}` (secret)" in notes
    assert f"`{PLAIN_NAMES[0]}` (secret)" not in notes

    assert _leaked_names(notes) == [], (
        f"variable-group values reached the notes: {_leaked_names(notes)}"
    )
    assert _LEAK_MARKER not in notes


def test_notes_omit_the_section_when_no_group_is_referenced(tmp_path):
    """A pipeline with no variable groups gets no Variable Groups section."""
    notes = PipelineTransformer().transform(
        _meta(with_groups=False), output_dir=tmp_path,
    )["notes_file"].read_text(encoding="utf-8")

    assert "Variable Groups" not in notes


def test_notes_render_metadata_persisted_before_this_change(tmp_path):
    """Groups restored from an older payload still render, without a secret mark.

    ``PipelineMetadata.from_dict`` replays ``variable_groups`` verbatim, so rows
    written before ``secret_variables`` existed — and any hand-written entry that
    is a bare group name — must not break note generation.
    """
    legacy = _meta(with_groups=False)
    legacy.variable_groups = [
        {"id": 11, "name": BUILD_GROUP, "type": "Vsts", "variables": PLAIN_NAMES},
        DEPLOY_GROUP,
    ]

    notes = PipelineTransformer().transform(
        legacy, output_dir=tmp_path,
    )["notes_file"].read_text(encoding="utf-8")

    assert f"### {BUILD_GROUP}" in notes
    assert f"### {DEPLOY_GROUP}" in notes
    for name in PLAIN_NAMES:
        assert f"- `{name}`" in notes
    assert "(secret)" not in notes


def test_workflow_yaml_is_unchanged_by_the_notes_section(tmp_path):
    """Option B was not applied: no group name reaches the generated workflow."""
    with_groups = PipelineTransformer().transform(
        _meta(with_groups=True), output_dir=tmp_path / "with",
    )["workflow_file"].read_text(encoding="utf-8")
    without = PipelineTransformer().transform(
        _meta(with_groups=False), output_dir=tmp_path / "without",
    )["workflow_file"].read_text(encoding="utf-8")

    # The generated header carries a timestamp, so compare everything below it
    # byte for byte rather than only the parsed document.
    assert _body(with_groups) == _body(without)
    assert yaml.safe_load(with_groups)["env"] == {"TARGET_ENV": "staging"}
    for name in [BUILD_GROUP, DEPLOY_GROUP, SECRET_NAME, *PLAIN_NAMES]:
        assert name not in with_groups, f"{name} leaked into the generated workflow"
    assert _leaked_names(with_groups) == []
