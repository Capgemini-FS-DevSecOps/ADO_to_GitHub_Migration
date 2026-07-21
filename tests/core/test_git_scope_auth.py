"""ADO git clone URL normalization and auth env."""
from ado2gh.core.scopes.git_scope import _ado_git_env, _clean_clone_url


def test_clean_clone_url_strips_embedded_username():
    raw = (
        "https://cloud-sre@dev.azure.com/cloud-sre/proj/_git/repo"
    )
    assert _clean_clone_url(raw) == (
        "https://dev.azure.com/cloud-sre/proj/_git/repo"
    )


def test_ado_git_env_sets_extra_header():
    env = _ado_git_env("test-pat-value")
    assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    assert env["GIT_TERMINAL_PROMPT"] == "0"
