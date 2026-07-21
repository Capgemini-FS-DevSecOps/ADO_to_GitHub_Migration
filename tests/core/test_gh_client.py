"""GHClient construction and token_manager access."""
from ado2gh.clients.gh_client import GHClient
from ado2gh.clients.token_manager import TokenManager


def test_gh_client_token_manager_property():
    tm = TokenManager.from_single_token("ghp_test_token")
    client = GHClient(tm)
    assert client.token_manager is tm
    assert client.token_manager.get_token() == "ghp_test_token"
