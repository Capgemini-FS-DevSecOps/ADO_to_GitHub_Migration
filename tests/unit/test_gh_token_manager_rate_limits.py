"""Rotation and rate-limit handling in ``ado2gh/clients/gh_token_manager.py`` (COV-DRIFT-003).

`CLAUDE.md` § Key Patterns names this module as the component that rotates
tokens per API call and updates rate limits from response headers. It had no
dedicated test module and sat at 38 % coverage — header-parsing logic that fails
silently is exactly what the drift report flags.

Nothing here sleeps for real or reaches the network: ``time.sleep`` and
``requests`` are replaced where the exhausted-pool and refresh paths reach them.
Every token literal is obviously fake (CA-003).
"""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from ado2gh.clients.gh_token_manager import AppCredentials, TokenInfo, TokenManager

FAKE_A = "ghp_faketokenaaaaaaaaaaaaaaaaaaaaaaaa01"
FAKE_B = "ghp_faketokenbbbbbbbbbbbbbbbbbbbbbbbb02"
FAKE_C = "ghp_faketokencccccccccccccccccccccccc03"


def _pool(*tokens: str) -> TokenManager:
    manager = TokenManager()
    manager._tokens = [TokenInfo(token=t) for t in tokens]
    return manager


# --------------------------------------------------------------------------
# Building the pool
# --------------------------------------------------------------------------


def test_a_single_token_pool_always_returns_that_token():
    manager = TokenManager.from_single_token(FAKE_A)
    assert manager.token_count == 1
    assert {manager.get_token() for _ in range(5)} == {FAKE_A}


def test_from_env_reads_every_populated_variable_in_order(monkeypatch):
    monkeypatch.setenv("GH_TOKEN_1", FAKE_A)
    monkeypatch.setenv("GH_TOKEN_2", FAKE_B)
    manager = TokenManager.from_env(["GH_TOKEN_1", "GH_TOKEN_2"])
    assert manager.token_count == 2
    assert [manager.get_token(), manager.get_token()] == [FAKE_A, FAKE_B]


def test_from_env_skips_an_unset_variable_without_failing(monkeypatch):
    monkeypatch.setenv("GH_TOKEN_1", FAKE_A)
    monkeypatch.delenv("GH_TOKEN_2", raising=False)
    manager = TokenManager.from_env(["GH_TOKEN_1", "GH_TOKEN_2"])
    assert manager.token_count == 1


def test_from_env_refuses_a_pool_with_no_tokens_at_all(monkeypatch):
    monkeypatch.delenv("GH_TOKEN_MISSING", raising=False)
    with pytest.raises(ValueError, match="No tokens found"):
        TokenManager.from_env(["GH_TOKEN_MISSING"])


def test_from_json_config_reads_pat_variables_named_by_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TARGET_TOKEN_1", FAKE_A)
    monkeypatch.setenv("TARGET_TOKEN_2", FAKE_B)
    config = tmp_path / "tokens.json"
    config.write_text(
        json.dumps({"pat_token_envs": {"target": ["TARGET_TOKEN_1", "TARGET_TOKEN_2"]}}),
    )
    manager = TokenManager.from_json_config(str(config))
    assert manager.token_count == 2


def test_from_json_config_picks_up_app_credentials_only_when_all_three_are_set(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("APP_ID", "12345")
    monkeypatch.setenv("INSTALL_ID", "67890")
    monkeypatch.setenv("KEY_PATH", str(tmp_path / "fake.pem"))
    config = tmp_path / "tokens.json"
    config.write_text(
        json.dumps({
            "app_token_envs": {"target": [["APP_ID", "INSTALL_ID", "KEY_PATH"]]},
        }),
    )
    manager = TokenManager.from_json_config(str(config))
    assert manager._app_creds == AppCredentials("12345", "67890", str(tmp_path / "fake.pem"))
    assert manager.token_count == 1, "a configured App counts as one credential"


def test_from_json_config_ignores_an_incomplete_app_triple(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ID", "12345")
    monkeypatch.delenv("INSTALL_ID", raising=False)
    monkeypatch.setenv("KEY_PATH", "x")
    config = tmp_path / "tokens.json"
    config.write_text(
        json.dumps({
            "app_token_envs": {"target": [["APP_ID", "INSTALL_ID", "KEY_PATH"]]},
        }),
    )
    with pytest.raises(ValueError, match="No tokens found"):
        TokenManager.from_json_config(str(config))


def test_from_json_config_refuses_a_key_that_yields_nothing(tmp_path):
    config = tmp_path / "tokens.json"
    config.write_text(json.dumps({"pat_token_envs": {"source": []}}))
    with pytest.raises(ValueError, match="key=target"):
        TokenManager.from_json_config(str(config))


def test_an_empty_pool_with_no_app_credentials_refuses_to_hand_out_a_token():
    with pytest.raises(ValueError, match="No tokens available"):
        TokenManager().get_token()


# --------------------------------------------------------------------------
# Round-robin rotation
# --------------------------------------------------------------------------


def test_tokens_are_handed_out_in_turn():
    manager = _pool(FAKE_A, FAKE_B, FAKE_C)
    assert [manager.get_token() for _ in range(6)] == [
        FAKE_A, FAKE_B, FAKE_C, FAKE_A, FAKE_B, FAKE_C,
    ]


def test_rotation_resumes_where_it_left_off_after_an_update():
    manager = _pool(FAKE_A, FAKE_B)
    assert manager.get_token() == FAKE_A
    manager.update_rate_limit(FAKE_A, 4999, time.time() + 3600)
    assert manager.get_token() == FAKE_B


# --------------------------------------------------------------------------
# Rate-limit headers
# --------------------------------------------------------------------------


def test_update_rate_limit_records_the_reported_state():
    manager = _pool(FAKE_A)
    reset = time.time() + 1800
    manager.update_rate_limit(FAKE_A, 42, reset)
    info = manager._tokens[0]
    assert info.remaining == 42
    assert info.reset_at == reset
    assert info.last_checked > 0


def test_update_rate_limit_ignores_a_token_that_is_not_in_the_pool():
    manager = _pool(FAKE_A)
    manager.update_rate_limit("ghp_notinthepool00000000000000000004", 0, 1.0)
    assert manager._tokens[0].remaining == 5000


def test_an_exhausted_token_is_skipped_in_favour_of_a_healthy_one():
    manager = _pool(FAKE_A, FAKE_B)
    manager.update_rate_limit(FAKE_A, 0, time.time() + 3600)
    assert {manager.get_token() for _ in range(4)} == {FAKE_B}, (
        "a token reported as exhausted was still handed out"
    )


def test_a_token_at_the_threshold_is_still_treated_as_exhausted():
    """The guard is ``remaining > 50``, so exactly 50 does not qualify."""
    manager = _pool(FAKE_A, FAKE_B)
    manager.update_rate_limit(FAKE_A, 50, time.time() + 3600)
    assert manager.get_token() == FAKE_B


def test_a_token_just_above_the_threshold_is_handed_out():
    manager = _pool(FAKE_A, FAKE_B)
    manager.update_rate_limit(FAKE_A, 51, time.time() + 3600)
    assert manager.get_token() == FAKE_A


def test_a_low_token_whose_window_has_already_reset_is_treated_as_replenished():
    manager = _pool(FAKE_A)
    manager.update_rate_limit(FAKE_A, 0, time.time() - 10)
    assert manager.get_token() == FAKE_A
    assert manager._tokens[0].remaining == 5000, "the replenished quota was not recorded"


def test_when_every_token_is_exhausted_the_soonest_reset_is_waited_for():
    manager = _pool(FAKE_A, FAKE_B)
    now = time.time()
    manager.update_rate_limit(FAKE_A, 0, now + 600)
    manager.update_rate_limit(FAKE_B, 0, now + 120)
    with patch("ado2gh.clients.gh_token_manager.time.sleep") as slept:
        token = manager.get_token()
    assert token == FAKE_B, "the pool waited for a later reset than it had to"
    assert slept.called
    waited = slept.call_args.args[0]
    assert 100 < waited < 140, f"waited {waited}s, not the ~122s until the soonest reset"
    assert manager._tokens[1].remaining == 5000


def test_a_reset_already_in_the_past_produces_no_negative_wait():
    manager = _pool(FAKE_A)
    manager._tokens[0].remaining = 0
    manager._tokens[0].reset_at = time.time() + 0.0001
    with patch("ado2gh.clients.gh_token_manager.time.sleep") as slept:
        manager.get_token()
    if slept.called:
        assert slept.call_args.args[0] >= 0


# --------------------------------------------------------------------------
# Refreshing limits from GitHub
# --------------------------------------------------------------------------


def test_check_rate_limits_reads_the_core_resource_for_each_token():
    manager = _pool(FAKE_A, FAKE_B)
    response = MagicMock(ok=True)
    response.json.return_value = {
        "resources": {"core": {"remaining": 77, "reset": 1700000000}},
    }
    with patch("ado2gh.clients.gh_token_manager.requests.get", return_value=response) as get:
        manager.check_rate_limits()
    assert [t.remaining for t in manager._tokens] == [77, 77]
    assert [t.reset_at for t in manager._tokens] == [1700000000, 1700000000]
    assert get.call_count == 2
    assert get.call_args.args[0] == "https://api.github.com/rate_limit"
    assert get.call_args.kwargs["headers"]["Authorization"] == f"Bearer {FAKE_B}"


def test_check_rate_limits_honours_an_enterprise_api_base():
    manager = _pool(FAKE_A)
    response = MagicMock(ok=True)
    response.json.return_value = {"resources": {"core": {}}}
    with patch("ado2gh.clients.gh_token_manager.requests.get", return_value=response) as get:
        manager.check_rate_limits(api_base="https://ghe.internal/api/v3")
    assert get.call_args.args[0] == "https://ghe.internal/api/v3/rate_limit"


def test_check_rate_limits_leaves_a_token_untouched_when_the_request_fails():
    manager = _pool(FAKE_A)
    manager.update_rate_limit(FAKE_A, 123, 456.0)
    with patch(
        "ado2gh.clients.gh_token_manager.requests.get", side_effect=OSError("network down"),
    ):
        manager.check_rate_limits()
    assert manager._tokens[0].remaining == 123
    assert manager._tokens[0].reset_at == 456.0


def test_check_rate_limits_leaves_a_token_untouched_on_a_non_ok_response():
    manager = _pool(FAKE_A)
    manager.update_rate_limit(FAKE_A, 123, 456.0)
    with patch(
        "ado2gh.clients.gh_token_manager.requests.get", return_value=MagicMock(ok=False),
    ):
        manager.check_rate_limits()
    assert manager._tokens[0].remaining == 123


def test_check_rate_limits_skips_app_installation_tokens():
    manager = TokenManager()
    manager._tokens = [TokenInfo(token="ghs_fakeapptoken0000000000000000000005", is_app_token=True)]
    with patch("ado2gh.clients.gh_token_manager.requests.get") as get:
        manager.check_rate_limits()
    assert not get.called, "an App installation token was sent to /rate_limit"


# --------------------------------------------------------------------------
# GitHub App installation tokens
# --------------------------------------------------------------------------


def test_a_cached_app_token_is_reused_until_shortly_before_it_expires():
    manager = TokenManager()
    manager.configure_app_auth("12345", "67890", "fake.pem")
    manager._tokens = [
        TokenInfo(
            token="ghs_fakecachedapptoken000000000000006",
            is_app_token=True,
            app_token_expiry=time.time() + 3000,
        ),
    ]
    with patch("ado2gh.clients.gh_token_manager.requests.post") as post:
        assert manager._get_app_token() == "ghs_fakecachedapptoken000000000000006"
    assert not post.called, "a valid cached App token was re-minted"


def test_an_app_token_about_to_expire_is_not_reused():
    manager = TokenManager()
    manager.configure_app_auth("12345", "67890", "fake.pem")
    manager._tokens = [
        TokenInfo(
            token="ghs_fakeexpiring000000000000000000007",
            is_app_token=True,
            app_token_expiry=time.time() + 30,  # inside the one-minute margin
        ),
    ]
    with patch.dict("sys.modules", {"jwt": None}), pytest.raises(ImportError, match="PyJWT"):
        manager._get_app_token()


def test_minting_an_app_token_without_credentials_is_refused():
    with pytest.raises(ValueError, match="No app credentials"):
        TokenManager()._get_app_token()


def test_configure_app_auth_records_the_identity():
    manager = TokenManager()
    manager.configure_app_auth("12345", "67890", "/tmp/fake.pem")
    assert manager._app_creds == AppCredentials("12345", "67890", "/tmp/fake.pem")
    assert manager.token_count == 1


# --------------------------------------------------------------------------
# Status reporting never leaks a credential (CA-003)
# --------------------------------------------------------------------------


def test_the_status_report_counts_tokens_without_naming_any_of_them():
    manager = _pool(FAKE_A, FAKE_B)
    manager._tokens.append(
        TokenInfo(token="ghs_fakeapptoken0000000000000000000008", is_app_token=True),
    )
    manager.configure_app_auth("12345", "67890", "fake.pem")
    manager.update_rate_limit(FAKE_A, 99, 0.0)

    status = manager.get_token_status()
    assert status["pat_count"] == 2
    assert status["app_configured"] is True
    assert status["tokens"] == [
        {"remaining": 99, "is_app": False},
        {"remaining": 5000, "is_app": False},
        {"remaining": 5000, "is_app": True},
    ]
    blob = json.dumps(status)
    for secret in (FAKE_A, FAKE_B, "ghs_fakeapptoken0000000000000000000008"):
        assert secret not in blob, "a credential value reached the status report"


def test_the_status_report_of_an_empty_pool_is_well_formed():
    status = TokenManager().get_token_status()
    assert status == {"pat_count": 0, "app_configured": False, "tokens": []}
